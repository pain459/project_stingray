"""Unit tests for the SPS30 hardware driver.

These run without hardware: a fake transport stands in for the I2C bus, so the
CRC, the 16-bit pointer protocol, and every failure path are exercised on any
machine. Tests that touch a real ``/dev/i2c-N`` are marked ``hardware``.
"""

from __future__ import annotations

import errno

import pytest

from stingray.core.errors import (
    SensorDisconnectedError,
    SensorInitError,
    SensorReadError,
)
from stingray.core.models import MetricId, SensorId, SensorState
from stingray.sensors.sps30 import (
    CMD_READ_VERSION,
    CMD_START_MEASUREMENT,
    FORMAT_UINT16,
    SPS30,
    _crc8,
    _uint16_to_value,
)


def _word_with_crc(value: int) -> list[int]:
    msb = (value >> 8) & 0xFF
    lsb = value & 0xFF
    return [msb, lsb, _crc8([msb, lsb])]


def _measurement_bytes(
    pm1: int,
    pm25: int,
    pm4: int,
    pm10: int,
    pm05: int,
    pm1n: int,
    pm25n: int,
    pm4n: int,
    pm10n: int,
    tps: int,
) -> list[int]:
    """Build a 30-byte measurement response (10 words + 10 CRCs)."""
    raw = []
    for v in (pm1, pm25, pm4, pm10, pm05, pm1n, pm25n, pm4n, pm10n, tps):
        raw.extend(_word_with_crc(v))
    return raw


class FakeTransport:
    """Minimal in-memory I2C bus with 16-bit pointer support."""

    def __init__(self) -> None:
        self.writes: list[tuple[int, list[int]]] = []
        self.version: list[int] = []
        self.serial: list[int] = []
        self.measurement: list[int] = []
        self.raise_on_write: OSError | None = None
        self.raise_on_read: OSError | None = None
        self.short_read: bool = False
        self.closed = 0

    def write_byte(self, address: int, value: int) -> None:
        if self.raise_on_write is not None:
            raise self.raise_on_write
        self.writes.append((address, [value]))

    def write_block(self, address: int, data: list[int]) -> None:
        if self.raise_on_write is not None:
            raise self.raise_on_write
        self.writes.append((address, list(data)))

    def read(self, address: int, length: int) -> list[int]:
        if self.raise_on_read is not None:
            raise self.raise_on_read
        # Determine which command was last written (pointer MSB, LSB).
        if self.writes:
            last_data = self.writes[-1][1]
            pointer = (last_data[0] << 8) | last_data[1] if len(last_data) >= 2 else 0
        else:
            pointer = 0

        if pointer == CMD_READ_VERSION:
            payload = self.version
        elif pointer == 0xD033:
            payload = self.serial
        else:
            payload = self.measurement

        if self.short_read:
            return payload[: max(0, length - 1)]
        return payload[:length]

    def close(self) -> None:
        self.closed += 1


def _driver(bus: FakeTransport, **kwargs: object) -> SPS30:
    return SPS30(bus=bus, sleep=lambda _s: None, **kwargs)  # type: ignore[arg-type]


def _valid_version() -> list[int]:
    return _word_with_crc((1 << 8) | 2)  # major=1, minor=2


def _valid_measurement() -> list[int]:
    # SPS30 response order: PM1, PM2.5, PM4, PM10 (mass),
    # PM0.5, PM1, PM2.5, PM4, PM10 (number), TPS
    return _measurement_bytes(100, 200, 300, 500, 50, 1000, 2000, 3000, 5000, 100)


class TestCrc:
    def test_datasheet_reference_vector(self) -> None:
        assert _crc8([0xBE, 0xEF]) == 0x92

    def test_empty_input_is_init_value(self) -> None:
        assert _crc8([]) == 0xFF


class TestConversions:
    def test_uint16_to_value(self) -> None:
        assert _uint16_to_value(0) == 0.0
        assert _uint16_to_value(100) == 1.0
        assert _uint16_to_value(2500) == 25.0
        assert _uint16_to_value(65535) == pytest.approx(655.35)


