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
backup_suffix="$(date +%s%N)-$$"
command -v secret-tool >/dev/null || {
  echo "secret-tool is required (package: libsecret)." >&2
  exit 1
}

mkdir -p "$data_root"
staging_dir="$(mktemp -d "$data_root/.venv-$lock_hash.new.XXXXXX")"
environment_dir="$data_root/.venv-$lock_hash.installed.$backup_suffix"
next_link="$data_root/.venv-link.$backup_suffix"
legacy_backup=""
environment_committed=0

cleanup_staging() {
  if [[ -d "$staging_dir" ]]; then
    rm -rf -- "$staging_dir"
  fi
  if [[ -L "$next_link" ]]; then
    rm -f -- "$next_link"
  fi
  if [[ "$environment_committed" == "0" && -n "$legacy_backup" && -d "$legacy_backup" && ! -e "$venv_dir" ]]; then
    mv "$legacy_backup" "$venv_dir"
  fi
}
trap cleanup_staging EXIT

python -m venv "$staging_dir"
"$staging_dir/bin/pip" install --disable-pip-version-check --require-hashes -r "$lock_file"
"$staging_dir/bin/python" "$plugin_dir/scripts/verify_environment.py" "$lock_file"
"$staging_dir/bin/python" -c 'import myjdapi; from Crypto.Cipher import AES'
printf '%s\n' "$lock_hash" > "$staging_dir/omajdownload-requirements.sha256"

mv "$staging_dir" "$environment_dir"

if [[ -d "$venv_dir" && ! -L "$venv_dir" ]]; then
  legacy_backup="$data_root/venv.legacy.$backup_suffix"
  mv "$venv_dir" "$legacy_backup"
  touch "$legacy_backup"
fi
ln -s "$(basename "$environment_dir")" "$next_link"
mv -Tf "$next_link" "$venv_dir"
environment_committed=1

if ! "$environment_dir/bin/python" "$plugin_dir/scripts/prune_environments.py" "$data_root" "$environment_dir"; then
  echo "Warning: superseded helper environments could not be pruned." >&2
fi

echo "OmaJDownLoad helper installed and verified in $environment_dir"
