"""Sensor driver protocol and shared scheduling primitives.

A driver is any object implementing :class:`SensorDriver`. The scheduler
only depends on this protocol, never on concrete hardware classes, so mock
and hardware drivers are interchangeable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field

from stingray.core.errors import SensorError
from stingray.core.models import Measurement, SensorHealth, SensorId, SensorState


class SensorDriver(ABC):
    """Contract every sensor driver (mock or hardware) must satisfy."""

    @property
    @abstractmethod
    def sensor_id(self) -> SensorId:
        """Canonical identifier of the sensor."""

    @abstractmethod
    def initialize(self) -> None:
        """Bring the sensor to a ready state.

        Raises:
            SensorInitError: if the sensor cannot be initialized.
        """

    @abstractmethod
    def read(self) -> Iterator[Measurement]:
        """Perform one acquisition cycle.

        Yields zero or more measurements. The driver is responsible for
        honoring its own data-ready timing; the scheduler only calls this
        when the sensor's cadence has elapsed.

        Raises:
            SensorReadError: on timeout, CRC error, or bus failure.
            SensorDisconnectedError: if the sensor is absent.
        """

    @abstractmethod
    def health(self) -> SensorHealth:
        """Return the current health snapshot without performing a read."""

    @abstractmethod
    def close(self) -> None:
        """Release resources. Must be idempotent and never raise."""


@dataclass
class CadenceSpec:
    """Scheduling parameters for one sensor."""

    sensor: SensorId
    interval_seconds: float
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    stale_after_seconds: float = field(default=0.0)  # 0 = no staleness policy

    def __post_init__(self) -> None:
        if self.interval_seconds < 0:
            raise ValueError(f"{self.sensor.value}: interval must be >= 0")
        if self.max_retries < 0:
            raise ValueError(f"{self.sensor.value}: max_retries must be >= 0")
        if self.retry_backoff_seconds < 0:
            raise ValueError(f"{self.sensor.value}: retry_backoff must be >= 0")


class SensorFailure(Exception):
    """Wraps a driver error with the sensor it came from.

    The scheduler catches :class:`SensorError` directly; this wrapper is
    used by the scheduler to record failures without propagating them.
    """

    def __init__(self, sensor: SensorId, error: SensorError) -> None:
        super().__init__(f"{sensor.value}: {error}")
        self.sensor = sensor
        self.error = error


def state_from_failures(consecutive_failures: int, *, disconnected: bool) -> SensorState:
    """Map a failure count to a sensor state.

    0 failures -> READY, 1-2 -> DEGRADED, >=3 -> FAILED.
    A disconnected sensor is always DISCONNECTED regardless of count.
    """
    if disconnected:
        return SensorState.DISCONNECTED
    if consecutive_failures == 0:
        return SensorState.READY
    if consecutive_failures < 3:
        return SensorState.DEGRADED
    return SensorState.FAILED


def retry_delay(spec: CadenceSpec, attempt: int) -> float:
    """Exponential backoff delay for the given 1-based attempt number."""
    if attempt <= 1:
        return 0.0
    return float(spec.retry_backoff_seconds * (2 ** (attempt - 2)))


def is_driver_error(exc: BaseException) -> bool:
    """True if the exception is a sensor-layer error (not a bug)."""
    return isinstance(exc, SensorError)


def describe_failure(error: SensorError) -> str:
    """One-line, log-safe description of a sensor failure."""
    return f"{type(error).__name__}: {error}"


AnyDriver = SensorDriver  # alias for type annotations in the scheduler
