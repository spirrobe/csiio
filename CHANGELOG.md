# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning.

## [Unreleased]

### Added
- Added `exists_action` support to `convert_csi_file`, `CSIDataFile.convert`, and the `csiio convert` CLI command. Supported values are `merge`, `overwrite`, and `skip`.
- Added merge semantics for existing CSI output files with deduplication of duplicate timestamps when `merge` is used.

### Docs
- Documented split-window conversion output naming and CLI usage in `README.md`.

## [0.2.2]
- Support for pathlib inputs

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
