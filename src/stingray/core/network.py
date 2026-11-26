"""Host network facts for the dashboard and API.

Standard library only, so this module is importable everywhere (CI included).
On a non-Linux host the individual lookups simply return ``None``; they never
raise into the acquisition path.

Only non-secret facts are exposed: interface name, IP address and WiFi signal
level. No SSID, credentials, MAC addresses or routes are reported.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

#: Linux ioctl for reading an interface's IPv4 address.
_SIOCGIFADDR = 0x8915


@dataclass(frozen=True)
class NetworkInfo:
    """A point-in-time snapshot of the host's primary network link."""

    interface: str
    ip_address: str | None = None
    wifi_rssi: int | None = None


def interface_address(interface: str) -> str | None:
    """Return the IPv4 address assigned to ``interface``, if any."""
    try:
        import fcntl
        import struct
    except ImportError:  # pragma: no cover - non-Linux hosts
        return None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            packed = struct.pack("256s", interface.encode("ascii")[:15])
            result = fcntl.ioctl(sock.fileno(), _SIOCGIFADDR, packed)
        return socket.inet_ntoa(result[20:24])
    except (OSError, ValueError):
        return None


def default_route_address() -> str | None:
    """Fallback IP when the named interface has no address.

    A connected UDP socket performs a route lookup without sending any packet.
    The destination is in the reserved TEST-NET block, so no traffic is sent.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            address: str = sock.getsockname()[0]
        return address
    except OSError:
        return None


def wifi_rssi(interface: str) -> int | None:
    """Return the WiFi signal level in dBm from ``/proc/net/wireless``."""
    try:
        with open("/proc/net/wireless", encoding="ascii") as handle:
            lines = handle.readlines()[2:]
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) >= 4 and fields[0].rstrip(":") == interface:
            try:
                return int(float(fields[3]))
            except ValueError:
                return None
    return None


def get_network_info(interface: str = "wlan0") -> NetworkInfo:
    """Collect the IP and WiFi level for ``interface`` with safe fallbacks."""
    return NetworkInfo(
        interface=interface,
        ip_address=interface_address(interface) or default_route_address(),
        wifi_rssi=wifi_rssi(interface),
    )


def as_dict(info: NetworkInfo) -> dict[str, Any]:
    """JSON-friendly form used by the API (no secrets included)."""
    return {
        "interface": info.interface,
        "ip_address": info.ip_address,
        "wifi_rssi": info.wifi_rssi,
    }
