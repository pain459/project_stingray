"""Unit tests for the Pi host CPU temperature driver (no hardware required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from stingray.core.errors import SensorInitError, SensorReadError
from stingray.core.models import MetricId, SensorId, SensorState
from stingray.sensors.pi_cpu import PiCpuTemperature


def _driver(*values: str) -> PiCpuTemperature:
    iterator = iter(values)
    return PiCpuTemperature(read_text=lambda _path: next(iterator))


def test_initialize_and_read() -> None:
    # The first value validates the thermal zone during initialize().
    driver = _driver("63783", "63783")

    assert driver.sensor_id is SensorId.PI
    assert driver.state is SensorState.INITIALIZING
    driver.initialize()
    assert driver.state is SensorState.READY

    readings = list(driver.read())

    assert len(readings) == 1
    measurement = readings[0]
    assert measurement.sensor is SensorId.PI
    assert measurement.metric is MetricId.CPU_TEMPERATURE_C
    assert measurement.unit == "degC"
    assert measurement.value == 63.8
    assert measurement.quality.value == "good"
    assert measurement.observed_at.tzinfo is not None
    assert measurement.diagnostics["source"] == str(Path("/sys/class/thermal/thermal_zone0/temp"))

    health = driver.health()
    assert health.state is SensorState.READY
    assert health.last_read_at is not None
    assert health.consecutive_failures == 0


def test_initialize_rejects_invalid_thermal_value() -> None:
    with pytest.raises(SensorInitError):
        _driver("not-a-number").initialize()


def test_initialize_rejects_implausible_temperature() -> None:
    with pytest.raises(SensorInitError):
        _driver("999999").initialize()


def test_read_failure_is_recorded_in_health() -> None:
    driver = _driver("63783", "garbage")
    driver.initialize()

    with pytest.raises(SensorReadError):
        list(driver.read())

    assert driver.health().consecutive_failures == 1
    assert driver.health().state is SensorState.READY


def test_close_is_idempotent() -> None:
    driver = _driver("63783")
    driver.initialize()

    driver.close()
    driver.close()

    with pytest.raises(SensorInitError):
        driver.initialize()
