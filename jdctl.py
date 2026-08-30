#!/usr/bin/env python3
"""JSON-lines bridge between Omarchy's QML shell and MyJDownloader."""

from __future__ import annotations

import base64
import binascii
import json
import os
import queue
import re
import select
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import myjdapi
from Crypto.Cipher import AES


APP_KEY = "https://github.com/flathack/omarchy-OmaJdownLoad"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omarchy" / "omajdownload"
CONFIG_FILE = CONFIG_DIR / "config.json"
INBOX_FILE = CONFIG_DIR / "clicknload-inbox.json"
POLL_SECONDS = 5.0
CNL_HOST = "127.0.0.1"
CNL_PORT = 9666
CNL_BODY_LIMIT = 1024 * 1024
CNL_KEY_PATTERN = re.compile(r"return\s+['\"]([0-9a-fA-F]{32})['\"]\s*;?", re.IGNORECASE)


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def text(value: Any, fallback: str = "") -> str:
    return fallback if value is None else str(value)


def number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def human_bytes(value: int, suffix: str = "") -> str:
    amount = float(max(0, value))
    units = ("B", "KB", "MB", "GB", "TB")
    unit = units[0]
    for unit in units:
        if amount < 1000 or unit == units[-1]:
            break
        amount /= 1000
    digits = 0 if amount >= 100 or unit == "B" else 1
    return f"{amount:.{digits}f} {unit}{suffix}"


