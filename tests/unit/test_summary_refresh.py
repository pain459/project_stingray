"""Unit tests for the on-demand summary trigger and Telegram sending."""

from __future__ import annotations

import json
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from stingray.api.server import ApiState, create_api_server
from stingray.core.config import AppConfig
from stingray.summary_job import send_telegram
from stingray.summary_refresh import SummaryRefresher


def _config(tmp_path: Path) -> AppConfig:
    config = AppConfig()
    config.summary.path = str(tmp_path / "summaries")
    config.summary.refresh_cooldown_seconds = 600
    config.alerts.telegram.enabled = False
    return config


class TestSummaryRefresher:
    def test_first_trigger_starts_run_and_sets_cooldown(self, tmp_path: Path) -> None:
        refresher = SummaryRefresher(_config(tmp_path))
        with patch("stingray.summary_refresh.threading.Thread") as thread_cls:
            status, payload = refresher.trigger()
            assert status == 202
            assert payload["status"] == "started"
            thread_cls.assert_called_once()
        # Simulate worker completion (the mocked thread never releases it).
        refresher._run_lock.release()  # noqa: SLF001 - test controls the mock

        status, payload = refresher.trigger()
        assert status == 429
        assert payload["error"] == "cooldown"
        assert payload["retry_after_seconds"] == 600
        assert payload["next_allowed_at"]

    def test_cooldown_survives_a_new_instance(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        refresher = SummaryRefresher(config)
        with patch("stingray.summary_refresh.threading.Thread"):
            refresher.trigger()
        refresher._run_lock.release()  # noqa: SLF001 - test controls the mock

        restarted = SummaryRefresher(config)
        state = restarted.status()
        assert 590 <= state["cooldown_remaining_seconds"] <= 600
        assert state["busy"] is False

    def test_busy_run_is_rejected(self, tmp_path: Path) -> None:
        refresher = SummaryRefresher(_config(tmp_path))
        refresher._run_lock.acquire()  # noqa: SLF001 - simulate active worker
        try:
            status, payload = refresher.trigger()
            assert status == 409
            assert payload["error"] == "busy"
        finally:
            refresher._run_lock.release()

    def test_status_before_any_trigger(self, tmp_path: Path) -> None:
        state = SummaryRefresher(_config(tmp_path)).status()
        assert state["busy"] is False
        assert state["cooldown_remaining_seconds"] == 0
        assert state["next_allowed_at"] is None


class TestRefreshEndpoint:
    def test_post_refresh_and_latest_status(self, tmp_path: Path) -> None:
        state = ApiState(
            object(),  # type: ignore[arg-type] - scheduler unused by these routes
            str(tmp_path),
            refresher=SummaryRefresher(_config(tmp_path)),
        )
        server = create_api_server("127.0.0.1", 0, state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # Empty summary dir: the worker safely no-ops, so no Ollama call.
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request(
                "POST",
                "/api/summaries/refresh",
                body="{}",
                headers={"Content-Type": "application/json"},
            )
            first = conn.getresponse()
            assert first.status == 202
            first.read()

            deadline = time.monotonic() + 5
            while state.refresher.status()["busy"] and time.monotonic() < deadline:  # type: ignore[union-attr]
                time.sleep(0.05)

            conn.request(
                "POST",
                "/api/summaries/refresh",
                body="{}",
                headers={"Content-Type": "application/json"},
            )
            second = conn.getresponse()
            assert second.status == 429
            second.read()

            conn.request("GET", "/api/summaries/latest")
            latest = conn.getresponse()
            body = json.loads(latest.read().decode("utf-8"))
            assert latest.status == 200
            assert body["summary"] is None
            assert body["refresh"]["cooldown_remaining_seconds"] > 0
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class TestTelegramSender:
    def test_send_uses_html_and_splits_long_text(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        calls: list[dict[str, object]] = []

        class _Response:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"ok":true}'

        def fake_urlopen(request, timeout: float) -> _Response:  # type: ignore[no-untyped-def]
            calls.append(json.loads(request.data.decode("utf-8")))
            return _Response()

        monkeypatch.setattr("stingray.summary_job.urllib.request.urlopen", fake_urlopen)
        text = "\n".join(f"<b>line {i}</b>" for i in range(400))

        send_telegram("fake-token-for-test", "12345", text)

        assert len(calls) > 1
        assert all(call["parse_mode"] == "HTML" for call in calls)
        assert all(call["chat_id"] == "12345" for call in calls)
        assert "(part 1/" in str(calls[0]["text"])
