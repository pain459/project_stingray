"""Unit tests for SQLite storage: schema, writer, queries, retention."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stingray.core.models import Measurement, MetricId, SensorHealth, SensorId, SensorState, utc_now
from stingray.storage.sqlite_store import (
    Database,
    HistoryQuery,
    MeasurementWriter,
    apply_retention,
    record_application_event,
    record_health_event,
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
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db", wal=True)
    yield d
    d.close()


class TestSchema:
    def test_version_is_one(self, db: Database) -> None:
        assert db.schema_version() == 1

    def test_tables_exist(self, db: Database) -> None:
        rows = db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {r["name"] for r in rows}
        for expected in (
            "measurements",
            "sensor_health_events",
            "alerts",
            "system_metrics",
            "application_events",
            "schema_version",
        ):
            assert expected in names

    def test_migrations_idempotent(self, tmp_path: Path) -> None:
        path = tmp_path / "idem.db"
        d1 = Database(path)
        d1.close()
        d2 = Database(path)
        assert d2.schema_version() == 1
        d2.close()


class TestWriter:
    def test_write_and_read_back(self, db: Database) -> None:
        writer = MeasurementWriter(db, batch_size=100)
        m = _measurement(value=21.5)
        writer.add(m)
        written = writer.flush()
        assert written == 1
        row = HistoryQuery(db).latest()
        assert row is not None
        assert row["value"] == 21.5
        assert row["sensor"] == "sht45"

    def test_batch_triggers_flush(self, db: Database) -> None:
        writer = MeasurementWriter(db, batch_size=3)
        for i in range(3):
            writer.add(_measurement(value=float(i)))
        # Buffer should be empty after auto-flush.
        assert len(writer) == 0
        assert writer.total_written == 3

    def test_persistence_across_restart(self, tmp_path: Path) -> None:
        path = tmp_path / "persist.db"
        d1 = Database(path)
        w = MeasurementWriter(d1, batch_size=10)
        w.add(_measurement(value=99.9))
        w.flush()
        d1.close()
        d2 = Database(path)
        row = HistoryQuery(d2).latest()
        assert row is not None
        assert row["value"] == 99.9
        d2.close()


class TestQueries:
    def test_range_filters(self, db: Database) -> None:
        writer = MeasurementWriter(db)
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(5):
            writer.add(
                _measurement(
                    observed=t0 + timedelta(minutes=i),
                    value=float(i),
                )
            )
        writer.flush()
        hq = HistoryQuery(db)
        start = t0 + timedelta(minutes=1)
        end = t0 + timedelta(minutes=3)
        rows = hq.range(start=start, end=end)
        assert len(rows) == 3
        assert [r["value"] for r in rows] == [1.0, 2.0, 3.0]

    def test_range_pagination(self, db: Database) -> None:
        writer = MeasurementWriter(db)
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(10):
            writer.add(_measurement(observed=t0 + timedelta(minutes=i), value=float(i)))
        writer.flush()
        hq = HistoryQuery(db)
        page1 = hq.range(limit=4)
        page2 = hq.range(limit=4, offset=4)
        assert len(page1) == 4
        assert len(page2) == 4
        assert page1[0]["id"] != page2[0]["id"]

    def test_latest_with_filter(self, db: Database) -> None:
        writer = MeasurementWriter(db)
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        writer.add(_measurement(observed=t0, value=1.0))
        writer.add(
            _measurement(
                sensor=SensorId.SCD4X,
                metric=MetricId.CO2_PPM,
                value=400.0,
                observed=t0 + timedelta(minutes=1),
            )
        )
        writer.flush()
        hq = HistoryQuery(db)
        row = hq.latest(sensor=SensorId.SCD4X, metric="co2_ppm")
        assert row is not None
        assert row["value"] == 400.0

    def test_count(self, db: Database) -> None:
        writer = MeasurementWriter(db)
        for i in range(4):
            writer.add(_measurement(value=float(i)))
        writer.flush()
        assert HistoryQuery(db).count() == 4


class TestRetention:
    def test_purge_old_rows(self, db: Database) -> None:
        from datetime import timedelta

        writer = MeasurementWriter(db)
        old = utc_now() - timedelta(days=60)
        new = utc_now() - timedelta(days=1)
        writer.add(_measurement(observed=old, value=1.0))
        writer.add(_measurement(observed=new, value=2.0))
        writer.flush()
        deleted = apply_retention(db, retention_days=30)
        assert deleted == 1
        assert HistoryQuery(db).count() == 1

    def test_purge_only_expired(self, db: Database) -> None:
        writer = MeasurementWriter(db)
        recent = utc_now() - timedelta(days=1)
        writer.add(_measurement(observed=recent, value=1.0))
        writer.flush()
        deleted = apply_retention(db, retention_days=30)
        assert deleted == 0
        assert HistoryQuery(db).count() == 1


class TestEvents:
    def test_health_event(self, db: Database) -> None:
        h = SensorHealth(
            sensor=SensorId.SPS30,
            state=SensorState.DEGRADED,
            message="test",
        )
        record_health_event(db, h)
        row = db.connection.execute(
            "SELECT * FROM sensor_health_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["sensor"] == "sps30"
        assert row["state"] == "degraded"

    def test_application_event(self, db: Database) -> None:
        record_application_event(db, "INFO", "startup", "boot ok")
        row = db.connection.execute(
            "SELECT * FROM application_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["event"] == "startup"


class TestFaultInjection:
    def test_disk_full_surfaces_error(self, tmp_path: Path) -> None:
        # Simulate a read-only filesystem by making the db path a directory.
        bad_path = tmp_path / "is_a_dir"
        bad_path.mkdir()
        with pytest.raises(sqlite3.OperationalError):
            Database(bad_path)
