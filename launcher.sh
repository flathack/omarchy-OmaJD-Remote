#!/usr/bin/env bash
set -euo pipefail

plugin_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_root="${XDG_DATA_HOME:-$HOME/.local/share}/omajdownload"
python_bin="$data_root/venv/bin/python"

if [[ ! -x "$python_bin" ]]; then
  printf '%s\n' '{"type":"fatal","code":"helper_missing","message":"Install the OmaJDownLoad helper to continue."}'
  exit 78
fi

exec "$python_bin" "$plugin_dir/jdctl.py" "${1:-daemon}"
