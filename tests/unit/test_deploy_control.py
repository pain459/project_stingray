"""Unit tests for the web-triggered release deployment controls."""

from __future__ import annotations

import json
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from stingray.api.server import ApiState, create_api_server
from stingray.core.config import AppConfig, DeployConfig
from stingray.deploy_control import DeployControl


def _config(tmp_path: Path, *, enabled: bool = True) -> DeployConfig:
    return DeployConfig(
        enabled=enabled,
        status_path=str(tmp_path / "web-deploy-status.json"),
    )


def _ok(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
    return CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_deploy_is_disabled_by_default() -> None:
    assert DeployConfig().enabled is False
    assert AppConfig().deploy.enabled is False


def test_disabled_control_rejects_trigger(tmp_path: Path) -> None:
    control = DeployControl(_config(tmp_path, enabled=False))

    status, payload = control.trigger(force=False)

    assert status == 503
    assert payload["error"] == "unavailable"
    assert control.status()["configured"] is False


def test_trigger_uses_only_fixed_systemctl_argv(tmp_path: Path) -> None:
    control = DeployControl(_config(tmp_path))

    with patch("stingray.deploy_control.subprocess.run", side_effect=_ok) as run:
        status, payload = control.trigger(force=False)

    assert status == 202
    assert payload["action"] == "poll"
    argv = run.call_args.args[0]
    assert argv == [
        "/usr/bin/sudo",
        "-n",
        "/usr/bin/systemctl",
        "start",
        "--no-block",
        "stingray-web-deploy@poll.service",
    ]


def test_force_trigger_selects_the_force_unit(tmp_path: Path) -> None:
    control = DeployControl(_config(tmp_path))

    with patch("stingray.deploy_control.subprocess.run", side_effect=_ok) as run:
        status, payload = control.trigger(force=True)

    assert status == 202
    assert payload["action"] == "force"
    assert run.call_args.args[0][-1] == "stingray-web-deploy@force.service"


def test_start_failure_is_reported(tmp_path: Path) -> None:
    control = DeployControl(_config(tmp_path))
    failed = CompletedProcess(args=[], returncode=1, stdout="", stderr="access denied")

    with patch("stingray.deploy_control.subprocess.run", return_value=failed):
        status, payload = control.trigger(force=False)

    assert status == 502
    assert payload["error"] == "start_failed"
    assert "access denied" in payload["detail"]


def test_running_status_blocks_another_trigger(tmp_path: Path) -> None:
    control = DeployControl(_config(tmp_path))
    status_file = Path(_config(tmp_path).status_path)
    status_file.write_text(
        json.dumps(
            {
                "state": "running",
                "action": "poll",
                "started_epoch": time.time(),
            }
        ),
        encoding="utf-8",
    )

    with patch("stingray.deploy_control.subprocess.run", side_effect=_ok) as run:
        status, payload = control.trigger(force=True)

    assert status == 409
    assert payload["error"] == "busy"
    run.assert_not_called()


def test_stale_running_status_is_reported_as_error(tmp_path: Path) -> None:
    control = DeployControl(_config(tmp_path))
    status_file = Path(_config(tmp_path).status_path)
    status_file.write_text(
        json.dumps(
            {
                "state": "running",
                "action": "poll",
                "started_epoch": time.time() - 1000,
            }
        ),
        encoding="utf-8",
    )

    result = control.status()

    assert result["state"] == "error"
    assert "stale" in result["message"]


def test_final_status_is_exposed(tmp_path: Path) -> None:
    control = DeployControl(_config(tmp_path))
    status_file = Path(_config(tmp_path).status_path)
    status_file.write_text(
        json.dumps(
            {
                "state": "success",
                "action": "force",
                "version": "0.2.1",
                "message": "Deployment completed",
                "exit_code": 0,
                "started_at": "2026-11-27T09:00:00+00:00",
                "finished_at": "2026-11-27T09:01:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    result = control.status()

    assert result["state"] == "success"
    assert result["version"] == "0.2.1"
    assert result["exit_code"] == 0


class TestDeployEndpoints:
    def test_post_and_status_routes(self, tmp_path: Path) -> None:
        control = DeployControl(_config(tmp_path))
        state = ApiState(object(), str(tmp_path), deploy=control)  # type: ignore[arg-type]
        server = create_api_server("127.0.0.1", 0, state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            with patch("stingray.deploy_control.subprocess.run", side_effect=_ok):
                conn.request("POST", "/api/deploy", body="{}")
                first = conn.getresponse()
                assert first.status == 202
                assert json.loads(first.read().decode("utf-8"))["action"] == "poll"

                conn.request("POST", "/api/deploy/force", body="{}")
                second = conn.getresponse()
                assert second.status == 202
                assert json.loads(second.read().decode("utf-8"))["action"] == "force"

            conn.request("GET", "/api/deploy/status")
            latest = conn.getresponse()
            assert latest.status == 200
            assert json.loads(latest.read().decode("utf-8"))["configured"] is True

            conn.request("POST", "/api/unknown", body="{}")
            assert conn.getresponse().status == 404
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_routes_when_deploy_is_not_configured(self, tmp_path: Path) -> None:
        state = ApiState(object(), str(tmp_path))  # type: ignore[arg-type]
        server = create_api_server("127.0.0.1", 0, state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request("GET", "/api/deploy/status")
            status = conn.getresponse()
            assert status.status == 200
            assert json.loads(status.read().decode("utf-8"))["configured"] is False

            conn.request("POST", "/api/deploy", body="{}")
            trigger = conn.getresponse()
            assert trigger.status == 503
            trigger.read()
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
