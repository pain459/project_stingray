# Open Notebook hot-set index (project_stingray)
# Updated 2026-11-28, release v0.2.14 = 8c42545; Pi runs 0.2.14 / 8c42545... with local browser clock, double-buffered chart, flat neon logo, full-window cold history, summary min/max plus manual-run source, browser-derived gas quality states/trends, and chart gap breaks; service active, dashboard 200. Storage = DuckDB + Parquet on the SSD.
# Dashboard live on Pi: http://192.168.68.53:8081/ (port 8081, hardware mode, genuine data).
# All four sensors online: SHT45, SCD4X, SGP41, SPS30 on /dev/i2c-1.
# Pi host telemetry enabled as pi:cpu_temperature_c (30s cadence), separate from ambient temperature. # codemap note:1k3u6dipbo4smx73jzz9
#
# Architecture / reference
# - Ref - project_stingray - Deep Architecture and Engineering Manual (note:klnqsmswnwqn4i1fd0f1)
# - Ref - project_stingray - Comprehensive Configuration Properties and Pod Overrides (note:31j28ur6e38zjh9k5aph)
# - Ref - project_stingray - Dashboard Modernization Blog Source Pack (note:lb88ced5ed92ah5quqcv) — blog-ready timeline, decisions, failure story, verification, outline
# - Ref - project_stingray - Hardware Wiring Reference (note:gzwrzkm1a3gxcfpkavkg) — DFR0918 BCM map, I2C/SPI wiring, SPS30/SCD4x/SHT4x+SGP41/ILI9341; owner confirms SCD4x VCC and combined-board VCC/VIN on 5V and working
# - Runbook - project_stingray - Debug Deployed Package on stingray (note:yzww5cqdo4jh5kdaibqk) — symptom-based Pi debugging; repo doc docs/deployment/operator-runbook.md
# - Runbook - project_stingray - Build, Test, Run (note:lbop5xicy244emtzy1yq) [unverified]
# - Gotcha - project_stingray - Critical Invariants, Build Overrides, and Operational Traps (note:0wabhzcbg4mr15xfmrz3)
# - Gotcha - project_stingray - Sensirion I2C Protocol Traps (note:fcgj8n7po6773f0a6vgp)
# - Gotcha - project_stingray - Tiny Ollama Prompt and Validation Traps (note:n60mxliem3ndy0gbfxzl)
# - Gotcha - project_stingray - DuckDB Thread Oversubscription Starves Ollama (note:6refxoy3yixwuqrczfjs)
# - Gotcha - project_stingray - Release Deployment Cutover Traps (note:m3tllw7y4q3p9po7imzm)
# - Gotcha - project_stingray - Cold History Limit Truncation (note:sv0wj6311idkf1qe927x) — no-since requests must use full-window downsampling, not oldest-first raw limit
# - Gotcha - project_stingray - History Gap Interpolation (note:qbmrk9rwvambmm8xrtdh) — break chart line/fill at real data gaps, all metrics and ranges; released and deployed in v0.2.14
# - Gotcha - project_stingray - Summary Metric Key Mismatch and Last Run Retention (note:ymcpawg06he4zfab6njz) — API min/max vs UI minimum/maximum; latest.json fallback
# - Decision - project_stingray - Telegram Compact Curated Card (note:2obwu9q4xwx44rra5z7a)
# - Decision - project_stingray - Dashboard Visual Direction Dark Instrument Panel (note:2940yg33k09buc8xbras)
# - Gotcha - project_stingray - Chart CSS Variables Must Be Six-Digit Hex (note:q8p1okl1mdlbaefqxbwp)
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
# - Code Map - project_stingray - Dashboard Clock, Refresh, and Logo Assets (note:17l70kcdjkmidl83w75v) — local clock, no-op DOM writes, offscreen chart, /assets/ route
# - Code Map - project_stingray - Webpage Deploy Control Flow (note:s72y4yu86y4ckicly8z1)
# - Code Map - project_stingray - Sensor Metrics Inventory and Utilization (note:44ovcy8ota8bvdlxxf88)
# - Code Map - project_stingray - Pi CPU Temperature Telemetry (note:1k3u6dipbo4smx73jzz9)
# - Code Map - project_stingray - SHT4x SGP41 Gas Acquisition (note:zur67oe676tzxo5015yf)
# - Code Map - project_stingray - Gas Quality Live Rendering (note:rpl2bcpyo95br2qd5xtj) — quality labels and 1 h trends derived in live.js
# - Code Map - project_stingray - SHT45 Driver (note:zr1sm26kwprguqo69w6a)
# - Code Map - project_stingray - SPS30 Driver (note:gqbf2r8u9enjtznd3tke)
# - Code Map - project_stingray - SCD4X Driver (note:7arty6f93nuk275efbwy)
# - Code Map - project_stingray - Hourly Local LLM Summary (note:bsk7ps9p9c7tx2g3orv3)
# - Code Map - project_stingray - Hourly Summary Run Source and Last-Run Rendering (note:k0ol4cffixwntlda70kk) — manual/hourly source stamping, min/max render fix, last-run retention
#
# Sessions
# - Session - 2026-11-26 - SHT4x and SGP41 Gas Sensor Bring-up (note:3qjpgmv87ldsmi977nbe)
# - Session - 2026-11-26 - Dashboard JS Scope Fix and Parquet Migration (note:ce2n4yobvoy3eyg4qrqm)
# - Session - 2026-11-26 - SPS30 TPS and Network Info (note:ao3xhogu1msrl31uv5eo)
# - Session - 2026-11-27 - Outdoor Context v0.2.4 Release and Forced Summary (note:q2py0ki7gxcqe9h5sf32)
# - Session - 2026-11-27 - Browser-Owned Dashboard v0.2.5 Release (note:frm7su5vp9052x6720hm) [release, auto-deploy, query/load and summary verification complete; TFT untouched]
# - Session - 2026-11-27 - Dashboard UI Modernization and v0.2.7 Release (note:9uks6aa1hg2tsleff52y) [early deploy before timer; human visual review pending]
# - Session - 2026-11-27 - Release Control Refresh and v0.2.8 (note:t5t9gduqw9y023i2q1tl) [read-only GitHub update check; deployed early]
# - Session - 2026-11-27 - Local Clock, Seamless Refresh, and Neon Logo (note:26oodb3hfzoc1eopg99t) [v0.2.10 deployed; logo restored; blog notes persisted]
# - Session - 2026-11-28 - Cold History Window Fix and v0.2.11 Release (note:sw4lmwu10tm0mobqvxcg) [cold 6 h history fixed, released and deployed]
# - Session - 2026-11-28 - Hourly Summary Min Max and v0.2.12 Release (note:884po6yvpajlbdkask97) [summary min/max, manual-run badge, last-run retention; released and deployed]
# - Session - 2026-11-28 - Gas Quality Dashboard and v0.2.13 Force Deploy (note:p1ybizgv7gah6f0c6a42) [gas labels/trends released and force-deployed; human visual review pending]
# - Session - 2026-11-28 - History Gap Chart Fix and v0.2.14 Release (note:twt294eqzir0dik5pfos) [line/fill breaks at real gaps; released and deployed by scheduled poll; human visual review pending]
# - Session - 2026-11-27 - Hardware Wiring Reference Persistence (note:awawrbgwfl9fk7pcbpyr) [owner-confirmed 5V SCD4x + combined board, not independently audited]
# - Session - 2026-11-27 - v0.2.3 version metadata rollout (note:nngavfdn0isrckzs11uz)
# - Session - 2026-11-27 - Release poll schedule to 15 and 45 (note:lggxfhjuu8rec56m3u1n) [verified]
# - Session - 2026-11-27 - Pi CPU temperature telemetry (note:5qp0j5qpamsvp49myv8e)
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
# (9) Pi CPU/SoC temperature is host telemetry from sysfs; keep it as pi:cpu_temperature_c, never merge into ambient temperature_c aggregates or the curated Telegram 7-card.
# (8) Web deploy must run in a separate oneshot systemd unit: a child of stingray.service is killed by the service cgroup on restart (setsid does not help). Fixed unit names + narrow sudoers only.
# (7) Pi repo is monusr-owned; ravik is in group monusr and setgid dirs keep new
# files shared. sudo -n expires with the interactive timestamp — a narrow NOPASSWD
# rule for /usr/bin/systemctl restart stingray.service is now permanent, NOT temporary.
# (10) HISTORY_URL already contains ?mode=history: append &metric=..., never ?metric=..., or the server returns 400 unknown mode (gotcha note:qj5im2u5bx1zns1jpvzc; fixed at 43aae91, released and force deployed as v0.2.6 / ab9e53a).
# (11) A new static asset URL prefix must be routed in ApiHandler/do_GET; adding the file and MIME type is not enough. v0.2.9 logo 404 fixed in v0.2.10 by routing /assets/ to _serve_static.
# (12) DFR0918 uses BCM labels; never confuse TFT SDI/SDA (SPI MOSI GPIO10) with sensor SDA (I2C GPIO2). Owner-confirmed as-built: SCD4x VCC and combined SHT4x/SGP4x VCC/VIN are on 5V and working; exact 3V3 roles remain open. Detailed reference: note:gzwrzkm1a3gxcfpkavkg.
# (13) A no-since history request is a cold/full window: use range_with_stats bucket downsampling. Raw range(limit=900) returns oldest rows, so live-merge draws a straight gap (fixed in v0.2.11; gotcha note:sv0wj6311idkf1qe927x).
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
# CUTOVER COMPLETE 2026-11-27: Pi runs v0.2.0 from /opt/stingray/releases/0.2.0 via /opt/stingray/current; git checkout removed; deploy timer was OnCalendar=*:0/30 at cutover (user asked for 30min then; now :15/:45); all four sensors healthy; dashboard summary.js?v=10. Old scripts/deploy-pi.sh deleted from GitHub. Bootstrap needs interactive sudo; see Gotcha note m3tllw7y4q3p9po7imzm.
# Never run summary_job or validate_gas_sensors.py while the other is active.

