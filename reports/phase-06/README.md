# Phase 06 Acceptance Report

- **Phase / Jira epic:** Phase 06 — Processing, local storage and history (`STG-P06`)
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
  - SQLite inspection of `/tmp/stg-e2e.db` (tables, counts, sample rows)
- **Expected vs actual outcomes:** all checks green; SQLite schema created via migrations; 42 measurements written and queryable; retention and restart persistence verified in tests
- **Metrics captured:** 72 tests pass (including `test_storage.py` and `test_pipeline.py`); mypy strict clean; 42 measurements in 6-second mock run
- **Known risks / defects:** backup/restore and disk-full I/O profiling not yet exercised end-to-end (covered by unit tests only)
- **Evidence paths / links:** this report; `src/stingray/storage/sqlite_store.py`, `tests/unit/test_storage.py`, `tests/integration/test_pipeline.py`
- **Gate decision:** **DATA_READY = PASS**
- **Approved by / date:** pending user sign-off

## STG-018 — Normalize and validate measurements

### Implemented

- **Canonical metric IDs and units** (`src/stingray/core/models.py`):
  - `MetricId` enum with `METRIC_UNITS` mapping
  - Every metric has a defined unit and source
- **`Measurement` model** (frozen pydantic):
  - `sensor: SensorId`, `metric: MetricId`, `value: float`, `unit: str`
  - `observed_at: datetime` (tz-aware UTC, validated)
  - `received_at: datetime` (tz-aware UTC, validated)
  - `quality: Quality` (`good`/`degraded`/`invalid`)
  - `diagnostics: dict` (JSON-serializable)
  - Rejects: non-finite values, unit↔metric mismatch, naive datetimes
- **`SensorHealth` model** (frozen pydantic):
  - `sensor`, `state`, `last_read_at`, `consecutive_failures`, `message`
- **Stale/missing/invalid distinction:**
  - `Quality.INVALID` for impossible numeric encodings
  - `SensorState.DISCONNECTED` / `SensorState.FAILED` for missing sensors
  - `SensorStaleError` for data-not-ready conditions
  - Stale measurements never masquerade as new valid readings

### Acceptance criteria

- [x] Every metric has defined unit/source and UTC timestamp
- [x] Stale/missing/invalid measurements never masquerade as new valid readings
- [x] Unit and boundary tests pass (`test_models.py`, `test_storage.py`)
- [ ] Sensor outputs map to ESP32 parity fields via compatibility layer (deferred to Phase 08)

## STG-019 — SQLite schema, writer and retention

### Implemented

**`src/stingray/storage/sqlite_store.py`:**

- **`Database`** class:
  - Versioned migrations (`schema_version` table, `_MIGRATIONS` list)
  - WAL mode (`PRAGMA journal_mode = WAL`)
  - `observed_epoch` REAL column for reliable retention (ISO-string comparison is unreliable)
  - Tables: `measurements`, `sensor_health_events`, `alerts`, `system_metrics`, `application_events`
  - Indexes on `(sensor, metric, observed_at)` and `observed_epoch`
- **`MeasurementWriter`** (batched):
  - Buffers up to `batch_size` rows, then inserts in a single transaction
  - `flush()` for shutdown or when buffer is full
  - `total_written` counter
- **`HistoryQuery`**:
  - `range(start, end, sensor, metric, limit, offset)` — time-range queries with pagination
  - `latest(sensor, metric)` — most recent measurement
  - `count(sensor, metric)` — row count
  - `purge_before(cutoff)` — delete measurements older than cutoff (uses `observed_epoch`)
- **`apply_retention(db, retention_days)`** — convenience wrapper for periodic purge
- **`record_health_event(db, health)`** — insert sensor health event row
- **`record_application_event(db, event_type, message)`** — insert application event row

### Acceptance criteria

- [x] Time-range queries return correct order/count and support pagination (`test_storage.py::TestHistoryQuery`)
- [x] 30-day default retention (configurable) deletes only expired records (`test_storage.py::TestRetention`)
- [x] Migration from previous schema tested (`test_storage.py::TestMigrations`)
- [x] Disk full/write failure surfaced without corrupting live state (`test_storage.py::TestFaultInjection`)
- [x] 1-hour simulated ingestion persists across restart (`test_pipeline.py::TestEndToEnd::test_restart_persistence`)
- [ ] Backup restored into an isolated environment and integrity check passes (unit test only)
- [ ] Database I/O/resource profile measured on Pi (deferred)

## Phase 06 exit gate

> Application can store and retrieve all readings reliably, including after restart and fault injection.

- `uv run pytest -q tests/unit/ tests/integration/` → **72 passed** (includes `test_storage.py`, `test_pipeline.py`)
- Mock app: 42 measurements written to `/tmp/stg-e2e.db`, all queryable via `HistoryQuery`
- Restart persistence: `test_restart_persistence` — 12 measurements across 2 simulated runs
- Fault injection: `test_disk_full_surfaces_error` — `sqlite3.OperationalError` raised, no corruption

**Gate decision: DATA_READY = PASS**
