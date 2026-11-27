# Open Notebook hot-set index (project_stingray)
# Updated 2026-11-27, commit a07e1af, release v0.2.1. Storage = DuckDB + Parquet on the SSD.
# Dashboard live on Pi: http://192.168.68.53:8081/ (port 8081, hardware mode, genuine data).
# All four sensors online: SHT45, SCD4X, SGP41, SPS30 on /dev/i2c-1.
#
# Architecture / reference
# - Ref - project_stingray - Deep Architecture and Engineering Manual (note:klnqsmswnwqn4i1fd0f1)
# - Ref - project_stingray - Comprehensive Configuration Properties and Pod Overrides (note:31j28ur6e38zjh9k5aph)
# - Runbook - project_stingray - Build, Test, Run (note:lbop5xicy244emtzy1yq) [unverified]
# - Runbook - project_stingray - Deploy from pathfinder to Pi (note:pfq82urnpiyjk2w8gzjb) [superseded by release model]
# - Gotcha - project_stingray - Critical Invariants, Build Overrides, and Operational Traps (note:0wabhzcbg4mr15xfmrz3)
# - Gotcha - project_stingray - Sensirion I2C Protocol Traps (note:fcgj8n7po6773f0a6vgp)
# - Gotcha - project_stingray - Tiny Ollama Prompt and Validation Traps (note:n60mxliem3ndy0gbfxzl)
# - Gotcha - project_stingray - DuckDB Thread Oversubscription Starves Ollama (note:6refxoy3yixwuqrczfjs)
# - Gotcha - project_stingray - Release Deployment Cutover Traps (note:m3tllw7y4q3p9po7imzm)
# - Decision - project_stingray - Telegram Compact Curated Card (note:2obwu9q4xwx44rra5z7a)
#
# Release / deployment (current)
# - Decision - project_stingray - GitHub Release Build and Pi Auto-Deploy (note:r12f3agbargyl1wsbai8)
# - Runbook - project_stingray - Build and Evaluate GitHub Release (note:he6pvq13x9jccdjc7ck8)
# - Decision - project_stingray - Web Deploy via One-Shot Systemd Unit (note:nnrirfjk19yxk2gfda7s)
#
# Code maps
# - Code Map - project_stingray - Core Architecture and Scheduler (note:93ltsompta5zwxq1mc75)
# - Code Map - project_stingray - DuckDB Parquet Storage and Retention (note:l3jhb964heucyg3whecj)
# - Code Map - project_stingray - Web Dashboard API and UI (note:k0l1zypuiwas4vkbz9hd)
# - Code Map - project_stingray - Webpage Deploy Control Flow (note:s72y4yu86y4ckicly8z1)
# - Code Map - project_stingray - SHT4x SGP41 Gas Acquisition (note:zur67oe676tzxo5015yf)
# - Code Map - project_stingray - SHT45 Driver (note:zr1sm26kwprguqo69w6a)
# - Code Map - project_stingray - SPS30 Driver (note:gqbf2r8u9enjtznd3tke)
# - Code Map - project_stingray - SCD4X Driver (note:7arty6f93nuk275efbwy)
# - Code Map - project_stingray - Hourly Local LLM Summary (note:bsk7ps9p9c7tx2g3orv3)
# - Code Map - project_stingray - SQLite Storage and Retention (note:7o7rfj8x91ioklg3mgd7) [SUPERSEDED]
#
# Sessions
# - Session - 2026-11-26 - SHT4x and SGP41 Gas Sensor Bring-up (note:3qjpgmv87ldsmi977nbe)
# - Session - 2026-11-26 - Dashboard JS Scope Fix and Parquet Migration (note:ce2n4yobvoy3eyg4qrqm)
# - Session - 2026-11-26 - SPS30 TPS and Network Info (note:ao3xhogu1msrl31uv5eo)
# - Session - 2026-11-26 - Data Provenance Sanity and Mock Cleanup (note:v5iotxf6z0oyhq502tns)
# - Ref - project_stingray - Local LLM Feasibility on the Pi (note:6eh6ox5ycn4sjo2lk3wp)
# - Session - 2026-11-26 - Hourly Local LLM Summary Feasibility (note:9lb5ooipkw9j1bcqr2nf)
#
# Critical gotchas: (1) classic <script> files share one global lexical scope, so
# every dashboard JS file must be an IIFE; (2) never use read_i2c_block_data for
# Sensirion responses -- use i2c_msg.read + i2c_rdwr; (3) stop the service before
# running scripts/validate_gas_sensors.py or the two processes corrupt I2C reads;
# (4) SPS30 uint16 TPS word is nm, not µm — divide by 1000; (5) API handler methods
# read shared state via self.state (self.network does not exist on the handler).
# (6) DuckDB defaults to 32 threads; use connect_read_limited() (threads=2) for all
# read queries or dashboard polling starves Ollama on the 4-core Pi.
# (8) Web deploy must run in a separate oneshot systemd unit: a child of stingray.service is killed by the service cgroup on restart (setsid does not help). Fixed unit names + narrow sudoers only.
# (7) Pi repo is monusr-owned; ravik is in group monusr and setgid dirs keep new
# files shared. sudo -n expires with the interactive timestamp — a narrow NOPASSWD
# rule for /usr/bin/systemctl restart stingray.service is now permanent, NOT temporary.
#
# Status: 30-minute gas test passed (all four sensors READY, no errors); TPS and
# network info live on the Pi (tps_um ≈ 0.6 µm, wlan0 IP/RSSI). Data-provenance audit at
# c64ebdf: live process, active Parquet store and served JS are hardware-only; scripts/backfill.py
# removed on Pi too. Both mock-era SQLite DBs quarantined to /mnt/stingray/archive/.
# Hourly local-LLM summary deployed at 83bfc10: qwen2.5:0.5b, Ollama localhost-only,
# live summary via /api/summaries/latest; Telegram delivery verified 23:23 IST via
# /etc/stingray/telegram.env (root:root 0600) and the hourly systemd unit.
# On-demand dashboard button + HTML Telegram deployed at d3c1a7e; 10-minute server-side
# cooldown in /mnt/stingray/summaries/.refresh_state.json. CPU fix at 5e7c88e (DuckDB 2
# threads, UI polls busy up to 7 min) is pulled on the Pi; docs at 5495556.
# Reproducible build docs committed at 5495556: docs/BUILD_GUIDE.md, docs/hardware/{wiring,sensors,inventory}.md,
# docs/DESIGN_AND_DECISIONS.md; README now links all guides.
# Telegram compact curated card at 9ddf2ce (7 metrics, code rows, raw signals hidden).
# CUTOVER COMPLETE 2026-11-27: Pi runs v0.2.0 from /opt/stingray/releases/0.2.0 via /opt/stingray/current; git checkout removed; deploy timer active at OnCalendar=*:0/30 (user asked for 30min, not 10min); all four sensors healthy; dashboard summary.js?v=10. Old scripts/deploy-pi.sh deleted from GitHub. Bootstrap needs interactive sudo; see Gotcha note m3tllw7y4q3p9po7imzm.
# Never run summary_job or validate_gas_sensors.py while the other is active.

# WEB DEPLOY COMPLETE 2026-11-27: v0.2.1 from a07e1af published after rerun of transient build failure (45974111254) and deployed to Pi. Both @poll and @force buttons verified end-to-end (202 → unit success → status file → API). Pi hardware mode, 0 failed reads; timer OnCalendar=*:0/30. Session: note:em68bsg3bi7u0jgpbl1b. Code map: note:s72y4yu86y4ckicly8z1. One-time Pi installer completed by user.
