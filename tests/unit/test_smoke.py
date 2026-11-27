"""Smoke tests: package imports and the entry point is wired."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import stingray
from stingray.main import main


def test_package_has_version() -> None:
    assert hasattr(stingray, "__name__")


def test_main_returns_zero() -> None:
    assert main() == 0


def test_module_entry_point_runs() -> None:
    env = os.environ.copy()
    stub_path = str(Path(__file__).resolve().parents[1] / "stubs")
    env["PYTHONPATH"] = os.pathsep.join(part for part in (stub_path, env.get("PYTHONPATH")) if part)
    result = subprocess.run(
        [sys.executable, "-m", "stingray.main"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0
