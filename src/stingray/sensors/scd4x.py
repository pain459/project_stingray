"""Sensirion SCD4X CO2 sensor driver.

The SCD4X (SCD40/SCD41/SCD43) sits on the shared I2C bus (default address
``0x62``). It measures CO2 concentration (ppm), temperature (°C), and relative
humidity (%).

Protocol (I2C, 16-bit big-endian commands + data, CRC-8 per 2-byte word):

* Start Periodic Measurement (0x21B1): start continuous 5-second measurements.
* Read Measurement (0xEC05): read 9 bytes (3 words × 2 bytes + 3 CRCs).
  - CO2: ppm (direct)
  - Temperature: T = -45 + 175 * raw / 65535
  - Humidity: RH = 100 * raw / 65535

The driver uses periodic measurement mode with a 5-second update interval.

Failure policy matches the SHT45 and SPS30 drivers: deployment errors (missing
smbus2, no /dev/i2c-N) raise SensorInitError; an absent sensor is reported
DISCONNECTED and retried.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import suppress
from datetime import datetime

from stingray.core.errors import (
    SensorDisconnectedError,
    SensorInitError,
    SensorReadError,
)
from stingray.core.models import Measurement, MetricId, SensorHealth, SensorId, SensorState
from stingray.core.sensor_protocol import SensorDriver
from stingray.sensors.i2c import I2CTransport, Smbus2Transport

logger = logging.getLogger(__name__)

# I2C address (default for SCD40/SCD41/SCD43)
DEFAULT_ADDRESS = 0x62

# Commands (16-bit big-endian)
CMD_START_PERIODIC_MEASUREMENT = 0x21B1
CMD_READ_MEASUREMENT = 0xEC05
CMD_STOP_PERIODIC_MEASUREMENT = 0x3F86
CMD_GET_SENSOR_VARIANT = 0x202F
CMD_GET_DATA_READY_STATUS = 0x2029

# Response sizes (bytes)
RESPONSE_SIZE_MEASUREMENT = 9  # 3 words × 2 bytes + 3 CRCs
RESPONSE_SIZE_VARIANT = 3  # 1 word × 2 bytes + 1 CRC


def _crc8(data: list[int]) -> int:
    """Compute CRC-8 with polynomial 0x31 and initial value 0xFF."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _temp_to_celsius(raw: int) -> float:
    """Convert SCD4X temperature raw value to degrees Celsius."""
    return -45.0 + 175.0 * raw / 65535.0


def _rh_to_percent(raw: int) -> float:
    """Convert SCD4X humidity raw value to percent RH."""
    return 100.0 * raw / 65535.0


