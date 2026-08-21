#!/usr/bin/env python3
import json
import re
import sys
import urllib.request

PACKAGE_NAME = "csiio-py"  # Replace with your actual package name


def get_current_project_version():
    with open("pyproject.toml") as f:
        match = re.search(r'^version\s*=\s*"(.*?)"', f.read(), re.MULTILINE)
        if match:
            return match.group(1)
    sys.exit("❌ Could not find version in pyproject.toml")


def main():
    version = get_current_project_version()
    url = f"https://test.pypi.org/pypi/{PACKAGE_NAME}/json"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            released_versions = data.get("releases", {}).keys()

            if version in released_versions:
                print(f"\n❌ ERROR: Version '{version}' already exists on TestPyPI!")
                print("You must bump your version in pyproject.toml before pushing/releasing.")
                return 1
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"📦 Package '{PACKAGE_NAME}' not found on TestPyPI yet. Proceeding.")
        else:
            print(f"⚠️ Warning: Could not check TestPyPI (HTTP {e.code}). Proceeding.")

    print(f"✅ Version '{version}' is safe to publish.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
