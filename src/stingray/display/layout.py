"""Display layout definitions for the V1.8 static dashboard.

Exact pixel coordinates from the specification.
"""

from dataclasses import dataclass


@dataclass
class Region:
    """A rectangular region on the display."""

    x: int
    y: int
    width: int
    height: int


# Display dimensions (landscape mode)
DISPLAY_WIDTH = 320
DISPLAY_HEIGHT = 240

# Region coordinates (x, y, width, height)
HEADER = Region(0, 0, 320, 26)
STATUS_BANNER = Region(0, 28, 320, 22)
PM25 = Region(4, 54, 154, 61)
CO2 = Region(162, 54, 154, 61)
PM1 = Region(4, 118, 60, 44)
PM4 = Region(67, 118, 60, 44)
PM10 = Region(130, 118, 60, 44)
VOC = Region(193, 118, 60, 44)
NOX = Region(256, 118, 60, 44)
TEMPERATURE = Region(4, 165, 102, 44)
HUMIDITY = Region(109, 165, 102, 44)
TPS = Region(214, 165, 102, 44)
FOOTER = Region(0, 212, 320, 28)

# All regions in drawing order
ALL_REGIONS = [
    HEADER,
    STATUS_BANNER,
    PM25,
    CO2,
    PM1,
    PM4,
    PM10,
    VOC,
    NOX,
    TEMPERATURE,
    HUMIDITY,
    TPS,
    FOOTER,
]

# Dictionary format for easy access by name
LAYOUT = {
    "header": HEADER,
    "status": STATUS_BANNER,
    "pm25": PM25,
    "co2": CO2,
    "pm1": PM1,
    "pm4": PM4,
    "pm10": PM10,
    "voc": VOC,
    "nox": NOX,
    "temperature": TEMPERATURE,
    "humidity": HUMIDITY,
    "tps": TPS,
    "footer": FOOTER,
}
