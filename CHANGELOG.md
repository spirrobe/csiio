# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning.

## [Unreleased]

### CI/Build
- Added a GitHub Actions test workflow running pytest on push/pull_request across Python 3.10, 3.11, 3.12, 3.13, and 3.14.
- Updated the local changelog pre-commit hook to require CHANGELOG.md changes only when relevant staged code/config files are modified.
- dependency to only push to pypi when testpypi succeeded
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

### Docs
- README updates for DataFrame initialization, split-window conversion, and metadata model (`meta` vs `file_meta`).

## [0.1.0] - 2026-05-20

### Added
- Initial public release.
