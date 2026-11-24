# Phase 00 Acceptance Report

- **Phase / Jira epic:** Phase 00 — Repository bootstrap and scope capture (`STG-P00`)
- **Date and implementer:** 2026-11-24, AI agent (executed on `stingray` via SSH)
- **Git SHA:** `6cfbf40` (baseline) → changes in this phase committed after report
- **OS / kernel / Python:** Debian 13 (trixie) / `6.18.60+rpt-rpi-v8` / Python 3.13.5
- **Hardware revisions / wiring version:** Pi 4 Model B Rev 1.1 (`b03111`); no sensors wired yet
- **Configuration revision (secrets redacted):** `config/*.example.yaml` v0.1
- **Tests performed / commands:** directory skeleton creation; `bash scripts/preflight.sh`
- **Expected vs actual outcomes:** repository skeleton created as specified; preflight runs and returns nonzero when I2C is absent (expected on a bare Pi)
- **Metrics captured:** see `reports/phase-01/hardware-inventory.md`
- **Known risks / defects:** I2C/SPI disabled (Phase 03); fan/power blocker (STG-004) open
- **Evidence paths / links:** `reports/phase-01/hardware-inventory.md`, `reports/phase-01/preflight-baseline.txt`
- **Gate decision:** PASS (repository usable; parity contract capture deferred — see below)
- **Approved by / date:** pending user sign-off

## STG-001 — Create `project_stingray`

### Implemented

Repository skeleton created on the Pi at `/home/ravik/src_git/project_stingray`
and tracked by Git (remote `github.com/pain459/project_stingray`).

```text
project_stingray/
├── README.md
├── STINGRAY_IMPLEMENTATION_PLAN.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
├── pyproject.toml
├── .env.example
├── .gitignore
├── .editorconfig
├── config/
│   ├── application.example.yaml
│   ├── sensors.example.yaml
│   └── hardware.example.yaml
├── src/stingray/
│   ├── __init__.py
│   ├── main.py
│   ├── core/ hardware/ sensors/ processing/ storage/
│   └── api/ display/ alerts/ observability/
├── tests/
│   ├── unit/ integration/ hardware/ fixtures/
├── scripts/
│   └── preflight.sh
├── deploy/systemd/
├── docs/
│   ├── adr/ architecture/ hardware/ compatibility/ development/ operations/
└── reports/
    ├── phase-00/
    └── phase-01/
```

### Acceptance criteria

- [x] Remote and local repository exist; clean checkout succeeds on Pi and dev machine.
- [x] `main` contains only approved scaffolding; no prebuilt application archive imported.
- [x] Secrets, `.venv`, `*.db`, logs and generated reports with private data are ignored
      (see `.gitignore`).
- [x] README states purpose, supported hardware, setup prerequisites and execution order.
- [x] Branch/PR strategy documented (`CONTRIBUTING.md`).
- [ ] Branch protection enforced on the remote — **requires GitHub UI action by the owner**
      (cannot be set from the CLI without an admin-scoped token).

### Evidence

- Remote: `https://github.com/pain459/project_stingray`
- Initial/baseline commit: `6cfbf40`
- Skeleton commit: see `git log` after this phase.

## STG-002 — Capture old ESP32 behavior (migration contract)

### Status: DEFERRED (blocked)

The ESP32 device is not currently reachable / not in scope for this run
(user directed to skip). The following remain outstanding and must be captured
before Phase 08 API compatibility work:

- [ ] Archive the current ESP32 firmware revision (separately, unaltered).
- [ ] Capture real `/api/status` and `/api/history` responses (redacted).
- [ ] Record collection intervals, retention, Telegram thresholds, quiet periods,
      display pages, boot/error behaviour, mDNS names.
- [ ] Save fixtures under `tests/fixtures/esp32/`.
- [ ] Build the parity matrix (`docs/compatibility/esp32-parity.md`).

**Blocking impact:** Phase 08 (`FEATURES_READY`) cannot pass without this contract.
Phases 01–07 and 09 are not blocked by it.

## Phase 00 exit gate

**Result: PASS (conditional)**

Repository is usable and structured per plan. The migration contract (STG-002)
is explicitly deferred and tracked as a blocker for Phase 08, not silently assumed.
