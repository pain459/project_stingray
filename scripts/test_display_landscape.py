#!/usr/bin/env python3
"""Test the ILI9341 display in landscape mode (320x240)."""

import sys
import time

sys.path.insert(0, "/opt/stingray/current/src")

from stingray.display.driver import ILI9341Driver
from stingray.display.colors import (
    BLACK, WHITE, RED, GREEN, BLUE, YELLOW, CYAN, ORANGE,
    rgb565_to_rgb
)


def main():
    print("Initializing display in landscape mode (320x240)...")
    driver = ILI9341Driver(
        spi_bus=0,
        spi_cs=0,
        dc_pin=25,
        reset_pin=24,
        max_speed_hz=10_000_000
    )
    driver.open()

    print("Display initialized. Testing colors...")

    # Test 1: Fill with black
    print("Filling with BLACK...")
    driver.fill_screen(rgb565_to_rgb(BLACK))
    time.sleep(2)

    # Test 2: Fill with white
    print("Filling with WHITE...")
    driver.fill_screen(rgb565_to_rgb(WHITE))
    time.sleep(2)

    # Test 3: Fill with red
    print("Filling with RED...")
    driver.fill_screen(rgb565_to_rgb(RED))
    time.sleep(2)

    # Test 4: Fill with green
    print("Filling with GREEN...")
    driver.fill_screen(rgb565_to_rgb(GREEN))
    time.sleep(2)

    # Test 5: Fill with blue
    print("Filling with BLUE...")
    driver.fill_screen(rgb565_to_rgb(BLUE))
    time.sleep(2)

    # Test 6: Draw test pattern with rectangles
    print("Drawing test pattern...")
    driver.fill_screen(rgb565_to_rgb(BLACK))
    time.sleep(0.5)
    
    # Draw colored rectangles in a grid
    colors = [RED, GREEN, BLUE, YELLOW, CYAN, ORANGE, WHITE, BLACK]
    for i, color in enumerate(colors):
        x = (i % 4) * 80
        y = (i // 4) * 120
        driver.fill_rectangle(x, y, 78, 118, rgb565_to_rgb(color))
    
    time.sleep(5)

    print("Test complete. Display should show a 4x2 grid of colored rectangles.")
    driver.close()


if __name__ == "__main__":
    main()
