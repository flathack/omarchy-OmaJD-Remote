import json
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
EXTENSION = PROJECT / "browser-extension"


class BrowserExtensionTests(unittest.TestCase):
    def test_manifests_are_minimally_scoped(self):
        for filename in ("manifest.json", "manifest.firefox.json"):
            manifest = json.loads((EXTENSION / filename).read_text(encoding="utf-8"))
            permissions = manifest.get("host_permissions", [])
            self.assertNotIn("<all_urls>", permissions)
            self.assertEqual(
                set(permissions),
                {"http://127.0.0.1:9666/*", "http://localhost:9666/*"},
            )
            self.assertEqual(manifest["manifest_version"], 3)
            self.assertEqual(manifest.get("permissions", []), [])

    def test_manifest_files_exist(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        referenced = {
            *manifest["background"].get("scripts", []),
            manifest["background"].get("service_worker", ""),
            *manifest["icons"].values(),
        }
        for content_script in manifest["content_scripts"]:
            referenced.update(content_script["js"])
        for relative in referenced - {""}:
            self.assertTrue((EXTENSION / relative).is_file(), relative)

    def test_service_worker_accepts_only_loopback_clicknload(self):
        source = (EXTENSION / "service-worker.js").read_text(encoding="utf-8")
        self.assertIn('url.protocol !== "http:"', source)
        self.assertIn('url.port !== "9666"', source)
        self.assertIn('url.pathname === "/flash/add"', source)
        self.assertIn('url.pathname === "/jdcheck.js"', source)
        self.assertIn("/omajdownload/extension-token", source)
        self.assertIn("CNL_LIMIT = 1024 * 1024", source)


if __name__ == "__main__":
    unittest.main()
