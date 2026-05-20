#!/usr/bin/env python3
"""Promote CHANGELOG.md [Unreleased] entries into a versioned section."""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import sys

UNRELEASED_HEADER = "## [Unreleased]"
VERSION_HEADER_RE = re.compile(r"^## \[[^\]]+\]", re.MULTILINE)

UNRELEASED_TEMPLATE = [
    "",
    "### Added",
    "",
    "### Changed",
    "",
    "### Fixed",
    "",
    "### CI/Build",
    "",
    "### Docs",
    "",
]


def _find_unreleased_block(lines: list[str]) -> tuple[int, int]:
    try:
        start = lines.index(UNRELEASED_HEADER)
    except ValueError as exc:
        raise ValueError("Could not find '## [Unreleased]' section.") from exc

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if lines[idx].startswith("## ["):
            end = idx
            break
    return start, end


def _has_release_entries(unreleased_body: list[str]) -> bool:
    return any(line.lstrip().startswith("- ") for line in unreleased_body)


def promote_unreleased(changelog: str, version: str, date: str) -> str:
    lines = changelog.splitlines()
    start, end = _find_unreleased_block(lines)

    unreleased_body = lines[start + 1 : end]
    if not _has_release_entries(unreleased_body):
        raise ValueError("[Unreleased] has no bullet entries to release.")

    while unreleased_body and unreleased_body[0] == "":
        unreleased_body = unreleased_body[1:]
    while unreleased_body and unreleased_body[-1] == "":
        unreleased_body = unreleased_body[:-1]

    release_header = f"## [{version}] - {date}"
    new_lines = []
    new_lines.extend(lines[: start + 1])
    new_lines.extend(UNRELEASED_TEMPLATE)
    new_lines.append(release_header)
    new_lines.append("")
    new_lines.extend(unreleased_body)
    new_lines.append("")
    new_lines.extend(lines[end:])

    content = "\n".join(new_lines)
    if not content.endswith("\n"):
        content += "\n"
    return content


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Move CHANGELOG [Unreleased] entries into a versioned section."
    )
    parser.add_argument("version", help="Release version, for example: 0.2.0")
    parser.add_argument(
        "--date",
        default=dt.date.today().isoformat(),
        help="Release date (YYYY-MM-DD). Default: today",
    )
    parser.add_argument(
        "--file",
        default="CHANGELOG.md",
        help="Path to changelog file. Default: CHANGELOG.md",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the result to stdout instead of writing the file",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not re.match(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$", args.version):
        print("Version must look like semantic versioning (for example 0.2.0).", file=sys.stderr)
        return 2

    changelog_path = pathlib.Path(args.file)
    if not changelog_path.exists():
        print(f"Changelog file not found: {changelog_path}", file=sys.stderr)
        return 2

    source = changelog_path.read_text(encoding="utf-8")

    try:
        updated = promote_unreleased(source, args.version, args.date)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.dry_run:
        print(updated)
        return 0

    changelog_path.write_text(updated, encoding="utf-8")
    print(f"Updated {changelog_path} with release {args.version} ({args.date}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
