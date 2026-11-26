"""ILI9341 2.8-inch TFT display driver for stingray.

Shows live sensor data (PM2.5, CO2, temperature, humidity) on the LCD.
"""

import time
from typing import Optional

import spidev
import RPi.GPIO as GPIO


class ILI9341Display:
    """Driver for the 2.8-inch ILI9341 TFT display."""

    WIDTH = 240
    HEIGHT = 320

    def __init__(
        self,
        cs_pin: int = 8,
        dc_pin: int = 25,
        reset_pin: int = 24,
        spi_bus: int = 0,
        spi_cs: int = 0,
        max_speed_hz: int = 40_000_000,
    ):
        self.cs_pin = cs_pin
        self.dc_pin = dc_pin
        self.reset_pin = reset_pin
        self.spi_bus = spi_bus
        self.spi_cs = spi_cs
        self.max_speed_hz = max_speed_hz

        self._spi: Optional[spidev.SpiDev] = None
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
        """Initialize the ILI9341 display."""
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

        # Memory access mode
        self._write_cmd(0x36)
        self._write_data(bytes([0x08]))

        # Pixel format 16-bit
        self._write_cmd(0x3A)
        self._write_data(bytes([0x05]))

        # Set address window to full screen
        self._write_cmd(0x2A)
        self._write_data(bytes([0x00, 0x00, 0x00, 0xEF]))
        self._write_cmd(0x2B)
        self._write_data(bytes([0x00, 0x00, 0x01, 0x3F]))

        # Display on
        self._write_cmd(0x29)
        time.sleep(0.100)

        self._initialized = True

    def open(self) -> None:
        """Open the display and initialize it."""
        self._spi = spidev.SpiDev()
        self._spi.open(self.spi_bus, self.spi_cs)
        self._spi.max_speed_hz = self.max_speed_hz
        self._spi.mode = 0

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.cs_pin, GPIO.OUT)
        GPIO.setup(self.dc_pin, GPIO.OUT)
        GPIO.setup(self.reset_pin, GPIO.OUT)

        self._initialize()

    def close(self) -> None:
        """Close the display and clean up GPIO."""
        if self._spi is not None:
            self._spi.close()
            self._spi = None
        GPIO.cleanup()
        self._initialized = False

    def fill(self, r: int, g: int, b: int) -> None:
        """Fill the entire screen with a color."""
        if not self._initialized:
            raise RuntimeError("Display not initialized")

        self._write_cmd(0x2C)
        color16 = (r << 11) | (g << 5) | b
        high = (color16 >> 8) & 0xFF
        low = color16 & 0xFF

        data = bytearray()
        for _ in range(self.WIDTH * self.HEIGHT):
            data.append(high)
            data.append(low)
        self._write_data(bytes(data))

    def _rgb565(self, r: int, g: int, b: int) -> bytes:
        """Convert RGB to RGB565 bytes."""
        color16 = (r << 11) | (g << 5) | b
        return bytes([(color16 >> 8) & 0xFF, color16 & 0xFF])

    def _draw_text_simple(self, x: int, y: int, text: str, color: tuple[int, int, int]) -> None:
        """Draw simple text using a basic 5x7 font (placeholder)."""
        # This is a simplified implementation - for production, use a proper font
        # For now, we'll just draw a colored rectangle as a placeholder
        pass

    def show_sensor_data(
        self,
        pm25: Optional[float] = None,
        pm10: Optional[float] = None,
        co2: Optional[float] = None,
        temp: Optional[float] = None,
        humidity: Optional[float] = None,
    ) -> None:
        """Display live sensor data on the LCD."""
        if not self._initialized:
            raise RuntimeError("Display not initialized")

        # Background: dark blue
        self.fill(0, 0, 50)

        # For now, just show a simple status indicator
        # In production, we'll render actual text with a proper font
        self._draw_status_indicator(pm25, co2, temp, humidity)

    def _draw_status_indicator(
        self,
        pm25: Optional[float],
        co2: Optional[float],
        temp: Optional[float],
        humidity: Optional[float],
    ) -> None:
        """Draw a simple status indicator showing sensor data availability."""
        # Draw colored squares for each sensor
        # PM2.5 - top left
        if pm25 is not None:
            self._draw_square(10, 10, 20, 20, (255, 0, 0))
        else:
            self._draw_square(10, 10, 20, 20, (50, 50, 50))

        # CO2 - top right
        if co2 is not None:
            self._draw_square(self.WIDTH - 30, 10, 20, 20, (0, 255, 0))
        else:
            self._draw_square(self.WIDTH - 30, 10, 20, 20, (50, 50, 50))

        # Temperature - middle left
        if temp is not None:
            self._draw_square(10, 40, 20, 20, (0, 0, 255))
        else:
            self._draw_square(10, 40, 20, 20, (50, 50, 50))

        # Humidity - middle right
        if humidity is not None:
            self._draw_square(self.WIDTH - 30, 40, 20, 20, (255, 255, 0))
        else:
            self._draw_square(self.WIDTH - 30, 40, 20, 20, (50, 50, 50))

    def _draw_square(self, x: int, y: int, width: int, height: int, color: tuple[int, int, int]) -> None:
        """Draw a filled square at the given position."""
        if not self._initialized:
            raise RuntimeError("Display not initialized")

        # Set address window
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

        # Fill with color
        self._write_cmd(0x2C)
        color_bytes = self._rgb565(*color)
        data = color_bytes * (width * height)
        self._write_data(data)
