"""Filesystem storage for hourly summaries.

Summaries are JSON documents plus a plain-text mirror. The API reads the JSON
documents directly; no database or extra service is required.
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stingray.summary import Summary, telegram_text


class SummaryStore:
    """Write and read hourly summary documents under a base directory."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)

    def save(self, summary: Summary) -> tuple[Path, Path]:
        """Atomically save a summary and return (json_path, text_path)."""
        generated = summary.generated_at.astimezone(UTC)
        day_dir = (
            self._base
            / f"year={generated.year:04d}"
            / f"month={generated.month:02d}"
            / f"day={generated.day:02d}"
        )
        day_dir.mkdir(parents=True, exist_ok=True)
        stem = f"summary_{generated:%Y%m%dT%H%M%SZ}"
        json_path = day_dir / f"{stem}.json"
        text_path = day_dir / f"{stem}.txt"

        payload = summary.to_jsonable()
        self._atomic_write_text(json_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        self._atomic_write_text(text_path, telegram_text(summary) + "\n")
        self._atomic_write_text(
            self._base / "latest.json",
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )
        return json_path, text_path

    @staticmethod
    def _atomic_write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with open(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    def latest(self) -> dict[str, Any] | None:
        """Return the most recently generated summary, if any.

        ``latest.json`` is the fast path. If it is missing or damaged, fall
        back to the newest dated document so the last run stays available.
        """
        path = self._base / "latest.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            return payload
        recovered = self.list(limit=1)
        return recovered[0] if recovered else None

    def list(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return summaries newest-first, capped at ``limit``."""
        items: list[tuple[datetime, dict[str, Any]]] = []
        pattern = "year=*/month=*/day=*/summary_*.json"
        for path in sorted(self._base.glob(pattern)):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            raw = payload.get("generated_at")
            try:
                when = datetime.fromisoformat(str(raw)).astimezone(UTC)
            except (TypeError, ValueError):
                continue
            items.append((when, payload))
        items.sort(key=lambda item: item[0], reverse=True)
        return [payload for _, payload in items[: max(1, limit)]]
