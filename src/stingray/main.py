"""Stingray application entry point.

Loads configuration, builds the sensor set (mock or hardware), starts the
scheduler, and runs until SIGTERM/SIGINT. In mock mode the application is
fully hardware-independent.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path

from stingray.api.server import ApiState, start_api_server
from stingray.core.config import AppConfig, load_config
from stingray.core.errors import ConfigError
from stingray.core.models import SensorId
from stingray.core.scheduler import MeasurementQueue, SensorScheduler, build_specs_from_config
from stingray.core.sensor_protocol import CadenceSpec, SensorDriver
from stingray.sensors.mock import MockSCD4X, MockSGP41, MockSHT45, MockSPS30
from stingray.storage.sqlite_store import (
    Database,
    MeasurementWriter,
    apply_retention,
)

log = logging.getLogger("stingray")

_DEFAULT_CONFIG = (
    Path(__file__).resolve().parent.parent.parent.parent / "config" / "application.example.yaml"
)


def build_mock_drivers(config: AppConfig) -> list[SensorDriver]:
    """Build the set of mock drivers for enabled sensors."""
    drivers: list[SensorDriver] = []
    if config.sensor_config(SensorId.SHT45).enabled:
        drivers.append(MockSHT45())
    if config.sensor_config(SensorId.SCD4X).enabled:
        drivers.append(MockSCD4X())
    if config.sensor_config(SensorId.SGP41).enabled:
        drivers.append(MockSGP41())
    if config.sensor_config(SensorId.SPS30).enabled:
        drivers.append(MockSPS30())
    return drivers


def run(config: AppConfig, *, duration_seconds: float | None = None) -> int:
    """Run the application loop. Returns a process exit code."""
    drivers = build_mock_drivers(config)
    specs = build_specs_from_config(
        {name: cfg.model_dump() for name, cfg in config.sensors.items()}
    )
    if not specs:
        # No explicit sensor config: use defaults for all enabled sensors.
        for sid in config.enabled_sensors():
            specs[sid] = _default_spec(sid)

    queue = MeasurementQueue()
    scheduler = SensorScheduler(drivers, specs, queue)

    db_path = config.database.path
    db = Database(db_path, wal=config.database.wal_mode)
    writer = MeasurementWriter(db, batch_size=config.database.batch_size)

    stop_event = threading.Event()

    def _drain_loop() -> None:
        while not stop_event.is_set():
            items = queue.drain()
            if items:
                writer.add_many(items)
            time.sleep(0.5)

    drain_thread = threading.Thread(target=_drain_loop, name="stingray-drain", daemon=True)
    drain_thread.start()

    scheduler.start()
    log.info("scheduler started with sensors: %s", [d.sensor_id.value for d in drivers])

    # Start the dashboard API server (unauthenticated, internal use).
    api_state = ApiState(scheduler, db_path)
    api_server = start_api_server(config.api.host, config.api.port, api_state)

    def _handle_signal(signum: int, _frame: object) -> None:
        log.info("received signal %d, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    start = time.monotonic()
    last_retention = start
    try:
        while not stop_event.is_set():
            time.sleep(0.5)
            now = time.monotonic()
            if duration_seconds is not None and (now - start) >= duration_seconds:
                log.info("duration reached, stopping")
                break
            if now - last_retention > 3600:
                deleted = apply_retention(db, config.database.retention_days)
                if deleted:
                    log.info("retention purge deleted %d rows", deleted)
                last_retention = now
    finally:
        stop_event.set()
        api_server.shutdown()
        drain_thread.join(timeout=5)
        scheduler.stop(timeout=10)
        flushed = writer.flush()
        if flushed:
            log.info("flushed %d buffered measurements", flushed)
        db.checkpoint()
        db.close()
        log.info("shutdown complete; total measurements written: %d", writer.total_written)
    return 0


def _default_spec(sid: SensorId) -> CadenceSpec:
    defaults = {
        SensorId.SHT45: 5.0,
        SensorId.SCD4X: 5.0,
        SensorId.SGP41: 1.0,
        SensorId.SPS30: 10.0,
    }
    return CadenceSpec(sensor=sid, interval_seconds=defaults.get(sid, 5.0))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stingray", description="Stingray air-quality monitor")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to application YAML config (default: config/application.example.yaml)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Run for N seconds then exit (for testing)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Override the database path from config",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override the API port from config",
    )
    args, _unknown = parser.parse_known_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    config_path = args.config if args.config is not None else _DEFAULT_CONFIG
    if not config_path.exists():
        log.warning("config %s not found; using built-in defaults", config_path)
        config = AppConfig()
    else:
        try:
            config = load_config(config_path)
        except ConfigError as exc:
            log.error("configuration error: %s", exc)
            return 1
    if args.db is not None:
        config.database = config.database.model_copy(update={"path": str(args.db)})
    if args.port is not None:
        config.api = config.api.model_copy(update={"port": args.port})

    # When no config file is found, no sensors are enabled. Running the full
    # scheduler loop with zero sensors is a no-op that blocks forever, so in
    # that case we only run when an explicit --duration was given.
    if not config_path.exists() and args.duration is None:
        log.info("no sensors configured; nothing to do (use --config or --duration)")
        return 0

    log.info("stingray starting (mode=%s)", config.mode)
    return run(config, duration_seconds=args.duration)


if __name__ == "__main__":
    sys.exit(main())
