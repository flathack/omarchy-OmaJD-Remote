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
verify_release = load_script("verify_release.py")
secure_install = load_script("secure_install.py")


class EnvironmentVerificationTests(unittest.TestCase):
    def test_current_environment_matches_lock(self):
        self.assertEqual(verify_environment.verify(PROJECT / "requirements.lock"), [])

    def test_version_drift_is_rejected_even_when_package_exists(self):
        expected = verify_environment.locked_versions(PROJECT / "requirements.lock")
        with mock.patch.object(verify_environment.importlib.metadata, "version", return_value="0.0-drifted"):
            errors = verify_environment.verify(PROJECT / "requirements.lock")
        self.assertEqual(len(errors), len(expected))
        self.assertTrue(all("expected" in error for error in errors))


class InstallerTransactionTests(unittest.TestCase):
    def test_shell_entrypoint_delegates_to_descriptor_safe_installer(self):
        source = (PROJECT / "install.sh").read_text(encoding="utf-8")
        helper = (PROJECT / "scripts" / "secure_install.py").read_text(encoding="utf-8")
        self.assertIn("scripts/secure_install.py", source)
        self.assertIn("O_NOFOLLOW", helper)
        self.assertIn("rename_exchange", helper)
        self.assertIn("OUTPUT_TOTAL_LIMIT", helper)
        self.assertIn("--development", (PROJECT / "scripts" / "setup-dev.sh").read_text(encoding="utf-8"))

    def test_data_root_symlink_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            link = root / "data-root"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(OSError):
                secure_install.open_directory(link, create=True, private=True)

    def run_install(self, data_root, active_kind):
        plugin_dir = data_root.parent / "plugin-source"
        plugin_dir.mkdir()
        shutil.copy2(PROJECT / "requirements.lock", plugin_dir / "requirements.lock")
        data_root.mkdir(mode=0o700)
        old_name = ".venv-" + "a" * 64 + ".installed.1-1-deadbeef"
        old = data_root / old_name
        old.mkdir()
        active = data_root / "venv"
        if active_kind == "symlink":
            active.symlink_to(old_name, target_is_directory=True)
        else:
            active.mkdir()
            (active / "bin").mkdir()
            (active / "bin" / "python").write_text("working", encoding="utf-8")
            old.rmdir()
        with mock.patch.object(secure_install.BoundedRunner, "run"):
            secure_install.install(plugin_dir, data_root)
        return active

    def test_atomic_publication_replaces_an_owned_symlink(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            active = self.run_install(root / "omajdownload", "symlink")
            self.assertTrue(active.is_symlink())
            self.assertRegex(active.readlink().name, secure_install.OWNED_ENVIRONMENT)

    def test_legacy_directory_is_atomically_exchanged(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "omajdownload"
            active = self.run_install(data_root, "legacy")
            self.assertTrue(active.is_symlink())
            legacy = list(data_root.glob("venv.legacy.*"))
            self.assertEqual(len(legacy), 1)
            self.assertEqual((legacy[0] / "bin" / "python").read_text(encoding="utf-8"), "working")

    def test_realpath_staging_resolves_to_private_directory_path(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            root_fd = secure_install.open_directory(root, create=True, private=True)
            try:
                staging_path = secure_install.open_realpath_staging(
                    root_fd, ".omajdownload-staging", mode=0o700,
                )
                try:
                    self.assertTrue(staging_path.startswith(str(root)))
                    details = os.stat(staging_path)
                    self.assertEqual(details.st_uid, os.getuid())
                    self.assertEqual(details.st_mode & 0o777, 0o700)
                    # The realpath helper must reject parent directories that
                    # are symlinks the way the rest of the installer does.
                    symlinked_root = root / "linked-parent"
                    symlinked_root.mkdir()
                    link = root / "link-only"
                    link.symlink_to(symlinked_root, target_is_directory=True)
                    with self.assertRaises(OSError):
                        secure_install.open_directory(link, create=False)
                finally:
                    secure_install.remove_realpath_staging(staging_path)
                    self.assertFalse(os.path.lexists(staging_path))
            finally:
                os.close(root_fd)

    def test_python_venv_succeeds_through_realpath_staging(self):
        """Regression test for issue #73.

        ``python -m venv`` boots a ``python -m ensurepip`` grand-child that
        does not inherit the parent process's directory descriptors, so an
        installer that addresses the staging tree exclusively through
        ``/proc/self/fd/<fd>/...`` cannot complete the bootstrap. We build
        the staging tree under the real filesystem path of the installer's
        private directory descriptor and rename it back into the descriptor
        namespace once venv has finished.
        """
        with TemporaryDirectory() as directory:
            root = Path(directory)
            staging_name = ".omajdownload-venv-test"
            # Demonstrate the original failure path first: addressing the
            # staging directory through the descriptor forces venv's
            # ``ensurepip`` grand-child to lose the descriptor, so the
            # bootstrap fails with ``No such file or directory``.
            descriptor_root = root / "descriptor"
            descriptor_root.mkdir()
            descriptor_fd = secure_install.open_directory(
                descriptor_root, create=True, private=True,
            )
            try:
                descriptor_target = f"/proc/self/fd/{descriptor_fd}/{staging_name}_descriptor"
                with mock.patch.object(secure_install, "open_realpath_staging",
                                       side_effect=AssertionError(
                                           "install() must use the descriptor path for the existing flow",
                                       )):
                    completed = subprocess.run(
                        [sys.executable, "-m", "venv", descriptor_target],
                        capture_output=True, text=True, pass_fds=(descriptor_fd,),
                    )
                self.assertNotEqual(
                    completed.returncode, 0,
                    msg=completed.stderr,
                )
                self.assertIn("No such file or directory", completed.stderr)
            finally:
                os.close(descriptor_fd)

            # Now show that creating the venv on the realpath succeeds and
            # the resulting tree can be moved back under the descriptor.
            root_fd = secure_install.open_directory(root, create=True, private=True)
            try:
                staging_real = secure_install.open_realpath_staging(
                    root_fd, staging_name, mode=0o700,
                )
                try:
                    completed = subprocess.run(
                        [sys.executable, "-m", "venv", staging_real],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(
                        completed.returncode, 0,
                        msg=completed.stderr,
                    )
                    os.rename(
                        staging_real, staging_name,
                        src_dir_fd=None, dst_dir_fd=root_fd,
                    )
                    staging_real = ""
                    inside = os.stat(
                        f"{staging_name}/bin/python", dir_fd=root_fd, follow_symlinks=False,
                    )
                    self.assertEqual(inside.st_uid, os.getuid())
                finally:
                    if staging_real:
                        secure_install.remove_realpath_staging(staging_real)
                try:
                    secure_install.remove_tree(root_fd, staging_name)
                except FileNotFoundError:
                    pass
            finally:
                os.close(root_fd)

    def test_setup_development_completes_via_realpath_staging(self):
        """End-to-end check that ``setup_development`` no longer hits the
        ``No such file or directory`` failure when venv addresses a staging
        tree exclusively through ``/proc/self/fd/<fd>/...``.

        We exercise only the descriptor-safe portion of the helper so the
        test does not need network access: venv is created, then renamed
        back under the descriptor, just like the production code path does
        between the ``pip install`` and the ``check.sh`` runs.
        """
        with TemporaryDirectory() as directory:
            root = Path(directory)
            environment_path = root / ".omajdownload-dev"
            plugin_dir = root / "plugin"
            plugin_dir.mkdir()
            shutil.copy2(PROJECT / "requirements.lock", plugin_dir / "requirements.lock")

            parent_fd = secure_install.open_directory(environment_path.parent, create=True)
            try:
                staging_real = secure_install.open_realpath_staging(
                    parent_fd, environment_path.name, mode=0o700,
                )
                try:
                    completed = subprocess.run(
                        [sys.executable, "-m", "venv", staging_real],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(
                        completed.returncode, 0,
                        msg=completed.stderr,
                    )
                    os.rename(
                        staging_real, environment_path.name,
                        src_dir_fd=None, dst_dir_fd=parent_fd,
                    )
                    staging_real = ""
                    environment_fd = os.open(
                        environment_path.name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=parent_fd,
                    )
                    try:
                        os.fsync(environment_fd)
                    finally:
                        os.close(environment_fd)
                finally:
                    if staging_real:
                        secure_install.remove_realpath_staging(staging_real)
            finally:
                try:
                    secure_install.remove_tree(parent_fd, environment_path.name)
                except FileNotFoundError:
                    pass
                os.close(parent_fd)


class WorkflowPinTests(unittest.TestCase):
    def test_ci_and_release_build_inputs_are_pinned(self):
        workflows = "\n".join(
            (PROJECT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            for name in ("ci.yml", "release.yml")
        )
        self.assertNotIn("ubuntu-latest", workflows)
        self.assertEqual(workflows.count("runs-on: ubuntu-24.04"), 6)
        self.assertEqual(workflows.count("container: archlinux:base@sha256:"), 6)
        self.assertNotIn("actions/checkout@v", workflows)
        self.assertNotIn("actions/setup-python@", workflows)
        self.assertNotIn("archlinux:base\n", workflows)
        self.assertNotIn("--branch quattro", workflows)
        self.assertIn("archive.archlinux.org/repos/2026/09/01", workflows)
        self.assertIn("d3d23fdddef846ebb98b52122a6ece66211c0daf", workflows)
        self.assertIn("tests/requirements.lock", workflows)
        reproducibility = (PROJECT / "docs" / "reproducible-builds.md").read_text(encoding="utf-8")
        self.assertIn("docs.github.com/en/actions/concepts/runners/github-hosted-runners", reproducibility)


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
        self.assertEqual(verify_release.verify(), "0.6.2")

    def test_release_tag_must_match_version(self):
        self.assertEqual(verify_release.verify("v0.6.2"), "0.6.2")
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