class TestReadPath:
    def test_read_yields_all_eight_metrics(self) -> None:
        bus = FakeTransport()
        bus.version = _valid_version()
        bus.measurement = _valid_measurement()
        driver = _driver(bus)
        driver.initialize()

        measurements = list(driver.read())

        expected_metrics = [
            MetricId.PM1_UG_M3,
            MetricId.PM2_5_UG_M3,
            MetricId.PM4_UG_M3,
            MetricId.PM10_UG_M3,
            MetricId.PM1_NUM_M3,
            MetricId.PM2_5_NUM_M3,
            MetricId.PM4_NUM_M3,
            MetricId.PM10_NUM_M3,
        ]
        assert [m.metric for m in measurements] == expected_metrics
        assert all(m.sensor is SensorId.SPS30 for m in measurements)
        # PM1.0 mass = 100 / 100 = 1.0
        assert measurements[0].value == pytest.approx(1.0)
        # PM2.5 mass = 200 / 100 = 2.0
        assert measurements[1].value == pytest.approx(2.0)
        # PM10 mass = 500 / 100 = 5.0
        assert measurements[3].value == pytest.approx(5.0)
        # PM1.0 number = 1000 / 100 = 10.0 (index 4)
        assert measurements[4].value == pytest.approx(10.0)
        # PM10 number = 5000 / 100 = 50.0 (index 7)
        assert measurements[7].value == pytest.approx(50.0)
        assert measurements[0].unit == "ug/m3"
        assert measurements[4].unit == "1/m3"

    def test_initialize_sends_start_measurement(self) -> None:
        bus = FakeTransport()
        bus.version = _valid_version()
        bus.measurement = _valid_measurement()
        driver = _driver(bus)
        driver.initialize()

        # Find the start measurement command in the write log.
        start_writes = [
            data
            for _addr, data in bus.writes
            if len(data) >= 2 and ((data[0] << 8) | data[1]) == CMD_START_MEASUREMENT
        ]
        assert len(start_writes) == 1
        # Verify the format byte is FORMAT_UINT16.
        assert start_writes[0][2] == FORMAT_UINT16

    def test_initialize_reads_firmware_version(self) -> None:
        bus = FakeTransport()
        bus.version = _valid_version()
        bus.measurement = _valid_measurement()
        driver = _driver(bus)
        driver.initialize()

        assert driver.firmware_version == (1, 2)

    def test_custom_address_is_used(self) -> None:
        bus = FakeTransport()
        bus.version = _valid_version()
        bus.measurement = _valid_measurement()
        driver = _driver(bus, address=0x6A)
        driver.initialize()

        # All writes should be to 0x6A.
        assert all(addr == 0x6A for addr, _ in bus.writes)


class TestFailurePaths:
    def test_crc_mismatch_raises_read_error(self) -> None:
        bus = FakeTransport()
        bus.version = _valid_version()
        # Corrupt the first CRC byte in the measurement.
        bad = _valid_measurement()
        bad[2] = 0x00  # wrong CRC
        bus.measurement = bad
        driver = _driver(bus)
        driver.initialize()

        with pytest.raises(SensorReadError, match="CRC mismatch"):
            list(driver.read())

    def test_short_read_raises(self) -> None:
        bus = FakeTransport()
        bus.version = _valid_version()
        bus.measurement = _valid_measurement()
        bus.short_read = True
        driver = _driver(bus)
        # initialize() probes via firmware version (3 bytes), which will
        # short-read and mark the sensor disconnected.
        driver.initialize()
        assert driver.health().state is SensorState.DISCONNECTED

    def test_nack_raises_disconnected(self) -> None:
        bus = FakeTransport()
        bus.version = _valid_version()
        bus.measurement = _valid_measurement()
        driver = _driver(bus)
        driver.initialize()
        bus.raise_on_read = OSError(errno.EREMOTEIO, "Remote I/O error")

        with pytest.raises(SensorDisconnectedError):
            list(driver.read())
        assert driver.health().state is SensorState.DISCONNECTED

    def test_other_oserror_raises_read_error(self) -> None:
        bus = FakeTransport()
        bus.version = _valid_version()
        bus.measurement = _valid_measurement()
        driver = _driver(bus)
        driver.initialize()
        bus.raise_on_read = OSError(errno.EINVAL, "Invalid argument")

        with pytest.raises(SensorReadError):
            list(driver.read())

    def test_recovers_after_disconnect(self) -> None:
        bus = FakeTransport()
        bus.version = _valid_version()
        bus.measurement = _valid_measurement()
        driver = _driver(bus)
        driver.initialize()
        bus.raise_on_read = OSError(errno.EREMOTEIO, "Remote I/O error")
        with pytest.raises(SensorDisconnectedError):
            list(driver.read())

        bus.raise_on_read = None
        measurements = list(driver.read())

        assert len(measurements) == 8
        assert driver.health().state is SensorState.READY
        assert driver.health().consecutive_failures == 0

    def test_read_before_initialize_raises(self) -> None:
        driver = _driver(FakeTransport())
        with pytest.raises(SensorInitError):
            list(driver.read())


