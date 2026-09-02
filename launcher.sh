#!/usr/bin/env bash
set -euo pipefail

plugin_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_root="${XDG_DATA_HOME:-$HOME/.local/share}/omajdownload"
python_bin="$data_root/venv/bin/python"
lock_file="$plugin_dir/requirements.lock"
marker_file="$data_root/venv/omajdownload-requirements.sha256"

command -v setsid >/dev/null || {
  printf '%s\n' '{"type":"fatal","code":"helper_runtime_missing","message":"The util-linux setsid command is required."}'
  exit 78
}

if [[ ! -x "$python_bin" ]]; then
  printf '%s\n' '{"type":"fatal","code":"helper_missing","message":"Install the OmaJD-Remote helper to continue."}'
  exit 78
fi

expected_hash="$(sha256sum "$lock_file" | cut -d' ' -f1)"
installed_hash=""
if [[ -f "$marker_file" && ! -L "$marker_file" ]]; then
  installed_hash="$(head -c 65 -- "$marker_file" 2>/dev/null || true)"
fi
if [[ "$installed_hash" != "$expected_hash" ]] \
  || ! "$python_bin" "$plugin_dir/scripts/verify_environment.py" "$lock_file" >/dev/null 2>&1 \
  || ! "$python_bin" -c 'import myjdapi; from Crypto.Cipher import AES' >/dev/null 2>&1; then
  printf '%s\n' '{"type":"fatal","code":"helper_outdated","message":"Repair the OmaJD-Remote helper to continue."}'
  exit 78
fi

if [[ "${1:-daemon}" != "daemon" ]]; then
  printf '%s\n' '{"type":"fatal","code":"invalid_command","message":"Unsupported helper command."}'
  exit 2
fi

# A private session lets the helper terminate any in-flight keyring/network
# child together with itself on watchdog or shell teardown.
exec setsid "$python_bin" "$plugin_dir/jdctl.py" daemon
