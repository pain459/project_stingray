"""Sensirion SPS30 particulate matter sensor driver.

The SPS30 sits on the shared I2C bus (default address ``0x69``). It measures
mass and number concentrations for five particle size channels (PM1.0, PM2.5,
PM4.0, PM10, and PM0.5 for number only).

Protocol (I2C, 16-bit big-endian pointer + data, CRC-8 per 2-byte word):

* Start Measurement (0x0010): write 3 bytes (format, dummy, CRC) to begin
  continuous 1 Hz measurements.
* Read Measured Values (0x0300): read 30 bytes (10 words × 2 bytes + 10 CRCs).
  The sensor always returns the latest values; no data-ready polling needed.

The driver uses the 16-bit unsigned integer output format (0x05), which
simplifies parsing compared to IEEE754 floats.

Failure policy matches the SHT45 driver: deployment errors (missing smbus2,
no /dev/i2c-N) raise SensorInitError; an absent sensor is reported
DISCONNECTED and retried.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
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

log = logging.getLogger("stingray.sensors.sps30")

#: Start Measurement command (pointer 0x0010).
CMD_START_MEASUREMENT = 0x0010
#: Read Measured Values command (pointer 0x0300).
CMD_READ_MEASURED_VALUES = 0x0300
#: Read firmware version (pointer 0xD100).
CMD_READ_VERSION = 0xD100
#: Read serial number (pointer 0xD033).
CMD_READ_SERIAL = 0xD033

#: Output format: 16-bit unsigned integer (big-endian).
FORMAT_UINT16 = 0x05

#: Datasheet: start/stop measurement takes < 20 ms; add margin.
CMD_DELAY_S = 0.025
#: Datasheet: read operations are fast; keep a small settling time.
READ_DELAY_S = 0.005

#: Number of 16-bit values in a measurement response.
_NUM_VALUES = 10
#: Total bytes to read for one measurement (10 words × 2 + 10 CRCs).
_RESPONSE_BYTES = 30

#: Response indices for each supported metric (uint16 format).
# The SPS30 returns 10 values:
#   0: PM1.0 mass, 1: PM2.5 mass, 2: PM4.0 mass, 3: PM10 mass,
#   4: PM0.5 number (not in our model), 5: PM1.0 number,
#   6: PM2.5 number, 7: PM4.0 number, 8: PM10 number,
#   9: typical particle size (not in our model)
_METRIC_INDICES: tuple[tuple[MetricId, int], ...] = (
    (MetricId.PM1_UG_M3, 0),
    (MetricId.PM2_5_UG_M3, 1),
    (MetricId.PM4_UG_M3, 2),
    (MetricId.PM10_UG_M3, 3),
    (MetricId.PM1_NUM_M3, 5),
    (MetricId.PM2_5_NUM_M3, 6),
    (MetricId.PM4_NUM_M3, 7),
    (MetricId.PM10_NUM_M3, 8),
)


def _crc8(data: bytes | list[int]) -> int:
    """Sensirion CRC-8: polynomial 0x31, init 0xFF, no reflection, no final XOR."""
    crc = 0xFF
    for byte in data:
        crc ^= byte & 0xFF
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _uint16_to_value(raw: int) -> float:
    """Convert a 16-bit SPS30 reading to engineering units.

    Both mass (µg/m³) and number (1/m³) concentrations use the same scaling:
    value = raw / 100.0 (datasheet specifies 0.01 resolution for both).
    """
    return raw / 100.0


class SPS30(SensorDriver):
    """Sensirion SPS30 particulate matter sensor.

    Args:
        bus: pre-built transport, mainly for tests. When omitted the driver
            opens ``/dev/i2c-<bus_number>`` on first :meth:`initialize`.
        bus_number: I2C bus index (1 on a Raspberry Pi header).
        address: 7-bit I2C address (0x69 for SPS30).
        sleep: injected sleep function so tests need no real delay.
    """

    DEFAULT_ADDRESS = 0x69

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
        self._firmware: tuple[int, int] | None = None
        self._serial: str | None = None

    # -- introspection -----------------------------------------------------

    @property
    def sensor_id(self) -> SensorId:
        return SensorId.SPS30

    @property
    def address(self) -> int:
        return self._address

    @property
    def bus_number(self) -> int:
        return self._bus_number

    @property
    def firmware_version(self) -> tuple[int, int] | None:
        """Cached firmware (major, minor), or None if not yet read."""
        return self._firmware

    @property
    def serial_number(self) -> str | None:
        """Cached serial number string, or None if not yet read."""
        return self._serial

    def __repr__(self) -> str:
        fw = f"fw={self._firmware[0]}.{self._firmware[1]}" if self._firmware else "fw=?"
        serial = f"serial={self._serial}" if self._serial else "serial=?"
        return f"SPS30(bus={self._bus_number}, address=0x{self._address:02X}, {fw}, {serial})"

    # -- driver protocol ---------------------------------------------------

    def initialize(self) -> None:
        """Open the bus, probe the sensor, and start continuous measurement.

        Raises:
            SensorInitError: only for deployment errors (missing ``smbus2`` or
                a missing ``/dev/i2c-N``). An absent sensor is recorded as
                disconnected instead of aborting startup.
        """
        if self._closed:
            raise SensorInitError("sps30: driver is closed")
        if self._bus is None:
            self._bus = Smbus2Transport(self._bus_number)

        self._initialized = True

        try:
            self._probe()
        except SensorError as exc:
            self._disconnected = True
            self._consecutive_failures += 1
            log.warning(
                "sps30: no working device at 0x%02X on bus %d (%s); "
                "reported as DISCONNECTED and retried on every cycle",
                self._address,
                self._bus_number,
                exc,
            )
            return
        except OSError as exc:
            self._disconnected = True
            self._consecutive_failures += 1
            log.warning("sps30: probe I/O error at 0x%02X: %s", self._address, exc)
            return

        self._disconnected = False
        self._consecutive_failures = 0

        # Read firmware version and serial (best-effort, non-fatal).
        try:
            self._firmware = self._read_firmware_version()
        except (SensorError, OSError):
            self._firmware = None
        try:
            self._serial = self._read_serial_number()
        except (SensorError, OSError):
            self._serial = None

        # Start continuous measurement mode.
        try:
            self._start_measurement()
        except (SensorError, OSError) as exc:
            log.warning("sps30: failed to start measurement: %s", exc)
            self._disconnected = True
            self._consecutive_failures += 1
            return

        log.info(
            "sps30: ready on bus %d at 0x%02X%s%s",
            self._bus_number,
            self._address,
            f" fw={self._firmware[0]}.{self._firmware[1]}" if self._firmware else "",
            f" serial={self._serial}" if self._serial else "",
        )

    def read(self) -> Iterator[Measurement]:
        """Read the latest measurement, yielding all 8 supported metrics."""
        if self._closed:
            raise SensorReadError("sps30: driver is closed")
        if not self._initialized:
            raise SensorInitError("sps30: driver not initialized")

        try:
            values = self._read_values()
        except SensorError:
            self._consecutive_failures += 1
            raise
        except OSError as exc:
            self._consecutive_failures += 1
            if is_disconnect_error(exc):
                self._disconnected = True
                raise SensorDisconnectedError(
                    f"sps30: no device at 0x{self._address:02X} on bus {self._bus_number}: {exc}"
                ) from exc
            raise SensorReadError(f"sps30: I2C transfer failed: {exc}") from exc

        self._disconnected = False
        self._consecutive_failures = 0
        observed_at = utc_now()
        self._last_read_wall = observed_at

        for metric, idx in _METRIC_INDICES:
            value = _uint16_to_value(values[idx])
            yield self._measurement(metric, value, observed_at)

    def health(self) -> SensorHealth:
        if not self._initialized and not self._closed:
            state = SensorState.INITIALIZING
        else:
            state = state_from_failures(
                self._consecutive_failures, disconnected=self._disconnected
            )
        return SensorHealth(
            sensor=SensorId.SPS30,
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
            with suppress(Exception):
                bus.close()

    # -- sensor commands ---------------------------------------------------

    def _start_measurement(self) -> None:
        """Send the Start Measurement command (0x0010) with uint16 format."""
        data = [FORMAT_UINT16, 0x00]
        crc = _crc8(data)
        self._write_command(CMD_START_MEASUREMENT, data + [crc])
        self._sleep(CMD_DELAY_S)

    def _read_firmware_version(self) -> tuple[int, int]:
        """Read the firmware version (2 bytes: major, minor + CRC)."""
        raw = self._read_command(CMD_READ_VERSION, 3)
        if _crc8(raw[0:2]) != raw[2]:
            raise SensorReadError("sps30: CRC mismatch on firmware version")
        return (raw[0], raw[1])

    def _read_serial_number(self) -> str:
        """Read the serial number (up to 48 bytes of hex pairs + CRCs).

        Reads in 30-byte chunks to stay within the SMBus block max of 32.
        """
        raw: list[int] = []
        offset = 0
        while offset < 48:
            chunk_len = min(30, 48 - offset)
            chunk = self._read_command(CMD_READ_SERIAL + offset, chunk_len)
            raw.extend(chunk)
            offset += chunk_len
        chars: list[str] = []
        for i in range(0, len(raw) - 2, 3):
            word = (raw[i] << 8) | raw[i + 1]
            if word == 0:
                break
            chars.append(f"{word:04X}")
        return "".join(chars) if chars else "unknown"

    # -- internals ---------------------------------------------------------

    def _probe(self) -> None:
        """Verify the sensor responds by reading the firmware version."""
        self._read_firmware_version()

    def _read_values(self) -> list[int]:
        """Read 10 16-bit values from the sensor, validating all CRCs."""
        raw = self._read_command(CMD_READ_MEASURED_VALUES, _RESPONSE_BYTES)
        values: list[int] = []
        for i in range(0, _RESPONSE_BYTES, 3):
            word = (raw[i] << 8) | raw[i + 1]
            if _crc8(raw[i : i + 2]) != raw[i + 2]:
                raise SensorReadError(f"sps30: CRC mismatch on value word {i // 3}")
            values.append(word)
        return values

    def _write_command(self, pointer: int, data: list[int]) -> None:
        """Write a 16-bit pointer followed by data bytes in one transaction."""
        bus = self._require_bus()
        payload = [(pointer >> 8) & 0xFF, pointer & 0xFF] + data
        bus.write_block(self._address, payload)

    def _read_command(self, pointer: int, length: int) -> list[int]:
        """Write a 16-bit pointer, then read ``length`` bytes."""
        bus = self._require_bus()
        bus.write_block(self._address, [(pointer >> 8) & 0xFF, pointer & 0xFF])
        self._sleep(READ_DELAY_S)
        raw = bus.read(self._address, length)
        if len(raw) != length:
            raise SensorReadError(f"sps30: expected {length} bytes, received {len(raw)}")
        return [int(b) & 0xFF for b in raw]

    def _require_bus(self) -> I2CTransport:
        if self._bus is None:
            raise SensorInitError("sps30: I2C transport is not open")
        return self._bus

    def _measurement(self, metric: MetricId, value: float, observed_at: datetime) -> Measurement:
        return Measurement(
            sensor=SensorId.SPS30,
            metric=metric,
            value=round(value, 2),
            unit=METRIC_UNITS[metric],
            observed_at=observed_at,
            received_at=utc_now(),
        )
