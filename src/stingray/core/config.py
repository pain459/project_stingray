"""Validated application configuration.

Settings are loaded from a YAML file (``application.yaml``) with
environment-variable overrides for secrets and deployment-specific values.
Invalid configurations fail fast at load time with :class:`ConfigError`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from stingray.core.errors import ConfigError
from stingray.core.models import SensorId

#: Environment variable overrides, checked after YAML values.
_ENV_OVERRIDES: dict[str, str] = {
    "mode": "STINGRAY_MODE",
    "api.host": "STINGRAY_API_HOST",
    "api.port": "STINGRAY_API_PORT",
    "storage.path": "STINGRAY_STORAGE_PATH",
    "alerts.telegram.bot_token": "STINGRAY_TELEGRAM_BOT_TOKEN",
    "alerts.telegram.chat_id": "STINGRAY_TELEGRAM_CHAT_ID",
    "display.timezone": "STINGRAY_DISPLAY_TZ",
    "summary.path": "STINGRAY_SUMMARY_PATH",
    "summary.model": "STINGRAY_SUMMARY_MODEL",
    "sensors.pi.thermal_path": "STINGRAY_PI_THERMAL_PATH",
}


class ApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)


class StorageConfig(BaseModel):
    path: str = "stingray-parquet"
    retention_days: int = Field(default=30, ge=1)
    batch_size: int = Field(default=50, ge=1)


class SensorConfig(BaseModel):
    enabled: bool = True
    i2c_bus: int = Field(default=1, ge=0)
    address: int = Field(default=0, ge=0x00, le=0x7F)
    cadence_seconds: float = Field(default=5.0, gt=0)
    #: Thermal zone read by the Pi host driver; irrelevant to I2C sensors.
    thermal_path: str = "/sys/class/thermal/thermal_zone0/temp"


class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    cooldown_seconds: int = Field(default=300, ge=0)


class ThresholdsConfig(BaseModel):
    pm2_5_ug_m3: float = Field(default=35.0, gt=0)
    co2_ppm: float = Field(default=1000.0, gt=0)
    voc_index: float = Field(default=100.0, gt=0)
    nox_index: float = Field(default=150.0, gt=0)
    rh_high_percent: float = Field(default=65.0, gt=0, lt=100)
    rh_low_percent: float = Field(default=30.0, gt=0, lt=100)
    temperature_high_c: float = Field(default=30.0, gt=0)


class AlertsConfig(BaseModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)


class SummaryConfig(BaseModel):
    """Hourly local-LLM summary generation."""

    enabled: bool = False
    path: str = "stingray-summaries"
    model: str = "qwen2.5:0.5b"
    ollama_url: str = "http://127.0.0.1:11434"
    hours: float = Field(default=1.0, gt=0)
    #: Minimum seconds between UI-triggered summary runs.
    refresh_cooldown_seconds: int = Field(default=600, ge=0)


class DeployConfig(BaseModel):
    """Web-triggered release deployment (Pi deployments only)."""

    enabled: bool = False
    status_path: str = "/opt/stingray/web-deploy-status.json"
    #: Timeout for starting the one-shot systemd unit, not for the deploy itself.
    timeout_seconds: int = Field(default=15, ge=1, le=120)


class DisplayConfig(BaseModel):
    enabled: bool = True
    spi_device: str = "/dev/spidev0.0"
    rotation: int = Field(default=0, ge=0, le=270)
    refresh_seconds: float = Field(default=2.0, gt=0)
    timezone: str = "UTC"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = ""

    @field_validator("level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log level must be one of {sorted(allowed)}")
        return upper


class AppConfig(BaseModel):
    """Top-level application configuration."""

    mode: str = "mock"
    api: ApiConfig = Field(default_factory=ApiConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    sensors: dict[str, SensorConfig] = Field(default_factory=dict)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    summary: SummaryConfig = Field(default_factory=SummaryConfig)
    deploy: DeployConfig = Field(default_factory=DeployConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, value: str) -> str:
        if value not in {"mock", "hardware"}:
            raise ValueError("mode must be 'mock' or 'hardware'")
        return value

    def sensor_config(self, sensor: SensorId) -> SensorConfig:
        """Return config for a sensor, or a disabled default if absent."""
        cfg = self.sensors.get(sensor.value)
        return cfg if cfg is not None else SensorConfig(enabled=False)

    def enabled_sensors(self) -> list[SensorId]:
        """All sensors that are configured and enabled, in canonical order."""
        order = [SensorId.SHT45, SensorId.SCD4X, SensorId.SGP41, SensorId.SPS30, SensorId.PI]
        return [s for s in order if self.sensor_config(s).enabled]


def _apply_env_overrides(data: dict[str, Any]) -> None:
    """Apply environment variable overrides onto the parsed YAML dict."""
    for dotted_key, env_var in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        parts = dotted_key.split(".")
        target = data
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = raw


def load_config(path: str | Path) -> AppConfig:
    """Load and validate configuration from a YAML file.

    Raises:
        ConfigError: if the file is missing, unparseable, or invalid.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file {path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must contain a mapping")
    _apply_env_overrides(data)
    try:
        return AppConfig(**data)
    except Exception as exc:  # pydantic ValidationError
        raise ConfigError(f"invalid configuration: {exc}") from exc
