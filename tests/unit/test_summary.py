"""Unit tests for the hourly summary rule layer and store."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stingray.core.config import ThresholdsConfig
from stingray.core.models import Measurement, MetricId, SensorId, utc_now
from stingray.storage.parquet_store import ParquetWriter
from stingray.summary import (
    HourlyFacts,
    MetricFacts,
    derive_findings,
    make_summary,
    telegram_text,
    validate_model_payload,
)
from stingray.summary_job import aggregate_facts
from stingray.summary_store import SummaryStore


def _facts(**overrides: float) -> HourlyFacts:
    end = datetime(2026, 11, 26, 22, 0, tzinfo=UTC)
    start = end - timedelta(hours=1)
    metrics = {
        "pm2_5_ug_m3": ("ug/m3", 8.0),
        "co2_ppm": ("ppm", 650.0),
        "voc_index": ("index", 40.0),
        "nox_index": ("index", 1.0),
        "temperature_c": ("degC", 29.0),
        "relative_humidity_percent": ("%", 55.0),
    }
    for name, value in overrides.items():
        metrics[name] = (metrics.get(name, ("", 0.0))[0], value)
    return HourlyFacts(
        generated_at=end,
        window_start=start,
        window_end=end,
        sample_count=120,
        metrics={
            name: MetricFacts(
                metric=name,
                unit=unit,
                count=20,
                avg=value,
                minimum=value,
                maximum=value,
                first=value,
                last=value,
                last_seen=end - timedelta(seconds=5),
            )
            for name, (unit, value) in metrics.items()
        },
    )


class TestRules:
    def test_normal_window_is_all_clear(self) -> None:
        findings = derive_findings(_facts(), ThresholdsConfig())
        assert [f.code for f in findings] == ["all_clear"]

    def test_high_co2_adds_ventilation_action(self) -> None:
        findings = derive_findings(_facts(co2_ppm=1200), ThresholdsConfig())
        codes = {f.code for f in findings}
        assert "co2_elevated" in codes
        assert any("window" in f.action.lower() for f in findings)

    def test_high_pm_is_critical_at_twice_threshold(self) -> None:
        findings = derive_findings(_facts(pm2_5_ug_m3=80), ThresholdsConfig())
        pm = next(f for f in findings if f.code == "pm25_elevated")
        assert pm.severity == "critical"

    def test_stale_metrics_flagged(self) -> None:
        facts = _facts()
        old = facts.metrics["co2_ppm"]
        facts.metrics["co2_ppm"] = MetricFacts(
            metric=old.metric,
            unit=old.unit,
            count=old.count,
            avg=old.avg,
            minimum=old.minimum,
            maximum=old.maximum,
            first=old.first,
            last=old.last,
            last_seen=facts.window_end - timedelta(minutes=20),
        )
        assert "stale_metrics" in {f.code for f in derive_findings(facts, ThresholdsConfig())}

    def test_significant_hour_change_adds_finding(self) -> None:
        facts = _facts()
        pm = facts.metrics["pm2_5_ug_m3"]
        facts.metrics["pm2_5_ug_m3"] = replace(pm, previous_avg=pm.avg / 2)
        findings = derive_findings(facts, ThresholdsConfig())
        change = next(f for f in findings if f.code == "pm2_5_ug_m3_hour_change")
        assert "up 100%" in change.message
        assert "previous hour" not in change.message  # wording is "last hour"
        assert "last hour" in change.message

    def test_small_hour_change_adds_no_finding(self) -> None:
        facts = _facts()
        pm = facts.metrics["pm2_5_ug_m3"]
        facts.metrics["pm2_5_ug_m3"] = replace(pm, previous_avg=pm.avg / 1.05)
        codes = {f.code for f in derive_findings(facts, ThresholdsConfig())}
        assert "pm2_5_ug_m3_hour_change" not in codes


class TestAggregation:
    def test_previous_window_average_is_captured(self, tmp_path: Path) -> None:
        end = datetime(2026, 11, 26, 22, 0, tzinfo=UTC)
        start = end - timedelta(hours=1)
        previous_start = start - timedelta(hours=1)
        writer = ParquetWriter(tmp_path, batch_size=10)
        for value, when in (
            (20.0, previous_start + timedelta(minutes=30)),
            (40.0, start + timedelta(minutes=30)),
        ):
            writer.add(
                Measurement(
                    sensor=SensorId.SPS30,
                    metric=MetricId.PM2_5_UG_M3,
                    value=value,
                    unit="ug/m3",
                    observed_at=when,
                    received_at=utc_now(),
                )
            )
        writer.flush()
        facts = aggregate_facts(tmp_path, generated_at=end, hours=1)
        pm = facts.metrics["pm2_5_ug_m3"]
        assert pm.previous_avg == 20.0
        assert pm.change_vs_previous_pct == 100.0
        assert facts.previous_sample_count == 1
        assert facts.previous_window_end == start


class TestValidation:
    def test_accepts_supplied_number(self) -> None:
        headline, text, error = validate_model_payload(
            {"headline": "Air quality steady", "summary": "CO2 averaged 650 ppm."},
            _facts(),
        )
        assert not error
        assert headline == "Air quality steady"
        assert "650" in text

    def test_accepts_previous_hour_number_from_facts(self) -> None:
        facts = _facts()
        pm = facts.metrics["pm2_5_ug_m3"]
        facts.metrics["pm2_5_ug_m3"] = replace(pm, previous_avg=pm.avg / 2)
        findings = derive_findings(facts, ThresholdsConfig())
        _, text, error = validate_model_payload(
            {
                "headline": "Particles rose this hour",
                "summary": f"PM2.5 averaged {pm.avg:.1f} ug/m3, up from {pm.avg / 2:.1f} ug/m3.",
            },
            facts,
            findings,
        )
        assert not error
        assert "up from" in text

    def test_rejects_invented_number(self) -> None:
        _, _, error = validate_model_payload(
            {"headline": "Air quality steady", "summary": "Levels fell by 37 percent."},
            _facts(),
        )
        assert "unapproved numbers" in error

    def test_accepts_threshold_number_from_finding(self) -> None:
        findings = derive_findings(_facts(relative_humidity_percent=70), ThresholdsConfig())
        headline, text, error = validate_model_payload(
            {
                "headline": "Humidity needs attention",
                "summary": "Humidity averaged 70.0%, above the 65.0% guide.",
            },
            _facts(relative_humidity_percent=70),
            findings,
        )
        assert not error
        assert "65.0" in text

    def test_rejects_copied_template(self) -> None:
        _, _, error = validate_model_payload(
            {"headline": "A short headline", "summary": "Two short sentences."},
            _facts(),
        )
        assert "copied the example template" in error

    def test_rejects_medical_claim(self) -> None:
        _, _, error = validate_model_payload(
            {"headline": "You are diagnosed", "summary": "This diagnoses asthma."},
            _facts(),
        )
        assert "medical claim" in error

    def test_fallback_used_when_model_rejected(self) -> None:
        findings = derive_findings(_facts(), ThresholdsConfig())
        summary = make_summary(
            _facts(),
            findings,
            model="qwen2.5:0.5b",
            payload={"headline": "x", "summary": "Invented 99 ppm."},
        )
        assert not summary.model_used
        assert summary.fallback_reason
        assert "PM2.5" in telegram_text(summary)


class TestStore:
    def test_round_trip(self, tmp_path: Path) -> None:
        facts = _facts()
        findings = derive_findings(facts, ThresholdsConfig())
        summary = make_summary(
            facts,
            findings,
            model="test-model",
            payload={"headline": "Steady", "summary": "All good."},
        )
        store = SummaryStore(tmp_path)
        json_path, text_path = store.save(summary)
        assert json_path.is_file() and text_path.is_file()
        assert (tmp_path / "latest.json").is_file()
        assert store.latest()["headline"] == "Steady"
        assert store.list(limit=5)[0]["headline"] == "Steady"

    def test_comparison_is_persisted(self, tmp_path: Path) -> None:
        facts = _facts()
        pm = facts.metrics["pm2_5_ug_m3"]
        facts.metrics["pm2_5_ug_m3"] = replace(pm, previous_avg=pm.avg / 2)
        findings = derive_findings(facts, ThresholdsConfig())
        summary = make_summary(facts, findings, model="test-model", payload=None)
        payload = summary.to_jsonable()
        assert payload["facts"]["previous_sample_count"] == 0
        pm_json = payload["facts"]["metrics"]["pm2_5_ug_m3"]
        assert pm_json["previous_avg"] == round(pm.avg / 2, 3)
        assert pm_json["change_vs_previous_pct"] == 100.0
        assert "Compared with previous hour:" in telegram_text(summary)
