"""Optional outdoor weather and air-quality context for hourly summaries.

The Pi already uses ``urllib`` for Telegram, so this module adds no dependency
and no on-Pi build step. Every request is best-effort: a timeout or a provider
error leaves the indoor-only summary intact.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from stingray.core.config import ExternalContextConfig

log = logging.getLogger(__name__)

#: Required attribution for Open-Meteo data (CC BY 4.0; AQI from CAMS).
ATTRIBUTION = "Open-Meteo/CAMS"


def _fmt(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}"


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def us_aqi_category(value: float) -> str:
    """Return the official US AQI category label for a numeric index."""
    if value <= 50:
        return "Good"
    if value <= 100:
        return "Moderate"
    if value <= 150:
        return "Unhealthy for sensitive groups"
    if value <= 200:
        return "Unhealthy"
    if value <= 300:
        return "Very unhealthy"
    return "Hazardous"


@dataclass(frozen=True)
class OutdoorContext:
    """A compact modelled outdoor snapshot, or an empty best-effort result."""

    temperature_c: float | None = None
    precipitation_mm: float | None = None
    wind_kmh: float | None = None
    us_aqi: float | None = None
    pm2_5_ug_m3: float | None = None
    observed_at: str = ""
    error: str = ""
    source: str = ATTRIBUTION

    @classmethod
    def empty(cls, error: str = "") -> OutdoorContext:
        return cls(error=error)

    @property
    def has_data(self) -> bool:
        return any(
            value is not None
            for value in (
                self.temperature_c,
                self.precipitation_mm,
                self.wind_kmh,
                self.us_aqi,
                self.pm2_5_ug_m3,
            )
        )

    def to_jsonable(self) -> dict[str, Any]:
        return {**asdict(self), "available": self.has_data}

    def fact_lines(self) -> list[str]:
        """Short plain-text facts for the tiny-model prompt."""
        lines: list[str] = []
        if self.temperature_c is not None:
            lines.append(f"- outdoor_temperature_c: {_fmt(self.temperature_c)} {ATTRIBUTION} °C")
        if self.precipitation_mm is not None:
            lines.append(f"- outdoor_precipitation_mm: {_fmt(self.precipitation_mm)} mm")
        if self.wind_kmh is not None:
            lines.append(f"- outdoor_wind_kmh: {_fmt(self.wind_kmh)} km/h")
        if self.us_aqi is not None:
            category = us_aqi_category(self.us_aqi)
            lines.append(f"- outdoor_us_aqi: {_fmt(self.us_aqi, 0)} ({category})")
        if self.pm2_5_ug_m3 is not None:
            lines.append(f"- outdoor_pm2_5_ug_m3: {_fmt(self.pm2_5_ug_m3)} µg/m³")
        return lines

    def footer_line(self) -> str:
        """One compact Telegram line; never a new metric row or section."""
        parts: list[str] = []
        if self.temperature_c is not None:
            parts.append(f"{_fmt(self.temperature_c)}°C")
        if self.precipitation_mm is not None:
            parts.append(f"rain {_fmt(self.precipitation_mm)} mm")
        if self.wind_kmh is not None:
            parts.append(f"wind {_fmt(self.wind_kmh)} km/h")
        if self.us_aqi is not None:
            parts.append(f"US AQI {_fmt(self.us_aqi, 0)}")
        if self.pm2_5_ug_m3 is not None:
            parts.append(f"PM2.5 {_fmt(self.pm2_5_ug_m3)} µg/m³")
        if not parts:
            return ""
        return f"Nearby outdoor (model): {' · '.join(parts)} · {ATTRIBUTION}"


def _fetch_json(url: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={"User-Agent": "stingray-summary/1.0"},
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URLs
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("provider returned a non-object payload")
    return payload


def _current(payload: dict[str, Any], key: str) -> float | None:
    current = payload.get("current")
    if not isinstance(current, dict):
        return None
    return _finite(current.get(key))


def fetch_outdoor_context(config: ExternalContextConfig) -> OutdoorContext:
    """Fetch weather and AQI context; return partial or empty data on error."""
    if not config.enabled or config.latitude is None or config.longitude is None:
        return OutdoorContext.empty()

    common = {
        "latitude": config.latitude,
        "longitude": config.longitude,
        "timezone": "auto",
    }
    weather: dict[str, Any] = {}
    air: dict[str, Any] = {}
    errors: list[str] = []

    try:
        weather = _fetch_json(
            config.weather_url,
            {
                **common,
                "current": "temperature_2m,precipitation,wind_speed_10m",
            },
            config.timeout_seconds,
        )
    except (OSError, ValueError) as exc:
        errors.append(f"weather:{type(exc).__name__}")

    try:
        air = _fetch_json(
            config.air_quality_url,
            {**common, "current": "us_aqi,pm2_5"},
            config.timeout_seconds,
        )
    except (OSError, ValueError) as exc:
        errors.append(f"air:{type(exc).__name__}")

    current = weather.get("current")
    observed_at = ""
    if isinstance(current, dict) and isinstance(current.get("time"), str):
        observed_at = current["time"]
    elif isinstance(air.get("current"), dict) and isinstance(air["current"].get("time"), str):
        observed_at = air["current"]["time"]

    context = OutdoorContext(
        temperature_c=_current(weather, "temperature_2m"),
        precipitation_mm=_current(weather, "precipitation"),
        wind_kmh=_current(weather, "wind_speed_10m"),
        us_aqi=_current(air, "us_aqi"),
        pm2_5_ug_m3=_current(air, "pm2_5"),
        observed_at=observed_at,
        error=",".join(errors),
    )
    if errors:
        log.warning("outdoor summary context unavailable: %s", ", ".join(errors))
    return context
