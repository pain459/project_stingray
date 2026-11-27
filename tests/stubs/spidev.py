"""Test-only spidev stub so display imports work on non-Pi CI hosts."""


class SpiDev:
    """Minimal SPI device stand-in used only during unit tests."""

    def open(self, bus: int, device: int) -> None:
        """Accept bus and device selection."""

    def close(self) -> None:
        """Accept close requests."""

    def xfer3(self, data: bytes) -> list[int]:
        """Return an empty transfer result."""
        return []
