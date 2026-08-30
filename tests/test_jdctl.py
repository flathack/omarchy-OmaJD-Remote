import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "jdctl.py"
SPEC = importlib.util.spec_from_file_location("jdctl", MODULE_PATH)
jdctl = importlib.util.module_from_spec(SPEC)
sys.modules["jdctl"] = jdctl
SPEC.loader.exec_module(jdctl)


class FormattingTests(unittest.TestCase):
    def test_human_bytes(self):
        self.assertEqual(jdctl.human_bytes(0), "0 B")
        self.assertEqual(jdctl.human_bytes(1_500, "/s"), "1.5 KB/s")
        self.assertEqual(jdctl.human_bytes(2_000_000), "2.0 MB")

    def test_normalize_package(self):
        package = jdctl.normalize_package(
            {
                "uuid": 42,
                "name": "Linux ISO",
                "bytesLoaded": 250,
                "bytesTotal": 1000,
                "speed": 50,
                "running": True,
                "childCount": 3,
            },
            "download",
        )
        self.assertEqual(package["uuid"], "42")
        self.assertEqual(package["progress"], 25)
        self.assertEqual(package["speed_text"], "50 B/s")
        self.assertTrue(package["running"])

    def test_normalize_package_clamps_bad_values(self):
        package = jdctl.normalize_package(
            {"bytesLoaded": 200, "bytesTotal": 100, "speed": -1},
            "download",
        )
        self.assertEqual(package["progress"], 100)
        self.assertEqual(package["speed_text"], "0 B/s")


class CredentialTests(unittest.TestCase):
    @mock.patch("jdctl.subprocess.run")
    def test_secret_lookup_uses_stable_attributes(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="secret\n")
        self.assertEqual(jdctl.secret_lookup("me@example.com"), "secret")
        args = run.call_args.args[0]
        self.assertEqual(
            args,
            [
                "secret-tool",
                "lookup",
                "omarchy-plugin",
                "omajdownload",
                "account",
                "me@example.com",
            ],
        )

    @mock.patch("jdctl.subprocess.run")
    def test_secret_store_sends_password_over_stdin(self, run):
        run.return_value = mock.Mock(returncode=0, stderr="")
        jdctl.secret_store("me@example.com", "very-secret")
        self.assertNotIn("very-secret", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["input"], "very-secret")


if __name__ == "__main__":
    unittest.main()
