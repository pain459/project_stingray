"""Footer behaviour: version, host IP, last sample time and quiet redraws."""

from __future__ import annotations

import time
from datetime import datetime

from PIL import ImageDraw

from stingray.display import dashboard as dashboard_module
from stingray.display.dashboard import DisplayDashboard, SensorSnapshot
from stingray.display.layout import LAYOUT


class FakeDriver:
    """Records which display regions were written."""

    def __init__(self) -> None:
        self.regions: list[tuple[int, int, int, int]] = []

    def set_address_window(self, x: int, y: int, width: int, height: int) -> None:
        self.regions.append((x, y, width, height))

    def _write_cmd(self, command: int) -> None:
        pass

    def _write_data(self, data: bytes) -> None:
        pass

    def fill_screen(self, color: int) -> None:
        pass


def _snapshot(timestamp: float, **kwargs: object) -> SensorSnapshot:
    return SensorSnapshot(timestamp=timestamp, **kwargs)  # type: ignore[arg-type]


def test_footer_state_uses_ip_and_sample_time() -> None:
    driver = FakeDriver()
    dashboard = DisplayDashboard(driver, app_version="0.2.0")  # type: ignore[arg-type]
    timestamp = time.time()

    state = dashboard._build_state(_snapshot(timestamp, ip_address="192.168.68.53"))

    assert dashboard.app_version == "0.2.0"
    assert state.ip_address == "192.168.68.53"
    assert state.last_updated == datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
    assert not hasattr(state, "wifi_rssi")


def test_footer_text_shows_version_ip_and_last_updated(monkeypatch) -> None:
    dashboard = DisplayDashboard(FakeDriver(), app_version="0.2.0")  # type: ignore[arg-type]
    timestamp = time.time()
    state = dashboard._build_state(_snapshot(timestamp, ip_address="192.168.68.53"))

    drawn: list[str] = []
    centered: list[str] = []
    original_text = ImageDraw.ImageDraw.text

    def capture_text(self, xy, text, *args, **kwargs):  # type: ignore[no-untyped-def]
        drawn.append(text)
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    monkeypatch.setattr(
        dashboard_module,
        "draw_centered_text",
        lambda draw, text, rect, font, fill: centered.append(text),
    )

    dashboard._render_footer(state)

    assert "stingray v0.2.0" in drawn
    assert centered[0] == "192.168.68.53"
    assert centered[1].startswith("Upd ")


def test_footer_only_redraw_when_only_network_fact_changes() -> None:
    driver = FakeDriver()
    dashboard = DisplayDashboard(driver, app_version="0.2.0")  # type: ignore[arg-type]
    timestamp = time.time()

    dashboard.render(_snapshot(timestamp, ip_address="192.168.68.53"))
    assert len(driver.regions) == len(LAYOUT)

    driver.regions.clear()
    dashboard.render(_snapshot(timestamp, ip_address="192.168.68.54"))

    footer = LAYOUT["footer"]
    assert driver.regions == [(footer.x, footer.y, footer.width, footer.height)]


def test_sensor_value_change_still_redraws_every_region() -> None:
    driver = FakeDriver()
    dashboard = DisplayDashboard(driver, app_version="0.2.0")  # type: ignore[arg-type]
    timestamp = time.time()

    dashboard.render(_snapshot(timestamp, pm25=12.5, ip_address="192.168.68.53"))
    driver.regions.clear()
    dashboard.render(_snapshot(timestamp, pm25=18.0, ip_address="192.168.68.53"))

    assert len(driver.regions) == len(LAYOUT)
