#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
environment_dir="${OMAJDOWNLOAD_DEV_VENV:-${XDG_CACHE_HOME:-$HOME/.cache}/omajdownload-dev-venv}"

exec python "$project_dir/scripts/secure_install.py" --development "$project_dir" "$environment_dir"
