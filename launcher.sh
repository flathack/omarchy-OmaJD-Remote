#!/usr/bin/env bash
set -euo pipefail

plugin_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_root="${XDG_DATA_HOME:-$HOME/.local/share}/omajdownload"
python_bin="$data_root/venv/bin/python"
lock_file="$plugin_dir/requirements.lock"
marker_file="$data_root/venv/omajdownload-requirements.sha256"

if [[ ! -x "$python_bin" ]]; then
  printf '%s\n' '{"type":"fatal","code":"helper_missing","message":"Install the OmaJDownLoad helper to continue."}'
  exit 78
fi

expected_hash="$(sha256sum "$lock_file" | cut -d' ' -f1)"
installed_hash="$(cat "$marker_file" 2>/dev/null || true)"
if [[ "$installed_hash" != "$expected_hash" ]] \
  || ! "$python_bin" "$plugin_dir/scripts/verify_environment.py" "$lock_file" >/dev/null 2>&1 \
  || ! "$python_bin" -c 'import myjdapi; from Crypto.Cipher import AES' >/dev/null 2>&1; then
  printf '%s\n' '{"type":"fatal","code":"helper_outdated","message":"Repair the OmaJDownLoad helper to continue."}'
  exit 78
fi

exec "$python_bin" "$plugin_dir/jdctl.py" "${1:-daemon}"
