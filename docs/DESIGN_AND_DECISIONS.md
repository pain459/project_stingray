# Project Stingray — Design and Decisions

This document records how the system is built, why the important choices were
made, and what has been delivered so far. It complements the practical
[`BUILD_GUIDE.md`](BUILD_GUIDE.md) and the hardware references in
[`hardware/wiring.md`](hardware/wiring.md) and
[`hardware/sensors.md`](hardware/sensors.md).

---

## 1. Mission and constraints

Replace an ESP32-S3 air-quality monitor with a Raspberry Pi 4 that has the same
sensor coverage, but with durable history, a LAN dashboard, and local-language
summaries. The non-negotiable constraints are:

- **Real hardware only in production.** `mode: hardware` on the Pi; every
  served/stored value comes from a physical sensor. Mock mode is for tests and
  development on `pathfinder` only.
- **Two-machine workflow.** Develop, test and push from `pathfinder`; this repo
  is the only development checkout and the place releases are tagged. GitHub
  builds the arm64 artifact, and the Pi polls and deploys it with no git repo of
  its own. The service never runs on the dev box.
- **Deterministic safety.** Python rules own thresholds, findings and
  recommended actions. The LLM may only phrase `headline` and `summary`;
  unapproved numbers or claims are rejected.
- **Non-diagnostic output.** All guidance is air-quality advice, never medical
  diagnosis.
- **Secrets stay off the repository.** Tokens and chat IDs live in a root-owned
  env file injected through systemd drop-ins; documentation uses variable names
  and paths only.

---

## 2. Architecture

```
SHT45 ─┐
SCD4X ─┼─ I²C1 ─┐
SGP41 ─┤        ├─ SensorScheduler ─> MeasurementQueue ─> ParquetWriter ─> SSD
SPS30 ─┘        │                                            │
                │                                            ├─> LAN dashboard :8081
ILI9341 ─ SPI0 ─┴─> TFT dashboard                            └─> hourly summary job
                                                                    ├─> dashboard card
                                                                    └─> Telegram (HTML)
```

### Layers

| Layer | Code | Responsibility |
|---|---|---|
| Entry point | `src/stingray/main.py` | CLI, config load, lifecycle, signal handling, retention loop |
| Config | `src/stingray/core/config.py` | pydantic-validated YAML + `STINGRAY_*` env overrides |
| Sensors | `src/stingray/sensors/*` | one `SensorDriver` per device; protocol, CRC, warm-up, health |
| Scheduling | `src/stingray/core/scheduler.py` | per-sensor cadence, retries/backoff, failure isolation |
| Storage | `src/stingray/storage/parquet_store.py` | batched Parquet writes, bounded DuckDB reads, retention |
| API/UI | `src/stingray/api/server.py`, `static/` | stdlib HTTP server, JSON API, no-build-step frontend |
| Display | `src/stingray/display/*` | ILI9341 TFT dashboard sampled from latest readings |
| Summary | `src/stingray/summary.py`, `summary_job.py`, `summary_refresh.py` | deterministic rules + local LLM + persistence + Telegram |

The contract between layers is the frozen pydantic `Measurement`. Drivers do
not know about storage; storage does not know about drivers; the API reads
storage and in-memory health only.

### Runtime sequence (`main.run()`)

1. Parse CLI (`--config`, `--duration`, `--db`, `--port`) and load config.
2. Build one driver per enabled sensor (`build_hardware_drivers`).
3. Build per-sensor `CadenceSpec` (interval, retries, backoff).
4. Create `ParquetWriter` on the SSD.
5. Start `SensorScheduler`; each tick reads every driver that is due.
6. A drain thread pulls batches from `MeasurementQueue` into the writer.
7. Run retention hourly (delete whole day directories older than 30 days).
8. Start the API server on port 8081.
9. On SIGINT/SIGTERM: stop the scheduler, flush the writer, shut down the API.

---

## 3. Decision log

