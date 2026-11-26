"""ILI9341 SPI display driver for stingray.

Handles low-level SPI communication and display initialization.
"""

import time
from typing import Optional

import spidev
import RPi.GPIO as GPIO


class ILI9341Driver:
    """Low-level driver for the ILI9341 2.8-inch TFT display."""

    def __init__(
        self,
        spi_bus: int = 0,
        spi_cs: int = 0,
        dc_pin: int = 25,
        reset_pin: int = 24,
        max_speed_hz: int = 10_000_000,
    ):
        self.spi_bus = spi_bus
        self.spi_cs = spi_cs
        self.dc_pin = dc_pin
        self.reset_pin = reset_pin
        self.max_speed_hz = max_speed_hz

        self._spi: Optional[spidev.SpiDev] = None
        self._initialized = False

    def open(self) -> None:
        """Open the display and initialize it."""
        self._spi = spidev.SpiDev()
        self._spi.open(self.spi_bus, self.spi_cs)
        self._spi.max_speed_hz = self.max_speed_hz
        self._spi.mode = 0

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.dc_pin, GPIO.OUT)
        GPIO.setup(self.reset_pin, GPIO.OUT)

        self._initialize()
        self._initialized = True

    def close(self) -> None:
        """Close the display and clean up GPIO."""
        if self._spi is not None:
            self._spi.close()
            self._spi = None
        GPIO.cleanup()
        self._initialized = False

    def _spi_write(self, data: bytes) -> None:
        """Write data to the display via SPI."""
        if self._spi is None:
            raise RuntimeError("Display not initialized")
        self._spi.xfer3(data)

    def _write_cmd(self, cmd: int) -> None:
        """Write a command to the display."""
        GPIO.output(self.dc_pin, GPIO.LOW)
        self._spi_write(bytes([cmd]))

    def _write_data(self, data: bytes) -> None:
        """Write data to the display."""
        GPIO.output(self.dc_pin, GPIO.HIGH)
        self._spi_write(data)

    def _hardware_reset(self) -> None:
        """Perform hardware reset."""
        GPIO.output(self.reset_pin, GPIO.HIGH)
        time.sleep(0.050)
        GPIO.output(self.reset_pin, GPIO.LOW)
        time.sleep(0.050)
        GPIO.output(self.reset_pin, GPIO.HIGH)
        time.sleep(0.200)

    def _initialize(self) -> None:
        """Initialize the ILI9341 display in landscape mode (320x240)."""
        # Hardware reset
        self._hardware_reset()

        # Software reset
        self._write_cmd(0x01)
        time.sleep(0.050)

        # Exit sleep mode
        self._write_cmd(0x11)
        time.sleep(0.500)

        # Display off
        self._write_cmd(0x28)

        # Memory access mode - landscape mode (320x240)
        # MADCTL 0x68 = Row/Column address swap + X-axis mirror
        self._write_cmd(0x36)
        self._write_data(bytes([0x68]))

        # Pixel format 16-bit
        self._write_cmd(0x3A)
        self._write_data(bytes([0x05]))

        # Set address window to full screen (320x240)
        self._write_cmd(0x2A)
        self._write_data(bytes([0x00, 0x00, 0x01, 0x3F]))  # 320 wide
        self._write_cmd(0x2B)
        self._write_data(bytes([0x00, 0x00, 0x00, 0xEF]))  # 240 tall

        # Display on
        self._write_cmd(0x29)
        time.sleep(0.100)

    def set_address_window(self, x: int, y: int, width: int, height: int) -> None:
        """Set the address window for drawing."""
        self._write_cmd(0x2A)
        self._write_data(bytes([
            (x >> 8) & 0xFF, x & 0xFF,
            ((x + width - 1) >> 8) & 0xFF, (x + width - 1) & 0xFF
        ]))
        self._write_cmd(0x2B)
        self._write_data(bytes([
            (y >> 8) & 0xFF, y & 0xFF,
            ((y + height - 1) >> 8) & 0xFF, (y + height - 1) & 0xFF
        ]))

    def fill_rectangle(self, x: int, y: int, width: int, height: int, color: tuple[int, int, int]) -> None:
        """Fill a rectangle with a color."""
        self.set_address_window(x, y, width, height)
        self._write_cmd(0x2C)

        # Convert RGB to RGB565
        r, g, b = color
        color16 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        high = (color16 >> 8) & 0xFF
        low = color16 & 0xFF

        data = bytearray()
        for _ in range(width * height):
            data.append(high)
            data.append(low)
        self._write_data(bytes(data))

    def fill_screen(self, color: tuple[int, int, int]) -> None:
        """Fill the entire screen with a color."""
        self.fill_rectangle(0, 0, 320, 240, color)
