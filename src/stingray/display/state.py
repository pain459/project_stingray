"""Display state snapshot for the V1.8 dashboard.

Immutable snapshot of sensor data for display rendering.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DisplayState:
    """Immutable snapshot of sensor data for display."""
    
    # SPS30 metrics
    pm1: Optional[float] = None
    pm25: Optional[float] = None
    pm4: Optional[float] = None
    pm10: Optional[float] = None
    tps: Optional[float] = None
    sps30_online: bool = False
    sps30_has_reading: bool = False
    
    # SCD4x metrics
    co2: Optional[float] = None
    scd4x_online: bool = False
    scd4x_has_reading: bool = False
    
    # SHT45 metrics (authoritative for temp/humidity)
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    sht45_online: bool = False
    sht45_has_reading: bool = False
    
    # SGP41 metrics
    voc: Optional[float] = None
    nox: Optional[float] = None
    sgp41_online: bool = False
    sgp41_has_reading: bool = False
    
    # Configuration
    pm25_threshold: float = 35.0
    pm10_threshold: float = 50.0
    
    # System info
    hostname: str = "stingray.local"
    wifi_rssi: Optional[int] = None
    last_update_age_seconds: Optional[float] = None
