"""On-demand summary generation for the web dashboard.

A button press runs the same real-data pipeline as the hourly systemd job:
aggregate the trailing window from Parquet, apply deterministic rules, let the
local Ollama model phrase the result, save it, and mirror it to Telegram.

Cooldown is enforced here, not in the browser. A small timestamp file in the
summary directory survives API restarts, and a non-blocking lock ensures only
one on-demand run is active at a time.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stingray.core.config import AppConfig
from stingray.summary import Summary, telegram_html
from stingray.summary_job import aggregate_facts, generate, send_telegram
from stingray.summary_store import SummaryStore

log = logging.getLogger("stingray.summary.refresh")

_STATE_FILE = ".refresh_state.json"


class SummaryRefresher:
    """Trigger and rate-limit on-demand summaries from the dashboard."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._cooldown = config.summary.refresh_cooldown_seconds
        self._state_path = Path(config.summary.path) / _STATE_FILE
        self._state_lock = threading.Lock()
        self._run_lock = threading.Lock()

    # -- cooldown state -------------------------------------------------
    def _last_trigger(self) -> float:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return 0.0
        value = payload.get("last_trigger_epoch") if isinstance(payload, dict) else None
        try:
            return float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _write_trigger(self, when: float) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self._state_path.parent, prefix=".refresh_state.", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with open(fd, "w", encoding="utf-8") as handle:
                json.dump({"last_trigger_epoch": when}, handle)
                handle.write("\n")
            tmp.replace(self._state_path)
        finally:
            tmp.unlink(missing_ok=True)

    # -- public API -----------------------------------------------------
    def status(self) -> dict[str, Any]:
        """Current cooldown state, safe to expose through the API."""
        last = self._last_trigger()
        remaining = max(0.0, self._cooldown - (time.time() - last)) if last else 0.0
        return {
            "cooldown_seconds": self._cooldown,
            "cooldown_remaining_seconds": int(remaining + 0.999),
            "next_allowed_at": (
                datetime.fromtimestamp(last + self._cooldown, tz=UTC).isoformat()
                if remaining > 0
                else None
            ),
            "busy": self._run_lock.locked(),
        }

    def trigger(self) -> tuple[int, dict[str, Any]]:
        """Reserve a run if allowed. Returns (HTTP status, response payload)."""
        state = self.status()
        if state["busy"]:
            return 409, {
                "error": "busy",
                "message": "A summary is already being generated.",
                **state,
            }
        if state["cooldown_remaining_seconds"] > 0:
            return 429, {
                "error": "cooldown",
                "message": "Summary refresh is cooling down.",
                "retry_after_seconds": state["cooldown_remaining_seconds"],
                **state,
            }

        with self._state_lock:
            # Re-check under the lock so simultaneous clicks cannot both pass.
            state = self.status()
            if state["busy"]:
                return 409, {
                    "error": "busy",
                    "message": "A summary is already being generated.",
                    **state,
                }
            if state["cooldown_remaining_seconds"] > 0:
                return 429, {
                    "error": "cooldown",
                    "message": "Summary refresh is cooling down.",
                    "retry_after_seconds": state["cooldown_remaining_seconds"],
                    **state,
                }
            if not self._run_lock.acquire(blocking=False):
                return 409, {
                    "error": "busy",
                    "message": "A summary is already being generated.",
                    **self.status(),
                }
            self._write_trigger(time.time())

        threading.Thread(target=self._run, name="stingray-summary-refresh", daemon=True).start()
        return 202, {"status": "started", **self.status()}

    # -- worker ---------------------------------------------------------
    def _run(self) -> None:
        try:
            generated_at = datetime.now(UTC)
            facts = aggregate_facts(
                self._config.storage.path,
                generated_at=generated_at,
                hours=self._config.summary.hours,
            )
            if facts.sample_count == 0:
                log.warning("on-demand summary skipped: no sensor samples in window")
                return
            summary = generate(
                facts,
                self._config,
                model=self._config.summary.model,
                source="manual",
            )
            json_path, _ = SummaryStore(self._config.summary.path).save(summary)
            log.info("on-demand summary saved: %s", json_path)
            self._send_telegram(summary)
        except Exception:  # noqa: BLE001 - worker must never die silently
            log.exception("on-demand summary failed")
        finally:
            self._run_lock.release()

    def _send_telegram(self, summary: Summary) -> None:
        if not self._config.alerts.telegram.enabled:
            return
        token = os.environ.get("STINGRAY_TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("STINGRAY_TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            log.warning("Telegram skipped for on-demand summary: credentials unavailable")
            return
        try:
            send_telegram(token, chat_id, telegram_html(summary))
        except RuntimeError as exc:
            log.warning("Telegram send failed for on-demand summary: %s", exc)
        else:
            log.info("Telegram message sent for on-demand summary")