# WEB DEPLOY COMPLETE 2026-11-27: v0.2.1 from a07e1af published after rerun of transient build failure (45974111254) and deployed to Pi. Both @poll and @force buttons verified end-to-end (202 → unit success → status file → API). Pi hardware mode, 0 failed reads; timer was OnCalendar=*:0/30 at v0.2.1 (now :15/:45). Session: note:em68bsg3bi7u0jgpbl1b. Code map: note:s72y4yu86y4ckicly8z1. One-time Pi installer completed by user.
#
# RELEASE v0.2.2 (523a50e) 2026-11-27: Pi CPU telemetry in TFT header, dashboard live card and history selector. Dry run 45985892080 passed before publishing; release run 45986331709; Pi force-deployed 0.2.2; /api/live pi=67.7 degC ready, history samples present. Session: note:5qp0j5qpamsvp49myv8e.
# RELEASE POLL SCHEDULE CHANGED 2026-11-27: deploy timer OnCalendar=*:15,45 (repo deploy/stingray-deploy.timer). Chosen so release restarts never overlap the :00 hourly summary/Telegram job; maximum lag stays 30 min. Pi-side unit install needs interactive sudo (ravik has no passwordless sudo for unit edits). # codemap note:1k3u6dipbo4smx73jzz9
# RELEASE v0.2.3 (d6c37a4) 2026-11-27: fixes displayed version drift; dry run 45990079883 and release 45990572782 passed; tag/assets verified; Pi auto-deployed at 17:15 IST with no force; dist-info and TFT footer both show v0.2.3; CPU metric still live. Session: note:nngavfdn0isrckzs11uz.
# RELEASE v0.2.4 (c0e9bc6) 2026-11-27: keyless Open-Meteo outdoor weather/AQI context in hourly summaries; dry run 45997052501 and release 45997649598 passed; assets checksum-verified, dist-info VERSION=0.2.4; Pi auto-deployed at 18:20:14 IST with no force (checksum OK, current -> releases/0.2.4, dist-info and VERSION 0.2.4, service active, hardware health clean, pi:cpu_temperature_c live); forced refresh at 12:57 UTC captured Open-Meteo context, saved summary and sent Telegram with one attributed footer line; Ollama hit the 300s timeout under Pi load ~25 and deterministic fallback was used (gotcha note:0wabhzcbg4mr15xfmrz3). Session: note:q2py0ki7gxcqe9h5sf32. Codemap: note:bsk7ps9p9c7tx2g3orv3.
