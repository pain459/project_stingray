"""Tests for driver selection and incremental hardware onboarding."""

from __future__ import annotations

from stingray.core.config import AppConfig, SensorConfig
from stingray.core.models import SensorId
from stingray.main import build_drivers, build_hardware_drivers, build_mock_drivers
from stingray.sensors.mock import MockPiCpu, MockSCD4X, MockSGP41, MockSHT45, MockSPS30
from stingray.sensors.pi_cpu import PiCpuTemperature
from stingray.sensors.sht45 import SHT45


def _config(mode: str, **sensors: bool) -> AppConfig:
    return AppConfig(
        mode=mode,
        sensors={name: SensorConfig(enabled=enabled) for name, enabled in sensors.items()},
    )


class TestMockDrivers:
    def test_builds_one_mock_per_enabled_sensor(self) -> None:
        config = _config("mock", sht45=True, scd4x=True, sgp41=True, sps30=True, pi=True)

        drivers = build_mock_drivers(config)

        assert [type(d) for d in drivers] == [
            MockSHT45,
            MockSCD4X,
            MockSGP41,
            MockSPS30,
            MockPiCpu,
        ]
        assert [d.sensor_id for d in drivers] == [
            SensorId.SHT45,
            SensorId.SCD4X,
            SensorId.SGP41,
            SensorId.SPS30,
            SensorId.PI,
        ]

    def test_skips_disabled_sensors(self) -> None:
        config = _config("mock", sht45=True, scd4x=False)

        drivers = build_mock_drivers(config)

        assert [d.sensor_id for d in drivers] == [SensorId.SHT45]


class TestHardwareDrivers:
    def test_sht45_only_is_the_supported_onboarding_state(self) -> None:
        config = _config("hardware", sht45=True, scd4x=False, sgp41=False, sps30=False)

        drivers = build_hardware_drivers(config)

        assert len(drivers) == 1
        assert isinstance(drivers[0], SHT45)
        assert drivers[0].sensor_id is SensorId.SHT45
        assert drivers[0].bus_number == 1
        assert drivers[0].address == 0x44

    def test_uses_configured_bus_and_address(self) -> None:
        config = AppConfig(
            mode="hardware",
            sensors={"sht45": SensorConfig(enabled=True, i2c_bus=3, address=0x45)},
        )

        drivers = build_hardware_drivers(config)

        assert isinstance(drivers[0], SHT45)
        assert drivers[0].bus_number == 3
        assert drivers[0].address == 0x45

    def test_all_enabled_sensors_build_hardware_drivers(self) -> None:
        config = _config("hardware", sht45=True, scd4x=True, sgp41=True, sps30=True, pi=True)

        drivers = build_hardware_drivers(config)

        assert [d.sensor_id for d in drivers] == [
            SensorId.SHT45,
            SensorId.SPS30,
            SensorId.SCD4X,
            SensorId.SGP41,
            SensorId.PI,
        ]
        assert isinstance(drivers[-1], PiCpuTemperature)

    def test_gas_and_particle_sensors_are_hardware_ready(self) -> None:
        config = _config("hardware", sht45=False, scd4x=True, sgp41=True, sps30=True)

        drivers = build_hardware_drivers(config)

        assert [d.sensor_id for d in drivers] == [
            SensorId.SPS30,
            SensorId.SCD4X,
            SensorId.SGP41,
        ]

    def test_pi_uses_configured_thermal_path(self) -> None:
        config = AppConfig(
            mode="hardware",
            sensors={"pi": SensorConfig(enabled=True, thermal_path="/tmp/pi-temp")},
        )

        drivers = build_hardware_drivers(config)

        assert len(drivers) == 1
        assert isinstance(drivers[0], PiCpuTemperature)
        assert str(drivers[0].thermal_path) == "/tmp/pi-temp"

    def test_no_enabled_sensors_yields_no_drivers(self) -> None:
        config = _config("hardware", sht45=False, scd4x=False, sgp41=False, sps30=False)

        assert build_hardware_drivers(config) == []


class TestDispatch:
    def test_mock_mode_uses_mock_drivers(self) -> None:
        config = _config("mock", sht45=True)

        assert isinstance(build_drivers(config)[0], MockSHT45)

    def test_hardware_mode_uses_real_drivers(self) -> None:
        config = _config("hardware", sht45=True)

        assert isinstance(build_drivers(config)[0], SHT45)