class SCD4X(SensorDriver):
    """Sensirion SCD4X CO2 sensor driver.

    Measures CO2 (ppm), temperature (°C), and relative humidity (%).
    Uses periodic measurement mode with 5-second update interval.
    """

    DEFAULT_ADDRESS = 0x62

    def __init__(
        self,
        *,
        bus: I2CTransport | None = None,
        bus_number: int = 1,
        address: int = DEFAULT_ADDRESS,
        read_timeout: float = 2.0,
    ) -> None:
        self._bus = bus
        self._owns_bus = bus is None
        self._bus_number = bus_number
        self._address = address
        self._read_timeout = read_timeout
        self._state = SensorState.DISCONNECTED
        self._last_read: datetime | None = None
        self._closed = False

    @property
    def sensor_id(self) -> SensorId:
        return SensorId.SCD4X

    @property
    def state(self) -> SensorState:
        return self._state

    def initialize(self) -> None:
        """Initialize the sensor and start periodic measurement.

        Raises:
            SensorInitError: If the sensor is not present or fails to respond.
        """
        if self._bus is None:
            self._bus = Smbus2Transport(self._bus_number)

        try:
            # Stop any ongoing measurement first (safe to call even if not measuring)
            self._write_command(CMD_STOP_PERIODIC_MEASUREMENT)
            time.sleep(0.01)

            # Check if sensor is present by reading sensor variant
            variant = self._read_variant()
            logger.info(
                "scd4x: ready at 0x%02X variant=%s",
                self._address,
                self._variant_name(variant),
            )

            # Start periodic measurement
            self._write_command(CMD_START_PERIODIC_MEASUREMENT)
            time.sleep(0.05)  # Give the sensor time to produce the first reading
            self._state = SensorState.READY
            logger.info("scd4x: periodic measurement started (5s interval)")

        except OSError as exc:
            self._state = SensorState.DISCONNECTED
            raise SensorInitError(f"scd4x: failed to initialize: {exc}") from exc

    def _is_data_ready(self) -> bool:
        """Check if measurement data is ready to be read.

        Returns:
            True if data is ready, False otherwise.
        """
        try:
            self._write_command(CMD_GET_DATA_READY_STATUS)
            time.sleep(0.001)
            raw = self._require_bus().read(self._address, 3)
            # Data ready status is in the first byte (bit 0)
            return bool(raw[0] & 0x01)
        except (OSError, SensorReadError):
            return False

    def read(self) -> Iterator[Measurement]:
        """Read the latest measurement values.

        Yields:
            Measurement objects for CO2, temperature, and humidity.

        Raises:
            SensorReadError: If the read fails.
            SensorDisconnectedError: If the sensor is not connected.
        """
        if self._state is not SensorState.READY:
            raise SensorDisconnectedError("scd4x: sensor not ready")

        try:
            # Check if data is ready before reading
            if not self._is_data_ready():
                # Data not ready yet - this is normal during warm-up
                # Don't raise an error, just yield nothing
                return

            # Read measurement (9 bytes: 3 words + 3 CRCs)
            raw = self._require_bus().read(self._address, RESPONSE_SIZE_MEASUREMENT)

            # Parse CO2 (word 0)
            co2_raw = (raw[0] << 8) | raw[1]
            co2_crc = raw[2]
            if _crc8([raw[0], raw[1]]) != co2_crc:
                raise SensorReadError("scd4x: CO2 CRC mismatch")

            # Parse temperature (word 1)
            temp_raw = (raw[3] << 8) | raw[4]
            temp_crc = raw[5]
            if _crc8([raw[3], raw[4]]) != temp_crc:
                raise SensorReadError("scd4x: temperature CRC mismatch")

            # Parse humidity (word 2)
            rh_raw = (raw[6] << 8) | raw[7]
            rh_crc = raw[8]
            if _crc8([raw[6], raw[7]]) != rh_crc:
                raise SensorReadError("scd4x: humidity CRC mismatch")

            # Convert to engineering units
            co2_ppm = float(co2_raw)
            temp_c = _temp_to_celsius(temp_raw)
            rh_percent = _rh_to_percent(rh_raw)

            observed_at = datetime.now(datetime.now().astimezone().tzinfo)
            received_at = datetime.now(datetime.now().astimezone().tzinfo)
            self._last_read = observed_at

            # Yield measurements
            yield Measurement(
                sensor=SensorId.SCD4X,
                metric=MetricId.CO2_PPM,
                value=co2_ppm,
                unit="ppm",
                observed_at=observed_at,
                received_at=received_at,
            )
            yield Measurement(
                sensor=SensorId.SCD4X,
                metric=MetricId.TEMPERATURE_C,
                value=temp_c,
                unit="degC",
                observed_at=observed_at,
                received_at=received_at,
            )
            yield Measurement(
                sensor=SensorId.SCD4X,
                metric=MetricId.RELATIVE_HUMIDITY_PERCENT,
                value=rh_percent,
                unit="%",
                observed_at=observed_at,
                received_at=received_at,
            )

        except OSError as exc:
            # Don't set state to DISCONNECTED on read failure - the sensor might
            # just need a moment. The scheduler will retry.
            raise SensorDisconnectedError(f"scd4x: read failed: {exc}") from exc

    def _require_bus(self) -> I2CTransport:
        """Ensure the bus is available, raising if not."""
        if self._bus is None:
            raise SensorInitError("scd4x: I2C transport is not open")
        return self._bus

    def _write_command(self, command: int) -> None:
        """Write a 16-bit command with CRC to the sensor."""
        msb = (command >> 8) & 0xFF
        lsb = command & 0xFF
        crc = _crc8([msb, lsb])
        self._require_bus().write_block(self._address, [msb, lsb, crc])

    def _read_variant(self) -> int:
        """Read the sensor variant (SCD40/SCD41/SCD43)."""
        self._write_command(CMD_GET_SENSOR_VARIANT)
        time.sleep(0.001)  # Wait 1 ms command execution time
        raw = self._require_bus().read(self._address, RESPONSE_SIZE_VARIANT)
        variant = (raw[0] << 8) | raw[1]
        crc = raw[2]
        if _crc8([raw[0], raw[1]]) != crc:
            raise SensorReadError("scd4x: variant CRC mismatch")
        return variant

    @staticmethod
    def _variant_name(variant: int) -> str:
        """Convert variant code to human-readable name."""
        code = (variant >> 12) & 0x0F
        names = {0: "SCD40", 1: "SCD41", 5: "SCD43"}
        return names.get(code, f"Unknown({code})")

    def health(self) -> SensorHealth:
        """Return the current health status of the sensor."""
        return SensorHealth(
            sensor=SensorId.SCD4X,
            state=self._state,
            last_read_at=self._last_read,
        )

    def close(self) -> None:
        """Release the bus. Idempotent and never raises."""
        if self._closed:
            return
        self._closed = True
        bus = self._bus
        self._bus = None
        if bus is not None and self._owns_bus:
            with suppress(Exception):  # close must never raise
                bus.close()
