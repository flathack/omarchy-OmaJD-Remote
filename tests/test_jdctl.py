import importlib.util
import base64
import concurrent.futures
import json
import os
import queue
import select
import subprocess
import sys
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import mock
from urllib import error, parse, request

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

    @mock.patch("jdctl.subprocess.run")
    def test_secret_clear_reports_keyring_failure(self, run):
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="keyring is locked\n")
        with self.assertRaisesRegex(RuntimeError, "keyring is locked"):
            jdctl.secret_clear("me@example.com")


class PaginationTests(unittest.TestCase):
    def test_reads_every_package_page(self):
        packages = [{"uuid": index} for index in range(137)]
        calls = []

        def query(arguments):
            params = arguments[0]
            calls.append((params["startAt"], params["maxResults"]))
            start = params["startAt"]
            return packages[start:start + params["maxResults"]]

        self.assertEqual(jdctl.query_all_packages(query, {"name": True}), packages)
        self.assertEqual(calls, [(0, 60), (60, 60), (120, 60)])

    def test_repeated_page_is_rejected(self):
        page = [{"uuid": index} for index in range(jdctl.PACKAGE_PAGE_SIZE)]
        with self.assertRaisesRegex(RuntimeError, "repeated a page"):
            jdctl.query_all_packages(lambda _arguments: page, {})


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

    def test_http_success_means_payload_is_already_persisted(self):
        admitted = []
        events = queue.Queue()
        server = jdctl.ClickNLoadServer(admitted.append, events, port=0)
        server.start()
        self.addCleanup(server.stop)
        body = parse.urlencode({"urls": "https://example.test/file"}).encode()
        response = request.urlopen(
            request.Request(f"http://127.0.0.1:{server.port}/flash/add", data=body),
            timeout=3,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(admitted[0]["links"], ["https://example.test/file"])

    def test_http_reports_persistence_failure(self):
        def fail(_payload):
            raise OSError("disk full")

        server = jdctl.ClickNLoadServer(fail, queue.Queue(), port=0)
        server.start()
        self.addCleanup(server.stop)
        body = parse.urlencode({"urls": "https://example.test/file"}).encode()
        with self.assertRaises(error.HTTPError) as raised:
            request.urlopen(
                request.Request(f"http://127.0.0.1:{server.port}/flash/add", data=body),
                timeout=3,
            )
        self.assertEqual(raised.exception.code, 503)
        raised.exception.close()

    def test_http_preflight_and_probe_endpoints(self):
        server = jdctl.ClickNLoadServer(lambda _payload: None, queue.Queue(), port=0)
        server.start()
        self.addCleanup(server.stop)
        base = f"http://127.0.0.1:{server.port}"
        with request.urlopen(request.Request(f"{base}/flash", method="OPTIONS"), timeout=3) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers["Access-Control-Allow-Private-Network"], "true")
            self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])
        with request.urlopen(f"{base}/flash", timeout=3) as response:
            self.assertEqual(response.read(), b"JDownloader\r\n")
        with request.urlopen(f"{base}/jdcheck.js", timeout=3) as response:
            self.assertEqual(response.read(), b"jdownloader=true;")

    def test_http_size_limit_and_capacity_response(self):
        server = jdctl.ClickNLoadServer(
            lambda _payload: (_ for _ in ()).throw(OverflowError("inbox full")),
            queue.Queue(),
            port=0,
        )
        server.start()
        self.addCleanup(server.stop)
        endpoint = f"http://127.0.0.1:{server.port}/flash/add"
        oversized = request.Request(endpoint, data=b"x" * (jdctl.CNL_BODY_LIMIT + 1))
        with self.assertRaises(error.HTTPError) as raised:
            request.urlopen(oversized, timeout=3)
        self.assertEqual(raised.exception.code, 413)
        raised.exception.close()
        body = parse.urlencode({"urls": "https://example.test/file"}).encode()
        with self.assertRaises(error.HTTPError) as raised:
            request.urlopen(request.Request(endpoint, data=body), timeout=3)
        self.assertEqual(raised.exception.code, 429)
        raised.exception.close()

    def test_concurrent_http_admissions_are_all_acknowledged(self):
        admitted = []
        lock = jdctl.threading.Lock()

        def admit(payload):
            with lock:
                admitted.append(payload)

        server = jdctl.ClickNLoadServer(admit, queue.Queue(), port=0)
        server.start()
        self.addCleanup(server.stop)

        def send(index):
            body = parse.urlencode({"urls": f"https://example.test/{index}"}).encode()
            with request.urlopen(
                request.Request(f"http://127.0.0.1:{server.port}/flash/add", data=body),
                timeout=3,
            ) as response:
                return response.status

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            statuses = list(executor.map(send, range(16)))
        self.assertEqual(statuses, [200] * 16)
        self.assertEqual(len(admitted), 16)