| Decision | Rationale | Rejected alternative |
|---|---|---|
| Raspberry Pi 4 + real Sensirion sensors | Required sensor parity with the ESP32 reference plus durable disk storage | Keeping the ESP32 (history lived in RAM and was lost on reboot) |
| One SHT45 + SGP41 **2-in-1 module** for temperature/RH and VOC/NOx | One physical board supplies full climate + gas coverage; SHT45 compensates the SGP41 on the same module. It exposes two I²C addresses (`0x44`, `0x59`) and is documented as one module / two logical streams alongside the separate SCD4X and SPS30 | Treating the two chips as two separately procured modules, or dropping gas compensation |
| Parquet on SSD, queried by DuckDB | Cheap columnar analytics, compression, partition pruning; history survives reboot | SQLite (`database.path`/`wal_mode` keys removed); storing on microSD or `/tmp` |
| `mode: hardware` on the Pi; mock only in tests | Production must never serve synthetic data | Mock mode on the running host |
| Docker-free stdlib `ThreadingHTTPServer` | No container runtime needed on a 2 GB Pi; simple deployment as a systemd service | A framework server with extra dependencies |
| Plain HTML/CSS/JS, no build step | Fast iteration and trivial redeploy; each JS file an IIFE with explicit `window.*` exports | A bundled frontend toolchain |
| Port 8081 | 8080 is occupied by another service on the Pi | 8080 default |
| `monusr` unprivileged service account + `i2c`,`spi`,`gpio` groups | Least privilege for bus access; no root service | Running as `ravik` or root |
| Telegram tokens in `/etc/stingray/telegram.env` via systemd drop-ins | Keep secrets out of the repo and out of `monusr`'s readable files | Plaintext in YAML or committed config |
| `qwen2.5:0.5b` as the default local model | Good phrasing quality at acceptable latency on the Pi | `smollm2:135m` benchmarked but rejected: it copied template text instead of summarising |
| Deterministic Python rules for thresholds/findings/actions; LLM only phrases `headline`/`summary` | Safety and reproducibility; model output is validated | Letting the model decide thresholds or recommend actions |
| Numeric `previous_avg` + `change_vs_previous_pct` and `*_hour_change` findings | Comparisons are computed, not inferred from prose | Asking the model to compare hours |
| Telegram HTML with bold headers, `•` bullets, escaped dynamic text, line-boundary splitting | Readable on a phone; long messages split into numbered parts, never truncated | Plain unformatted text or hard truncation |
| Curated 7-metric compact card (`<code>` rows: avg + min–max) in Telegram | Raw SGP41 signals and particle number concentrations dominated the message and broke phone layout; curated rows stay aligned and short | Rendering every stored metric as `avg/min/max` bullets |
| Values (avg/min/max) → notes → practices → footer | Stable reading order across every message | Model-chosen section order |
| On-demand **Generate now** runs the whole pipeline including Telegram | What the user expects from the button | Dashboard-only refresh or Telegram-only send |
| Server-side cooldown persisted in `/mnt/stingray/summaries/.refresh_state.json` | Cannot be bypassed by clearing browser state; survives restart | Client-only timer |
| 600 s cooldown (`SummaryConfig.refresh_cooldown_seconds`) | Prevents CPU/API abuse while remaining useful | No limit or a much longer interval |
| DuckDB read queries capped at 2 threads (`connect_read_limited()`) | DuckDB's 32-thread default plus 5 s polling saturated all four cores and starved Ollama (5-minute timeouts, ~0.7 tok/s) | Reducing dashboard polling frequency, which would make live data stale |
| UI polls `busy` for up to 7 minutes after a refresh | The worker can take minutes on a cold model load | Short polling that shows a stuck button |
| Script cache busting with `?v=N` in `index.html` | Browsers otherwise serve stale JS after deployment | Relying on hard refresh by users |
| I²C reads via `smbus2.i2c_msg.read()` + `i2c_rdwr()` | Sensirion command-then-read chips NACK an injected SMBus register byte | `read_i2c_block_data` and raw ioctl attempts (both failed) |
| Gas indices via a pure-Python port of Sensirion's v3.2.0 algorithm | The datasheet has no raw→index formula; a linear approximation would be wrong | Reporting raw signals as indices |
| Tag-driven GitHub Release with a prebuilt arm64 venv, auto-deployed by a Pi timer | GitHub is the only source of truth; the exact evaluated artifact runs on the Pi and the Pi needs no git repo | Git pull on the Pi, or resolving/compiling dependencies on the Pi |
| Immutable release directories + `current` symlink, last 3 kept | Atomic cutover and automatic rollback without touching `/mnt/stingray` data | In-place updates that cannot be undone |
| Evaluate builds with a dry-run dispatch before any tag or Pi change | Build time and artifact contents are verified on GitHub first, as requested | Publishing a release or touching the Pi before evaluation |
| Pi polls GitHub Releases at :15 and :45 (`OnCalendar=*:15,45`) | Keeps the 30-minute maximum lag of the original `*:0/30` schedule but moves the release restart away from the top-of-hour hourly summary, so an LLM/Telegram run is never overlapped | 10-minute interval; `*:0/30` at :00; polling on every main push |
| Webpage "Deploy latest" and "Force redeploy" trigger a one-shot systemd unit, not a child process of the API | A deploy restarts `stingray.service`, and systemd's cgroup kill mode would terminate any API-spawned worker mid-deploy | Running the CLI directly from the request handler or a background thread |
| Two fixed unit instances with a narrow `monusr` sudoers rule and no dashboard auth | The CLI is privileged; fixed names prevent arbitrary unit starts, and the dashboard is for a trusted LAN | Token auth or an open sudo rule |

