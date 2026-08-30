#!/usr/bin/env bash
set -euo pipefail

plugin_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_root="${XDG_DATA_HOME:-$HOME/.local/share}/omajdownload"
venv_dir="$data_root/venv"
lock_file="$plugin_dir/requirements.lock"

command -v python >/dev/null || {
  echo "Python is required." >&2
  exit 1
}
command -v sha256sum >/dev/null || {
  echo "sha256sum is required (package: coreutils)." >&2
  exit 1
}

lock_hash="$(sha256sum "$lock_file" | cut -d' ' -f1)"
environment_dir="$data_root/.venv-$lock_hash"
staging_dir="$data_root/.venv-$lock_hash.new.$PPID"
backup_suffix="$(date +%s%N)-$$"
command -v secret-tool >/dev/null || {
  echo "secret-tool is required (package: libsecret)." >&2
  exit 1
}

mkdir -p "$data_root"
if [[ -e "$staging_dir" ]]; then
  echo "Staging environment already exists: $staging_dir" >&2
  exit 1
fi

cleanup_staging() {
  if [[ -d "$staging_dir" ]]; then
    rm -rf -- "$staging_dir"
  fi
}
trap cleanup_staging EXIT

python -m venv "$staging_dir"
"$staging_dir/bin/pip" install --disable-pip-version-check --require-hashes -r "$lock_file"
"$staging_dir/bin/python" "$plugin_dir/scripts/verify_environment.py" "$lock_file"
"$staging_dir/bin/python" -c 'import myjdapi; from Crypto.Cipher import AES'
printf '%s\n' "$lock_hash" > "$staging_dir/omajdownload-requirements.sha256"

if [[ -e "$environment_dir" || -L "$environment_dir" ]]; then
  mv "$environment_dir" "$environment_dir.broken.$backup_suffix"
fi
mv "$staging_dir" "$environment_dir"

if [[ -d "$venv_dir" && ! -L "$venv_dir" ]]; then
  mv "$venv_dir" "$data_root/venv.legacy.$backup_suffix"
fi
ln -sfn ".venv-$lock_hash" "$data_root/venv.next"
mv -Tf "$data_root/venv.next" "$venv_dir"

if ! "$environment_dir/bin/python" "$plugin_dir/scripts/prune_environments.py" "$data_root" "$environment_dir"; then
  echo "Warning: superseded helper environments could not be pruned." >&2
fi

echo "OmaJDownLoad helper installed and verified in $environment_dir"
