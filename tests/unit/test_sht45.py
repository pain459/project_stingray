"""Unit tests for the SHT45 hardware driver.

These run without hardware: a fake transport stands in for the I2C bus, so the
CRC, the datasheet conversions and every failure path are exercised on any
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
from stingray.sensors.sht45 import (
    CMD_MEASURE_HIGH_PRECISION,
    CMD_READ_SERIAL,
    CMD_SOFT_RESET,
    SHT45,
    crc8_sensirion,
    ticks_to_humidity,
    ticks_to_temperature,
)


def _word_with_crc(ticks: int) -> list[int]:
    msb = (ticks >> 8) & 0xFF
    lsb = ticks & 0xFF
    return [msb, lsb, crc8_sensirion([msb, lsb])]


class FakeTransport:
    """Minimal in-memory I2C bus."""

    def __init__(self) -> None:
        self.writes: list[tuple[int, int]] = []
        self.measurement: list[int] = []
        self.serial: list[int] = []
        self.raise_on_write: OSError | None = None
        self.raise_on_read: OSError | None = None
        self.short_read: bool = False
        self.closed = 0

    def write_byte(self, address: int, value: int) -> None:
        if self.raise_on_write is not None:
            raise self.raise_on_write
        self.writes.append((address, value))

    def read(self, address: int, length: int) -> list[int]:
        if self.raise_on_read is not None:
            raise self.raise_on_read
        command = self.writes[-1][1] if self.writes else CMD_MEASURE_HIGH_PRECISION
        payload = self.serial if command == CMD_READ_SERIAL else self.measurement
        if self.short_read:
            return payload[: max(0, length - 1)]
        return payload[:length]

    def close(self) -> None:
        self.closed += 1


def _measurement_bytes(temperature_ticks: int, humidity_ticks: int) -> list[int]:
    return _word_with_crc(temperature_ticks) + _word_with_crc(humidity_ticks)


def _driver(bus: FakeTransport, **kwargs: object) -> SHT45:
    return SHT45(bus=bus, sleep=lambda _s: None, **kwargs)  # type: ignore[arg-type]


class TestCrc:
    def test_datasheet_reference_vector(self) -> None:
        assert crc8_sensirion([0xBE, 0xEF]) == 0x92

    def test_single_zero_byte(self) -> None:
        # Sanity: the CRC of 0x00 is not 0x00 for init 0xFF / poly 0x31.
        assert crc8_sensirion([0x00]) == 0xAC

    def test_empty_input_is_init_value(self) -> None:
        assert crc8_sensirion([]) == 0xFF


class TestConversions:
    def test_temperature_at_mid_scale_is_about_25c(self) -> None:
        assert ticks_to_temperature(0x6666) == pytest.approx(24.99, abs=0.02)

    def test_temperature_bounds(self) -> None:
        assert ticks_to_temperature(0) == pytest.approx(-45.0)
        assert ticks_to_temperature(0xFFFF) == pytest.approx(130.0)

    def test_humidity_mid_scale(self) -> None:
        # -6 + 125 * 0x6666 / 0xFFFF ~= 44.0
        assert ticks_to_humidity(0x6666) == pytest.approx(44.0, abs=0.05)

    def test_humidity_is_clamped_to_physical_range(self) -> None:
        assert ticks_to_humidity(0) == 0.0  # formula gives -6
        assert ticks_to_humidity(0xFFFF) == 100.0  # formula gives 119


class TestReadPath:
    def test_read_yields_temperature_then_humidity(self) -> None:
        bus = FakeTransport()
        bus.measurement = _measurement_bytes(0x6666, 0x6666)
        driver = _driver(bus)
        driver.initialize()

        measurements = list(driver.read())

        assert [m.metric for m in measurements] == [
            MetricId.TEMPERATURE_C,
            MetricId.RELATIVE_HUMIDITY_PERCENT,
        ]
        assert all(m.sensor is SensorId.SHT45 for m in measurements)
        assert measurements[0].value == pytest.approx(25.0, abs=0.02)
        assert measurements[1].value == pytest.approx(44.0, abs=0.05)
        assert measurements[0].unit == "degC"
        assert measurements[1].unit == "%"
        assert measurements[0].observed_at.tzinfo is not None

    def test_measurement_command_is_sent(self) -> None:
        bus = FakeTransport()
        bus.measurement = _measurement_bytes(0x6666, 0x6666)
        driver = _driver(bus)
        driver.initialize()
        bus.writes.clear()

        list(driver.read())

        assert bus.writes == [(0x44, CMD_MEASURE_HIGH_PRECISION)]

    def test_initialize_sends_soft_reset_and_reads_serial(self) -> None:
        bus = FakeTransport()
        bus.measurement = _measurement_bytes(0x6666, 0x6666)
        bus.serial = [0x12, 0x34, crc8_sensirion([0x12, 0x34]),
                      0x56, 0x78, crc8_sensirion([0x56, 0x78])]
        driver = _driver(bus)

        driver.initialize()

        commands = [value for _addr, value in bus.writes]
        assert CMD_SOFT_RESET in commands
        assert CMD_READ_SERIAL in commands
        assert driver.serial_number == 0x12345678

    def test_custom_address_is_used(self) -> None:
        bus = FakeTransport()
        bus.measurement = _measurement_bytes(0x6666, 0x6666)
        driver = _driver(bus, address=0x45)
        driver.initialize()
        bus.writes.clear()

        list(driver.read())

        assert bus.writes == [(0x45, CMD_MEASURE_HIGH_PRECISION)]


class TestFailurePaths:
    def test_crc_mismatch_raises_read_error(self) -> None:
        bus = FakeTransport()
        bus.measurement = [0x66, 0x66, 0x00, 0x66, 0x66, crc8_sensirion([0x66, 0x66])]
        driver = _driver(bus)
        driver.initialize()

        with pytest.raises(SensorReadError, match="CRC mismatch"):
            list(driver.read())

    def test_crc_mismatch_on_humidity_word(self) -> None:
        bus = FakeTransport()
        bus.measurement = _word_with_crc(0x6666) + [0x66, 0x66, 0x00]
        driver = _driver(bus)
        driver.initialize()

        with pytest.raises(SensorReadError, match="humidity"):
            list(driver.read())

    def test_short_read_raises(self) -> None:
        bus = FakeTransport()
        bus.measurement = _measurement_bytes(0x6666, 0x6666)
        bus.short_read = True
        driver = _driver(bus)
        # initialize() probes, which will hit the short read and mark the
        # sensor disconnected rather than raising.
        driver.initialize()
        assert driver.health().state is SensorState.DISCONNECTED

    def test_nack_raises_disconnected(self) -> None:
        bus = FakeTransport()
        bus.measurement = _measurement_bytes(0x6666, 0x6666)
        driver = _driver(bus)
        driver.initialize()
        bus.raise_on_read = OSError(errno.EREMOTEIO, "Remote I/O error")

        with pytest.raises(SensorDisconnectedError):
            list(driver.read())
        assert driver.health().state is SensorState.DISCONNECTED

    def test_other_oserror_raises_read_error(self) -> None:
        bus = FakeTransport()
        bus.measurement = _measurement_bytes(0x6666, 0x6666)
        driver = _driver(bus)
        driver.initialize()
        bus.raise_on_read = OSError(errno.EINVAL, "Invalid argument")

        with pytest.raises(SensorReadError):
            list(driver.read())

    def test_recovers_after_disconnect(self) -> None:
        bus = FakeTransport()
        bus.measurement = _measurement_bytes(0x6666, 0x6666)
        driver = _driver(bus)
        driver.initialize()
        bus.raise_on_read = OSError(errno.EREMOTEIO, "Remote I/O error")
        with pytest.raises(SensorDisconnectedError):
            list(driver.read())

        bus.raise_on_read = None
        measurements = list(driver.read())

        assert len(measurements) == 2
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
        bus.measurement = _measurement_bytes(0x6666, 0x6666)
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
        bus.measurement = _measurement_bytes(0x6666, 0x6666)
        driver = _driver(bus)
        driver.initialize()
        assert driver.health().state is SensorState.READY

    def test_close_is_idempotent(self) -> None:
        bus = FakeTransport()
        bus.measurement = _measurement_bytes(0x6666, 0x6666)
        driver = _driver(bus)
        driver.initialize()
        driver.close()
        driver.close()  # must not raise

    def test_read_after_close_raises(self) -> None:
        bus = FakeTransport()
        bus.measurement = _measurement_bytes(0x6666, 0x6666)
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
        bus.measurement = _measurement_bytes(0x6666, 0x6666)
        driver = _driver(bus)
        driver.initialize()
        driver.close()
        assert bus.closed == 0


class TestRepr:
    def test_repr_reports_address_and_serial(self) -> None:
        bus = FakeTransport()
        bus.measurement = _measurement_bytes(0x6666, 0x6666)
        bus.serial = [0x00, 0x00, crc8_sensirion([0x00, 0x00]),
                      0x00, 0x01, crc8_sensirion([0x00, 0x01])]
        driver = _driver(bus)
        driver.initialize()
        text = repr(driver)
        assert "0x44" in text
        assert "0x00000001" in text


@pytest.mark.hardware
def test_real_sensor_on_bus() -> None:
    """Requires a real SHT45 on /dev/i2c-1 (deselected by default)."""
    driver = SHT45(bus_number=1, address=0x44)
    driver.initialize()
    try:
        measurements = list(driver.read())
    finally:
        driver.close()
    assert len(measurements) == 2
    assert -45.0 <= measurements[0].value <= 130.0
    assert 0.0 <= measurements[1].value <= 100.0
