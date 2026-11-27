# Stingray systemd service reference

> **Current model (2026-11-27):** the Pi has **no git checkout**. GitHub builds
> one arm64 release and the Pi runs exactly that artifact from
> `/opt/stingray/current` → `/opt/stingray/releases/<version>`. For building,
> releasing, bootstrapping and rollback see
> [`release-and-deploy.md`](release-and-deploy.md). This document describes the
> host prerequisites, the installed units, operations and troubleshooting.

## What runs where

| Path | Owner | Contents |
|---|---|---|
| `/opt/stingray/releases/<version>/` | `ravik:monusr`, `2775` | Immutable release: app, config, units, complete venv |
| `/opt/stingray/current` | symlink | Points at the active release; every unit uses this path |
| `/opt/stingray/downloads` | `ravik:monusr` | Downloaded tarballs and checksums |
| `/mnt/stingray` | `monusr` | Parquet data and summaries — **never** in a release |
| `/etc/stingray/telegram.env` | `root:root`, `0600` | Credentials, injected by systemd drop-ins |
| `/home/ravik/bin/stingray-deploy` | `ravik` | Poll/install/rollback/status/verify command |

Nothing on the Pi is built, compiled or dependency-resolved. The release tarball
contains `src/`, `config/`, `deploy/`, and a self-contained `.venv` built for
`aarch64` with Python 3.13.5 and the `hardware` extra (`smbus2`, `spidev`,
`RPi.GPIO`).

## One-time host prerequisites

These were installed at cutover and do not change with a release. They are
listed here for disaster recovery of a fresh host.

1. **Enable buses** in `/boot/firmware/config.txt`:

   ```ini
   dtparam=i2c_arm=on
   dtparam=spi=on
   ```

   Load `i2c-dev` at boot via `/etc/modules-load.d/i2c-dev.conf`, then reboot
   and confirm `/dev/i2c-1`, `/dev/spidev0.0` and `/dev/spidev0.1` exist.

2. **Create the service user** with only the bus groups it needs:

   ```sh
   sudo useradd --system --no-create-home --shell /usr/sbin/nologin monusr
   sudo usermod -aG i2c,spi,gpio monusr
   ```

   | Group | Purpose |
   |---|---|
   | `i2c` | `/dev/i2c-1` — sensor bus |
   | `spi` | `/dev/spidev0.0` — TFT display |
   | `gpio` | `/dev/mem` via `RPi.GPIO` — display control pins |

3. **Create host-owned directories:**

   ```sh
   sudo mkdir -p /var/log/stingray
   sudo chown monusr:monusr /var/log/stingray
   ```

4. **Install fonts** for the TFT: `sudo apt install -y fonts-dejavu-core`.

5. **Install the units and deploy command** by running
   `scripts/pi-bootstrap.sh` with sudo from a temporary GitHub checkout (the
   Pi normally keeps no checkout). Bootstrap creates `/opt/stingray`, installs
   the units, and enables **and starts** the timers without restarting the
   running service.

6. **Narrow sudoers rule** in `/etc/sudoers.d/stingray-deploy` permits only
   `/usr/bin/systemctl restart stingray.service` without a password. Bootstrap
   itself needs full sudo and must be run interactively.

## Installed units

| Unit | Purpose |
|---|---|
| `stingray.service` | Main application; runs as `monusr`, restarts on failure |
| `stingray-hourly-summary.timer` / `.service` | Hourly local-LLM summary and Telegram delivery |
| `stingray-deploy.timer` / `.service` | Polls GitHub Releases every 30 minutes |

`stingray.service` (canonical source: `deploy/stingray.service` in the release):

```ini
[Unit]
Description=Stingray Air Quality Monitor
After=network.target
Wants=network-online.target
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
User=monusr
Group=monusr
WorkingDirectory=/opt/stingray/current
ExecStart=/opt/stingray/current/.venv/bin/python -m stingray.main --config /opt/stingray/current/config/application.combined.yaml
Restart=on-failure
RestartSec=5
Environment=PATH=/home/ravik/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
SyslogIdentifier=stingray

[Install]
WantedBy=multi-user.target
```

| Setting | Value | Purpose |
|---|---|---|
| `User` / `Group` | `monusr` | Unprivileged execution |
| `WorkingDirectory` | `/opt/stingray/current` | Relative paths resolve inside the active release |
| `Restart` / `RestartSec` | `on-failure` / `5` | Auto-recover from crashes |
| `StartLimitBurst` / `StartLimitIntervalSec` | `5` / `60` | Cap restart storms |
| `PYTHONUNBUFFERED` | `1` | Immediate journal output |

The summary unit also uses `/opt/stingray/current/.venv/bin/python` and
`/opt/stingray/current/config/application.combined.yaml`.

