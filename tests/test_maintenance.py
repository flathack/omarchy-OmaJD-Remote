import importlib.util
import hashlib
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
verify_release = load_script("verify_release.py")


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

    def test_installed_environment_names_are_owned(self):
        name = ".venv-" + "a" * 64 + ".installed.123456-42"
        self.assertIsNotNone(prune_environments.OWNED.fullmatch(name))


class InstallerTransactionTests(unittest.TestCase):
    def test_installer_does_not_move_the_live_environment_before_commit(self):
        source = (PROJECT / "install.sh").read_text(encoding="utf-8")
        self.assertIn(".installed.$backup_suffix", source)
        self.assertIn('mv "$staging_dir" "$environment_dir"', source)
        self.assertIn('mv -Tf "$next_link" "$venv_dir"', source)
        self.assertNotIn('mv "$environment_dir" "$environment_dir.broken', source)

    def run_failed_install(self, root, active_kind, failure):
        data_root = root / "share" / "omajdownload"
        data_root.mkdir(parents=True)
        active = data_root / "venv"
        if active_kind == "symlink":
            previous = data_root / "previous-environment"
            (previous / "bin").mkdir(parents=True)
            (previous / "bin" / "python").write_text("working", encoding="utf-8")
            active.symlink_to(previous.name, target_is_directory=True)
        else:
            (active / "bin").mkdir(parents=True)
            (active / "bin" / "python").write_text("working", encoding="utf-8")

        fake_bin = root / "bin"
        fake_bin.mkdir()
        fake_python = fake_bin / "python"
        fake_python.write_text("""#!/usr/bin/env bash
if [[ "$1" == "-m" && "$2" == "venv" ]]; then
  mkdir -p "$3/bin"
  printf '#!/usr/bin/env bash\\nexit 0\\n' > "$3/bin/pip"
  printf '#!/usr/bin/env bash\\nexit 0\\n' > "$3/bin/python"
  chmod +x "$3/bin/pip" "$3/bin/python"
fi
exit 0
""", encoding="utf-8")
        fake_mv = fake_bin / "mv"
        fake_mv.write_text("""#!/usr/bin/env bash
if [[ "${FAIL_MODE:-}" == "commit" && "${1:-}" == "-Tf" ]]; then exit 77; fi
exec /usr/bin/mv "$@"
""", encoding="utf-8")
        fake_ln = fake_bin / "ln"
        fake_ln.write_text("""#!/usr/bin/env bash
if [[ "${FAIL_MODE:-}" == "link" ]]; then exit 78; fi
exec /usr/bin/ln "$@"
""", encoding="utf-8")
        fake_secret = fake_bin / "secret-tool"
        fake_secret.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        for executable in (fake_python, fake_mv, fake_ln, fake_secret):
            executable.chmod(0o755)

        result = subprocess.run(
            ["/usr/bin/bash", str(PROJECT / "install.sh")],
            cwd=PROJECT,
            env={
                **os.environ,
                "XDG_DATA_HOME": str(root / "share"),
                "PATH": f"{fake_bin}:/usr/bin",
                "FAIL_MODE": failure,
            },
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((active / "bin" / "python").is_file())
        return active

    def test_link_creation_failure_preserves_active_symlink(self):
        with TemporaryDirectory() as directory:
            active = self.run_failed_install(Path(directory), "symlink", "link")
            self.assertTrue(active.is_symlink())
            self.assertEqual(active.readlink(), Path("previous-environment"))

    def test_final_symlink_move_failure_preserves_active_symlink(self):
        with TemporaryDirectory() as directory:
            active = self.run_failed_install(Path(directory), "symlink", "commit")
            self.assertTrue(active.is_symlink())
            self.assertEqual(active.readlink(), Path("previous-environment"))

    def test_final_symlink_move_failure_restores_legacy_environment(self):
        with TemporaryDirectory() as directory:
            active = self.run_failed_install(Path(directory), "legacy", "commit")
            self.assertTrue(active.is_dir())
            self.assertFalse(active.is_symlink())


class BrowserPackagingTests(unittest.TestCase):
    def test_rebuild_drops_stale_archive_members(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copy2(PROJECT / "scripts" / "build-extension.sh", root / "scripts" / "build-extension.sh")
            shutil.copytree(PROJECT / "browser-extension", root / "browser-extension")
            script = root / "scripts" / "build-extension.sh"
            subprocess.run([str(script)], check=True, cwd=root, capture_output=True, text=True)
            archive = root / "dist" / "omajd-remote-clicknload-chromium.zip"
            with zipfile.ZipFile(archive, "a") as package:
                package.writestr("stale-review-artifact.js", "unexpected")
            subprocess.run([str(script)], check=True, cwd=root, capture_output=True, text=True)
            with zipfile.ZipFile(archive) as package:
                self.assertNotIn("stale-review-artifact.js", package.namelist())
                self.assertEqual(set(package.namelist()), {
                    "content-script.js", "icons/", "icons/icon-128.png", "icons/icon-48.png",
                    "manifest.json", "page-bridge.js", "service-worker.js",
                })

    def test_build_is_reproducible_across_mtime_changes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copy2(PROJECT / "scripts" / "build-extension.sh", root / "scripts" / "build-extension.sh")
            shutil.copytree(PROJECT / "browser-extension", root / "browser-extension")
            script = root / "scripts" / "build-extension.sh"
            environment = {**os.environ, "SOURCE_DATE_EPOCH": "1700000000"}
            subprocess.run([str(script)], check=True, cwd=root, env=environment, capture_output=True, text=True)
            archive = root / "dist" / "omajd-remote-clicknload-chromium.zip"
            first = hashlib.sha256(archive.read_bytes()).hexdigest()
            os.utime(root / "browser-extension" / "page-bridge.js", (1900000000, 1900000000))
            subprocess.run([str(script)], check=True, cwd=root, env=environment, capture_output=True, text=True)
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), first)

    def test_parallel_builds_do_not_share_staging_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copy2(PROJECT / "scripts" / "build-extension.sh", root / "scripts" / "build-extension.sh")
            shutil.copytree(PROJECT / "browser-extension", root / "browser-extension")
            script = root / "scripts" / "build-extension.sh"
            processes = [subprocess.Popen([str(script)], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
            for process in processes:
                stdout, stderr = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, (stdout + stderr).decode())
            self.assertEqual(list((root / "dist").glob(".*.zip")), [])


class ReleaseMetadataTests(unittest.TestCase):
    def test_release_versions_and_changelog_match(self):
        self.assertEqual(verify_release.verify(), "0.5.0")

    def test_release_tag_must_match_version(self):
        self.assertEqual(verify_release.verify("v0.5.0"), "0.5.0")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            verify_release.verify("v9.9.9")

    def test_tagged_release_rejects_unreleased_entries(self):
        self.assertFalse(verify_release.has_unreleased_entries(
            "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n"
        ))
        self.assertTrue(verify_release.has_unreleased_entries(
            "# Changelog\n\n## [Unreleased]\n\n- pending fix\n\n## [1.0.0] - 2026-01-01\n"
        ))


if __name__ == "__main__":
    unittest.main()
