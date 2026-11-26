"""Unit tests for the SCD4X hardware driver.

These run without hardware: a fake transport stands in for the I2C bus, so the
CRC, the 16-bit command protocol, and every failure path are exercised on any
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
from stingray.sensors.scd4x import (
    SCD4X,
    _crc8,
    _rh_to_percent,
    _temp_to_celsius,
)


def _word_with_crc(value: int) -> list[int]:
    msb = (value >> 8) & 0xFF
    lsb = value & 0xFF
    return [msb, lsb, _crc8([msb, lsb])]


def _measurement_bytes(co2: int, temp: int, rh: int) -> list[int]:
    return _word_with_crc(co2) + _word_with_crc(temp) + _word_with_crc(rh)


def _valid_variant() -> list[int]:
    # SCD41 = 0x1440
    return _word_with_crc(0x1440)


class FakeTransport:
    """Fake I2C transport for testing."""

    def __init__(self) -> None:
        self.bus_number = 1
        self.written: list[tuple[int, list[int]]] = []
        self.reads: list[tuple[int, int]] = []
        self.variant: list[int] | None = None
        self.measurement: list[int] | None = None
        self.fail_read: bool = False
        self.fail_write: bool = False

    def write_block(self, address: int, data: list[int]) -> None:
        if self.fail_write:
            raise OSError(errno.ENODEV, "No such device")
        self.written.append((address, data))

    def read(self, address: int, length: int) -> list[int]:
        if self.fail_read:
            raise OSError(errno.ENODEV, "No such device")
        self.reads.append((address, length))
        if length == 3:  # Variant read
            if self.variant is None:
                raise OSError(errno.ENODEV, "No such device")
            return self.variant
        elif length == 9:  # Measurement read
            if self.measurement is None:
                raise OSError(errno.ENODEV, "No such device")
            return self.measurement
        else:
            raise ValueError(f"Unexpected read length: {length}")


def _driver(bus: FakeTransport) -> SCD4X:
    return SCD4X(bus=bus)


class TestCRC:
    def test_crc8_known_values(self) -> None:
        # CRC-8 with polynomial 0x31 and initial value 0xFF
        # Test with known values from SCD4X datasheet
        # Temp=25°C (0x6667) -> CRC=0xA2
        assert _crc8([0x66, 0x67]) == 0xA2
        # RH=37% (0x5EB9) -> CRC=0x3C
        assert _crc8([0x5E, 0xB9]) == 0x3C

    def test_crc8_empty_input_is_init_value(self) -> None:
        assert _crc8([]) == 0xFF


class TestConversions:
    def test_temp_to_celsius(self) -> None:
        # T = -45 + 175 * raw / 65535
        assert _temp_to_celsius(0) == pytest.approx(-45.0)
        assert _temp_to_celsius(65535) == pytest.approx(130.0)
        # 25°C: raw = (25 + 45) / 175 * 65535 = 26294
        # Actual: -45 + 175 * 26294 / 65535 = 25.21°C
        assert _temp_to_celsius(26294) == pytest.approx(25.21, abs=0.1)

    def test_rh_to_percent(self) -> None:
        # RH = 100 * raw / 65535
        assert _rh_to_percent(0) == pytest.approx(0.0)
        assert _rh_to_percent(65535) == pytest.approx(100.0)
        # 50%: raw = 50 / 100 * 65535 = 32767.5
        assert _rh_to_percent(32768) == pytest.approx(50.0, abs=0.1)


class TestInitialize:
    def test_initialize_reads_variant(self) -> None:
        bus = FakeTransport()
        bus.variant = _valid_variant()
        driver = _driver(bus)
        driver.initialize()

        # Should have written the variant command
        assert len(bus.written) >= 1
        assert bus.written[0][0] == 0x62  # SCD4X address

    def test_initialize_starts_periodic_measurement(self) -> None:
        bus = FakeTransport()
        bus.variant = _valid_variant()
        driver = _driver(bus)
        driver.initialize()

        # Should have written the start periodic measurement command
        commands = [data for _, data in bus.written]
        start_cmd = [0x21, 0xB1, _crc8([0x21, 0xB1])]
        assert start_cmd in commands

    def test_initialize_sets_state_to_ready(self) -> None:
        bus = FakeTransport()
        bus.variant = _valid_variant()
        driver = _driver(bus)
        driver.initialize()

        assert driver.state is SensorState.READY

    def test_initialize_raises_if_sensor_absent(self) -> None:
        bus = FakeTransport()
        bus.variant = None  # No sensor
        driver = _driver(bus)

        with pytest.raises(SensorInitError):
            driver.initialize()

        assert driver.state is SensorState.DISCONNECTED


class TestReadPath:
    def test_read_yields_all_three_metrics(self) -> None:
        bus = FakeTransport()
        bus.variant = _valid_variant()
        # CO2=500, Temp=25°C (raw=26294), RH=50% (raw=32768)
        bus.measurement = _measurement_bytes(500, 26294, 32768)
        driver = _driver(bus)
        driver.initialize()

        measurements = list(driver.read())

        expected_metrics = [
            MetricId.CO2_PPM,
            MetricId.TEMPERATURE_C,
            MetricId.RELATIVE_HUMIDITY_PERCENT,
        ]
        assert [m.metric for m in measurements] == expected_metrics
        assert all(m.sensor is SensorId.SCD4X for m in measurements)

        # CO2 = 500 ppm
        assert measurements[0].value == pytest.approx(500.0)
        assert measurements[0].unit == "ppm"

        # Temperature ≈ 25.21°C (raw=26294)
        assert measurements[1].value == pytest.approx(25.21, abs=0.1)
        assert measurements[1].unit == "degC"

        # Humidity ≈ 50% (raw=32768)
        assert measurements[2].value == pytest.approx(50.0, abs=0.1)
        assert measurements[2].unit == "%"

    def test_read_raises_if_not_ready(self) -> None:
        bus = FakeTransport()
        bus.variant = None
        driver = _driver(bus)

        with pytest.raises(SensorDisconnectedError):
            list(driver.read())

    def test_read_raises_on_crc_mismatch(self) -> None:
        bus = FakeTransport()
        bus.variant = _valid_variant()
        # Create invalid CRC
        invalid_measurement = [0x01, 0xF4, 0x00, 0x66, 0x67, 0xA2, 0x5E, 0xB9, 0x3C]
        bus.measurement = invalid_measurement
        driver = _driver(bus)
        driver.initialize()

        with pytest.raises(SensorReadError):
            list(driver.read())

    def test_read_raises_on_i2c_error(self) -> None:
        bus = FakeTransport()
        bus.variant = _valid_variant()
        bus.measurement = _measurement_bytes(500, 26294, 32768)
        driver = _driver(bus)
        driver.initialize()

        # Simulate I2C error on read
        bus.fail_read = True

        with pytest.raises(SensorDisconnectedError):
            list(driver.read())

        assert driver.state is SensorState.DISCONNECTED


class TestVariant:
    def test_variant_scd40(self) -> None:
        bus = FakeTransport()
        bus.variant = _word_with_crc(0x0440)  # SCD40
        driver = _driver(bus)
        driver.initialize()

        assert driver.state is SensorState.READY

    def test_variant_scd41(self) -> None:
        bus = FakeTransport()
        bus.variant = _word_with_crc(0x1440)  # SCD41
        driver = _driver(bus)
        driver.initialize()

        assert driver.state is SensorState.READY

    def test_variant_scd43(self) -> None:
        bus = FakeTransport()
        bus.variant = _word_with_crc(0x5441)  # SCD43
        driver = _driver(bus)
        driver.initialize()

        assert driver.state is SensorState.READY
