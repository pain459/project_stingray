#!/usr/bin/env python3
"""Generate the hourly Stingray summary from real Parquet sensor data.

The job reads a compact aggregate from the live Parquet store, applies the
configured deterministic thresholds, asks a local Ollama model to phrase the
findings, writes JSON/text under the summary directory, and optionally sends
the same text to Telegram.

Credentials are read only from the environment (or an EnvironmentFile in the
systemd unit). They are never logged or stored in the summary documents.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from stingray.core.config import AppConfig, load_config
from stingray.summary import (
    HourlyFacts,
    MetricFacts,
    build_prompt,
    derive_findings,
    make_summary,
    telegram_text,
)
from stingray.summary_store import SummaryStore

log = logging.getLogger("stingray.summary")


def aggregate_facts(
    parquet_path: str | Path,
    *,
    generated_at: datetime,
    hours: float,
) -> HourlyFacts:
    """Aggregate the current and previous trailing windows into fact objects."""
    generated_at = generated_at.astimezone(UTC)
    start = generated_at - timedelta(hours=hours)
    previous_start = start - timedelta(hours=hours)
    base = Path(parquet_path)
    glob = str(base / "**" / "*.parquet")
    sql = f"""
        SELECT metric,
               any_value(unit) AS unit,
               count(*) AS n,
               avg(value) AS avg_value,
               min(value) AS min_value,
               max(value) AS max_value,
               arg_min(value, observed_epoch) AS first_value,
               arg_max(value, observed_epoch) AS last_value,
               max(observed_epoch) AS last_epoch
        FROM read_parquet('{glob}', hive_partitioning=true)
        WHERE observed_epoch >= ? AND observed_epoch < ?
        GROUP BY metric
        ORDER BY metric
    """
    con = duckdb.connect()
    current_rows: list[tuple[Any, ...]] = []
    previous_rows: list[tuple[Any, ...]] = []
    try:
        current_rows = con.execute(sql, [start.timestamp(), generated_at.timestamp()]).fetchall()
        previous_rows = con.execute(sql, [previous_start.timestamp(), start.timestamp()]).fetchall()
    except Exception as exc:  # noqa: BLE001 - no/malformed parquet files yet
        log.warning("summary aggregation returned no data: %s", type(exc).__name__)
    finally:
        con.close()

    previous: dict[str, tuple[float, int]] = {}
    for metric, _unit, count, avg_value, *_rest in previous_rows:
        if not metric or count is None or avg_value is None:
            continue
        previous[str(metric)] = (float(avg_value), int(count))

    metrics: dict[str, MetricFacts] = {}
    for (
        metric,
        unit,
        count,
        avg_value,
        min_value,
        max_value,
        first_value,
        last_value,
        last_epoch,
    ) in current_rows:
        if not metric or count is None:
            continue
        prior = previous.get(str(metric))
        metrics[str(metric)] = MetricFacts(
            metric=str(metric),
            unit=str(unit),
            count=int(count),
            avg=float(avg_value),
            minimum=float(min_value),
            maximum=float(max_value),
            first=float(first_value),
            last=float(last_value),
            last_seen=datetime.fromtimestamp(float(last_epoch), tz=UTC),
            previous_avg=prior[0] if prior else None,
        )
    return HourlyFacts(
        generated_at=generated_at,
        window_start=start,
        window_end=generated_at,
        sample_count=sum(f.count for f in metrics.values()),
        metrics=metrics,
        previous_window_start=previous_start,
        previous_window_end=start,
        previous_sample_count=sum(count for _avg, count in previous.values()),
    )


def call_ollama(
    base_url: str,
    model: str,
    prompt: str,
    *,
    timeout_seconds: float = 300.0,
) -> tuple[str, dict[str, Any]]:
    """Call the local Ollama /api/chat endpoint with strict JSON output."""
    endpoint = base_url.rstrip("/") + "/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["headline", "summary"],
        },
        "keep_alive": 0,
        "options": {
            "temperature": 0,
            "num_ctx": 1024,
            "num_predict": 256,
        },
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = json.loads(response.read().decode("utf-8"))
    message = body.get("message") if isinstance(body, dict) else None
    content = message.get("content", "") if isinstance(message, dict) else ""
    metrics = {
        key: body.get(key)
        for key in (
            "total_duration",
            "load_duration",
            "prompt_eval_count",
            "eval_count",
            "eval_duration",
        )
        if isinstance(body, dict) and body.get(key) is not None
    }
    return str(content), metrics


def generate(
    facts: HourlyFacts,
    config: AppConfig,
    *,
    model: str,
) -> Any:
    """Run the rules and the model, returning a validated Summary."""
    findings = derive_findings(facts, config.alerts.thresholds)
    prompt = build_prompt(facts, findings)
    error = ""
    payload: Any = None
    metrics: dict[str, Any] = {}
    try:
        content, metrics = call_ollama(config.summary.ollama_url, model, prompt)
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            error = "model response was not valid JSON"
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        error = f"ollama request failed: {type(exc).__name__}"
        log.warning("%s", error)
    return make_summary(
        facts,
        findings,
        model=model,
        payload=payload,
        model_error=error,
        model_metrics=metrics,
    )


def split_telegram_text(text: str, limit: int = 3900) -> list[str]:
    """Split long summaries into Telegram-sized parts at line boundaries.

    No content is discarded; only pathologically long single lines are hard
    wrapped. The limit stays below Telegram's 4096-character message cap so a
    small ``(part n/m)`` footer can be added safely.
    """
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append("\n".join(current))
                current = []
            parts.append(line[:limit])
            line = line[limit:]
        candidate = "\n".join([*current, line])
        if len(candidate) > limit and current:
            parts.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        parts.append("\n".join(current))
    return parts


def send_telegram(
    bot_token: str, chat_id: str, text: str, *, timeout_seconds: float = 20.0
) -> None:
    """Send a plain-text Telegram message. Never log the token or full URL."""
    token = quote_token(bot_token)
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    parts = split_telegram_text(text)
    for index, part in enumerate(parts, start=1):
        body_text = part if len(parts) == 1 else f"{part}\n\n(part {index}/{len(parts)})"
        payload = json.dumps(
            {"chat_id": chat_id, "text": body_text, "disable_web_page_preview": True}
        ).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"telegram request failed: {type(exc).__name__}") from None
        if not isinstance(body, dict) or not body.get("ok"):
            detail = (
                body.get("description", "unknown error")
                if isinstance(body, dict)
                else "unknown error"
            )
            raise RuntimeError(f"telegram rejected message: {detail}")


def quote_token(token: str) -> str:
    """Escape a bot token for a URL path while keeping its colon separator."""
    return urllib.parse.quote(token, safe=":")


def _print_benchmark_row(model: str, summary: Any, elapsed: float) -> None:
    row = {
        "model": model,
        "seconds": round(elapsed, 2),
        "model_used": summary.model_used,
        "fallback_reason": summary.fallback_reason,
        "eval_count": summary.model_metrics.get("eval_count"),
        "tokens_per_second": (
            round(
                summary.model_metrics["eval_count"]
                / (summary.model_metrics["eval_duration"] / 1_000_000_000),
                2,
            )
            if summary.model_metrics.get("eval_duration")
            and summary.model_metrics.get("eval_count")
            else None
        ),
        "headline": summary.headline,
        "summary": summary.text,
    }
    print(json.dumps(row, ensure_ascii=False), flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="config/application.combined.yaml",
        help="Application YAML config (default: config/application.combined.yaml).",
    )
    parser.add_argument("--hours", type=float, default=None, help="Trailing window in hours.")
    parser.add_argument("--model", default=None, help="Override summary.model from config.")
    parser.add_argument(
        "--summaries-dir",
        default=None,
        help="Override summary.path (default: value from config).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write summary files.")
    parser.add_argument(
        "--no-telegram", action="store_true", help="Do not send a Telegram message."
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run each model against one real window, print timing, and write/send nothing.",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        help="Models to benchmark (default: configured model plus smollm2:135m).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    try:
        config = load_config(args.config)
    except Exception as exc:  # noqa: BLE001 - CLI should fail cleanly
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    hours = args.hours if args.hours is not None else config.summary.hours
    generated_at = datetime.now(UTC)
    facts = aggregate_facts(config.storage.path, generated_at=generated_at, hours=hours)
    models = args.models or (
        [config.summary.model, "smollm2:135m"]
        if args.benchmark
        else [args.model or config.summary.model]
    )

    if args.benchmark:
        for model in models:
            started = time.monotonic()
            summary = generate(facts, config, model=model)
            _print_benchmark_row(model, summary, time.monotonic() - started)
        return 0

    if facts.sample_count == 0:
        print("no sensor samples in the requested window; summary skipped", file=sys.stderr)
        return 1

    model = args.model or config.summary.model
    summary = generate(facts, config, model=model)

    if not args.dry_run:
        summaries_dir = args.summaries_dir or config.summary.path
        json_path, _ = SummaryStore(summaries_dir).save(summary)
        print(f"summary saved: {json_path}")
    else:
        print("dry run: no summary files written")

    if args.no_telegram or not config.alerts.telegram.enabled:
        return 0
    token = os.environ.get("STINGRAY_TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("STINGRAY_TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("Telegram skipped: credentials are unset in the environment")
        return 0
    try:
        send_telegram(token, chat_id, telegram_text(summary))
    except RuntimeError as exc:
        print(f"Telegram send failed: {exc}", file=sys.stderr)
        return 1
    print("Telegram message sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
