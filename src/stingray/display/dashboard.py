"""V1.8 TFT dashboard for the ILI9341 display.

Implements the air quality monitor dashboard with 13 regions,
severity classification, and dirty-region rendering.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .driver import ILI9341Driver
from .layout import LAYOUT
from .colors import COLORS, DisplayLevel
from .classification import classify_severity, get_overall_status
from .state import DisplayState
from .font import draw_text_5x7, draw_text_8x10


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
    
    def set_thresholds(self, pm25: float, pm10: float) -> None:
        """Set PM thresholds for severity classification."""
        self._pm25_threshold = pm25
        self._pm10_threshold = pm10
    
    def _draw_rounded_rect(self, x: int, y: int, width: int, height: int, color: int) -> None:
        """Draw a rounded rectangle with 5-pixel corners."""
        # Main body
        self.driver.fill_rectangle(x + 5, y, width - 10, height, color)
        # Top and bottom edges
        self.driver.fill_rectangle(x + 5, y, width - 10, 5, color)
        self.driver.fill_rectangle(x + 5, y + height - 5, width - 10, 5, color)
        # Left and right edges
        self.driver.fill_rectangle(x, y + 5, 5, height - 10, color)
        self.driver.fill_rectangle(x + width - 5, y + 5, 5, height - 10, color)
        # Corners (approximate with small squares)
        self.driver.fill_rectangle(x, y, 5, 5, color)
        self.driver.fill_rectangle(x + width - 5, y, 5, 5, color)
        self.driver.fill_rectangle(x, y + height - 5, 5, 5, color)
        self.driver.fill_rectangle(x + width - 5, y + height - 5, 5, 5, color)
    
    def _draw_thermometer_icon(self, x: int, y: int, color: int) -> None:
        """Draw a thermometer icon using orange primitives."""
        # Thermometer body
        self.driver.fill_rectangle(x + 2, y + 2, 4, 12, color)
        # Thermometer bulb
        self.driver.fill_rectangle(x, y + 14, 8, 6, color)
        # Thermometer top
        self.driver.fill_rectangle(x + 1, y, 6, 2, color)
    
    def _draw_droplet_icon(self, x: int, y: int, color: int) -> None:
        """Draw a water droplet icon using cyan primitives."""
        # Droplet top (pointed)
        self.driver.fill_rectangle(x + 3, y, 2, 2, color)
        self.driver.fill_rectangle(x + 2, y + 2, 4, 2, color)
        self.driver.fill_rectangle(x + 1, y + 4, 6, 2, color)
        # Droplet body (rounded)
        self.driver.fill_rectangle(x, y + 6, 8, 6, color)
        self.driver.fill_rectangle(x + 1, y + 12, 6, 2, color)
    
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
    
    def _format_value(self, value: Optional[float], decimals: int, unit: str = "") -> str:
        """Format a sensor value for display."""
        if value is None:
            return "--"
        if decimals == 0:
            return f"{int(value)}{unit}"
        return f"{value:.{decimals}f}{unit}"
    
    def _draw_header(self, state: DisplayState) -> None:
        """Draw the header region with date and time."""
        x, y, width, height = LAYOUT["header"]
        
        # Background
        self.driver.fill_rectangle(x, y, width, height, COLORS["navy"])
        
        # Date (left)
        date_text = state.date
        draw_text_8x10(x + 4, y + 5, date_text, COLORS["white"], 
                       lambda px, py, w, h, c: self.driver.fill_rectangle(px, py, w, h, c))
        
        # Time (right)
        time_text = state.time
        time_width = len(time_text) * 9
        draw_text_8x10(x + width - time_width - 4, y + 5, time_text, COLORS["white"],
                       lambda px, py, w, h, c: self.driver.fill_rectangle(px, py, w, h, c))
    
    def _draw_status(self, state: DisplayState) -> None:
        """Draw the status banner."""
        x, y, width, height = LAYOUT["status"]
        
        # Background based on overall status
        status_color = self._get_tile_color(state.overall_level)
        self.driver.fill_rectangle(x, y, width, height, status_color)
        
        # Status text
        text_color = self._get_text_color(state.overall_level)
        draw_text_8x10(x + 4, y + 5, state.status_text, text_color,
                       lambda px, py, w, h, c: self.driver.fill_rectangle(px, py, w, h, c))
    
    def _draw_sensor_tile(self, region: str, label: str, value: str, unit: str, 
                         level: DisplayLevel, icon: Optional[str] = None) -> None:
        """Draw a sensor tile with label, value, and unit."""
        x, y, width, height = LAYOUT[region]
        
        # Background
        tile_color = self._get_tile_color(level)
        self._draw_rounded_rect(x, y, width, height, tile_color)
        
        # Text color
        text_color = self._get_text_color(level)
        
        # Icon (if any)
        if icon == "thermometer":
            self._draw_thermometer_icon(x + 4, y + 4, COLORS["orange"])
        elif icon == "droplet":
            self._draw_droplet_icon(x + 4, y + 4, COLORS["cyan"])
        
        # Label (top)
        label_x = x + 16 if icon else x + 4
        draw_text_5x7(label_x, y + 4, label, text_color,
                      lambda px, py, w, h, c: self.driver.fill_rectangle(px, py, w, h, c))
        
        # Value (center)
        value_width = len(value) * 9
        value_x = x + (width - value_width) // 2
        draw_text_8x10(value_x, y + 18, value, text_color,
                       lambda px, py, w, h, c: self.driver.fill_rectangle(px, py, w, h, c))
        
        # Unit (bottom)
        if unit:
            unit_width = len(unit) * 6
            unit_x = x + (width - unit_width) // 2
            draw_text_5x7(unit_x, y + height - 10, unit, text_color,
                         lambda px, py, w, h, c: self.driver.fill_rectangle(px, py, w, h, c))
    
    def _draw_footer(self, state: DisplayState) -> None:
        """Draw the footer with hostname, WiFi RSSI, and measurement age."""
        x, y, width, height = LAYOUT["footer"]
        
        # Background
        self.driver.fill_rectangle(x, y, width, height, COLORS["navy"])
        
        # Hostname (left)
        draw_text_5x7(4, 221, self.hostname, COLORS["white"],
                     lambda px, py, w, h, c: self.driver.fill_rectangle(px, py, w, h, c))
        
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
        
        draw_text_5x7(114, 221, rssi_text, rssi_color,
                     lambda px, py, w, h, c: self.driver.fill_rectangle(px, py, w, h, c))
        
        # Measurement age (right)
        if state.measurement_age is not None:
            age_text = f"Age:{state.measurement_age}s"
            if state.measurement_age <= 10:
                age_color = COLORS["green"]
            elif state.measurement_age <= 30:
                age_color = COLORS["warning"]
            else:
                age_color = COLORS["bad"]
        else:
            age_text = "Updated --"
            age_color = COLORS["warning"]
        
        draw_text_5x7(232, 221, age_text, age_color,
                     lambda px, py, w, h, c: self.driver.fill_rectangle(px, py, w, h, c))
    
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
        
        # Render all regions
        self._draw_header(state)
        self._draw_status(state)
        
        # Sensor tiles
        self._draw_sensor_tile("pm25", "PM2.5", state.pm25, "µg/m³", state.pm25_level)
        self._draw_sensor_tile("co2", "CO2", state.co2, "ppm", state.co2_level)
        self._draw_sensor_tile("pm1", "PM1", state.pm1, "µg/m³", state.pm1_level)
        self._draw_sensor_tile("pm4", "PM4", state.pm4, "µg/m³", state.pm4_level)
        self._draw_sensor_tile("pm10", "PM10", state.pm10, "µg/m³", state.pm10_level)
        self._draw_sensor_tile("voc", "VOC", state.voc, "idx", state.voc_level)
        self._draw_sensor_tile("nox", "NOx", state.nox, "ppb", state.nox_level)
        self._draw_sensor_tile("temperature", "TEMP", state.temperature, "°C", 
                             DisplayLevel.NEUTRAL, icon="thermometer")
        self._draw_sensor_tile("humidity", "HUMID", state.humidity, "%", 
                             DisplayLevel.NEUTRAL, icon="droplet")
        self._draw_sensor_tile("tps", "TPS", state.tps, "kPa", DisplayLevel.NEUTRAL)
        
        self._draw_footer(state)
        
        # Update state cache
        self._last_state = state
        self._last_render_time = time.time()
    
    def start(self, get_snapshot: callable) -> None:
        """Start the display refresh loop."""
        # Full redraw on startup
        self.driver.fill_screen((0, 0, 0))
        
        while True:
            try:
                snapshot = get_snapshot()
                self.render(snapshot)
            except Exception as e:
                # Display failure should not interrupt sensor acquisition
                print(f"Display error: {e}")
            
            time.sleep(self._refresh_interval)
