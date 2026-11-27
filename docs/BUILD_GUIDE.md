# Project Stingray — Build Guide

This guide takes you from a box of parts to a running, self-contained air-quality
monitor: four real Sensirion sensors, an ILI9341 TFT, local Parquet history, a
LAN web dashboard, and optional hourly local-LLM summaries delivered to
Telegram.

Every value served or stored by the running system comes from physical sensors.
There is a mock mode for development and tests only; it is never used on the
Pi in production.

Related documents:

- [`hardware/wiring.md`](hardware/wiring.md) — pinouts, bus mapping, power rules
- [`hardware/sensors.md`](hardware/sensors.md) — sensor-by-sensor details
- [`hardware/inventory.md`](hardware/inventory.md) — baseline snapshot of the original host
- [`DESIGN_AND_DECISIONS.md`](DESIGN_AND_DECISIONS.md) — architecture, rationale, progress
- [`deployment/systemd-service.md`](deployment/systemd-service.md) — service reference
- [`deployment/hourly-summary.md`](deployment/hourly-summary.md) — LLM summary and Telegram

---

## 1. What you are building

```
SHT45 ─┐
SCD4X ─┼─ I²C (/dev/i2c-1) ─┐
SGP41 ─┤                     ├─ scheduler ──> Parquet on SSD ──> DuckDB queries
SPS30 ─┘                     │                      │
                             │                      ├─> LAN dashboard (port 8081)
ILI9341 ── SPI (/dev/spidev0.0) ─> TFT dashboard     └─> hourly local LLM summary
                                                             ├─> dashboard card
                                                             └─> Telegram (HTML)
```

The deterministic Python layer owns every threshold, finding, and recommended
action. A small local Ollama model is allowed only to phrase the headline and
summary text; its numbers and claims are validated and rejected if unapproved.

Two machines are used during development:

| Host | Role |
|---|---|
| `pathfinder` | x86 development box — edit, lint, test, build |
| `stingray` | Raspberry Pi 4 — the only machine that runs the service and sensors |

Workflow: develop on `pathfinder`, push to GitHub, pull on `stingray`, restart
the service, verify on the Pi. Never develop directly on the Pi.

---

## 2. Bill of materials

| Item | Required | Notes |
|---|---|---|
| Raspberry Pi 4 Model B Rev 1.1 | yes | 2 GB RAM is enough; 64-bit OS |
| microSD card for OS | yes | Original build used a 238 GB card |
| USB SSD for data | yes | Mount at `/mnt/stingray`; original build used `/dev/sda1` ext4 |
| 5 V USB-C Pi power supply | yes | Adequate for Pi 4; avoid undervoltage |
| Sensirion SPS30 | yes | Particulate matter, **I²C mode** at `0x69` |
| Sensirion SCD4X (SCD41 verified) | yes | CO₂, temperature, RH at `0x62` |
| Sensirion SHT45 + SGP41 **2-in-1 module** | yes | One physical board: temperature/RH at `0x44` (authoritative, compensates SGP41) and VOC/NOx at `0x59` |
| ILI9341 2.8-inch SPI TFT | yes | 320×240, 3.3 V logic |
| DFRobot DFR0918 terminal-block HAT | yes | Terminal breakout only — **not** a regulated power supply |
| Jumper wires / terminal connectors | yes | Common ground across all boards is mandatory |
| Fan | optional | The original build runs **without** a fan; see note below |

**Power warning:** the DFR0918 is a GPIO terminal breakout, not a 5 V or 3.3 V
regulator. Do not feed 5 V into any Pi logic pin, do not power a sensor from a
weak GPIO output, and do not tie independent supplies together without an
intentional, reviewed design. An unidentified fan must never be connected
directly to GPIO. The original host had no fan attached; see
[`hardware/inventory.md`](hardware/inventory.md) for the historical boot note.

---

## 3. OS preparation

Install 64-bit Debian 13 (trixie) for Raspberry Pi. The verified host ran
kernel `6.18.60+rpt-rpi-v8`, Python 3.13.5, `uv` 0.13.9.

Enable the buses in `/boot/firmware/config.txt`:

```ini
dtparam=i2c_arm=on
dtparam=spi=on
```

Ensure `i2c-dev` loads at boot:

```sh
echo i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf
sudo reboot
```

Install OS tooling:

```sh
sudo apt update
sudo apt install -y i2c-tools spi-tools gpiod python3-libgpiod fonts-dejavu-core git curl jq
```

Verify device nodes:

```sh
ls -l /dev/i2c-1 /dev/spidev0.0
```

Mount the SSD and create the data directories:

```sh
sudo mkdir -p /mnt/stingray/parquet /mnt/stingray/summaries
sudo chown -R monusr:monusr /mnt/stingray
```

Run the built-in preflight to confirm buses, thermals and throttling:

```sh
bash scripts/preflight.sh
```

---

## 4. Software installation

Create the unprivileged service account and grant only the bus groups it needs:

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin monusr
sudo usermod -aG i2c,spi,gpio monusr
```

The Pi does **not** clone the repository or resolve dependencies. GitHub builds
one arm64 release containing the application and a complete venv; the Pi
downloads and verifies it. Only host-level prerequisites are created locally:

```sh
sudo mkdir -p /var/log/stingray
sudo chown monusr:monusr /var/log/stingray
```

For a fresh host, run `scripts/pi-bootstrap.sh` from a GitHub checkout to
install `/opt/stingray`, the static systemd units and the deploy command.
Development and release builds happen on `pathfinder` (`uv sync --all-extras`);
see [`deployment/release-and-deploy.md`](deployment/release-and-deploy.md).

The release venv already contains `smbus2`, `spidev` and `RPi.GPIO` from the
`hardware` extra, plus the core dependencies (pydantic, DuckDB, PyArrow,
PyYAML, Pillow) from `pyproject.toml`. Nothing is compiled or installed on the Pi.

Sanity-check the code without hardware first:

```sh
uv run ruff check src/ tests/
uv run pytest -q
```

Mock-mode tests must pass on any machine. Hardware tests are marked and
excluded by default.

---

## 5. Configuration

The Pi uses `config/application.combined.yaml`. Key values:

| Key | Value | Purpose |
|---|---|---|
| `mode` | `hardware` | real sensors only |
| `api.port` | `8081` | port 8080 is occupied on the Pi |
| `storage.path` | `/mnt/stingray/parquet` | durable history on SSD |
| `storage.retention_days` | `30` | automatic purge window |
| `sensors.*.i2c_bus` | `1` | `/dev/i2c-1` |
| `sensors.*.address` | see below | `0x44`, `0x62`, `0x59`, `0x69` |
| `display.spi_device` | `/dev/spidev0.0` | TFT SPI bus/CE |
| `alerts.thresholds` | see below | deterministic guidance limits |
| `summary.model` | `qwen2.5:0.5b` | local LLM |
| `summary.refresh_cooldown_seconds` | `600` | on-demand button cooldown |

| Sensor | Address (decimal) | Cadence |
|---|---|---|
| SHT45 | `0x44` (68) | 1 s |
| SCD4X | `0x62` (98) | 5 s |
| SGP41 | `0x59` (89) | 1 s |
| SPS30 | `0x69` (105) | 10 s |

Thresholds: PM2.5 35 µg/m³, CO₂ 1000 ppm, VOC index 100, NOx index 150,
RH 30–65 %, temperature 30 °C. These are advisory air-quality guides, not
medical limits.

---

## 6. Services

Three systemd units work together:

| Unit | Purpose |
|---|---|
| `stingray.service` | main acquisition + API + TFT service, runs as `monusr` |
| `stingray-hourly-summary.timer` / `.service` | hourly local-LLM summary, `Nice=15`, one-shot |
| `ollama.service` | local inference server on `127.0.0.1:11434` |

Install and start the main service:

```sh
sudo cp deploy/stingray.service /etc/systemd/system/stingray.service
sudo systemctl daemon-reload
sudo systemctl enable --now stingray.service
```

> The repository ships the main unit's reference text in
> `docs/deployment/systemd-service.md`; if `deploy/stingray.service` is absent,
> copy the unit from that document.

Hourly summary:

```sh
sudo cp deploy/stingray-hourly-summary.service /etc/systemd/system/
sudo cp deploy/stingray-hourly-summary.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stingray-hourly-summary.timer
```

Telegram credentials never go in the repository or YAML. They live in
`/etc/stingray/telegram.env` (root-owned, mode 0600) and are injected into the
services with systemd drop-ins:

```sh
sudo install -d -m 0755 /etc/systemd/system/stingray.service.d
sudo install -m 0644 deploy/stingray-telegram.conf \
  /etc/systemd/system/stingray.service.d/telegram.conf
sudo install -d -m 0755 /etc/systemd/system/stingray-hourly-summary.service.d
sudo install -m 0644 deploy/stingray-telegram.conf \
  /etc/systemd/system/stingray-hourly-summary.service.d/telegram.conf
