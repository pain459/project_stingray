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
from stingray.core.errors import ConfigError, SensorInitError
from stingray.core.models import SensorId
from stingray.core.scheduler import MeasurementQueue, SensorScheduler, build_specs_from_config
from stingray.core.sensor_protocol import CadenceSpec, SensorDriver
from stingray.sensors.mock import MockSCD4X, MockSGP41, MockSHT45, MockSPS30
from stingray.sensors.scd4x import SCD4X
from stingray.sensors.sht45 import SHT45
from stingray.sensors.sps30 import SPS30
from stingray.storage.parquet_store import ParquetWriter, apply_retention

log = logging.getLogger("stingray")

#: Hardware drivers that exist today. Onboarding is deliberately incremental:
#: a sensor listed here has a real driver, everything else must stay disabled
#: in hardware mode so a half-finished bring-up cannot silently produce gaps.
_HARDWARE_READY: frozenset[SensorId] = frozenset({SensorId.SHT45, SensorId.SCD4X, SensorId.SPS30})

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


def build_hardware_drivers(config: AppConfig) -> list[SensorDriver]:
    """Build the set of real drivers for enabled sensors.

    Sensors are onboarded one at a time, so any enabled sensor without a real
    driver is a configuration error rather than a silent no-op.

    Raises:
        ConfigError: if an enabled sensor has no hardware driver yet.
    """
    drivers: list[SensorDriver] = []
    unsupported = [
        sid
        for sid in config.enabled_sensors()
        if sid not in _HARDWARE_READY
    ]
    if unsupported:
        names = ", ".join(sid.value for sid in unsupported)
        raise ConfigError(
            f"no hardware driver yet for: {names}. "
            f"Hardware-ready sensors: {', '.join(sorted(s.value for s in _HARDWARE_READY))}. "
            f"Set the others to 'enabled: false' while bringing sensors up one at a time."
        )

    if config.sensor_config(SensorId.SHT45).enabled:
        sht = config.sensor_config(SensorId.SHT45)
        # SensorConfig.address defaults to 0, meaning "not specified"; fall
        # back to the sensor's datasheet address rather than probing 0x00.
        drivers.append(
            SHT45(
                bus_number=sht.i2c_bus,
                address=sht.address or SHT45.DEFAULT_ADDRESS,
            )
        )
    if config.sensor_config(SensorId.SPS30).enabled:
        sps = config.sensor_config(SensorId.SPS30)
        drivers.append(
            SPS30(
                bus_number=sps.i2c_bus,
                address=sps.address or SPS30.DEFAULT_ADDRESS,
            )
        )
    return drivers


def build_drivers(config: AppConfig) -> list[SensorDriver]:
    """Build the driver set for the configured mode (mock or hardware)."""
    if config.mode == "hardware":
        return build_hardware_drivers(config)
    return build_mock_drivers(config)


def run(config: AppConfig, *, duration_seconds: float | None = None) -> int:
    """Run the application loop. Returns a process exit code."""
    drivers = build_drivers(config)
    if not drivers:
        log.error(
            "no drivers to run (mode=%s); enable at least one sensor in the config",
            config.mode,
        )
        return 1
    specs = build_specs_from_config(
        {name: cfg.model_dump() for name, cfg in config.sensors.items()}
    )
    if not specs:
        # No explicit sensor config: use defaults for all enabled sensors.
        for sid in config.enabled_sensors():
            specs[sid] = _default_spec(sid)

    queue = MeasurementQueue()
    scheduler = SensorScheduler(drivers, specs, queue)

    storage_path = config.storage.path
    writer = ParquetWriter(storage_path, batch_size=config.storage.batch_size)

    stop_event = threading.Event()

    def _drain_loop() -> None:
        while not stop_event.is_set():
            items = queue.drain()
            if items:
                writer.add_many(items)
            time.sleep(0.5)

    drain_thread = threading.Thread(target=_drain_loop, name="stingray-drain", daemon=True)
    drain_thread.start()

    try:
        scheduler.start()
    except SensorInitError as exc:
        # A deployment error (missing hardware library, I2C disabled) is not
        # recoverable at runtime: log it plainly and exit instead of dumping a
        # traceback into the service log.
        log.error("sensor initialization failed: %s", exc)
        stop_event.set()
        drain_thread.join(timeout=5)
        scheduler.stop(timeout=10)
        writer.flush()
        return 1
    log.info("scheduler started with sensors: %s", [d.sensor_id.value for d in drivers])

    # Start the dashboard API server (unauthenticated, internal use).
    api_state = ApiState(scheduler, storage_path)
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
                deleted = apply_retention(storage_path, config.storage.retention_days)
                if deleted:
                    log.info("retention purge deleted %d day files", deleted)
                last_retention = now
    finally:
        stop_event.set()
        api_server.shutdown()
        drain_thread.join(timeout=5)
        scheduler.stop(timeout=10)
        flushed = writer.flush()
        if flushed:
            log.info("flushed %d buffered measurements", flushed)
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
        config.storage = config.storage.model_copy(update={"path": str(args.db)})
    if args.port is not None:
        config.api = config.api.model_copy(update={"port": args.port})

    # When no config file is found, no sensors are enabled. Running the full
    # scheduler loop with zero sensors is a no-op that blocks forever, so in
    # that case we only run when an explicit --duration was given.
    if not config_path.exists() and args.duration is None:
        log.info("no sensors configured; nothing to do (use --config or --duration)")
        return 0

    log.info("stingray starting (mode=%s)", config.mode)
    try:
        return run(config, duration_seconds=args.duration)
    except ConfigError as exc:
        log.error("configuration error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
