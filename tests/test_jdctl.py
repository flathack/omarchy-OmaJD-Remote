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
from http import client
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
        run.return_value = mock.Mock(returncode=0, stdout="secret\n", stderr="")
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
    def test_secret_lookup_distinguishes_missing_from_keyring_failure(self, run):
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="")
        self.assertIsNone(jdctl.secret_lookup("missing@example.com"))
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="keyring locked")
        with self.assertRaisesRegex(RuntimeError, "keyring locked"):
            jdctl.secret_lookup("me@example.com")

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


class StateWriterTests(unittest.TestCase):
    def test_atomic_writer_is_private_under_permissive_umask_and_syncs(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            previous = os.umask(0o022)
            try:
                with mock.patch("jdctl.os.fsync", wraps=os.fsync) as fsync:
                    jdctl.atomic_write_json(path, {"url": "https://private.example/token"})
            finally:
                os.umask(previous)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(fsync.call_count, 2)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["url"], "https://private.example/token")
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_atomic_writer_does_not_replace_state_when_file_sync_fails(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"old": true}\n', encoding="utf-8")
            with mock.patch("jdctl.os.fsync", side_effect=OSError("sync failed")):
                with self.assertRaisesRegex(OSError, "sync failed"):
                    jdctl.atomic_write_json(path, {"new": True})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"old": True})
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_atomic_writer_marks_directory_sync_failure_as_committed(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with mock.patch("jdctl.os.fsync", side_effect=[None, OSError("directory sync failed")]):
                with self.assertRaises(jdctl.StateCommitError) as raised:
                    jdctl.atomic_write_json(path, {"new": True})
            self.assertTrue(raised.exception.committed)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"new": True})


