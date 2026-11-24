"""Typed error hierarchy for sensor and application failures.

Drivers raise these so the scheduler can classify failures without parsing
exception messages. All are catchable as :class:`SensorError`.
"""

from __future__ import annotations


class SensorError(Exception):
    """Base class for all sensor-layer errors."""


class SensorInitError(SensorError):
    """Raised by ``initialize()`` when the sensor cannot be brought up."""


class SensorReadError(SensorError):
    """Raised by ``read()`` when a read attempt fails (timeout, CRC, bus)."""


class SensorDisconnectedError(SensorReadError):
    """Raised when the sensor is no longer present on the bus."""


class SensorStaleError(SensorReadError):
    """Raised when the sensor reports data that is not fresh enough."""


class ConfigError(Exception):
    """Raised when configuration is invalid or incompatible."""
