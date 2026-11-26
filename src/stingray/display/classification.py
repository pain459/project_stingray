"""Classification logic for air quality severity.

Implements the V1.8 classification rules.
"""

from typing import Optional

from .colors import DisplayLevel


def classify_severity(
    sensor_type: str,
    value: Optional[float],
    warning_threshold: float,
    critical_threshold: Optional[float] = None,
) -> DisplayLevel:
    """Classify a sensor value into a severity level.
    
    Args:
        sensor_type: Type of sensor (pm25, pm10, co2, voc, nox)
        value: Sensor reading (None if unavailable)
        warning_threshold: Value at which WARNING starts
        critical_threshold: Value at which BAD starts (optional)
    
    Returns:
        DisplayLevel (GOOD, WARNING, BAD, or NEUTRAL)
    """
    if value is None:
        return DisplayLevel.NEUTRAL
    
    if sensor_type in ("pm25", "pm10"):
        # PM sensors: warning at 75% of threshold, bad at threshold
        if value >= warning_threshold:
            return DisplayLevel.BAD
        if value >= 0.75 * warning_threshold:
            return DisplayLevel.WARNING
        return DisplayLevel.GOOD
    
    elif sensor_type == "co2":
        # CO2: warning at 800 ppm, bad at 1500 ppm
        if critical_threshold and value >= critical_threshold:
            return DisplayLevel.BAD
        if value >= warning_threshold:
            return DisplayLevel.WARNING
        return DisplayLevel.GOOD
    
    elif sensor_type == "voc":
        # VOC: warning above 100, bad above 250
        if critical_threshold and value > critical_threshold:
            return DisplayLevel.BAD
        if value > warning_threshold:
            return DisplayLevel.WARNING
        return DisplayLevel.GOOD
    
    elif sensor_type == "nox":
        # NOx: warning above 1, bad above 150
        if critical_threshold and value > critical_threshold:
            return DisplayLevel.BAD
        if value > warning_threshold:
            return DisplayLevel.WARNING
        return DisplayLevel.GOOD
    
    return DisplayLevel.NEUTRAL


def get_overall_status(
    pm25_level: DisplayLevel,
    pm10_level: DisplayLevel,
    co2_level: DisplayLevel,
    voc_level: DisplayLevel,
    nox_level: DisplayLevel,
    sensor_online: bool = True,
    sensor_warming: bool = False,
) -> DisplayLevel:
    """Calculate overall air quality status.
    
    Args:
        pm25_level: PM2.5 severity
        pm10_level: PM10 severity
        co2_level: CO2 severity
        voc_level: VOC severity
        nox_level: NOx severity
        sensor_online: Whether all sensors are online
        sensor_warming: Whether sensors are still warming up
    
    Returns:
        Overall DisplayLevel
    """
    # Sensor health takes precedence
    if not sensor_online:
        return DisplayLevel.BAD
    
    if sensor_warming:
        return DisplayLevel.WARNING
    
    # Use maximum severity
    levels = [pm25_level, pm10_level, co2_level, voc_level, nox_level]
    return max(levels)
