import importlib.util
import base64
import sys
import unittest
from pathlib import Path
from unittest import mock

from Crypto.Cipher import AES


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


class ClickNLoadTests(unittest.TestCase):
    def test_plain_payload(self):
        payload = jdctl.parse_cnl_payload(
            "/flash/add",
            {
                "urls": ["https://example.test/a\r\nhttps://example.test/b"],
                "passwords": ["secret"],
                "source": ["https://files.example.test/post/1"],
            },
        )
        self.assertEqual(payload["links"], ["https://example.test/a", "https://example.test/b"])
        self.assertEqual(payload["passwords"], ["secret"])
        self.assertFalse(payload["encrypted"])

    def test_encrypted_payload(self):
        key_hex = "00112233445566778899aabbccddeeff"
        key = bytes.fromhex(key_hex)
        clear = b"https://example.test/file"
        padding = AES.block_size - len(clear) % AES.block_size
        crypted = AES.new(key, AES.MODE_CBC, iv=key).encrypt(clear + bytes([padding]) * padding)
        payload = jdctl.parse_cnl_payload(
            "/flash/addcrypted2",
            {"jk": [f"function f() {{ return '{key_hex}'; }}"], "crypted": [base64.b64encode(crypted).decode()]},
        )
        self.assertEqual(payload["links"], ["https://example.test/file"])
        self.assertTrue(payload["encrypted"])

    def test_dynamic_javascript_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "dynamic or unsupported"):
            jdctl.decrypt_cnl2("function f() { return buildKey(); }", "AAAA")

    def test_source_label_hides_path(self):
        self.assertEqual(jdctl.source_label("https://files.example.test/private/post"), "files.example.test")


if __name__ == "__main__":
    unittest.main()
