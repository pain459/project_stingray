# Phase 04 Acceptance Report

- **Phase / Jira epic:** Phase 04 — Application foundation and mock mode (`STG-P04`)
- **Date and implementer:** 2026-11-24, AI agent (executed on `stingray` via SSH)
- **Git SHA (base):** `2675f8f` (Phase 04/06 work committed on top of this)
- **OS / kernel / Python:** Debian 13 (trixie) / `6.18.60+rpt-rpi-v8` / Python 3.13.5
- **Hardware revisions / wiring version:** Pi 4 Model B Rev 1.1; **no sensors wired** (mock mode only)
- **Configuration revision (secrets redacted):** `config/application.example.yaml`, `pyproject.toml` v0.1.0
- **Tests performed / commands:**
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run mypy src/`
  - `uv run pytest -q tests/unit/ tests/integration/`
  - `uv run stingray --config config/application.example.yaml --duration 6 --db /tmp/stg-e2e.db`
  - `uv run stingray --config /nonexistent.yaml` (early-exit path)
- **Expected vs actual outcomes:** all checks green; mock app runs all 4 sensors, writes to SQLite, shuts down cleanly; no-config path exits 0 immediately
- **Metrics captured:** 72 tests pass; mypy strict clean across 18 source files; 42 measurements written in a 6-second mock run
- **Known risks / defects:** none blocking; real hardware drivers deferred to Phase 05
- **Evidence paths / links:** this report; `src/stingray/core/`, `src/stingray/sensors/mock.py`, `tests/unit/`, `tests/integration/`
- **Gate decision:** **MOCK_READY = PASS**
- **Approved by / date:** pending user sign-off

## STG-010 — Define architecture and schemas

### Implemented

Layered architecture as specified in the plan:

```text
HAL (I2C/SPI/GPIO drivers)  ->  acquisition/scheduler  ->  normalization  ->  storage / live state / API / display / alerts
```

Phase 04 delivers the middle and right-hand layers with mock HAL:

- **`src/stingray/core/models.py`** — canonical data model:
  - `SensorId` enum (`sht45`, `scd4x`, `sgp41`, `sps30`)
  - `MetricId` enum with `METRIC_UNITS` mapping (e.g. `pm2_5_ug_m3` → `µg/m³`, `co2_ppm` → `ppm`)
  - `Quality` enum (`good`, `degraded`, `invalid`)
  - `SensorState` enum (`initializing`, `ready`, `degraded`, `failed`, `disconnected`)
  - `Measurement` — frozen pydantic model; validates tz-aware UTC `observed_at`/`received_at`, finite value, unit↔metric consistency
  - `SensorHealth` — frozen pydantic model
  - `utc_now()` helper
- **`src/stingray/core/errors.py`** — `SensorError` hierarchy: `SensorInitError`, `SensorReadError`, `SensorDisconnectedError`, `SensorStaleError`, `ConfigError`
- **`src/stingray/core/sensor_protocol.py`** — `SensorDriver` ABC (`initialize`/`read`/`health`/`close`), `CadenceSpec` (interval ≥ 0, max_retries, backoff), `retry_delay()`, `state_from_failures()`
- **`src/stingray/core/config.py`** — pydantic `AppConfig` + `load_config()` (YAML + `STINGRAY_*` env overrides), fail-fast validation
- **`src/stingray/core/scheduler.py`** — `SensorScheduler` (single-threaded, monotonic clock, per-sensor cadence, failure isolation, retry/backoff, `run_cycles()` for deterministic tests), `MeasurementQueue` (bounded, drops oldest), `SchedulerStats`, `build_specs_from_config()`
- **`src/stingray/sensors/mock.py`** — `MockSHT45`, `MockSCD4X`, `MockSGP41`, `MockSPS30`: deterministic seeded readings, `inject_failure(timeout|crc|stale|disconnected)`, `set_disconnected()`, health state transitions
- **`src/stingray/main.py`** — argparse entry point (`--config`, `--duration`, `--db`), scheduler + drain thread + retention loop + SIGTERM/SIGINT graceful shutdown; **early-exit** when no config file and no `--duration` (prevents infinite no-op loop)

### Acceptance criteria

- [x] Layered architecture implemented (HAL → scheduler → normalization → storage)
- [x] Typed `SensorDriver` protocol with `initialize`/`read`/`health`/`close`
- [x] Immutable frozen `Measurement`/`SensorHealth` pydantic models with tz-aware UTC timestamps
- [x] Single-threaded monotonic scheduler with bounded `MeasurementQueue`
- [x] Configuration via YAML + env overrides, fail-fast validation
- [x] Mock drivers for all 4 sensors with deterministic seeded output
- [x] Failure injection (timeout, CRC, stale, disconnected) exercises scheduler recovery
- [x] `main()` does not block forever with zero sensors (early-exit fix)

## STG-011 — Scheduler and failure isolation

### Implemented

- Per-sensor cadence via `CadenceSpec.interval_seconds`
- Failure isolation: one sensor's error never blocks others
- Retry with exponential backoff (`retry_delay()`)
- `max_retries` exhaustion → sensor marked failed, loop continues
- Recovery: after failures, sensor returns to ready on next successful read
- `run_cycles(n)` for deterministic test execution without real-time waiting

### Acceptance criteria

- [x] Per-sensor cadence honored (verified in `test_scheduler.py`)
- [x] Failure isolation: `test_failure_isolation` — one bad sensor does not block good sensors
- [x] Retry/backoff: `test_retry_exhaustion_backs_off`
- [x] Recovery: `test_recovery_after_failures`
- [x] Threaded start/stop: `test_start_stop`, `test_stop_is_prompt`

## STG-012 — Mock simulation

### Implemented

- 4 mock drivers, each producing realistic metric sets:
  - `MockSHT45`: temperature_c, relative_humidity_percent
  - `MockSCD4X`: co2_ppm, temperature_c, relative_humidity_percent
  - `MockSGP41`: voc_raw, nox_raw, voc_index, nox_index
  - `MockSPS30`: pm1/pm2.5/pm4/pm10 (µg/m³ and #/m³)
- Deterministic seeded random walk (reproducible test runs)
- Failure injection: `inject_failure(mode, count)` and `set_disconnected()`
- Health state transitions: `initializing` → `ready` → `degraded` → `failed` / `disconnected`

### Acceptance criteria

- [x] All 4 mock drivers produce correct metric sets with correct units
- [x] Deterministic output (seeded RNG)
- [x] Failure injection exercises all scheduler recovery paths
- [x] Health state transitions verified in `test_mock_sensors.py`

## Phase 04 exit gate

> `uv run pytest -m 'not hardware'` passes, mock app starts and recovers from injected sensor faults.

- `uv run pytest -q tests/unit/ tests/integration/` → **72 passed**
- Mock app: `uv run stingray --config config/application.example.yaml --duration 6 --db /tmp/stg-e2e.db` → 42 measurements written, clean shutdown
- Fault injection: `test_failure_isolation`, `test_retry_exhaustion_backs_off`, `test_recovery_after_failures` all pass

**Gate decision: MOCK_READY = PASS**
