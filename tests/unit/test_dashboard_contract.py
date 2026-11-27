"""Static contract checks for the browser-owned dashboard data pull."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "src" / "stingray" / "api" / "static"


def _js(name: str) -> str:
    return (_STATIC / "js" / name).read_text(encoding="utf-8")


def test_store_owns_datapoint_fetches() -> None:
    store = _js("store.js")
    assert "/api/datapoints?mode=latest" in store
    assert "/api/datapoints?mode=history" in store
    assert "If-None-Match" in store
    assert "BroadcastChannel" in store
    assert "document.hidden" in store
    assert "setInterval(tick" in store


def test_history_url_appends_to_existing_query_string() -> None:
    store = _js("store.js")
    assert "HISTORY_URL + '&metric='" in store
    assert "HISTORY_URL + '?metric='" not in store


def test_widgets_do_not_fetch_datapoints_directly() -> None:
    for name in ("app.js", "live.js", "history.js"):
        assert "fetch(" not in _js(name), name
    assert "/api/live" not in _js("live.js")
    assert "/api/history" not in _js("history.js")
    assert "DP.subscribe" in _js("live.js")
    assert "DP.subscribe" in _js("history.js")


def test_slow_state_uses_snapshot_subscription() -> None:
    summary = _js("summary.js")
    deploy = _js("deploy.js")
    assert "DP.subscribe" in summary
    assert "DP.subscribe" in deploy
    assert "/api/datapoints" not in summary
    assert "/api/datapoints" not in deploy


def test_script_tags_are_ordered_and_cache_bumped() -> None:
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    scripts = [line.split('"')[1] for line in html.splitlines() if '<script src="js/' in line]
    assert scripts == [
        "js/store.js?v=2",
        "js/chart.js?v=7",
        "js/history.js?v=12",
        "js/live.js?v=12",
        "js/summary.js?v=11",
        "js/deploy.js?v=3",
        "js/app.js?v=11",
    ]


def test_deploy_panel_has_read_only_update_check() -> None:
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="deployCheck"' in html
    assert 'id="deployUpdate"' in html
    assert "<details" in html and "About deploy actions" in html

    deploy = _js("deploy.js")
    assert "api.github.com/repos/pain459/project_stingray/releases/latest" in deploy
    assert "window.checkDeployUpdates = checkUpdates" in deploy
    assert "deployCheck" in deploy
