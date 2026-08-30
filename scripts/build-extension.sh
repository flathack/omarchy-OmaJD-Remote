#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$project_dir/browser-extension"
output_dir="$project_dir/dist"
build_root="$(mktemp -d)"
source_date_epoch="${SOURCE_DATE_EPOCH:-$(git -C "$project_dir" log -1 --format=%ct 2>/dev/null || printf '315532800')}"
staged_archives=()

cleanup() {
  rm -rf -- "$build_root"
}
trap cleanup EXIT

command -v zip >/dev/null || {
  echo "zip is required to package the browser extension." >&2
  exit 1
}
command -v unzip >/dev/null || {
  echo "unzip is required to verify browser packages." >&2
  exit 1
}

mkdir -p "$output_dir" "$build_root/chromium/icons" "$build_root/firefox/icons"

for target in chromium firefox; do
  cp "$source_dir/service-worker.js" "$build_root/$target/"
  cp "$source_dir/content-script.js" "$build_root/$target/"
  cp "$source_dir/page-bridge.js" "$build_root/$target/"
  cp "$source_dir/icons/icon-48.png" "$build_root/$target/icons/"
  cp "$source_dir/icons/icon-128.png" "$build_root/$target/icons/"
done

cp "$source_dir/manifest.chromium.json" "$build_root/chromium/manifest.json"
cp "$source_dir/manifest.firefox.json" "$build_root/firefox/manifest.json"

find "$build_root/chromium" "$build_root/firefox" -type d -exec chmod 0755 {} +
find "$build_root/chromium" "$build_root/firefox" -type f -exec chmod 0644 {} +
find "$build_root/chromium" "$build_root/firefox" -exec touch -d "@$source_date_epoch" {} +

expected_members=$'content-script.js\nicons/\nicons/icon-128.png\nicons/icon-48.png\nmanifest.json\npage-bridge.js\nservice-worker.js'

package_target() {
  local target="$1"
  local destination="$output_dir/omajd-remote-clicknload-$target.zip"
  local built_archive="$build_root/omajd-remote-clicknload-$target.zip"
  local staged_archive
  staged_archive="$(mktemp "$output_dir/.omajd-remote-clicknload-$target.XXXXXX.zip")"
  staged_archives+=("$staged_archive")

  (
    cd "$build_root/$target"
    zip -Xq9 "$built_archive" \
      content-script.js icons/ icons/icon-128.png icons/icon-48.png \
      manifest.json page-bridge.js service-worker.js
  )
  actual_members="$(unzip -Z1 "$built_archive" | sort)"
  if [[ "$actual_members" != "$expected_members" ]]; then
    echo "Unexpected files in $target browser package:" >&2
    diff -u <(printf '%s\n' "$expected_members") <(printf '%s\n' "$actual_members") >&2 || true
    exit 1
  fi
  cp "$built_archive" "$staged_archive"
  mv -f "$staged_archive" "$destination"
}

cleanup_output() {
  if (( ${#staged_archives[@]} > 0 )); then
    rm -f -- "${staged_archives[@]}"
  fi
}
trap 'cleanup_output; cleanup' EXIT

package_target chromium
package_target firefox

echo "Browser packages created in $output_dir"
