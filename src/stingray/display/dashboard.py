"""V1.8 TFT dashboard for the ILI9341 display.

Implements the air quality monitor dashboard with 13 regions,
severity classification, and anti-aliased text rendering using Pillow.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw

from .driver import ILI9341Driver
from .layout import LAYOUT
from .colors import COLORS, DisplayLevel, rgb565_to_rgb
from .classification import classify_severity, get_overall_status
from .state import DisplayState
from .font_truetype import (
    get_font,
    draw_centered_text,
    draw_text_fitted,
    render_region_to_rgb565,
)

log = logging.getLogger("stingray.display.dashboard")


@dataclass
class SensorSnapshot:
    """Immutable snapshot of sensor data for display."""
    timestamp: float
    pm1: Optional[float] = None
    pm25: Optional[float] = None
    pm4: Optional[float] = None
    pm10: Optional[float] = None
    co2: Optional[float] = None
    voc: Optional[float] = None
    nox: Optional[float] = None
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    tps: Optional[float] = None
    sensor_online: bool = True
    sensor_warming: bool = False


class DisplayDashboard:
    """V1.8 air quality dashboard for the ILI9341 display."""

    def __init__(self, driver: ILI9341Driver, hostname: str = "stingray"):
        self.driver = driver
        self.hostname = hostname
        self._last_state: Optional[DisplayState] = None
        self._last_render_time: float = 0.0
        self._refresh_interval: float = 1.0  # 1 second

        # Configuration
        self._pm25_threshold: float = 35.0
        self._pm10_threshold: float = 55.0
        self._co2_warning: float = 800.0
        self._co2_critical: float = 1500.0
        self._voc_warning: float = 100.0
        self._voc_critical: float = 250.0
        self._nox_warning: float = 1.0
        self._nox_critical: float = 150.0

        # Font cache
        self._fonts: dict[tuple[int, bool], object] = {}

    def set_thresholds(self, pm25: float, pm10: float) -> None:
        """Set PM thresholds for severity classification."""
        self._pm25_threshold = pm25
        self._pm10_threshold = pm10

    def _get_font(self, size: int, bold: bool = False):
        """Get a cached font."""
        key = (size, bold)
        if key not in self._fonts:
            self._fonts[key] = get_font(size, bold)
        return self._fonts[key]

    def _get_tile_color(self, level: DisplayLevel) -> int:
        """Get the RGB565 color for a severity level."""
        color_map = {
            DisplayLevel.GOOD: COLORS["good"],
            DisplayLevel.WARNING: COLORS["warning"],
            DisplayLevel.BAD: COLORS["bad"],
            DisplayLevel.NEUTRAL: COLORS["neutral"],
        }
        return color_map.get(level, COLORS["neutral"])

    def _get_text_color(self, level: DisplayLevel) -> int:
        """Get text color based on tile background."""
        # Yellow tiles use black text, all others use white
        if level == DisplayLevel.WARNING:
            return COLORS["black"]
        return COLORS["white"]

    def _format_value(self, value: Optional[float], decimals: int) -> str:
        """Format a sensor value for display."""
        if value is None:
            return "--"
        if decimals == 0:
            return f"{int(value)}"
        return f"{value:.{decimals}f}"

    def _draw_rounded_rect(self, draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int, color: tuple[int, int, int]) -> None:
        """Draw a rounded rectangle with 5-pixel corners."""
        radius = 5
        # Main body
        draw.rectangle([x + radius, y, x + width - radius, y + height], fill=color)
        # Left and right edges
        draw.rectangle([x, y + radius, x + radius, y + height - radius], fill=color)
        draw.rectangle([x + width - radius, y + radius, x + width, y + height - radius], fill=color)
        # Corners (approximate with circles)
        draw.ellipse([x, y, x + 2 * radius, y + 2 * radius], fill=color)
        draw.ellipse([x + width - 2 * radius, y, x + width, y + 2 * radius], fill=color)
        draw.ellipse([x, y + height - 2 * radius, x + 2 * radius, y + height], fill=color)
        draw.ellipse([x + width - 2 * radius, y + height - 2 * radius, x + width, y + height], fill=color)

    def _draw_thermometer_icon(self, draw: ImageDraw.ImageDraw, x: int, y: int, color: tuple[int, int, int]) -> None:
        """Draw a thermometer icon using orange primitives."""
        # Thermometer body
        draw.rectangle([x + 2, y + 2, x + 6, y + 14], fill=color)
        # Thermometer bulb
        draw.ellipse([x, y + 12, x + 8, y + 20], fill=color)
        # Thermometer top
        draw.rectangle([x + 1, y, x + 7, y + 2], fill=color)

    def _draw_droplet_icon(self, draw: ImageDraw.ImageDraw, x: int, y: int, color: tuple[int, int, int]) -> None:
        """Draw a water droplet icon using cyan primitives."""
        # Droplet top (pointed)
        draw.polygon([(x + 4, y), (x + 2, y + 4), (x + 6, y + 4)], fill=color)
        # Droplet body (rounded)
        draw.ellipse([x, y + 4, x + 8, y + 14], fill=color)
        draw.rectangle([x + 1, y + 10, x + 7, y + 14], fill=color)

    def _render_header(self, state: DisplayState) -> bytes:
        """Render the header region and return RGB565 buffer."""
        region = LAYOUT["header"]
        x, y, width, height = region.x, region.y, region.width, region.height

        def draw_func(draw: ImageDraw.ImageDraw) -> None:
            # Background
            draw.rectangle([0, 0, width, height], fill=rgb565_to_rgb(COLORS["navy"]))

            # Date (left)
            font = self._get_font(15, bold=True)
            draw.text((4, (height - 15) // 2), state.date, font=font, fill=rgb565_to_rgb(COLORS["white"]))

            # Time (right)
            time_text = state.time
            bbox = draw.textbbox((0, 0), time_text, font=font)
            text_width = bbox[2] - bbox[0]
            draw.text((width - text_width - 4, (height - 15) // 2), time_text, font=font, fill=rgb565_to_rgb(COLORS["white"]))

        return render_region_to_rgb565(x, y, width, height, draw_func, COLORS["navy"])

    def _render_status(self, state: DisplayState) -> bytes:
        """Render the status banner and return RGB565 buffer."""
        region = LAYOUT["status"]
        x, y, width, height = region.x, region.y, region.width, region.height

        status_color = self._get_tile_color(state.overall_level)
        text_color = self._get_text_color(state.overall_level)

        def draw_func(draw: ImageDraw.ImageDraw) -> None:
            # Background
            draw.rectangle([0, 0, width, height], fill=rgb565_to_rgb(status_color))

            # Status text (centered)
            font = self._get_font(15, bold=True)
            draw_centered_text(draw, state.status_text, (0, 0, width, height), font, rgb565_to_rgb(text_color))

        return render_region_to_rgb565(x, y, width, height, draw_func, status_color)

    def _render_sensor_tile(self, region_name: str, label: str, value: str, unit: str,
                           level: DisplayLevel, icon: Optional[str] = None) -> bytes:
        """Render a sensor tile and return RGB565 buffer."""
        r = LAYOUT[region_name]
        x, y, width, height = r.x, r.y, r.width, r.height

        tile_color = self._get_tile_color(level)
        text_color = self._get_text_color(level)

        def draw_func(draw: ImageDraw.ImageDraw) -> None:
            # Background
            self._draw_rounded_rect(draw, 0, 0, width, height, rgb565_to_rgb(tile_color))

            # Icon (if any)
            if icon == "thermometer":
                self._draw_thermometer_icon(draw, 4, 4, rgb565_to_rgb(COLORS["orange"]))
            elif icon == "droplet":
                self._draw_droplet_icon(draw, 4, 4, rgb565_to_rgb(COLORS["cyan"]))

            # Determine font sizes based on tile type
            is_primary = region_name in ("pm25", "co2")
            is_environmental = region_name in ("temperature", "humidity", "tps")

            if is_primary:
                label_font = self._get_font(12, bold=True)
                value_font = self._get_font(29, bold=True)
                unit_font = self._get_font(10)
            elif is_environmental:
                label_font = self._get_font(10, bold=True)
                value_font = self._get_font(21, bold=True)
                unit_font = self._get_font(11)
            else:
                label_font = self._get_font(10, bold=True)
                value_font = self._get_font(19, bold=True)
                unit_font = self._get_font(9)

            # Label (top, centered)
            label_x = 16 if icon else 0
            label_rect = (label_x, 2, width - label_x, 14)
            draw_centered_text(draw, label, label_rect, label_font, rgb565_to_rgb(text_color))

            # Value (center, centered)
            value_rect = (0, 16, width, height - 28)
            draw_text_fitted(draw, value, value_rect, value_font, rgb565_to_rgb(text_color))

            # Unit (bottom, centered)
            if unit:
                unit_rect = (0, height - 12, width, 10)
                draw_centered_text(draw, unit, unit_rect, unit_font, rgb565_to_rgb(text_color))

        return render_region_to_rgb565(x, y, width, height, draw_func, tile_color)

    def _render_footer(self, state: DisplayState) -> bytes:
        """Render the footer and return RGB565 buffer."""
        region = LAYOUT["footer"]
        x, y, width, height = region.x, region.y, region.width, region.height

        def draw_func(draw: ImageDraw.ImageDraw) -> None:
            # Background
            draw.rectangle([0, 0, width, height], fill=rgb565_to_rgb(COLORS["navy"]))

            font = self._get_font(10, bold=True)

            # Hostname (left)
            draw.text((4, (height - 10) // 2), self.hostname, font=font, fill=rgb565_to_rgb(COLORS["white"]))

            # WiFi RSSI (center)
            if state.wifi_rssi is not None:
                rssi_text = f"RSSI:{state.wifi_rssi}"
                if state.wifi_rssi >= -67:
                    rssi_color = COLORS["green"]
                elif state.wifi_rssi >= -75:
                    rssi_color = COLORS["warning"]
                else:
                    rssi_color = COLORS["bad"]
            else:
                rssi_text = "WiFi OFF"
                rssi_color = COLORS["bad"]

            rssi_rect = (114, 0, 100, height)
            draw_centered_text(draw, rssi_text, rssi_rect, font, rgb565_to_rgb(rssi_color))

            # Measurement age (right)
            if state.measurement_age is not None:
                age_text = f"Updated {state.measurement_age}s"
                if state.measurement_age <= 10:
                    age_color = COLORS["green"]
                elif state.measurement_age <= 30:
                    age_color = COLORS["warning"]
                else:
                    age_color = COLORS["bad"]
            else:
                age_text = "Updated --"
                age_color = COLORS["warning"]

            age_rect = (232, 0, width - 232, height)
            draw_centered_text(draw, age_text, age_rect, font, rgb565_to_rgb(age_color))

        return render_region_to_rgb565(x, y, width, height, draw_func, COLORS["navy"])

    def _build_state(self, snapshot: SensorSnapshot) -> DisplayState:
        """Build a display state from a sensor snapshot."""
        now = datetime.now()

        # Calculate measurement age
        measurement_age = None
        if snapshot.timestamp > 0:
            measurement_age = int(time.time() - snapshot.timestamp)

        # Classify severities
        pm25_level = classify_severity("pm25", snapshot.pm25, self._pm25_threshold)
        pm10_level = classify_severity("pm10", snapshot.pm10, self._pm10_threshold)
        pm1_level = pm25_level if snapshot.pm1 is not None else DisplayLevel.NEUTRAL
        pm4_level = pm25_level if snapshot.pm4 is not None else DisplayLevel.NEUTRAL

        co2_level = classify_severity("co2", snapshot.co2, self._co2_warning, self._co2_critical)
        voc_level = classify_severity("voc", snapshot.voc, self._voc_warning, self._voc_critical)
        nox_level = classify_severity("nox", snapshot.nox, self._nox_warning, self._nox_critical)

        # Overall status
        overall_level = get_overall_status(
            pm25_level, pm10_level, co2_level, voc_level, nox_level,
            snapshot.sensor_online, snapshot.sensor_warming
        )

        # Format values
        pm1_text = self._format_value(snapshot.pm1, 1)
        pm25_text = self._format_value(snapshot.pm25, 1)
        pm4_text = self._format_value(snapshot.pm4, 1)
        pm10_text = self._format_value(snapshot.pm10, 1)
        co2_text = self._format_value(snapshot.co2, 0)
        voc_text = self._format_value(snapshot.voc, 0)
        nox_text = self._format_value(snapshot.nox, 0)
        temp_text = self._format_value(snapshot.temperature, 1)
        humidity_text = self._format_value(snapshot.humidity, 0)
        tps_text = self._format_value(snapshot.tps, 2)

        return DisplayState(
            date=now.strftime("%a %d %b"),
            time=now.strftime("%H:%M"),
            status_text=f"AIR QUALITY: {overall_level.name}",
            overall_level=overall_level,
            pm1=pm1_text,
            pm25=pm25_text,
            pm4=pm4_text,
            pm10=pm10_text,
            co2=co2_text,
            voc=voc_text,
            nox=nox_text,
            temperature=temp_text,
            humidity=humidity_text,
            tps=tps_text,
            pm1_level=pm1_level,
            pm25_level=pm25_level,
            pm4_level=pm4_level,
            pm10_level=pm10_level,
            co2_level=co2_level,
            voc_level=voc_level,
            nox_level=nox_level,
            wifi_rssi=None,  # TODO: Get from WiFi
            measurement_age=measurement_age,
        )

    def render(self, snapshot: SensorSnapshot) -> None:
        """Render the dashboard with the given sensor snapshot."""
        state = self._build_state(snapshot)

        # Check if anything changed
        if self._last_state is not None and self._last_state == state:
            return  # No changes, skip rendering

        # Render all regions and send to display
        self._send_region("header", self._render_header(state))
        self._send_region("status", self._render_status(state))

        # Sensor tiles
        self._send_region("pm25", self._render_sensor_tile("pm25", "PM2.5", state.pm25, "µg/m³", state.pm25_level))
        self._send_region("co2", self._render_sensor_tile("co2", "CO2", state.co2, "ppm", state.co2_level))
        self._send_region("pm1", self._render_sensor_tile("pm1", "PM1", state.pm1, "µg/m³", state.pm1_level))
        self._send_region("pm4", self._render_sensor_tile("pm4", "PM4", state.pm4, "µg/m³", state.pm4_level))
        self._send_region("pm10", self._render_sensor_tile("pm10", "PM10", state.pm10, "µg/m³", state.pm10_level))
        self._send_region("voc", self._render_sensor_tile("voc", "VOC", state.voc, "index", state.voc_level))
        self._send_region("nox", self._render_sensor_tile("nox", "NOx", state.nox, "index", state.nox_level))
        self._send_region("temperature", self._render_sensor_tile("temperature", "TEMP", state.temperature, "°C",
                                                                 DisplayLevel.NEUTRAL, icon="thermometer"))
        self._send_region("humidity", self._render_sensor_tile("humidity", "HUMID", state.humidity, "%",
                                                              DisplayLevel.NEUTRAL, icon="droplet"))
        self._send_region("tps", self._render_sensor_tile("tps", "TPS", state.tps, "kPa", DisplayLevel.NEUTRAL))

        self._send_region("footer", self._render_footer(state))

        # Update state cache
        self._last_state = state
        self._last_render_time = time.time()

    def _send_region(self, region_name: str, rgb565_buffer: bytes) -> None:
        """Send a rendered region to the display."""
        region = LAYOUT[region_name]
        x, y, width, height = region.x, region.y, region.width, region.height

        # Set address window and send data
        self.driver.set_address_window(x, y, width, height)
        self.driver._write_cmd(0x2C)
        self.driver._write_data(rgb565_buffer)

    def start(self, get_snapshot: callable) -> None:
        """Start the display refresh loop."""
        # Full redraw on startup
        self.driver.fill_screen(0x0000)

        while True:
            try:
                snapshot = get_snapshot()
                self.render(snapshot)
            except Exception as e:
                # Display failure should not interrupt sensor acquisition
                log.warning("Display error: %s", e)

            time.sleep(self._refresh_interval)
