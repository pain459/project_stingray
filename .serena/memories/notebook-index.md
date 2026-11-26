# Open Notebook hot-set index (project_stingray)
# Updated 2026-11-26, commit e1a7805. Storage = DuckDB + Parquet on the SSD.
# Dashboard live on Pi: http://192.168.68.53:8081/ (port 8081, mock data)
#
# Architecture / reference
# - Ref - project_stingray - Deep Architecture and Engineering Manual (note:klnqsmswnwqn4i1fd0f1)
# - Ref - project_stingray - Comprehensive Configuration Properties and Pod Overrides (note:31j28ur6e38zjh9k5aph)
# - Runbook - project_stingray - Build, Test, Run (note:lbop5xicy244emtzy1yq) [unverified]
# - Gotcha - project_stingray - Critical Invariants, Build Overrides, and Operational Traps (note:0wabhzcbg4mr15xfmrz3)
#
# Code maps
# - Code Map - project_stingray - Core Architecture and Scheduler (note:93ltsompta5zwxq1mc75)
# - Code Map - project_stingray - DuckDB Parquet Storage and Retention (note:l3jhb964heucyg3whecj)
# - Code Map - project_stingray - Web Dashboard API and UI (note:k0l1zypuiwas4vkbz9hd)
# - Code Map - project_stingray - SQLite Storage and Retention (note:7o7rfj8x91ioklg3mgd7) [SUPERSEDED]
#
# Sessions
# - Session - 2026-11-26 - Dashboard JS Scope Fix and Parquet Migration (note:ce2n4yobvoy3eyg4qrqm)
#
# Critical gotcha: classic <script> files share one global lexical scope. A top-level
# const in more than one file is a fatal redeclaration that kills the whole file.
# Every dashboard JS file must be an IIFE. Verify with Node vm in ONE shared context.
#
# Next: confirm the dashboard in a real browser; then Phase 05 (needs I2C/SPI enabled).
