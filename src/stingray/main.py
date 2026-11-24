"""Stingray application entry point.

Migrated from the ESP32-S3 air-quality monitor to Raspberry Pi 4.
Runs in mock mode by default; hardware mode requires explicit config.
"""

from __future__ import annotations

import logging
import sys


def main() -> int:
    """Application entry point.

    Returns:
        Process exit code (0 = success).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("stingray")
    log.info("Stingray starting (mode not yet configured)")
    # Phase 04 implements the full scheduler, config loading and sensor
    # acquisition loop. This stub keeps the package importable and the
    # console entry point wired.
    return 0


if __name__ == "__main__":
    sys.exit(main())
