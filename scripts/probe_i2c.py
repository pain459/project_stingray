#!/usr/bin/env python3
"""Probe the I2C bus without i2c-tools or smbus2.

Uses raw ioctls on /dev/i2c-N so it runs even before ``uv sync --extra
hardware``. Usage:

    python scripts/probe_i2c.py            # scan bus 1
    python scripts/probe_i2c.py --bus 1 --address 0x44   # probe one device
    python scripts/probe_i2c.py --sht45    # scan + SHT45 serial + one sample
    python scripts/probe_i2c.py --sps30    # scan + SPS30 firmware + one sample
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

# SPS30 commands (16-bit pointers)
SPS30_CMD_START_MEASUREMENT = 0x0010
SPS30_CMD_READ_MEASURED_VALUES = 0x0300
SPS30_CMD_READ_VERSION = 0xD100
SPS30_FORMAT_UINT16 = 0x05


def crc8_sensirion(data: bytes | list[int]) -> int:
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
    try:
        fcntl.ioctl(fd, I2C_RETRIES, struct.pack("i", 1))
    except OSError:
        pass  # not all I2C controllers support this
    try:
        fcntl.ioctl(fd, I2C_TIMEOUT, struct.pack("i", 1000))  # ms
    except OSError:
        pass
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


def write_block(fd: int, address: int, data: bytes) -> None:
    fcntl.ioctl(fd, I2C_SLAVE_FORCE, struct.pack("i", address))
    os.write(fd, data)


def read_after_write(
    fd: int, address: int, write_data: bytes, read_length: int, delay_s: float
) -> bytes:
    fcntl.ioctl(fd, I2C_SLAVE_FORCE, struct.pack("i", address))
    os.write(fd, write_data)
    time.sleep(delay_s)
    return os.read(fd, read_length)


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


def probe_sps30(fd: int, address: int) -> None:
    print(f"\nSPS30 probe at 0x{address:02X}:")
    # Read firmware version (pointer 0xD100, 3 bytes: major, minor, CRC)
    try:
        raw = read_after_write(fd, address, bytes([0xD1, 0x00]), 3, 0.005)
    except OSError as exc:
        print(f"  firmware read failed: {exc}")
        return
    if len(raw) < 3:
        print(f"  firmware short read: {len(raw)} bytes")
        return
    ok = crc8_sensirion(raw[0:2]) == raw[2]
    print(f"  firmware: {raw[0]}.{raw[1]}  (CRC {'OK' if ok else 'FAIL'})")

    # Start measurement (pointer 0x0010, 3 bytes: format, dummy, CRC)
    data = bytes([SPS30_FORMAT_UINT16, 0x00, crc8_sensirion([SPS30_FORMAT_UINT16, 0x00])])
    try:
        write_block(fd, address, bytes([0x00, 0x10]) + data)
    except OSError as exc:
        print(f"  start measurement failed: {exc}")
        return
    time.sleep(0.025)

    # Read measured values (pointer 0x0300, 30 bytes)
    try:
        raw = read_after_write(fd, address, bytes([0x03, 0x00]), 30, 0.005)
    except OSError as exc:
        print(f"  measurement read failed: {exc}")
        return
    if len(raw) < 30:
        print(f"  measurement short read: {len(raw)} bytes")
        return

    # Parse 10 16-bit values with CRCs
    values = []
    all_ok = True
    for i in range(0, 30, 3):
        word = (raw[i] << 8) | raw[i + 1]
        if crc8_sensirion(raw[i : i + 2]) != raw[i + 2]:
            all_ok = False
        values.append(word)

    labels = [
        "PM1.0 mass",
        "PM2.5 mass",
        "PM4.0 mass",
        "PM10 mass",
        "PM0.5 num",
        "PM1.0 num",
        "PM2.5 num",
        "PM4.0 num",
        "PM10 num",
        "TPS",
    ]
    print(f"  CRCs: {'all OK' if all_ok else 'MISMATCH'}")
    for label, v in zip(labels, values, strict=True):
        print(f"  {label:12s}: {v / 100.0:.2f}")


def probe_scd4x(fd: int, address: int) -> None:
    print(f"\nSCD4X probe at 0x{address:02X}:")
    
    # Read sensor variant (command 0x202F, 3 bytes: variant, CRC)
    try:
        raw = read_after_write(fd, address, bytes([0x20, 0x2F]), 3, 0.001)
    except OSError as exc:
        print(f"  variant read failed: {exc}")
        return
    if len(raw) < 3:
        print(f"  variant short read: {len(raw)} bytes")
        return
    variant = (raw[0] << 8) | raw[1]
    ok = crc8_sensirion(raw[0:2]) == raw[2]
    variant_code = (variant >> 12) & 0x0F
    variant_names = {0: "SCD40", 1: "SCD41", 5: "SCD43"}
    variant_name = variant_names.get(variant_code, f"Unknown({variant_code})")
    print(f"  variant: {variant_name}  (CRC {'OK' if ok else 'FAIL'})")
    
    # Start periodic measurement (command 0x21B1, 3 bytes: cmd, CRC)
    cmd = bytes([0x21, 0xB1])
    crc = crc8_sensirion(cmd)
    try:
        write_block(fd, address, cmd + bytes([crc]))
    except OSError as exc:
        print(f"  start measurement failed: {exc}")
        return
    time.sleep(0.025)
    
    # Read measurement (command 0xEC05, 9 bytes: CO2, temp, RH + 3 CRCs)
    try:
        raw = read_after_write(fd, address, bytes([0xEC, 0x05]), 9, 0.005)
    except OSError as exc:
        print(f"  measurement read failed: {exc}")
        return
    if len(raw) < 9:
        print(f"  measurement short read: {len(raw)} bytes")
        return
    
    # Parse 3 16-bit values with CRCs
    co2_raw = (raw[0] << 8) | raw[1]
    co2_crc_ok = crc8_sensirion(raw[0:2]) == raw[2]
    
    temp_raw = (raw[3] << 8) | raw[4]
    temp_crc_ok = crc8_sensirion(raw[3:5]) == raw[5]
    
    rh_raw = (raw[6] << 8) | raw[7]
    rh_crc_ok = crc8_sensirion(raw[6:8]) == raw[8]
    
    all_ok = co2_crc_ok and temp_crc_ok and rh_crc_ok
    
    # Convert to engineering units
    co2_ppm = float(co2_raw)
    temp_c = -45.0 + 175.0 * temp_raw / 65535.0
    rh_percent = 100.0 * rh_raw / 65535.0
    
    print(f"  CRCs: {'all OK' if all_ok else 'MISMATCH'}")
    print(f"  CO2: {co2_ppm:.0f} ppm")
    print(f"  Temperature: {temp_c:.1f} °C")
    print(f"  Humidity: {rh_percent:.1f} %")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", type=int, default=1, help="I2C bus number (default 1)")
    parser.add_argument(
        "--address", type=lambda s: int(s, 0), default=0x44, help="7-bit address to probe"
    )
    parser.add_argument(
        "--sht45", action="store_true", help="scan, then probe SHT45 serial + one sample"
    )
    parser.add_argument(
        "--sps30", action="store_true", help="scan, then probe SPS30 firmware + one sample"
    )
    parser.add_argument(
        "--scd4x", action="store_true", help="scan, then probe SCD4X variant + one sample"
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
        elif args.sps30:
            print(f"Scanning bus {args.bus} (0x08-0x77):")
            found = scan(fd)
            devices = ", ".join(f"0x{a:02X}" for a in found) if found else "(none)"
            print(f"  devices: {devices}")
            sps_addr = 0x69
            if sps_addr not in found:
                print("  warning: 0x69 did not ACK; probing anyway")
            probe_sps30(fd, sps_addr)
        elif args.scd4x:
            print(f"Scanning bus {args.bus} (0x08-0x77):")
            found = scan(fd)
            devices = ", ".join(f"0x{a:02X}" for a in found) if found else "(none)"
            print(f"  devices: {devices}")
            scd_addr = 0x62
            if scd_addr not in found:
                print("  warning: 0x62 did not ACK; probing anyway")
            probe_scd4x(fd, scd_addr)
        else:
            print(f"Probing bus {args.bus} at 0x{args.address:02X}:")
            print(f"  {'ACK' if probe_address(fd, args.address) else 'no ACK'}")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
