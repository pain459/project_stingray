"""Unit tests for the pure-Python Sensirion Gas Index Algorithm port."""

from __future__ import annotations

from stingray.sensors.gas_index import (
    ALGORITHM_TYPE_NOX,
    ALGORITHM_TYPE_VOC,
    INITIAL_BLACKOUT,
    GasIndexAlgorithm,
)


class TestGasIndexAlgorithm:
    def test_voc_stable_state_reaches_100(self) -> None:
        algorithm = GasIndexAlgorithm(ALGORITHM_TYPE_VOC)
        for _ in range(200):
            algorithm.process(1337)
        assert algorithm.process(1337) == 100

    def test_nox_stable_state_reaches_1(self) -> None:
        algorithm = GasIndexAlgorithm(ALGORITHM_TYPE_NOX)
        for _ in range(200):
            algorithm.process(1337)
        assert algorithm.process(1337) == 1

    def test_blackout_returns_sentinel_zero(self) -> None:
        algorithm = GasIndexAlgorithm(ALGORITHM_TYPE_VOC)
        for _ in range(45):
            assert algorithm.process(20000) == 0

    def test_warming_up_tracks_blackout(self) -> None:
        algorithm = GasIndexAlgorithm(ALGORITHM_TYPE_VOC)
        assert algorithm.warming_up
        for _ in range(int(INITIAL_BLACKOUT) + 1):
            algorithm.process(20000)
        assert not algorithm.warming_up

    def test_index_is_bounded(self) -> None:
        algorithm = GasIndexAlgorithm(ALGORITHM_TYPE_VOC)
        for _ in range(47):  # skip the initial blackout sentinel zeros
            algorithm.process(20000)
        for i in range(120):
            value = algorithm.process(20000 + i * 100)
            assert 1 <= value <= 500

    def test_reset_restores_blackout(self) -> None:
        algorithm = GasIndexAlgorithm(ALGORITHM_TYPE_VOC)
        for _ in range(60):
            algorithm.process(20000)
        assert not algorithm.warming_up
        algorithm.reset()
        assert algorithm.warming_up
        assert algorithm.process(20000) == 0
