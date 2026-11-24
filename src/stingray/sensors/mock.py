"""Deterministic mock sensors for hardware-free development and testing.

Each mock sensor implements the :class:`SensorDriver` protocol and produces
a deterministic, seeded sequence of readings. Failures can be injected
programmatically to exercise the scheduler's recovery paths.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import datetime

from stingray.core.errors import (
    SensorDisconnectedError,
    SensorInitError,
    SensorReadError,
    SensorStaleError,
)
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
from stingray.core.sensor_protocol import SensorDriver


class MockSensor(SensorDriver):
    """Base class for all mock sensors.

    Args:
        sensor: which sensor to emulate.
        seed: RNG seed for deterministic readings.
        start_value: initial value for the primary metric.
        drift: per-cycle random walk magnitude for the primary metric.
    """

    def __init__(
        self,
        sensor: SensorId,
        *,
        seed: int = 0,
        start_value: float = 0.0,
        drift: float = 0.1,
    ) -> None:
        self._sensor = sensor
        self._rng = random.Random(seed)
        self._start_value = start_value
        self._drift = drift
        self._value = start_value
        self._initialized = False
        self._closed = False
        self._consecutive_failures = 0
        self._last_read_wall: datetime | None = None
        self._disconnected = False
        self._fail_next_reads = 0
        self._fail_mode: str = "timeout"

    @property
    def sensor_id(self) -> SensorId:
        return self._sensor

    def initialize(self) -> None:
        if self._closed:
            raise SensorInitError(f"{self._sensor.value}: sensor is closed")
        self._initialized = True

    def close(self) -> None:
        self._closed = True

    # -- failure injection -------------------------------------------------

    def inject_failure(self, mode: str = "timeout", count: int = 1) -> None:
        """Make the next ``count`` reads fail with the given mode.

        Modes: ``timeout``, ``crc``, ``stale``, ``disconnected``.
        """
        if mode not in {"timeout", "crc", "stale", "disconnected"}:
            raise ValueError(f"unknown failure mode: {mode}")
        self._fail_mode = mode
        self._fail_next_reads = count

    def set_disconnected(self, disconnected: bool = True) -> None:
        """Permanently mark the sensor as disconnected until re-initialized."""
        self._disconnected = disconnected

    # -- driver protocol ---------------------------------------------------

    def read(self) -> Iterator[Measurement]:
        if not self._initialized:
            raise SensorInitError(f"{self._sensor.value}: not initialized")
        if self._closed:
            raise SensorReadError(f"{self._sensor.value}: sensor is closed")
        if self._disconnected:
            self._consecutive_failures += 1
            raise SensorDisconnectedError(f"{self._sensor.value}: no device on bus")
        if self._fail_next_reads > 0:
            self._fail_next_reads -= 1
            self._consecutive_failures += 1
            self._raise_injected_failure()
        self._advance()
        self._consecutive_failures = 0
        now = utc_now()
        self._last_read_wall = now
        yield from self._measurements(now)

    def health(self) -> SensorHealth:
        if self._disconnected:
            state = SensorState.DISCONNECTED
        elif not self._initialized:
            state = SensorState.INITIALIZING
        else:
            state = _state_from_failures(self._consecutive_failures)
        return SensorHealth(
            sensor=self._sensor,
            state=state,
            last_read_at=self._last_read_wall,
            consecutive_failures=self._consecutive_failures,
            message="" if state == SensorState.READY else f"state={state.value}",
        )

    # -- internals ---------------------------------------------------------

    def _raise_injected_failure(self) -> None:
        if self._fail_mode == "timeout":
            raise SensorReadError(f"{self._sensor.value}: read timeout")
        if self._fail_mode == "crc":
            raise SensorReadError(f"{self._sensor.value}: CRC mismatch")
        if self._fail_mode == "stale":
            raise SensorStaleError(f"{self._sensor.value}: data not ready")
        raise SensorDisconnectedError(f"{self._sensor.value}: no device on bus")

    def _advance(self) -> None:
        self._value += self._rng.uniform(-self._drift, self._drift)

    def _measurements(self, now: datetime) -> Iterator[Measurement]:
        raise NotImplementedError

    def _measurement(
        self,
        metric: MetricId,
        value: float,
        observed_at: datetime,
        quality: Quality = Quality.GOOD,
    ) -> Measurement:
        return Measurement(
            sensor=self._sensor,
            metric=metric,
            value=value,
            unit=METRIC_UNITS[metric],
            observed_at=observed_at,
            received_at=utc_now(),
            quality=quality,
        )


class MockSHT45(MockSensor):
    """Emulates SHT45 temperature + relative humidity."""

    def __init__(self, *, seed: int = 0, temp_c: float = 25.0, rh_percent: float = 50.0) -> None:
        super().__init__(SensorId.SHT45, seed=seed, start_value=temp_c, drift=0.05)
        self._rh = rh_percent

    def _measurements(self, now: datetime) -> Iterator[Measurement]:
        temp = max(-40.0, min(125.0, self._value))
        rh = max(0.0, min(100.0, self._rh + self._rng.uniform(-0.2, 0.2)))
        yield self._measurement(MetricId.TEMPERATURE_C, round(temp, 2), now)
        yield self._measurement(MetricId.RELATIVE_HUMIDITY_PERCENT, round(rh, 2), now)


class MockSCD4X(MockSensor):
    """Emulates SCD4x CO2 + temperature + relative humidity."""

    def __init__(self, *, seed: int = 0, co2_ppm: float = 450.0) -> None:
        super().__init__(SensorId.SCD4X, seed=seed, start_value=co2_ppm, drift=2.0)
        self._temp = 25.0
        self._rh = 50.0

    def _measurements(self, now: datetime) -> Iterator[Measurement]:
        co2 = max(0.0, self._value)
        self._temp += self._rng.uniform(-0.05, 0.05)
        self._rh = max(0.0, min(100.0, self._rh + self._rng.uniform(-0.2, 0.2)))
        yield self._measurement(MetricId.CO2_PPM, round(co2, 1), now)
        yield self._measurement(MetricId.TEMPERATURE_C, round(self._temp, 2), now)
        yield self._measurement(MetricId.RELATIVE_HUMIDITY_PERCENT, round(self._rh, 2), now)


class MockSGP41(MockSensor):
    """Emulates SGP41 raw VOC/NOx signals plus gas index values."""

    def __init__(self, *, seed: int = 0, voc_raw: float = 200.0, nox_raw: float = 100.0) -> None:
        super().__init__(SensorId.SGP41, seed=seed, start_value=voc_raw, drift=1.0)
        self._nox_raw = nox_raw
        self._voc_index = 0.0
        self._nox_index = 0.0

    def _measurements(self, now: datetime) -> Iterator[Measurement]:
        voc_raw = max(0.0, self._value)
        self._nox_raw = max(0.0, self._nox_raw + self._rng.uniform(-0.5, 0.5))
        # Gas index tracks raw signal loosely; real algorithm is Phase 05.
        self._voc_index = max(0.0, min(500.0, voc_raw / 4.0))
        self._nox_index = max(0.0, min(500.0, self._nox_raw / 2.0))
        yield self._measurement(MetricId.VOC_RAW, round(voc_raw, 1), now)
        yield self._measurement(MetricId.NOX_RAW, round(self._nox_raw, 1), now)
        yield self._measurement(MetricId.VOC_INDEX, round(self._voc_index, 1), now)
        yield self._measurement(MetricId.NOX_INDEX, round(self._nox_index, 1), now)


class MockSPS30(MockSensor):
    """Emulates SPS30 particulate mass and number concentrations."""

    def __init__(self, *, seed: int = 0, pm25_ug_m3: float = 12.0) -> None:
        super().__init__(SensorId.SPS30, seed=seed, start_value=pm25_ug_m3, drift=0.5)
        self._pm1 = pm25_ug_m3 * 0.5
        self._pm4 = pm25_ug_m3 * 1.2
        self._pm10 = pm25_ug_m3 * 1.5

    def _measurements(self, now: datetime) -> Iterator[Measurement]:
        pm25 = max(0.0, self._value)
        self._pm1 = max(0.0, self._pm1 + self._rng.uniform(-0.2, 0.2))
        self._pm4 = max(0.0, self._pm4 + self._rng.uniform(-0.3, 0.3))
        self._pm10 = max(0.0, self._pm10 + self._rng.uniform(-0.4, 0.4))
        yield self._measurement(MetricId.PM1_UG_M3, round(self._pm1, 2), now)
        yield self._measurement(MetricId.PM2_5_UG_M3, round(pm25, 2), now)
        yield self._measurement(MetricId.PM4_UG_M3, round(self._pm4, 2), now)
        yield self._measurement(MetricId.PM10_UG_M3, round(self._pm10, 2), now)
        yield self._measurement(MetricId.PM1_NUM_M3, round(self._pm1 * 100, 0), now)
        yield self._measurement(MetricId.PM2_5_NUM_M3, round(pm25 * 100, 0), now)
        yield self._measurement(MetricId.PM4_NUM_M3, round(self._pm4 * 100, 0), now)
        yield self._measurement(MetricId.PM10_NUM_M3, round(self._pm10 * 100, 0), now)


def _state_from_failures(count: int) -> SensorState:
    if count == 0:
        return SensorState.READY
    if count < 3:
        return SensorState.DEGRADED
    return SensorState.FAILED
