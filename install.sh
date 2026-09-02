#!/usr/bin/env bash
set -euo pipefail

plugin_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_root="${XDG_DATA_HOME:-$HOME/.local/share}/omajdownload"

command -v python >/dev/null || {
  echo "Python is required." >&2
  exit 1
}
command -v secret-tool >/dev/null || {
  echo "secret-tool is required (package: libsecret)." >&2
  exit 1
}

exec python "$plugin_dir/scripts/secure_install.py" "$plugin_dir" "$data_root"
