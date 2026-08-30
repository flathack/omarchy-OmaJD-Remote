#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$project_dir/browser-extension"
output_dir="$project_dir/dist"
build_root="$(mktemp -d)"

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

cp "$source_dir/manifest.json" "$build_root/chromium/manifest.json"
cp "$source_dir/manifest.firefox.json" "$build_root/firefox/manifest.json"

expected_members=$'content-script.js\nicons/\nicons/icon-128.png\nicons/icon-48.png\nmanifest.json\npage-bridge.js\nservice-worker.js'

package_target() {
  local target="$1"
  local destination="$output_dir/omajdownload-clicknload-$target.zip"
  local built_archive="$build_root/omajdownload-clicknload-$target.zip"
  local staged_archive="$output_dir/.omajdownload-clicknload-$target.$PPID.zip"

  (
    cd "$build_root/$target"
    zip -qr "$built_archive" .
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
  rm -f -- "$output_dir/.omajdownload-clicknload-chromium.$PPID.zip" "$output_dir/.omajdownload-clicknload-firefox.$PPID.zip"
}
trap 'cleanup_output; cleanup' EXIT

package_target chromium
package_target firefox

echo "Browser packages created in $output_dir"
