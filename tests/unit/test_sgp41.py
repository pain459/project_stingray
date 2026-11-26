"""Unit tests for the SGP41 driver (no hardware required)."""

from __future__ import annotations

import errno

import pytest

from stingray.core.errors import SensorDisconnectedError, SensorReadError
from stingray.core.models import MetricId, SensorId, SensorState
from stingray.sensors.sgp41 import (
    CMD_EXECUTE_CONDITIONING,
    CMD_EXECUTE_SELF_TEST,
    CMD_GET_SERIAL_NUMBER,
    CMD_MEASURE_RAW_SIGNALS,
    CONDITIONING_CYCLES,
    DEFAULT_RH_TICKS,
    DEFAULT_T_TICKS,
    SGP41,
    SELF_TEST_PASS,
    _build_command,
    crc8_sensirion,
    ticks_to_rh,
    ticks_to_temp,
)

SELF_TEST_RESPONSE = [0xD4, 0x00, crc8_sensirion([0xD4, 0x00])]
SERIAL_RESPONSE = [
    0x00, 0x00, crc8_sensirion([0x00, 0x00]),
    0x05, 0x9E, crc8_sensirion([0x05, 0x9E]),
    0x86, 0x9D, crc8_sensirion([0x86, 0x9D]),
]
CONDITIONING_RESPONSE = [0x6D, 0x3D, crc8_sensirion([0x6D, 0x3D])]
MEASURE_RESPONSE = [
    0x6D, 0x3A, crc8_sensirion([0x6D, 0x3A]),
    0x3B, 0x98, crc8_sensirion([0x3B, 0x98]),
]


class FakeTransport:
    """In-memory I2C bus that answers SGP41 commands."""

    def __init__(self) -> None:
        self.frames: list[list[int]] = []
        self.raise_on_write: OSError | None = None
        self.raise_on_read: OSError | None = None
        self.corrupt_response = False
        self.closed = 0

    @staticmethod
    def _command(frame: list[int]) -> int:
        return (frame[0] << 8) | frame[1]

    def write_block(self, address: int, data: list[int]) -> None:
        if self.raise_on_write is not None:
            raise self.raise_on_write
        self.frames.append(list(data))

    def read(self, address: int, length: int) -> list[int]:
        if self.raise_on_read is not None:
            raise self.raise_on_read
        command = self._command(self.frames[-1]) if self.frames else 0
        if command == CMD_EXECUTE_SELF_TEST:
            payload = SELF_TEST_RESPONSE
        elif command == CMD_GET_SERIAL_NUMBER:
            payload = SERIAL_RESPONSE
        elif command == CMD_EXECUTE_CONDITIONING:
            payload = CONDITIONING_RESPONSE
        elif command == CMD_MEASURE_RAW_SIGNALS:
            payload = MEASURE_RESPONSE
        else:
            payload = []
        if self.corrupt_response and payload:
            payload = list(payload)
            payload[2] ^= 0xFF
        return payload[:length]

    def close(self) -> None:
        self.closed += 1


def _driver(bus: FakeTransport, **kwargs: object) -> SGP41:
    return SGP41(bus=bus, sleep=lambda _s: None, **kwargs)  # type: ignore[arg-type]


class TestProtocol:
    def test_crc_reference_vector(self) -> None:
        assert crc8_sensirion([0xBE, 0xEF]) == 0x92

    def test_no_arg_command_includes_command_crc(self) -> None:
        frame = _build_command(CMD_EXECUTE_SELF_TEST)
        assert frame == [0x28, 0x0E, crc8_sensirion([0x28, 0x0E])]

    def test_arg_command_omits_command_crc(self) -> None:
        frame = _build_command(CMD_MEASURE_RAW_SIGNALS, [DEFAULT_RH_TICKS, DEFAULT_T_TICKS])
        assert frame[0:2] == [0x26, 0x19]
        assert len(frame) == 8  # 2 command bytes + two 3-byte words
        assert frame[2:5] == [0x80, 0x00, crc8_sensirion([0x80, 0x00])]
        assert frame[5:8] == [0x66, 0x66, crc8_sensirion([0x66, 0x66])]

    def test_tick_conversions(self) -> None:
        assert ticks_to_rh(50) == DEFAULT_RH_TICKS
        assert ticks_to_temp(25) == pytest.approx(DEFAULT_T_TICKS, abs=1)
        assert ticks_to_rh(0) == 0
        assert ticks_to_temp(-45) == 0


