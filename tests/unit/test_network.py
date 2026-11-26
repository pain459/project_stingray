"""Unit tests for the host network facts (no real network required)."""

from __future__ import annotations

import io

from stingray.core.network import (
    NetworkInfo,
    as_dict,
    get_network_info,
    wifi_rssi,
)

# Matches the real /proc/net/wireless layout on the Pi: the dBm level is
# field 3 after the interface name (e.g. "66." is link quality, "-44." is level).
_WIRELESS = """\
Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   66.  -55.  -256        0      0      0      5      0        0
  eth0: 0000   66.  -30.  -256        0      0      0      0      0        0
"""


def test_wifi_rssi_parses_level_in_dbm(monkeypatch) -> None:
    monkeypatch.setattr("builtins.open", lambda *_a, **_k: io.StringIO(_WIRELESS))
    assert wifi_rssi("wlan0") == -55
    assert wifi_rssi("eth0") == -30


def test_wifi_rssi_missing_interface(monkeypatch) -> None:
    monkeypatch.setattr("builtins.open", lambda *_a, **_k: io.StringIO(_WIRELESS))
    assert wifi_rssi("wlan1") is None


def test_wifi_rssi_unreadable_file(monkeypatch) -> None:
    def fail(*_a, **_k):
        raise OSError("no /proc/net/wireless")

    monkeypatch.setattr("builtins.open", fail)
    assert wifi_rssi("wlan0") is None


def test_get_network_info_uses_interface_address(monkeypatch) -> None:
    monkeypatch.setattr("stingray.core.network.interface_address", lambda _i: "192.168.68.53")
    monkeypatch.setattr("stingray.core.network.default_route_address", lambda: "10.0.0.2")
    monkeypatch.setattr("stingray.core.network.wifi_rssi", lambda _i: -60)

    info = get_network_info("wlan0")
    assert info == NetworkInfo("wlan0", "192.168.68.53", -60)
    assert as_dict(info) == {
        "interface": "wlan0",
        "ip_address": "192.168.68.53",
        "wifi_rssi": -60,
    }


def test_get_network_info_falls_back_to_default_route(monkeypatch) -> None:
    monkeypatch.setattr("stingray.core.network.interface_address", lambda _i: None)
    monkeypatch.setattr("stingray.core.network.default_route_address", lambda: "10.0.0.2")
    monkeypatch.setattr("stingray.core.network.wifi_rssi", lambda _i: None)

    info = get_network_info("wlan0")
    assert info.ip_address == "10.0.0.2"
    assert info.wifi_rssi is None
    assert as_dict(info)["wifi_rssi"] is None
