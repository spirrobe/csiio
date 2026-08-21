# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning.

## [Unreleased]

## [0.3.5] - 2026-08-21

### Changed
- Fixed an issue with L format (LONG) type when reading data as these were seen as 8 bytes instead of 4 which would be correct.
- updated workflow versions...
- added button to run them manually
- pre-commit check for version bump in pyproject

## [0.3.4] - 2026-06-04

### Changed
- Exposed `line_terminator` through `convert_csi_file()`, `CSIDataFile.write()`, and `csiio convert --line-terminator`.
- Updated docs and added public API tests for line terminator customization and split-window `closed`/`label` behavior.

## [0.3.3] - 2026-06-04

### Changed
- Added `closed` and `label` support to split-window chunking so users can control how timestamped data is grouped and labeled.

## [0.3.1] - 2026-06-04

### Changed
- Added optional `line_terminator` keyword support to `write_csi_ascii()` so callers can override default ASCII line endings.
- Relaxed ASCII header tests to accept either LF-only or CRLF line endings while preserving default CRLF output for TOA5/TOACI1.

## [0.3.0] - 2026-06-03

### Changed
- Refactored reader and writer logic into separate modules and split convert into a thin wrapper around read + write.
- Updated `CSIDataFile` to act as a lightweight convenience wrapper around read/write functionality.
- Added support for reading only a subset of requested columns and preserving requested columns across concatenated multi-file reads.
- Changed multi-file read semantics so missing columns in some files are logged but remaining data is still returned when at least one file contains requested columns.
- Added stricter single-file behavior: reading with requested columns now raises if none of the requested columns are present.

## [0.2.2] - 2026-05-28

### Added
- Added `exists_action` support to `convert_csi_file`, `CSIDataFile.write`, and the `csiio convert` CLI command. Supported values are `merge`, `overwrite`, and `skip`.
- Added merge semantics for existing CSI output files with deduplication of duplicate timestamps when `merge` is used.
- Support for pathlib inputs
- Added a Python support policy checker and GitHub Actions workflow to enforce supported runtime versions.

### Docs
- Documented split-window conversion output naming and CLI usage in `README.md`.
- Updated README to reflect CSV export via `csiio convert --output-format CSV` and `CSIDataFile.write(..., output_format='CSV')` instead of the removed `to-csv` CLI flow.

### Fixed
- Made fixture-heavy test cases more robust in CI by skipping CardConvert parity checks when no raw/reference pairs are discoverable in the environment.
- Hardened TOA5 conversion smoke coverage to tolerate a known CSIXML parser edge-case while still requiring successful conversions.
- Updated CI test workflow to fetch Git LFS fixture payloads and skip fixture-parity checks when only LFS pointer stubs are available.
- Added explicit CSV escaping when writing TOA5 output so Python 3.10 can serialize text fields that require escaping.

## [0.2.1] - 2026-05-21

### Added
- Expanded test coverage with new CLI and internal helper test modules.
- Added targeted `CSIDataFile` branch tests while preserving full end-to-end fixture sweep coverage.

### CI/Build
- Added a local pre-commit pytest fast gate (`not cardconvert`) and a full pytest pre-push gate.

## [0.2.0]

### Added
- Metadata-aware writer defaults now use normalized metadata while keeping explicit keyword arguments as overrides.
- Conversion supports split-window output across writer formats.
- Tests for normalized metadata vs per-file metadata behavior and writer precedence.

### Changed
- Conversion and writer metadata sourcing now consistently prefers normalized metadata.
- Package distribution name changed to `csiio-py` to avoid naming conflicts.
- Packaging and tool configuration expanded in `pyproject.toml`.

### Fixed
- Multiple linter and import cleanup issues.

### CI/Build
- Added pre-commit hooks (`ruff`, `ruff-format`, `black`, and standard checks).
- Publishing workflow tightened to release-on-tag behavior.
- Git LFS tracking added for large fixture files.
- Added a GitHub Actions test workflow running pytest on push and pull requests across Python 3.10, 3.11, 3.12, 3.13, and 3.14.
- Updated the local changelog pre-commit hook to require CHANGELOG.md changes only when relevant staged code/config files are modified.
- Updated the publish workflow so PyPI publishing runs only after TestPyPI publishing succeeds.
- Fixed CI dependency installation to use optional dependency extras (`pip install ".[dev]"`) instead of `--group dev`.

### Docs
- README updates for DataFrame initialization, split-window conversion, and metadata model (`meta` vs `file_meta`).

## [0.1.0] - 2026-05-20

### Added
- Initial public release.
