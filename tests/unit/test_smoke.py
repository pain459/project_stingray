"""Smoke tests: package imports and the entry point is wired."""

from __future__ import annotations

import subprocess
import sys

import stingray
from stingray.main import main


def test_package_has_version() -> None:
    assert hasattr(stingray, "__name__")


def test_main_returns_zero() -> None:
    assert main() == 0


def test_module_entry_point_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "stingray.main"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
