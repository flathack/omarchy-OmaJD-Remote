import importlib.util
import os
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = PROJECT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


verify_environment = load_script("verify_environment.py")
prune_environments = load_script("prune_environments.py")


class EnvironmentVerificationTests(unittest.TestCase):
    def test_current_environment_matches_lock(self):
        self.assertEqual(verify_environment.verify(PROJECT / "requirements.lock"), [])

    def test_version_drift_is_rejected_even_when_package_exists(self):
        expected = verify_environment.locked_versions(PROJECT / "requirements.lock")
        with mock.patch.object(verify_environment.importlib.metadata, "version", return_value="0.0-drifted"):
            errors = verify_environment.verify(PROJECT / "requirements.lock")
        self.assertEqual(len(errors), len(expected))
        self.assertTrue(all("expected" in error for error in errors))


class EnvironmentPruningTests(unittest.TestCase):
    def test_pruning_keeps_active_newest_rollback_and_symlinks(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / (".venv-" + "a" * 64)
            newest = root / (".venv-" + "b" * 64)
            oldest = root / (".venv-" + "c" * 64)
            for index, path in enumerate((oldest, newest, active), start=1):
                path.mkdir()
                os.utime(path, (index, index))
            outside = root / "outside"
            outside.mkdir()
            link = root / (".venv-" + "d" * 64)
            link.symlink_to(outside, target_is_directory=True)
            interrupted = root / (".venv-" + "e" * 64 + ".new.1234")
            interrupted.mkdir()

            removed = prune_environments.prune(root, active, keep_rollbacks=1)
            self.assertEqual(removed, [oldest])
            self.assertTrue(active.is_dir())
            self.assertTrue(newest.is_dir())
            self.assertTrue(link.is_symlink())
            self.assertTrue(outside.is_dir())
            self.assertTrue(interrupted.is_dir())


class BrowserPackagingTests(unittest.TestCase):
    def test_rebuild_drops_stale_archive_members(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copy2(PROJECT / "scripts" / "build-extension.sh", root / "scripts" / "build-extension.sh")
            shutil.copytree(PROJECT / "browser-extension", root / "browser-extension")
            script = root / "scripts" / "build-extension.sh"
            subprocess.run([str(script)], check=True, cwd=root, capture_output=True, text=True)
            archive = root / "dist" / "omajdownload-clicknload-chromium.zip"
            with zipfile.ZipFile(archive, "a") as package:
                package.writestr("stale-review-artifact.js", "unexpected")
            subprocess.run([str(script)], check=True, cwd=root, capture_output=True, text=True)
            with zipfile.ZipFile(archive) as package:
                self.assertNotIn("stale-review-artifact.js", package.namelist())
                self.assertEqual(set(package.namelist()), {
                    "content-script.js", "icons/", "icons/icon-128.png", "icons/icon-48.png",
                    "manifest.json", "page-bridge.js", "service-worker.js",
                })


if __name__ == "__main__":
    unittest.main()
