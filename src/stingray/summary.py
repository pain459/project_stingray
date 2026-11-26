"""Pure logic for the hourly air-quality summary.

The deterministic rule layer owns every threshold and recommendation. The
local LLM is only allowed to rephrase those findings; it never invents sensor
values, thresholds, actions, or medical claims.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from stingray.core.config import ThresholdsConfig

#: Tokens that contain digits but are labels, not measurement values.
_LABEL_TOKENS = (
    "PM1.0",
    "PM2.5",
    "PM4.0",
    "PM10",
    "CO2",
    "SHT4x",
    "SHT45",
    "SGP41",
    "SPS30",
    "NOx",
    "ug/m3",
    "ug/m³",
    "µg/m³",
    "degC",
)

_FORBIDDEN_CLAIMS = (
    "diagnos",
    "disease",
    "hospital",
    "emergency",
    "doctor",
    "medication",
    "prescription",
    "covid",
)

#: Metric order, display labels, and decimal places for Telegram values.
_TELEGRAM_METRICS = (
    ("pm2_5_ug_m3", "PM2.5", 1),
    ("co2_ppm", "CO2", 0),
    ("voc_index", "VOC index", 0),
    ("nox_index", "NOx index", 0),
    ("temperature_c", "Temperature", 1),
    ("relative_humidity_percent", "Humidity", 1),
    ("tps_um", "TPS", 2),
)


@dataclass(frozen=True)
class MetricFacts:
    """Compact aggregate for one metric over one time window."""

    metric: str
    unit: str
    count: int
    avg: float
    minimum: float
    maximum: float
    first: float
    last: float
    last_seen: datetime
    previous_avg: float | None = None

    @property
    def change_pct(self) -> float | None:
        if self.first == 0:
            return None
        return (self.last - self.first) / abs(self.first) * 100.0

    @property
    def change_vs_previous_pct(self) -> float | None:
        """Change in this hour's average versus the previous hour's average."""
        if self.previous_avg is None or self.previous_avg == 0:
            return None
        return (self.avg - self.previous_avg) / abs(self.previous_avg) * 100.0


@dataclass(frozen=True)
class HourlyFacts:
    """All numerical facts handed to the rule layer and the LLM."""

    generated_at: datetime
    window_start: datetime
    window_end: datetime
    sample_count: int
    metrics: dict[str, MetricFacts] = field(default_factory=dict)
    previous_window_start: datetime | None = None
    previous_window_end: datetime | None = None
    previous_sample_count: int = 0

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "sample_count": self.sample_count,
            "previous_window_start": (
                self.previous_window_start.isoformat() if self.previous_window_start else None
            ),
            "previous_window_end": (
                self.previous_window_end.isoformat() if self.previous_window_end else None
            ),
            "previous_sample_count": self.previous_sample_count,
            "metrics": {
                name: {
                    "unit": f.unit,
                    "count": f.count,
                    "avg": round(f.avg, 3),
                    "min": round(f.minimum, 3),
                    "max": round(f.maximum, 3),
                    "first": round(f.first, 3),
                    "last": round(f.last, 3),
                    "change_pct": None if f.change_pct is None else round(f.change_pct, 1),
                    "previous_avg": (None if f.previous_avg is None else round(f.previous_avg, 3)),
                    "change_vs_previous_pct": (
                        None
                        if f.change_vs_previous_pct is None
                        else round(f.change_vs_previous_pct, 1)
                    ),
                    "last_seen": f.last_seen.isoformat(),
                }
                for name, f in sorted(self.metrics.items())
            },
        }


@dataclass(frozen=True)
class Finding:
    """A deterministic, rule-derived observation and recommended action."""

    code: str
    severity: str
    message: str
    action: str

    def to_jsonable(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class Summary:
    """Final hourly summary ready for storage, API, and Telegram."""

    headline: str
    text: str
    actions: list[str]
    findings: list[Finding]
    facts: HourlyFacts
    model: str
    model_used: bool
    generated_at: datetime
    fallback_reason: str = ""
    model_metrics: dict[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "text": self.text,
            "actions": list(self.actions),
            "findings": [f.to_jsonable() for f in self.findings],
            "facts": self.facts.to_jsonable(),
            "model": self.model,
            "model_used": self.model_used,
            "generated_at": self.generated_at.isoformat(),
            "fallback_reason": self.fallback_reason,
            "model_metrics": dict(self.model_metrics),
        }


def _fmt(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}"


def derive_findings(facts: HourlyFacts, thresholds: ThresholdsConfig) -> list[Finding]:
    """Apply configured thresholds and trends. No LLM input is trusted here."""
    findings: list[Finding] = []

    def add(code: str, severity: str, message: str, action: str) -> None:
        findings.append(Finding(code, severity, message, action))

    pm = facts.metrics.get("pm2_5_ug_m3")
    if pm is not None and pm.avg >= thresholds.pm2_5_ug_m3:
        severity = "critical" if pm.avg >= 2 * thresholds.pm2_5_ug_m3 else "warning"
        add(
            "pm25_elevated",
            severity,
            "PM2.5 averaged "
            f"{_fmt(pm.avg)} ug/m3, above the {_fmt(thresholds.pm2_5_ug_m3)} ug/m3 guide.",
            "Reduce cooking, frying, candles, and aerosol sprays until particles settle.",
        )

    co2 = facts.metrics.get("co2_ppm")
    if co2 is not None and co2.avg >= thresholds.co2_ppm:
        add(
            "co2_elevated",
            "warning",
            "CO2 averaged "
            f"{_fmt(co2.avg, 0)} ppm, above the {_fmt(thresholds.co2_ppm, 0)} ppm guide.",
            "Open a door or window for a few minutes, or increase fresh-air ventilation.",
        )

    voc = facts.metrics.get("voc_index")
    if voc is not None and voc.avg >= thresholds.voc_index:
        add(
            "voc_elevated",
            "warning",
            "VOC index averaged "
            f"{_fmt(voc.avg, 0)}, above the {_fmt(thresholds.voc_index, 0)} guide.",
            "Limit solvents, paints, strong cleaners, and high-heat cooking "
            "while the index settles.",
        )

    nox = facts.metrics.get("nox_index")
    if nox is not None and nox.avg >= thresholds.nox_index:
        add(
            "nox_elevated",
            "warning",
            "NOx index averaged "
            f"{_fmt(nox.avg, 0)}, above the {_fmt(thresholds.nox_index, 0)} guide.",
            "Check combustion sources such as a gas stove, fireplace, or generator and ventilate.",
        )

    rh = facts.metrics.get("relative_humidity_percent")
    if rh is not None and rh.avg >= thresholds.rh_high_percent:
        add(
            "humidity_high",
            "info",
            "Humidity averaged "
            f"{_fmt(rh.avg)}%, above the {_fmt(thresholds.rh_high_percent)}% guide.",
            "Ventilate after cooking or showering and check for condensation or moisture sources.",
        )
    elif rh is not None and rh.avg <= thresholds.rh_low_percent:
        add(
            "humidity_low",
            "info",
            "Humidity averaged "
            f"{_fmt(rh.avg)}%, below the {_fmt(thresholds.rh_low_percent)}% guide.",
            "Consider humidifying the room; very dry air can irritate airways.",
        )

    temp = facts.metrics.get("temperature_c")
    if temp is not None and temp.avg >= thresholds.temperature_high_c:
        add(
            "temperature_high",
            "info",
            "Temperature averaged "
            f"{_fmt(temp.avg)} C, above the {_fmt(thresholds.temperature_high_c)} C guide.",
            "Reduce heat-producing activities and increase ventilation if the room stays warm.",
        )

    for metric, label in (("pm2_5_ug_m3", "PM2.5"), ("voc_index", "VOC"), ("nox_index", "NOx")):
        f = facts.metrics.get(metric)
        if f is None or f.change_pct is None:
            continue
        if abs(f.change_pct) >= 25.0:
            direction = "rising" if f.change_pct > 0 else "falling"
            add(
                f"{metric}_trend",
                "info",
                f"{label} was {direction} across the window ({f.change_pct:+.0f}%).",
                "Keep the current ventilation plan under review over the next hour.",
            )

    for metric, label in (
        ("pm2_5_ug_m3", "PM2.5"),
        ("co2_ppm", "CO2"),
        ("voc_index", "VOC"),
        ("nox_index", "NOx"),
    ):
        f = facts.metrics.get(metric)
        if f is None or f.previous_avg is None or f.change_vs_previous_pct is None:
            continue
        pct = f.change_vs_previous_pct
        if abs(pct) < 25.0:
            continue
        direction = "up" if pct > 0 else "down"
        digits = 0 if metric == "co2_ppm" else 1
        add(
            f"{metric}_hour_change",
            "info",
            f"{label} averaged {_fmt(f.avg, digits)} {f.unit} this hour, {direction} "
            f"{abs(pct):.0f}% from {_fmt(f.previous_avg, digits)} {f.unit} last hour.",
            "Track the hour-to-hour change and adjust ventilation if it continues.",
        )

    stale = [
        name
        for name, f in sorted(facts.metrics.items())
        if (facts.window_end - f.last_seen).total_seconds() > 900
    ]
    if stale:
        add(
            "stale_metrics",
            "warning",
            f"No fresh samples in the final 15 minutes for: {', '.join(stale)}.",
            "Check the sensor service and I2C wiring before relying on this summary.",
        )

    if not findings:
        add(
            "all_clear",
            "info",
            "All tracked metrics stayed within the configured guides.",
            "No change needed; continue normal ventilation and household routines.",
        )
    return findings


def build_prompt(facts: HourlyFacts, findings: list[Finding]) -> str:
    """Build a short, fact-only prompt that tiny models can follow reliably."""
    keys = (
        "pm2_5_ug_m3",
        "co2_ppm",
        "voc_index",
        "nox_index",
        "temperature_c",
        "relative_humidity_percent",
        "tps_um",
    )
    lines = ["Facts (averages over the last hour):"]
    for key in keys:
        f = facts.metrics.get(key)
        if f is None:
            continue
        lines.append(f"- {key}: avg {_fmt(f.avg)}, peak {_fmt(f.maximum)} {f.unit}")
    comparisons: list[str] = []
    for key in keys:
        f = facts.metrics.get(key)
        if f is None or f.previous_avg is None or f.change_vs_previous_pct is None:
            continue
        comparisons.append(
            f"- {key}: last hour avg {_fmt(f.previous_avg)}, this hour "
            f"{_fmt(f.avg)} ({f.change_vs_previous_pct:+.0f}%)"
        )
    if comparisons:
        lines.append("Compared with previous hour:")
        lines.extend(comparisons)
    lines.append("Findings:")
    for finding in findings:
        lines.append(f"- {finding.message}")
    lines.extend(
        [
            "Write a calm indoor air-quality update.",
            "Use only these facts and findings. "
            "Do not add numbers, medical claims, or outdoor claims.",
            "Reply with exactly this JSON shape and nothing else:",
            '{"headline":"short headline","summary":"two short sentences"}',
        ]
    )
    return "\n".join(lines)


def _allowed_number_tokens(facts: HourlyFacts, findings: list[Finding] | None = None) -> set[str]:
    tokens: set[str] = {str(int(facts.window_end.timestamp())), "1"}
    for f in facts.metrics.values():
        values = [f.avg, f.minimum, f.maximum, f.first, f.last, f.change_pct or 0.0]
        if f.previous_avg is not None:
            values.append(f.previous_avg)
        if f.change_vs_previous_pct is not None:
            values.append(f.change_vs_previous_pct)
        for value in values:
            for digits in (0, 1, 2, 3):
                tokens.add(_fmt(value, digits))
            tokens.add(str(int(round(value))))
    for finding in findings or []:
        for text in (finding.message, finding.action):
            for token in re.findall(r"\d+(?:\.\d+)?", text):
                tokens.add(token)
                try:
                    value = float(token)
                except ValueError:
                    continue
                for digits in (0, 1, 2, 3):
                    tokens.add(_fmt(value, digits))
    return tokens


def _unapproved_numbers(text: str, allowed: set[str]) -> list[str]:
    cleaned = text
    for token in _LABEL_TOKENS:
        cleaned = cleaned.replace(token, " ")
    return [n for n in re.findall(r"\d+(?:\.\d+)?", cleaned) if n not in allowed]


def validate_model_payload(
    payload: Any, facts: HourlyFacts, findings: list[Finding] | None = None
) -> tuple[str, str, str]:
    """Return (headline, summary, error). Error is empty when payload is valid."""
    if not isinstance(payload, dict):
        return "", "", "model response was not a JSON object"
    headline = payload.get("headline")
    summary = payload.get("summary")
    if not isinstance(headline, str) or not isinstance(summary, str):
        return "", "", "model response missing string headline/summary"
    headline = " ".join(headline.split())
    summary = " ".join(summary.split())
    if not headline or not summary:
        return "", "", "model response had empty headline/summary"
    if len(headline) > 140 or len(summary) > 1000:
        return "", "", "model response exceeded length limit"
    low = (headline + " " + summary).lower()
    if any(word in low for word in _FORBIDDEN_CLAIMS):
        return "", "", "model response contained a medical claim"
    if any(
        phrase in low for phrase in ("short headline", "two short sentences", "short status line")
    ):
        return "", "", "model response copied the example template"
    allowed = _allowed_number_tokens(facts, findings)
    bad = _unapproved_numbers(headline + " " + summary, allowed)
    if bad:
        return "", "", f"model response contained unapproved numbers: {', '.join(bad[:5])}"
    return headline, summary, ""


def render_fallback(facts: HourlyFacts, findings: list[Finding]) -> tuple[str, str]:
    """Deterministic wording used when the model is unavailable or rejected."""
    pm = facts.metrics.get("pm2_5_ug_m3")
    co2 = facts.metrics.get("co2_ppm")
    voc = facts.metrics.get("voc_index")
    rh = facts.metrics.get("relative_humidity_percent")
    parts: list[str] = []
    if pm is not None:
        parts.append(f"PM2.5 averaged {_fmt(pm.avg)} ug/m3")
    if co2 is not None:
        parts.append(f"CO2 averaged {_fmt(co2.avg, 0)} ppm")
    if voc is not None:
        parts.append(f"VOC index averaged {_fmt(voc.avg, 0)}")
    if rh is not None:
        parts.append(f"humidity averaged {_fmt(rh.avg)}%")
    measurements = "; ".join(parts) if parts else "no metrics were available"
    severity = (
        " Elevated values need attention."
        if any(f.severity in {"warning", "critical"} for f in findings)
        else " Conditions stayed within the configured guides."
    )
    headline = "Indoor air quality: " + (
        "attention needed" if "need attention" in severity else "steady"
    )
    return headline, f"{measurements}.{severity}"


def make_summary(
    facts: HourlyFacts,
    findings: list[Finding],
    *,
    model: str,
    payload: Any | None,
    model_error: str = "",
    model_metrics: dict[str, Any] | None = None,
) -> Summary:
    """Create the final Summary, falling back safely if needed."""
    headline, text, reason = "", "", model_error
    if payload is not None and not reason:
        headline, text, reason = validate_model_payload(payload, facts, findings)
    if not headline or not text:
        headline, text = render_fallback(facts, findings)
        reason = reason or "model output rejected"
    return Summary(
        headline=headline,
        text=text,
        actions=[f.action for f in findings],
        findings=findings,
        facts=facts,
        model=model,
        model_used=not bool(reason),
        generated_at=facts.generated_at.astimezone(UTC),
        fallback_reason=reason,
        model_metrics=dict(model_metrics or {}),
    )


def telegram_text(summary: Summary) -> str:
    """Render a plain-text Telegram message from a validated Summary.

    Values come first, followed by hour-to-hour changes, deterministic notes,
    and recommended practices. Nothing is truncated here; the sender splits
    long messages into readable parts at line boundaries.
    """
    lines = ["Stingray hourly air-quality summary", "", summary.headline, "", summary.text]

    lines.extend(["", "Values (last hour):"])
    shown: set[str] = set()
    for key, label, digits in _TELEGRAM_METRICS:
        f = summary.facts.metrics.get(key)
        if f is None:
            continue
        shown.add(key)
        lines.append(
            f"- {label}: avg {_fmt(f.avg, digits)}, min {_fmt(f.minimum, digits)}, "
            f"max {_fmt(f.maximum, digits)} {f.unit}"
        )
    for key, f in sorted(summary.facts.metrics.items()):
        if key in shown:
            continue
        lines.append(
            f"- {f.metric}: avg {_fmt(f.avg)}, min {_fmt(f.minimum)}, "
            f"max {_fmt(f.maximum)} {f.unit}"
        )

    changes = [f.message for f in summary.findings if f.code.endswith("_hour_change")]
    if changes:
        lines.extend(["", "Compared with previous hour:"])
        lines.extend(f"- {message}" for message in changes)

    notes = [f.message for f in summary.findings if not f.code.endswith("_hour_change")]
    if notes:
        lines.extend(["", "Notes:"])
        lines.extend(f"- {message}" for message in notes)

    if summary.actions:
        lines.extend(["", "Recommended practices:"])
        lines.extend(f"- {action}" for action in summary.actions)
    local = summary.generated_at.astimezone()
    lines.extend(
        [
            "",
            f"Window: {summary.facts.window_start.astimezone():%H:%M} to "
            f"{summary.facts.window_end.astimezone():%H:%M}",
            f"Model: {summary.model}{'' if summary.model_used else ' (fallback used)'}",
            f"Generated: {local:%Y-%m-%d %H:%M %Z}",
        ]
    )
    return "\n".join(lines)
