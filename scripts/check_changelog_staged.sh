#!/bin/bash
# Require a changelog update only when relevant code/config changes are staged.
set -euo pipefail

STAGED_FILES="$(git diff --cached --name-only)"

# If nothing is staged, let pre-commit continue.
if [[ -z "${STAGED_FILES}" ]]; then
    exit 0
fi

CODE_CHANGES="$(echo "${STAGED_FILES}" | grep -E '^(csiio/|tests/|pyproject.toml|MANIFEST.in|\.github/workflows/)' || true)"

# No relevant code/config changes staged -> no changelog requirement.
if [[ -z "${CODE_CHANGES}" ]]; then
    exit 0
fi

# Relevant changes are staged; require actual staged changes in CHANGELOG.md.
if git diff --cached --quiet -- CHANGELOG.md; then
    echo "[PRE-COMMIT] Relevant code/config changes detected but CHANGELOG.md has no staged changes."
    echo "Please add an entry under [Unreleased] and stage CHANGELOG.md before committing."
    exit 1
fi

exit 0
