#!/usr/bin/env python3
"""Verify that plugin, browser, changelog, and optional tag versions agree."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def manifest_version(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8")).get("version", "")
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise RuntimeError(f"{path.name} has an invalid release version: {value!r}")
    return value


def verify(tag: str = "") -> str:
    versions = {
        manifest_version(PROJECT / "manifest.json"),
        manifest_version(PROJECT / "browser-extension" / "manifest.json"),
        manifest_version(PROJECT / "browser-extension" / "manifest.firefox.json"),
    }
    if len(versions) != 1:
        raise RuntimeError(f"manifest versions do not match: {sorted(versions)}")
    version = versions.pop()
    changelog = (PROJECT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.MULTILINE):
        raise RuntimeError(f"CHANGELOG.md has no dated [{version}] release section")
    if tag and tag != f"v{version}":
        raise RuntimeError(f"tag {tag!r} does not match manifest version v{version}")
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    print(verify(args.tag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
