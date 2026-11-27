"""Lightweight HTTP API for the Stingray dashboard.

Uses only the standard library (``http.server``) so the dashboard can run
without extra dependencies. The API is unauthenticated by design — it is
intended for internal use on the local network.

Endpoints:
    GET /            -> dashboard HTML (static; the browser pulls all data)
    GET /api/datapoints?mode=latest
                     -> cached snapshot of every current datapoint + health/summary state
    GET /api/datapoints?mode=history&metric=&hours=&since=&limit=
                     -> bounded samples for one metric, optionally only new rows
    GET /api/live    -> legacy alias for the latest datapoint snapshot
    GET /api/history -> legacy alias for the history mode
    GET /api/health  -> process + scheduler health
    GET /api/summaries -> recent hourly LLM summaries
    POST /api/summaries/refresh -> start an on-demand summary (rate-limited)
    GET /api/deploy/status -> last web-triggered deploy result
    POST /api/deploy       -> deploy the latest GitHub release
    POST /api/deploy/force -> reinstall the latest release even at the same version
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
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

# One snapshot serves every browser tab. A TTL this short keeps live values
# fresh while collapsing the old ~72 DuckDB queries/min down to ~12/min.
_SNAPSHOT_TTL_SECONDS = 5.0


class ApiState:
    """Shared state passed to the API handler.

    Holds references to the scheduler (for in-memory health) and the Parquet
    root (for measurements). The web UI never renders server-side: it pulls
    datapoints from here. A short-TTL snapshot is shared by every browser tab
    so only one bounded DuckDB read runs per TTL window.
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
        snapshot_ttl_seconds: float = _SNAPSHOT_TTL_SECONDS,
    ) -> None:
        self.scheduler = scheduler
        self.db_path = db_path
        self.mode = mode
        self.start_time = start_time if start_time is not None else time.monotonic()
        self.network = network
        self.summaries_path = Path(summaries_path) if summaries_path is not None else None
        self.refresher = refresher
        self.deploy = deploy
        self.snapshot_ttl_seconds = max(0.0, float(snapshot_ttl_seconds))
        self._snapshot_lock = threading.RLock()
        self._snapshot_cache: dict[str, Any] | None = None
        self._snapshot_expires_at = 0.0
        self._snapshot_revision = 0
        self._query_count = 0

    def uptime_seconds(self) -> float:
        return time.monotonic() - self._start_mono()

    def _start_mono(self) -> float:
        return self.start_time

    def record_query(self) -> None:
        """Count one DuckDB read for the diagnostic snapshot."""
        with self._snapshot_lock:
            self._query_count += 1

    @property
    def query_count(self) -> int:
        with self._snapshot_lock:
            return self._query_count

    def snapshot_payload(self, build: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Return the cached snapshot, rebuilding it at most once per TTL.

        The lock is held while building so concurrent tabs coalesce onto the
        same payload instead of each opening a DuckDB connection.
        """
        with self._snapshot_lock:
            now = time.monotonic()
            if self._snapshot_cache is not None and now < self._snapshot_expires_at:
                return self._snapshot_cache
            payload = build()
            self._snapshot_revision += 1
            payload["revision"] = self._snapshot_revision
            payload["generated_at"] = datetime.now(UTC).isoformat()
            payload["query_count"] = self._query_count
            self._snapshot_cache = payload
            self._snapshot_expires_at = now + self.snapshot_ttl_seconds
            return payload


def _etag_for_bytes(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest() + '"'


def _bounded_int(
    query: dict[str, list[str]], key: str, default: int, low: int, high: int
) -> int:
    try:
        value = int(query.get(key, [str(default)])[0])
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def _parse_since(query: dict[str, list[str]]) -> float | None:
    raw = query.get("since", [None])[0]
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _etag_matches(handler: BaseHTTPRequestHandler, etag: str) -> bool:
    """Return True when the request's If-None-Match covers this ETag."""
    headers = getattr(handler, "headers", None)
    if headers is None:
        return False
    supplied = headers.get("If-None-Match")
    if not supplied:
        return False
    return any(
        tag.strip().removeprefix("W/") in (etag, "*") for tag in supplied.split(",")
    )


def _json_bytes_response(
    handler: BaseHTTPRequestHandler,
    status: int,
    body: bytes,
    *,
    etag: str | None = None,
    cache_control: str = "no-cache, no-store, must-revalidate",
) -> bool:
    """Send a JSON body, or a 304 when the cached ETag still matches."""
    if etag is not None and _etag_matches(handler, etag):
        handler.send_response(304)
        handler.send_header("ETag", etag)
        handler.send_header("Cache-Control", cache_control)
        handler.end_headers()
        return False

    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cache-Control", cache_control)
    if etag is not None:
        handler.send_header("ETag", etag)
    handler.end_headers()
    handler.wfile.write(body)
    return True


def _json_response(
    handler: BaseHTTPRequestHandler,
    status: int,
    payload: Any,
    *,
    etag: str | None = None,
    cache_control: str = "no-cache, no-store, must-revalidate",
) -> bool:
    body = json.dumps(payload, default=str).encode("utf-8")
    return _json_bytes_response(
        handler, status, body, etag=etag, cache_control=cache_control
    )


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
        elif path == "/api/datapoints":
            self._serve_datapoints(query)
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

    def _serve_datapoints(self, query: dict[str, list[str]]) -> None:
        """One API surface; query parameters select the payload shape."""
        mode = query.get("mode", ["latest"])[0].lower()
        if mode == "latest":
            self._serve_datapoints_latest()
        elif mode == "history":
            self._serve_datapoints_history(query)
        else:
            _json_response(self, 400, {"error": "unknown mode", "mode": mode})

    def _sensor_health(self) -> dict[str, Any]:
        health: dict[str, Any] = {}
        for sid, h in self.state.scheduler.health().items():
            health[sid.value] = {
                "state": h.state.value,
                "consecutive_failures": h.consecutive_failures,
                "last_read_at": h.last_read_at.isoformat() if h.last_read_at else None,
                "message": h.message,
            }
        return health

    def _system_payload(self) -> dict[str, Any]:
        stats_out: dict[str, Any] = {}
        for sid, s in self.state.scheduler.stats.items():
            stats_out[sid.value] = {
                "reads_ok": s.reads_ok,
                "reads_failed": s.reads_failed,
                "measurements": s.measurements,
                "last_error": s.last_error,
            }
        return {
            "uptime_seconds": round(self.state.uptime_seconds(), 1),
            "mode": self.state.mode,
            "stats": stats_out,
            "time": datetime.now(UTC).isoformat(),
        }

    def _summary_payload(self) -> dict[str, Any]:
        store = self._summaries_store()
        return {
            "summary": store.latest() if store is not None else None,
            "refresh": self.state.refresher.status() if self.state.refresher else None,
        }

    def _deploy_status_payload(self) -> dict[str, Any]:
        control = self.state.deploy
        if control is not None:
            return control.status()
        return {"configured": False, "state": "idle"}

    def _latest_payload(self) -> dict[str, Any]:
        """Build (or reuse) the one snapshot that serves all browser tabs."""

        def build() -> dict[str, Any]:
            pq = ParquetQuery(self.state.db_path)
            datapoints = pq.latest()
            self.state.record_query()
            # Total readings come from in-memory scheduler counters; a second
            # DuckDB count_recent() query is deliberately avoided here.
            total = sum(s.measurements for s in self.state.scheduler.stats.values())
            return {
                "mode": "latest",
                "datapoints": datapoints,
                "health": self._sensor_health(),
                "network": as_dict(self.state.network)
                if self.state.network is not None
                else None,
                "total": total,
                "system": self._system_payload(),
                "summary": self._summary_payload(),
                "deploy": self._deploy_status_payload(),
            }

        return self.state.snapshot_payload(build)

    def _serve_datapoints_latest(self) -> None:
        payload = self._latest_payload()
        body = json.dumps(payload, default=str).encode("utf-8")
        _json_bytes_response(
            self,
            200,
            body,
            etag=_etag_for_bytes(body),
            cache_control="no-cache",
        )

    def _history_payload(self, query: dict[str, list[str]]) -> dict[str, Any]:
        """Bounded samples for one metric, optionally only rows after `since`."""
        metric = query.get("metric", [None])[0]
        if not metric:
            raise ValueError("metric is required")
        sensor = query.get("sensor", [None])[0]
        hours = _bounded_int(query, "hours", 24, 1, 720)
        limit = _bounded_int(query, "limit", 900, 1, 5000)
        since = _parse_since(query)

        sid = None
        if sensor:
            try:
                sid = SensorId(sensor)
            except ValueError as exc:
                raise ValueError("unknown sensor") from exc

        end = datetime.now(UTC)
        start = end - timedelta(hours=hours)
        if since is not None:
            # The cursor is the last sample already seen: make the next query
            # strictly exclusive so a delta does not redraw that row.
            since_start = datetime.fromtimestamp(since, UTC) + timedelta(microseconds=1)
            if since_start > start:
                start = since_start

        pq = ParquetQuery(self.state.db_path)
        rows = pq.range(
            metric=metric,
            sensor=sid,
            start=start,
            end=end,
            limit=limit,
        )
        self.state.record_query()
        samples = [[r["observed_epoch"], r["value"]] for r in rows]
        cursor = samples[-1][0] if samples else (since if since is not None else start.timestamp())
        return {
            "mode": "history",
            "metric": metric,
            "sensor": sensor,
            "hours": hours,
            "samples": samples,
            "next_cursor": cursor,
            "returned": len(samples),
            "truncated": len(samples) >= limit,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    def _serve_datapoints_history(self, query: dict[str, list[str]]) -> None:
        try:
            payload = self._history_payload(query)
        except ValueError as exc:
            _json_response(self, 400, {"error": str(exc)})
            return
        body = json.dumps(payload, default=str).encode("utf-8")
        _json_bytes_response(
            self,
            200,
            body,
            etag=_etag_for_bytes(body),
            cache_control="no-cache",
        )

    def _serve_live(self) -> None:
        """Legacy alias for clients still calling /api/live."""
        payload = self._latest_payload()
        _json_response(
            self,
            200,
            {
                "live": payload.get("datapoints", []),
                "health": payload.get("health", {}),
                "network": payload.get("network"),
                "total": payload.get("total", 0),
            },
        )

    def _serve_history(self, query: dict[str, list[str]]) -> None:
        """Legacy alias for clients still calling /api/history."""
        try:
            payload = self._history_payload(query)
        except ValueError as exc:
            _json_response(self, 400, {"error": str(exc)})
            return
        samples = payload["samples"]
        values = [float(s[1]) for s in samples if s[1] is not None]
        stats = None
        if values:
            stats = {
                "min": min(values),
                "avg": sum(values) / len(values),
                "max": max(values),
                "count": len(values),
            }
        _json_response(
            self,
            200,
            {
                "metric": payload["metric"],
                "sensor": payload["sensor"],
                "hours": payload["hours"],
                "samples": samples,
                "stats": stats,
                "returned": payload["returned"],
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
        _json_response(self, 200, self._system_payload())


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
