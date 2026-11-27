"""Unit tests for the hourly summary rule layer and store."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stingray.core.config import (
    AppConfig,
    ExternalContextConfig,
    SummaryConfig,
    ThresholdsConfig,
)
from stingray.core.models import Measurement, MetricId, SensorId, utc_now
from stingray.external_context import OutdoorContext, fetch_outdoor_context
from stingray.storage.parquet_store import ParquetWriter
from stingray.summary import (
    HourlyFacts,
    MetricFacts,
    Summary,
    build_prompt,
    derive_findings,
    make_summary,
    telegram_html,
    telegram_text,
    validate_model_payload,
)
from stingray.summary_job import aggregate_facts, generate, split_telegram_text
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


class TestTelegram:
    def test_values_then_notes_then_practices(self) -> None:
        facts = _facts(pm2_5_ug_m3=40.0)
        pm = facts.metrics["pm2_5_ug_m3"]
        facts.metrics["pm2_5_ug_m3"] = replace(pm, minimum=30.0, maximum=50.0)
        findings = derive_findings(facts, ThresholdsConfig())
        summary = make_summary(
            facts,
            findings,
            model="test-model",
            payload={"headline": "Headline", "summary": "Summary."},
        )
        text = telegram_text(summary)
        assert "PM2.5          40.0  30.0–50.0 µg/m³" in text
        assert "Notes:" in text
        assert "Recommended practices:" in text
        assert (
            text.index("Values (last hour):")
            < text.index("Notes:")
            < text.index("Recommended practices:")
        )

    def test_html_is_formatted_and_escaped(self) -> None:
        facts = _facts(pm2_5_ug_m3=40.0)
        findings = derive_findings(facts, ThresholdsConfig())
        summary = make_summary(
            facts,
            findings,
            model="test-model",
            payload={"headline": "Air <b>good</b>", "summary": "Values & trends."},
        )
        html_text = telegram_html(summary)
        assert "<b>Stingray · Air quality</b>" in html_text
        assert "<b>Values (last hour):</b>" in html_text
        assert "<code>PM2.5          40.0  40.0–40.0 µg/m³</code>" in html_text
        assert "Air &lt;b&gt;good&lt;/b&gt;" in html_text
        assert "Values &amp; trends." in html_text
        assert all(line.count("<") == line.count(">") for line in html_text.splitlines())

    def test_raw_signals_and_number_concentrations_are_hidden(self) -> None:
        facts = _facts(voc_raw=31935.0, pm10_num_m3=292.0)
        summary = make_summary(
            facts,
            derive_findings(facts, ThresholdsConfig()),
            model="test-model",
            payload=None,
        )
        for rendered in (telegram_text(summary), telegram_html(summary)):
            assert "voc_raw" not in rendered
            assert "31935" not in rendered
            assert "pm10_num_m3" not in rendered

    def test_html_split_keeps_tags_balanced(self) -> None:
        facts = _facts(pm2_5_ug_m3=40.0)
        findings = derive_findings(facts, ThresholdsConfig())
        summary = make_summary(
            facts,
            findings,
            model="test-model",
            payload={"headline": "Headline", "summary": "Summary."},
        )
        parts = split_telegram_text(telegram_html(summary), limit=200)
        assert len(parts) > 1
        for part in parts:
            assert part.count("<b>") == part.count("</b>")
            assert part.count("<i>") == part.count("</i>")
            assert part.count("<code>") == part.count("</code>")

    def test_long_text_is_split_without_loss(self) -> None:
        text = "\n".join(f"line {i:03d} " + "x" * 20 for i in range(300))
        parts = split_telegram_text(text, limit=500)
        assert len(parts) > 1
        assert all(len(part) <= 500 for part in parts)
        assert sum(part.count("line ") for part in parts) == 300
        assert "line 299" in parts[-1]


class TestOutdoorRules:
    def test_elevated_aqi_adds_finding(self) -> None:
        context = OutdoorContext(us_aqi=131.0)
        findings = derive_findings(_facts(), ThresholdsConfig(), context)
        aqi = next(f for f in findings if f.code == "outdoor_aqi_elevated")
        assert aqi.severity == "warning"
        assert "131" in aqi.message
        assert "Unhealthy for sensitive groups" in aqi.message
        assert "briefly" in aqi.action

    def test_very_high_aqi_is_critical(self) -> None:
        findings = derive_findings(
            _facts(), ThresholdsConfig(), OutdoorContext(us_aqi=180.0)
        )
        aqi = next(f for f in findings if f.code == "outdoor_aqi_elevated")
        assert aqi.severity == "critical"
        assert "recirculation" in aqi.action

    def test_outdoor_pm25_higher_uses_threshold_and_indoor_ratio(self) -> None:
        context = OutdoorContext(pm2_5_ug_m3=12.0)
        findings = derive_findings(
            _facts(pm2_5_ug_m3=8.0),
            ThresholdsConfig(pm2_5_ug_m3=10.0),
            context,
        )
        pm = next(f for f in findings if f.code == "outdoor_pm25_higher")
        assert "12.0 ug/m3 versus 8.0 ug/m3 indoors" in pm.message

    def test_outdoor_pm25_below_ratio_is_not_flagged(self) -> None:
        findings = derive_findings(
            _facts(pm2_5_ug_m3=8.0),
            ThresholdsConfig(pm2_5_ug_m3=10.0),
            OutdoorContext(pm2_5_ug_m3=11.0),
        )
        assert "outdoor_pm25_higher" not in {f.code for f in findings}

    def test_rain_adds_info_finding(self) -> None:
        findings = derive_findings(
            _facts(), ThresholdsConfig(), OutdoorContext(precipitation_mm=0.2)
        )
        rain = next(f for f in findings if f.code == "outdoor_rain")
        assert rain.severity == "info"
        assert "0.2 mm" in rain.message

    def test_co2_action_switches_when_outdoor_air_is_polluted(self) -> None:
        facts = _facts(co2_ppm=1200.0)
        polluted = derive_findings(facts, ThresholdsConfig(), OutdoorContext(us_aqi=131.0))
        co2 = next(f for f in polluted if f.code == "co2_elevated")
        assert "recirculation" in co2.action
        indoor_only = derive_findings(facts, ThresholdsConfig())
        co2 = next(f for f in indoor_only if f.code == "co2_elevated")
        assert "window" in co2.action

    def test_empty_context_keeps_indoor_rules(self) -> None:
        findings = derive_findings(_facts(), ThresholdsConfig(), OutdoorContext.empty())
        assert [f.code for f in findings] == ["all_clear"]


class TestOutdoorFetch:
    def test_disabled_context_returns_empty(self) -> None:
        context = fetch_outdoor_context(ExternalContextConfig())
        assert not context.has_data
        assert context.error == ""
        assert context.footer_line() == ""

    def test_parses_both_feeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_fetch(url: str, params: dict[str, object], timeout: float) -> dict[str, object]:
            assert timeout == 5.0
            assert params["latitude"] == 17.38
            assert params["longitude"] == 78.36
            if "air-quality" in url:
                return {
                    "current": {
                        "time": "2026-11-27T22:00",
                        "us_aqi": 131,
                        "pm2_5": 40.0,
                    }
                }
            return {
                "current": {
                    "time": "2026-11-27T22:00",
                    "temperature_2m": 30.4,
                    "precipitation": 0.0,
                    "wind_speed_10m": 7.6,
                }
            }

        monkeypatch.setattr("stingray.external_context._fetch_json", fake_fetch)
        context = fetch_outdoor_context(
            ExternalContextConfig(enabled=True, latitude=17.38, longitude=78.36)
        )
        assert context.error == ""
        assert context.observed_at == "2026-11-27T22:00"
        assert context.us_aqi == 131.0
        assert context.pm2_5_ug_m3 == 40.0
        assert context.source == "Open-Meteo/CAMS"
        assert context.footer_line() == (
            "Nearby outdoor (model): 30.4°C · rain 0.0 mm · wind 7.6 km/h · "
            "US AQI 131 · PM2.5 40.0 µg/m³ · Open-Meteo/CAMS"
        )

    def test_partial_failure_is_fail_soft(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_fetch(url: str, params: dict[str, object], timeout: float) -> dict[str, object]:
            if "air-quality" in url:
                return {"current": {"us_aqi": 42}}
            raise OSError("offline")

        monkeypatch.setattr("stingray.external_context._fetch_json", fake_fetch)
        context = fetch_outdoor_context(
            ExternalContextConfig(enabled=True, latitude=17.38, longitude=78.36)
        )
        assert context.has_data
        assert context.us_aqi == 42.0
        assert context.error == "weather:OSError"
        assert "US AQI 42" in context.footer_line()

    def test_non_numeric_payload_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_fetch(url: str, params: dict[str, object], timeout: float) -> dict[str, object]:
            return {"current": {"us_aqi": "unknown", "temperature_2m": None}}

        monkeypatch.setattr("stingray.external_context._fetch_json", fake_fetch)
        context = fetch_outdoor_context(
            ExternalContextConfig(enabled=True, latitude=17.38, longitude=78.36)
        )
        assert not context.has_data
        assert context.footer_line() == ""

    def test_generate_fetches_context_once_for_both_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        context = OutdoorContext(us_aqi=131.0, temperature_c=30.4)
        calls: list[object] = []

        def fake_fetch(config: ExternalContextConfig) -> OutdoorContext:
            calls.append(config)
            return context

        def fake_ollama(url: str, model: str, prompt: str) -> tuple[str, dict[str, object]]:
            assert "outdoor_us_aqi: 131" in prompt
            return '{"headline":"Outdoor air is poor","summary":"Nearby US AQI is 131."}', {}

        monkeypatch.setattr("stingray.summary_job.fetch_outdoor_context", fake_fetch)
        monkeypatch.setattr("stingray.summary_job.call_ollama", fake_ollama)
        config = AppConfig(
            summary=SummaryConfig(
                external=ExternalContextConfig(
                    enabled=True, latitude=17.38, longitude=78.36
                )
            )
        )
        summary = generate(_facts(), config, model="test-model")
        assert isinstance(summary, Summary)
        assert len(calls) == 1
        assert summary.outdoor == context
        assert "outdoor_aqi_elevated" in {f.code for f in summary.findings}


class TestOutdoorPromptAndValidation:
    def test_prompt_labels_outdoor_as_nearby_estimate(self) -> None:
        context = OutdoorContext(
            temperature_c=30.4,
            precipitation_mm=0.0,
            wind_kmh=7.6,
            us_aqi=131.0,
            pm2_5_ug_m3=40.0,
            observed_at="2026-11-27T22:00",
        )
        prompt = build_prompt(_facts(), [], context)
        assert "Outdoor context (nearby model estimate):" in prompt
        assert "outdoor_us_aqi: 131 (Unhealthy for sensitive groups)" in prompt
        assert "- observed_at: 2026-11-27T22:00" in prompt
        assert "not a local reading" in prompt

    def test_prompt_without_context_has_no_outdoor_section(self) -> None:
        assert "Outdoor context" not in build_prompt(_facts(), [])

    def test_accepts_supplied_outdoor_number(self) -> None:
        context = OutdoorContext(us_aqi=131.0)
        _, text, error = validate_model_payload(
            {"headline": "Outdoor air is poor", "summary": "Nearby US AQI is 131."},
            _facts(),
            [],
            context,
        )
        assert not error
        assert "131" in text

    def test_rejects_invented_outdoor_number(self) -> None:
        _, _, error = validate_model_payload(
            {"headline": "Outdoor air is poor", "summary": "Nearby US AQI is 132."},
            _facts(),
            [],
            OutdoorContext(us_aqi=131.0),
        )
        assert "unapproved numbers" in error


class TestOutdoorTelegram:
    @staticmethod
    def _context() -> OutdoorContext:
        return OutdoorContext(
            temperature_c=30.4,
            precipitation_mm=0.0,
            wind_kmh=7.6,
            us_aqi=131.0,
            pm2_5_ug_m3=40.0,
        )

    @staticmethod
    def _summary(context: OutdoorContext | None = None) -> object:
        facts = _facts(pm2_5_ug_m3=40.0)
        return make_summary(
            facts,
            derive_findings(facts, ThresholdsConfig(), context),
            model="test-model",
            payload={"headline": "Headline", "summary": "Summary."},
            outdoor=context,
        )

    def test_outdoor_line_is_one_footer_line(self) -> None:
        summary = self._summary(self._context())
        assert isinstance(summary, Summary)
        footer = (
            "Nearby outdoor (model): 30.4°C · rain 0.0 mm · wind 7.6 km/h · "
            "US AQI 131 · PM2.5 40.0 µg/m³ · Open-Meteo/CAMS"
        )
        text = telegram_text(summary)
        assert text.count(footer) == 1
        assert text.index("Recommended practices:") < text.index(footer) < text.index("Window:")
        html_text = telegram_html(summary)
        assert f"<i>{footer}</i>" in html_text

    def test_no_outdoor_line_without_context(self) -> None:
        summary = self._summary()
        assert isinstance(summary, Summary)
        assert "Nearby outdoor" not in telegram_text(summary)
        assert "Nearby outdoor" not in telegram_html(summary)

    def test_outdoor_context_is_persisted(self) -> None:
        summary = self._summary(self._context())
        assert isinstance(summary, Summary)
        payload = summary.to_jsonable()
        assert payload["outdoor"]["available"] is True
        assert payload["outdoor"]["us_aqi"] == 131.0
        assert payload["outdoor"]["source"] == "Open-Meteo/CAMS"


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
