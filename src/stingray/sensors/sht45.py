"""Sensirion SHT45 temperature and relative-humidity driver.

The SHT45 sits on the shared I2C bus (default address ``0x44``). This driver
uses the no-clock-stretching high-precision measurement command, validates both
16-bit words with the Sensirion CRC-8, and converts raw ticks with the
datasheet formulae.

Bring-up requirements on the host:

* I2C enabled (``dtparam=i2c_arm=on`` in ``/boot/firmware/config.txt``) so that
  ``/dev/i2c-1`` exists.
* ``smbus2`` installed (``uv sync --extra hardware``).
* The running user in the ``i2c`` group.

Failure policy, chosen so an appliance stays observable:

* A deployment problem (no ``smbus2``, no ``/dev/i2c-1``) raises
  :class:`SensorInitError` from :meth:`SHT45.initialize` and stops startup. That
  is a configuration bug and must be loud.
* A merely absent sensor does **not** stop startup. The driver reports
  ``DISCONNECTED`` and keeps raising on each read, so the dashboard stays up
  and the sensor recovers automatically once it is wired.
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

log = logging.getLogger("stingray.sensors.sht45")

#: Measure T and RH, high repeatability, clock stretching disabled.
CMD_MEASURE_HIGH_PRECISION = 0xFD
#: Soft reset (1 ms).
CMD_SOFT_RESET = 0x94
#: Read the 32-bit serial number (6 bytes: two words plus CRCs).
CMD_READ_SERIAL = 0x89

#: Datasheet maximum for a high-precision measurement is 8.3 ms; add margin.
MEASURE_DELAY_S = 0.012
#: Datasheet soft-reset duration is 1 ms; add margin.
RESET_DELAY_S = 0.002
#: Serial-number read is ready immediately; keep the same small settling time.
SERIAL_DELAY_S = 0.002


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


def ticks_to_temperature(ticks: int) -> float:
    """Convert raw temperature ticks to degrees Celsius.

    ``T = -45 + 175 * S_T / (2^16 - 1)``
    """
    return -45.0 + 175.0 * ticks / 65535.0


def ticks_to_humidity(ticks: int) -> float:
    """Convert raw humidity ticks to %RH, clamped to the physical range.

    ``RH = -6 + 125 * S_RH / (2^16 - 1)``, then clamped to ``[0, 100]`` as the
    datasheet requires (the formula's range is wider than physically valid).
    """
    rh = -6.0 + 125.0 * ticks / 65535.0
    return max(0.0, min(100.0, rh))


class SHT45(SensorDriver):
    """SHT45 temperature and humidity sensor.

    Args:
        bus: pre-built transport, mainly for tests. When omitted the driver
            opens ``/dev/i2c-<bus_number>`` on first :meth:`initialize`.
        bus_number: I2C bus index (1 on a Raspberry Pi header).
        address: 7-bit I2C address (0x44 for SHT45).
        measurement_delay_s: settle time between command and read.
        sleep: injected sleep function so tests need no real delay.
    """

    DEFAULT_ADDRESS = 0x44

    def __init__(
        self,
        *,
        bus: I2CTransport | None = None,
        bus_number: int = 1,
        address: int = DEFAULT_ADDRESS,
        measurement_delay_s: float = MEASURE_DELAY_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._bus = bus
        self._owns_bus = bus is None
        self._bus_number = bus_number
        self._address = address
        self._measurement_delay_s = measurement_delay_s
        self._sleep = sleep
        self._initialized = False
        self._closed = False
        self._disconnected = False
        self._consecutive_failures = 0
        self._last_read_wall: datetime | None = None
        self._serial: int | None = None

    # -- introspection -----------------------------------------------------

    @property
    def sensor_id(self) -> SensorId:
        return SensorId.SHT45

    @property
    def address(self) -> int:
        return self._address

    @property
    def bus_number(self) -> int:
        return self._bus_number

    @property
    def serial_number(self) -> int | None:
        """Cached 32-bit serial number, or ``None`` if it could not be read."""
        return self._serial

    def __repr__(self) -> str:
        return (
            f"SHT45(bus={self._bus_number}, address=0x{self._address:02X}, "
            f"serial={'None' if self._serial is None else f'0x{self._serial:08X}'})"
        )

    # -- driver protocol ---------------------------------------------------

    def initialize(self) -> None:
        """Open the bus, reset the sensor and probe it once.

        Raises:
            SensorInitError: only for deployment errors (missing ``smbus2`` or
                a missing ``/dev/i2c-N``). An absent sensor is recorded as
                disconnected instead of aborting startup.
        """
        if self._closed:
            raise SensorInitError("sht45: driver is closed")
        if self._bus is None:
            self._bus = Smbus2Transport(self._bus_number)

        # Soft reset clears any half-finished transaction from a previous run.
        try:
            self._bus.write_byte(self._address, CMD_SOFT_RESET)
            self._sleep(RESET_DELAY_S)
        except OSError:
            # A NACK here normally means the device is absent. The probe below
            # produces the precise diagnosis, so this is not fatal on its own.
            pass

        self._initialized = True

        try:
            self._probe()
        except SensorError as exc:
            self._disconnected = True
            self._consecutive_failures += 1
            log.warning(
                "sht45: no working device at 0x%02X on bus %d (%s); "
                "reported as DISCONNECTED and retried on every cycle",
                self._address,
                self._bus_number,
                exc,
            )
            return
        except OSError as exc:
            self._disconnected = True
            self._consecutive_failures += 1
            log.warning("sht45: probe I/O error at 0x%02X: %s", self._address, exc)
            return

        self._disconnected = False
        self._consecutive_failures = 0
        try:
            self._serial = self.read_serial_number()
        except (SensorError, OSError):
            self._serial = None
        log.info(
            "sht45: ready on bus %d at 0x%02X%s",
            self._bus_number,
            self._address,
            "" if self._serial is None else f" (serial 0x{self._serial:08X})",
        )

    def read(self) -> Iterator[Measurement]:
        """Perform one measurement, yielding temperature then humidity."""
        if self._closed:
            raise SensorReadError("sht45: driver is closed")
        if not self._initialized:
            raise SensorInitError("sht45: driver not initialized")

        try:
            temperature_c, humidity_percent = self._sample()
        except SensorError:
            self._consecutive_failures += 1
            raise
        except OSError as exc:
            self._consecutive_failures += 1
            if is_disconnect_error(exc):
                self._disconnected = True
                raise SensorDisconnectedError(
                    f"sht45: no device at 0x{self._address:02X} on bus {self._bus_number}: {exc}"
                ) from exc
            raise SensorReadError(f"sht45: I2C transfer failed: {exc}") from exc

        self._disconnected = False
        self._consecutive_failures = 0
        observed_at = utc_now()
        self._last_read_wall = observed_at

        yield self._measurement(MetricId.TEMPERATURE_C, temperature_c, observed_at)
        yield self._measurement(
            MetricId.RELATIVE_HUMIDITY_PERCENT, humidity_percent, observed_at
        )

    def health(self) -> SensorHealth:
        if not self._initialized and not self._closed:
            state = SensorState.INITIALIZING
        else:
            state = state_from_failures(
                self._consecutive_failures, disconnected=self._disconnected
            )
        return SensorHealth(
            sensor=SensorId.SHT45,
            state=state,
            last_read_at=self._last_read_wall,
            consecutive_failures=self._consecutive_failures,
            message="" if state is SensorState.READY else f"state={state.value}",
        )

    def close(self) -> None:
        """Release the bus. Idempotent and never raises."""
        if self._closed:
            return
        self._closed = True
        self._initialized = False
        bus = self._bus
        self._bus = None
        if bus is not None and self._owns_bus:
            with suppress(Exception):  # close must never raise
                bus.close()

    # -- sensor commands ---------------------------------------------------

    def read_serial_number(self) -> int:
        """Read the 32-bit serial number from the sensor."""
        raw = self._transact(CMD_READ_SERIAL, 6, SERIAL_DELAY_S)
        for offset in (0, 3):
            if crc8_sensirion(raw[offset : offset + 2]) != raw[offset + 2]:
                raise SensorReadError("sht45: CRC mismatch on serial number word")
        return (raw[0] << 24) | (raw[1] << 16) | (raw[3] << 8) | raw[4]

    def soft_reset(self) -> None:
        """Issue a soft reset and wait for the sensor to come back."""
        bus = self._require_bus()
        bus.write_byte(self._address, CMD_SOFT_RESET)
        self._sleep(RESET_DELAY_S)

    # -- internals ---------------------------------------------------------

    def _probe(self) -> None:
        """One full measurement; validates presence, length and both CRCs."""
        self._sample()

    def _sample(self) -> tuple[float, float]:
        raw = self._transact(CMD_MEASURE_HIGH_PRECISION, 6, self._measurement_delay_s)
        if crc8_sensirion(raw[0:2]) != raw[2]:
            raise SensorReadError("sht45: CRC mismatch on temperature word")
        if crc8_sensirion(raw[3:5]) != raw[5]:
            raise SensorReadError("sht45: CRC mismatch on humidity word")
        temperature_ticks = (raw[0] << 8) | raw[1]
        humidity_ticks = (raw[3] << 8) | raw[4]
        return ticks_to_temperature(temperature_ticks), ticks_to_humidity(humidity_ticks)

    def _transact(self, command: int, length: int, delay_s: float) -> list[int]:
        bus = self._require_bus()
        bus.write_byte(self._address, command)
        self._sleep(delay_s)
        raw = bus.read(self._address, length)
        if len(raw) != length:
            raise SensorReadError(f"sht45: expected {length} bytes, received {len(raw)}")
        return [int(b) & 0xFF for b in raw]

    def _require_bus(self) -> I2CTransport:
        if self._bus is None:
            raise SensorInitError("sht45: I2C transport is not open")
        return self._bus

    def _measurement(self, metric: MetricId, value: float, observed_at: datetime) -> Measurement:
        return Measurement(
            sensor=SensorId.SHT45,
            metric=metric,
            value=round(value, 3),
            unit=METRIC_UNITS[metric],
            observed_at=observed_at,
            received_at=utc_now(),
        )
