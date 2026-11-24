"""Unit tests for core domain models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from stingray.core.models import (
    METRIC_UNITS,
    Measurement,
    MetricId,
    Quality,
    SensorHealth,
    SensorId,
    SensorState,
    utc_now,
)


def _measurement(**overrides: object) -> Measurement:
    base: dict = {
        "sensor": SensorId.SHT45,
        "metric": MetricId.TEMPERATURE_C,
        "value": 25.0,
        "unit": "degC",
        "observed_at": datetime(2026, 1, 1, tzinfo=UTC),
        "received_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return Measurement(**base)  # type: ignore[arg-type]


class TestMeasurement:
    def test_valid_measurement(self) -> None:
        m = _measurement()
        assert m.sensor is SensorId.SHT45
        assert m.metric is MetricId.TEMPERATURE_C
        assert m.value == 25.0
        assert m.quality is Quality.GOOD

    def test_rejects_naive_timestamps(self) -> None:
        with pytest.raises(ValidationError):
            _measurement(observed_at=datetime(2026, 1, 1))

    def test_rejects_nan_value(self) -> None:
        with pytest.raises(ValidationError):
            _measurement(value=float("nan"))

    def test_rejects_inf_value(self) -> None:
        with pytest.raises(ValidationError):
            _measurement(value=float("inf"))

    def test_rejects_mismatched_unit(self) -> None:
        with pytest.raises(ValidationError):
            _measurement(unit="ppm")

    def test_unit_must_match_metric(self) -> None:
        m = _measurement(
            metric=MetricId.CO2_PPM,
            value=450.0,
            unit="ppm",
            sensor=SensorId.SCD4X,
        )
        assert m.unit == "ppm"

    def test_is_stale(self) -> None:
        m = _measurement()
        now = m.observed_at
        assert not m.is_stale(now, threshold_seconds=60)
        later = datetime.fromtimestamp(m.observed_at.timestamp() + 120, tz=UTC)
        assert m.is_stale(later, threshold_seconds=60)

    def test_frozen(self) -> None:
        m = _measurement()
        with pytest.raises(ValidationError):
            m.value = 30.0  # type: ignore[misc]


class TestSensorHealth:
    def test_valid_health(self) -> None:
        h = SensorHealth(
            sensor=SensorId.SPS30,
            state=SensorState.READY,
            last_read_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert h.state is SensorState.READY

    def test_rejects_naive_last_read(self) -> None:
        with pytest.raises(ValidationError):
            SensorHealth(
                sensor=SensorId.SPS30,
                state=SensorState.READY,
                last_read_at=datetime(2026, 1, 1),
            )


class TestMetricUnits:
    def test_every_metric_has_a_unit(self) -> None:
        for metric in MetricId:
            assert metric in METRIC_UNITS
            assert METRIC_UNITS[metric]

    def test_utc_now_is_aware(self) -> None:
        now = utc_now()
        assert now.tzinfo is not None
        assert now.utcoffset() is not None
