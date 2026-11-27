"""Unit tests for mock sensors: determinism, failure injection, contract."""

from __future__ import annotations

import pytest

from stingray.core.errors import (
    SensorDisconnectedError,
    SensorInitError,
    SensorReadError,
)
from stingray.core.models import MetricId, SensorId, SensorState
from stingray.sensors.mock import MockPiCpu, MockSCD4X, MockSGP41, MockSHT45, MockSPS30


class TestMockSHT45:
    def test_deterministic_sequence(self) -> None:
        a = MockSHT45(seed=42)
        a.initialize()
        b = MockSHT45(seed=42)
        b.initialize()
        for _ in range(5):
            ma = list(a.read())
            mb = list(b.read())
            assert [m.value for m in ma] == [m.value for m in mb]

    def test_produces_temp_and_rh(self) -> None:
        s = MockSHT45(seed=1)
        s.initialize()
        readings = list(s.read())
        metrics = {m.metric for m in readings}
        assert MetricId.TEMPERATURE_C in metrics
        assert MetricId.RELATIVE_HUMIDITY_PERCENT in metrics

    def test_requires_initialize(self) -> None:
        s = MockSHT45()
        with pytest.raises(SensorInitError):
            list(s.read())

    def test_closed_sensor_raises(self) -> None:
        s = MockSHT45()
        s.initialize()
        s.close()
        with pytest.raises(SensorReadError):
            list(s.read())


class TestMockSCD4X:
    def test_produces_co2_temp_rh(self) -> None:
        s = MockSCD4X(seed=7)
        s.initialize()
        metrics = {m.metric for m in s.read()}
        assert MetricId.CO2_PPM in metrics
        assert MetricId.TEMPERATURE_C in metrics
        assert MetricId.RELATIVE_HUMIDITY_PERCENT in metrics


class TestMockSGP41:
    def test_produces_raw_and_index(self) -> None:
        s = MockSGP41(seed=3)
        s.initialize()
        metrics = {m.metric for m in s.read()}
        assert MetricId.VOC_RAW in metrics
        assert MetricId.NOX_RAW in metrics
        assert MetricId.VOC_INDEX in metrics
        assert MetricId.NOX_INDEX in metrics


class TestMockSPS30:
    def test_produces_pm_mass_and_number(self) -> None:
        s = MockSPS30(seed=5)
        s.initialize()
        metrics = {m.metric for m in s.read()}
        for expected in (
            MetricId.PM1_UG_M3,
            MetricId.PM2_5_UG_M3,
            MetricId.PM4_UG_M3,
            MetricId.PM10_UG_M3,
            MetricId.PM2_5_NUM_M3,
        ):
            assert expected in metrics


class TestMockPiCpu:
    def test_produces_distinct_cpu_metric(self) -> None:
        s = MockPiCpu(seed=1)
        s.initialize()
        readings = list(s.read())

        assert len(readings) == 1
        assert readings[0].sensor is SensorId.PI
        assert readings[0].metric is MetricId.CPU_TEMPERATURE_C
        assert readings[0].unit == "degC"


class TestFailureInjection:
    def test_timeout_failure(self) -> None:
        s = MockSHT45(seed=1)
        s.initialize()
        s.inject_failure("timeout", count=1)
        with pytest.raises(SensorReadError):
            list(s.read())
        # Next read succeeds.
        assert len(list(s.read())) == 2

    def test_crc_failure(self) -> None:
        s = MockSHT45(seed=1)
        s.initialize()
        s.inject_failure("crc", count=1)
        with pytest.raises(SensorReadError):
            list(s.read())

    def test_disconnected_failure(self) -> None:
        s = MockSHT45(seed=1)
        s.initialize()
        s.set_disconnected()
        with pytest.raises(SensorDisconnectedError):
            list(s.read())
        assert s.health().state is SensorState.DISCONNECTED

    def test_multiple_failures_then_recovery(self) -> None:
        s = MockSHT45(seed=1)
        s.initialize()
        s.inject_failure("timeout", count=3)
        for _ in range(3):
            with pytest.raises(SensorReadError):
                list(s.read())
        assert s.health().consecutive_failures == 3
        assert s.health().state is SensorState.FAILED
        # Recovery: next successful read resets the counter.
        list(s.read())
        assert s.health().consecutive_failures == 0
        assert s.health().state is SensorState.READY


class TestHealth:
    def test_initializing_state(self) -> None:
        s = MockSHT45()
        assert s.health().state is SensorState.INITIALIZING

    def test_ready_after_init(self) -> None:
        s = MockSHT45()
        s.initialize()
        assert s.health().state is SensorState.READY

    def test_last_read_set_after_read(self) -> None:
        s = MockSHT45()
        s.initialize()
        assert s.health().last_read_at is None
        list(s.read())
        assert s.health().last_read_at is not None


class TestContract:
    """Every mock sensor must satisfy the SensorDriver protocol."""

    @pytest.mark.parametrize(
        "factory",
        [MockSHT45, MockSCD4X, MockSGP41, MockSPS30, MockPiCpu],
    )
    def test_protocol(self, factory: type) -> None:
        s = factory(seed=0)
        assert isinstance(s.sensor_id, SensorId)
        s.initialize()
        readings = list(s.read())
        assert len(readings) >= 1
        for m in readings:
            assert m.observed_at.tzinfo is not None
            assert m.received_at.tzinfo is not None
            assert m.value == m.value  # not NaN
        s.close()
        s.close()  # idempotent
