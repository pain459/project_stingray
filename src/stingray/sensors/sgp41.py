"""Sensirion SGP41 VOC and NOx gas sensor driver.

The SGP41 sits on the shared I2C bus (default address ``0x59``). It uses the
Sensirion 16-bit command protocol with CRC-8 (poly 0x31, init 0xFF) on every
data word, the same family as the SCD4X.

SRAW_VOC / SRAW_NOX are raw sensor signals. They are converted to VOC and NOx
index values with the official Sensirion Gas Index Algorithm (v3.2.0), ported
in :mod:`stingray.sensors.gas_index`. Reporting raw signals as indices would be
wrong: they are on unrelated scales.

Bring-up requirements on the host:

* I2C enabled (``dtparam=i2c_arm=on`` in ``/boot/firmware/config.txt``) so that
  ``/dev/i2c-1`` exists.
* ``smbus2`` installed (``uv sync --extra hardware``).
* The running user in the ``i2c`` group.

Failure policy:

* A deployment problem (no ``smbus2``, no ``/dev/i2c-1``) raises
  :class:`SensorInitError` from :meth:`SGP41.initialize` and stops startup.
* A merely absent sensor does **not** stop startup. The driver reports
  ``DISCONNECTED`` and keeps retrying on every cycle.

Conditioning: the datasheet recommends conditioning once per second for the
first 10 seconds after power-on. During that period VOC/NOx indices are
reported as ``None`` (WARM) rather than as misleading zeros.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import suppress
from datetime import datetime

from stingray.core.errors import (
    SensorDisconnectedError,
    SensorError,
    SensorInitError,
    SensorReadError,
)
from stingray.core.models import (
    METRIC_UNITS,
    Measurement,
    MetricId,
    SensorHealth,
    SensorId,
    SensorState,
    utc_now,
)
from stingray.core.sensor_protocol import SensorDriver, state_from_failures
from stingray.sensors.gas_index import (
    ALGORITHM_TYPE_NOX,
    ALGORITHM_TYPE_VOC,
    GasIndexAlgorithm,
)
from stingray.sensors.i2c import I2CTransport, Smbus2Transport, is_disconnect_error

log = logging.getLogger("stingray.sensors.sgp41")

# -- SGP41 I2C commands (16-bit, big-endian) -------------------------------

#: Execute conditioning. Args: RH ticks, T ticks. Returns SRAW_VOC (2+1 bytes).
CMD_EXECUTE_CONDITIONING = 0x2612
#: Measure raw signals. Args: RH ticks, T ticks.
#: Returns SRAW_VOC (2+1) + SRAW_NOX (2+1) = 6 bytes.
CMD_MEASURE_RAW_SIGNALS = 0x2619
#: Execute self test. Returns test result (2 bytes + CRC).
CMD_EXECUTE_SELF_TEST = 0x280E
#: Turn heater off / stop measurement (datasheet value, NOT 0x0300).
CMD_TURN_HEATER_OFF = 0x3615
#: Get serial number. Returns 3 words (6 data bytes + 3 CRC bytes) = 9 bytes.
CMD_GET_SERIAL_NUMBER = 0x3682

#: Datasheet: conditioning and measurement each take ~50 ms.
CONDITIONING_DELAY_S = 0.05
MEASURE_DELAY_S = 0.05
SELF_TEST_DELAY_S = 0.32
SERIAL_DELAY_S = 0.01
HEATER_OFF_DELAY_S = 0.002

#: Condition once per second for the first 10 seconds after power-on.
CONDITIONING_CYCLES = 10
SAMPLE_INTERVAL_S = 1.0

#: Default RH and T values for humidity compensation (50% RH, 25 degC).
DEFAULT_RH_TICKS = 0x8000
DEFAULT_T_TICKS = 0x6666

#: Built-in self-test success code.
SELF_TEST_PASS = 0xD400


def crc8_sensirion(data: Iterable[int]) -> int:
    """Sensirion CRC-8: polynomial 0x31, init 0xFF, no reflection, no final XOR.

    Datasheet reference vector: ``0xBE 0xEF`` -> ``0x92``.
    """
    crc = 0xFF
    for byte in data:
        crc ^= byte & 0xFF
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _word(value: int) -> list[int]:
    """Encode one 16-bit word as [high, low, CRC]."""
    high, low = (value >> 8) & 0xFF, value & 0xFF
    return [high, low, crc8_sensirion((high, low))]


def _build_command(cmd: int, args: list[int] | None = None) -> list[int]:
    """Build a command frame.

    Commands without arguments carry a CRC byte after the command word;
    commands with arguments are sent as ``[cmd_hi, cmd_lo]`` followed by each
    ``[arg_hi, arg_lo, arg_crc]`` word -- adding a CRC byte after the command
    makes the chip NACK the transfer.
    """
    frame = [(cmd >> 8) & 0xFF, cmd & 0xFF]
    if not args:
        frame.append(crc8_sensirion(frame))
        return frame
    for arg in args:
        frame.extend(_word(arg))
    return frame


def _validate_crc(data: list[int], offset: int) -> None:
    """Validate the CRC byte at ``offset + 2`` for the word at ``offset``."""
    if crc8_sensirion(data[offset : offset + 2]) != data[offset + 2]:
        raise SensorReadError(f"sgp41: CRC mismatch at offset {offset}")


def ticks_to_rh(rh_percent: float) -> int:
    """Convert %RH to SGP41 RH ticks (round, then clamp to 16-bit)."""
    return max(0, min(65535, round(rh_percent * 65535 / 100)))


def ticks_to_temp(temp_c: float) -> int:
    """Convert degrees Celsius to SGP41 temperature ticks."""
    return max(0, min(65535, round((temp_c + 45) * 65535 / 175)))


class SGP41(SensorDriver):
    """SGP41 VOC and NOx gas sensor.

    Args:
        bus: pre-built transport, mainly for tests. When omitted the driver
            opens ``/dev/i2c-<bus_number>`` on first :meth:`initialize`.
        bus_number: I2C bus index (1 on a Raspberry Pi header).
        address: 7-bit I2C address (0x59 for SGP41).
        sleep: injected sleep function so tests need no real delay.
        temperature_provider: optional callable returning ``(temp_c, rh_pct)``
            from the authoritative SHT4x sensor for humidity compensation.
            Defaults to 25 degC / 50 %RH when omitted.
    """

    DEFAULT_ADDRESS = 0x59

    def __init__(
        self,
        *,
        bus: I2CTransport | None = None,
        bus_number: int = 1,
        address: int = DEFAULT_ADDRESS,
        sleep: Callable[[float], None] = time.sleep,
        temperature_provider: Callable[[], tuple[float, float]] | None = None,
    ) -> None:
        self._bus = bus
        self._owns_bus = bus is None
        self._bus_number = bus_number
        self._address = address
        self._sleep = sleep
        self._temperature_provider = temperature_provider
        self._initialized = False
        self._closed = False
        self._disconnected = False
        self._consecutive_failures = 0
        self._last_read_wall: datetime | None = None
        self._serial: int | None = None
        self._self_test_passed: bool | None = None
        self._conditioning_cycles_done = 0
        self._voc_algorithm = GasIndexAlgorithm(ALGORITHM_TYPE_VOC, SAMPLE_INTERVAL_S)
        self._nox_algorithm = GasIndexAlgorithm(ALGORITHM_TYPE_NOX, SAMPLE_INTERVAL_S)

    # -- introspection -----------------------------------------------------

    @property
    def sensor_id(self) -> SensorId:
        return SensorId.SGP41

    @property
    def address(self) -> int:
        return self._address

    @property
    def bus_number(self) -> int:
        return self._bus_number

    @property
    def serial_number(self) -> int | None:
        """Cached 48-bit serial number, or ``None`` if it could not be read."""
        return self._serial

    @property
    def in_warmup(self) -> bool:
        """True while the sensor is still in its conditioning period."""
        return self._conditioning_cycles_done < CONDITIONING_CYCLES

    @property
    def conditioning_cycles_done(self) -> int:
        return self._conditioning_cycles_done

    @property
    def gas_indices_ready(self) -> bool:
        """True once both index algorithms have left their initial blackout.

        The Sensirion algorithm returns a sentinel ``0`` while it learns a
        baseline. Those values must be reported as WARM/null, never as zero
        indices, so the driver simply omits index measurements until this is
        true. Raw signals are always published.
        """
        return not (
            self._voc_algorithm.warming_up or self._nox_algorithm.warming_up
        )

    @property
    def compensation_source(self) -> str:
        """Human-readable source of the T/RH used for gas compensation."""
        if self._temperature_provider is None:
            return "default (25 C / 50 %RH)"
        return "SHT4x (SHT45)"

    def __repr__(self) -> str:
        return (
            f"SGP41(bus={self._bus_number}, address=0x{self._address:02X}, "
            f"serial={'None' if self._serial is None else f'0x{self._serial:012X}'})"
        )

    # -- driver protocol ---------------------------------------------------

    def initialize(self) -> None:
        """Open the bus, probe the sensor and start the conditioning period."""
        if self._closed:
            raise SensorInitError("sgp41: driver is closed")
        if self._bus is None:
            self._bus = Smbus2Transport(self._bus_number)

        self._initialized = True
        try:
            self._probe()
        except SensorError as exc:
            self._disconnected = True
            self._consecutive_failures += 1
            log.warning(
                "sgp41: no working device at 0x%02X on bus %d (%s); "
                "reported as DISCONNECTED and retried on every cycle",
                self._address,
                self._bus_number,
                exc,
            )
            return
        except OSError as exc:
            self._disconnected = True
            self._consecutive_failures += 1
            log.warning("sgp41: probe I/O error at 0x%02X: %s", self._address, exc)
            return

        self._disconnected = False
        self._consecutive_failures = 0

        try:
            self._serial = self.read_serial_number()
        except (SensorError, OSError):
            self._serial = None

        self._conditioning_cycles_done = 0
        log.info(
            "sgp41: ready on bus %d at 0x%02X%s; conditioning %d cycles",
            self._bus_number,
            self._address,
            "" if self._serial is None else f" (serial 0x{self._serial:012X})",
            CONDITIONING_CYCLES,
        )

    def read(self) -> Iterator[Measurement]:
        """Perform one measurement cycle.

        During the first 10 cycles the chip is conditioned once per second and
        no index values are emitted (the standalone validation script shows
        them as WARM). Raw signals are still emitted so the warm-up is visible.
        """
        if self._closed:
            raise SensorReadError("sgp41: driver is closed")
        if not self._initialized:
            raise SensorInitError("sgp41: driver not initialized")

        temperature_c, rh_percent = self._compensation()

        if self.in_warmup:
            try:
                self._condition(ticks_to_rh(rh_percent), ticks_to_temp(temperature_c))
            except (SensorError, OSError) as exc:
                log.warning("sgp41: conditioning cycle failed: %s", exc)
            self._conditioning_cycles_done += 1
            if self._conditioning_cycles_done == CONDITIONING_CYCLES:
                log.info("sgp41: conditioning complete, starting measurements")
            return

        try:
            sraw_voc, sraw_nox = self._measure(
                ticks_to_rh(rh_percent), ticks_to_temp(temperature_c)
            )
        except SensorError:
            self._consecutive_failures += 1
            raise
        except OSError as exc:
            self._consecutive_failures += 1
            if is_disconnect_error(exc):
                self._disconnected = True
                raise SensorDisconnectedError(
                    f"sgp41: no device at 0x{self._address:02X} on bus {self._bus_number}: {exc}"
                ) from exc
            raise SensorReadError(f"sgp41: I2C transfer failed: {exc}") from exc

        self._disconnected = False
        self._consecutive_failures = 0
        observed_at = utc_now()
        self._last_read_wall = observed_at

        # Capture readiness *before* advancing the algorithms: process()
        # increments uptime on its final blackout sample, so checking after
        # would publish that sample's sentinel zero as a real index.
        indices_ready = self.gas_indices_ready

        # Always advance both algorithms, even during their blackout: the
        # reference implementation learns its baseline from those samples.
        voc_index = self._voc_algorithm.process(sraw_voc)
        nox_index = self._nox_algorithm.process(sraw_nox)

        yield self._measurement(MetricId.VOC_RAW, float(sraw_voc), observed_at)
        yield self._measurement(MetricId.NOX_RAW, float(sraw_nox), observed_at)
        if indices_ready:
            yield self._measurement(MetricId.VOC_INDEX, float(voc_index), observed_at)
            yield self._measurement(MetricId.NOX_INDEX, float(nox_index), observed_at)

    def health(self) -> SensorHealth:
        if self._disconnected:
            state = SensorState.DISCONNECTED
        elif not self._initialized and not self._closed:
            state = SensorState.INITIALIZING
        elif self.in_warmup or not self.gas_indices_ready:
            state = SensorState.INITIALIZING
        else:
            state = state_from_failures(
                self._consecutive_failures, disconnected=self._disconnected
            )
        msg = ""
        if state is SensorState.INITIALIZING:
            if self.in_warmup:
                msg = "conditioning"
            elif not self.gas_indices_ready:
                msg = "gas index warmup"
        elif state is not SensorState.READY:
            msg = f"state={state.value}"
        return SensorHealth(
            sensor=SensorId.SGP41,
            state=state,
            last_read_at=self._last_read_wall,
            consecutive_failures=self._consecutive_failures,
            message=msg,
        )

    def close(self) -> None:
        """Turn off the heater and release the bus. Idempotent and never raises."""
        if self._closed:
            return
        self._closed = True
        self._initialized = False
        if self._bus is not None:
            with suppress(Exception):
                self._turn_heater_off()
        bus = self._bus
        self._bus = None
        if bus is not None and self._owns_bus:
            with suppress(Exception):
                bus.close()

    # -- sensor commands ---------------------------------------------------

    def read_serial_number(self) -> int:
        """Read the 48-bit serial number (three 16-bit words)."""
        raw = self._transact(CMD_GET_SERIAL_NUMBER, 9, SERIAL_DELAY_S)
        for offset in (0, 3, 6):
            _validate_crc(raw, offset)
        words = [
            (raw[0] << 8) | raw[1],
            (raw[3] << 8) | raw[4],
            (raw[6] << 8) | raw[7],
        ]
        return (words[0] << 32) | (words[1] << 16) | words[2]

    def execute_self_test(self) -> int:
        """Run the built-in self test and return the result word."""
        raw = self._transact(CMD_EXECUTE_SELF_TEST, 3, SELF_TEST_DELAY_S)
        _validate_crc(raw, 0)
        return (raw[0] << 8) | raw[1]

    def _turn_heater_off(self) -> None:
        """Send the turn-heater-off command (no response expected)."""
        bus = self._require_bus()
        bus.write_block(self._address, _build_command(CMD_TURN_HEATER_OFF))
        self._sleep(HEATER_OFF_DELAY_S)

    def _compensation(self) -> tuple[float, float]:
        """Return ``(temperature_c, rh_percent)`` for gas compensation."""
        if self._temperature_provider is None:
            return 25.0, 50.0
        try:
            temperature_c, rh_percent = self._temperature_provider()
        except Exception as exc:  # never let compensation kill the gas sensor
            log.warning("sgp41: compensation source unavailable (%s); using defaults", exc)
            return 25.0, 50.0
        return float(temperature_c), float(rh_percent)

    # -- internals ---------------------------------------------------------

    def _probe(self) -> None:
        """One self-test to validate presence and firmware behaviour."""
        result = self.execute_self_test()
        self._self_test_passed = result == SELF_TEST_PASS
        if not self._self_test_passed:
            log.warning(
                "sgp41: self-test returned 0x%04X (expected 0x%04X)",
                result,
                SELF_TEST_PASS,
            )

    def _condition(self, rh_ticks: int, t_ticks: int) -> int:
        """Run one conditioning cycle and return SRAW_VOC."""
        bus = self._require_bus()
        frame = _build_command(CMD_EXECUTE_CONDITIONING, [rh_ticks, t_ticks])
        bus.write_block(self._address, frame)
        self._sleep(CONDITIONING_DELAY_S)
        raw = bus.read(self._address, 3)
        if len(raw) != 3:
            raise SensorReadError(f"sgp41: expected 3 bytes, received {len(raw)}")
        raw = [int(b) & 0xFF for b in raw]
        _validate_crc(raw, 0)
        return (raw[0] << 8) | raw[1]

    def _measure(self, rh_ticks: int, t_ticks: int) -> tuple[int, int]:
        """Perform one raw signal measurement with T/RH compensation."""
        bus = self._require_bus()
        frame = _build_command(CMD_MEASURE_RAW_SIGNALS, [rh_ticks, t_ticks])
        bus.write_block(self._address, frame)
        self._sleep(MEASURE_DELAY_S)
        raw = bus.read(self._address, 6)
        if len(raw) != 6:
            raise SensorReadError(f"sgp41: expected 6 bytes, received {len(raw)}")
        raw = [int(b) & 0xFF for b in raw]
        _validate_crc(raw, 0)
        _validate_crc(raw, 3)
        return (raw[0] << 8) | raw[1], (raw[3] << 8) | raw[4]

    def _transact(self, command: int, length: int, delay_s: float) -> list[int]:
        """Send a command (no args) and read ``length`` bytes back."""
        bus = self._require_bus()
        bus.write_block(self._address, _build_command(command))
        self._sleep(delay_s)
        raw = bus.read(self._address, length)
        if len(raw) != length:
            raise SensorReadError(f"sgp41: expected {length} bytes, received {len(raw)}")
        return [int(b) & 0xFF for b in raw]

    def _require_bus(self) -> I2CTransport:
        if self._bus is None:
            raise SensorInitError("sgp41: I2C transport is not open")
        return self._bus

    def _measurement(self, metric: MetricId, value: float, observed_at: datetime) -> Measurement:
        return Measurement(
            sensor=SensorId.SGP41,
            metric=metric,
            value=round(value, 3),
            unit=METRIC_UNITS[metric],
            observed_at=observed_at,
            received_at=utc_now(),
        )