---

## 4. How the summary pipeline works

1. **Aggregate.** Query the last hour of Parquet data into deterministic facts:
   averages, min/max, sample counts, previous-hour average and percent change.
2. **Rules.** Python applies thresholds (PM2.5 35 µg/m³, CO₂ 1000 ppm, VOC 100,
   NOx 150, RH 30–65 %, temperature 30 °C) and emits findings and advisory
   practices. No model is involved in these decisions.
3. **Phrase.** A local Ollama model receives a tight prompt and is asked only
   for `headline` and `summary`. Output is validated; on timeout or invalid
   content the system falls back to deterministic text (`model_used: false`).
4. **Persist.** The complete summary is saved under
   `/mnt/stingray/summaries/year=YYYY/month=MM/day=DD/` so it is stable and
   reviewable.
5. **Publish.** The latest summary appears on the dashboard and is sent to
   Telegram as formatted HTML.

Hourly runs come from `stingray-hourly-summary.timer` with `Nice=15`. The
on-demand path enters the same code via `summary_refresh.py`, guarded by the
server-side cooldown.

---

## 5. Progress to date

| Area | Status |
|---|---|
| Repository bootstrap, preflight, baseline inventory | done |
| Mock-mode application foundation and tests | done |
| Storage foundation (Parquet/DuckDB), retention | done |
| SHT45, SPS30, SCD4X, SGP41 real drivers on I²C1 | done |
| Combined acquisition with real data only | done |
| API and LAN dashboard, ILI9341 TFT dashboard | done and deployed |
| Hourly local-LLM summary + Telegram | done and deployed |
| Beautified Telegram/dashboard output + rate-limited on-demand refresh | done and deployed |
| DuckDB thread cap + refresh polling fix | implemented and pushed, awaiting Pi pull |
| ESP32 reference API capture / alert parity (Phase 08) | open / blocked on reference captures |
| Fan/power root cause (STG-004) | open; original host runs without a fan |

Data volume reality check from the migrated dataset: roughly 500 measurements
per minute at production cadence, ~112 MB/day of ZSTD Parquet; a 30-day
backfill produced 15,099,053 rows / 473 MB. This is why every API query is
time-bounded (latest 2 h, count 24 h) and why bulk work happens on
`pathfinder`.

---

## 6. Engineering invariants

1. Storage path must be on the SSD (`/mnt/stingray`), never `/tmp` — `/tmp` is
   cleared on reboot and would silently destroy history.
2. Never run an unbounded DuckDB aggregate over the live dataset.
3. Bulk data generation happens on `pathfinder`; only finished artifacts go to
   the Pi.
4. The service runs on the Pi; `pathfinder` never serves the dashboard.
5. Never delete data unless the user explicitly asks.
6. One IIFE per dashboard JS file, explicit `window.*` exports, versioned
   script URLs.
7. Never run `scripts/validate_gas_sensors.py` while `stingray.service` is
   running.
8. Health/medical claims are out of scope; all output is air-quality guidance.

---

## 7. Known constraints and follow-ups

- **CPU budget.** The Pi has four cores. DuckDB reads are limited to two
  threads; Ollama is restricted to one loaded model, one parallel request,
  1024-token context and no cloud. If summaries become slow again, check load
  average and `ollama` logs before changing thresholds.
- **Gas index learning is not persisted.** A service restart resets SGP41 index
  learning; early indices should be treated as provisional.
- **Cooldown state is server-side.** Clearing browser storage does not reset
  it; editing `.refresh_state.json` is the only manual override.
- **Physical wiring labels.** The DFR0918 terminal positions and the exact
  display board revision were not recorded on the original harness; see the
  open items in [`hardware/wiring.md`](hardware/wiring.md).
- **Deployment lag.** After pushing a release from `pathfinder`, confirm the
  deployed version with `/home/ravik/bin/stingray-deploy status` and the served
  script cache version before judging a fix; the Pi polls at :15 and :45.
