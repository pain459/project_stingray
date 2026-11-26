#!/usr/bin/env python3
"""Probe the I2C bus without i2c-tools or smbus2.

Uses raw ioctls on /dev/i2c-N so it runs even before ``uv sync --extra
hardware``. Usage:

    python scripts/probe_i2c.py            # scan bus 1
    python scripts/probe_i2c.py --bus 1 --address 0x44   # probe one device
    python scripts/probe_i2c.py --sht45    # scan + SHT45 serial + one sample
"""

from __future__ import annotations

import argparse
import fcntl
import os
import struct
import sys
import time

# I2C ioctl numbers (linux/i2c.h)
I2C_SLAVE = 0x0703
I2C_SLAVE_FORCE = 0x0706
I2C_RETRIES = 0x0707
I2C_TIMEOUT = 0x0708

# SHT45 commands
CMD_MEASURE_HIGH_PRECISION = 0xFD
CMD_READ_SERIAL = 0x89
CMD_SOFT_RESET = 0x94


def crc8_sensirion(data: bytes) -> int:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def open_bus(bus: int) -> int:
    path = f"/dev/i2c-{bus}"
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        sys.exit(
            f"error: {path} does not exist. I2C is not enabled on this host.\n"
            f"Fix: add 'dtparam=i2c_arm=on' to /boot/firmware/config.txt and reboot."
        )
    except PermissionError:
        sys.exit(f"error: permission denied on {path}. Add the user to the 'i2c' group.")
    fcntl.ioctl(fd, I2C_RETRIES, struct.pack("i", 1))
    fcntl.ioctl(fd, I2C_TIMEOUT, struct.pack("i", 1000))  # ms
    return fd


def probe_address(fd: int, address: int) -> bool:
    """Return True if the address acknowledges (writes a single 0x00 byte)."""
    try:
        fcntl.ioctl(fd, I2C_SLAVE_FORCE, struct.pack("i", address))
        os.write(fd, b"\x00")
        return True
    except OSError:
        return False


def read_bytes(fd: int, address: int, command: int, length: int, delay_s: float) -> bytes:
    fcntl.ioctl(fd, I2C_SLAVE_FORCE, struct.pack("i", address))
    os.write(fd, bytes([command]))
    time.sleep(delay_s)
    return os.read(fd, length)


def scan(fd: int) -> list[int]:
    found = []
    for address in range(0x08, 0x78):
        if probe_address(fd, address):
            found.append(address)
    return found


def probe_sht45(fd: int, address: int) -> None:
    print(f"\nSHT45 probe at 0x{address:02X}:")
    try:
        raw = read_bytes(fd, address, CMD_READ_SERIAL, 6, 0.002)
    except OSError as exc:
        print(f"  serial read failed: {exc}")
        return
    if len(raw) < 6:
        print(f"  short read: {len(raw)} bytes")
        return
    ok1 = crc8_sensirion(raw[0:2]) == raw[2]
    ok2 = crc8_sensirion(raw[3:5]) == raw[5]
    serial = (raw[0] << 24) | (raw[1] << 16) | (raw[3] << 8) | raw[4]
    crc1 = "OK" if ok1 else "FAIL"
    crc2 = "OK" if ok2 else "FAIL"
    print(f"  serial: 0x{serial:08X}  (CRC1 {crc1}, CRC2 {crc2})")

    raw = read_bytes(fd, address, CMD_MEASURE_HIGH_PRECISION, 6, 0.012)
    if len(raw) < 6:
        print(f"  measurement short read: {len(raw)} bytes")
        return
    t_ticks = (raw[0] << 8) | raw[1]
    rh_ticks = (raw[3] << 8) | raw[4]
    temp = -45.0 + 175.0 * t_ticks / 65535.0
    rh = max(0.0, min(100.0, -6.0 + 125.0 * rh_ticks / 65535.0))
    print(f"  temperature: {temp:.2f} C")
    print(f"  humidity:    {rh:.2f} %RH")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", type=int, default=1, help="I2C bus number (default 1)")
    parser.add_argument(
        "--address", type=lambda s: int(s, 0), default=0x44, help="7-bit address to probe"
    )
    parser.add_argument(
        "--sht45", action="store_true", help="scan, then probe SHT45 serial + one sample"
    )
    args = parser.parse_args()

    fd = open_bus(args.bus)
    try:
        if args.sht45:
            print(f"Scanning bus {args.bus} (0x08-0x77):")
            found = scan(fd)
            devices = ", ".join(f"0x{a:02X}" for a in found) if found else "(none)"
            print(f"  devices: {devices}")
            if args.address not in found:
                print(f"  warning: 0x{args.address:02X} did not ACK; probing anyway")
            probe_sht45(fd, args.address)
        else:
            print(f"Probing bus {args.bus} at 0x{args.address:02X}:")
            print(f"  {'ACK' if probe_address(fd, args.address) else 'no ACK'}")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
