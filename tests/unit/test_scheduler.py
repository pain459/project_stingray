"""Unit tests for the sensor scheduler: cadence, failure isolation, shutdown."""

from __future__ import annotations

import time

import pytest

from stingray.core.errors import SensorError
from stingray.core.models import SensorId
from stingray.core.scheduler import MeasurementQueue, SensorScheduler, build_specs_from_config
from stingray.core.sensor_protocol import CadenceSpec
from stingray.sensors.mock import MockSCD4X, MockSGP41, MockSHT45, MockSPS30


def _specs() -> dict[SensorId, CadenceSpec]:
    return {
        SensorId.SHT45: CadenceSpec(sensor=SensorId.SHT45, interval_seconds=1.0),
        SensorId.SCD4X: CadenceSpec(sensor=SensorId.SCD4X, interval_seconds=1.0),
        SensorId.SGP41: CadenceSpec(sensor=SensorId.SGP41, interval_seconds=1.0),
        SensorId.SPS30: CadenceSpec(sensor=SensorId.SPS30, interval_seconds=1.0),
    }


class TestSchedulerSync:
    def test_all_sensors_produce_measurements(self) -> None:
        drivers = [MockSHT45(seed=1), MockSCD4X(seed=2), MockSGP41(seed=3), MockSPS30(seed=4)]
        queue = MeasurementQueue()
        sched = SensorScheduler(drivers, _specs(), queue)
        sched.run_cycles(3)
        items = queue.drain()
        assert len(items) > 0
        sensors_seen = {m.sensor for m in items}
        assert sensors_seen == {SensorId.SHT45, SensorId.SCD4X, SensorId.SGP41, SensorId.SPS30}

    def test_failure_isolation(self) -> None:
        good = MockSHT45(seed=1)
        bad = MockSCD4X(seed=2)
        bad.inject_failure("timeout", count=100)
        specs = {
            SensorId.SHT45: CadenceSpec(sensor=SensorId.SHT45, interval_seconds=1.0),
            SensorId.SCD4X: CadenceSpec(sensor=SensorId.SCD4X, interval_seconds=1.0, max_retries=2),
        }
        queue = MeasurementQueue()
        sched = SensorScheduler([good, bad], specs, queue)
        sched.run_cycles(5)
        items = queue.drain()
        # SHT45 kept producing despite SCD4X failing.
        assert any(m.sensor is SensorId.SHT45 for m in items)
        stats = sched.stats
        assert stats[SensorId.SCD4X].reads_failed > 0
        assert stats[SensorId.SHT45].reads_failed == 0

    def test_retry_exhaustion_backs_off(self) -> None:
        bad = MockSHT45(seed=1)
        bad.inject_failure("timeout", count=100)
        specs = {
            SensorId.SHT45: CadenceSpec(sensor=SensorId.SHT45, interval_seconds=1.0, max_retries=2)
        }
        queue = MeasurementQueue()
        sched = SensorScheduler([bad], specs, queue)
        sched.run_cycles(10)
        stats = sched.stats
        assert stats[SensorId.SHT45].reads_failed > 0
        assert stats[SensorId.SHT45].reads_ok == 0

    def test_recovery_after_failures(self) -> None:
        s = MockSHT45(seed=1)
        s.inject_failure("timeout", count=2)
        specs = {
            SensorId.SHT45: CadenceSpec(sensor=SensorId.SHT45, interval_seconds=0.0, max_retries=5)
        }
        queue = MeasurementQueue()
        sched = SensorScheduler([s], specs, queue)
        sched.run_cycles(10)
        stats = sched.stats
        assert stats[SensorId.SHT45].reads_failed == 2
        assert stats[SensorId.SHT45].reads_ok > 0


class TestSchedulerThreaded:
    def test_start_stop(self) -> None:
        drivers = [MockSHT45(seed=1)]
        specs = {SensorId.SHT45: CadenceSpec(sensor=SensorId.SHT45, interval_seconds=0.05)}
        queue = MeasurementQueue()
        sched = SensorScheduler(drivers, specs, queue, tick_seconds=0.01)
        sched.start()
        time.sleep(0.3)
        sched.stop(timeout=5)
        items = queue.drain()
        assert len(items) > 0
        # Driver was closed on stop.
        with pytest.raises(SensorError):
            list(drivers[0].read())

    def test_stop_is_prompt(self) -> None:
        drivers = [MockSHT45(seed=1)]
        specs = {SensorId.SHT45: CadenceSpec(sensor=SensorId.SHT45, interval_seconds=0.05)}
        sched = SensorScheduler(drivers, specs, MeasurementQueue(), tick_seconds=0.01)
        sched.start()
        time.sleep(0.1)
        start = time.monotonic()
        sched.stop(timeout=5)
        elapsed = time.monotonic() - start
        assert elapsed < 2.0


class TestMeasurementQueue:
    def test_put_and_drain(self) -> None:
        from stingray.core.models import Measurement, MetricId, utc_now

        q = MeasurementQueue()
        m = Measurement(
            sensor=SensorId.SHT45,
            metric=MetricId.TEMPERATURE_C,
            value=20.0,
            unit="degC",
            observed_at=utc_now(),
            received_at=utc_now(),
        )
        q.put(m)
        assert len(q) == 1
        items = q.drain()
        assert len(items) == 1
        assert len(q) == 0

    def test_bounded_drops_oldest(self) -> None:
        from stingray.core.models import Measurement, MetricId, utc_now

        q = MeasurementQueue(maxlen=3)
        for i in range(5):
            q.put(
                Measurement(
                    sensor=SensorId.SHT45,
                    metric=MetricId.TEMPERATURE_C,
                    value=float(i),
                    unit="degC",
                    observed_at=utc_now(),
                    received_at=utc_now(),
                )
            )
        assert q.dropped == 2
        items = q.drain()
        assert len(items) == 3
        assert [m.value for m in items] == [2.0, 3.0, 4.0]


class TestBuildSpecs:
    def test_builds_from_config_dict(self) -> None:
        specs = build_specs_from_config(
            {
                "sht45": {"cadence_seconds": 3.0},
                "scd4x": {"cadence_seconds": 5.0},
                "unknown_sensor": {"cadence_seconds": 1.0},
            }
        )
        assert SensorId.SHT45 in specs
        assert specs[SensorId.SHT45].interval_seconds == 3.0
        assert SensorId.SCD4X in specs
        assert "unknown_sensor" not in {s.value for s in specs}
