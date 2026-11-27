"""Unit tests for Parquet storage: writer, queries, retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stingray.core.models import Measurement, MetricId, SensorId, utc_now
from stingray.storage.parquet_store import (
    ParquetQuery,
    ParquetWriter,
    apply_retention,
    connect_read_limited,
)


def _measurement(
    sensor: SensorId = SensorId.SHT45,
    metric: MetricId = MetricId.TEMPERATURE_C,
    value: float = 25.0,
    observed: datetime | None = None,
) -> Measurement:
    obs = observed if observed is not None else utc_now()
    return Measurement(
        sensor=sensor,
        metric=metric,
        value=value,
        unit="degC" if metric is MetricId.TEMPERATURE_C else "ppm",
        observed_at=obs,
        received_at=utc_now(),
    )


@pytest.fixture
def storage_dir(tmp_path: Path) -> Path:
    d = tmp_path / "parquet"
    d.mkdir()
    return d


class TestWriter:
    def test_write_and_read_back(self, storage_dir: Path) -> None:
        writer = ParquetWriter(storage_dir, batch_size=100)
        m = _measurement(value=21.5)
        writer.add(m)
        written = writer.flush()
        assert written == 1
        pq = ParquetQuery(storage_dir)
        rows = pq.latest()
        assert len(rows) == 1
        assert rows[0]["value"] == 21.5
        assert rows[0]["sensor"] == "sht45"

    def test_batch_triggers_flush(self, storage_dir: Path) -> None:
        writer = ParquetWriter(storage_dir, batch_size=3)
        for i in range(3):
            writer.add(_measurement(value=float(i)))
        # Buffer should be empty after auto-flush.
        assert len(writer) == 0
        assert writer.total_written == 3

    def test_persistence_across_restart(self, storage_dir: Path) -> None:
        w1 = ParquetWriter(storage_dir, batch_size=10)
        w1.add(_measurement(value=99.9))
        w1.flush()
        # Simulate restart: new writer + query on the same directory
        pq = ParquetQuery(storage_dir)
        rows = pq.latest()
        assert len(rows) == 1
        assert rows[0]["value"] == 99.9

    def test_files_created_on_disk(self, storage_dir: Path) -> None:
        writer = ParquetWriter(storage_dir, batch_size=10)
        writer.add(_measurement(value=1.0))
        writer.flush()
        # Should have at least one parquet file in the tree
        files = list(storage_dir.rglob("*.parquet"))
        assert len(files) >= 1
        # No .tmp files should remain
        tmps = list(storage_dir.rglob("*.tmp"))
        assert len(tmps) == 0


class TestReadThreadBudget:
    def test_read_connections_do_not_use_duckdb_default_thread_count(self) -> None:
        con = connect_read_limited()
        try:
            threads = con.execute("SELECT current_setting('threads')").fetchone()
            assert threads is not None
            assert 1 <= int(threads[0]) <= 2
        finally:
            con.close()


class TestQueries:
    def test_range_filters(self, storage_dir: Path) -> None:
        writer = ParquetWriter(storage_dir)
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(5):
            writer.add(
                _measurement(
                    observed=t0 + timedelta(minutes=i),
                    value=float(i),
                )
            )
        writer.flush()
        pq = ParquetQuery(storage_dir)
        start = t0 + timedelta(minutes=1)
        end = t0 + timedelta(minutes=3)
        rows = pq.range(start=start, end=end)
        assert len(rows) == 3
        assert [r["value"] for r in rows] == [1.0, 2.0, 3.0]

    def test_range_pagination(self, storage_dir: Path) -> None:
        writer = ParquetWriter(storage_dir)
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(10):
            writer.add(_measurement(observed=t0 + timedelta(minutes=i), value=float(i)))
        writer.flush()
        pq = ParquetQuery(storage_dir)
        page1 = pq.range(limit=4)
        page2 = pq.range(limit=4, offset=4)
        assert len(page1) == 4
        assert len(page2) == 4

    def test_latest_with_filter(self, storage_dir: Path) -> None:
        writer = ParquetWriter(storage_dir)
        now = utc_now()
        writer.add(_measurement(observed=now - timedelta(minutes=5), value=1.0))
        writer.add(
            _measurement(
                sensor=SensorId.SCD4X,
                metric=MetricId.CO2_PPM,
                value=400.0,
                observed=now - timedelta(minutes=4),
            )
        )
        writer.flush()
        pq = ParquetQuery(storage_dir)
        rows = pq.latest(sensor=SensorId.SCD4X, metric="co2_ppm")
        assert len(rows) == 1
        assert rows[0]["value"] == 400.0

    def test_count(self, storage_dir: Path) -> None:
        writer = ParquetWriter(storage_dir)
        for i in range(4):
            writer.add(_measurement(value=float(i)))
        writer.flush()
        assert ParquetQuery(storage_dir).count() == 4

    def test_count_with_filter(self, storage_dir: Path) -> None:
        writer = ParquetWriter(storage_dir)
        writer.add(_measurement(sensor=SensorId.SHT45, value=1.0))
        writer.add(_measurement(sensor=SensorId.SCD4X, metric=MetricId.CO2_PPM, value=400.0))
        writer.flush()
        pq = ParquetQuery(storage_dir)
        assert pq.count(sensor=SensorId.SHT45) == 1
        assert pq.count(sensor=SensorId.SCD4X) == 1

    def test_range_with_stats(self, storage_dir: Path) -> None:
        writer = ParquetWriter(storage_dir)
        now = utc_now()
        for i in range(10):
            writer.add(
                _measurement(
                    observed=now - timedelta(minutes=60 - i),
                    value=float(i),
                )
            )
        writer.flush()
        pq = ParquetQuery(storage_dir)
        samples, stats = pq.range_with_stats(hours=2, limit=100)
        assert len(samples) > 0
        assert stats is not None
        assert stats["min"] >= 0
        assert stats["max"] <= 9
        assert stats["count"] > 0


class TestRetention:
    def test_purge_old_files(self, storage_dir: Path) -> None:
        writer = ParquetWriter(storage_dir)
        old = utc_now() - timedelta(days=60)
        new = utc_now() - timedelta(days=1)
        writer.add(_measurement(observed=old, value=1.0))
        writer.add(_measurement(observed=new, value=2.0))
        writer.flush()
        deleted = apply_retention(storage_dir, retention_days=30)
        assert deleted >= 1
        # Recent data should still be queryable
        pq = ParquetQuery(storage_dir)
        assert pq.count() >= 1

    def test_purge_only_expired(self, storage_dir: Path) -> None:
        writer = ParquetWriter(storage_dir)
        recent = utc_now() - timedelta(days=1)
        writer.add(_measurement(observed=recent, value=1.0))
        writer.flush()
        deleted = apply_retention(storage_dir, retention_days=30)
        assert deleted == 0
        assert ParquetQuery(storage_dir).count() == 1


class TestFaultInjection:
    def test_write_to_non_writable_path(self, tmp_path: Path) -> None:
        # Simulate a read-only filesystem by making the base dir a file
        bad_path = tmp_path / "is_a_file"
        bad_path.write_text("not a dir")
        writer = ParquetWriter(bad_path, batch_size=10)
        writer.add(_measurement(value=1.0))
        with pytest.raises((OSError, Exception)):
            writer.flush()
