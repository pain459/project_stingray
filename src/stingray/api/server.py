"""Lightweight HTTP API for the Stingray dashboard.

Uses only the standard library (``http.server``) so the dashboard can run
without extra dependencies. The API is unauthenticated by design — it is
intended for internal use on the local network.

Endpoints:
    GET /            -> dashboard HTML
    GET /api/live    -> latest reading per (sensor, metric) + sensor health
    GET /api/history -> recent measurements (for sparklines)
    GET /api/health  -> process + scheduler health
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from stingray.core.models import SensorId
from stingray.core.scheduler import SensorScheduler
from stingray.storage.sqlite_store import HistoryQuery

log = logging.getLogger("stingray.api")

_DASHBOARD_HTML = Path(__file__).resolve().parent / "static" / "index.html"


class ApiState:
    """Shared state passed to the API handler.

    Holds references to the scheduler (for live health) and the database
    (for history queries). The dashboard reads from the database, which is
    the single source of truth for persisted measurements.
    """

    def __init__(
        self,
        scheduler: SensorScheduler,
        db_path: str,
        *,
        start_time: float | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.db_path = db_path
        self.start_time = start_time if start_time is not None else time.monotonic()
        self._lock = threading.Lock()

    def uptime_seconds(self) -> float:
        return time.monotonic() - self._start_mono()

    def _start_mono(self) -> float:
        return self.start_time


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, status: int, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class ApiHandler(BaseHTTPRequestHandler):
    """Request handler for the Stingray dashboard API."""

    # Set by the server factory; see create_api_server().
    state: ApiState

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: N802
        log.debug("api: " + fmt, *args)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path == "/":
            self._serve_dashboard()
        elif path == "/api/live":
            self._serve_live()
        elif path == "/api/history":
            self._serve_history(query)
        elif path == "/api/health":
            self._serve_health()
        else:
            _json_response(self, 404, {"error": "not found"})

    def _serve_dashboard(self) -> None:
        try:
            html = _DASHBOARD_HTML.read_text(encoding="utf-8")
        except FileNotFoundError:
            _html_response(self, 500, "<h1>Dashboard not found</h1>")
            return
        _html_response(self, 200, html)

    def _serve_live(self) -> None:
        """Latest reading per (sensor, metric) plus sensor health."""
        from stingray.storage.sqlite_store import Database

        db = Database(self.state.db_path, wal=False)
        try:
            hq = HistoryQuery(db)
            # Latest per sensor+metric
            rows = db.connection.execute(
                """
                SELECT m.sensor, m.metric, m.value, m.unit, m.observed_at, m.quality
                FROM measurements m
                INNER JOIN (
                    SELECT sensor, metric, MAX(id) AS max_id
                    FROM measurements
                    GROUP BY sensor, metric
                ) latest ON m.id = latest.max_id
                ORDER BY m.sensor, m.metric
                """
            ).fetchall()
            live = [
                {
                    "sensor": r["sensor"],
                    "metric": r["metric"],
                    "value": r["value"],
                    "unit": r["unit"],
                    "observed_at": r["observed_at"],
                    "quality": r["quality"],
                }
                for r in rows
            ]
            total = hq.count()
        finally:
            db.close()

        # Sensor health from the scheduler (live, in-memory)
        health = {}
        for sid, h in self.state.scheduler.health().items():
            health[sid.value] = {
                "state": h.state.value,
                "consecutive_failures": h.consecutive_failures,
                "last_read_at": h.last_read_at.isoformat() if h.last_read_at else None,
                "message": h.message,
            }

        _json_response(self, 200, {"live": live, "health": health, "total": total})

    def _serve_history(self, query: dict[str, list[str]]) -> None:
        """Recent measurements for sparklines."""
        from stingray.storage.sqlite_store import Database

        limit = int(query.get("limit", ["100"])[0])
        limit = max(1, min(limit, 1000))
        sensor = query.get("sensor", [None])[0]
        metric = query.get("metric", [None])[0]

        db = Database(self.state.db_path, wal=False)
        try:
            hq = HistoryQuery(db)
            sid = SensorId(sensor) if sensor else None
            rows = hq.range(
                start=None,
                end=None,
                sensor=sid,
                metric=metric,
                limit=limit,
                offset=0,
            )
            # Return newest-first for the client
            rows.reverse()
        finally:
            db.close()

        _json_response(self, 200, {"rows": rows, "limit": limit})

    def _serve_health(self) -> None:
        """Process and scheduler health."""
        stats = self.state.scheduler.stats
        stats_out = {}
        for sid, s in stats.items():
            stats_out[sid.value] = {
                "reads_ok": s.reads_ok,
                "reads_failed": s.reads_failed,
                "measurements": s.measurements,
                "last_error": s.last_error,
            }
        _json_response(
            self,
            200,
            {
                "uptime_seconds": round(self.state.uptime_seconds(), 1),
                "mode": "mock",
                "stats": stats_out,
                "time": datetime.now(UTC).isoformat(),
            },
        )


def create_api_server(
    host: str,
    port: int,
    state: ApiState,
) -> ThreadingHTTPServer:
    """Create and configure the API server (does not start it)."""
    handler = type("BoundApiHandler", (ApiHandler,), {"state": state})
    server = ThreadingHTTPServer((host, port), handler)
    return server


def start_api_server(
    host: str,
    port: int,
    state: ApiState,
) -> ThreadingHTTPServer:
    """Create, start, and return the API server."""
    server = create_api_server(host, port, state)
    thread = threading.Thread(target=server.serve_forever, name="stingray-api", daemon=True)
    thread.start()
    log.info("api server listening on http://%s:%d", host, port)
    return server
