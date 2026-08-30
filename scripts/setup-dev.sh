#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
environment_dir="${OMAJDOWNLOAD_DEV_VENV:-${XDG_CACHE_HOME:-$HOME/.cache}/omajdownload-dev-venv}"

python -m venv "$environment_dir"
"$environment_dir/bin/pip" install --disable-pip-version-check --require-hashes -r "$project_dir/requirements.lock"
PYTHON_BIN="$environment_dir/bin/python" "$project_dir/scripts/check.sh"

echo "Development environment is ready at $environment_dir"
