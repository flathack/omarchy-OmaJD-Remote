#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
omarchy_shell="${OMARCHY_SHELL_SOURCE:-/usr/share/omarchy/shell}"
quickshell_bin="$(command -v qs || command -v quickshell || true)"

if [[ -z "$quickshell_bin" ]]; then
  echo "Quickshell is required for the QML runtime smoke test." >&2
  exit 1
fi
if [[ ! -d "$omarchy_shell/Commons" || ! -d "$omarchy_shell/Ui" ]]; then
  echo "Omarchy shell Commons and Ui sources are required: $omarchy_shell" >&2
  exit 1
fi

qml_import_root="$(mktemp -d)"
runtime_log="$(mktemp)"
cleanup() {
  rm -rf -- "$qml_import_root"
  rm -f -- "$runtime_log"
}
trap cleanup EXIT
mkdir -p "$qml_import_root/Plugin"
cp "$project_dir/tests/qml-smoke.qml" "$qml_import_root/shell.qml"
cp "$project_dir/BarWidget.qml" "$project_dir/Service.qml" "$project_dir/DownloadMark.qml" "$qml_import_root/Plugin/"
ln -s "$omarchy_shell/Commons" "$qml_import_root/Commons"
ln -s "$omarchy_shell/Ui" "$qml_import_root/Ui"

set +e
QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" \
  timeout 10s "$quickshell_bin" --no-duplicate --path "$qml_import_root/shell.qml" \
  >"$runtime_log" 2>&1
status=$?
set -e
if [[ "$status" != "0" ]]; then
  cat "$runtime_log" >&2
  echo "QML runtime smoke test failed with exit $status." >&2
  exit "$status"
fi

# Quickshell infrastructure messages are allowed; QML load/runtime warnings are not.
if grep -Eqi '(^|[[:space:]])(warning|critical|fatal):|failed to load|is not a type|cannot assign|referenceerror|typeerror' "$runtime_log"; then
  cat "$runtime_log" >&2
  echo "QML runtime smoke test emitted a non-allowlisted warning." >&2
  exit 1
fi
