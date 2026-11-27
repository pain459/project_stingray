# project_stingray

Home automation project with AQI and other sensors.

The ESP32-S3 air-quality application has been migrated to a Raspberry Pi 4
Model B Rev 1.1 (host: `stingray`) with sensor parity, durable history and a LAN
dashboard.

**This repository on `pathfinder` is the only development checkout and the place
where changes and release tags are pushed.** GitHub is the release source of
truth; the Pi has no git checkout and never builds or resolves dependencies.

## Supported hardware

- Raspberry Pi 4 Model B Rev 1.1 (ARM64, Debian 13)
- SPS30 particulate matter sensor (I²C `0x69`)
- SCD4x CO₂/temperature/RH sensor (I²C `0x62`)
- Combined SHT45 + SGP41 **2-in-1 module** — temperature/RH at `0x44` and
  VOC/NOx at `0x59` on one physical board
- ILI9341 2.8-inch SPI display
- DFRobot DFR0918 terminal-block HAT

## Setup prerequisites

Development (on `pathfinder`):

- Python 3.11+ (release builds use Python 3.13.5 to match the Pi)
- [uv](https://docs.astral.sh/uv/)

Pi host (installed once at provisioning; see
[`docs/hardware/wiring.md`](docs/hardware/wiring.md)):

- I2C and SPI enabled
- `i2c-tools`, `gpiod`, `spi-tools` installed

## Quick start (on `pathfinder`)

```sh
uv sync --all-extras
uv run pytest          # mock-mode tests, no hardware required
uv run stingray        # run the application (mock mode by default)
```

Mock mode is for tests and development on `pathfinder` only. Production on
`stingray` runs `mode: hardware` from a GitHub-built arm64 release.

## Deployment

GitHub builds one arm64 release and the Pi runs exactly that artifact from
`/opt/stingray/current`. There is **no git checkout on the Pi**; a timer polls
for new `v*` tags every 30 minutes with checksum verification and automatic
rollback. See
[`docs/deployment/release-and-deploy.md`](docs/deployment/release-and-deploy.md)
for the release process, bootstrap, verification and rollback, and
[`docs/deployment/systemd-service.md`](docs/deployment/systemd-service.md)
for the service/unit reference and troubleshooting.

Development and release workflow:

1. Make and push changes from this checkout on `pathfinder`.
2. Evaluate a GitHub build dry run, then tag `v*` from this repository.
3. GitHub builds the arm64 tarball + checksum and publishes the release.
4. The Pi polls every 30 minutes, deploys the exact artifact and health-checks
   it, rolling back automatically on failure.

Never develop, build or git-pull on the Pi.

## Documentation

- [`docs/BUILD_GUIDE.md`](docs/BUILD_GUIDE.md) — end-to-end build from parts to
  running service (bill of materials, OS, config, services, verification)
- [`docs/hardware/wiring.md`](docs/hardware/wiring.md) — pinouts, bus map,
  power and grounding rules
- [`docs/hardware/sensors.md`](docs/hardware/sensors.md) — sensor commands,
  conversions, warm-up and units
- [`docs/hardware/inventory.md`](docs/hardware/inventory.md) — baseline snapshot
  of the original host
- [`docs/DESIGN_AND_DECISIONS.md`](docs/DESIGN_AND_DECISIONS.md) — architecture,
  design rationale and progress
- [`docs/deployment/release-and-deploy.md`](docs/deployment/release-and-deploy.md) —
  release process, Pi auto-deploy and rollback
- [`docs/deployment/systemd-service.md`](docs/deployment/systemd-service.md) —
  service/unit reference and troubleshooting
- [`docs/deployment/hourly-summary.md`](docs/deployment/hourly-summary.md) —
  LLM summary, Telegram formatting and refresh cooldown

## Project phases (completed)

All phases in `STINGRAY_IMPLEMENTATION_PLAN.md` are done; the plan is kept for
historical context only. Future changes follow the release workflow above.

1. Phase 00 — Repository bootstrap and scope capture
2. Phase 01 — Baseline, power, boot and cooling safety
3. Phase 02 — Toolchain, local workflow and CI
4. Phase 03 — Wiring specification and bus preflight
5. Phase 04 — Application foundation and mock mode
6. Phase 05 — Integrate sensors individually
7. Phase 06 — Processing, local storage and history
8. Phase 07 — Combined sensors and physical data quality
9. Phase 08 — API, alerting and compatibility
10. Phase 09 — ILI9341 display and local interaction
11. Phase 10 — Deployment, telemetry, faults and endurance
12. Phase 11 — Documentation, release and handover

## License

MIT