class FakeDevice:
    def __init__(self, device_id="device-1"):
        self.device_id = device_id
        self.disable_direct_connection = mock.Mock()
        self.downloadcontroller = mock.Mock()
        self.downloadcontroller.get_current_state.return_value = "IDLE"
        self.downloadcontroller.get_speed_in_bytes.return_value = 0
        self.downloads = mock.Mock()
        self.downloads.query_packages.return_value = []
        self.linkgrabber = mock.Mock()
        self.linkgrabber.query_packages.return_value = []


class FakeApi:
    def __init__(self, devices=None):
        self.rows = list(devices or [])
        self.device_objects = {row["id"]: FakeDevice(row["id"]) for row in self.rows}
        self.update_devices = mock.Mock()
        self.set_app_key = mock.Mock()
        self.connect = mock.Mock()

    def list_devices(self):
        return self.rows

    def get_device(self, device_id):
        return self.device_objects[device_id]

    def is_connected(self):
        return True

    def disconnect(self):
        return None


class BridgeTests(unittest.TestCase):
    def make_bridge(self):
        with mock.patch("jdctl.read_config", return_value={}), mock.patch("jdctl.read_inbox", return_value=[]):
            return jdctl.Bridge(start_cnl=False)

    def test_device_refresh_preserves_offline_preference(self):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com", "selected_device_id": "preferred-offline"}
        bridge.jd = FakeApi([{"id": "fallback", "name": "Server", "type": "jd"}])
        bridge.refresh_devices()
        self.assertEqual(bridge.active_device_id, "fallback")
        self.assertEqual(bridge.selected_id, "preferred-offline")
        bridge.jd.rows = [{"id": "preferred-offline", "name": "Preferred", "type": "jd"}]
        bridge.jd.device_objects["preferred-offline"] = FakeDevice("preferred-offline")
        bridge.refresh_devices()
        self.assertEqual(bridge.active_device_id, "preferred-offline")

    @mock.patch("jdctl.emit")
    def test_configured_account_without_devices_stays_configured(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com"}
        bridge.jd = FakeApi([])
        bridge.last_device_refresh = jdctl.time.monotonic()
        bridge.snapshot()
        snapshot = emit.call_args.args[0]
        self.assertTrue(snapshot["configured"])
        self.assertFalse(snapshot["connected"])
        self.assertEqual(snapshot["selected_device_name"], "No online instance")

    @mock.patch("jdctl.emit")
    def test_snapshot_does_not_poll_keyring_after_connection(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com"}
        bridge.jd = FakeApi([{"id": "device-1", "name": "Server", "type": "jd"}])
        bridge.device = bridge.jd.device_objects["device-1"]
        bridge.devices = bridge.jd.rows
        bridge.active_device_id = "device-1"
        bridge.last_device_refresh = jdctl.time.monotonic()
        with mock.patch("jdctl.secret_lookup") as lookup:
            bridge.snapshot()
        lookup.assert_not_called()
        self.assertTrue(emit.call_args.args[0]["connected"])

    def test_each_connection_attempt_performs_only_one_keyring_lookup(self):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com"}
        api = FakeApi([])
        api.set_app_key = mock.Mock()
        api.connect = mock.Mock()
        with mock.patch("jdctl.secret_lookup", return_value="secret") as lookup, mock.patch("jdctl.myjdapi.Myjdapi", return_value=api):
            bridge.connect()
            bridge.disconnect()
            bridge.connect()
        self.assertEqual(lookup.call_args_list, [mock.call("me@example.com"), mock.call("me@example.com")])
        self.assertEqual(api.connect.call_count, 2)

    @mock.patch("jdctl.emit")
    def test_add_links_echoes_request_id_and_command(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com"}
        bridge.jd = FakeApi([{"id": "device-1", "name": "Server", "type": "jd"}])
        bridge.device = bridge.jd.device_objects["device-1"]
        with mock.patch.object(bridge, "snapshot"), mock.patch("jdctl.time.sleep"):
            bridge.handle({
                "command": "add_links",
                "request_id": "links-17",
                "links": "https://example.test/file",
            })
        action = next(call.args[0] for call in emit.call_args_list if call.args[0].get("type") == "action")
        self.assertTrue(action["ok"])
        self.assertEqual(action["command"], "add_links")
        self.assertEqual(action["request_id"], "links-17")

    def test_cnl_admission_is_durable_and_bounded(self):
        bridge = self.make_bridge()
        stored = []
        with mock.patch("jdctl.write_inbox", side_effect=lambda rows: stored.append(list(rows))):
            bridge.admit_cnl({"links": ["https://example.test/file"]})
        self.assertEqual(stored[-1], bridge.inbox)
        self.assertEqual(len(bridge.inbox), 1)

        bridge.inbox = [{"id": str(index)} for index in range(jdctl.CNL_INBOX_LIMIT)]
        with self.assertRaises(OverflowError):
            bridge.admit_cnl({"links": ["https://example.test/overflow"]})

    def test_cnl_inbox_survives_bridge_restart(self):
        with TemporaryDirectory() as directory:
            config_dir = Path(directory)
            with mock.patch("jdctl.CONFIG_DIR", config_dir), \
                    mock.patch("jdctl.CONFIG_FILE", config_dir / "config.json"), \
                    mock.patch("jdctl.INBOX_FILE", config_dir / "inbox.json"):
                first = jdctl.Bridge(start_cnl=False)
                first.admit_cnl({"links": ["https://example.test/persisted"], "source": "https://example.test"})
                second = jdctl.Bridge(start_cnl=False)
            self.assertEqual(second.inbox[0]["links"], ["https://example.test/persisted"])

    @mock.patch("jdctl.emit")
    def test_forget_reports_config_removal_failure(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com"}
        with mock.patch("jdctl.secret_clear"), mock.patch.object(Path, "unlink", side_effect=OSError("read-only")), mock.patch.object(bridge, "snapshot"):
            bridge.handle({"command": "forget"})
        action = emit.call_args.args[0]
        self.assertFalse(action["ok"])
        self.assertIn("read-only", action["message"])

    @mock.patch("jdctl.emit")
    def test_cnl_reject_reports_persistence_failure_without_mutating_inbox(self, emit):
        bridge = self.make_bridge()
        bridge.inbox = [{"id": "request-1", "links": ["https://example.test/file"]}]
        with mock.patch("jdctl.write_inbox", side_effect=OSError("read-only")):
            bridge.handle({"command": "cnl_reject", "id": "request-1"})
        action = emit.call_args.args[0]
        self.assertFalse(action["ok"])
        self.assertIn("read-only", action["message"])
        self.assertEqual([item["id"] for item in bridge.inbox], ["request-1"])

    @mock.patch("jdctl.emit")
    def test_every_remote_command_calls_the_expected_api(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com"}
        bridge.jd = FakeApi([
            {"id": "device-1", "name": "One", "type": "jd"},
            {"id": "device-2", "name": "Two", "type": "jd"},
        ])
        bridge.devices = bridge.jd.rows
        bridge.device = bridge.jd.device_objects["device-1"]
        bridge.active_device_id = "device-1"
        bridge.inbox = [{"id": "cnl-1", "links": ["https://example.test/cnl"], "passwords": ["pw"]}]
        device = bridge.device

        commands = [
            ({"command": "control", "action": "start"}, device.downloadcontroller.start_downloads),
            ({"command": "control", "action": "stop"}, device.downloadcontroller.stop_downloads),
            ({"command": "control", "action": "pause"}, device.downloadcontroller.pause_downloads),
            ({"command": "control", "action": "resume"}, device.downloadcontroller.pause_downloads),
            ({"command": "force_download", "package_ids": ["42"]}, device.downloads.force_download),
            ({"command": "move_grabber", "package_ids": ["42"]}, device.linkgrabber.move_to_downloadlist),
            ({"command": "rename_grabber", "package_id": "42", "name": "  Clear release name  "}, device.linkgrabber.rename_package),
            ({"command": "remove_downloads", "package_ids": ["42"]}, device.downloads.remove_links),
            ({"command": "remove_grabber", "package_ids": ["42"]}, device.linkgrabber.remove_links),
        ]
        with mock.patch.object(bridge, "snapshot"), mock.patch("jdctl.time.sleep"):
            for command, method in commands:
                bridge.handle(command)
                method.assert_called()

        device.linkgrabber.rename_package.assert_called_once_with("42", "Clear release name")

        with mock.patch("jdctl.write_config"), mock.patch.object(bridge, "snapshot"), mock.patch("jdctl.time.sleep"):
            bridge.handle({"command": "select_device", "device_id": "device-2"})
        self.assertEqual(bridge.active_device_id, "device-2")

        bridge.device = device
        with mock.patch("jdctl.write_inbox"), mock.patch.object(bridge, "snapshot"), mock.patch("jdctl.time.sleep"):
            bridge.handle({"command": "cnl_accept", "id": "cnl-1", "autostart": False})
        device.linkgrabber.add_links.assert_called()
        self.assertEqual(bridge.inbox, [])
        self.assertTrue(all(call.args[0]["ok"] for call in emit.call_args_list if call.args[0].get("type") == "action"))

    @mock.patch("jdctl.emit")
    def test_rename_grabber_rejects_an_empty_name(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com"}
        bridge.jd = FakeApi([{"id": "device-1", "name": "Server", "type": "jd"}])
        bridge.device = bridge.jd.device_objects["device-1"]
        with mock.patch.object(bridge, "snapshot"), mock.patch("jdctl.time.sleep"):
            bridge.handle({"command": "rename_grabber", "package_id": "42", "name": "   "})
        bridge.device.linkgrabber.rename_package.assert_not_called()
        self.assertFalse(emit.call_args.args[0]["ok"])
        self.assertIn("required", emit.call_args.args[0]["message"])

    @mock.patch("jdctl.emit")
    def test_configure_and_idempotent_forget_persist_state(self, emit):
        bridge = self.make_bridge()
        api = FakeApi([])
        with mock.patch("jdctl.myjdapi.Myjdapi", return_value=api), \
                mock.patch("jdctl.secret_store") as store, \
                mock.patch("jdctl.secret_clear") as clear, \
                mock.patch("jdctl.write_config") as write, \
                mock.patch.object(bridge, "snapshot"):
            bridge.handle({"command": "configure", "email": "me@example.com", "password": "secret"})
            store.assert_called_once_with("me@example.com", "secret")
            write.assert_called_once()
            self.assertEqual(bridge.email, "me@example.com")
            with mock.patch.object(Path, "unlink") as unlink:
                bridge.handle({"command": "forget"})
                bridge.handle({"command": "forget"})
            self.assertEqual(unlink.call_count, 2)
            self.assertEqual(bridge.config, {})
            self.assertGreaterEqual(clear.call_count, 2)


class ProcessProtocolTests(unittest.TestCase):
    def test_daemon_json_lines_lifecycle(self):
        with TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["XDG_CONFIG_HOME"] = directory
            environment["OMAJDOWNLOAD_CNL_PORT"] = "0"
            process = subprocess.Popen(
                [sys.executable, str(MODULE_PATH), "daemon"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            output_buffer = b""

            def read_message():
                nonlocal output_buffer
                while b"\n" not in output_buffer:
                    readable, _, _ = select.select([process.stdout], [], [], 4)
                    self.assertTrue(readable, "helper output timeout")
                    output_buffer += os.read(process.stdout.fileno(), 65536)
                line, output_buffer = output_buffer.split(b"\n", 1)
                return json.loads(line)

            try:
                self.assertEqual(read_message()["type"], "snapshot")
                self.assertEqual(read_message()["type"], "cnl")
                process.stdin.write(b"not-json\n")
                process.stdin.flush()
                invalid = read_message()
                self.assertEqual(invalid["type"], "action")
                self.assertFalse(invalid["ok"])
                process.stdin.write(b'{"command":"refresh"}\n')
                process.stdin.flush()
                self.assertEqual(read_message()["type"], "snapshot")
            finally:
                process.terminate()
                process.wait(timeout=4)
                process.stdin.close()
                process.stdout.close()
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
