# Open Notebook hot-set index (project_stingray)
# Updated 2026-11-26, commit c649458. Storage = DuckDB + Parquet on the SSD.
# Dashboard live on Pi: http://192.168.68.53:8081/ (port 8081, hardware mode, genuine data).
# All four sensors online: SHT45, SCD4X, SGP41, SPS30 on /dev/i2c-1.
#
# Architecture / reference
# - Ref - project_stingray - Deep Architecture and Engineering Manual (note:klnqsmswnwqn4i1fd0f1)
# - Ref - project_stingray - Comprehensive Configuration Properties and Pod Overrides (note:31j28ur6e38zjh9k5aph)
# - Runbook - project_stingray - Build, Test, Run (note:lbop5xicy244emtzy1yq) [unverified]
# - Gotcha - project_stingray - Critical Invariants, Build Overrides, and Operational Traps (note:0wabhzcbg4mr15xfmrz3)
# - Gotcha - project_stingray - Sensirion I2C Protocol Traps (note:fcgj8n7po6773f0a6vgp)
#
# Code maps
# - Code Map - project_stingray - Core Architecture and Scheduler (note:93ltsompta5zwxq1mc75)
# - Code Map - project_stingray - DuckDB Parquet Storage and Retention (note:l3jhb964heucyg3whecj)
# - Code Map - project_stingray - Web Dashboard API and UI (note:k0l1zypuiwas4vkbz9hd)
# - Code Map - project_stingray - SHT4x SGP41 Gas Acquisition (note:zur67oe676tzxo5015yf)
# - Code Map - project_stingray - SHT45 Driver (note:zr1sm26kwprguqo69w6a)
# - Code Map - project_stingray - SPS30 Driver (note:gqbf2r8u9enjtznd3tke)
# - Code Map - project_stingray - SCD4X Driver (note:7arty6f93nuk275efbwy)
# - Code Map - project_stingray - SQLite Storage and Retention (note:7o7rfj8x91ioklg3mgd7) [SUPERSEDED]
#
# Sessions
# - Session - 2026-11-26 - SHT4x and SGP41 Gas Sensor Bring-up (note:3qjpgmv87ldsmi977nbe)
# - Session - 2026-11-26 - Dashboard JS Scope Fix and Parquet Migration (note:ce2n4yobvoy3eyg4qrqm)
# - Session - 2026-11-26 - SPS30 TPS and Network Info (note:ao3xhogu1msrl31uv5eo)
#
# Critical gotchas: (1) classic <script> files share one global lexical scope, so
# every dashboard JS file must be an IIFE; (2) never use read_i2c_block_data for
# Sensirion responses -- use i2c_msg.read + i2c_rdwr; (3) stop the service before
# running scripts/validate_gas_sensors.py or the two processes corrupt I2C reads;
# (4) SPS30 uint16 TPS word is nm, not µm — divide by 1000; (5) API handler methods
# read shared state via self.state (self.network does not exist on the handler).
#
# Status: 30-minute gas test passed (all four sensors READY, no errors); TPS and
# network info live on the Pi at commit c649458 (tps_um ≈ 0.6 µm, wlan0 IP/RSSI).