class PaginationTests(unittest.TestCase):
    def test_reads_every_package_page(self):
        packages = [{"uuid": index} for index in range(137)]
        calls = []

        def query(arguments):
            params = arguments[0]
            calls.append((params["startAt"], params["maxResults"]))
            start = params["startAt"]
            return packages[start:start + params["maxResults"]]

        result = jdctl.query_all_packages(query, {"name": True})
        self.assertEqual(result.rows, packages)
        self.assertFalse(result.truncated)
        self.assertEqual(calls, [(0, 60), (60, 60), (120, 60)])

    def test_repeated_page_is_rejected(self):
        page = [{"uuid": index} for index in range(jdctl.PACKAGE_PAGE_SIZE)]
        with self.assertRaisesRegex(RuntimeError, "repeated a page"):
            jdctl.query_all_packages(lambda _arguments: page, {})

    def test_large_package_list_is_returned_as_truncated_not_an_error(self):
        packages = [{"uuid": index} for index in range(121)]

        def query(arguments):
            params = arguments[0]
            start = params["startAt"]
            return packages[start:start + params["maxResults"]]

        with mock.patch("jdctl.PACKAGE_MODEL_LIMIT", 120):
            result = jdctl.query_all_packages(query, {})
        self.assertEqual(len(result.rows), 120)
        self.assertTrue(result.truncated)

    def test_real_display_ceiling_and_one_additional_row(self):
        for total, truncated in ((6000, False), (6001, True)):
            with self.subTest(total=total):
                packages = [{"uuid": index} for index in range(total)]

                def query(arguments):
                    params = arguments[0]
                    start = params["startAt"]
                    return packages[start:start + params["maxResults"]]

                result = jdctl.query_all_packages(query, {})
                self.assertEqual(len(result.rows), 6000)
                self.assertEqual(result.truncated, truncated)


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

    def test_http_records_browser_origin_and_requires_token_for_extension_origin(self):
        admitted = []
        server = jdctl.ClickNLoadServer(admitted.append, queue.Queue(), port=0)
        server.start()
        self.addCleanup(server.stop)
        base = f"http://127.0.0.1:{server.port}"
        body = parse.urlencode({"urls": "https://files.example/file", "source": "https://claimed.example/post"}).encode()
        request.urlopen(request.Request(
            f"{base}/flash/add", data=body, headers={"Origin": "https://actual.example"}
        ), timeout=3).close()
        self.assertEqual(admitted[-1]["origin"], "https://actual.example")
        self.assertTrue(admitted[-1]["origin_verified"])
        self.assertEqual(admitted[-1]["claimed_source"], "https://claimed.example/post")

        request.urlopen(request.Request(
            f"{base}/flash/add", data=body,
            headers={"X-OmaJDownLoad-Origin": "https://spoofed.example"},
        ), timeout=3).close()
        self.assertEqual(admitted[-1]["origin"], "")
        self.assertFalse(admitted[-1]["origin_verified"])

        with request.urlopen(f"{base}/omajdownload/extension-token", timeout=3) as response:
            token = response.read().decode()
            self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
        request.urlopen(request.Request(
            f"{base}/flash/add", data=body,
            headers={
                "X-OmaJDownLoad-Origin": "https://extension-source.example/page",
                "X-OmaJDownLoad-Token": token,
            },
        ), timeout=3).close()
        self.assertEqual(admitted[-1]["origin"], "https://extension-source.example")
        self.assertTrue(admitted[-1]["origin_verified"])

    def test_http_size_limit_and_capacity_response(self):
        server = jdctl.ClickNLoadServer(
            lambda _payload: (_ for _ in ()).throw(OverflowError("inbox full")),
            queue.Queue(),
            port=0,
        )
        server.start()
        self.addCleanup(server.stop)
        endpoint = f"http://127.0.0.1:{server.port}/flash/add"
        connection = client.HTTPConnection("127.0.0.1", server.port, timeout=3)
        connection.putrequest("POST", "/flash/add")
        connection.putheader("Content-Length", str(jdctl.CNL_BODY_LIMIT + 1))
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        response.close()
        connection.close()
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

    @mock.patch("jdctl.emit")
    def test_manual_add_links_uncertainty_requires_explicit_retry(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com"}
        bridge.jd = FakeApi([{"id": "device-1", "name": "Server", "type": "jd"}])
        bridge.device = bridge.jd.device_objects["device-1"]
        bridge.device.linkgrabber.add_links.side_effect = [TimeoutError("response lost"), None]
        request_data = {
            "command": "add_links", "request_id": "links-1",
            "links": "https://example.test/file", "autostart": False,
        }
        with mock.patch.object(bridge, "snapshot"), mock.patch("jdctl.time.sleep"):
            bridge.handle(dict(request_data))
            uncertain = emit.call_args.args[0]
            self.assertFalse(uncertain["ok"])
            self.assertTrue(uncertain["uncertain"])
            bridge.handle(dict(request_data, request_id="links-2"))
            self.assertEqual(bridge.device.linkgrabber.add_links.call_count, 1)
            bridge.handle({
                **request_data,
                "command": "retry_add_links",
                "request_id": "links-3",
                "retry_token": uncertain["retry_token"],
                "duplicate_confirmed": True,
            })
        self.assertEqual(bridge.device.linkgrabber.add_links.call_count, 2)
        self.assertTrue(emit.call_args.args[0]["ok"])

    @mock.patch("jdctl.emit")
    def test_device_selection_commit_failure_preserves_live_device(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com", "selected_device_id": "device-1"}
        bridge.jd = FakeApi([
            {"id": "device-1", "name": "One", "type": "jd"},
            {"id": "device-2", "name": "Two", "type": "jd"},
        ])
        bridge.devices = bridge.jd.rows
        bridge.device = bridge.jd.device_objects["device-1"]
        bridge.active_device_id = "device-1"
        with mock.patch.object(bridge, "persist_config", side_effect=OSError("read-only")):
            bridge.handle({"command": "select_device", "device_id": "device-2"})
        self.assertFalse(emit.call_args.args[0]["ok"])
        self.assertEqual(bridge.active_device_id, "device-1")
        self.assertIs(bridge.device, bridge.jd.device_objects["device-1"])
        self.assertEqual(bridge.selected_id, "device-1")

    @mock.patch("jdctl.emit")
    def test_package_refresh_failures_are_section_local_and_polling_reuses_cache(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com"}
        bridge.jd = FakeApi([{"id": "device-1", "name": "Server", "type": "jd"}])
        bridge.device = bridge.jd.device_objects["device-1"]
        bridge.devices = bridge.jd.rows
        bridge.active_device_id = "device-1"
        bridge.last_device_refresh = jdctl.time.monotonic()
        bridge.device.downloads.query_packages.side_effect = RuntimeError("downloads unavailable")
        bridge.device.linkgrabber.query_packages.return_value = [{"uuid": "g1", "name": "Kept"}]
        bridge.snapshot(refresh=True)
        snapshot = emit.call_args.args[0]
        self.assertTrue(snapshot["connected"])
        self.assertIn("downloads unavailable", snapshot["download_error"])
        self.assertEqual(snapshot["grabber"][0]["uuid"], "g1")
        calls = bridge.device.linkgrabber.query_packages.call_count
        bridge.snapshot(refresh=False)
        self.assertEqual(bridge.device.linkgrabber.query_packages.call_count, calls)

    @mock.patch("jdctl.emit")
    def test_large_cached_package_lists_do_not_scale_five_second_polling(self, _emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com"}
        bridge.jd = FakeApi([{"id": "device-1", "name": "Server", "type": "jd"}])
        bridge.device = bridge.jd.device_objects["device-1"]
        bridge.devices = bridge.jd.rows
        bridge.active_device_id = "device-1"
        bridge.last_device_refresh = jdctl.time.monotonic()
        packages = [{"uuid": str(index), "name": f"Package {index}"} for index in range(1001)]

        def page(arguments):
            params = arguments[0]
            start = params["startAt"]
            return packages[start:start + params["maxResults"]]

        bridge.device.downloads.query_packages.side_effect = page
        bridge.device.linkgrabber.query_packages.side_effect = page
        bridge.snapshot(refresh=True)
        initial_download_calls = bridge.device.downloads.query_packages.call_count
        initial_grabber_calls = bridge.device.linkgrabber.query_packages.call_count
        self.assertGreater(initial_download_calls, 1)
        self.assertGreater(initial_grabber_calls, 1)

        bridge.snapshot(refresh=False)
        self.assertEqual(bridge.device.downloads.query_packages.call_count, initial_download_calls)
        self.assertEqual(bridge.device.linkgrabber.query_packages.call_count, initial_grabber_calls)
        self.assertEqual(bridge.device.downloadcontroller.get_current_state.call_count, 2)

        bridge.snapshot(refresh=True)
        self.assertGreater(bridge.device.downloads.query_packages.call_count, initial_download_calls)
        self.assertGreater(bridge.device.linkgrabber.query_packages.call_count, initial_grabber_calls)

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

    def test_cnl_admission_enforces_link_and_aggregate_byte_limits(self):
        bridge = self.make_bridge()
        with mock.patch("jdctl.CNL_REQUEST_LINK_LIMIT", 2):
            with self.assertRaisesRegex(OverflowError, "more than 2 links"):
                bridge.admit_cnl({"links": ["https://e.test/1", "https://e.test/2", "https://e.test/3"]})
        bridge.inbox = [{"id": "old", "links": ["https://e.test/" + "x" * 100]}]
        with mock.patch("jdctl.CNL_INBOX_BYTE_LIMIT", 150):
            with self.assertRaisesRegex(OverflowError, "inbox exceeds"):
                bridge.admit_cnl({"links": ["https://e.test/new"]})

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

    def test_cnl_review_separates_verified_origin_claim_and_private_url_details(self):
        bridge = self.make_bridge()
        bridge.inbox = [{
            "id": "review-1",
            "links": ["https://cdn.example/private/file?token=secret"],
            "claimed_source": "https://trusted.example/misleading/path",
            "origin": "https://actual.example",
            "origin_verified": True,
        }]
        view = bridge.inbox_view()[0]
        self.assertEqual(view["origin"], "https://actual.example")
        self.assertTrue(view["origin_verified"])
        self.assertEqual(view["source"], "trusted.example")
        self.assertEqual(view["link_hosts"], ["cdn.example"])
        self.assertNotIn("secret", " ".join(view["link_hosts"]))
        self.assertNotIn("link_urls", view)
        with mock.patch("jdctl.emit") as emit:
            bridge.handle({"command": "cnl_details", "id": "review-1"})
        self.assertEqual(emit.call_args.args[0]["link_urls"], ["https://cdn.example/private/file?token=secret"])

    def test_corrupt_inbox_is_quarantined_before_new_admission(self):
        with TemporaryDirectory() as directory:
            config_dir = Path(directory)
            inbox_file = config_dir / "inbox.json"
            inbox_file.write_text("not-json", encoding="utf-8")
            with mock.patch("jdctl.CONFIG_DIR", config_dir), \
                    mock.patch("jdctl.CONFIG_FILE", config_dir / "config.json"), \
                    mock.patch("jdctl.INBOX_FILE", inbox_file):
                bridge = jdctl.Bridge(start_cnl=False)
                self.assertIn("preserved as", bridge.state_warning)
                backups = list(config_dir.glob("inbox.json.corrupt-*"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_text(encoding="utf-8"), "not-json")
                bridge.admit_cnl({"links": ["https://new.example/file"]})
                self.assertEqual(jdctl.read_inbox()[0]["links"], ["https://new.example/file"])

    def test_corrupt_config_is_quarantined_before_reconfiguration(self):
        with TemporaryDirectory() as directory:
            config_dir = Path(directory)
            config_file = config_dir / "config.json"
            config_file.write_text("[not-an-object]", encoding="utf-8")
            with mock.patch("jdctl.CONFIG_DIR", config_dir), \
                    mock.patch("jdctl.CONFIG_FILE", config_file), \
                    mock.patch("jdctl.INBOX_FILE", config_dir / "inbox.json"):
                bridge = jdctl.Bridge(start_cnl=False)
                self.assertEqual(bridge.config, {})
                self.assertIn("preserved as", bridge.state_warning)
                backups = list(config_dir.glob("config.json.corrupt-*"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_text(encoding="utf-8"), "[not-an-object]")
                bridge.persist_config({"email": "new@example.test"})
                self.assertEqual(jdctl.read_config()["email"], "new@example.test")

    def test_wrong_inbox_type_is_preserved_and_failed_quarantine_blocks_writes(self):
        with TemporaryDirectory() as directory:
            config_dir = Path(directory)
            inbox_file = config_dir / "inbox.json"
            inbox_file.write_text('{"unexpected": "object"}', encoding="utf-8")
            with mock.patch("jdctl.CONFIG_DIR", config_dir), \
                    mock.patch("jdctl.CONFIG_FILE", config_dir / "config.json"), \
                    mock.patch("jdctl.INBOX_FILE", inbox_file), \
                    mock.patch("jdctl.quarantine_state_file", side_effect=PermissionError("permission denied")):
                bridge = jdctl.Bridge(start_cnl=False)
                self.assertTrue(bridge.inbox_write_blocked)
                self.assertIn("could not preserve it", bridge.state_warning)
                with self.assertRaisesRegex(OSError, "locked to preserve"):
                    bridge.admit_cnl({"links": ["https://new.example/file"]})
                self.assertEqual(inbox_file.read_text(encoding="utf-8"), '{"unexpected": "object"}')

    @mock.patch("jdctl.emit")
    def test_cnl_uncertain_state_requires_explicit_retry(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com"}
        bridge.jd = FakeApi([{"id": "device-1", "name": "Server", "type": "jd"}])
        bridge.device = bridge.jd.device_objects["device-1"]
        bridge.inbox = [{"id": "cnl-1", "links": ["https://example.test/cnl"], "status": jdctl.CNL_PENDING}]
        with mock.patch.object(bridge, "persist_inbox", side_effect=[None, OSError("disk full"), None]), \
                mock.patch.object(bridge, "snapshot"), mock.patch("jdctl.time.sleep"):
            bridge.handle({"command": "cnl_accept", "id": "cnl-1", "autostart": False})
        self.assertEqual(bridge.device.linkgrabber.add_links.call_count, 1)
        self.assertEqual(bridge.inbox[0]["status"], jdctl.CNL_UNCERTAIN)

        with mock.patch.object(bridge, "snapshot"), mock.patch("jdctl.time.sleep"):
            bridge.handle({"command": "cnl_accept", "id": "cnl-1", "autostart": False})
        self.assertEqual(bridge.device.linkgrabber.add_links.call_count, 1)
        self.assertIn("explicit submit-again", emit.call_args.args[0]["message"])

        with mock.patch.object(bridge, "persist_inbox"), mock.patch.object(bridge, "snapshot"), mock.patch("jdctl.time.sleep"):
            bridge.handle({"command": "cnl_retry", "id": "cnl-1", "autostart": False})
        self.assertEqual(bridge.device.linkgrabber.add_links.call_count, 2)
        self.assertEqual(bridge.inbox, [])

    @mock.patch("jdctl.emit")
    def test_forget_keeps_account_removed_when_empty_config_unlink_fails(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com"}
        with mock.patch("jdctl.secret_clear"), mock.patch("jdctl.write_config"), mock.patch.object(Path, "unlink", side_effect=OSError("read-only")), mock.patch.object(bridge, "snapshot"):
            bridge.handle({"command": "forget"})
        action = emit.call_args.args[0]
        self.assertTrue(action["ok"])
        self.assertIn("read-only", action["message"])
        self.assertEqual(bridge.config, {})

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
                mock.patch("jdctl.secret_lookup", return_value=None), \
                mock.patch("jdctl.secret_store") as store, \
                mock.patch("jdctl.secret_clear") as clear, \
                mock.patch("jdctl.write_config") as write, \
                mock.patch.object(bridge, "snapshot"):
            configure_request = {"command": "configure", "email": "me@example.com", "password": "secret"}
            bridge.handle(configure_request)
            self.assertNotIn("password", configure_request)
            store.assert_called_once_with("me@example.com", "secret")
            write.assert_called_once()
            self.assertEqual(bridge.email, "me@example.com")
            with mock.patch.object(Path, "unlink") as unlink:
                bridge.handle({"command": "forget"})
                bridge.handle({"command": "forget"})
            self.assertEqual(unlink.call_count, 2)
            self.assertEqual(bridge.config, {})
            self.assertGreaterEqual(clear.call_count, 2)

    @mock.patch("jdctl.emit")
    def test_invalid_configuration_request_is_scrubbed(self, _emit):
        bridge = self.make_bridge()
        request_data = {"command": "configure", "email": "invalid", "password": "secret"}
        bridge.handle(request_data)
        self.assertNotIn("password", request_data)

    @mock.patch("jdctl.emit")
    def test_configure_succeeds_with_warning_when_old_secret_cleanup_fails(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "old@example.com"}
        api = FakeApi([])
        with mock.patch("jdctl.myjdapi.Myjdapi", return_value=api), \
                mock.patch("jdctl.secret_lookup", return_value=None), \
                mock.patch("jdctl.secret_store"), \
                mock.patch("jdctl.secret_clear", side_effect=RuntimeError("locked")), \
                mock.patch("jdctl.write_config"), mock.patch.object(bridge, "snapshot"):
            bridge.handle({"command": "configure", "email": "new@example.com", "password": "secret"})
        action = emit.call_args.args[0]
        self.assertTrue(action["ok"])
        self.assertIn("old credential could not be removed", action["message"])
        self.assertEqual(bridge.email, "new@example.com")

    @mock.patch("jdctl.emit")
    def test_configure_rolls_back_new_secret_when_config_commit_fails(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "old@example.com"}
        api = FakeApi([])
        with mock.patch("jdctl.myjdapi.Myjdapi", return_value=api), \
                mock.patch("jdctl.secret_lookup", return_value=None), \
                mock.patch("jdctl.secret_store") as store, \
                mock.patch("jdctl.secret_clear") as clear, \
                mock.patch("jdctl.write_config", side_effect=OSError("read-only")):
            bridge.handle({"command": "configure", "email": "new@example.com", "password": "secret"})
        self.assertFalse(emit.call_args.args[0]["ok"])
        self.assertEqual(bridge.email, "old@example.com")
        store.assert_called_once_with("new@example.com", "secret")
        clear.assert_called_once_with("new@example.com")

    @mock.patch("jdctl.emit")
    def test_same_account_reconfigure_aborts_when_previous_secret_is_unknown(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "same@example.com"}
        request_data = {"command": "configure", "email": "same@example.com", "password": "new-secret"}
        with mock.patch("jdctl.secret_lookup", side_effect=RuntimeError("keyring locked")), \
                mock.patch("jdctl.secret_store") as store:
            bridge.handle(request_data)
        store.assert_not_called()
        self.assertFalse(emit.call_args.args[0]["ok"])
        self.assertNotIn("password", request_data)

    @mock.patch("jdctl.emit")
    def test_same_account_config_commit_restores_previous_secret(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "same@example.com"}
        api = FakeApi([])
        with mock.patch("jdctl.myjdapi.Myjdapi", return_value=api), \
                mock.patch("jdctl.secret_lookup", return_value="old-secret"), \
                mock.patch("jdctl.secret_store") as store, \
                mock.patch("jdctl.secret_clear") as clear, \
                mock.patch("jdctl.write_config", side_effect=OSError("read-only")):
            bridge.handle({"command": "configure", "email": "same@example.com", "password": "new-secret"})
        self.assertEqual(store.call_args_list, [
            mock.call("same@example.com", "new-secret"),
            mock.call("same@example.com", "old-secret"),
        ])
        clear.assert_not_called()
        self.assertFalse(emit.call_args.args[0]["ok"])

    @mock.patch("jdctl.emit")
    def test_forget_restores_config_when_secret_removal_fails(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com", "selected_device_id": "one"}
        writes = []
        with mock.patch("jdctl.write_config", side_effect=lambda data: writes.append(dict(data))), \
                mock.patch("jdctl.secret_clear", side_effect=RuntimeError("keyring locked")), \
                mock.patch.object(bridge, "snapshot"):
            bridge.handle({"command": "forget"})
        self.assertFalse(emit.call_args.args[0]["ok"])
        self.assertEqual(writes, [{}, {"email": "me@example.com", "selected_device_id": "one"}])
        self.assertEqual(bridge.email, "me@example.com")

    @mock.patch("jdctl.emit")
    def test_forget_does_not_claim_old_config_when_rollback_fails(self, emit):
        bridge = self.make_bridge()
        bridge.config = {"email": "me@example.com"}
        with mock.patch("jdctl.write_config", side_effect=[None, OSError("rollback blocked")]), \
                mock.patch("jdctl.secret_clear", side_effect=RuntimeError("keyring locked")), \
                mock.patch.object(bridge, "snapshot"):
            bridge.handle({"command": "forget"})
        self.assertFalse(emit.call_args.args[0]["ok"])
        self.assertIn("configuration rollback failed", emit.call_args.args[0]["message"])
        self.assertEqual(bridge.config, {})


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
