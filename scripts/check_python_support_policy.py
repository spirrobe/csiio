#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import datetime
import re
from collections.abc import Iterable
from pathlib import Path

EOL_SCHEDULE = {
    "3.8": datetime.date(2024, 10, 14),
    "3.9": datetime.date(2025, 10, 14),
    "3.10": datetime.date(2026, 10, 14),
    "3.11": datetime.date(2027, 10, 14),
    "3.12": datetime.date(2028, 10, 14),
    "3.13": datetime.date(2029, 10, 14),
    "3.14": datetime.date(2030, 10, 14),
}
WARNING_WINDOW_DAYS = 180
GRACE_PERIOD_DAYS = 365
# Versions may remain supported for up to one year after official EOL.

PYTHON_CLASSIFIER_PREFIX = "Programming Language :: Python :: "


def parse_pyproject(pyproject_path: Path) -> tuple[str, list[str]]:
    text = pyproject_path.read_text(encoding="utf-8")

    requires_match = re.search(r"^\s*requires-python\s*=\s*([\"'].*?[\"'])", text, re.MULTILINE)
    if requires_match is None:
        raise ValueError("Could not parse requires-python from pyproject.toml")
    requires_python = ast.literal_eval(repr(requires_match.group(1).strip("\"'")))

    classifiers_match = re.search(
        r"^\s*classifiers\s*=\s*\[(.*?)\]",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if classifiers_match is None:
        raise ValueError("Could not parse classifiers from pyproject.toml")

    classifier_list = classifiers_match.group(1).strip()
    classifiers = ast.literal_eval(f"[{classifier_list}]")
    return requires_python, classifiers


def normalize_version(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def parse_requires_python_minimum(requires_python: str) -> tuple[int, ...] | None:
    parts = [part.strip() for part in requires_python.split(",") if part.strip()]
    minimum: tuple[int, ...] | None = None
    for part in parts:
        if part.startswith(">="):
            version = part[2:].strip()
            if version:
                candidate = normalize_version(version)
                minimum = candidate if minimum is None else min(minimum, candidate)
        elif re.match(r"^[0-9]+(\.[0-9]+)*$", part):
            candidate = normalize_version(part)
            minimum = candidate if minimum is None else min(minimum, candidate)
    return minimum


def python_versions_from_classifiers(classifiers: Iterable[str]) -> list[str]:
    versions = []
    for classifier in classifiers:
        match = re.fullmatch(r"Programming Language :: Python :: (3\.\d+)", classifier.strip())
        if match:
            versions.append(match.group(1))
    return sorted(versions, key=normalize_version)


def compare_versions(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    return (left > right) - (left < right)


def format_version(version_tuple: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version_tuple)


def check_version_policy(requires_python: str, classifiers: list[str]) -> int:
    today = datetime.date.today()
    exit_code = 0

    classifier_versions = python_versions_from_classifiers(classifiers)
    if not classifier_versions:
        print("ERROR: No supported Python 3 classifiers found in pyproject.toml.")
        return 1

    minimum_spec = parse_requires_python_minimum(requires_python)
    if minimum_spec is None:
        print(
            f"ERROR: Unable to parse a minimum Python version from requires-python='{requires_python}'"
        )
        return 1

    classifier_version_tuples = [normalize_version(v) for v in classifier_versions]
    lowest_classifier = classifier_version_tuples[0]

    if compare_versions(minimum_spec, lowest_classifier) > 0:
        print(
            "ERROR: pyproject.toml classifiers declare support for versions older than the requires-python minimum."
        )
        print(f"  requires-python minimum: {format_version(minimum_spec)}")
        print(f"  lowest supported classifier: {format_version(lowest_classifier)}")
        exit_code = 1

    past_eol_versions = []
    grace_period_versions = []
    warning_versions = []
    for version in classifier_versions:
        if version not in EOL_SCHEDULE:
            continue
        eol_date = EOL_SCHEDULE[version]
        grace_end_date = eol_date + datetime.timedelta(days=GRACE_PERIOD_DAYS)
        if today > grace_end_date:
            past_eol_versions.append((version, eol_date, grace_end_date))
        elif today > eol_date:
            grace_period_versions.append(
                (version, eol_date, grace_end_date, (grace_end_date - today).days)
            )
        elif (eol_date - today).days <= WARNING_WINDOW_DAYS:
            warning_versions.append((version, eol_date, (eol_date - today).days))

    if past_eol_versions:
        print("ERROR: The following supported Python versions are past their EOL grace period:")
        for version, eol_date, grace_end_date in past_eol_versions:
            print(
                f"  - Python {version} (EOL {eol_date.isoformat()}, grace ended {grace_end_date.isoformat()})"
            )
        exit_code = 1

    if grace_period_versions:
        print(
            "WARNING: The following supported Python versions are past official EOL but still allowed under the 1-year grace period:"
        )
        for version, eol_date, grace_end_date, days_left in grace_period_versions:
            print(
                f"  - Python {version} (EOL {eol_date.isoformat()}, grace ends {grace_end_date.isoformat()} in {days_left} days)"
            )
        print(
            "Support is permitted for 1 year after official EOL, but plan to remove these versions before grace expires."
        )

    if warning_versions:
        print("WARNING: The following supported Python versions are entering EOL soon:")
        for version, eol_date, days_left in warning_versions:
            print(
                f"  - Python {version} will reach EOL on {eol_date.isoformat()} ({days_left} days left)"
            )
        print(
            "Consider removing versions from support soon or updating the package metadata if support will continue through the EOL date."
        )

    if compare_versions(minimum_spec, normalize_version(classifier_versions[-1])) > 0:
        print(
            "ERROR: The requires-python minimum is greater than the highest declared supported classifier."
        )
        print(f"  requires-python minimum: {format_version(minimum_spec)}")
        print(f"  highest supported classifier: {classifier_versions[-1]}")
        exit_code = 1

    if exit_code == 0:
        print("Python support policy check passed.")

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate Python support policy against pyproject.toml metadata. "
            "This enforces supported Python classifiers and allows a 1-year grace "
            "period after official EOL for declared versions."
        )
    )
    parser.add_argument(
        "--pyproject",
        default="pyproject.toml",
        help="Path to pyproject.toml",
    )
    args = parser.parse_args()

    requires_python, classifiers = parse_pyproject(Path(args.pyproject))
    return check_version_policy(requires_python, classifiers)


if __name__ == "__main__":
    raise SystemExit(main())
