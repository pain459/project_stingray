# Phase 01 Acceptance Report

- **Phase / Jira epic:** Phase 01 — Baseline, power, boot and cooling safety (`STG-P01`)
- **Date and implementer:** 2026-11-24, AI agent (executed on `stingray` via SSH)
- **Git SHA:** baseline `6cfbf40`
- **OS / kernel / Python:** Debian 13 (trixie) / `6.18.60+rpt-rpi-v8` / Python 3.13.5
- **Hardware revisions / wiring version:** Pi 4 Model B Rev 1.1 (`b03111`); no HAT/sensors wired
- **Configuration revision (secrets redacted):** stock `config.txt` (I2C/SPI disabled)
- **Tests performed / commands:**
  - `hostnamectl`, `/etc/os-release`, `uname -a`, `/proc/device-tree/model`
  - `free -h`, `lsblk`, `df -h`, `id`
  - `vcgencmd get_throttled`, `vcgencmd measure_temp`, `vcgencmd get_config int`
  - `systemctl --failed`, `dmesg` scan for I/O errors
  - `bash scripts/preflight.sh` and `--json`
- **Expected vs actual outcomes:** all baseline facts captured; preflight correctly
  returns nonzero because I2C is not enabled (expected on a bare Pi)
- **Metrics captured:** see `reports/phase-01/hardware-inventory.md`
- **Known risks / defects:** fan/power boot blocker (STG-004) unresolved; no thermal soak yet
- **Evidence paths / links:** `reports/phase-01/hardware-inventory.md`,
  `reports/phase-01/preflight-baseline.txt`
- **Gate decision:** **POWER_STABLE = PARTIAL / BLOCKED** (see below)
- **Approved by / date:** pending user sign-off

## STG-003 — Capture system inventory

### Implemented

- Full inventory captured → `reports/phase-01/hardware-inventory.md`.
- `scripts/preflight.sh` written: emits human-readable and `--json` output,
  returns nonzero on hard failures (missing I2C node, throttling).
- Baseline saved → `reports/phase-01/preflight-baseline.txt`.

### Preflight checks implemented

| Check | Type | Fails build? |
|---|---|---|
| `os` (distro/kernel/arch/model) | info | no |
| `memory` | info | no |
| `storage` (root avail/use) | info | no |
| `i2c_devices` | mandatory | **yes** |
| `spi_devices` | warn | no |
| `throttling` (`get_throttled`) | mandatory | **yes** |
| `temperature` | info | no |
| `systemd` failed units | warn | no |
| `uptime` | info | no |
| `user_groups` (i2c/spi/gpio) | warn | no |

### Acceptance criteria

- [x] Baseline report has OS, kernel, hardware revision, memory, storage, interface
      and throttling information.
- [ ] SSH reconnect and clean reboot verified — **pending** (reboot not yet performed
      in this session; must be done before Phase 03).
- [x] Preflight returns nonzero on missing mandatory device nodes / failed critical checks.
- [x] Baseline collected before any electrical modification (none performed yet).

## STG-004 — Diagnose fan-related boot failures (critical blocker)

### Status: NOT STARTED (physical / electrical)

Requires hands-on inspection and a multimeter. Software-side facts recorded:

- No fan is currently connected; the Pi booted and is stable.
- `get_throttled=0x0` at idle, ~49 °C, no undervoltage in `dmesg`.
- Plan's known risk stands: Pi reportedly boots only with fan disconnected.

**Cannot be closed by remote software work.** Staged tests A–E require the operator.

## STG-005 — Cooling / SD-card operational baseline

### Status: PARTIAL

- Idle thermal observed (~49–50 °C) via the pre-existing `~/stingray-diagnostics/thermal.sh`
  sample script (5 s cadence, logs `temp` + `get_throttled`).
- Time sync confirmed (`systemd-timesyncd` active, synchronized; TZ `Asia/Kolkata`,
  storage will be UTC).
- No sustained-load thermal profile yet; no fan installed.
- SD free space ample (220 GB free of 235 GB).

Remaining: sustained-load thermal profile with fans enabled, 5 s sampling,
target < 75 °C and zero throttling, plus a documented SD backup method.

## Phase 01 exit gate

**POWER_STABLE = BLOCKED**

Rationale: STG-004 (fan/boot root cause) is a physical/electrical task that has not
been performed. Per the plan's stop-work rule, **physical sensor wiring must not begin**
until this gate passes. Mock/software work (Phases 02, 04, 06) may continue in parallel.

### Next actions

1. Operator: inspect fan wiring/connector/rails; perform staged tests A–E with a meter.
2. Operator: verify five consecutive cold boots with intended cooling.
3. Then re-run `scripts/preflight.sh` before/after and record results here.
4. Confirm clean reboot + SSH reconnect (STG-003 outstanding item).
