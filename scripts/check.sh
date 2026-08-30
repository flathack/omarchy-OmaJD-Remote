#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

python_bin="${PYTHON_BIN:-python}"

"$python_bin" -m json.tool manifest.json >/dev/null
"$python_bin" -m json.tool browser-extension/manifest.json >/dev/null
"$python_bin" -m json.tool browser-extension/manifest.firefox.json >/dev/null
diff -u \
  <(sort requirements.txt) \
  <(awk '/^[[:alnum:]][^ ]*==/{print $1}' requirements.lock | sort)
"$python_bin" scripts/verify_release.py >/dev/null
"$python_bin" -m py_compile jdctl.py scripts/verify_environment.py scripts/prune_environments.py scripts/verify_release.py tests/test_jdctl.py tests/test_ui_contract.py
bash -n install.sh launcher.sh scripts/check.sh scripts/check-qml.sh scripts/qml-runtime-smoke.sh scripts/setup-dev.sh scripts/build-extension.sh
qml_formatter="$(command -v qmlformat || true)"
if [[ -z "$qml_formatter" && -x /usr/lib/qt6/bin/qmlformat ]]; then
  qml_formatter=/usr/lib/qt6/bin/qmlformat
fi
if [[ "${REQUIRE_QML_SEMANTICS:-0}" == "1" ]]; then
  ./scripts/check-qml.sh
  ./scripts/qml-runtime-smoke.sh
fi
if [[ -n "$qml_formatter" ]]; then
  "$qml_formatter" BarWidget.qml >/dev/null
  "$qml_formatter" Service.qml >/dev/null
  "$qml_formatter" DownloadMark.qml >/dev/null
elif [[ "${REQUIRE_QML_TOOLS:-0}" == "1" ]]; then
  echo "qmlformat is required for this check." >&2
  exit 1
fi
if command -v node >/dev/null 2>&1; then
  node --check browser-extension/service-worker.js
  node --check browser-extension/content-script.js
  node --check browser-extension/page-bridge.js
  node --test tests/browser_extension_runtime.test.js
elif [[ "${REQUIRE_BROWSER_TESTS:-0}" == "1" ]]; then
  echo "Node.js is required for browser companion runtime tests." >&2
  exit 1
fi
"$python_bin" -m unittest discover -s tests -v
"$python_bin" -m pip check

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck install.sh launcher.sh scripts/check.sh scripts/setup-dev.sh scripts/build-extension.sh
fi

if command -v omarchy >/dev/null 2>&1; then
  omarchy plugin validate "$project_dir"
fi

echo "All checks passed."
