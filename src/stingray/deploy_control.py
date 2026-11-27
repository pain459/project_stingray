"""Web-triggered release deployment for the Raspberry Pi dashboard.

A deploy restarts ``stingray.service``. Any child process started by the API
would be killed with the service (systemd's default cgroup kill mode), so the
API starts a dedicated one-shot systemd unit instead. That unit runs the same
``/home/ravik/bin/stingray-deploy`` CLI as the polling timer, in its own cgroup.

Only two fixed unit instances are accepted: ``poll`` and ``force``. The final
result is written to a small JSON file by the deploy CLI, so the dashboard can
keep polling while the web service itself is restarting.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stingray.core.config import DeployConfig

log = logging.getLogger("stingray.deploy")

_ALLOWED_ACTIONS = ("poll", "force")
_UNIT_TEMPLATE = "stingray-web-deploy@{action}.service"
_STALE_RUNNING_SECONDS = 900


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_epoch(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class DeployControl:
    """Start and report on web-triggered deploys. Disabled off the Pi."""

    def __init__(self, config: DeployConfig) -> None:
        self._config = config
        self._status_path = Path(config.status_path)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def _command(self, action: str) -> list[str]:
        """Fixed argv; ``action`` is only ever one of two internal constants."""
        if action not in _ALLOWED_ACTIONS:
            raise ValueError(f"unsupported deploy action: {action}")
        return [
            "/usr/bin/sudo",
            "-n",
            "/usr/bin/systemctl",
            "start",
            "--no-block",
            _UNIT_TEMPLATE.format(action=action),
        ]

    def status(self) -> dict[str, Any]:
        """Last deploy state, safe to expose through the API."""
        payload: dict[str, Any] = {
            "configured": self.enabled,
            "state": "idle",
            "action": None,
            "message": "",
            "version": None,
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
        }
        try:
            raw = json.loads(self._status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return payload
        if isinstance(raw, dict):
            payload.update(raw)

        if payload.get("state") == "running":
            started = _parse_epoch(payload.get("started_epoch"))
            if started is not None and time.time() - started > _STALE_RUNNING_SECONDS:
                payload["state"] = "error"
                payload["message"] = (
                    "Deployment status is stale; check journalctl -u stingray-deploy.service"
                )
        return payload

    def trigger(self, *, force: bool) -> tuple[int, dict[str, Any]]:
        """Start a deploy. Returns (HTTP status, response payload)."""
        if not self.enabled:
            return 503, {
                "error": "unavailable",
                "message": "Web deployment is not configured on this host.",
            }

        action = "force" if force else "poll"
        with self._lock:
            if self.status().get("state") == "running":
                return 409, {
                    "error": "busy",
                    "message": "A deployment is already running.",
                    **self.status(),
                }
            argv = self._command(action)
            try:
                result = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self._config.timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.error("could not start deploy unit %s: %s", action, exc)
                return 500, {
                    "error": "start_failed",
                    "message": "Could not start the deployment unit.",
                }
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()[-500:]
                log.error("deploy unit %s failed to start: %s", action, detail)
                return 502, {
                    "error": "start_failed",
                    "message": "Deployment unit rejected the request.",
                    "detail": detail,
                }

        return 202, {
            "status": "started",
            "action": action,
            "at": _iso_now(),
            "message": "Deployment started; the service will restart shortly.",
        }
