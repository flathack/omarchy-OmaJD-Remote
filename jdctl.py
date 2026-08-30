#!/usr/bin/env python3
"""JSON-lines bridge between Omarchy's QML shell and MyJDownloader."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import myjdapi


APP_KEY = "https://github.com/flathack/omarchy-OmaJdownLoad"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omarchy" / "omajdownload"
CONFIG_FILE = CONFIG_DIR / "config.json"
POLL_SECONDS = 5.0


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
        self.jd: Any = None
        self.device: Any = None
        self.devices: list[dict[str, Any]] = []
        self.last_poll = 0.0
        self.last_error = ""

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
        self.last_poll = time.monotonic()
        while True:
            timeout = max(0.0, POLL_SECONDS - (time.monotonic() - self.last_poll))
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
