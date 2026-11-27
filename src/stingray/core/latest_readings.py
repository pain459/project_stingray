"""Thread-safe cache of the latest reading per (sensor, metric) pair.

The storage drain loop is the sole drainer of the ``MeasurementQueue``.
It updates this cache after each drain, so other consumers (display, API)
can read the latest values without competing for the queue.
"""

from __future__ import annotations

import threading

from .models import Measurement, MetricId, SensorId


class LatestReadings:
    """Thread-safe cache of the most recent measurement per (sensor, metric)."""

    def __init__(self) -> None:
        self._latest: dict[tuple[SensorId, MetricId], Measurement] = {}
        self._lock = threading.Lock()

    def update(self, measurements: list[Measurement]) -> None:
        """Update the cache with a batch of measurements."""
        if not measurements:
            return
        with self._lock:
            for m in measurements:
                self._latest[(m.sensor, m.metric)] = m

    def get(self, sensor: SensorId, metric: MetricId) -> Measurement | None:
        """Get the latest measurement for a (sensor, metric) pair."""
        with self._lock:
            return self._latest.get((sensor, metric))

    def snapshot(self) -> dict[tuple[SensorId, MetricId], Measurement]:
        """Get a copy of the entire cache."""
        with self._lock:
            return dict(self._latest)
