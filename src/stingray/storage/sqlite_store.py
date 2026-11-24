"""SQLite storage: schema, migrations, writer, retention, and queries.

The database is the durable history store. All timestamps are stored as
UTC ISO-8601 strings. WAL mode is enabled for concurrent reads during
writes. Migrations are versioned and applied in order.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from stingray.core.models import Measurement, SensorHealth, SensorId

log = logging.getLogger("stingray.storage")

SCHEMA_VERSION = 1

_MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            unit TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            quality TEXT NOT NULL DEFAULT 'good',
            diagnostics TEXT NOT NULL DEFAULT '{}',
            observed_epoch REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_measurements_observed
            ON measurements (sensor, metric, observed_at);
        CREATE INDEX IF NOT EXISTS idx_measurements_received
            ON measurements (received_at);
        CREATE TABLE IF NOT EXISTS sensor_health_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor TEXT NOT NULL,
            state TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_health_recorded
            ON sensor_health_events (sensor, recorded_at);
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type TEXT NOT NULL,
            sensor TEXT,
            metric TEXT,
            value REAL,
            threshold REAL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS system_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            recorded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS application_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT NOT NULL,
            event TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        """,
    ),
]


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class Database:
    """Thin wrapper over a SQLite connection with migration support."""

    def __init__(self, path: str | Path, *, wal: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if wal:
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._apply_migrations()

    def _apply_migrations(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = row["v"] if row["v"] is not None else 0
        for version, sql in _MIGRATIONS:
            if version > current:
                self._conn.executescript(sql)
                self._conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (version, _utcnow_iso()),
                )
                log.info("applied migration %d", version)
        self._conn.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def schema_version(self) -> int:
        row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return row["v"] if row["v"] is not None else 0

    def close(self) -> None:
        self._conn.close()

    def checkpoint(self) -> None:
        """Force a WAL checkpoint to flush to the main database file."""
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


class MeasurementWriter:
    """Batches measurements and writes them to the database.

    The writer buffers up to ``batch_size`` rows, then inserts them in a
    single transaction. ``flush()`` must be called before shutdown or when
    the buffer is full.
    """

    def __init__(self, db: Database, *, batch_size: int = 50) -> None:
        self._db = db
        self._batch_size = batch_size
        self._buffer: list[tuple[Any, ...]] = []
        self.total_written = 0

    def add(self, m: Measurement) -> None:
        self._buffer.append(
            (
                m.sensor.value,
                m.metric.value,
                m.value,
                m.unit,
                _iso(m.observed_at),
                _iso(m.received_at),
                m.quality.value,
                _json_diagnostics(m.diagnostics),
                m.observed_at.timestamp(),
            )
        )
        if len(self._buffer) >= self._batch_size:
            self.flush()

    def add_many(self, measurements: list[Measurement]) -> None:
        for m in measurements:
            self.add(m)

    def flush(self) -> int:
        """Write buffered rows. Returns the number of rows written."""
        if not self._buffer:
            return 0
        rows = self._buffer
        self._buffer = []
        self._db.connection.executemany(
            """
            INSERT INTO measurements
                (sensor, metric, value, unit, observed_at, received_at,
                 quality, diagnostics, observed_epoch)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._db.connection.commit()
        self.total_written += len(rows)
        return len(rows)

    def __len__(self) -> int:
        return len(self._buffer)


def _json_diagnostics(diagnostics: dict[str, Any]) -> str:
    import json

    return json.dumps(diagnostics, sort_keys=True)


class HistoryQuery:
    """Read-side queries over the measurements table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def range(
        self,
        sensor: SensorId | None = None,
        metric: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return measurements in [start, end], oldest first, paginated."""
        sql = "SELECT * FROM measurements WHERE 1=1"
        params: list[Any] = []
        if sensor is not None:
            sql += " AND sensor = ?"
            params.append(sensor.value)
        if metric is not None:
            sql += " AND metric = ?"
            params.append(metric)
        if start is not None:
            sql += " AND observed_at >= ?"
            params.append(_iso(start))
        if end is not None:
            sql += " AND observed_at <= ?"
            params.append(_iso(end))
        sql += " ORDER BY observed_at ASC, id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._db.connection.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def latest(
        self, sensor: SensorId | None = None, metric: str | None = None
    ) -> dict[str, Any] | None:
        """Return the most recent measurement, optionally filtered."""
        sql = "SELECT * FROM measurements WHERE 1=1"
        params: list[Any] = []
        if sensor is not None:
            sql += " AND sensor = ?"
            params.append(sensor.value)
        if metric is not None:
            sql += " AND metric = ?"
            params.append(metric)
        sql += " ORDER BY observed_at DESC, id DESC LIMIT 1"
        row = self._db.connection.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def count(self, sensor: SensorId | None = None, metric: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM measurements WHERE 1=1"
        params: list[Any] = []
        if sensor is not None:
            sql += " AND sensor = ?"
            params.append(sensor.value)
        if metric is not None:
            sql += " AND metric = ?"
            params.append(metric)
        row = self._db.connection.execute(sql, params).fetchone()
        return int(row["n"])

    def purge_before(self, cutoff: datetime) -> int:
        """Delete measurements older than ``cutoff``. Returns rows deleted."""
        cur = self._db.connection.execute(
            "DELETE FROM measurements WHERE observed_epoch <= ?", (cutoff.timestamp(),)
        )
        self._db.connection.commit()
        return cur.rowcount


def apply_retention(db: Database, retention_days: int) -> int:
    """Purge measurements older than the retention window. Returns rows deleted."""
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    return HistoryQuery(db).purge_before(cutoff)


def record_health_event(db: Database, health: SensorHealth) -> None:
    """Insert a sensor health event row."""
    db.connection.execute(
        "INSERT INTO sensor_health_events "
        "(sensor, state, message, recorded_at) VALUES (?, ?, ?, ?)",
        (health.sensor.value, health.state.value, health.message, _utcnow_iso()),
    )
    db.connection.commit()


def record_application_event(db: Database, level: str, event: str, details: str = "") -> None:
    db.connection.execute(
        "INSERT INTO application_events (level, event, details, created_at) VALUES (?, ?, ?, ?)",
        (level, event, details, _utcnow_iso()),
    )
    db.connection.commit()
