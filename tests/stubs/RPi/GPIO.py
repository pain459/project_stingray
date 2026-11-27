"""Test-only RPi.GPIO stub so display imports work on non-Pi CI hosts."""

BCM = "BCM"
BOARD = "BOARD"
IN = "IN"
OUT = "OUT"
HIGH = 1
LOW = 0


def setmode(mode: object) -> None:
    """Accept GPIO numbering mode."""


def setwarnings(flag: bool) -> None:
    """Accept warning configuration."""


def setup(pin: int, direction: object) -> None:
    """Accept pin direction configuration."""


def output(pin: int, state: int) -> None:
    """Accept pin output state."""


def cleanup() -> None:
    """Accept GPIO cleanup."""
