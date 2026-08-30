#!/usr/bin/env python3
"""Verify that the active helper environment exactly matches the runtime lock."""

from __future__ import annotations

import importlib.metadata
import re
import sys
from pathlib import Path


REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_versions(path: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = REQUIREMENT.match(line)
        if match:
            expected[canonical(match.group(1))] = match.group(2)
    if not expected:
        raise RuntimeError("requirements lock contains no packages")
    return expected


def verify(path: Path) -> list[str]:
    errors: list[str] = []
    for name, expected in locked_versions(path).items():
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            errors.append(f"{name} is missing (expected {expected})")
            continue
        if installed != expected:
            errors.append(f"{name} is {installed} (expected {expected})")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_environment.py REQUIREMENTS_LOCK", file=sys.stderr)
        return 2
    try:
        errors = verify(Path(sys.argv[1]))
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if errors:
        print("; ".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
