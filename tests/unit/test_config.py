"""Unit tests for configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from stingray.core.config import load_config
from stingray.core.errors import ConfigError
from stingray.core.models import SensorId


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "app.yaml"
    p.write_text(content, encoding="utf-8")
    return p


class TestLoadConfig:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            load_config(tmp_path / "nope.yaml")

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "mode: [unclosed")
        with pytest.raises(ConfigError):
            load_config(p)

    def test_valid_minimal(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "mode: mock\n")
        cfg = load_config(p)
        assert cfg.mode == "mock"
        assert cfg.api.port == 8080

    def test_rejects_bad_mode(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "mode: turbo\n")
        with pytest.raises(ConfigError):
            load_config(p)

    def test_rejects_negative_interval(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "mode: mock\nsensors:\n  sht45:\n    enabled: true\n    cadence_seconds: -5\n",
        )
        with pytest.raises(ConfigError):
            load_config(p)

    def test_rejects_bad_port(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "mode: mock\napi:\n  port: 99999\n")
        with pytest.raises(ConfigError):
            load_config(p)

    def test_sensor_config_lookup(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "mode: mock\nsensors:\n  sht45:\n    enabled: true\n    cadence_seconds: 3\n",
        )
        cfg = load_config(p)
        from stingray.core.models import SensorId

        sht = cfg.sensor_config(SensorId.SHT45)
        assert sht.enabled is True
        assert sht.cadence_seconds == 3
        # Unconfigured sensor defaults to disabled.
        sps = cfg.sensor_config(SensorId.SPS30)
        assert sps.enabled is False

    def test_enabled_sensors_order(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "mode: mock\nsensors:\n"
            "  sps30:\n    enabled: true\n"
            "  sht45:\n    enabled: true\n"
            "  scd4x:\n    enabled: false\n",
        )
        cfg = load_config(p)
        from stingray.core.models import SensorId

        enabled = cfg.enabled_sensors()
        assert enabled == [SensorId.SHT45, SensorId.SPS30]

    def test_env_override_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = _write(tmp_path, "mode: mock\n")
        monkeypatch.setenv("STINGRAY_MODE", "hardware")
        cfg = load_config(p)
        assert cfg.mode == "hardware"

    def test_env_override_storage_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = _write(tmp_path, "mode: mock\n")
        monkeypatch.setenv("STINGRAY_STORAGE_PATH", "/tmp/custom-parquet")
        cfg = load_config(p)
        assert cfg.storage.path == "/tmp/custom-parquet"

    def test_env_override_pi_thermal_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = _write(tmp_path, "mode: mock\n")
        monkeypatch.setenv("STINGRAY_PI_THERMAL_PATH", "/tmp/cpu-thermal")
        cfg = load_config(p)
        assert cfg.sensor_config(SensorId.PI).thermal_path == "/tmp/cpu-thermal"

    def test_telegram_token_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = _write(tmp_path, "mode: mock\n")
        monkeypatch.setenv("STINGRAY_TELEGRAM_BOT_TOKEN", "secret-token")
        cfg = load_config(p)
        assert cfg.alerts.telegram.bot_token == "secret-token"

    def test_external_context_defaults_disabled(self, tmp_path: Path) -> None:
        cfg = load_config(_write(tmp_path, "mode: mock\n"))
        ext = cfg.summary.external
        assert ext.enabled is False
        assert ext.latitude is None
        assert ext.longitude is None
        assert ext.timeout_seconds == 5.0
        assert ext.weather_url == "https://api.open-meteo.com/v1/forecast"
        assert ext.air_quality_url == "https://air-quality-api.open-meteo.com/v1/air-quality"

    def test_external_context_loads_coordinates(self, tmp_path: Path) -> None:
        cfg = load_config(
            _write(
                tmp_path,
                "mode: mock\nsummary:\n  external:\n"
                "    enabled: true\n    latitude: 17.38\n    longitude: 78.36\n",
            )
        )
        assert cfg.summary.external.enabled is True
        assert cfg.summary.external.latitude == 17.38
        assert cfg.summary.external.longitude == 78.36

    def test_external_context_requires_coordinates(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "mode: mock\nsummary:\n  external:\n    enabled: true\n")
        with pytest.raises(ConfigError):
            load_config(p)

    def test_external_context_rejects_out_of_range(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "mode: mock\nsummary:\n  external:\n"
            "    enabled: true\n    latitude: 100\n    longitude: 78.36\n",
        )
        with pytest.raises(ConfigError):
            load_config(p)

    def test_external_context_coordinates_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = _write(tmp_path, "mode: mock\nsummary:\n  external:\n    enabled: true\n")
        monkeypatch.setenv("STINGRAY_SUMMARY_LATITUDE", "17.38")
        monkeypatch.setenv("STINGRAY_SUMMARY_LONGITUDE", "78.36")
        cfg = load_config(p)
        assert cfg.summary.external.latitude == 17.38
        assert cfg.summary.external.longitude == 78.36

    def test_example_config_loads(self) -> None:
        example = (
            Path(__file__).resolve().parent.parent.parent / "config" / "application.example.yaml"
        )
        cfg = load_config(example)
        assert cfg.mode == "mock"
        assert len(cfg.enabled_sensors()) == 5
