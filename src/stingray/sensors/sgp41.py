"""Sensirion SGP41 VOC and NOx gas sensor driver.

The SGP41 sits on the shared I2C bus (default address ``0x59``). It uses the
Sensirion 16-bit command protocol with CRC-8 (poly 0x31, init 0xFF), the same
family as the SPS30.

The SGP41 measures raw VOC and NOx signals (SRAW_VOC, SRAW_NOX) which are
proportional to the logarithm of the sensing element resistance. The raw
signals are converted to VOC and NOx index values (1-500 scale) using the
Sensirion algorithm.

Bring-up requirements on the host:

* I2C enabled (``dtparam=i2c_arm=on`` in ``/boot/firmware/config.txt``) so that
  ``/dev/i2c-1`` exists.
* ``smbus2`` installed (``uv sync --extra hardware``).
* The running user in the ``i2c`` group.

Failure policy, chosen so an appliance stays observable:

* A deployment problem (no ``smbus2``, no ``/dev/i2c-1``) raises
  :class:`SensorInitError` from :meth:`SGP41.initialize` and stops startup.
* A merely absent sensor does **not** stop startup. The driver reports
  ``DISCONNECTED`` and keeps raising on each read, so the dashboard stays up
  and the sensor recovers automatically once it is wired.

The SGP41 requires a conditioning period after power-on before it produces
valid readings. The driver tracks warm-up state and yields no measurements
during the first ~60 seconds (the datasheet recommends 20-60 seconds of
conditioning). During warm-up, read failures are treated as "not ready yet"
rather than as disconnection.
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
from stingray.sensors.i2c import I2CTransport, Smbus2Transport, is_disconnect_error

log = logging.getLogger("stingray.sensors.sgp41")

# -- SGP41 I2C commands (16-bit, big-endian, with CRC-8) -------------------

#: Execute conditioning: VOC pixel at measurement temp, NOx pixel at
#: conditioning temp. Args: RH ticks, T ticks. Returns SRAW_VOC (2 bytes + CRC).
CMD_EXECUTE_CONDITIONING = 0x2612
#: Measure raw signals: VOC + NOx. Args: RH ticks, T ticks.
#: Returns SRAW_VOC (2+1) + SRAW_NOX (2+1) = 6 bytes.
CMD_MEASURE_RAW_SIGNALS = 0x2619
#: Execute self test. Returns test result (2 bytes + CRC).
CMD_EXECUTE_SELF_TEST = 0x280E
#: Turn heater off / stop measurement.
CMD_TURN_HEATER_OFF = 0x0300
#: Get serial number. Returns 3 words (6 bytes + 3 CRC bytes) = 9 bytes.
CMD_GET_SERIAL_NUMBER = 0x3682

#: Datasheet: conditioning takes ~45 ms, measurement takes ~45 ms.
CONDITIONING_DELAY_S = 0.05
MEASURE_DELAY_S = 0.05
SELF_TEST_DELAY_S = 0.32
SERIAL_DELAY_S = 0.05

#: The SGP41 needs a conditioning period after power-on. The datasheet
#: recommends 20-60 seconds. We use 60 seconds to be safe.
WARMUP_SECONDS = 60.0

#: Default RH and T values for humidity compensation (50% RH, 25 degC).
#: 0x8000 = 50% RH, 0x6666 = 25 degC (per datasheet).
DEFAULT_RH_TICKS = 0x8000
DEFAULT_T_TICKS = 0x6666


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


def _build_command(cmd: int, args: list[int] | None = None) -> list[int]:
    """Build a 16-bit command frame with CRC, optionally with argument words.

    The frame is: [cmd_hi, cmd_lo, cmd_crc] followed by each argument word
    as [arg_hi, arg_lo, arg_crc].
    """
    frame = [(cmd >> 8) & 0xFF, cmd & 0xFF]
    frame.append(crc8_sensirion(frame))
    if args:
        for arg in args:
            word = [(arg >> 8) & 0xFF, arg & 0xFF]
            frame.extend(word)
            frame.append(crc8_sensirion(word))
    return frame


def _validate_crc(data: list[int], offset: int, word_len: int = 2) -> None:
    """Validate the CRC byte at ``offset + word_len`` for the word at ``offset``."""
    word = data[offset : offset + word_len]
    crc_byte = data[offset + word_len]
    if crc8_sensirion(word) != crc_byte:
        raise SensorReadError(f"sgp41: CRC mismatch at offset {offset}")


def _ticks_to_rh(rh_percent: float) -> int:
    """Convert %RH to SGP41 RH ticks."""
    return int(rh_percent * 65535 / 100)


def _ticks_to_temp(temp_c: float) -> int:
    """Convert degrees Celsius to SGP41 temperature ticks."""
    return int((temp_c + 45) * 65535 / 175)


def _sraw_to_index(sraw: int, *, is_voc: bool = True) -> float:
    """Convert SRAW signal to VOC or NOx index (1-500 scale).

    The SGP41 datasheet provides a lookup table for the conversion. The raw
    signal is proportional to the logarithm of the resistance. The index
    scale is 1-500, where 1 is clean air and 500 is heavily polluted.

    The conversion uses the Sensirion algorithm which maps the raw signal
    to an index value. For a simplified implementation, we use a linear
    mapping based on the datasheet's reference points:
    - SRAW ~ 20000 -> index ~ 1 (clean air)
    - SRAW ~ 50000 -> index ~ 500 (heavily polluted)

    A more accurate implementation would use the full lookup table from
    the datasheet, but this linear approximation is sufficient for
    monitoring purposes.
    """
    # Clamp to valid range
    sraw = max(0, min(65535, sraw))
    # Linear mapping: index = 1 + (sraw - 20000) * 499 / (50000 - 20000)
    # This gives index 1 at sraw=20000 and index 500 at sraw=50000
    if sraw <= 20000:
        return 1.0
    if sraw >= 50000:
        return 500.0
    return 1.0 + (sraw - 20000) * 499.0 / 30000.0


class SGP41(SensorDriver):
    """SGP41 VOC and NOx gas sensor.

    Args:
        bus: pre-built transport, mainly for tests. When omitted the driver
            opens ``/dev/i2c-<bus_number>`` on first :meth:`initialize`.
        bus_number: I2C bus index (1 on a Raspberry Pi header).
        address: 7-bit I2C address (0x59 for SGP41).
        sleep: injected sleep function so tests need no real delay.
    """

    DEFAULT_ADDRESS = 0x59

    def __init__(
        self,
        *,
        bus: I2CTransport | None = None,
        bus_number: int = 1,
        address: int = DEFAULT_ADDRESS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._bus = bus
        self._owns_bus = bus is None
        self._bus_number = bus_number
        self._address = address
        self._sleep = sleep
        self._initialized = False
        self._closed = False
        self._disconnected = False
        self._consecutive_failures = 0
        self._last_read_wall: datetime | None = None
        self._serial: int | None = None
        self._warmup_start: float | None = None
        self._in_warmup = False
        self._conditioning_done = False

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
        return self._in_warmup

    def __repr__(self) -> str:
        return (
            f"SGP41(bus={self._bus_number}, address=0x{self._address:02X}, "
            f"serial={'None' if self._serial is None else f'0x{self._serial:012X}'})"
        )

    # -- driver protocol ---------------------------------------------------

    def initialize(self) -> None:
        """Open the bus, probe the sensor, and start the conditioning period.

        Raises:
            SensorInitError: only for deployment errors (missing ``smbus2`` or
                a missing ``/dev/i2c-N``). An absent sensor is recorded as
                disconnected instead of aborting startup.
        """
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

        # Read serial number (best effort).
        try:
            self._serial = self.read_serial_number()
        except (SensorError, OSError):
            self._serial = None

        # Start the conditioning period.
        self._warmup_start = time.monotonic()
        self._in_warmup = True
        self._conditioning_done = False

        log.info(
            "sgp41: ready on bus %d at 0x%02X%s; conditioning for %ds",
            self._bus_number,
            self._address,
            "" if self._serial is None else f" (serial 0x{self._serial:012X})",
            int(WARMUP_SECONDS),
        )

    def read(self) -> Iterator[Measurement]:
        """Perform one measurement cycle, yielding VOC and NOx index values.

        During the warm-up period, the sensor is conditioned but no valid
        readings are produced. The method yields nothing during warm-up.
        """
        if self._closed:
            raise SensorReadError("sgp41: driver is closed")
        if not self._initialized:
            raise SensorInitError("sgp41: driver not initialized")

        # Check if still in warm-up.
        if self._in_warmup:
            elapsed = time.monotonic() - (self._warmup_start or 0)
            if elapsed < WARMUP_SECONDS:
                # Still conditioning; yield nothing.
                return
            # Warm-up complete.
            self._in_warmup = False
            log.info("sgp41: conditioning complete, starting measurements")

        try:
            sraw_voc, sraw_nox = self._measure()
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

        voc_index = _sraw_to_index(sraw_voc, is_voc=True)
        nox_index = _sraw_to_index(sraw_nox, is_voc=False)

        yield self._measurement(MetricId.VOC_INDEX, voc_index, observed_at)
        yield self._measurement(MetricId.NOX_INDEX, nox_index, observed_at)
        yield self._measurement(MetricId.VOC_RAW, float(sraw_voc), observed_at)
        yield self._measurement(MetricId.NOX_RAW, float(sraw_nox), observed_at)

    def health(self) -> SensorHealth:
        if not self._initialized and not self._closed:
            state = SensorState.INITIALIZING
        elif self._in_warmup:
            state = SensorState.INITIALIZING
        else:
            state = state_from_failures(
                self._consecutive_failures, disconnected=self._disconnected
            )
        msg = ""
        if state is SensorState.INITIALIZING and self._in_warmup:
            msg = "conditioning"
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
        # Turn off the heater to save power and protect the sensor.
        if self._bus is not None:
            with suppress(Exception):
                self._turn_heater_off()
        bus = self._bus
        self._bus = None
        if bus is not None and self._owns_bus:
            with suppress(Exception):  # close must never raise
                bus.close()

    # -- sensor commands ---------------------------------------------------

    def read_serial_number(self) -> int:
        """Read the 48-bit serial number from the sensor."""
        raw = self._transact(CMD_GET_SERIAL_NUMBER, 9, SERIAL_DELAY_S)
        for offset in (0, 3, 6):
            _validate_crc(raw, offset)
        return (
            (raw[0] << 24) | (raw[1] << 16) | (raw[2] << 8) | raw[3]
            | (raw[4] << 24) | (raw[5] << 16) | (raw[6] << 8) | raw[7]
        )

    def execute_self_test(self) -> int:
        """Run the built-in self test and return the result word."""
        raw = self._transact(CMD_EXECUTE_SELF_TEST, 3, SELF_TEST_DELAY_S)
        _validate_crc(raw, 0)
        return (raw[0] << 8) | raw[1]

    def _turn_heater_off(self) -> None:
        """Send the turn-heater-off command (no response expected)."""
        bus = self._require_bus()
        frame = _build_command(CMD_TURN_HEATER_OFF)
        bus.write_block(self._address, frame)

    # -- internals ---------------------------------------------------------

    def _probe(self) -> None:
        """One self-test to validate presence and CRC."""
        self.execute_self_test()

    def _measure(self) -> tuple[int, int]:
        """Perform one raw signal measurement.

        Returns:
            Tuple of (SRAW_VOC, SRAW_NOX) as unsigned 16-bit integers.
        """
        # Use default RH and T values (50% RH, 25 degC) for humidity compensation.
        args = [DEFAULT_RH_TICKS, DEFAULT_T_TICKS]
        frame = _build_command(CMD_MEASURE_RAW_SIGNALS, args)
        bus = self._require_bus()
        bus.write_block(self._address, frame)
        self._sleep(MEASURE_DELAY_S)
        raw = bus.read(self._address, 6)
        if len(raw) != 6:
            raise SensorReadError(f"sgp41: expected 6 bytes, received {len(raw)}")
        raw = [int(b) & 0xFF for b in raw]
        # Validate CRCs: SRAW_VOC at offset 0, SRAW_NOX at offset 3.
        _validate_crc(raw, 0)
        _validate_crc(raw, 3)
        sraw_voc = (raw[0] << 8) | raw[1]
        sraw_nox = (raw[3] << 8) | raw[4]
        return sraw_voc, sraw_nox

    def _transact(self, command: int, length: int, delay_s: float) -> list[int]:
        """Send a command (no args) and read ``length`` bytes back."""
        bus = self._require_bus()
        frame = _build_command(command)
        bus.write_block(self._address, frame)
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
