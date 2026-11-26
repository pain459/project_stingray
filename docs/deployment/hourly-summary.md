# Hourly local-LLM air-quality summary

The Pi generates one summary per hour from the live Parquet store. A
deterministic rule layer owns all thresholds and recommendations; the local
Ollama model only phrases those findings. Every numeric value in the model
output is checked against the aggregate it was given, and invalid output is
replaced with deterministic text.

## Files

- `src/stingray/summary_job.py` — CLI entry point (`python -m stingray.summary_job`).
- `src/stingray/summary.py` — rules, prompt, validation, Telegram rendering.
- `src/stingray/summary_store.py` — JSON/text summary storage.
- `deploy/stingray-hourly-summary.service` / `.timer` — hourly systemd units.
- `/mnt/stingray/summaries` — live summary documents and `latest.json`.
- `GET /api/summaries` and `GET /api/summaries/latest` — dashboard API.

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
