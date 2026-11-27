"""Raspberry Pi host CPU temperature driver.

The kernel exposes the SoC temperature as millidegrees Celsius in a sysfs
thermal zone (``/sys/class/thermal/thermal_zone0/temp`` on the Pi). This is
host telemetry, **not** ambient air temperature, so it is published as its own
metric rather than as another ``temperature_c`` source.

No I2C bus, GPIO pin or external dependency is required. The sysfs file is
world-readable, so the unprivileged service user can read it directly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

from stingray.core.errors import SensorInitError, SensorReadError
from stingray.core.models import (
    METRIC_UNITS,
    Measurement,
    MetricId,
    SensorHealth,
    SensorId,
    SensorState,
    utc_now,
)
from stingray.core.sensor_protocol import SensorDriver

log = logging.getLogger(__name__)


class PiCpuTemperature(SensorDriver):
    """Publish the Pi's CPU/SoC temperature from a sysfs thermal zone."""

    DEFAULT_THERMAL_PATH = "/sys/class/thermal/thermal_zone0/temp"
    #: Sanity bounds for a Raspberry Pi SoC reading in Celsius.
    MIN_C = -40.0
    MAX_C = 125.0

    def __init__(
        self,
        thermal_path: str = DEFAULT_THERMAL_PATH,
        *,
        read_text: Callable[[Path], str] | None = None,
    ) -> None:
        self._path = Path(thermal_path)
        self._read_text = read_text or self._default_read_text
        self._state = SensorState.INITIALIZING
        self._last_read: datetime | None = None
        self._consecutive_failures = 0
        self._closed = False

    @staticmethod
    def _default_read_text(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    @property
    def sensor_id(self) -> SensorId:
        return SensorId.PI

    @property
    def state(self) -> SensorState:
        return self._state

    @property
    def thermal_path(self) -> Path:
        return self._path

    def initialize(self) -> None:
        """Validate the thermal zone and mark the driver ready."""
        if self._closed:
            raise SensorInitError("pi: thermal driver is closed")
        try:
            self._read_temperature_c()
        except SensorReadError as exc:
            self._state = SensorState.DISCONNECTED
            raise SensorInitError(f"pi: cannot read CPU temperature: {exc}") from exc
        self._state = SensorState.READY
        log.info("pi: CPU temperature ready from %s", self._path)

    def _read_temperature_c(self) -> float:
        raw = ""
        try:
            raw = self._read_text(self._path).strip()
            millidegrees = int(raw)
        except (OSError, ValueError, TypeError) as exc:
            raise SensorReadError(
                f"pi: invalid thermal zone reading from {self._path}: {raw!r}"
            ) from exc

        temp_c = millidegrees / 1000.0
        if not self.MIN_C <= temp_c <= self.MAX_C:
            raise SensorReadError(f"pi: implausible CPU temperature {temp_c:.1f} C")
        return temp_c

    def read(self) -> Iterator[Measurement]:
        """Yield one CPU temperature measurement."""
        if self._state is not SensorState.READY:
            raise SensorReadError("pi: thermal driver not ready")

        try:
            temp_c = self._read_temperature_c()
        except SensorReadError:
            self._consecutive_failures += 1
            raise

        self._consecutive_failures = 0
        now = utc_now()
        self._last_read = now
        yield Measurement(
            sensor=SensorId.PI,
            metric=MetricId.CPU_TEMPERATURE_C,
            value=round(temp_c, 1),
            unit=METRIC_UNITS[MetricId.CPU_TEMPERATURE_C],
            observed_at=now,
            received_at=now,
            diagnostics={"source": str(self._path)},
        )

    def health(self) -> SensorHealth:
        """Return the current health status without performing a read."""
        return SensorHealth(
            sensor=SensorId.PI,
            state=self._state,
            last_read_at=self._last_read,
            consecutive_failures=self._consecutive_failures,
        )

    def close(self) -> None:
        """Release resources. Idempotent and never raises."""
        self._closed = True
