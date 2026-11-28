"""DuckDB + Parquet storage: batched writer and query engine.

Replaces the SQLite storage layer. Measurements are buffered in memory and
flushed to Hive-partitioned daily Parquet files (ZSTD compressed) on the SSD.
Queries use DuckDB's native Parquet reader with column projection and
``time_bucket()`` downsampling for time-range queries.

Layout on disk::

    base_dir/
      year=2026/
        month=11/
          day=25/
            chunk_9f0b5d2e4a6c47f1a83e6b90d2c4f7ab.parquet
            chunk_c1d8e3a07b5f4e6d9a2c8f4b1e7d5036.parquet
            ...

Atomic, durable writes: each flush writes to a ``.tmp`` filename, fsyncs the
file, renames to the final name, then fsyncs the directory. DuckDB only ever
sees complete files, and an unclean shutdown cannot leave a 0-byte chunk.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from stingray.core.models import Measurement, SensorId

log = logging.getLogger("stingray.storage")


def _next_chunk_id() -> str:
    """Return a collision-free chunk id.

    A process-local counter is reused after a restart and overwrites the
    chunk files written by the previous process. UUIDs remain unique across
    processes and restarts, so never-restarted history is preserved.
    """
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_PARQUET_SCHEMA = pa.schema(
    [
        pa.field("sensor", pa.string()),
        pa.field("metric", pa.string()),
        pa.field("value", pa.float64()),
        pa.field("unit", pa.string()),
        pa.field("observed_at", pa.string()),
        pa.field("received_at", pa.string()),
        pa.field("quality", pa.string()),
        pa.field("diagnostics", pa.string()),
        pa.field("observed_epoch", pa.float64()),
    ]
)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _json_diagnostics(diagnostics: dict[str, Any]) -> str:
    return json.dumps(diagnostics, sort_keys=True)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class ParquetWriter:
    """Batches measurements and flushes them to Hive-partitioned Parquet files.

    The writer buffers up to ``batch_size`` rows, then writes them to a
    new Parquet file under the appropriate ``year=YYYY/month=MM/day=DD/``
    directory. ``flush()`` must be called before shutdown or when the buffer
    is full.

    Thread-safe: a lock guards the buffer and the flush operation.
    """

    def __init__(self, base_dir: str | Path, *, batch_size: int = 50) -> None:
        self._base_dir = Path(base_dir)
        self._batch_size = batch_size
        self._buffer: list[Measurement] = []
        self._lock = threading.Lock()
        self.total_written = 0

    def add(self, m: Measurement) -> None:
        with self._lock:
            self._buffer.append(m)
            if len(self._buffer) >= self._batch_size:
                self._flush_locked()

    def add_many(self, measurements: list[Measurement]) -> None:
        with self._lock:
            self._buffer.extend(measurements)
            if len(self._buffer) >= self._batch_size:
                self._flush_locked()

    def flush(self) -> int:
        """Write buffered rows to Parquet. Returns the number of rows written."""
        with self._lock:
            return self._flush_locked()

    def _flush_locked(self) -> int:
        if not self._buffer:
            return 0
        rows = self._buffer
        self._buffer = []

        # Group by date (from observed_at)
        by_date: dict[str, list[Measurement]] = {}
        for m in rows:
            d = m.observed_at.astimezone(UTC)
            key = f"year={d.year:04d}/month={d.month:02d}/day={d.day:02d}"
            by_date.setdefault(key, []).append(m)

        for date_key, batch in by_date.items():
            self._write_batch(date_key, batch)

        self.total_written += len(rows)
        log.debug("flushed %d measurements to parquet", len(rows))
        return len(rows)

    def _write_batch(self, date_key: str, batch: list[Measurement]) -> None:
        day_dir = self._base_dir / date_key
        day_dir.mkdir(parents=True, exist_ok=True)

        chunk_id = _next_chunk_id()
        final_name = f"chunk_{chunk_id}.parquet"
        final_path = day_dir / final_name
        tmp_path = day_dir / f"{final_name}.tmp"

        # Build the Arrow table
        table = pa.table(
            {
                "sensor": [m.sensor.value for m in batch],
                "metric": [m.metric.value for m in batch],
                "value": [m.value for m in batch],
                "unit": [m.unit for m in batch],
                "observed_at": [_iso(m.observed_at) for m in batch],
                "received_at": [_iso(m.received_at) for m in batch],
                "quality": [m.quality.value for m in batch],
                "diagnostics": [_json_diagnostics(m.diagnostics) for m in batch],
                "observed_epoch": [m.observed_at.timestamp() for m in batch],
            },
            schema=_PARQUET_SCHEMA,
        )

        # Write to temp file, flush it to stable storage, then atomically
        # rename. A plain rename is not durable: an unclean shutdown can keep
        # the new name while losing the file's bytes, leaving a 0-byte chunk.
        try:
            with tmp_path.open("wb") as fh:
                pq.write_table(table, fh, compression="zstd")
                fh.flush()
                os.fsync(fh.fileno())
            if tmp_path.stat().st_size == 0:
                raise OSError(f"refusing to publish empty parquet chunk: {tmp_path}")
            tmp_path.rename(final_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

        # Persist the directory entry so a crash cannot resurrect a chunk
        # whose data was never written.
        dir_fd = os.open(day_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


# DuckDB defaults to 32 threads regardless of core count. On the 4-core Pi,
# dashboard polling then starves the local Ollama inference that shares the CPU.
# Read queries scan small time windows, so two threads are plenty.
_READ_QUERY_THREADS = 2

# Typed empty relation used when no usable Parquet file exists. It keeps every
# query's column references valid instead of raising "No files found".
_EMPTY_TABLE_REF = """(
    SELECT
        CAST(NULL AS VARCHAR) AS sensor,
        CAST(NULL AS VARCHAR) AS metric,
        CAST(NULL AS DOUBLE) AS value,
        CAST(NULL AS VARCHAR) AS unit,
        CAST(NULL AS VARCHAR) AS observed_at,
        CAST(NULL AS VARCHAR) AS received_at,
        CAST(NULL AS VARCHAR) AS quality,
        CAST(NULL AS VARCHAR) AS diagnostics,
        CAST(NULL AS DOUBLE) AS observed_epoch
    WHERE FALSE
)"""


def _sql_string_list(values: list[str]) -> str:
    """Render a DuckDB list of SQL string literals."""
    return "[" + ", ".join("'" + value.replace("'", "''") + "'" for value in values) + "]"


def connect_read_limited() -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection with a small, Ollama-friendly thread budget."""
    con = duckdb.connect()
    con.execute(f"SET threads={_READ_QUERY_THREADS}")
    return con


