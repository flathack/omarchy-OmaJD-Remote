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

(
  cd "$build_root/chromium"
  zip -qr "$output_dir/omajdownload-clicknload-chromium.zip" .
)
(
  cd "$build_root/firefox"
  zip -qr "$output_dir/omajdownload-clicknload-firefox.zip" .
)

echo "Browser packages created in $output_dir"