sudo systemctl daemon-reload
sudo systemctl restart stingray.service
```

Set the two variables in the env file: `STINGRAY_TELEGRAM_BOT_TOKEN` and
`STINGRAY_TELEGRAM_CHAT_ID`. Do not print the file or commit it.

Ollama:

```sh
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:0.5b
```

The verified Ollama drop-in (`/etc/systemd/system/ollama.service.d/10-stingray.conf`)
keeps inference local and bounded:

```ini
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_NO_CLOUD=1"
Environment="OLLAMA_KEEP_ALIVE=0"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_CONTEXT_LENGTH=1024"
```

---

## 7. Verify the build

1. **Sensor buses:** `scripts/probe_i2c.py` reads serial numbers, firmware and a
   live sample from each Sensirion device and checks CRCs.
2. **System preflight:** `bash scripts/preflight.sh` checks OS, memory, storage,
   device nodes, throttling, temperature and services.
3. **Live values:** `curl -s http://127.0.0.1:8081/api/live | jq`
4. **History:** `curl -s 'http://127.0.0.1:8081/api/history?metric=pm2_5_ug_m3&hours=24' | jq`
5. **Health:** `curl -s http://127.0.0.1:8081/api/health | jq`
6. **Dashboard:** open `http://<pi-address>:8081/` on the LAN.
7. **Summary:** `curl -s http://127.0.0.1:8081/api/summaries/latest | jq`
8. **Manual summary run (no Telegram):**
   `/opt/stingray/current/.venv/bin/python -m stingray.summary_job --config /opt/stingray/current/config/application.combined.yaml --no-telegram`

Expected healthy signals: all four logical sensor streams `READY` in the
journal (SHT45 and SGP41 are two streams on the one 2-in-1 module), no I²C CRC
mismatches, `vcgencmd get_throttled` = `0x0`, and live values changing at their
configured cadence.

**Do not run `scripts/validate_gas_sensors.py` while `stingray.service` is
running** — two processes on the same I²C bus corrupt reads. Stop the service
first, run the validator, then restart.

---

## 8. Daily operation

- The dashboard polls `/api/live` every 5 s, `/api/health` every 30 s, and
  history every 30 s (short ranges) or 5 min (long ranges).
- The **Generate now** button runs the full pipeline on demand: real Parquet
  aggregate → deterministic rules → local Ollama → save → dashboard →
  Telegram. It is rate-limited to one run per 10 minutes, enforced
  server-side in `/mnt/stingray/summaries/.refresh_state.json`.
- Telegram messages use HTML formatting: bold section headers, a compact
  seven-metric `<code>` card (average + min–max, display units), previous-hour
  comparison, notes, practices, and a footer. Raw sensor signals are omitted.
  Long messages are split at line boundaries, never truncated.
- Hourly summaries use the same pipeline via systemd.

Useful commands:

```sh
sudo systemctl status stingray.service
sudo journalctl -u stingray.service -n 50 --no-pager
sudo systemctl status stingray-hourly-summary.timer
sudo systemctl restart stingray.service
```

---

## 9. Known constraints and traps

- **DuckDB thread budget:** read queries are capped at 2 threads
  (`connect_read_limited()` in `src/stingray/storage/parquet_store.py`).
  DuckDB's 32-thread default combined with 5-second dashboard polling
  saturates all four Pi cores and starves Ollama, causing 5-minute inference
  timeouts and deterministic fallbacks.
- **Classic scripts share one global lexical scope:** every dashboard JS file
  must remain an IIFE, or a `const` redeclaration kills the whole file.
- **Scrape-safe API:** `/api/live` must stay time-bounded (2 h for latest,
  24 h for count); a full scan over the backfill times out on the Pi.
- **Storage must be on SSD:** the SD card is for the OS only.
- **No mock data in production:** `mode: hardware` is mandatory on the Pi.
- **Sensirion I²C reads need `i2c_msg.read` + `i2c_rdwr`**, not
  `read_i2c_block_data`; command frames need CRC-8.
- **SGP41 needs SHT45 compensation** and a 10-cycle conditioning period at
  startup before indices are meaningful.
- **SPS30 typical particle size is in nm on the wire** and is divided by 1000
  before publishing as µm.

---

## 10. Development and updates

```sh
# on pathfinder: bump version, evaluate a dry run, then tag
git tag v0.2.0 && git push origin v0.2.0

# on stingray: the timer picks it up within 30 minutes, or force a check
/home/ravik/bin/stingray-deploy poll
/home/ravik/bin/stingray-deploy status
sudo journalctl -u stingray.service -n 20 --no-pager
```

Hard-refresh the browser after a dashboard change; script tags carry `?v=N`
cache-busting versions that must be bumped in
`src/stingray/api/static/index.html`.
