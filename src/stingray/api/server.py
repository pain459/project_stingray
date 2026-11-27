"""Lightweight HTTP API for the Stingray dashboard.

Uses only the standard library (``http.server``) so the dashboard can run
without extra dependencies. The API is unauthenticated by design — it is
intended for internal use on the local network.

Endpoints:
    GET /            -> dashboard HTML
    GET /api/live    -> latest reading per (sensor, metric) + sensor health
    GET /api/history -> recent measurements (for sparklines)
    GET /api/health  -> process + scheduler health
    GET /api/summaries -> recent hourly LLM summaries
    POST /api/summaries/refresh -> start an on-demand summary (rate-limited)
    GET /api/deploy/status -> last web-triggered deploy result
    POST /api/deploy       -> deploy the latest GitHub release
    POST /api/deploy/force -> reinstall the latest release even at the same version
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
from stingray.core.network import NetworkInfo, as_dict
from stingray.core.scheduler import SensorScheduler
from stingray.deploy_control import DeployControl
from stingray.storage.parquet_store import ParquetQuery
from stingray.summary_refresh import SummaryRefresher
from stingray.summary_store import SummaryStore

log = logging.getLogger("stingray.api")

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_DASHBOARD_HTML = _STATIC_DIR / "index.html"

_MIME_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


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
        mode: str = "mock",
        start_time: float | None = None,
        network: NetworkInfo | None = None,
        summaries_path: str | Path | None = None,
        refresher: SummaryRefresher | None = None,
        deploy: DeployControl | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.db_path = db_path
        self.mode = mode
        self.start_time = start_time if start_time is not None else time.monotonic()
        self.network = network
        self.summaries_path = Path(summaries_path) if summaries_path is not None else None
        self.refresher = refresher
        self.deploy = deploy
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
    handler.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
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
        elif path == "/api/summaries":
            self._serve_summaries(query)
        elif path == "/api/summaries/latest":
            self._serve_latest_summary()
        elif path == "/api/deploy/status":
            self._serve_deploy_status()
        elif path.startswith("/css/") or path.startswith("/js/"):
            self._serve_static(path)
        else:
            _json_response(self, 404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/api/summaries/refresh":
            self._serve_refresh()
        elif path == "/api/deploy":
            self._serve_deploy(force=False)
        elif path == "/api/deploy/force":
            self._serve_deploy(force=True)
        else:
            _json_response(self, 404, {"error": "not found"})

    def _serve_dashboard(self) -> None:
        try:
            html = _DASHBOARD_HTML.read_text(encoding="utf-8")
        except FileNotFoundError:
            _html_response(self, 500, "<h1>Dashboard not found</h1>")
            return
        _html_response(self, 200, html)

    def _serve_static(self, path: str) -> None:
        """Serve static assets (CSS, JS) from the static directory."""
        # Prevent path traversal
        if ".." in path:
            _json_response(self, 403, {"error": "forbidden"})
            return
        file_path = _STATIC_DIR / path.lstrip("/")
        if not file_path.is_file():
            _json_response(self, 404, {"error": "not found"})
            return
        try:
            data = file_path.read_bytes()
        except OSError:
            _json_response(self, 500, {"error": "read error"})
            return
        ext = file_path.suffix.lower()
        mime = _MIME_TYPES.get(ext, "application/octet-stream")
        body = data
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_live(self) -> None:
        """Latest reading per (sensor, metric) plus sensor health."""
        pq = ParquetQuery(self.state.db_path)
        live = pq.latest()
        total = pq.count_recent(hours=24)

        # Sensor health from the scheduler (live, in-memory)
        health = {}
        for sid, h in self.state.scheduler.health().items():
            health[sid.value] = {
                "state": h.state.value,
                "consecutive_failures": h.consecutive_failures,
                "last_read_at": h.last_read_at.isoformat() if h.last_read_at else None,
                "message": h.message,
            }

        _json_response(
            self,
            200,
            {
                "live": live,
                "health": health,
                "network": as_dict(self.state.network) if self.state.network is not None else None,
                "total": total,
            },
        )

    def _serve_history(self, query: dict[str, list[str]]) -> None:
        """Time-range history with stats, matching the reference API shape.

        Query params:
            metric: metric name (e.g. pm2_5_ug_m3)
            sensor: sensor name (optional)
            hours: time range in hours (default 24, max 720)
            limit: max rows to return (default 900, max 5000)
        """
        metric = query.get("metric", [None])[0]
        sensor = query.get("sensor", [None])[0]
        hours = int(query.get("hours", ["24"])[0])
        hours = max(1, min(hours, 720))
        limit = int(query.get("limit", ["900"])[0])
        limit = max(1, min(limit, 5000))

        pq = ParquetQuery(self.state.db_path)
        sid = SensorId(sensor) if sensor else None
        samples, stats = pq.range_with_stats(
            metric=metric,
            sensor=sid,
            hours=hours,
            limit=limit,
        )

        _json_response(
            self,
            200,
            {
                "metric": metric,
                "sensor": sensor,
                "hours": hours,
                "samples": samples,
                "stats": stats,
                "returned": len(samples),
            },
        )

    def _summaries_store(self) -> SummaryStore | None:
        if self.state.summaries_path is None:
            return None
        return SummaryStore(self.state.summaries_path)

    def _serve_summaries(self, query: dict[str, list[str]]) -> None:
        """Return recent hourly summaries, newest first."""
        store = self._summaries_store()
        if store is None:
            _json_response(self, 200, {"summaries": [], "returned": 0})
            return
        limit = int(query.get("limit", ["10"])[0])
        limit = max(1, min(limit, 100))
        summaries = store.list(limit=limit)
        _json_response(
            self,
            200,
            {"summaries": summaries, "returned": len(summaries)},
        )

    def _serve_latest_summary(self) -> None:
        """Return the most recent hourly summary plus refresh cooldown state."""
        store = self._summaries_store()
        summary = store.latest() if store is not None else None
        _json_response(
            self,
            200,
            {
                "summary": summary,
                "refresh": self.state.refresher.status() if self.state.refresher else None,
            },
        )

    def _serve_refresh(self) -> None:
        """Start an on-demand summary run, enforcing the server-side cooldown."""
        refresher = self.state.refresher
        if refresher is None:
            _json_response(self, 503, {"error": "summary refresh is not configured"})
            return
        status, payload = refresher.trigger()
        _json_response(self, status, payload)

    def _serve_deploy_status(self) -> None:
        """Last web-triggered deploy result (or disabled state)."""
        control = self.state.deploy
        status = control.status() if control is not None else {"configured": False, "state": "idle"}
        _json_response(self, 200, status)

    def _serve_deploy(self, *, force: bool) -> None:
        """Trigger the Pi's deploy unit with a fixed, non-user-supplied argv."""
        control = self.state.deploy
        if control is None:
            _json_response(
                self,
                503,
                {
                    "error": "unavailable",
                    "message": "Web deployment is not configured on this host.",
                },
            )
            return
        status, payload = control.trigger(force=force)
        _json_response(self, status, payload)

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
                "mode": self.state.mode,
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
