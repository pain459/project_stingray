"""Sensor scheduler: independent cadences, failure isolation, clean shutdown.

The scheduler runs each sensor on its own cadence using a monotonic clock.
A failure in one sensor never stops the others. All measurements are pushed
into a bounded in-memory queue that downstream consumers (storage, API,
display, alerts) drain.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from stingray.core.errors import SensorError
from stingray.core.models import Measurement, SensorHealth, SensorId
from stingray.core.sensor_protocol import CadenceSpec, SensorDriver, retry_delay

log = logging.getLogger("stingray.scheduler")


@dataclass
class SchedulerStats:
    """Per-sensor counters for observability."""

    reads_ok: int = 0
    reads_failed: int = 0
    measurements: int = 0
    last_read_at: float = 0.0  # monotonic
    last_error: str = ""


class MeasurementQueue:
    """Bounded in-memory queue of measurements, newest last.

    Consumers call :meth:`drain` to atomically take all pending items.
    """

    def __init__(self, maxlen: int = 10_000) -> None:
        self._queue: deque[Measurement] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.dropped = 0

    def put(self, measurement: Measurement) -> None:
        with self._lock:
            if len(self._queue) == self._queue.maxlen:
                self.dropped += 1
            self._queue.append(measurement)

    def drain(self) -> list[Measurement]:
        with self._lock:
            items = list(self._queue)
            self._queue.clear()
            return items

    def __len__(self) -> int:
        with self._lock:
            return len(self._queue)


class SensorScheduler:
    """Runs multiple sensors on independent cadences in one thread.

    The scheduler is single-threaded by design: one loop, one sensor at a
    time, scheduled by monotonic time. This keeps I2C transactions
    serialized without locks and makes behavior deterministic.
    """

    def __init__(
        self,
        drivers: list[SensorDriver],
        specs: dict[SensorId, CadenceSpec],
        queue: MeasurementQueue | None = None,
        *,
        tick_seconds: float = 0.1,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        self._drivers = {d.sensor_id: d for d in drivers}
        self._specs = specs
        self._queue = queue if queue is not None else MeasurementQueue()
        self._tick = tick_seconds
        self._sleep = sleep_func
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._stats: dict[SensorId, SchedulerStats] = {
            sid: SchedulerStats() for sid in self._drivers
        }
        self._next_due: dict[SensorId, float] = {}
        self._retry_attempt: dict[SensorId, int] = {sid: 0 for sid in self._drivers}
        self._lock = threading.Lock()

    @property
    def queue(self) -> MeasurementQueue:
        return self._queue

    @property
    def stats(self) -> dict[SensorId, SchedulerStats]:
        with self._lock:
            return {k: SchedulerStats(**vars(v)) for k, v in self._stats.items()}

    def health(self) -> dict[SensorId, SensorHealth]:
        return {sid: driver.health() for sid, driver in self._drivers.items()}

    def start(self) -> None:
        """Initialize all drivers and start the scheduler thread."""
        for sid, driver in self._drivers.items():
            driver.initialize()
            self._next_due[sid] = 0.0  # due immediately
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="stingray-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Signal stop and wait for the loop to exit within ``timeout`` seconds."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        for driver in self._drivers.values():
            driver.close()

    def run_cycles(self, cycles: int) -> None:
        """Run exactly ``cycles`` loop iterations synchronously (for tests)."""
        for sid, driver in self._drivers.items():
            driver.initialize()
            self._next_due[sid] = 0.0
        for _ in range(cycles):
            self._tick_once(time.monotonic())

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._tick_once(time.monotonic())
            self._sleep(self._tick)

    def _tick_once(self, now_mono: float) -> None:
        for sid in sorted(self._drivers):
            spec = self._specs[sid]
            if spec.interval_seconds > 0 and now_mono < self._next_due[sid]:
                continue
            driver = self._drivers[sid]
            try:
                measurements = list(driver.read())
            except SensorError as exc:
                self._handle_failure(sid, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - a driver bug must not kill the loop
                self._handle_failure(sid, SensorError(f"unexpected: {exc!r}"))
                continue
            self._handle_success(sid, measurements)

    def _handle_success(self, sid: SensorId, measurements: list[Measurement]) -> None:
        spec = self._specs[sid]
        for m in measurements:
            self._queue.put(m)
        with self._lock:
            self._stats[sid].reads_ok += 1
            self._stats[sid].measurements += len(measurements)
            self._stats[sid].last_read_at = time.monotonic()
            self._stats[sid].last_error = ""
        self._retry_attempt[sid] = 0
        self._next_due[sid] = time.monotonic() + spec.interval_seconds

    def _handle_failure(self, sid: SensorId, error: SensorError) -> None:
        spec = self._specs[sid]
        attempt = self._retry_attempt[sid] + 1
        self._retry_attempt[sid] = attempt
        with self._lock:
            self._stats[sid].reads_failed += 1
            self._stats[sid].last_error = f"{type(error).__name__}: {error}"
        log.warning("sensor %s read failed (attempt %d): %s", sid.value, attempt, error)
        # Always schedule a retry; back off briefly while retries remain,
        # then fall back to the normal interval. The sensor can recover at
        # any retry, so we never give up permanently.
        if attempt <= spec.max_retries:
            delay = retry_delay(spec, attempt)
            self._next_due[sid] = time.monotonic() + max(delay, self._tick)
        else:
            self._retry_attempt[sid] = 0
            self._next_due[sid] = time.monotonic() + spec.interval_seconds


def build_specs_from_config(
    sensor_configs: dict[str, Any],
) -> dict[SensorId, CadenceSpec]:
    """Build cadence specs from a mapping of sensor name -> config dict."""
    specs: dict[SensorId, CadenceSpec] = {}
    for name, cfg in sensor_configs.items():
        try:
            sid = SensorId(name)
        except ValueError:
            continue
        specs[sid] = CadenceSpec(
            sensor=sid,
            interval_seconds=float(cfg.get("cadence_seconds", 5.0)),
        )
    return specs
