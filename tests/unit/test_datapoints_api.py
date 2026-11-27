"""Tests for the browser-facing /api/datapoints publication surface."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.client import HTTPConnection
from pathlib import Path

import pytest

from stingray.api.server import ApiState, create_api_server
from stingray.core.models import SensorId
from stingray.core.network import NetworkInfo

_BASE_EPOCH = datetime(2026, 11, 27, 12, 0, 0, tzinfo=UTC).timestamp()


class _FakeQuery:
    latest_calls = 0
    range_calls: list[dict[str, object]] = []

    def __init__(self, _path: str) -> None:
        pass

    def latest(self) -> list[dict[str, object]]:
        type(self).latest_calls += 1
        return [
            {
                "sensor": "sps30",
                "metric": "pm2_5_ug_m3",
                "value": 12.3,
                "unit": "µg/m³",
                "observed_at": datetime.fromtimestamp(_BASE_EPOCH + 3, UTC).isoformat(),
                "quality": "ok",
            }
        ]

    def range(
        self,
        *,
        metric: str | None,
        sensor: object,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[dict[str, object]]:
        type(self).range_calls.append(
            {"metric": metric, "sensor": sensor, "start": start.timestamp(), "limit": limit}
        )
        rows = [
            {"observed_epoch": _BASE_EPOCH + i, "value": float(i), "metric": metric}
            for i in (1, 2, 3)
        ]
        return [r for r in rows if r["observed_epoch"] >= start.timestamp()][:limit]


class _FakeStats:
    reads_ok = 2
    reads_failed = 0
    measurements = 7
    last_error = ""


class _FakeScheduler:
    stats = {SensorId.SPS30: _FakeStats()}

    def health(self) -> dict:
        return {}


@pytest.fixture(autouse=True)
def _reset_fake_query() -> Iterator[None]:
    _FakeQuery.latest_calls = 0
    _FakeQuery.range_calls = []
    yield


@contextmanager
def _client(state: ApiState) -> Iterator[HTTPConnection]:
    server = create_api_server("127.0.0.1", 0, state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        yield conn
    finally:
        conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _state(tmp_path: Path, *, ttl: float = 5.0) -> ApiState:
    return ApiState(
        _FakeScheduler(),  # type: ignore[arg-type]
        str(tmp_path),
        start_time=0,
        network=NetworkInfo("wlan0", "192.168.68.53", -42),
        snapshot_ttl_seconds=ttl,
    )


def _get(
    conn: HTTPConnection,
    path: str,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, object]:
    conn.request("GET", path, headers=headers or {})
    response = conn.getresponse()
    body = response.read()
    return response.status, body, response


def test_latest_is_cached_before_duckdb(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("stingray.api.server.ParquetQuery", _FakeQuery)
    with _client(_state(tmp_path)) as conn:
        status, first_body, _ = _get(conn, "/api/datapoints?mode=latest")
        status2, second_body, _ = _get(conn, "/api/datapoints?mode=latest")
    assert status == status2 == 200
    first = json.loads(first_body)
    second = json.loads(second_body)
    assert _FakeQuery.latest_calls == 1
    assert first["revision"] == second["revision"] == 1
    assert first["query_count"] == 1
    assert first["datapoints"][0]["metric"] == "pm2_5_ug_m3"
    assert first["network"]["ip_address"] == "192.168.68.53"
    assert first["system"]["stats"]["sps30"]["measurements"] == 7
    assert first["total"] == 7
    assert "summary" in first and "deploy" in first
    assert first_body == second_body


def test_zero_ttl_rebuilds_snapshot_and_bumps_revision(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("stingray.api.server.ParquetQuery", _FakeQuery)
    with _client(_state(tmp_path, ttl=0)) as conn:
        _, first_body, _ = _get(conn, "/api/datapoints?mode=latest")
        _, second_body, _ = _get(conn, "/api/datapoints?mode=latest")
    assert _FakeQuery.latest_calls == 2
    assert json.loads(first_body)["revision"] == 1
    assert json.loads(second_body)["revision"] == 2


def test_etag_returns_304_without_query(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("stingray.api.server.ParquetQuery", _FakeQuery)
    with _client(_state(tmp_path)) as conn:
        _, _, first_response = _get(conn, "/api/datapoints?mode=latest")
        etag = first_response.getheader("ETag")
        assert etag
        status, body, _ = _get(
            conn, "/api/datapoints?mode=latest", {"If-None-Match": etag}
        )
    assert status == 304
    assert body == b""
    assert _FakeQuery.latest_calls == 1


def test_history_uses_since_cursor(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("stingray.api.server.ParquetQuery", _FakeQuery)
    with _client(_state(tmp_path)) as conn:
        _, first_body, _ = _get(
            conn, "/api/datapoints?mode=history&metric=pm2_5_ug_m3&hours=6&limit=2"
        )
        first = json.loads(first_body)
        cursor = first["next_cursor"]
        _, second_body, _ = _get(
            conn,
            "/api/datapoints?mode=history&metric=pm2_5_ug_m3&hours=6&limit=2"
            "&since=" + str(cursor),
        )
        second = json.loads(second_body)
    assert first["samples"] == [[_BASE_EPOCH + 1, 1.0], [_BASE_EPOCH + 2, 2.0]]
    assert first["truncated"] is True
    assert second["samples"] == [[_BASE_EPOCH + 3, 3.0]]
    assert len(_FakeQuery.range_calls) == 2
    assert _FakeQuery.range_calls[1]["start"] == pytest.approx(_BASE_EPOCH + 2)


def test_invalid_datapoints_requests(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("stingray.api.server.ParquetQuery", _FakeQuery)
    with _client(_state(tmp_path)) as conn:
        status, _, _ = _get(conn, "/api/datapoints?mode=bogus")
        status2, body, _ = _get(conn, "/api/datapoints?mode=history")
    assert status == 400
    assert status2 == 400
    assert json.loads(body)["error"] == "metric is required"
    assert _FakeQuery.latest_calls == 0