class TestLifecycle:
    def test_absent_sensor_does_not_abort_initialize(self) -> None:
        """A sensor that is simply not wired must not stop the service."""
        bus = FakeTransport()
        bus.version = _valid_version()
        bus.measurement = _valid_measurement()
        bus.raise_on_write = OSError(errno.EREMOTEIO, "Remote I/O error")
        bus.raise_on_read = OSError(errno.EREMOTEIO, "Remote I/O error")
        driver = _driver(bus)

        driver.initialize()  # must not raise

        health = driver.health()
        assert health.state is SensorState.DISCONNECTED
        assert health.consecutive_failures >= 1

    def test_health_is_initializing_before_initialize(self) -> None:
        driver = _driver(FakeTransport())
        assert driver.health().state is SensorState.INITIALIZING

    def test_health_ready_after_successful_probe(self) -> None:
        bus = FakeTransport()
        bus.version = _valid_version()
        bus.measurement = _valid_measurement()
        driver = _driver(bus)
        driver.initialize()
        assert driver.health().state is SensorState.READY

    def test_close_is_idempotent(self) -> None:
        bus = FakeTransport()
        bus.version = _valid_version()
        bus.measurement = _valid_measurement()
        driver = _driver(bus)
        driver.initialize()
        driver.close()
        driver.close()  # must not raise

    def test_read_after_close_raises(self) -> None:
        bus = FakeTransport()
        bus.version = _valid_version()
        bus.measurement = _valid_measurement()
        driver = _driver(bus)
        driver.initialize()
        driver.close()
        with pytest.raises(SensorReadError):
            list(driver.read())

    def test_initialize_after_close_raises(self) -> None:
        driver = _driver(FakeTransport())
        driver.close()
        with pytest.raises(SensorInitError):
            driver.initialize()

    def test_injected_bus_is_not_closed_by_driver(self) -> None:
        bus = FakeTransport()
        bus.version = _valid_version()
        bus.measurement = _valid_measurement()
        driver = _driver(bus)
        driver.initialize()
        driver.close()
        assert bus.closed == 0


class TestRepr:
    def test_repr_reports_address_and_firmware(self) -> None:
        bus = FakeTransport()
        bus.version = _valid_version()
        bus.measurement = _valid_measurement()
        driver = _driver(bus)
        driver.initialize()
        text = repr(driver)
        assert "0x69" in text
        assert "fw=1.2" in text


@pytest.mark.hardware
def test_real_sensor_on_bus() -> None:
    """Requires a real SPS30 on /dev/i2c-1 (deselected by default)."""
    driver = SPS30(bus_number=1, address=0x69)
    driver.initialize()
    try:
        measurements = list(driver.read())
    finally:
        driver.close()
    assert len(measurements) == 8
    assert all(m.value >= 0.0 for m in measurements)
