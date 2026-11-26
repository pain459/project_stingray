"""Classification logic for air quality severity.

Implements the V1.8 classification rules.
"""

from .colors import DisplayLevel


def classify_pm(value: float, threshold: float) -> DisplayLevel:
    """Classify PM value against threshold.
    
    GOOD: value < 0.75 * threshold
    WARNING: 0.75 * threshold <= value < threshold
    BAD: value >= threshold
    """
    if value >= threshold:
        return DisplayLevel.BAD
    if value >= 0.75 * threshold:
        return DisplayLevel.WARNING
    return DisplayLevel.GOOD


def classify_co2(value: float) -> DisplayLevel:
    """Classify CO2 value.
    
    GOOD: < 800 ppm
    WARNING: >= 800 and < 1500 ppm
    BAD: >= 1500 ppm
    """
    if value >= 1500:
        return DisplayLevel.BAD
    if value >= 800:
        return DisplayLevel.WARNING
    return DisplayLevel.GOOD


def classify_voc(value: float) -> DisplayLevel:
    """Classify VOC index.
    
    GOOD: <= 100
    WARNING: > 100 and <= 250
    BAD: > 250
    """
    if value > 250:
        return DisplayLevel.BAD
    if value > 100:
        return DisplayLevel.WARNING
    return DisplayLevel.GOOD


def classify_nox(value: float) -> DisplayLevel:
    """Classify NOx index.
    
    GOOD: <= 1
    WARNING: > 1 and <= 150
    BAD: > 150
    """
    if value > 150:
        return DisplayLevel.BAD
    if value > 1:
        return DisplayLevel.WARNING
    return DisplayLevel.GOOD


def overall_status(
    sps30_online: bool,
    scd4x_online: bool,
    sht45_online: bool,
    sgp41_online: bool,
    pm25: float | None,
    pm10: float | None,
    co2: float | None,
    voc: float | None,
    nox: float | None,
    pm25_threshold: float = 35.0,
    pm10_threshold: float = 50.0,
) -> tuple[DisplayLevel, str]:
    """Calculate overall air quality status.
    
    Returns:
        Tuple of (severity level, status message)
    """
    # Check 1: Any sensor offline
    if not (sps30_online and scd4x_online and sht45_online and sgp41_online):
        return DisplayLevel.BAD, "AIR QUALITY: SENSOR CHECK"

    # Check 2: Any required reading missing
    if not all([pm25 is not None, pm10 is not None, co2 is not None, voc is not None, nox is not None]):
        return DisplayLevel.WARNING, "AIR QUALITY: STARTING"

    # Check 3: Use maximum severity
    levels = [
        classify_pm(pm25, pm25_threshold),
        classify_pm(pm10, pm10_threshold),
        classify_co2(co2),
        classify_voc(voc),
        classify_nox(nox),
    ]
    level = max(levels)

    labels = {
        DisplayLevel.GOOD: "GOOD",
        DisplayLevel.WARNING: "ELEVATED",
        DisplayLevel.BAD: "POOR",
    }
    return level, f"AIR QUALITY: {labels[level]}"
