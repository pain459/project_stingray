"""Color palette for the V1.8 display.

Original Adafruit ILI9341 palette in RGB565 format.
"""

from enum import IntEnum


class DisplayLevel(IntEnum):
    """Severity levels for air quality display."""
    NEUTRAL = 0
    GOOD = 1
    WARNING = 2
    BAD = 3


# RGB565 color values
BLACK = 0x0000
NAVY = 0x000F
DARK_GREEN = 0x03E0
YELLOW = 0xFFE0
RED = 0xF800
DARK_GREY = 0x7BEF
WHITE = 0xFFFF
LIGHT_GREY = 0xC618
GREEN = 0x07E0
ORANGE = 0xFD20
CYAN = 0x07FF
BLUE = 0x001F


def rgb565_to_rgb(r565: int) -> tuple[int, int, int]:
    """Convert RGB565 to RGB888."""
    r = (r565 >> 11) & 0x1F
    g = (r565 >> 5) & 0x3F
    b = r565 & 0x1F

    # Expand to 8-bit
    r8 = (r << 3) | (r >> 2)
    g8 = (g << 2) | (g >> 4)
    b8 = (b << 3) | (b >> 2)

    return (r8, g8, b8)


def get_severity_color(level: DisplayLevel) -> int:
    """Get the background color for a severity level."""
    colors = {
        DisplayLevel.NEUTRAL: DARK_GREY,
        DisplayLevel.GOOD: DARK_GREEN,
        DisplayLevel.WARNING: YELLOW,
        DisplayLevel.BAD: RED,
    }
    return colors[level]


def get_severity_text_color(level: DisplayLevel) -> int:
    """Get the text color for a severity level."""
    # Black text on yellow, white text on all others
    if level == DisplayLevel.WARNING:
        return BLACK
    return WHITE
