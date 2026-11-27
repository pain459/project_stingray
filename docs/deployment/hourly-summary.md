# Hourly local-LLM air-quality summary

The Pi generates one summary per hour from the live Parquet store. A
deterministic rule layer owns all thresholds and recommendations; the local
Ollama model only phrases those findings. Every numeric value in the model
output is checked against the aggregate it was given, and invalid output is
replaced with deterministic text.

Each run also aggregates the **preceding** hour alongside the current one. Every
metric in the JSON document carries `previous_avg` and
`change_vs_previous_pct`, so hour-over-hour movement is visible even when no
threshold is crossed. Significant changes (25% or more in PM2.5, CO₂, VOC or
NOx) add a deterministic `*_hour_change` finding; the comparison is included in
the model prompt, the dashboard card, and the Telegram message.

## Files

- `src/stingray/summary_job.py` — CLI entry point (`python -m stingray.summary_job`).
- `src/stingray/summary.py` — rules, prompt, validation, Telegram rendering (plain text and HTML).
- `src/stingray/summary_job.py` — CLI entry point and Telegram sender.
- `src/stingray/summary_refresh.py` — on-demand trigger, persistent cooldown, background worker.
- `src/stingray/summary_store.py` — JSON/text summary storage.
- `deploy/stingray-hourly-summary.service` / `.timer` — hourly systemd units.
- `deploy/stingray-telegram.conf` — systemd drop-in giving the dashboard service Telegram credentials.
- `/mnt/stingray/summaries` — live summary documents, `latest.json`, and `.refresh_state.json`.
- `GET /api/summaries` and `GET /api/summaries/latest` — dashboard API.
- `POST /api/summaries/refresh` — on-demand summary trigger (rate-limited).

## One-time setup on `stingray`

Ollama is installed as a localhost-only service (`127.0.0.1:11434`, no cloud
fallback, no resident model). Both `qwen2.5:0.5b` and `smollm2:135m` are pulled.

Install the timer:

```sh
cd /home/ravik/src_git/project_stingray
sudo install -m 0644 deploy/stingray-hourly-summary.service /etc/systemd/system/
sudo install -m 0644 deploy/stingray-hourly-summary.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stingray-hourly-summary.timer
```

Verify:

```sh
systemctl list-timers stingray-hourly-summary.timer
systemctl status stingray-hourly-summary.service
curl -s http://127.0.0.1:8081/api/summaries/latest
```

## Telegram credentials

Credentials are never stored in the repository, config file, summary files, or
logs. The service reads them from `/etc/stingray/telegram.env` only. Create it
on the Pi with values supplied privately:

```sh
sudo install -d -m 0700 /etc/stingray
sudo tee /etc/stingray/telegram.env >/dev/null <<'EOF'
STINGRAY_TELEGRAM_BOT_TOKEN=<bot-token>
STINGRAY_TELEGRAM_CHAT_ID=<chat-id>
EOF
sudo chmod 0600 /etc/stingray/telegram.env
sudo chown root:root /etc/stingray/telegram.env
sudo systemctl restart stingray-hourly-summary.timer
```

To send to a different chat, change only `STINGRAY_TELEGRAM_CHAT_ID`.

The dashboard service needs the same credentials for the **Generate now** button.
Do not make the root-owned env file readable by `monusr`; use a systemd drop-in
instead. systemd reads the file as root and injects the variables into the
service process:

```sh
cd /home/ravik/src_git/project_stingray
sudo install -d -m 0755 /etc/systemd/system/stingray.service.d
sudo install -m 0644 deploy/stingray-telegram.conf /etc/systemd/system/stingray.service.d/telegram.conf
sudo systemctl daemon-reload
sudo systemctl restart stingray.service
```

If the drop-in is missing, on-demand summaries still generate and save locally,
but the API log records that Telegram was skipped because credentials were
unavailable.

## On-demand dashboard refresh

The **Generate now** button in the hourly summary card sends
`POST /api/summaries/refresh`. The API starts a background thread that runs the
same pipeline as the hourly job (Parquet aggregate → deterministic rules →
local Ollama → save → Telegram) and answers immediately:

| Status | Meaning |
|---|---|
| `202` | Run accepted; `next_allowed_at` gives the cooldown deadline. |
| `409` | Another refresh is already running. |
| `429` | Cooldown active; `retry_after_seconds` is returned. |
| `503` | Summary refresh is not configured. |

The default cooldown is 600 seconds (`summary.refresh_cooldown_seconds` in
`config/application.combined.yaml`). It is enforced server-side and persisted
in `/mnt/stingray/summaries/.refresh_state.json`, so restarting the dashboard
service cannot reset it. The button shows a live countdown from the server's
`next_allowed_at` value. The UI polls `/api/summaries/latest` while the server
reports `busy: true` (up to 7 minutes) instead of giving up after 90 seconds,
because local inference can take several minutes on the Pi.

**CPU budget:** read-side DuckDB connections are capped at 2 threads
(`connect_read_limited` in `src/stingray/storage/parquet_store.py`). DuckDB's
32-thread default made the dashboard's 5-second `/api/live` polling saturate all
four Pi cores and starve Ollama, which caused 5-minute inference timeouts and
deterministic fallbacks. The aggregation used by both the hourly job and the
on-demand worker uses the same capped connection.

## Telegram and dashboard formatting

Telegram messages use HTML parse mode: bold section headers, `•` bullets, and
one self-contained tag per line so long messages can be split at line
boundaries without breaking markup. Dynamic text is HTML-escaped. Sections are
ordered: values, previous-hour comparison, notes, recommended practices, and a
window/model footer. The stored `.txt` mirror keeps the same content in plain
text.

The values section is a **compact card**: only seven curated metrics (PM2.5,
CO2, VOC index, NOx index, temperature, humidity, particle size) appear, each as
one fixed-width `<code>` row showing average and min–max range with display
units (µg/m³, ppm, °C, %, µm). Raw SGP41 signals, particle number concentrations
and the remaining stored metrics are intentionally omitted — including them
made the message long and visually skewed on a phone. Machine unit spellings
(`ug/m3`, `degC`, `um`) are converted to display units everywhere in the
message.

The dashboard card shows the same values as compact metric tiles with a
previous-hour change badge, plus separate Notes and Recommended practices
lists.

## Deployment

Deployment is release-based: GitHub builds one arm64 artifact and the Pi runs
exactly that build, with no git checkout. See
[release-and-deploy.md](release-and-deploy.md) for building, bootstrapping the
Pi, polling releases, verifying and rolling back. The old git-pull deploy flow
was removed after the cutover on 2026-11-27.

## Manual runs

```sh
# Safe manual run; writes a summary but does not send Telegram.
.venv/bin/python -m stingray.summary_job --config config/application.combined.yaml --no-telegram

# Compare both models on one real Parquet window; writes and sends nothing.
.venv/bin/python -m stingray.summary_job --config config/application.combined.yaml --benchmark

# Dry run with a specific model.
.venv/bin/python -m stingray.summary_job --config config/application.combined.yaml \
  --model smollm2:135m --dry-run --no-telegram
```

The job is cheap but not free: each run loads a 270–400 MB model, uses one CPU
thread, and unloads the model immediately (`keep_alive=0`). Do not run it while
`scripts/validate_gas_sensors.py` is active.
