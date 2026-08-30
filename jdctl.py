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
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import myjdapi
from Crypto.Cipher import AES


APP_KEY = "https://github.com/flathack/omarchy-OmaJD-Remote"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omarchy" / "omajdownload"
CONFIG_FILE = CONFIG_DIR / "config.json"
INBOX_FILE = CONFIG_DIR / "clicknload-inbox.json"
POLL_SECONDS = 5.0
DEVICE_REFRESH_SECONDS = 15.0
PACKAGE_REFRESH_SECONDS = 30.0
PACKAGE_PAGE_SIZE = 60
PACKAGE_MODEL_LIMIT = 6000
CNL_HOST = "127.0.0.1"
CNL_PORT = int(os.environ.get("OMAJDOWNLOAD_CNL_PORT", "9666"))
CNL_BODY_LIMIT = 1024 * 1024
CNL_INBOX_LIMIT = 30
CNL_INBOX_BYTE_LIMIT = 4 * 1024 * 1024
CNL_REQUEST_LINK_LIMIT = 2000
CNL_INBOX_LINK_LIMIT = 5000
CNL_DETAIL_LINK_LIMIT = 200
CNL_EVENT_LIMIT = 32
CNL_EVENT_DRAIN_LIMIT = 8
CNL_SOURCE_LABEL_LIMIT = 256
CNL_KEY_PATTERN = re.compile(r"return\s+['\"]([0-9a-fA-F]{32})['\"]\s*;?", re.IGNORECASE)
CNL_PENDING = "pending"
CNL_SUBMITTING = "submitting"
CNL_UNCERTAIN = "uncertain"


class StateFileError(RuntimeError):
    def __init__(self, path: Path, message: str) -> None:
        super().__init__(message)
        self.path = path


class StateCommitError(OSError):
    def __init__(self, message: str, *, committed: bool) -> None:
        super().__init__(message)
        self.committed = committed


@dataclass(frozen=True)
class PackageQueryResult:
    rows: list[dict[str, Any]]
    truncated: bool = False