def read_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_config(data: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temp = CONFIG_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(CONFIG_FILE)


def read_inbox() -> list[dict[str, Any]]:
    try:
        data = json.loads(INBOX_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def write_inbox(data: list[dict[str, Any]]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temp = INBOX_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(INBOX_FILE)


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.replace("\r", "\n").split("\n") if line.strip()]


def decrypt_cnl2(jk: str, crypted: str) -> str:
    """Decrypt CNL2 without executing the website-provided JavaScript."""
    match = CNL_KEY_PATTERN.search(jk)
    if not match:
        raise ValueError("CNL2 key is dynamic or unsupported")
    key = bytes.fromhex(match.group(1))
    try:
        ciphertext = base64.b64decode(crypted, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("CNL2 payload is not valid base64") from exc
    if not ciphertext or len(ciphertext) % AES.block_size:
        raise ValueError("CNL2 payload has an invalid length")
    plaintext = AES.new(key, AES.MODE_CBC, iv=key).decrypt(ciphertext)
    padding = plaintext[-1]
    if 0 < padding <= AES.block_size and plaintext.endswith(bytes([padding]) * padding):
        plaintext = plaintext[:-padding]
    else:
        plaintext = plaintext.rstrip(b"\x00")
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("CNL2 payload is not UTF-8") from exc


def parse_cnl_payload(path: str, fields: dict[str, list[str]]) -> dict[str, Any]:
    encrypted = path.rstrip("/").endswith("addcrypted2")
    if encrypted:
        links_text = decrypt_cnl2((fields.get("jk") or [""])[0], (fields.get("crypted") or [""])[0])
    else:
        links_text = (fields.get("urls") or [""])[0]
    links = split_lines(links_text)
    if not links:
        raise ValueError("Click'n'Load did not contain any links")
    passwords = split_lines((fields.get("passwords") or [""])[0])
    source = ((fields.get("source") or [""])[0]).strip()
    return {"links": links, "passwords": passwords, "source": source, "encrypted": encrypted}


def source_label(source: str) -> str:
    try:
        return urlsplit(source).hostname or source or "Unknown website"
    except ValueError:
        return source or "Unknown website"


class ClickNLoadServer:
    def __init__(self, events: queue.Queue[dict[str, Any]]) -> None:
        self.events = events
        self.httpd: ThreadingHTTPServer | None = None
        self.error = ""

    @property
    def listening(self) -> bool:
        return self.httpd is not None

    def start(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "OmaJDownLoad-CNL/1"

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def headers_common(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Cache-Control", "no-store")

            def reply(self, status: int, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.headers_common()
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self.headers_common()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:
                if self.path.rstrip("/") == "/flash":
                    self.reply(200, "JDownloader\r\n")
                elif self.path.split("?", 1)[0] == "/jdcheck.js":
                    self.reply(200, "jdownloader=true;", "application/javascript; charset=utf-8")
                else:
                    self.reply(404, "Not found")

            def do_POST(self) -> None:
                endpoint = self.path.split("?", 1)[0].rstrip("/")
                if endpoint not in ("/flash/add", "/flash/addcrypted2"):
                    self.reply(404, "Not found")
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.reply(400, "Invalid content length")
                    return
                if size <= 0 or size > CNL_BODY_LIMIT:
                    self.reply(413, "Click'n'Load payload is empty or too large")
                    return
                try:
                    raw = self.rfile.read(size).decode("utf-8")
                    fields = parse_qs(raw, keep_blank_values=True, max_num_fields=24)
                    payload = parse_cnl_payload(endpoint, fields)
                    owner.events.put(payload)
                    self.reply(200, "success\r\n")
                except (UnicodeDecodeError, ValueError) as exc:
                    owner.events.put({"error": str(exc)})
                    self.reply(400, str(exc))

        try:
            self.httpd = ThreadingHTTPServer((CNL_HOST, CNL_PORT), Handler)
            self.httpd.daemon_threads = True
            thread = threading.Thread(target=self.httpd.serve_forever, name="omajdownload-cnl", daemon=True)
            thread.start()
        except OSError as exc:
            self.httpd = None
            self.error = str(exc)


def secret_lookup(email: str) -> str:
    if not email:
        return ""
    result = subprocess.run(
        ["secret-tool", "lookup", "omarchy-plugin", "omajdownload", "account", email],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def secret_store(email: str, password: str) -> None:
    result = subprocess.run(
        [
            "secret-tool",
            "store",
            f"--label=Omarchy JDownloader ({email})",
            "omarchy-plugin",
            "omajdownload",
            "account",
            email,
        ],
        input=password,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not store password in the keyring")


def secret_clear(email: str) -> None:
    if not email:
        return
    subprocess.run(
        ["secret-tool", "clear", "omarchy-plugin", "omajdownload", "account", email],
        check=False,
        capture_output=True,
        timeout=10,
    )


def package_uuid(row: dict[str, Any]) -> str:
    for key in ("uuid", "packageUUID", "packageUUIDs", "id"):
        value = row.get(key)
        if isinstance(value, list):
            value = value[0] if value else ""
        if value not in (None, ""):
            return str(value)
    return ""


def normalize_package(row: Any, kind: str) -> dict[str, Any]:
    if not isinstance(row, dict):
        row = {}
    loaded = number(row.get("bytesLoaded"))
    total = number(row.get("bytesTotal"))
    speed = number(row.get("speed"))
    progress = round((loaded / total) * 100) if total > 0 else 0
    name = text(row.get("name") or row.get("packageName") or row.get("comment"), "Unnamed package")
    status = text(row.get("status") or row.get("availability"), "")
    running = bool(row.get("running")) or speed > 0
    return {
        "uuid": package_uuid(row),
        "name": name,
        "status": status,
        "loaded": loaded,
        "total": total,
        "size_text": human_bytes(total),
        "progress": max(0, min(100, progress)),
        "speed": speed,
        "speed_text": human_bytes(speed, "/s"),
        "running": running,
        "finished": bool(row.get("finished")),
        "enabled": row.get("enabled") is not False,
        "child_count": number(row.get("childCount")),
        "kind": kind,
    }


class Bridge:
    def __init__(self) -> None:
        self.config = read_config()
        self.inbox = read_inbox()
        self.jd: Any = None
        self.device: Any = None
        self.devices: list[dict[str, Any]] = []
        self.last_poll = 0.0
        self.last_error = ""
        self.cnl_events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.cnl_server = ClickNLoadServer(self.cnl_events)
        self.cnl_server.start()

    @property
    def email(self) -> str:
        return text(self.config.get("email"))

    @property
    def selected_id(self) -> str:
        return text(self.config.get("selected_device_id"))

    def connect(self, email: str | None = None, password: str | None = None) -> None:
        account = email or self.email
        credential = password if password is not None else secret_lookup(account)
        if not account or not credential:
            raise RuntimeError("MyJDownloader account is not configured")

        jd = myjdapi.Myjdapi()
        jd.set_app_key(APP_KEY)
        jd.connect(account, credential)
        devices = jd.list_devices() or []
        if not devices:
            raise RuntimeError("No online JDownloader instance was found")

        selected = self.selected_id
        if not any(text(item.get("id")) == selected for item in devices):
            selected = text(devices[0].get("id"))
        device = jd.get_device(device_id=selected)
        device.disable_direct_connection()

        self.jd = jd
        self.device = device
        self.devices = [
            {"id": text(item.get("id")), "name": text(item.get("name"), "JDownloader"), "type": text(item.get("type"))}
            for item in devices
        ]
        self.config["email"] = account
        self.config["selected_device_id"] = selected
        self.last_error = ""

    def disconnect(self) -> None:
        try:
            if self.jd is not None and self.jd.is_connected():
                self.jd.disconnect()
        except Exception:
            pass
        self.jd = None
        self.device = None
        self.devices = []

    def inbox_view(self) -> list[dict[str, Any]]:
        return [
            {
                "id": text(item.get("id")),
                "source": source_label(text(item.get("source"))),
                "link_count": len(item.get("links", [])),
                "encrypted": item.get("encrypted") is True,
                "received_at": text(item.get("received_at")),
            }
            for item in self.inbox
        ]

    def emit_cnl_state(self) -> None:
        emit({
            "type": "cnl",
            "listening": self.cnl_server.listening,
            "port": CNL_PORT,
            "error": self.cnl_server.error,
            "inbox": self.inbox_view(),
        })

    def drain_cnl_events(self) -> None:
        changed = False
        while True:
            try:
                payload = self.cnl_events.get_nowait()
            except queue.Empty:
                break
            if payload.get("error"):
                emit({"type": "cnl_error", "message": text(payload.get("error"))})
                continue
            payload["id"] = uuid.uuid4().hex
            payload["received_at"] = time.strftime("%H:%M")
            self.inbox.append(payload)
            self.inbox = self.inbox[-30:]
            changed = True
        if changed:
            write_inbox(self.inbox)
            self.emit_cnl_state()

    def snapshot(self, refresh: bool = True) -> None:
        configured = bool(self.email and secret_lookup(self.email))
        if not configured:
            emit({
                "type": "snapshot",
                "configured": False,
                "connected": False,
                "devices": [],
                "controller_state": "OFFLINE",
                "speed": 0,
                "speed_text": "0 B/s",
                "downloads": [],
                "grabber": [],
                "active_downloads": 0,
                "error": "",
            })
            return

        try:
            if self.device is None:
                self.connect()
            controller = text(self.device.downloadcontroller.get_current_state(), "IDLE")
            speed = number(self.device.downloadcontroller.get_speed_in_bytes())
            download_rows = self.device.downloads.query_packages([{
                "bytesLoaded": True,
                "bytesTotal": True,
                "childCount": True,
                "enabled": True,
                "eta": True,
                "finished": True,
                "hosts": True,
                "maxResults": 60,
                "running": True,
                "speed": True,
                "startAt": 0,
                "status": True,
            }]) or []
            grabber_rows = self.device.linkgrabber.query_packages([{
                "availableOfflineCount": True,
                "availableOnlineCount": True,
                "bytesTotal": True,
                "childCount": True,
                "enabled": True,
                "hosts": True,
                "maxResults": 60,
                "startAt": 0,
                "status": True,
            }]) or []
            downloads = [normalize_package(row, "download") for row in download_rows]
            grabber = [normalize_package(row, "grabber") for row in grabber_rows]
            active = sum(1 for item in downloads if item["running"])
            selected_name = next(
                (item["name"] for item in self.devices if item["id"] == self.selected_id),
                "JDownloader",
            )
            emit({
                "type": "snapshot",
                "configured": True,
                "connected": True,
                "devices": self.devices,
                "selected_device_id": self.selected_id,
                "selected_device_name": selected_name,
                "controller_state": controller,
                "speed": speed,
                "speed_text": human_bytes(speed, "/s"),
                "downloads": downloads,
                "grabber": grabber,
                "active_downloads": active,
                "error": "",
            })
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc).strip() or exc.__class__.__name__
            self.disconnect()
            emit({
                "type": "snapshot",
                "configured": True,
                "connected": False,
                "devices": self.devices,
                "selected_device_id": self.selected_id,
                "selected_device_name": "JDownloader",
                "controller_state": "OFFLINE",
                "speed": 0,
                "speed_text": "0 B/s",
                "downloads": [],
                "grabber": [],
                "active_downloads": 0,
                "error": self.last_error,
            })

    def action_result(self, ok: bool, message: str) -> None:
        emit({"type": "action", "ok": ok, "message": message})

    def handle(self, request: dict[str, Any]) -> None:
        command = text(request.get("command"))
        if command == "refresh":
            self.snapshot()
            self.last_poll = time.monotonic()
            return

        if command == "configure":
            email = text(request.get("email")).strip()
            password = text(request.get("password"))
            if "@" not in email or not password:
                self.action_result(False, "Please enter a valid email address and password")
                return
            try:
                old_email = self.email
                self.disconnect()
                self.connect(email, password)
                secret_store(email, password)
                write_config(self.config)
                if old_email and old_email != email:
                    secret_clear(old_email)
                self.action_result(True, f"Connected to {len(self.devices)} JDownloader instance(s)")
                self.snapshot()
            except Exception as exc:
                self.disconnect()
                self.action_result(False, str(exc).strip() or "MyJDownloader login failed")
            finally:
                password = ""
            return

        if command == "forget":
            old_email = self.email
            self.disconnect()
            secret_clear(old_email)
            try:
                CONFIG_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            self.config = {}
            self.action_result(True, "MyJDownloader account removed")
            self.snapshot()
            return

        if command == "cnl_reject":
            item_id = text(request.get("id"))
            before = len(self.inbox)
            self.inbox = [item for item in self.inbox if text(item.get("id")) != item_id]
            write_inbox(self.inbox)
            removed = len(self.inbox) != before
            self.action_result(removed, "Click'n'Load request removed" if removed else "Click'n'Load request not found")
            self.emit_cnl_state()
            return

        try:
            if self.device is None:
                self.connect()

            if command == "select_device":
                device_id = text(request.get("device_id"))
                if not any(item["id"] == device_id for item in self.devices):
                    raise RuntimeError("The selected JDownloader instance is not online")
                self.device = self.jd.get_device(device_id=device_id)
                self.device.disable_direct_connection()
                self.config["selected_device_id"] = device_id
                write_config(self.config)
                self.action_result(True, "JDownloader instance selected")
            elif command == "control":
                action = text(request.get("action"))
                if action == "start":
                    self.device.downloadcontroller.start_downloads()
                elif action == "stop":
                    self.device.downloadcontroller.stop_downloads()
                elif action == "pause":
                    self.device.downloadcontroller.pause_downloads(True)
                elif action == "resume":
                    self.device.downloadcontroller.pause_downloads(False)
                else:
                    raise RuntimeError("Unknown download control action")
                self.action_result(True, f"Downloads: {action}")
            elif command == "force_download":
                ids = [str(value) for value in request.get("package_ids", [])]
                self.device.downloads.force_download([], ids)
                self.action_result(True, "Package started")
            elif command == "add_links":
                links = text(request.get("links")).strip()
                if not links:
                    raise RuntimeError("Paste at least one download link")
                autostart = request.get("autostart") is True
                self.device.linkgrabber.add_links([{
                    "autostart": autostart,
                    "links": links,
                    "packageName": None,
                    "extractPassword": None,
                    "priority": "DEFAULT",
                    "downloadPassword": None,
                    "destinationFolder": None,
                    "overwritePackagizerRules": False,
                }])
                self.action_result(True, "Links added" + (" and queued" if autostart else " to LinkGrabber"))
            elif command == "cnl_accept":
                item_id = text(request.get("id"))
                item = next((entry for entry in self.inbox if text(entry.get("id")) == item_id), None)
                if item is None:
                    raise RuntimeError("Click'n'Load request not found")
                links = "\r\n".join(str(value) for value in item.get("links", []))
                passwords = "\r\n".join(str(value) for value in item.get("passwords", [])) or None
                autostart = request.get("autostart") is True
                self.device.linkgrabber.add_links([{
                    "autostart": autostart,
                    "links": links,
                    "packageName": None,
                    "extractPassword": passwords,
                    "priority": "DEFAULT",
                    "downloadPassword": None,
                    "destinationFolder": None,
                    "overwritePackagizerRules": False,
                }])
                self.inbox = [entry for entry in self.inbox if text(entry.get("id")) != item_id]
                write_inbox(self.inbox)
                self.emit_cnl_state()
                self.action_result(True, "Click'n'Load links added" + (" and queued" if autostart else " to LinkGrabber"))
            elif command == "move_grabber":
                ids = [str(value) for value in request.get("package_ids", [])]
                self.device.linkgrabber.move_to_downloadlist([], ids)
                self.action_result(True, "Package moved to downloads")
            elif command == "remove_downloads":
                ids = [str(value) for value in request.get("package_ids", [])]
                self.device.downloads.remove_links([], ids)
                self.action_result(True, "Download entry removed; files were kept")
            elif command == "remove_grabber":
                ids = [str(value) for value in request.get("package_ids", [])]
                self.device.linkgrabber.remove_links([], ids)
                self.action_result(True, "LinkGrabber entry removed")
            else:
                raise RuntimeError("Unknown command")

            time.sleep(0.25)
            self.snapshot()
            self.last_poll = time.monotonic()
        except Exception as exc:
            self.action_result(False, str(exc).strip() or "JDownloader action failed")

    def run(self) -> None:
        self.snapshot()
        self.emit_cnl_state()
        self.last_poll = time.monotonic()
        while True:
            self.drain_cnl_events()
            timeout = min(0.25, max(0.0, POLL_SECONDS - (time.monotonic() - self.last_poll)))
            readable, _, _ = select.select([sys.stdin], [], [], timeout)
            if readable:
                line = sys.stdin.readline()
                if line == "":
                    return
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise ValueError("request must be an object")
                    self.handle(request)
                except (json.JSONDecodeError, ValueError) as exc:
                    self.action_result(False, f"Invalid helper request: {exc}")
            elif time.monotonic() - self.last_poll >= POLL_SECONDS:
                self.snapshot()
                self.last_poll = time.monotonic()


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] != "daemon":
        print("usage: jdctl.py daemon", file=sys.stderr)
        return 2
    Bridge().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
