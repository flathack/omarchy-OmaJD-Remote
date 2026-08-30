#!/usr/bin/env python3
"""Safely prune superseded OmaJDownLoad virtual environments."""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path


OWNED = re.compile(
    r"(?:\.venv-[0-9a-f]{64}(?:\.installed\.[0-9-]+|\.broken\.[0-9-]+)?|venv\.legacy\.[0-9-]+)"
)


def prune(root: Path, active: Path, keep_rollbacks: int = 1) -> list[Path]:
    root = root.resolve(strict=True)
    active = active.resolve(strict=True)
    candidates = [
        path for path in root.iterdir()
        if path.is_dir() and not path.is_symlink() and OWNED.fullmatch(path.name) and path.resolve() != active
    ]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    removed: list[Path] = []
    for path in candidates[max(0, keep_rollbacks):]:
        if path.parent.resolve() != root or not OWNED.fullmatch(path.name):
            raise RuntimeError(f"refusing to remove unexpected path: {path}")
        shutil.rmtree(path)
        removed.append(path)
    return removed


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: prune_environments.py DATA_ROOT ACTIVE_ENV", file=sys.stderr)
        return 2
    try:
        removed = prune(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if removed:
        print(f"Pruned {len(removed)} superseded helper environment(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
