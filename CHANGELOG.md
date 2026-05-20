# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning.

## [Unreleased]

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

### Docs
- README updates for DataFrame initialization, split-window conversion, and metadata model (`meta` vs `file_meta`).

## [0.1.0] - 2026-05-20

### Added
- Initial public release.
