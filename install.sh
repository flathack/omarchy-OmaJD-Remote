#!/usr/bin/env bash
set -euo pipefail

plugin_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_root="${XDG_DATA_HOME:-$HOME/.local/share}/omajdownload"
venv_dir="$data_root/venv"

command -v python >/dev/null || {
  echo "Python is required." >&2
  exit 1
}
command -v secret-tool >/dev/null || {
  echo "secret-tool is required (package: libsecret)." >&2
  exit 1
}

mkdir -p "$data_root"
python -m venv "$venv_dir"
"$venv_dir/bin/pip" install --disable-pip-version-check -r "$plugin_dir/requirements.txt"

echo "OmaJDownLoad helper installed in $venv_dir"
