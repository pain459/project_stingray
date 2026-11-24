# Phase 02 Acceptance Report

- **Phase / Jira epic:** Phase 02 — Toolchain, local workflow and CI (`STG-P02`)
- **Date and implementer:** 2026-11-24, AI agent (executed on `stingray` via SSH)
- **Git SHA:** `8bf88f7`
- **OS / kernel / Python:** Debian 13 (trixie) / `6.18.60+rpt-rpi-v8` / Python 3.13.5
- **Hardware revisions / wiring version:** Pi 4 Model B Rev 1.1; no sensors wired
- **Configuration revision (secrets redacted):** `pyproject.toml` v0.1.0
- **Tests performed / commands:**
  - `uv sync --extra dev`
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run mypy src/`
  - `uv run pytest`
  - fresh clone → `uv sync --frozen --extra dev` → same checks
  - negative CI test on branch `test/ci-negative` (deliberate lint error → PR #1)
- **Expected vs actual outcomes:** all local checks green; fresh clone reproduces;
  deliberately broken commit produced a failed CI run (as required)
- **Metrics captured:** 3 smoke tests pass; mypy strict clean across 11 source files
- **Known risks / defects:** dev-machine (non-Pi) install not yet verified
- **Evidence paths / links:** this report; GitHub Actions run on `8bf88f7` (success)
  and on `test/ci-negative` (failure); `uv.lock`
- **Gate decision:** **DEV_READY = PASS** (with one open item: non-Pi install)
- **Approved by / date:** pending user sign-off

## STG-006 — Set up Python project and diagnostics

### Implemented

- Python: system CPython **3.13.5** (Debian 13 supported interpreter).
- `uv` installed (`0.13.9`, `aarch64-unknown-linux-gnu`) at `~/.local/bin/uv`.
- `pyproject.toml` with:
  - base runtime deps: `pydantic`, `pydantic-settings`, `fastapi`, `uvicorn`,
    `httpx`, `pyyaml`
  - `[hardware]` extra: `smbus2`, `spidev` (sensor driver libs deferred to Phase 05
    after ARM64/API verification — deliberately not guessed)
  - `[alerts]` extra: `python-telegram-bot`
  - `[dev]` extra: `pytest`, `pytest-cov`, `ruff`, `mypy`
- `uv.lock` committed.
- Tooling present on Pi: `i2c-tools 4.4`, `spi-tools 0.8.4`, `gpiod 2.2.1`,
  `python3-libgpiod 2.2.1`, `jq`, `tmux`, `git`, `sqlite3`, `vcgencmd`.

### Acceptance criteria

- [x] Fresh clone installs using documented commands and lockfile
      (`git clone` → `uv sync --frozen --extra dev` → OK in `/tmp/stingray-fresh`).
- [x] No global pip modifications / no `--break-system-packages`.
- [x] `uv run pytest`, `uv run ruff check .`, and type checks complete.
- [x] Dependency install works on the Pi (ARM64).
- [x] Hardware-only dependencies do not prevent mock tests
      (kept in `[hardware]` extra; base install is hardware-free).
- [ ] Install verified on a second (development / non-Pi) machine — **pending**.

## STG-007 — Configure CI and developer commands

### Implemented

- `.github/workflows/ci.yml`: checkout → setup-uv → Python 3.12 → `uv sync --extra dev`
  → ruff lint → ruff format check → mypy → pytest with coverage.
  Pinned actions (`actions/checkout@v4`, `astral-sh/setup-uv@v5`),
  `permissions: contents: read`.
- `Makefile` targets: `setup`, `lint`, `format-check`, `typecheck`, `test`,
  `test-hardware`, `run-mock`, `preflight`, `clean`.
- Hardware tests convention: `@pytest.mark.hardware`, excluded via
  `addopts = -m "not hardware"` in `pyproject.toml`.

### Acceptance criteria

- [x] PR pipeline runs without Pi or sensors (GitHub-hosted runner, mock mode).
- [x] Deliberately introduced lint failure causes a non-green pipeline
      (PR #1 on `test/ci-negative`: CI run conclusion = **failure**; PR closed and
      branch deleted after the test).
- [x] Test results and coverage summary archived by the workflow
      (`--cov=stingray --cov-report=term-missing`).
- [x] CI never requires Telegram credentials or actual GPIO access.

## Phase 02 exit gate

**DEV_READY = PASS**

Two-machine reproduction is *partially* verified: the Pi and a fresh clone on the
Pi both reproduce results, and CI runs on a clean GitHub runner. The remaining item
is a first-time install on the user's separate development machine.

### Next actions

1. User: run `uv sync --extra dev && uv run pytest` on the Mac/dev machine; record result.
2. Phase 03 remains blocked on Phase 01 `POWER_STABLE` (fan/power root cause).
3. Mock/architecture work (Phase 04) and storage work (Phase 06) may proceed now.
