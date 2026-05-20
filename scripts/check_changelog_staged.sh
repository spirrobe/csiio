#!/bin/bash
# Fails commit if CHANGELOG.md does not have staged changes
if git diff --cached --quiet -- CHANGELOG.md; then
    echo "[PRE-COMMIT] CHANGELOG.md has no staged changes. Please update and stage the changelog before committing."
    exit 1
else
    exit 0
fi
