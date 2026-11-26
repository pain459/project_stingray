"""I2C transport abstraction for hardware sensor drivers.

Drivers depend on :class:`I2CTransport` rather than on ``smbus2`` directly.
That keeps the sensor package importable without hardware libraries installed
(so mock mode and CI on non-Pi machines work) and lets drivers be unit-tested
against a fake bus.

``smbus2`` is declared in the ``hardware`` extra of ``pyproject.toml``::

    uv sync --extra hardware
"""

from __future__ import annotations

import errno
from contextlib import suppress
from typing import Any, Protocol

from stingray.core.errors import SensorInitError

#: ``errno`` values that mean "nobody answered" or "no such device" rather
#: than "the device answered with something malformed".
_DISCONNECT_ERRNOS: frozenset[int] = frozenset(
    {
        errno.EREMOTEIO,  # 121 - NACK from the addressed device
        errno.EIO,  # 5 - generic bus I/O failure
        errno.ENXIO,  # 6 - no such device or address
        errno.ENODEV,  # 19 - no such device
        errno.EBADF,  # 9 - bus handle already closed
        errno.ENOENT,  # 2 - /dev/i2c-N missing
    }
)


class I2CTransport(Protocol):
    """The bus operations the sensor drivers need.

    Deliberately tiny: a command byte write plus a raw read. Sensors that need
    write-then-read in one transaction can compose these two calls.
    """

    def write_byte(self, address: int, value: int) -> None:
        """Send a single command byte to ``address``."""

    def write_block(self, address: int, data: list[int]) -> None:
        """Write multiple bytes to ``address`` in a single I2C transaction."""

    def read(self, address: int, length: int) -> list[int]:
        """Read ``length`` bytes from ``address`` without writing first."""

    def close(self) -> None:
        """Release the bus. Must be idempotent and must never raise."""


def is_disconnect_error(exc: BaseException) -> bool:
    """True when an ``OSError`` means the device is absent.

    Distinguishing this from a malformed response matters: an absent device
    should surface as ``DISCONNECTED`` and recover on its own once wired,
    whereas a bad response is a read failure.
    """
    if not isinstance(exc, OSError):
        return False
    return exc.errno in _DISCONNECT_ERRNOS


class Smbus2Transport:
    """I2C transport backed by ``smbus2`` (the Raspberry Pi implementation).

    Raises:
        SensorInitError: if ``smbus2`` is not installed, or if the bus device
            node cannot be opened. Both are deployment errors that deserve a
            fail-fast message rather than a silent retry loop.
    """

    def __init__(self, bus_number: int = 1) -> None:
        self._bus_number = bus_number
        self._bus: Any = None
        try:
            from smbus2 import SMBus
        except ImportError as exc:
            raise SensorInitError(
                "smbus2 is not installed; install the hardware extra: "
                "uv sync --extra hardware"
            ) from exc
        try:
            self._bus = SMBus(bus_number)
        except FileNotFoundError as exc:
            raise SensorInitError(
                f"/dev/i2c-{bus_number} does not exist, so I2C is not enabled on this "
                f"host. Add 'dtparam=i2c_arm=on' to /boot/firmware/config.txt and reboot."
            ) from exc
        except OSError as exc:
            raise SensorInitError(f"cannot open /dev/i2c-{bus_number}: {exc}") from exc

    @property
    def bus_number(self) -> int:
        return self._bus_number

    def write_byte(self, address: int, value: int) -> None:
        if self._bus is None:
            raise SensorInitError("I2C bus is closed")
        self._bus.write_byte(address, value)

    def write_block(self, address: int, data: list[int]) -> None:
        if self._bus is None:
            raise SensorInitError("I2C bus is closed")
        # Use i2c_rdwr for a raw I2C write (no extra register byte).
        # I2C_M_RD = 0x01, so address | 0x00 = write direction.
        msg = [address << 1] + [b & 0xFF for b in data]
        self._bus.i2c_rdwr(msg)

    def read(self, address: int, length: int) -> list[int]:
        if self._bus is None:
            raise SensorInitError("I2C bus is closed")
        # Use i2c_rdwr for a raw I2C read (no extra register byte).
        # I2C_M_RD = 0x01, so address | 0x01 = read direction.
        msg = [address << 1 | 0x01, length]
        self._bus.i2c_rdwr(msg)
        return [int(b) for b in msg[2 : 2 + length]]

    def close(self) -> None:
        bus = self._bus
        self._bus = None
        if bus is not None:
            with suppress(Exception):  # close must never raise
                bus.close()
