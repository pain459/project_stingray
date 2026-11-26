"""Display state snapshot for the V1.8 dashboard.

Immutable snapshot of formatted sensor data for display rendering.
"""

from dataclasses import dataclass
from typing import Optional

from .colors import DisplayLevel


@dataclass(frozen=True)
class DisplayState:
    """Immutable snapshot of formatted sensor data for display."""
    
    # Header
    date: str = ""
    time: str = ""
    
    # Status
    status_text: str = "AIR QUALITY: --"
    overall_level: DisplayLevel = DisplayLevel.NEUTRAL
    
    # Sensor values (formatted strings)
    pm1: str = "--"
    pm25: str = "--"
    pm4: str = "--"
    pm10: str = "--"
    co2: str = "--"
    voc: str = "--"
    nox: str = "--"
    temperature: str = "--"
    humidity: str = "--"
    tps: str = "--"
    
    # Severity levels for each sensor
    pm1_level: DisplayLevel = DisplayLevel.NEUTRAL
    pm25_level: DisplayLevel = DisplayLevel.NEUTRAL
    pm4_level: DisplayLevel = DisplayLevel.NEUTRAL
    pm10_level: DisplayLevel = DisplayLevel.NEUTRAL
    co2_level: DisplayLevel = DisplayLevel.NEUTRAL
    voc_level: DisplayLevel = DisplayLevel.NEUTRAL
    nox_level: DisplayLevel = DisplayLevel.NEUTRAL
    
    # Footer
    wifi_rssi: Optional[int] = None
    measurement_age: Optional[int] = None