def atomic_write_json(path: Path, data: Any, *, ensure_ascii: bool = True) -> None:
    """Atomically and durably replace a private JSON state file."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    encoded = (json.dumps(data, ensure_ascii=ensure_ascii, indent=2) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    replaced = False
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        replaced = True
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            try:
                os.fsync(directory_fd)
            except OSError as exc:
                raise StateCommitError(f"Could not synchronize {path.name} directory entry: {exc}", committed=True) from exc
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        if not replaced:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


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
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise StateFileError(CONFIG_FILE, f"Could not read configuration: {exc}") from exc
    if not isinstance(data, dict):
        raise StateFileError(CONFIG_FILE, "Configuration must contain a JSON object")
    return data


def write_config(data: dict[str, Any]) -> None:
    atomic_write_json(CONFIG_FILE, data)


def read_inbox() -> list[dict[str, Any]]:
    try:
        data = json.loads(INBOX_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise StateFileError(INBOX_FILE, f"Could not read Click'n'Load inbox: {exc}") from exc
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise StateFileError(INBOX_FILE, "Click'n'Load inbox must contain a JSON array of objects")
    return data


def write_inbox(data: list[dict[str, Any]]) -> None:
    atomic_write_json(INBOX_FILE, data, ensure_ascii=False)


def quarantine_state_file(path: Path) -> Path:
    suffix = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    backup = path.with_name(f"{path.name}.corrupt-{suffix}")
    path.replace(backup)
    return backup


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


def bounded_source_label(source: str) -> str:
    """Return a compact, display-only source label safe for persistence/QML."""
    value = source_label(source.strip())
    if len(value) <= CNL_SOURCE_LABEL_LIMIT:
        return value
    return value[: CNL_SOURCE_LABEL_LIMIT - 1] + "…"


def compact_claimed_source(source: str) -> str:
    value = source.strip()
    return bounded_source_label(value) if value else ""


def origin_label(origin: str) -> str:
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ""
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}"
    except (ValueError, TypeError):
        return ""


def link_hosts(links: list[Any]) -> list[str]:
    hosts: list[str] = []
    for value in links:
        try:
            parsed = urlsplit(str(value))
            host = parsed.hostname or parsed.scheme or "Unknown destination"
        except ValueError:
            host = "Unknown destination"
        if host not in hosts:
            hosts.append(host)
    return hosts


class ClickNLoadServer:
    def __init__(
        self,
        admit: Callable[[dict[str, Any]], None],
        events: queue.Queue[dict[str, Any]],
        host: str = CNL_HOST,
        port: int = CNL_PORT,
    ) -> None:
        self.admit = admit
        self.events = events
        self.host = host
        self.requested_port = port
        self.httpd: ThreadingHTTPServer | None = None
        self.error = ""
        self.extension_token = uuid.uuid4().hex

    @property
    def listening(self) -> bool:
        return self.httpd is not None

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1]) if self.httpd is not None else self.requested_port

    def start(self) -> None:
        owner = self

        def report_error(message: str) -> None:
            payload = {"error": message}
            try:
                owner.events.put_nowait(payload)
                return
            except queue.Full:
                pass
            # Keep the newest error without allowing rejected requests to grow
            # memory indefinitely. Accepted state notifications use a separate
            # threading.Event and therefore cannot be displaced here.
            try:
                owner.events.get_nowait()
            except queue.Empty:
                pass
            try:
                owner.events.put_nowait(payload)
            except queue.Full:
                pass

        class Handler(BaseHTTPRequestHandler):
            server_version = "OmaJD-Remote-CNL/1"

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

            def reply_private(self, status: int, body: str) -> None:
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(encoded)

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self.headers_common()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:
                endpoint = urlsplit(self.path).path.rstrip("/")
                if endpoint == "/flash":
                    self.reply(200, "JDownloader\r\n")
                elif endpoint == "/jdcheck.js":
                    self.reply(200, "jdownloader=true;", "application/javascript; charset=utf-8")
                elif self.path.split("?", 1)[0] == "/omajdownload/extension-token":
                    self.reply_private(200, owner.extension_token)
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
                    trusted_extension = self.headers.get("X-OmaJDownLoad-Token", "") == owner.extension_token
                    browser_origin = origin_label(self.headers.get("X-OmaJDownLoad-Origin", "")) if trusted_extension else ""
                    http_origin = origin_label(self.headers.get("Origin", ""))
                    payload["claimed_source"] = text(payload.get("source"))
                    payload["origin"] = browser_origin or http_origin
                    payload["origin_verified"] = bool(browser_origin or http_origin)
                    owner.admit(payload)
                    self.reply(200, "success\r\n")
                except (UnicodeDecodeError, ValueError) as exc:
                    report_error(str(exc))
                    self.reply(400, str(exc))
                except OverflowError as exc:
                    report_error(str(exc))
                    self.reply(429, str(exc))
                except OSError as exc:
                    report_error(f"Could not persist Click'n'Load request: {exc}")
                    self.reply(503, "Could not persist Click'n'Load request")

        try:
            self.httpd = ThreadingHTTPServer((self.host, self.requested_port), Handler)
            self.httpd.daemon_threads = True
            thread = threading.Thread(target=self.httpd.serve_forever, name="omajdownload-cnl", daemon=True)
            thread.start()
        except OSError as exc:
            self.httpd = None
            self.error = str(exc)

    def stop(self) -> None:
        if self.httpd is None:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = None


def secret_lookup(email: str) -> str | None:
    if not email:
        return None
    result = subprocess.run(
        ["secret-tool", "lookup", "omarchy-plugin", "omajdownload", "account", email],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 0:
        return result.stdout.rstrip("\n")
    if result.returncode == 1 and not result.stderr.strip():
        return None
    raise RuntimeError(result.stderr.strip() or f"Could not read password from the keyring (exit {result.returncode})")


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
    result = subprocess.run(
        ["secret-tool", "clear", "omarchy-plugin", "omajdownload", "account", email],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0 and result.stderr.strip():
        raise RuntimeError(result.stderr.strip() or "Could not remove password from the keyring")


def query_all_packages(query: Any, fields: dict[str, Any]) -> PackageQueryResult:
    rows: list[dict[str, Any]] = []
    seen_pages: set[tuple[str, ...]] = set()
    page = 0
    while len(rows) < PACKAGE_MODEL_LIMIT:
        params = dict(fields)
        params["maxResults"] = PACKAGE_PAGE_SIZE
        params["startAt"] = page * PACKAGE_PAGE_SIZE
        batch = query([params]) or []
        if not isinstance(batch, list):
            raise RuntimeError("JDownloader returned an invalid package list")
        signature = tuple(package_uuid(row) for row in batch if isinstance(row, dict))
        if batch and signature in seen_pages:
            raise RuntimeError("JDownloader package pagination repeated a page")
        seen_pages.add(signature)
        rows.extend(row for row in batch if isinstance(row, dict))
        if len(batch) < PACKAGE_PAGE_SIZE:
            return PackageQueryResult(rows)
        page += 1

    params = dict(fields)
    params["maxResults"] = 1
    params["startAt"] = len(rows)
    remainder = query([params]) or []
    if not isinstance(remainder, list):
        raise RuntimeError("JDownloader returned an invalid package list")
    return PackageQueryResult(rows[:PACKAGE_MODEL_LIMIT], truncated=bool(remainder))


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
    def __init__(self, start_cnl: bool = True, cnl_port: int = CNL_PORT) -> None:
        self.state_warnings: list[str] = []
        self.config_write_blocked = False
        self.inbox_write_blocked = False
        self.config = self.load_config_state()
        self.inbox = self.load_inbox_state()
        self.jd: Any = None
        self.device: Any = None
        self.active_device_id = ""
        self.devices: list[dict[str, Any]] = []
        self.last_poll = 0.0
        self.last_device_refresh = 0.0
        self.last_package_refresh = 0.0
        self.last_error = ""
        self.cached_downloads: list[dict[str, Any]] = []
        self.cached_grabber: list[dict[str, Any]] = []
        self.download_error = ""
        self.grabber_error = ""
        self.downloads_truncated = False
        self.grabber_truncated = False
        self.uncertain_add_links: dict[str, Any] | None = None
        self.inbox_lock = threading.Lock()
        self.cnl_events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=CNL_EVENT_LIMIT)
        self.cnl_state_changed = threading.Event()
        self.cnl_server = ClickNLoadServer(self.admit_cnl, self.cnl_events, port=cnl_port)
        if start_cnl:
            self.cnl_server.start()

    def load_config_state(self) -> dict[str, Any]:
        try:
            return read_config()
        except StateFileError as exc:
            try:
                backup = quarantine_state_file(exc.path)
                self.state_warnings.append(f"{exc}; preserved as {backup.name}")
            except OSError as backup_error:
                self.config_write_blocked = True
                self.state_warnings.append(f"{exc}; could not preserve it: {backup_error}")
            return {}

    def load_inbox_state(self) -> list[dict[str, Any]]:
        try:
            inbox = read_inbox()
        except StateFileError as exc:
            try:
                backup = quarantine_state_file(exc.path)
                self.state_warnings.append(f"{exc}; preserved as {backup.name}")
            except OSError as backup_error:
                self.inbox_write_blocked = True
                self.state_warnings.append(f"{exc}; could not preserve it: {backup_error}")
            return []

        normalized: list[dict[str, Any]] = []
        changed = False
        for source in inbox:
            item = dict(source)
            compact_source = compact_claimed_source(text(item.get("claimed_source") or item.get("source")))
            if item.get("claimed_source") != compact_source or "source" in item:
                item["claimed_source"] = compact_source
                item.pop("source", None)
                changed = True
            compact_origin = origin_label(text(item.get("origin")))
            if text(item.get("origin")) != compact_origin:
                item["origin"] = compact_origin
                changed = True
            status = text(item.get("status"), CNL_PENDING)
            if status == CNL_SUBMITTING:
                status = CNL_UNCERTAIN
                changed = True
            elif status not in (CNL_PENDING, CNL_UNCERTAIN):
                status = CNL_PENDING
                changed = True
            item["status"] = status
            normalized.append(item)
        if changed:
            try:
                write_inbox(normalized)
            except OSError as exc:
                self.inbox_write_blocked = True
                self.state_warnings.append(f"Could not recover interrupted Click'n'Load state: {exc}")
        return normalized

    @property
    def state_warning(self) -> str:
        return " · ".join(self.state_warnings)

    def persist_config(self, data: dict[str, Any]) -> None:
        if self.config_write_blocked:
            raise OSError("Configuration is locked to preserve an unreadable state file")
        write_config(data)

    def persist_inbox(self, data: list[dict[str, Any]]) -> None:
        if self.inbox_write_blocked:
            raise OSError("Click'n'Load inbox is locked to preserve an unreadable state file")
        write_inbox(data)

    @property
    def email(self) -> str:
        return text(self.config.get("email"))

    @property
    def selected_id(self) -> str:
        return text(self.config.get("selected_device_id"))

    def refresh_devices(self, update: bool = True) -> None:
        if self.jd is None:
            self.devices = []
            self.device = None
            return
        if update:
            self.jd.update_devices()
        rows = self.jd.list_devices() or []
        self.devices = [
            {"id": text(item.get("id")), "name": text(item.get("name"), "JDownloader"), "type": text(item.get("type"))}
            for item in rows
        ]
        online_ids = {item["id"] for item in self.devices}
        current_id = text(getattr(self.device, "device_id", ""))
        preferred = self.selected_id
        target = preferred if preferred in online_ids else (current_id if current_id in online_ids else "")
        if not target and self.devices:
            target = self.devices[0]["id"]
        if not target:
            self.device = None
        elif self.device is None or current_id != target:
            self.device = self.jd.get_device(device_id=target)
            self.device.disable_direct_connection()
            self.cached_downloads = []
            self.cached_grabber = []
            self.last_package_refresh = 0.0
        self.active_device_id = target
        self.last_device_refresh = time.monotonic()

    def connect(self, email: str | None = None, password: str | None = None) -> None:
        account = email or self.email
        credential = password if password is not None else secret_lookup(account)
        if not account or not credential:
            raise RuntimeError("MyJDownloader password is unavailable in the desktop keyring")

        jd = myjdapi.Myjdapi()
        jd.set_app_key(APP_KEY)
        jd.connect(account, credential)
        self.jd = jd
        if self.email and self.email != account:
            self.config.pop("selected_device_id", None)
        self.config["email"] = account
        self.refresh_devices(update=False)
        self.last_error = ""

    def disconnect(self) -> None:
        try:
            if self.jd is not None and self.jd.is_connected():
                self.jd.disconnect()
        except Exception:
            pass
        self.jd = None
        self.device = None
        self.active_device_id = ""
        self.devices = []

    @staticmethod
    def inbox_item_size(item: dict[str, Any]) -> int:
        return len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def inbox_view(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in self.inbox:
            hosts = link_hosts(item.get("links", []))
            result.append({
                "id": text(item.get("id")),
                "source": bounded_source_label(text(item.get("claimed_source") or item.get("source"))),
                "origin": origin_label(text(item.get("origin"))),
                "origin_verified": item.get("origin_verified") is True,
                "link_hosts": hosts[:4],
                "hidden_host_count": max(0, len(hosts) - 4),
                "link_count": len(item.get("links", [])),
                "encrypted": item.get("encrypted") is True,
                "received_at": text(item.get("received_at")),
                "status": text(item.get("status"), CNL_PENDING),
            })
        return result

    def emit_cnl_state(self) -> None:
        emit({
            "type": "cnl",
            "listening": self.cnl_server.listening,
            "port": self.cnl_server.port,
            "error": " · ".join(value for value in (self.cnl_server.error, self.state_warning) if value),
            "inbox": self.inbox_view(),
        })

    def admit_cnl(self, payload: dict[str, Any]) -> None:
        with self.inbox_lock:
            if len(self.inbox) >= CNL_INBOX_LIMIT:
                raise OverflowError(f"Click'n'Load inbox is full ({CNL_INBOX_LIMIT} requests)")
            item = dict(payload)
            item["claimed_source"] = compact_claimed_source(text(item.get("claimed_source") or item.get("source")))
            item.pop("source", None)
            links = item.get("links", [])
            if len(links) > CNL_REQUEST_LINK_LIMIT:
                raise OverflowError(f"Click'n'Load request has more than {CNL_REQUEST_LINK_LIMIT} links")
            item["id"] = uuid.uuid4().hex
            item["received_at"] = time.strftime("%H:%M")
            item["status"] = CNL_PENDING
            updated = [*self.inbox, item]
            total_links = sum(len(entry.get("links", [])) for entry in updated)
            if total_links > CNL_INBOX_LINK_LIMIT:
                raise OverflowError(f"Click'n'Load inbox exceeds {CNL_INBOX_LINK_LIMIT} links")
            total_bytes = sum(self.inbox_item_size(entry) for entry in updated)
            if total_bytes > CNL_INBOX_BYTE_LIMIT:
                raise OverflowError(f"Click'n'Load inbox exceeds {human_bytes(CNL_INBOX_BYTE_LIMIT)}")
            try:
                self.persist_inbox(updated)
            except StateCommitError as exc:
                if exc.committed:
                    self.inbox = updated
                raise
            self.inbox = updated
            self.cnl_state_changed.set()

    def drain_cnl_events(self) -> None:
        for _ in range(CNL_EVENT_DRAIN_LIMIT):
            try:
                payload = self.cnl_events.get_nowait()
            except queue.Empty:
                break
            if payload.get("error"):
                emit({"type": "cnl_error", "message": text(payload.get("error"))})
        with self.inbox_lock:
            changed = self.cnl_state_changed.is_set()
            if changed:
                self.cnl_state_changed.clear()
        if changed:
            self.emit_cnl_state()

    def snapshot(self, refresh: bool = False) -> None:
        configured = bool(self.email)
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
                "download_error": "",
                "grabber_error": "",
                "downloads_truncated": False,
                "grabber_truncated": False,
                "active_downloads": 0,
                "error": self.state_warning,
                "add_links_uncertain": self.uncertain_add_links is not None,
                "add_links_retry_token": text((self.uncertain_add_links or {}).get("token")),
            })
            return

        try:
            if self.jd is None:
                self.connect()
            elif time.monotonic() - self.last_device_refresh >= DEVICE_REFRESH_SECONDS:
                self.refresh_devices()
            if self.device is None:
                emit({
                    "type": "snapshot",
                    "configured": True,
                    "connected": False,
                    "devices": self.devices,
                    "selected_device_id": self.active_device_id,
                    "selected_device_name": "No online instance",
                    "controller_state": "OFFLINE",
                    "speed": 0,
                    "speed_text": "0 B/s",
                    "downloads": [],
                    "grabber": [],
                    "download_error": "",
                    "grabber_error": "",
                    "downloads_truncated": False,
                    "grabber_truncated": False,
                    "active_downloads": 0,
                    "error": " · ".join(value for value in ("No online JDownloader instance was found", self.state_warning) if value),
                    "add_links_uncertain": self.uncertain_add_links is not None,
                    "add_links_retry_token": text((self.uncertain_add_links or {}).get("token")),
                })
                return
            controller = text(self.device.downloadcontroller.get_current_state(), "IDLE")
            speed = number(self.device.downloadcontroller.get_speed_in_bytes())
            packages_due = refresh or self.last_package_refresh == 0.0 \
                or time.monotonic() - self.last_package_refresh >= PACKAGE_REFRESH_SECONDS
            if packages_due:
                try:
                    download_result = query_all_packages(self.device.downloads.query_packages, {
                        "bytesLoaded": True,
                        "bytesTotal": True,
                        "childCount": True,
                        "enabled": True,
                        "eta": True,
                        "finished": True,
                        "hosts": True,
                        "running": True,
                        "speed": True,
                        "status": True,
                    })
                    self.cached_downloads = [normalize_package(row, "download") for row in download_result.rows]
                    self.downloads_truncated = download_result.truncated
                    self.download_error = ""
                except Exception as exc:
                    self.download_error = str(exc).strip() or "Could not refresh downloads"
                try:
                    grabber_result = query_all_packages(self.device.linkgrabber.query_packages, {
                        "availableOfflineCount": True,
                        "availableOnlineCount": True,
                        "bytesTotal": True,
                        "childCount": True,
                        "enabled": True,
                        "hosts": True,
                        "status": True,
                    })
                    self.cached_grabber = [normalize_package(row, "grabber") for row in grabber_result.rows]
                    self.grabber_truncated = grabber_result.truncated
                    self.grabber_error = ""
                except Exception as exc:
                    self.grabber_error = str(exc).strip() or "Could not refresh LinkGrabber"
                self.last_package_refresh = time.monotonic()
            active = sum(1 for item in self.cached_downloads if item["running"])
            selected_name = next(
                (item["name"] for item in self.devices if item["id"] == self.active_device_id),
                "JDownloader",
            )
            emit({
                "type": "snapshot",
                "configured": True,
                "connected": True,
                "devices": self.devices,
                "selected_device_id": self.active_device_id,
                "selected_device_name": selected_name,
                "controller_state": controller,
                "speed": speed,
                "speed_text": human_bytes(speed, "/s"),
                "downloads": self.cached_downloads,
                "grabber": self.cached_grabber,
                "download_error": self.download_error,
                "grabber_error": self.grabber_error,
                "downloads_truncated": self.downloads_truncated,
                "grabber_truncated": self.grabber_truncated,
                "active_downloads": active,
                "error": self.state_warning,
                "add_links_uncertain": self.uncertain_add_links is not None,
                "add_links_retry_token": text((self.uncertain_add_links or {}).get("token")),
            })
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc).strip() or exc.__class__.__name__
            display_error = " · ".join(value for value in (self.last_error, self.state_warning) if value)
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
                "download_error": "",
                "grabber_error": "",
                "downloads_truncated": False,
                "grabber_truncated": False,
                "active_downloads": 0,
                "error": display_error,
                "add_links_uncertain": self.uncertain_add_links is not None,
                "add_links_retry_token": text((self.uncertain_add_links or {}).get("token")),
            })

    def action_result(
        self,
        ok: bool,
        message: str,
        request: dict[str, Any] | None = None,
        **details: Any,
    ) -> None:
        source = request or {}
        emit({
            "type": "action",
            "ok": ok,
            "message": message,
            "command": text(source.get("command")),
            "request_id": text(source.get("request_id")),
            **details,
        })

    def replace_inbox_item(self, item_id: str, status: str) -> dict[str, Any]:
        with self.inbox_lock:
            found: dict[str, Any] | None = None
            updated: list[dict[str, Any]] = []
            for source in self.inbox:
                item = dict(source)
                if text(item.get("id")) == item_id:
                    item["status"] = status
                    found = item
                updated.append(item)
            if found is None:
                raise RuntimeError("Click'n'Load request not found")
            try:
                self.persist_inbox(updated)
            except StateCommitError as exc:
                if exc.committed:
                    self.inbox = updated
                raise
            self.inbox = updated
            return found

    def mark_cnl_uncertain(self, item_id: str) -> str:
        with self.inbox_lock:
            updated: list[dict[str, Any]] = []
            for source in self.inbox:
                item = dict(source)
                if text(item.get("id")) == item_id:
                    item["status"] = CNL_UNCERTAIN
                updated.append(item)
            self.inbox = updated
            try:
                self.persist_inbox(updated)
                return ""
            except OSError as exc:
                return f"; uncertain state could not be persisted: {exc}"

    def submit_cnl(self, request: dict[str, Any], retry: bool) -> None:
        item_id = text(request.get("id"))
        current = next((entry for entry in self.inbox if text(entry.get("id")) == item_id), None)
        if current is None:
            raise RuntimeError("Click'n'Load request not found")
        status = text(current.get("status"), CNL_PENDING)
        expected = CNL_UNCERTAIN if retry else CNL_PENDING
        if status != expected:
            if status == CNL_UNCERTAIN:
                raise RuntimeError("Previous submission outcome is uncertain; use the explicit submit-again action")
            raise RuntimeError(f"Click'n'Load request cannot be submitted from state {status}")

        try:
            item = self.replace_inbox_item(item_id, CNL_SUBMITTING)
        except StateCommitError as exc:
            persistence_warning = self.mark_cnl_uncertain(item_id)
            self.emit_cnl_state()
            self.action_result(False, f"Could not durably start Click'n'Load submission: {exc}{persistence_warning}", request)
            return
        self.emit_cnl_state()
        links = "\r\n".join(str(value) for value in item.get("links", []))
        passwords = "\r\n".join(str(value) for value in item.get("passwords", [])) or None
        autostart = request.get("autostart") is True
        try:
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
        except Exception as exc:
            persistence_warning = self.mark_cnl_uncertain(item_id)
            self.emit_cnl_state()
            self.action_result(
                False,
                f"Click'n'Load submission outcome is uncertain: {str(exc).strip() or exc.__class__.__name__}{persistence_warning}",
                request,
            )
            return

        try:
            with self.inbox_lock:
                updated = [entry for entry in self.inbox if text(entry.get("id")) != item_id]
                try:
                    self.persist_inbox(updated)
                except StateCommitError as exc:
                    if exc.committed:
                        self.inbox = updated
                        self.emit_cnl_state()
                        self.action_result(False, f"Links reached JDownloader, but durable inbox removal is uncertain: {exc}", request)
                        return
                    raise
                self.inbox = updated
        except OSError as exc:
            persistence_warning = self.mark_cnl_uncertain(item_id)
            self.emit_cnl_state()
            self.action_result(
                False,
                f"Links reached JDownloader, but local completion is uncertain: {exc}{persistence_warning}",
                request,
            )
            return
        self.emit_cnl_state()
        self.action_result(True, "Click'n'Load links added" + (" and queued" if autostart else " to LinkGrabber"), request)

    def handle(self, request: dict[str, Any]) -> None:
        command = text(request.get("command"))
        if command == "refresh":
            self.snapshot(refresh=True)
            self.last_poll = time.monotonic()
            return

        if command == "configure":
            email = text(request.get("email")).strip()
            password = text(request.pop("password", ""))
            if "@" not in email or not password:
                self.action_result(False, "Please enter a valid email address and password", request)
                return
            try:
                old_email = self.email
                old_config = dict(self.config)
                previous_target_secret = secret_lookup(email)
                self.disconnect()
                self.connect(email, password)
                secret_store(email, password)
                persistence_warning = ""
                try:
                    self.persist_config(self.config)
                except StateCommitError as exc:
                    if exc.committed:
                        persistence_warning = f"; configuration durability warning: {exc}"
                    else:
                        try:
                            if previous_target_secret is not None:
                                secret_store(email, previous_target_secret)
                            else:
                                secret_clear(email)
                        finally:
                            self.config = old_config
                        raise
                except Exception:
                    try:
                        if previous_target_secret is not None:
                            secret_store(email, previous_target_secret)
                        else:
                            secret_clear(email)
                    finally:
                        self.config = old_config
                    raise
                cleanup_warning = ""
                if old_email and old_email != email:
                    try:
                        secret_clear(old_email)
                    except (RuntimeError, subprocess.SubprocessError) as exc:
                        cleanup_warning = f"; old credential could not be removed: {str(exc).strip()}"
                self.action_result(
                    True,
                    f"Connected to {len(self.devices)} JDownloader instance(s){cleanup_warning}{persistence_warning}",
                    request,
                )
                self.snapshot()
            except Exception as exc:
                self.disconnect()
                if "old_config" in locals():
                    self.config = old_config
                self.action_result(False, str(exc).strip() or "MyJDownloader login failed", request)
            finally:
                password = ""
            return

        if command == "forget":
            old_email = self.email
            self.disconnect()
            empty_config_committed = False
            try:
                old_config = dict(self.config)
                self.persist_config({})
                empty_config_committed = True
                secret_clear(old_email)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                rollback_error = ""
                rollback_succeeded = False
                try:
                    if "old_config" in locals():
                        self.persist_config(old_config)
                        rollback_succeeded = True
                except OSError as rollback_exc:
                    rollback_error = f"; configuration rollback failed: {rollback_exc}"
                message = str(exc).strip() or "Could not remove the MyJDownloader account"
                self.action_result(False, f"{message}{rollback_error}", request)
                self.config = old_config if rollback_succeeded or not empty_config_committed else {}
                self.snapshot()
                return
            self.config = {}
            cleanup_warning = ""
            try:
                CONFIG_FILE.unlink(missing_ok=True)
            except OSError as exc:
                cleanup_warning = f"; empty configuration file retained: {exc}"
            self.action_result(True, f"MyJDownloader account removed{cleanup_warning}", request)
            self.snapshot()
            return

        if command == "cnl_reject":
            item_id = text(request.get("id"))
            try:
                with self.inbox_lock:
                    before = len(self.inbox)
                    updated = [item for item in self.inbox if text(item.get("id")) != item_id]
                    removed = len(updated) != before
                    if removed:
                        try:
                            self.persist_inbox(updated)
                        except StateCommitError as exc:
                            if exc.committed:
                                self.inbox = updated
                            raise
                        self.inbox = updated
            except OSError as exc:
                self.action_result(False, f"Could not remove Click'n'Load request: {exc}", request)
                return
            self.action_result(removed, "Click'n'Load request removed" if removed else "Click'n'Load request not found", request)
            self.emit_cnl_state()
            return

        if command == "cnl_details":
            item_id = text(request.get("id"))
            current = next((entry for entry in self.inbox if text(entry.get("id")) == item_id), None)
            if current is None:
                self.action_result(False, "Click'n'Load request not found", request)
                return
            links = [str(value) for value in current.get("links", [])]
            emit({
                "type": "cnl_details",
                "id": item_id,
                "link_urls": links[:CNL_DETAIL_LINK_LIMIT],
                "hidden_link_count": max(0, len(links) - CNL_DETAIL_LINK_LIMIT),
            })
            return

        try:
            if self.jd is None:
                self.connect()
            if self.device is None:
                raise RuntimeError("No online JDownloader instance is available")

            if command == "select_device":
                device_id = text(request.get("device_id"))
                if not any(item["id"] == device_id for item in self.devices):
                    raise RuntimeError("The selected JDownloader instance is not online")
                candidate = self.jd.get_device(device_id=device_id)
                candidate.disable_direct_connection()
                updated_config = dict(self.config)
                updated_config["selected_device_id"] = device_id
                persistence_warning = ""
                try:
                    self.persist_config(updated_config)
                except StateCommitError as exc:
                    if exc.committed:
                        persistence_warning = f"; selection durability warning: {exc}"
                    else:
                        raise
                self.device = candidate
                self.active_device_id = device_id
                self.config = updated_config
                self.cached_downloads = []
                self.cached_grabber = []
                self.last_package_refresh = 0.0
                self.action_result(True, "JDownloader instance selected" + persistence_warning, request)
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
                self.action_result(True, f"Downloads: {action}", request)
            elif command == "force_download":
                ids = [str(value) for value in request.get("package_ids", [])]
                self.device.downloads.force_download([], ids)
                self.action_result(True, "Package started", request)
            elif command in ("add_links", "retry_add_links"):
                links = text(request.get("links")).strip()
                if not links:
                    raise RuntimeError("Paste at least one download link")
                autostart = request.get("autostart") is True
                retry_token = text(request.get("retry_token"))
                if command == "retry_add_links":
                    uncertain = self.uncertain_add_links or {}
                    valid_persisted_retry = bool(uncertain) \
                        and retry_token == text(uncertain.get("token")) \
                        and links == text(uncertain.get("links")) \
                        and autostart is (uncertain.get("autostart") is True)
                    if not valid_persisted_retry and request.get("duplicate_confirmed") is not True:
                        raise RuntimeError("The uncertain Add Links request changed; review it before submitting as new")
                elif self.uncertain_add_links is not None \
                        and links == text(self.uncertain_add_links.get("links")) \
                        and autostart is (self.uncertain_add_links.get("autostart") is True):
                    self.action_result(
                        False,
                        "The previous submission may already have reached JDownloader; use Submit again",
                        request,
                        uncertain=True,
                        retry_token=text(self.uncertain_add_links.get("token")),
                    )
                    return
                try:
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
                except Exception as exc:
                    retry_token = uuid.uuid4().hex
                    self.uncertain_add_links = {
                        "token": retry_token,
                        "links": links,
                        "autostart": autostart,
                    }
                    self.action_result(
                        False,
                        "Add Links outcome is uncertain: " + (str(exc).strip() or exc.__class__.__name__),
                        request,
                        uncertain=True,
                        retry_token=retry_token,
                    )
                    return
                self.uncertain_add_links = None
                self.action_result(True, "Links added" + (" and queued" if autostart else " to LinkGrabber"), request)
            elif command in ("cnl_accept", "cnl_retry"):
                self.submit_cnl(request, retry=command == "cnl_retry")
            elif command == "move_grabber":
                ids = [str(value) for value in request.get("package_ids", [])]
                self.device.linkgrabber.move_to_downloadlist([], ids)
                self.action_result(True, "Package moved to downloads", request)
            elif command == "rename_grabber":
                package_id = text(request.get("package_id")).strip()
                name = text(request.get("name")).strip()
                if not package_id or not name:
                    raise RuntimeError("Package ID and name are required")
                self.device.linkgrabber.rename_package(package_id, name)
                self.action_result(True, "LinkGrabber package renamed", request)
            elif command == "remove_downloads":
                ids = [str(value) for value in request.get("package_ids", [])]
                self.device.downloads.remove_links([], ids)
                self.action_result(True, "Download entry removed; files were kept", request)
            elif command == "remove_grabber":
                ids = [str(value) for value in request.get("package_ids", [])]
                self.device.linkgrabber.remove_links([], ids)
                self.action_result(True, "LinkGrabber entry removed", request)
            else:
                raise RuntimeError("Unknown command")

            time.sleep(0.25)
            self.snapshot(refresh=True)
            self.last_poll = time.monotonic()
        except Exception as exc:
            self.action_result(False, str(exc).strip() or "JDownloader action failed", request)

    def run(self) -> None:
        self.snapshot(refresh=True)
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
                    line = ""
                    if not isinstance(request, dict):
                        raise ValueError("request must be an object")
                    try:
                        self.handle(request)
                    finally:
                        request.pop("password", None)
                        request.clear()
                except (json.JSONDecodeError, ValueError) as exc:
                    line = ""
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
