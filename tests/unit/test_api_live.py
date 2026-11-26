"""Regression tests for the /api/live response shape (no HTTP server needed)."""

from __future__ import annotations

import io
import json

from stingray.api.server import ApiHandler, ApiState
from stingray.core.network import NetworkInfo


class _FakeQuery:
    def __init__(self, _path: str) -> None:
        pass

    def latest(self) -> list[object]:
        return []

    def count_recent(self, *, hours: int) -> int:
        return 7


class _FakeHandler:
    def __init__(self, state: ApiState) -> None:
        self.state = state
        self.wfile = io.BytesIO()
        self.status: int | None = None

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, _key: str, _value: str) -> None:
        pass

    def end_headers(self) -> None:
        pass


class _FakeScheduler:
    def health(self) -> dict:
        return {}


def _live_payload(monkeypatch, network: NetworkInfo | None) -> dict:
    monkeypatch.setattr("stingray.api.server.ParquetQuery", _FakeQuery)
    state = ApiState(_FakeScheduler(), "/tmp/stingray-test", network=network)  # type: ignore[arg-type]
    handler = _FakeHandler(state)
    ApiHandler._serve_live(handler)  # type: ignore[arg-type]
    assert handler.status == 200
    return json.loads(handler.wfile.getvalue())


def test_live_includes_network(monkeypatch) -> None:
    payload = _live_payload(
        monkeypatch, NetworkInfo("wlan0", "192.168.68.53", -42)
    )
    assert payload["network"] == {
        "interface": "wlan0",
        "ip_address": "192.168.68.53",
        "wifi_rssi": -42,
    }
    assert payload["total"] == 7
    assert payload["live"] == []


def test_live_network_is_null_when_unavailable(monkeypatch) -> None:
    assert _live_payload(monkeypatch, None)["network"] is None
