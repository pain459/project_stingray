# project_stingray

Home automation project with AQI and other sensors.

Migrates the existing ESP32-S3 air-quality monitoring application to a
Raspberry Pi 4 Model B Rev 1.1 (host: `stingray`) with sensor parity,
operational reliability, and maintainable software.

## Supported hardware

- Raspberry Pi 4 Model B Rev 1.1 (ARM64, Debian 13)
- SPS30 particulate matter sensor (I²C `0x69`)
- SCD4x CO₂/temperature/RH sensor (I²C `0x62`)
- Combined SHT45 + SGP41 **2-in-1 module** — temperature/RH at `0x44` and
  VOC/NOx at `0x59` on one physical board
- ILI9341 2.8-inch SPI display
- DFRobot DFR0918 terminal-block HAT

## Setup prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- I2C and SPI enabled on the Pi (see [`docs/hardware/wiring.md`](docs/hardware/wiring.md))
- `i2c-tools`, `gpiod`, `spi-tools` installed

## Quick start

```sh
uv sync --all-extras
uv run pytest          # mock-mode tests, no hardware required
uv run stingray        # run the application (mock mode by default)
```

## Deployment

GitHub builds one arm64 release and the Pi runs exactly that artifact from
`/opt/stingray/current`. There is **no git checkout on the Pi**; a timer polls
for new `v*` tags every 30 minutes with checksum verification and automatic
rollback. See
[`docs/deployment/release-and-deploy.md`](docs/deployment/release-and-deploy.md)
for the release process, bootstrap, verification and rollback, and
[`docs/deployment/systemd-service.md`](docs/deployment/systemd-service.md)
for the service/unit reference and troubleshooting.

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

## Execution order

Work proceeds in phases defined in `STINGRAY_IMPLEMENTATION_PLAN.md`:

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
