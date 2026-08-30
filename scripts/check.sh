#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

python_bin="${PYTHON_BIN:-python}"

"$python_bin" -m json.tool manifest.json >/dev/null
"$python_bin" -m py_compile jdctl.py tests/test_jdctl.py
bash -n install.sh launcher.sh scripts/check.sh
"$python_bin" -m unittest discover -s tests -v

if command -v omarchy >/dev/null 2>&1; then
  omarchy plugin validate "$project_dir"
fi

echo "All checks passed."
