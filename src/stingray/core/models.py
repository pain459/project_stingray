"""Core domain models: measurements, sensor health, and quality.

These models are the contract between the sensor layer (mock or hardware),
the processing layer, storage, and the API. They are hardware-independent
and must never import from ``stingray.hardware``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SensorId(StrEnum):
    """Canonical sensor identifiers."""

    SHT45 = "sht45"
    SCD4X = "scd4x"
    SGP41 = "sgp41"
    SPS30 = "sps30"


class MetricId(StrEnum):
    """Canonical metric identifiers with fixed units.

    Units are part of the identity; a metric ID implies exactly one unit.
    """

    PM1_UG_M3 = "pm1_ug_m3"
    PM2_5_UG_M3 = "pm2_5_ug_m3"
    PM4_UG_M3 = "pm4_ug_m3"
    PM10_UG_M3 = "pm10_ug_m3"
    PM1_NUM_M3 = "pm1_num_m3"
    PM2_5_NUM_M3 = "pm2_5_num_m3"
    PM4_NUM_M3 = "pm4_num_m3"
    PM10_NUM_M3 = "pm10_num_m3"
    #: SPS30 typical particle size (SPS30 response word 9).
    TPS_UM = "tps_um"
    CO2_PPM = "co2_ppm"
    TEMPERATURE_C = "temperature_c"
    RELATIVE_HUMIDITY_PERCENT = "relative_humidity_percent"
    VOC_INDEX = "voc_index"
    NOX_INDEX = "nox_index"
    VOC_RAW = "voc_raw"
    NOX_RAW = "nox_raw"


#: The single unit implied by each metric ID.
METRIC_UNITS: dict[MetricId, str] = {
    MetricId.PM1_UG_M3: "ug/m3",
    MetricId.PM2_5_UG_M3: "ug/m3",
    MetricId.PM4_UG_M3: "ug/m3",
    MetricId.PM10_UG_M3: "ug/m3",
    MetricId.PM1_NUM_M3: "1/m3",
    MetricId.PM2_5_NUM_M3: "1/m3",
    MetricId.PM4_NUM_M3: "1/m3",
    MetricId.PM10_NUM_M3: "1/m3",
    MetricId.TPS_UM: "um",
    MetricId.CO2_PPM: "ppm",
    MetricId.TEMPERATURE_C: "degC",
    MetricId.RELATIVE_HUMIDITY_PERCENT: "%",
    MetricId.VOC_INDEX: "index",
    MetricId.NOX_INDEX: "index",
    MetricId.VOC_RAW: "raw",
    MetricId.NOX_RAW: "raw",
}

#: Metrics that must be finite, non-negative physical quantities.
_NON_NEGATIVE_METRICS = frozenset(
    {
        MetricId.PM1_UG_M3,
        MetricId.PM2_5_UG_M3,
        MetricId.PM4_UG_M3,
        MetricId.PM10_UG_M3,
        MetricId.PM1_NUM_M3,
        MetricId.PM2_5_NUM_M3,
        MetricId.PM4_NUM_M3,
        MetricId.PM10_NUM_M3,
        MetricId.TPS_UM,
        MetricId.CO2_PPM,
        MetricId.RELATIVE_HUMIDITY_PERCENT,
        MetricId.VOC_INDEX,
        MetricId.NOX_INDEX,
    }
)


class Quality(StrEnum):
    """Data quality classification for a single measurement."""

    GOOD = "good"
    ESTIMATED = "estimated"
    STALE = "stale"
    INVALID = "invalid"


class SensorState(StrEnum):
    """Lifecycle state of a sensor driver."""

    INITIALIZING = "initializing"
    READY = "ready"
    STALE = "stale"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


class Measurement(BaseModel):
    """A single validated sensor reading.

    ``observed_at`` is when the sensor took the reading; ``received_at`` is
    when the application ingested it. Both are timezone-aware UTC.
    """

    model_config = ConfigDict(frozen=True)

    sensor: SensorId
    metric: MetricId
    value: float
    unit: str
    observed_at: datetime
    received_at: datetime
    quality: Quality = Quality.GOOD
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at", "received_at")
    @classmethod
    def _require_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("value")
    @classmethod
    def _value_is_finite(cls, value: float) -> float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("measurement value must be finite")
        return value

    @field_validator("unit")
    @classmethod
    def _unit_matches_metric(cls, unit: str, info: Any) -> str:
        metric = info.data.get("metric")
        if isinstance(metric, MetricId) and METRIC_UNITS[metric] != unit:
            raise ValueError(f"unit {unit!r} does not match metric {metric.value}")
        return unit

    def is_stale(self, now: datetime, threshold_seconds: float) -> bool:
        """Return True if the reading is older than the threshold."""
        age = (now - self.observed_at).total_seconds()
        return age > threshold_seconds


class SensorHealth(BaseModel):
    """Health snapshot for one sensor, produced by ``health()``."""

    model_config = ConfigDict(frozen=True)

    sensor: SensorId
    state: SensorState
    last_read_at: datetime | None = None
    consecutive_failures: int = 0
    message: str = ""

    @field_validator("last_read_at")
    @classmethod
    def _require_tz_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("last_read_at must be timezone-aware")
        return value.astimezone(UTC) if value is not None else None


def utc_now() -> datetime:
    """Timezone-aware UTC now, the single source of time for new records."""
    return datetime.now(UTC)