class ParquetQuery:
    """Read-side queries over Hive-partitioned Parquet files using DuckDB.

    Each query opens a fresh DuckDB connection (read-only, no shared state),
    runs the query, and closes the connection. This is safe for concurrent
    use from the API server's thread pool.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return connect_read_limited()

    def _usable_files(self) -> list[str]:
        """Return Parquet files safe to hand to DuckDB.

        An unclean shutdown can leave a renamed but empty/truncated chunk.
        A single such file aborts every query, so skip files without valid
        Parquet magic at both ends.
        """
        files: list[str] = []
        if not self._base_dir.exists():
            return files
        for path in self._base_dir.glob("**/*.parquet"):
            try:
                if path.stat().st_size < 8:
                    continue
                with path.open("rb") as fh:
                    if fh.read(4) != b"PAR1":
                        continue
                    fh.seek(-4, os.SEEK_END)
                    if fh.read(4) != b"PAR1":
                        continue
            except OSError:
                continue
            files.append(str(path))
        return files

    def _table_ref(self) -> str:
        """Return the DuckDB table reference for all usable Parquet files."""
        files = self._usable_files()
        if not files:
            return _EMPTY_TABLE_REF
        return f"read_parquet({_sql_string_list(files)}, hive_partitioning=true)"

    def latest(
        self, sensor: SensorId | None = None, metric: str | None = None
    ) -> list[dict[str, Any]]:
        """Return the most recent measurement per (sensor, metric).

        Only scans the last 2 hours of data to keep this fast even with
        large datasets. Returns an empty list if no data exists.
        """
        con = self._connect()
        try:
            cutoff = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
            sql = f"""
                SELECT sensor, metric, value, unit, observed_at, quality
                FROM (
                    SELECT *,
                           row_number() OVER (
                               PARTITION BY sensor, metric
                               ORDER BY observed_epoch DESC
                           ) AS rn
                    FROM {self._table_ref()}
                    WHERE observed_epoch >= ?
            """
            params: list[Any] = [cutoff]
            if sensor is not None:
                sql += " AND sensor = ?"
                params.append(sensor.value)
            if metric is not None:
                sql += " AND metric = ?"
                params.append(metric)
            sql += """
                ) ranked
                WHERE rn = 1
                ORDER BY sensor, metric
            """
            try:
                rows = con.execute(sql, params).fetchall()
            except Exception:
                # No files found or other IO error - return empty list
                return []
            cols = ["sensor", "metric", "value", "unit", "observed_at", "quality"]
            return [dict(zip(cols, r, strict=False)) for r in rows]
        finally:
            con.close()

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
        con = self._connect()
        try:
            sql = f"""
                SELECT sensor, metric, value, unit, observed_at,
                       observed_epoch, quality
                FROM {self._table_ref()}
                WHERE 1=1
            """
            params: list[Any] = []
            if sensor is not None:
                sql += " AND sensor = ?"
                params.append(sensor.value)
            if metric is not None:
                sql += " AND metric = ?"
                params.append(metric)
            if start is not None:
                sql += " AND observed_epoch >= ?"
                params.append(start.timestamp())
            if end is not None:
                sql += " AND observed_epoch <= ?"
                params.append(end.timestamp())
            sql += " ORDER BY observed_epoch ASC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = con.execute(sql, params).fetchall()
            cols = [
                "sensor",
                "metric",
                "value",
                "unit",
                "observed_at",
                "observed_epoch",
                "quality",
            ]
            return [dict(zip(cols, r, strict=False)) for r in rows]
        finally:
            con.close()

    def range_with_stats(
        self,
        metric: str | None = None,
        sensor: SensorId | None = None,
        hours: int = 24,
        limit: int = 900,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[list[float]], dict[str, Any] | None]:
        """Time-range query with downsampling and stats.

        Uses DuckDB's ``time_bucket()`` to downsample to at most ``limit``
        points. Returns (samples, stats) where samples is a list of
        [epoch, value] pairs and stats is {min, avg, max, count}. Callers
        may override the window with explicit ``start``/``end`` bounds.
        """
        con = self._connect()
        try:
            if start is None or end is None:
                now = datetime.now(UTC)
            if start is None:
                start = now - timedelta(hours=hours)
            if end is None:
                end = now

            # Choose bucket interval to target ~limit points
            total_seconds = hours * 3600
            bucket_seconds = max(1, total_seconds // limit)
            # Round to a nice interval
            if bucket_seconds <= 60:
                bucket = f"{bucket_seconds} seconds"
            elif bucket_seconds <= 3600:
                bucket = f"{bucket_seconds // 60} minutes"
            else:
                bucket = f"{bucket_seconds // 3600} hours"

            sql = f"""
                SELECT
                    time_bucket(INTERVAL '{bucket}', to_timestamp(observed_epoch)) AS bucket,
                    AVG(value) AS avg_val,
                    MIN(value) AS min_val,
                    MAX(value) AS max_val,
                    COUNT(*) AS cnt
                FROM {self._table_ref()}
                WHERE 1=1
            """
            params: list[Any] = []
            if sensor is not None:
                sql += " AND sensor = ?"
                params.append(sensor.value)
            if metric is not None:
                sql += " AND metric = ?"
                params.append(metric)
            sql += " AND observed_epoch >= ?"
            params.append(start.timestamp())
            sql += " AND observed_epoch <= ?"
            params.append(end.timestamp())
            sql += """
                GROUP BY bucket
                ORDER BY bucket ASC
            """
            rows = con.execute(sql, params).fetchall()
            if not rows:
                return [], None

            samples: list[list[float]] = []
            all_values: list[float] = []
            for r in rows:
                bucket_ts = r[0]
                if hasattr(bucket_ts, "timestamp"):
                    epoch = bucket_ts.timestamp()
                else:
                    epoch = float(bucket_ts)
                samples.append([epoch, r[1]])
                all_values.append(r[1])

            stats = {
                "min": min(all_values),
                "avg": sum(all_values) / len(all_values),
                "max": max(all_values),
                "count": len(all_values),
            }
            return samples, stats
        finally:
            con.close()

    def count(self, sensor: SensorId | None = None, metric: str | None = None) -> int:
        con = self._connect()
        try:
            sql = f"SELECT COUNT(*) FROM {self._table_ref()} WHERE 1=1"
            params: list[Any] = []
            if sensor is not None:
                sql += " AND sensor = ?"
                params.append(sensor.value)
            if metric is not None:
                sql += " AND metric = ?"
                params.append(metric)
            row = con.execute(sql, params).fetchone()
            return int(row[0]) if row else 0
        finally:
            con.close()

    def count_recent(
        self, hours: int = 24, sensor: SensorId | None = None, metric: str | None = None
    ) -> int:
        """Count measurements in the last N hours (fast, avoids full scan)."""
        con = self._connect()
        try:
            cutoff = (datetime.now(UTC) - timedelta(hours=hours)).timestamp()
            sql = f"SELECT COUNT(*) FROM {self._table_ref()} WHERE observed_epoch >= ?"
            params: list[Any] = [cutoff]
            if sensor is not None:
                sql += " AND sensor = ?"
                params.append(sensor.value)
            if metric is not None:
                sql += " AND metric = ?"
                params.append(metric)
            row = con.execute(sql, params).fetchone()
            return int(row[0]) if row else 0
        finally:
            con.close()

    def purge_before(self, cutoff: datetime) -> int:
        """Delete Parquet files older than the cutoff date. Returns files deleted."""
        cutoff_date = cutoff.astimezone(UTC)
        deleted = 0
        if not self._base_dir.exists():
            return 0
        for year_dir in self._base_dir.iterdir():
            if not year_dir.is_dir() or not year_dir.name.startswith("year="):
                continue
            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir() or not month_dir.name.startswith("month="):
                    continue
                for day_dir in month_dir.iterdir():
                    if not day_dir.is_dir() or not day_dir.name.startswith("day="):
                        continue
                    # Parse the date from the directory name
                    try:
                        year = int(year_dir.name.split("=")[1])
                        month = int(month_dir.name.split("=")[1])
                        day = int(day_dir.name.split("=")[1])
                        day_start = datetime(year, month, day, tzinfo=UTC)
                    except (ValueError, IndexError):
                        continue
                    if day_start <= cutoff_date:
                        import shutil

                        shutil.rmtree(day_dir, ignore_errors=True)
                        deleted += 1
                        log.info("retention: deleted %s", day_dir)
        return deleted


def apply_retention(base_dir: str | Path, retention_days: int) -> int:
    """Purge Parquet files older than the retention window. Returns files deleted."""
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    return ParquetQuery(base_dir).purge_before(cutoff)
