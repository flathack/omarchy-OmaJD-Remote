#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
qml_linter="$(command -v qmllint || true)"
if [[ -z "$qml_linter" && -x /usr/lib/qt6/bin/qmllint ]]; then
  qml_linter=/usr/lib/qt6/bin/qmllint
fi
if [[ -z "$qml_linter" ]]; then
  echo "qmllint is required for semantic QML validation." >&2
  exit 1
fi

omarchy_shell="${OMARCHY_SHELL_SOURCE:-/usr/share/omarchy/shell}"
if [[ ! -d "$omarchy_shell/Commons" || ! -d "$omarchy_shell/Ui" ]]; then
  echo "Omarchy shell Commons and Ui sources are required: $omarchy_shell" >&2
  exit 1
fi

qml_import_root="$(mktemp -d)"
cleanup() {
  rm -rf -- "$qml_import_root"
}
trap cleanup EXIT
mkdir -p "$qml_import_root/qs"
ln -s "$omarchy_shell/Commons" "$qml_import_root/qs/Commons"
ln -s "$omarchy_shell/Ui" "$qml_import_root/qs/Ui"

"$qml_linter" \
  -I "$qml_import_root" \
  --import error \
  --missing-type error \
  --duplicate-property-binding error \
  --incompatible-type error \
  --signal-handler-parameters info \
  "$project_dir/BarWidget.qml" "$project_dir/Service.qml" "$project_dir/DownloadMark.qml"
