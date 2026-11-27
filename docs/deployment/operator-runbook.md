# Operator runbook: debug the deployed package on `stingray`

This runbook is for the **deployed** system on the Raspberry Pi. All development,
builds and releases happen on `pathfinder`; the Pi has **no git checkout**, does
no dependency resolution and compiles nothing. If a fix is needed, make it on
`pathfinder`, evaluate the GitHub build and publish a new tag — never edit files
under `/opt/stingray/current` by hand.

For how a release is built and installed, see
[`release-and-deploy.md`](release-and-deploy.md). For the installed units and
host prerequisites, see [`systemd-service.md`](systemd-service.md).

## Ground rules

1. **Read before you change.** Capture the commands in [Quick triage](#quick-triage)
   before restarting anything; a restart can erase the in-memory symptom.
2. **Journal is authoritative.** `stingray.service` sends stdout/stderr to the
   journal (`SyslogIdentifier=stingray`). `/var/log/stingray/` exists but
   `/var/log/stingray/stingray.log` is normally **absent** — do not conclude
   "no logs" from that file alone.
3. **Never expose credentials.** `/etc/stingray/telegram.env` is root-owned
   `0600` and injected through a systemd drop-in. Reference only the path and
   the env var names (`STINGRAY_TELEGRAM_BOT_TOKEN`,
   `STINGRAY_TELEGRAM_CHAT_ID`); never print, copy or paste their values.
4. **One I²C reader at a time.** Never run `scripts/validate_gas_sensors.py`
   while `stingray.service` is active. Two command-then-read transactions on the
   same bus produce spurious `EIO` errors.
5. **Deployments are health-gated.** The deploy CLI flips the symlink, restarts
   the service and waits up to 40 seconds for `/api/health`; on failure it rolls
   back automatically. A failed deploy means "fix forward with a new tag", not
   "re-tag or hand-edit the old asset".
6. **Static units do not come from releases.** `stingray.service`, timers and
   sudoers drop-ins are installed once at bootstrap. Changing a release does not
   change unit contents. The deploy poll interval is `OnCalendar=*:0/30`.

### Connect

```sh
ssh -i /home/ravik/.ssh/id_ed25519_stingray ravik@192.168.68.53
```

Dashboard: <http://192.168.68.53:8081/>. Local API checks on the Pi use
`http://127.0.0.1:8081`.

## Known-good baseline (verified 2026-11-27)

| Item | Expected value |
|---|---|
| Active release | `/opt/stingray/current -> /opt/stingray/releases/0.2.1` |
| `VERSION` / `deployed_version` | `0.2.1` |
| `BUILD_SHA` | `a07e1afd10602aec4a72586fa32abc7cac44545f` |
| `stingray.service` | `active` (running as `monusr`) |
| `stingray-deploy.timer` | `active`, next run at `*:00` / `*:30` |
| `stingray-hourly-summary.timer` | `active`, next run at the hour boundary |
| API port | `0.0.0.0:8081` listening |
| `/api/health` | `mode: hardware`, all four sensors `reads_failed: 0` |
| Sensor addresses on bus 1 | `0x44` SHT45, `0x59` SGP41, `0x62` SCD4X, `0x69` SPS30 |
| Display device | `/dev/spidev0.0` (there is **no** `/dev/fb0`) |
| Data | `/mnt/stingray/parquet/year=…/month=…/day=…/chunk_*.parquet`, `/mnt/stingray/summaries/latest.json` |
| Ollama | `http://127.0.0.1:11434/api/tags` reachable, `smollm2:135m` present |

Compare against this table first; it distinguishes "new release is broken" from
"the Pi was already unhealthy".

## Quick triage

Run these read-only checks in order. Each one narrows the fault domain.

```sh
# 1. What is actually deployed?
/home/ravik/bin/stingray-deploy status
readlink -f /opt/stingray/current
cat /opt/stingray/current/VERSION /opt/stingray/current/BUILD_SHA /opt/stingray/deployed_version

# 2. Is the service up, and is it the new code?
systemctl status stingray.service --no-pager
systemctl cat stingray.service
journalctl -u stingray.service -n 100 --no-pager

# 3. Other units involved
systemctl is-active stingray.service stingray-deploy.timer stingray-hourly-summary.timer
systemctl list-timers 'stingray*' --no-pager
systemctl --failed --no-legend

# 4. Is the API alive and reading hardware?
curl -fsS http://127.0.0.1:8081/api/health
curl -fsS http://127.0.0.1:8081/api/live | head -c 500
ss -ltnp | grep 8081

# 5. Is data being written?
find /mnt/stingray/parquet -name '*.parquet' -printf '%TY-%Tm-%Td %TH:%TM %p\n' | sort -r | head -5
ls -l /mnt/stingray/summaries/latest.json
curl -fsS http://127.0.0.1:8081/api/deploy/status
```

Interpretation:

- Wrong `VERSION`/`BUILD_SHA` → the deploy never happened; go to
  [Deployment did not happen or rolled back](#deployment-did-not-happen-or-rolled-back).
- Right version, service `inactive`/`failed` → [Service will not start](#service-will-not-start-or-enters-a-restart-loop).
- Service active, health shows `reads_failed > 0` or `mode: mock` →
  [Sensor / bus errors](#sensor--bus-errors) or [Data not updating](#data-not-updating).
- Service active, health clean, but TFT blank → [No display](#no-display-on-the-tft).
- Service active, health clean, display fine, but summaries missing →
  [Summaries or Telegram not arriving](#summaries-or-telegram-not-arriving).

## Service will not start or enters a restart loop

```sh
systemctl status stingray.service --no-pager
journalctl -u stingray.service -n 200 --no-pager
journalctl -u stingray.service --since '30 minutes ago' --no-pager
systemctl show stingray.service -p ExecMainStatus -p ExecMainCode -p NRestarts --no-pager
```

A clean startup logs, in order: each sensor `ready on bus 1`, `scheduler started
with sensors: [...]`, `api server listening on http://0.0.0.0:8081`, then
`display initialized`. Use the **last** successful line to locate the failure.

| Where it stops | Likely cause | Next check |
|---|---|---|
| Before any sensor line | Config/venv/import error | `systemctl cat stingray.service`; run the foreground command below; check `VERSION` |
| At one sensor | Bus/device or permission problem | [Sensor / bus errors](#sensor--bus-errors) |
| At `api server listening` | Port conflict or bind failure | `ss -ltnp \| grep 8081`; look for `Address already in use` in the journal |
| At `display initialized` | SPI/gpio/font issue | [No display](#no-display-on-the-tft) |
| Starts, dies, repeats every ~5 s | `Restart=on-failure` plus a persistent exception | Full traceback in journal; `systemctl reset-failed` only after the cause is understood |

To see the real traceback without systemd's restart cycle, stop the service and
run the exact `ExecStart` in the foreground as the service user:

```sh
sudo systemctl stop stingray.service
sudo -u monusr /opt/stingray/current/.venv/bin/python -m stingray.main \
  --config /opt/stingray/current/config/application.combined.yaml
# Ctrl-C when done, then:
sudo systemctl start stingray.service
```

This is a **second sensor reader and a second API listener**: never run it while
the service is active. If it fails immediately, the traceback is the diagnosis.
If it succeeds in the foreground but fails under systemd, the difference is
environment/permissions — compare `systemctl cat` (including drop-ins) and
`groups monusr`.

After a restart storm is resolved:

```sh
sudo systemctl reset-failed stingray.service
sudo systemctl start stingray.service
systemctl status stingray.service --no-pager
curl -fsS http://127.0.0.1:8081/api/health
```

## No display on the TFT

The display is SPI-attached at `/dev/spidev0.0` with GPIO control pins. There is
no `/dev/fb0`; do not look for one.

```sh
journalctl -u stingray.service --no-pager | grep -i -E 'display|spi|gpio|font'
ls -l /dev/spidev0.0
groups monusr                         # expect i2c spi gpio
dpkg -l fonts-dejavu-core | tail -1
curl -fsS http://127.0.0.1:8081/api/live | head -c 300
```

Checks, in order:

1. **`display initialized` present in the journal?** If absent, the app did not
   reach display setup — treat this as a service-start failure, not a panel
   fault.
2. **Service still running?** `systemctl is-active stingray.service`. A blank
   panel with a healthy API often means a power/cable/connector issue or a
   display reset pin problem rather than software.
3. **SPI device and group.** `/dev/spidev0.0` must exist and `monusr` must be in
   `spi` and `gpio`. Confirm `dtparam=spi=on` in `/boot/firmware/config.txt` only
   if the device node is missing; that is a host prerequisite, not a release
   change.
4. **Fonts.** Missing `fonts-dejavu-core` prevents text rendering; the journal
   usually names the missing font file.
5. **Data vs rendering.** If `/api/live` has fresh values but the panel is
   frozen, the scheduler/storage are fine and the fault is isolated to display
   rendering. If the panel shows a stale footer/version after a deploy, confirm
   `VERSION` and whether the service actually restarted with that release.
6. **Footer specifics.** The footer shows the app version next to `stingray`,
   the host IP (not RSSI), and a last-updated clock time. A wrong/blank footer
   therefore points to package metadata (`importlib.metadata.version("stingray")`)
   or timezone config (`STINGRAY_DISPLAY_TZ`), not to sensor data.

Do not change display code on the Pi. Capture the journal lines and fix forward
with a release if a code defect is confirmed.

## Data not updating

Separate acquisition from persistence from presentation.

```sh
# Acquisition: are reads succeeding, and is mode hardware?
curl -fsS http://127.0.0.1:8081/api/health

# Live values and their timestamps
curl -fsS http://127.0.0.1:8081/api/live | head -c 800

# Historical rows actually stored
curl -fsS 'http://127.0.0.1:8081/api/history?metric=pm2_5_ug_m3&hours=1' | head -c 400

# Files on disk and their modification times
find /mnt/stingray/parquet -name '*.parquet' -printf '%TY-%Tm-%Td %TH:%TM %p\n' | sort -r | head -5
df -h /mnt/stingray /opt/stingray

# Journal errors around the same time
journalctl -u stingray.service --since '30 minutes ago' --no-pager | grep -i -E 'error|fail|warn'
```

| Observation | Meaning | Action |
|---|---|---|
| `mode: mock` in health | Hardware drivers were not built (wrong config or env override) | Check `systemctl cat` and `STINGRAY_MODE`; production must be `hardware` |
| `reads_failed` climbing, `last_error` populated | Acquisition problem | [Sensor / bus errors](#sensor--bus-errors) |
| Fresh `/api/live`, stale `/api/history` and no new Parquet files | Write path blocked | Check disk space, permissions on `/mnt/stingray/parquet`, "batch" errors in journal |
| Fresh Parquet files, stale dashboard | Browser cache or API serving issue | Hard-refresh the page; verify the served JS version and `curl` the API directly |
| All timestamps frozen but service active | Scheduler stalled | Journal around the last sample; check `top -H -p <pid>` for a stuck worker; restart only after capturing evidence |
| Sensor present but no data for one metric | Per-sensor failure | Compare that sensor's counters in `/api/health`; do not use mock data as a workaround |

Sensor sampling intervals differ by design: SHT45 and SGP41 are high-frequency,
SCD4X is ~5 s, and SPS30 is slower. A slow-moving metric is not necessarily
stale — compare `observed_at` in `/api/live` with the current time.

## Sensor / bus errors

```sh
sudo i2cdetect -y 1                    # expect 44, 59, 62, 69
curl -fsS http://127.0.0.1:8081/api/health
journalctl -u stingray.service --no-pager | grep -i -E 'sht45|sgp41|scd4x|sps30|EIO|i2c'
ls -l /dev/i2c-1 /dev/spidev0.0
groups monusr                          # expect i2c spi gpio
```

- Missing addresses with all four sensors failing → bus not enabled or Wiring
  issue; check `/dev/i2c-1`, `/etc/modules-load.d/i2c-dev.conf` and cabling.
- One address missing → that module only; check its connector first.
- `EIO` or CRC errors **only during diagnostics** → two readers on the bus.
  Stop `stingray.service` before running the validator:

  ```sh
  sudo systemctl stop stingray.service
  /opt/stingray/current/.venv/bin/python /tmp/validate_gas_sensors.py --seconds 1800
  sudo systemctl start stingray.service
  ```

  The validator is **not bundled in release tarballs**; copy it from GitHub to
  `/tmp` on the Pi first. Never run it alongside the service.
- SGP41 VOC/NOx indices can remain `WARM` during the 10 conditioning cycles and
  early warm-up. Raw signals are still emitted; this is expected, not a fault.
- SCD4X temperature/humidity are redundant: SHT45 is authoritative and SCD4X
  values are only a TFT fallback. Blended hourly aggregates are expected.

## Summaries or Telegram not arriving

```sh
systemctl is-active stingray-hourly-summary.timer stingray-hourly-summary.service
systemctl list-timers 'stingray*' --no-pager
journalctl -u stingray-hourly-summary.service -n 100 --no-pager
curl -fsS http://127.0.0.1:8081/api/summaries/latest
ls -l /mnt/stingray/summaries/latest.json /mnt/stingray/summaries/.refresh_state.json
curl -fsS http://127.0.0.1:11434/api/tags
```

| Symptom | Check |
|---|---|
| No summary file at all | Is the hourly timer active? Did its service run at the hour boundary? Journal is definitive. |
| `ollama request failed: TimeoutError` in journal | Usually **non-fatal**: the deterministic summary may still be saved and delivered. Only treat it as the cause if no summary was produced. |
| Summary saved but no Telegram message | Check the drop-in and credential file **exist** (`ls -l` only); never print contents. Journal will show delivery errors. Names: `STINGRAY_TELEGRAM_BOT_TOKEN`, `STINGRAY_TELEGRAM_CHAT_ID`. |
| "Generate now" does nothing | 10-minute server-side cooldown; see `/mnt/stingray/summaries/.refresh_state.json` and the journal for a cooldown message. |
| Wrong model or endpoint | Config/env names only: `STINGRAY_SUMMARY_MODEL`, `STINGRAY_SUMMARY_PATH`, and the Ollama URL in the app config. Verify Ollama with `/api/tags`. |

Thresholds, findings and suggested actions are computed by deterministic Python
rules; the LLM only phrases `headline`/`summary`. If a warning looks wrong, check
the rule/threshold configuration before blaming the model.

## Deployment did not happen or rolled back

```sh
/home/ravik/bin/stingray-deploy status
cat /opt/stingray/web-deploy-status.json
systemctl status stingray-deploy.timer stingray-deploy.service --no-pager
journalctl -u stingray-deploy.service -n 100 --no-pager
systemctl status stingray-web-deploy@poll.service stingray-web-deploy@force.service --no-pager
journalctl -u stingray-web-deploy@poll.service -n 50 --no-pager
ls -la /opt/stingray/releases /opt/stingray/downloads
curl -fsS http://127.0.0.1:8081/api/deploy/status
```

Decision tree:

1. **Version unchanged and no deploy attempted.** Timer inactive or network to
   GitHub unavailable. Check timer state, `Next` time in `systemctl list-timers`,
   and download errors in the deploy journal. Force a check:
   `/home/ravik/bin/stingray-deploy poll`.
2. **Checksum/layout failure.** Do not install the asset manually. Verify the
   release asset on `pathfinder` with `gh release view <tag> --json assets` and
   publish a corrected **new** tag. Never re-tag or replace an asset.
3. **Deploy attempted, health gate failed, automatic rollback occurred.** The
   old release is running again by design. Read `/api/health` and the journal
   from the failed window — the failure is an application startup problem, so
   use [Service will not start](#service-will-not-start-or-enters-a-restart-loop).
4. **Wrong version running with no rollback.** Check for a stale symlink or
   `deployed_version` mismatch; the CLI derives status from both.
5. **Web buttons do nothing.** Both buttons return `202` and then the UI polls
   `/api/deploy/status`; check the status file and the `@poll`/`@force` unit
   journal. The API starts a dedicated one-shot unit because a deploy restarts
   `stingray.service` and would kill a child of that service. Endpoints return
   `503` if the running release has deployment disabled.

Manual recovery, when needed:

```sh
/home/ravik/bin/stingray-deploy rollback      # flips to the previous retained release
/home/ravik/bin/stingray-deploy poll          # re-check GitHub now
/home/ravik/bin/stingray-deploy verify <archive>   # checksum + layout, no install
```

Only the three newest release directories are kept. After any flip, confirm
with `readlink -f /opt/stingray/current`, `cat /opt/stingray/current/VERSION`
and `curl -fsS http://127.0.0.1:8081/api/health`.

## Internal service debugging

Use these when a symptom does not fit the sections above.

```sh
# Effective unit, including drop-ins (credentials are referenced, never displayed)
systemctl cat stingray.service

# Exit reason and restart count
systemctl show stingray.service -p ExecMainStatus -p ExecMainCode -p NRestarts --no-pager

# Live process/tree and threads
systemctl show stingray.service -p MainPID --no-pager
top -H -p <MainPID>
ps -eo pid,user,etime,cmd | grep -E '[s]tingray|[o]llama'

# Installed package identity inside the release venv
/opt/stingray/current/.venv/bin/python -c 'import importlib.metadata as m; print(m.version("stingray"))'

# Config file actually in use (paths and values, no secrets)
sed -n '1,80p' /opt/stingray/current/config/application.combined.yaml

# Data write health
df -h /mnt/stingray /opt/stingray
find /mnt/stingray/parquet -name '*.parquet' -printf '%TY-%Tm-%Td %TH:%TM %p\n' | sort -r | head -5
```

Environment override names (values live in systemd drop-ins or the root-owned
credential file; never echo them):

| Name | Purpose |
|---|---|
| `STINGRAY_MODE` | `hardware` in production; `mock` only in tests/dev |
| `STINGRAY_API_HOST`, `STINGRAY_API_PORT` | Bind address/port |
| `STINGRAY_STORAGE_PATH` | Parquet root |
| `STINGRAY_SUMMARY_PATH`, `STINGRAY_SUMMARY_MODEL` | Summary storage and local model |
| `STINGRAY_DISPLAY_TZ` | TFT clock timezone |
| `STINGRAY_TELEGRAM_BOT_TOKEN`, `STINGRAY_TELEGRAM_CHAT_ID` | Delivery credentials |

Defaults are relative to the working directory `/opt/stingray/current`; the
current config points storage at `/mnt/stingray/parquet` and summaries at
`/mnt/stingray/summaries`. The on-demand summary refresh cooldown is 600 s.

API surface for isolating a layer:

| Endpoint | Use |
|---|---|
| `GET /api/health` | Uptime, mode, per-sensor read counters and errors |
| `GET /api/live` | Current measurements with `observed_at` and quality |
| `GET /api/history?metric=<id>&hours=<n>&sensor=<id>` | Stored rows; accepts any stored metric |
| `GET /api/summaries`, `GET /api/summaries/latest` | Generated summaries |
| `POST /api/summaries/refresh` | On-demand run (server-side cooldown applies) |
| `GET /api/deploy/status` | Last deploy result |

## Evidence to capture before escalating

Include all of the following with any bug report; omit credential values:

```sh
{
  echo "--- deploy ---";  /home/ravik/bin/stingray-deploy status
  echo "--- version ---"; cat /opt/stingray/current/VERSION /opt/stingray/current/BUILD_SHA /opt/stingray/deployed_version
  echo "--- units ---";   systemctl is-active stingray.service stingray-deploy.timer stingray-hourly-summary.timer
  echo "--- journal ---"; journalctl -u stingray.service -n 200 --no-pager
  echo "--- health ---";  curl -fsS http://127.0.0.1:8081/api/health
  echo "--- data ---";    find /mnt/stingray/parquet -name '*.parquet' -printf '%TY-%Tm-%Td %TH:%TM %p\n' | sort -r | head -5
} 2>&1 | tee /tmp/stingray-evidence.txt
```

Then fix forward from `pathfinder`: reproduce in tests/dev, evaluate the GitHub
build with `dry_run=true`, publish a new tag, and let the Pi deploy it within 30
minutes (or run `poll`).
