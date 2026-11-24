"""Integration tests: end-to-end mock application run with storage."""

from __future__ import annotations

from pathlib import Path

from stingray.core.models import SensorId
from stingray.core.scheduler import MeasurementQueue, SensorScheduler
from stingray.core.sensor_protocol import CadenceSpec
from stingray.sensors.mock import MockSCD4X, MockSGP41, MockSHT45, MockSPS30
from stingray.storage.sqlite_store import Database, HistoryQuery, MeasurementWriter


def _fast_specs() -> dict[SensorId, CadenceSpec]:
    return {
        SensorId.SHT45: CadenceSpec(sensor=SensorId.SHT45, interval_seconds=0.05),
        SensorId.SCD4X: CadenceSpec(sensor=SensorId.SCD4X, interval_seconds=0.05),
        SensorId.SGP41: CadenceSpec(sensor=SensorId.SGP41, interval_seconds=0.05),
        SensorId.SPS30: CadenceSpec(sensor=SensorId.SPS30, interval_seconds=0.05),
    }


class TestEndToEnd:
    def test_scheduler_to_sqlite(self, tmp_path: Path) -> None:
        drivers = [MockSHT45(seed=1), MockSCD4X(seed=2), MockSGP41(seed=3), MockSPS30(seed=4)]
        queue = MeasurementQueue()
        sched = SensorScheduler(drivers, _fast_specs(), queue)
        db = Database(tmp_path / "e2e.db")
        writer = MeasurementWriter(db, batch_size=100)

        sched.run_cycles(5)
        items = queue.drain()
        writer.add_many(items)
        writer.flush()

        hq = HistoryQuery(db)
        total = hq.count()
        assert total == len(items)
        assert total > 0

        # Each sensor contributed.
        for sid in (SensorId.SHT45, SensorId.SCD4X, SensorId.SGP41, SensorId.SPS30):
            assert hq.count(sensor=sid) > 0

        db.close()

    def test_restart_persistence(self, tmp_path: Path) -> None:
        path = tmp_path / "restart.db"
        specs = {SensorId.SHT45: CadenceSpec(sensor=SensorId.SHT45, interval_seconds=0.0)}

        # First "run": 3 cycles -> 6 measurements (temp + rh per cycle).
        sched = SensorScheduler([MockSHT45(seed=9)], specs, MeasurementQueue())
        q1 = MeasurementQueue()
        sched = SensorScheduler([MockSHT45(seed=9)], specs, q1)
        sched.run_cycles(3)
        db1 = Database(path)
        w1 = MeasurementWriter(db1, batch_size=100)
        w1.add_many(q1.drain())
        w1.flush()
        db1.close()
        sched.stop(timeout=5)

        # Second "run" after restart: 3 more cycles -> 6 more measurements.
        q2 = MeasurementQueue()
        sched2 = SensorScheduler([MockSHT45(seed=9)], specs, q2)
        sched2.run_cycles(3)
        db2 = Database(path)
        w2 = MeasurementWriter(db2, batch_size=100)
        w2.add_many(q2.drain())
        w2.flush()
        total = HistoryQuery(db2).count()
        db2.close()
        sched2.stop(timeout=5)

        assert total == 12  # 6 from each run

    def test_fault_injection_does_not_stop_others(self, tmp_path: Path) -> None:
        good = MockSHT45(seed=1)
        bad = MockSCD4X(seed=2)
        bad.set_disconnected()
        specs = {
            SensorId.SHT45: CadenceSpec(sensor=SensorId.SHT45, interval_seconds=0.05),
            SensorId.SCD4X: CadenceSpec(
                sensor=SensorId.SCD4X, interval_seconds=0.05, max_retries=1
            ),
        }
        queue = MeasurementQueue()
        sched = SensorScheduler([good, bad], specs, queue)
        sched.run_cycles(5)
        items = queue.drain()
        assert any(m.sensor is SensorId.SHT45 for m in items)
        assert not any(m.sensor is SensorId.SCD4X for m in items)
        stats = sched.stats
        assert stats[SensorId.SCD4X].reads_failed > 0
        assert stats[SensorId.SHT45].reads_failed == 0
