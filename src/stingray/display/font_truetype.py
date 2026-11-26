"""TrueType font rendering for the ILI9341 display using Pillow.

Provides anti-aliased text rendering with DejaVu Sans fonts, replacing
the bitmap fonts for improved readability and visual quality.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("stingray.display.font")

# Font directory - DejaVu Sans is installed via fonts-dejavu-core
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")

# Font file names
FONT_REGULAR = "DejaVuSans.ttf"
FONT_BOLD = "DejaVuSans-Bold.ttf"


@lru_cache(maxsize=32)
def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load and cache a TrueType font.

    Args:
        size: Font size in pixels.
        bold: Use bold variant if True.

    Returns:
        ImageFont.FreeTypeFont object.

    Raises:
        OSError: If the font file cannot be loaded.
    """
    filename = FONT_BOLD if bold else FONT_REGULAR
    font_path = FONT_DIR / filename

    if not font_path.exists():
        raise OSError(f"Font file not found: {font_path}")

    return ImageFont.truetype(str(font_path), size=size)


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Get a cached TrueType font.

    Args:
        size: Font size in pixels.
        bold: Use bold variant if True.

    Returns:
        ImageFont.FreeTypeFont object.
    """
    return _load_font(size, bold)


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    """Get the actual rendered size of text.

    Args:
        draw: ImageDraw object.
        text: Text string.
        font: Font object.

    Returns:
        Tuple of (width, height) in pixels.
    """
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return (right - left, bottom - top)


def draw_text(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int],
    align: str = "left",
) -> None:
    """Draw text at the specified position.

    Args:
        draw: ImageDraw object.
        x: X coordinate.
        y: Y coordinate.
        text: Text string.
        font: Font object.
        color: RGB color tuple.
        align: "left", "center", or "right".
    """
    if align == "center":
        # Center the text at the given x position
        width, _ = text_size(draw, text, font)
        x = x - width // 2
    elif align == "right":
        width, _ = text_size(draw, text, font)
        x = x - width

    draw.text((x, y), text, font=font, fill=color)


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    rect: tuple[int, int, int, int],
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int],
) -> None:
    """Draw text centered within a rectangle.

    Args:
        draw: ImageDraw object.
        text: Text string.
        rect: (x, y, width, height) tuple.
        font: Font object.
        color: RGB color tuple.
    """
    x, y, w, h = rect
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_width = right - left
    text_height = bottom - top

    # Center within the rectangle
    tx = x + (w - text_width) // 2 - left
    ty = y + (h - text_height) // 2 - top

    draw.text((tx, ty), text, font=font, fill=color)


def draw_text_fitted(
    draw: ImageDraw.ImageDraw,
    text: str,
    rect: tuple[int, int, int, int],
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int],
    min_size: int = 8,
) -> None:
    """Draw text, reducing font size if it doesn't fit.

    Args:
        draw: ImageDraw object.
        text: Text string.
        rect: (x, y, width, height) tuple.
        font: Font object (initial size).
        color: RGB color tuple.
        min_size: Minimum font size to try.
    """
    x, y, w, h = rect

    # Try the initial font size
    current_font = font
    while True:
        text_width, text_height = text_size(draw, text, current_font)
        if text_width <= w and text_height <= h:
            break

        # Reduce font size
        current_size = current_font.size
        if current_size <= min_size:
            break
        current_font = get_font(current_size - 1, bold=current_font.getname()[1] == "Bold")

    draw_centered_text(draw, text, rect, current_font, color)


def rgb565_to_rgb(r565: int) -> tuple[int, int, int]:
    """Convert RGB565 to RGB888.

    Args:
        r565: RGB565 color value.

    Returns:
        RGB888 tuple.
    """
    r = (r565 >> 11) & 0x1F
    g = (r565 >> 5) & 0x3F
    b = r565 & 0x1F

    # Expand to 8-bit
    r8 = (r << 3) | (r >> 2)
    g8 = (g << 2) | (g >> 4)
    b8 = (b << 3) | (b >> 2)

    return (r8, g8, b8)


def rgb_to_rgb565(r: int, g: int, b: int) -> int:
    """Convert RGB888 to RGB565.

    Args:
        r: Red (0-255).
        g: Green (0-255).
        b: Blue (0-255).

    Returns:
        RGB565 color value.
    """
    r5 = (r >> 3) & 0x1F
    g6 = (g >> 2) & 0x3F
    b5 = (b >> 3) & 0x1F
    return (r5 << 11) | (g6 << 5) | b5


def render_region_to_rgb565(
    x: int,
    y: int,
    width: int,
    height: int,
    draw_func,
    background_rgb565: int,
) -> bytes:
    """Render a region to an RGB565 byte buffer.

    Args:
        x: X coordinate.
        y: Y coordinate.
        width: Region width.
        height: Region height.
        draw_func: Function that takes an ImageDraw object and draws content.
        background_rgb565: Background color in RGB565.

    Returns:
        RGB565 byte buffer (width * height * 2 bytes).
    """
    # Create an RGB image for the region
    img = Image.new("RGB", (width, height), rgb565_to_rgb(background_rgb565))
    draw = ImageDraw.Draw(img)

    # Draw content
    draw_func(draw)

    # Convert to RGB565
    pixels = img.load()
    buffer = bytearray()
    for row in range(height):
        for col in range(width):
            r, g, b = pixels[col, row]
            rgb565 = rgb_to_rgb565(r, g, b)
            buffer.append((rgb565 >> 8) & 0xFF)
            buffer.append(rgb565 & 0xFF)

    return bytes(buffer)