class TestInitialization:
    def test_probe_reads_serial_and_selftest(self) -> None:
        bus = FakeTransport()
        driver = _driver(bus)
        driver.initialize()
        assert driver.serial_number == 0x0000059E869D
        assert driver.execute_self_test() == SELF_TEST_PASS
        assert driver.health().state is SensorState.INITIALIZING  # conditioning
        driver.close()

    def test_corrupt_selftest_does_not_abort_startup(self) -> None:
        bus = FakeTransport()
        bus.corrupt_response = True
        driver = _driver(bus)
        driver.initialize()  # must not raise
        assert driver.health().state is SensorState.DISCONNECTED
        driver.close()

    def test_absent_sensor_is_disconnected_not_fatal(self) -> None:
        bus = FakeTransport()
        bus.raise_on_write = OSError(errno.EREMOTEIO, "Remote I/O error")
        driver = _driver(bus)
        driver.initialize()
        assert driver.health().state is SensorState.DISCONNECTED
        driver.close()


class TestReadPath:
    def test_conditioning_then_raw_then_indices(self) -> None:
        bus = FakeTransport()
        driver = _driver(bus)
        driver.initialize()

        for _ in range(CONDITIONING_CYCLES):
            assert list(driver.read()) == []
        assert driver.in_warmup is False

        raw_cycle = list(driver.read())
        assert [m.metric for m in raw_cycle] == [MetricId.VOC_RAW, MetricId.NOX_RAW]
        assert not driver.gas_indices_ready

        # The algorithm blackout suppresses the sentinel zero indices.
        # 46 calls in total are blacked out; the first was the raw_cycle above.
        for _ in range(45):
            readings = list(driver.read())
            assert all(m.metric not in (MetricId.VOC_INDEX, MetricId.NOX_INDEX) for m in readings)

        assert driver.gas_indices_ready
        readings = list(driver.read())
        assert [m.metric for m in readings] == [
            MetricId.VOC_RAW, MetricId.NOX_RAW,
            MetricId.VOC_INDEX, MetricId.NOX_INDEX,
        ]
        assert all(1 <= m.value <= 500 for m in readings if m.metric.value.endswith("index"))
        assert all(m.sensor is SensorId.SGP41 for m in readings)
        driver.close()

    def test_compensation_provider_is_used(self) -> None:
        calls: list[tuple[float, float]] = []

        def provider() -> tuple[float, float]:
            calls.append((30.0, 60.0))
            return calls[-1]

        bus = FakeTransport()
        driver = _driver(bus, temperature_provider=provider)
        driver.initialize()
        for _ in range(CONDITIONING_CYCLES + 1):
            list(driver.read())

        assert calls
        rh_frame = bus.frames[-2]  # last conditioning, then measure
        measure_frame = bus.frames[-1]
        assert measure_frame[0:2] == [0x26, 0x19]
        # RH = round(60 * 65535 / 100) = 39321, T = round(75 * 65535 / 175) = 28086.
        assert measure_frame[2:5] == [0x99, 0x99, crc8_sensirion([0x99, 0x99])]
        assert measure_frame[5:8] == [0x6D, 0xB6, crc8_sensirion([0x6D, 0xB6])]
        assert rh_frame[0:2] == [0x26, 0x12]
        driver.close()

    def test_default_compensation_when_provider_fails(self) -> None:
        def broken_provider() -> tuple[float, float]:
            raise RuntimeError("SHT4x unavailable")

        bus = FakeTransport()
        driver = _driver(bus, temperature_provider=broken_provider)
        driver.initialize()
        for _ in range(CONDITIONING_CYCLES + 1):
            list(driver.read())

        frame = bus.frames[-1]
        assert frame[2:5] == [0x80, 0x00, crc8_sensirion([0x80, 0x00])]
        assert frame[5:8] == [0x66, 0x66, crc8_sensirion([0x66, 0x66])]
        assert driver.compensation_source == "SHT4x (SHT45)"
        driver.close()

    def test_crc_mismatch_raises(self) -> None:
        bus = FakeTransport()
        driver = _driver(bus)
        driver.initialize()
        for _ in range(CONDITIONING_CYCLES):
            list(driver.read())
        bus.corrupt_response = True
        with pytest.raises(SensorReadError, match="CRC mismatch"):
            list(driver.read())
        driver.close()

    def test_disconnect_error_maps_to_disconnected(self) -> None:
        bus = FakeTransport()
        driver = _driver(bus)
        driver.initialize()
        for _ in range(CONDITIONING_CYCLES):
            list(driver.read())
        bus.raise_on_read = OSError(errno.EREMOTEIO, "Remote I/O error")
        with pytest.raises(SensorDisconnectedError):
            list(driver.read())
        assert driver.health().state is SensorState.DISCONNECTED
        driver.close()

    def test_close_is_idempotent(self) -> None:
        bus = FakeTransport()
        driver = _driver(bus)
        driver.initialize()
        driver.close()
        driver.close()
        assert bus.closed == 0  # injected bus is owned by the caller