## Telegram credentials

Credentials are never stored in the repository, release tarball, config file,
summary files or logs. The service reads them only from `/etc/stingray/telegram.env`
(root-owned, `0600`). Because `monusr` cannot read that file, a systemd drop-in
at `/etc/systemd/system/stingray.service.d/telegram.conf` injects the variables.
Bootstrap preserves an existing drop-in and installs the one from
`/opt/stingray/current/deploy/stingray-telegram.conf` only if missing. Both the
hourly summary service and the dashboard's **Generate now** button need it.

See [`hourly-summary.md`](hourly-summary.md) for the env var names and delivery
details.

## Operations

```sh
systemctl status stingray.service                     # state and recent logs
journalctl -u stingray.service -f                     # follow logs
journalctl -u stingray.service --since "1 hour ago"
sudo systemctl restart stingray.service               # also done by a deploy
systemctl list-timers --all | grep stingray
/home/ravik/bin/stingray-deploy status                # version, sha, service state
```

## Updating the application

Push a `v*` tag on `pathfinder` after evaluating the build on GitHub. The Pi
polls within 30 minutes, or force a check:

```sh
/home/ravik/bin/stingray-deploy poll
/home/ravik/bin/stingray-deploy status
curl -fsS http://127.0.0.1:8081/api/health | jq
```

A deploy verifies SHA256, flips `/opt/stingray/current`, restarts the service and
waits for `/api/health`; it rolls back automatically on failure. Manual rollback:

```sh
/home/ravik/bin/stingray-deploy rollback
```

Only the three newest release directories are kept. Never run git commands or
`uv sync` on the Pi.

## Validating the combined SHT45 + SGP41 module

The SHT45 (temperature/RH at `0x44`) and SGP41 (VOC/NOx at `0x59`) are **one
physical 2-in-1 module** on a single board with two I²C addresses. It shares the
bus with the separate SCD4X (`0x62`) and SPS30 (`0x69`) modules. The SHT45 is the
authoritative T/RH source and compensates the SGP41 gas readings on the same
board.

**Stop the service first** — two processes issuing command-then-read
transactions on the same bus interleave and produce spurious `EIO` errors. The
validator is a diagnostic script and is not bundled in the release tarball; copy
it from GitHub to a temporary path on the Pi:

```sh
sudo systemctl stop stingray.service
/opt/stingray/current/.venv/bin/python /tmp/validate_gas_sensors.py --seconds 1800
sudo systemctl start stingray.service
```

Expected startup sequence: serial/self-test (`SGP41 self-test=0xD400`), then
`SHT4x: ONLINE` and `SGP41: ONLINE`, 10 conditioning cycles, raw VOC/NOx values
with indices still `WARM`, then `READY` with integer indices in 1–500. The gas
indices come from a pure-Python port of Sensirion's Gas Index Algorithm v3.2.0,
not from raw signals; if the SHT45 is unavailable the SGP41 falls back to
datasheet defaults (25 °C / 50 %RH).

## Metrics notes

- SPS30 typical particle size uses the uint16 field, which is in **nm**; the
  driver divides by 1000 to publish `tps_um` in µm (raw 566 → 0.566 µm).
- `/api/live` includes a `network` object with active WiFi interface, IP and
  RSSI (dBm) from standard library calls; no SSID, password, MAC or route is
  exposed.

## Troubleshooting

```sh
journalctl -u stingray.service --no-pager -n 50
curl -fsS http://127.0.0.1:8081/api/health | jq
sudo i2cdetect -y 1          # expect 44, 59, 62, 69 on bus 1
/home/ravik/bin/stingray-deploy status
```

| Symptom | Check |
|---|---|
| Service will not start | Journal; device nodes; port 8081 in use; then redeploy a known-good release |
| Sensor errors | `i2cdetect` addresses; bus groups for `monusr`; never run two I²C readers at once |
| Display not initializing | SPI enabled; fonts installed; `monusr` in `gpio`; journal grep `display` |
| Restart loop | `journalctl -u stingray.service -n 100`; `sudo systemctl reset-failed stingray.service` then start |
| Deploy rolled back | Checksum/network; `/api/health`; fix forward with a new tag, never re-tag |

## Security notes

- The service runs as `monusr`, not root; `monusr` has no login shell or home.
- No sandbox hardening (`ProtectSystem`, `PrivateTmp`) is applied because the
  app needs `/mnt`, device nodes and the release tree.
- Credentials stay root-owned outside the release; the deploy command needs no
  sudo except the narrow service restart.
- Future hardening candidates: `ProtectSystem=full` with explicit
  `ReadWritePaths=`, `NoNewPrivileges=true`, `MemoryMax=512M`.
