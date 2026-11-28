# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.16] - 2026-11-28

### Fixed
- Parquet chunks are fsynced before rename and the day directory is fsynced
  after rename, so an unclean shutdown cannot leave a 0-byte chunk.
- Read queries skip zero-byte or bad-magic Parquet files and return clean empty
  results when none are usable; one damaged chunk no longer breaks the history
  or latest APIs.
- Added regression tests for fsync, empty-chunk rejection, and damaged-file
  tolerance.

## [0.2.15] - 2026-11-28

### Fixed
- Replaced the process-local Parquet chunk counter with UUIDv4 filenames so a
  restart can no longer overwrite previously written history.

## [0.2.14] - 2026-11-28

### Fixed
- History charts break the line and fill at real data gaps instead of drawing
  across missing samples.

## [Unreleased]

### Added
- Initial repository skeleton (Phase 00 / STG-001).
- Project plan: `STINGRAY_IMPLEMENTATION_PLAN.md`.
