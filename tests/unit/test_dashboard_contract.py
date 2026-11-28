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


def test_summary_widget_renders_api_min_max_and_labels_run_source() -> None:
    summary = _js("summary.js")
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    css = (_STATIC / "css/main.css").read_text(encoding="utf-8")

    assert "m.min" in summary
    assert "m.max" in summary
    assert "value === null || value === undefined" in summary
    assert "summary.source === 'manual' ? 'Manual run' : 'Hourly run'" in summary
    assert "if (!summary) summary = lastSummary" in summary

    for element_id in ("summaryMeta", "summaryGenerated", "summarySource", "summaryModel"):
        assert f'id="{element_id}"' in html
    assert ".summary-meta-source.manual" in css


def test_live_renders_gas_quality_states_and_trends() -> None:
    live = _js("live.js")
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    css = (_STATIC / "css/main.css").read_text(encoding="utf-8")

    for element_id in ("vocstate", "vocstate2", "noxstate", "voctrend", "noxtrend"):
        assert f'id="{element_id}"' in html
    assert "qualityLabel(voc, 'voc_index')" in live
    assert "qualityLabel(nox, 'nox_index')" in live
    assert "setQuality('vocstate', vocQuality)" in live
    assert "setQuality('vocstate2', vocQuality)" in live
    assert "setQuality('noxstate', noxQuality)" in live
    assert "window.DP.history('voc_index', TREND_HOURS)" in live
    assert "window.DP.history('nox_index', TREND_HOURS)" in live
    assert "setText('voctrend', trendText('voc_index'" in live
    assert "setText('noxtrend', trendText('nox_index'" in live
    assert ".unit.quality--good" in css
    assert ".unit.quality--warn" in css
    assert ".unit.quality--bad" in css


def test_script_tags_are_ordered_and_cache_bumped() -> None:
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    scripts = [line.split('"')[1] for line in html.splitlines() if '<script src="js/' in line]
    assert scripts == [
        "js/store.js?v=2",
        "js/chart.js?v=8",
        "js/history.js?v=13",
        "js/live.js?v=14",
        "js/summary.js?v=12",
        "js/deploy.js?v=3",
        "js/app.js?v=12",
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


def test_clock_is_browser_local() -> None:
    app = _js("app.js")
    live = _js("live.js")
    assert "window.setInterval(updateClock, 1000)" in app
    assert "toLocaleTimeString([], TIME_FORMAT)" in app
    assert "setText('dt'" not in live
    assert "'Device time: '" in live


def test_refresh_writes_only_changed_text() -> None:
    live = _js("live.js")
    history = _js("history.js")
    assert "if (el.textContent !== next)" in live
    assert "if (el.className !== className)" in live
    assert "if (el.textContent !== next)" in history


def test_chart_uses_double_buffer() -> None:
    chart = _js("chart.js")
    assert "document.createElement('canvas')" in chart
    assert "if (canvas.width !== w || canvas.height !== h)" in chart
    assert "visible.drawImage(buffer, 0, 0)" in chart


def test_neon_logo_replaces_letter_and_gradients_are_removed() -> None:
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    css = (_STATIC / "css/main.css").read_text(encoding="utf-8")
    assert 'src="assets/stingray-logo.png"' in html
    assert "gradient(" not in css


def test_static_asset_route_serves_the_logo() -> None:
    server = (_ROOT / "src" / "stingray" / "api" / "server.py").read_text(encoding="utf-8")
    assert 'path.startswith("/assets/")' in server
    assert '".png": "image/png"' in server
