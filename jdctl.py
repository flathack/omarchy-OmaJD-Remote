#!/usr/bin/env python3
"""JSON-lines bridge between Omarchy's QML shell and MyJDownloader."""

from __future__ import annotations

import base64
import binascii
import contextlib
import contextvars
import json
import os
import queue
import re
import select
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import myjdapi
import requests
from Crypto.Cipher import AES


APP_KEY = "https://github.com/flathack/omarchy-OmaJD-Remote"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omarchy" / "omajdownload"
CONFIG_FILE = CONFIG_DIR / "config.json"
INBOX_FILE = CONFIG_DIR / "clicknload-inbox.json"
UNCERTAIN_ADD_LINKS_FILE = Path(
    os.environ.get(
        "OMAJDOWNLOAD_UNCERTAIN_ADD_LINKS_FILE",
        str(CONFIG_DIR / "uncertain-add-links.json"),
    )
)
POLL_SECONDS = 5.0
DEVICE_REFRESH_SECONDS = 15.0
PACKAGE_REFRESH_SECONDS = 30.0
PACKAGE_PAGE_SIZE = 60
PACKAGE_MODEL_LIMIT = 1000
DEVICE_MODEL_LIMIT = 128
REMOTE_RESPONSE_BYTE_LIMIT = 8 * 1024 * 1024
REMOTE_CALL_TIMEOUT = 20.0
REMOTE_OPERATION_TIMEOUT = 25.0
IPC_LINE_BYTE_LIMIT = 256 * 1024
IPC_OUTPUT_BYTE_LIMIT = 4 * 1024 * 1024
IPC_REQUEST_DRAIN_LIMIT = 4 * 1024 * 1024
IPC_REQUEST_READ_TIMEOUT = float(os.environ.get("OMAJDOWNLOAD_IPC_READ_TIMEOUT", "5.0"))
IPC_STRING_LIMIT = 4096
IPC_DISPLAY_STRING_LIMIT = 512
IPC_ID_LIMIT = 256
IPC_ID_LIST_LIMIT = 64
CONFIG_BYTE_LIMIT = 64 * 1024
CNL_HOST = "127.0.0.1"
CNL_PORT = int(os.environ.get("OMAJDOWNLOAD_CNL_PORT", "9666"))
CNL_BODY_LIMIT = 1024 * 1024
CNL_REQUEST_TIMEOUT = 5.0
CNL_MAX_WORKERS = 8
CNL_INBOX_LIMIT = 30
CNL_INBOX_BYTE_LIMIT = 4 * 1024 * 1024
CNL_REQUEST_LINK_LIMIT = 2000
CNL_INBOX_LINK_LIMIT = 5000
CNL_DETAIL_LINK_LIMIT = 200
CNL_EVENT_LIMIT = 32
CNL_EVENT_DRAIN_LIMIT = 8
CNL_SOURCE_LABEL_LIMIT = 256
CNL_LINK_STRING_LIMIT = 8192
CNL_PASSWORD_STRING_LIMIT = 1024
CNL_KEY_PATTERN = re.compile(r"return\s+['\"]([0-9a-fA-F]{32})['\"]\s*;?", re.IGNORECASE)
CNL_PENDING = "pending"
CNL_SUBMITTING = "submitting"
CNL_UNCERTAIN = "uncertain"
_REMOTE_OPERATION_DEADLINE: contextvars.ContextVar[tuple[float, str] | None] = contextvars.ContextVar(
    "remote_operation_deadline", default=None
)


class RemoteResponseTooLarge(RuntimeError):
    pass


class _BoundedRawResponse:
    """Count decoded response bytes before requests can materialize them."""

    def __init__(self, raw: Any, limit: int) -> None:
        self.raw = raw
        self.limit = limit
        self.count = 0

    def _account(self, chunk: bytes | None) -> bytes | None:
        if chunk:
            self.count += len(chunk)
            if self.count > self.limit:
                self.raw.close()
                raise RemoteResponseTooLarge(
                    f"MyJDownloader response exceeds {human_bytes(self.limit)}"
                )
        return chunk

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        return self._account(self.raw.read(*args, **kwargs)) or b""

    def stream(self, *args: Any, **kwargs: Any) -> Any:
        for chunk in self.raw.stream(*args, **kwargs):
            yield self._account(chunk)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.raw, name)


def install_http_bounds() -> None:
    """Give every myjdapi HTTP call a socket timeout and producer-side cap."""
    original = requests.sessions.Session.request
    if getattr(original, "_omajdownload_bounded", False):
        return

    def bounded_request(session: Any, method: str, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", (5.0, REMOTE_CALL_TIMEOUT))
        kwargs["stream"] = True
        response = original(session, method, url, **kwargs)
        declared = response.headers.get("Content-Length")
        if declared:
            try:
                if int(declared) > REMOTE_RESPONSE_BYTE_LIMIT:
                    response.close()
                    raise RemoteResponseTooLarge(
                        f"MyJDownloader response exceeds {human_bytes(REMOTE_RESPONSE_BYTE_LIMIT)}"
                    )
            except ValueError:
                response.close()
                raise RuntimeError("MyJDownloader returned an invalid Content-Length")
        response.raw = _BoundedRawResponse(response.raw, REMOTE_RESPONSE_BYTE_LIMIT)
        return response

    bounded_request._omajdownload_bounded = True  # type: ignore[attr-defined]
    requests.sessions.Session.request = bounded_request


_ACTIVE_DEADLINE: contextvars.ContextVar[tuple[float, str] | None] = contextvars.ContextVar(
    "active_deadline", default=None
)

_MIN_TIMER_SECONDS = 1e-3


def _floor_interval(seconds: float) -> float:
    """Round a timer interval up to a deliverable minimum.

    POSIX treats an ``it_value`` of zero as "disable the timer", so any
    positive interval smaller than the floor must be raised instead of
    silently cancelling the deadline it represents.
    """
    if 0 < seconds < _MIN_TIMER_SECONDS:
        return _MIN_TIMER_SECONDS
    return seconds


@contextlib.contextmanager
def absolute_deadline(seconds: float, operation: str) -> Any:
    """Interrupt a synchronous remote call at an absolute wall-clock deadline.

    Nested deadlines honor the earliest absolute deadline: an outer timer that
    would expire sooner than the requested inner deadline is preserved instead
    of being replaced. On exit, the previously installed timer and handler are
    restored so that an outer deadline that already elapsed while we were
    running still fires (with zero remaining time, which causes an immediate
    delivery on the next signal-check).
    """
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "setitimer")
        or not hasattr(signal, "getitimer")
    ):
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    outer_remaining = previous_timer[0]
    if outer_remaining and outer_remaining <= seconds:
        # The currently-running timer expires sooner than the requested inner
        # deadline. Honor it and inherit its operation label so the
        # TimeoutError reflects the trigger that actually fired.
        active_seconds = outer_remaining
        parent = _ACTIVE_DEADLINE.get()
        active_operation = parent[1] if parent is not None else operation
    else:
        active_seconds = seconds
        active_operation = operation
    active_seconds = _floor_interval(active_seconds)

    def expired(_signum: int, _frame: Any) -> None:
        raise TimeoutError(f"{active_operation} exceeded its {active_seconds:g}-second deadline")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, active_seconds)
    token = _ACTIVE_DEADLINE.set((active_seconds, active_operation))
    try:
        yield
    finally:
        elapsed = time.monotonic() - started
        # The outer timer (if any) has been advancing during our run. Replace
        # it with the *original* remaining time minus what already elapsed so
        # that an outer deadline that has now lapsed still surfaces
        # (remaining = 0).
        if outer_remaining:
            previous_remaining = outer_remaining - elapsed
        else:
            previous_remaining = 0.0
        # Cancel the timer we installed before restoring. setitimer(0) clears
        # any pending SIGALRM delivery POSIX might have queued while we ran.
        signal.setitimer(signal.ITIMER_REAL, 0)
        if previous_remaining > 0:
            signal.setitimer(signal.ITIMER_REAL, _floor_interval(previous_remaining), previous_timer[1])
        elif outer_remaining:
            # There was an outer timer and its deadline elapsed while we
            # were running (including the remaining == 0 boundary). Re-arm
            # with the deliverable minimum so the next signal-check
            # delivers the pending SIGALRM instead of silently dropping
            # the deadline. Without a previous timer nothing is re-armed.
            signal.setitimer(signal.ITIMER_REAL, _MIN_TIMER_SECONDS, previous_timer[1])
        signal.signal(signal.SIGALRM, previous_handler)
        _ACTIVE_DEADLINE.reset(token)


@contextlib.contextmanager
def remote_operation_deadline(seconds: float, operation: str) -> Any:
    """Set one absolute deadline shared by nested remote calls."""
    deadline = time.monotonic() + seconds
    previous = _REMOTE_OPERATION_DEADLINE.get()
    if previous is not None:
        deadline = min(deadline, previous[0])
        operation = previous[1]
    token = _REMOTE_OPERATION_DEADLINE.set((deadline, operation))
    try:
        yield
    finally:
        _REMOTE_OPERATION_DEADLINE.reset(token)


def remote_call(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    operation = "MyJDownloader request"
    seconds = REMOTE_CALL_TIMEOUT
    deadline = _REMOTE_OPERATION_DEADLINE.get()
    if deadline is not None:
        seconds = min(seconds, deadline[0] - time.monotonic())
        if seconds <= 0:
            raise TimeoutError(f"{deadline[1]} exceeded its deadline")
        operation = deadline[1]
    with absolute_deadline(seconds, operation):
        return call(*args, **kwargs)


def run_bounded_subprocess(
    arguments: list[str],
    *,
    input_text: str | None = None,
    timeout: float,
    output_limit: int = 16 * 1024,
) -> subprocess.CompletedProcess[str]:
    """Run a local helper with an absolute deadline and bounded captured output."""
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        if process.stdin is not None:
            encoded_input = input_text.encode("utf-8")
            if len(encoded_input) > 4096:
                raise ValueError("Secret input is too large")
            process.stdin.write(encoded_input)
            process.stdin.close()
        assert process.stdout is not None and process.stderr is not None
        descriptors = {process.stdout.fileno(): bytearray(), process.stderr.fileno(): bytearray()}
        deadline = time.monotonic() + timeout
        open_descriptors = set(descriptors)
        while open_descriptors:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(arguments, timeout)
            readable, _, _ = select.select(list(open_descriptors), [], [], min(0.25, remaining))
            for descriptor in readable:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    open_descriptors.remove(descriptor)
                    continue
                target = descriptors[descriptor]
                if len(target) < output_limit:
                    target.extend(chunk[: output_limit + 1 - len(target)])
        code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        if any(len(value) > output_limit for value in descriptors.values()):
            raise RuntimeError("Local helper output exceeded its limit")
        return subprocess.CompletedProcess(
            arguments,
            code,
            bytes(descriptors[process.stdout.fileno()]).decode("utf-8", "replace"),
            bytes(descriptors[process.stderr.fileno()]).decode("utf-8", "replace"),
        )
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            except ProcessLookupError:
                pass
        raise


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


def open_private_directory(path: Path, *, create: bool) -> int:
    """Open a directory without following any path component symlinks."""
    absolute = path.absolute()
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open("/", flags)
    try:
        for part in absolute.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=descriptor)
                child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        details = os.fstat(descriptor)
        if details.st_uid != os.getuid():
            raise PermissionError(f"State directory is not owned by uid {os.getuid()}: {path}")
        if details.st_mode & 0o077:
            os.fchmod(descriptor, 0o700)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _validate_state_file(directory_fd: int, name: str) -> os.stat_result:
    details = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if not stat.S_ISREG(details.st_mode):
        raise StateFileError(Path(name), "State path must be a regular file")
    if details.st_uid != os.getuid():
        raise StateFileError(Path(name), "State file is not owned by the current user")
    return details


def read_private_json(path: Path, byte_limit: int) -> Any:
    directory_fd = open_private_directory(path.parent, create=False)
    descriptor = -1
    try:
        details = _validate_state_file(directory_fd, path.name)
        if details.st_size > byte_limit:
            raise StateFileError(path, f"State file exceeds {human_bytes(byte_limit)}")
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid():
            raise StateFileError(path, "State file changed while it was being opened")
        if details.st_mode & 0o077:
            os.fchmod(descriptor, 0o600)
        chunks: list[bytes] = []
        remaining = byte_limit + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        if len(encoded) > byte_limit:
            raise StateFileError(path, f"State file exceeds {human_bytes(byte_limit)}")
        return json.loads(encoded.decode("utf-8"))
    except FileNotFoundError:
        raise
    except StateFileError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateFileError(path, f"Could not read state: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def atomic_write_json(path: Path, data: Any, *, ensure_ascii: bool = True) -> None:
    """Atomically and durably replace a private JSON state file."""
    encoded = (json.dumps(data, ensure_ascii=ensure_ascii, indent=2) + "\n").encode("utf-8")
    if len(encoded) > CNL_INBOX_BYTE_LIMIT:
        raise OSError(f"State exceeds {human_bytes(CNL_INBOX_BYTE_LIMIT)}")
    directory_fd = open_private_directory(path.parent, create=True)
    temp_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    fd = os.open(
        temp_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    replaced = False
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            _validate_state_file(directory_fd, path.name)
        except FileNotFoundError:
            pass
        os.replace(temp_name, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        replaced = True
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            raise StateCommitError(f"Could not synchronize {path.name} directory entry: {exc}", committed=True) from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if not replaced:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        os.close(directory_fd)


def emit(payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > IPC_OUTPUT_BYTE_LIMIT:
        raise RuntimeError(f"Helper response exceeds {human_bytes(IPC_OUTPUT_BYTE_LIMIT)}")
    print(encoded, flush=True)


def text(value: Any, fallback: str = "") -> str:
    return fallback if value is None else str(value)


def bounded_text(value: Any, limit: int = IPC_DISPLAY_STRING_LIMIT, fallback: str = "") -> str:
    result = text(value, fallback)
    return result if len(result) <= limit else result[: limit - 1] + "…"


def required_text(value: Any, field: str, limit: int = IPC_STRING_LIMIT, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if len(value) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    if not empty and not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


def remote_identifier(value: Any, field: str) -> str:
    """Canonicalize a remote identifier without changing its identity."""
    if value in (None, ""):
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise RuntimeError(f"MyJDownloader returned an invalid {field}")
    result = str(value)
    if len(result) > IPC_ID_LIMIT:
        raise RuntimeError(f"MyJDownloader returned an oversized {field}")
    return result


def id_list(value: Any, field: str = "package_ids") -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty array")
    if len(value) > IPC_ID_LIST_LIMIT:
        raise ValueError(f"{field} exceeds {IPC_ID_LIST_LIMIT} entries")
    return [required_text(item, field, IPC_ID_LIMIT).strip() for item in value]


def validate_request(request: dict[str, Any]) -> None:
    command = required_text(request.get("command"), "command", 64).strip()
    common = {"command", "request_id"}
    schemas: dict[str, set[str]] = {
        "refresh": common,
        "configure": common | {"email", "password"},
        "set_connection_enabled": common | {"enabled"},
        "forget": common,
        "select_device": common | {"device_id"},
        "control": common | {"action"},
        "force_download": common | {"package_ids"},
        "add_links": common | {"links", "autostart", "retry_token", "duplicate_confirmed"},
        "retry_add_links": common | {"links", "autostart", "retry_token", "duplicate_confirmed"},
        "move_grabber": common | {"package_ids"},
        "rename_grabber": common | {"package_id", "name"},
        "remove_downloads": common | {"package_ids"},
        "remove_grabber": common | {"package_ids"},
        "cnl_accept": common | {"id", "autostart"},
        "cnl_retry": common | {"id", "autostart"},
        "cnl_reject": common | {"id"},
        "cnl_details": common | {"id"},
    }
    allowed = schemas.get(command)
    if allowed is None:
        raise ValueError("Unknown command")
    unexpected = set(request) - allowed
    if unexpected:
        raise ValueError(f"Unexpected request field: {sorted(unexpected)[0]}")
    if "request_id" in request:
        required_text(request["request_id"], "request_id", IPC_ID_LIMIT, empty=True)
    if command == "configure":
        required_text(request.get("email"), "email", 320)
        required_text(request.get("password"), "password", 1024)
    elif command == "set_connection_enabled":
        if type(request.get("enabled")) is not bool:
            raise ValueError("enabled must be a boolean")
    elif command == "select_device":
        required_text(request.get("device_id"), "device_id", IPC_ID_LIMIT)
    elif command == "control":
        if request.get("action") not in {"start", "stop", "pause", "resume"}:
            raise ValueError("Unknown download control action")
    elif command in {"force_download", "move_grabber", "remove_downloads", "remove_grabber"}:
        id_list(request.get("package_ids"))
    elif command in {"add_links", "retry_add_links"}:
        required_text(request.get("links"), "links", IPC_LINE_BYTE_LIMIT // 2)
        if "retry_token" in request:
            required_text(request["retry_token"], "retry_token", IPC_ID_LIMIT, empty=True)
        for field in ("autostart", "duplicate_confirmed"):
            if field in request and type(request[field]) is not bool:
                raise ValueError(f"{field} must be a boolean")
    elif command == "rename_grabber":
        required_text(request.get("package_id"), "package_id", IPC_ID_LIMIT)
        required_text(request.get("name"), "name", IPC_DISPLAY_STRING_LIMIT)
    elif command in {"cnl_accept", "cnl_retry", "cnl_reject", "cnl_details"}:
        required_text(request.get("id"), "id", IPC_ID_LIMIT)
        if "autostart" in request and type(request["autostart"]) is not bool:
            raise ValueError("autostart must be a boolean")


def number(value: Any) -> int:
    """Coerce a remote numeric into a bounded integer, treating bad input as 0.

    NaN, +/-inf, and the like can show up in malformed remote responses
    (the API occasionally reports ``Infinity`` for unrestrained speeds).
    We treat every non-finite or out-of-range numeric as 0 so the snapshot
    processing pipeline never aborts on a single bad value.
    """
    if value is None or value == "":
        return 0
    try:
        converted = int(value)
    except (TypeError, ValueError):
        # Coerce bools (already coerced above by int(True)==1) and floats
        # that may have lost precision on the way to JSON.
        try:
            converted = int(float(value))
        except (TypeError, ValueError, OverflowError):
            return 0
    except OverflowError:
        return 0
    return max(-(2**63), min(2**63 - 1, converted))


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
        data = read_private_json(CONFIG_FILE, CONFIG_BYTE_LIMIT)
    except FileNotFoundError:
        return {}
    except (OSError, StateFileError, json.JSONDecodeError) as exc:
        raise StateFileError(CONFIG_FILE, f"Could not read configuration: {exc}") from exc
    if not isinstance(data, dict):
        raise StateFileError(CONFIG_FILE, "Configuration must contain a JSON object")
    if set(data) - {"email", "selected_device_id", "connection_enabled"}:
        raise StateFileError(CONFIG_FILE, "Configuration contains unknown fields")
    try:
        if "email" in data:
            required_text(data["email"], "email", 320)
        if "selected_device_id" in data:
            required_text(data["selected_device_id"], "selected_device_id", IPC_ID_LIMIT)
        if "connection_enabled" in data and type(data["connection_enabled"]) is not bool:
            raise ValueError("connection_enabled must be a boolean")
    except ValueError as exc:
        raise StateFileError(CONFIG_FILE, str(exc)) from exc
    return data


def write_config(data: dict[str, Any]) -> None:
    if len(json.dumps(data).encode("utf-8")) > CONFIG_BYTE_LIMIT:
        raise OSError(f"Configuration exceeds {human_bytes(CONFIG_BYTE_LIMIT)}")
    atomic_write_json(CONFIG_FILE, data)


def read_inbox() -> list[dict[str, Any]]:
    try:
        data = read_private_json(INBOX_FILE, CNL_INBOX_BYTE_LIMIT)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise StateFileError(INBOX_FILE, f"Could not read Click'n'Load inbox: {exc}") from exc
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise StateFileError(INBOX_FILE, "Click'n'Load inbox must contain a JSON array of objects")
    if len(data) > CNL_INBOX_LIMIT:
        raise StateFileError(INBOX_FILE, f"Click'n'Load inbox exceeds {CNL_INBOX_LIMIT} requests")
    total_links = 0
    try:
        for item in data:
            links = item.get("links", [])
            passwords = item.get("passwords", [])
            if not isinstance(links, list) or not isinstance(passwords, list):
                raise ValueError("Click'n'Load links and passwords must be arrays")
            if len(links) > CNL_REQUEST_LINK_LIMIT or len(passwords) > CNL_REQUEST_LINK_LIMIT:
                raise ValueError("Click'n'Load item contains too many values")
            total_links += len(links)
            for value in links:
                required_text(value, "Click'n'Load link", CNL_LINK_STRING_LIMIT)
            for value in passwords:
                required_text(value, "Click'n'Load password", CNL_PASSWORD_STRING_LIMIT, empty=True)
            for field in ("id", "status"):
                if field in item:
                    required_text(item[field], field, IPC_ID_LIMIT, empty=True)
            for field in ("claimed_source", "source", "origin", "received_at"):
                if field in item:
                    required_text(item[field], field, IPC_DISPLAY_STRING_LIMIT, empty=True)
        if total_links > CNL_INBOX_LINK_LIMIT:
            raise ValueError(f"Click'n'Load inbox exceeds {CNL_INBOX_LINK_LIMIT} links")
    except ValueError as exc:
        raise StateFileError(INBOX_FILE, str(exc)) from exc
    return data


def write_inbox(data: list[dict[str, Any]]) -> None:
    encoded_size = len((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    if encoded_size > CNL_INBOX_BYTE_LIMIT:
        raise OSError(f"Click'n'Load inbox exceeds {human_bytes(CNL_INBOX_BYTE_LIMIT)}")
    atomic_write_json(INBOX_FILE, data, ensure_ascii=False)


def read_uncertain_add_links() -> dict[str, Any] | None:
    """Recover an Add Links request whose remote outcome was uncertain.

    The helper persists the request's links, autostart flag, and a retry
    token the moment a remote call fails in an ambiguous way so the
    next helper start can surface the same duplicate-risk warning and
    acceptance token that the previous session established.
    """
    try:
        data = read_private_json(UNCERTAIN_ADD_LINKS_FILE, CNL_INBOX_BYTE_LIMIT)
    except FileNotFoundError:
        return None
    except (OSError, StateFileError, json.JSONDecodeError) as exc:
        raise StateFileError(UNCERTAIN_ADD_LINKS_FILE, f"Could not read uncertain Add Links state: {exc}") from exc
    if not isinstance(data, dict):
        raise StateFileError(UNCERTAIN_ADD_LINKS_FILE, "Uncertain Add Links state must contain a JSON object")
    try:
        token = required_text(data.get("token"), "token", IPC_ID_LIMIT)
        links = required_text(data.get("links"), "links", IPC_LINE_BYTE_LIMIT // 2)
        autostart = data.get("autostart")
        email = required_text(data.get("email"), "email", 320, empty=True)
        device_id = required_text(data.get("device_id"), "device_id", IPC_ID_LIMIT, empty=True)
    except ValueError as exc:
        raise StateFileError(UNCERTAIN_ADD_LINKS_FILE, str(exc)) from exc
    if not token or not links:
        raise StateFileError(UNCERTAIN_ADD_LINKS_FILE, "Uncertain Add Links state is missing required fields")
    if not isinstance(autostart, bool):
        raise StateFileError(UNCERTAIN_ADD_LINKS_FILE, "Uncertain Add Links autostart must be a boolean")
    record = {"token": token, "links": links, "autostart": autostart}
    if email:
        record["email"] = email
    if device_id:
        record["device_id"] = device_id
    return record


def write_uncertain_add_links(record: dict[str, Any]) -> None:
    """Persist an uncertain Add Links record so it survives restarts."""
    encoded = json.dumps(record, ensure_ascii=True, indent=2)
    if len(encoded.encode("utf-8")) + 16 > CNL_INBOX_BYTE_LIMIT:
        raise OSError(f"Uncertain Add Links state exceeds {human_bytes(CNL_INBOX_BYTE_LIMIT)}")
    atomic_write_json(UNCERTAIN_ADD_LINKS_FILE, record)


def clear_uncertain_add_links() -> None:
    """Remove the persisted uncertain Add Links record, if any."""
    try:
        directory_fd = open_private_directory(UNCERTAIN_ADD_LINKS_FILE.parent, create=True)
    except (FileNotFoundError, OSError):
        return
    try:
        try:
            os.unlink(UNCERTAIN_ADD_LINKS_FILE.name, dir_fd=directory_fd)
        except FileNotFoundError:
            return
    finally:
        os.close(directory_fd)


def quarantine_state_file(path: Path) -> Path:
    suffix = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    backup = path.with_name(f"{path.name}.corrupt-{suffix}")
    directory_fd = open_private_directory(path.parent, create=False)
    try:
        _validate_state_file(directory_fd, path.name)
        os.rename(path.name, backup.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return backup


def unlink_state_file(path: Path) -> None:
    directory_fd = open_private_directory(path.parent, create=False)
    try:
        try:
            _validate_state_file(directory_fd, path.name)
        except FileNotFoundError:
            return
        os.unlink(path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def split_lines(
    value: str,
    *,
    item_limit: int = CNL_REQUEST_LINK_LIMIT,
    string_limit: int = CNL_LINK_STRING_LIMIT,
) -> list[str]:
    rows: list[str] = []
    for source in value.replace("\r", "\n").split("\n"):
        line = source.strip()
        if not line:
            continue
        if len(line) > string_limit:
            raise ValueError(f"Click'n'Load field exceeds {string_limit} characters")
        rows.append(line)
        if len(rows) > item_limit:
            raise OverflowError(f"Click'n'Load request has more than {item_limit} values")
    return rows


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
    passwords = split_lines(
        (fields.get("passwords") or [""])[0],
        item_limit=CNL_REQUEST_LINK_LIMIT,
        string_limit=CNL_PASSWORD_STRING_LIMIT,
    )
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
        return bounded_text(f"{parsed.scheme}://{parsed.hostname}{port}", IPC_DISPLAY_STRING_LIMIT)
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


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    request_queue_size = CNL_MAX_WORKERS

    def __init__(self, *args: Any, max_workers: int = CNL_MAX_WORKERS, **kwargs: Any) -> None:
        self._worker_slots = threading.BoundedSemaphore(max_workers)
        super().__init__(*args, **kwargs)

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self._worker_slots.acquire(blocking=False):
            try:
                request.settimeout(0.25)
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Connection: close\r\nContent-Length: 0\r\n\r\n"
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


class ClickNLoadServer:
    def __init__(
        self,
        admit: Callable[[dict[str, Any]], None],
        events: queue.Queue[dict[str, Any]],
        host: str = CNL_HOST,
        port: int = CNL_PORT,
        max_workers: int = CNL_MAX_WORKERS,
    ) -> None:
        self.admit = admit
        self.events = events
        self.host = host
        self.requested_port = port
        if max_workers < 1 or max_workers > CNL_MAX_WORKERS:
            raise ValueError(f"max_workers must be between 1 and {CNL_MAX_WORKERS}")
        self.max_workers = max_workers
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
        # Repeated start() calls must be harmless. A previous listener
        # keeps holding its socket until stop() shuts it down, and the
        # caller may legitimately retry after a transient bind failure.
        if self.httpd is not None:
            return
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

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(CNL_REQUEST_TIMEOUT)

            def read_body(self, size: int) -> bytes:
                deadline = time.monotonic() + CNL_REQUEST_TIMEOUT
                remaining = size
                chunks: list[bytes] = []
                while remaining:
                    available = deadline - time.monotonic()
                    if available <= 0:
                        raise TimeoutError("Click'n'Load request timed out")
                    self.connection.settimeout(min(CNL_REQUEST_TIMEOUT, available))
                    chunk = self.rfile.read1(min(65536, remaining))
                    if not chunk:
                        raise ValueError("Click'n'Load request ended before its declared length")
                    chunks.append(chunk)
                    remaining -= len(chunk)
                return b"".join(chunks)

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
                    raw = self.read_body(size).decode("utf-8")
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
                except TimeoutError as exc:
                    report_error(str(exc))
                    self.reply(408, str(exc))
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
            self.httpd = BoundedThreadingHTTPServer(
                (self.host, self.requested_port), Handler, max_workers=self.max_workers
            )
            self.httpd.daemon_threads = True
            thread = threading.Thread(target=self.httpd.serve_forever, name="omajdownload-cnl", daemon=True)
            thread.start()
        except OSError as exc:
            # A bind failure must not lose the handle to an already-running
            # listener. ``httpd`` is still None here because start() refuses
            # to start a second listener, so the assignment below only ever
            # affects the local variable; clear ``self.error`` so the
            # caller can see why start() came back without one.
            self.error = str(exc)
        else:
            # The listener is up: drop any stale bind-failure message from
            # a previous failed attempt so the UI stops showing an error
            # that no longer applies.
            self.error = ""

    def stop(self) -> None:
        if self.httpd is None:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = None


def secret_lookup(email: str) -> str | None:
    if not email:
        return None
    required_text(email, "email", 320)
    result = run_bounded_subprocess(
        ["secret-tool", "lookup", "omarchy-plugin", "omajdownload", "account", email],
        timeout=10,
    )
    if result.returncode == 0:
        return result.stdout.rstrip("\n")
    if result.returncode == 1 and not result.stderr.strip():
        return None
    raise RuntimeError(result.stderr.strip() or f"Could not read password from the keyring (exit {result.returncode})")


def secret_store(email: str, password: str) -> None:
    required_text(email, "email", 320)
    required_text(password, "password", 1024)
    result = run_bounded_subprocess(
        [
            "secret-tool",
            "store",
            f"--label=Omarchy JDownloader ({email})",
            "omarchy-plugin",
            "omajdownload",
            "account",
            email,
        ],
        input_text=password,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not store password in the keyring")


def secret_clear(email: str) -> None:
    if not email:
        return
    required_text(email, "email", 320)
    result = run_bounded_subprocess(
        ["secret-tool", "clear", "omarchy-plugin", "omajdownload", "account", email],
        timeout=10,
    )
    if result.returncode != 0 and result.stderr.strip():
        raise RuntimeError(result.stderr.strip() or "Could not remove password from the keyring")


def query_all_packages(query: Any, fields: dict[str, Any]) -> PackageQueryResult:
    if _REMOTE_OPERATION_DEADLINE.get() is not None:
        return _query_all_packages(query, fields)
    with remote_operation_deadline(REMOTE_OPERATION_TIMEOUT, "MyJDownloader package query"):
        return _query_all_packages(query, fields)


def _query_all_packages(query: Any, fields: dict[str, Any]) -> PackageQueryResult:
    rows: list[dict[str, Any]] = []
    seen_pages: set[tuple[str, ...]] = set()
    page = 0
    while len(rows) < PACKAGE_MODEL_LIMIT:
        params = dict(fields)
        params["maxResults"] = PACKAGE_PAGE_SIZE
        params["startAt"] = page * PACKAGE_PAGE_SIZE
        batch = remote_call(query, [params]) or []
        if not isinstance(batch, list):
            raise RuntimeError("JDownloader returned an invalid package list")
        if len(batch) > PACKAGE_PAGE_SIZE:
            raise RuntimeError("JDownloader returned an oversized package page")
        signature = tuple(package_uuid(row) for row in batch if isinstance(row, dict))
        if batch and signature in seen_pages:
            raise RuntimeError("JDownloader package pagination repeated a page")
        seen_pages.add(signature)
        rows.extend(row for row in batch if isinstance(row, dict))
        if len(rows) > PACKAGE_MODEL_LIMIT:
            return PackageQueryResult(rows[:PACKAGE_MODEL_LIMIT], truncated=True)
        if len(batch) < PACKAGE_PAGE_SIZE:
            return PackageQueryResult(rows)
        page += 1

    params = dict(fields)
    params["maxResults"] = 1
    params["startAt"] = len(rows)
    remainder = remote_call(query, [params]) or []
    if not isinstance(remainder, list):
        raise RuntimeError("JDownloader returned an invalid package list")
    if len(remainder) > 1:
        raise RuntimeError("JDownloader returned an oversized package remainder")
    return PackageQueryResult(rows[:PACKAGE_MODEL_LIMIT], truncated=bool(remainder))


def package_uuid(row: dict[str, Any]) -> str:
    for key in ("uuid", "packageUUID", "packageUUIDs", "id"):
        value = row.get(key)
        if isinstance(value, list):
            value = value[0] if value else ""
        if value not in (None, ""):
            return remote_identifier(value, "package identifier")
    return ""


def normalize_package(row: Any, kind: str) -> dict[str, Any]:
    if not isinstance(row, dict):
        row = {}
    loaded = number(row.get("bytesLoaded"))
    total = number(row.get("bytesTotal"))
    speed = number(row.get("speed"))
    progress = round((loaded / total) * 100) if total > 0 else 0
    name = bounded_text(
        row.get("name") or row.get("packageName") or row.get("comment"),
        IPC_DISPLAY_STRING_LIMIT,
        "Unnamed package",
    )
    status = bounded_text(row.get("status") or row.get("availability"), IPC_DISPLAY_STRING_LIMIT)
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
        install_http_bounds()
        self.state_warnings: list[str] = []
        self.config_write_blocked = False
        self.inbox_write_blocked = False
        self.config = self.load_config_state()
        self.inbox = self.load_inbox_state()
        self.uncertain_add_links = self.load_uncertain_add_links_state()
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

    def load_uncertain_add_links_state(self) -> dict[str, Any] | None:
        try:
            return read_uncertain_add_links()
        except StateFileError as exc:
            try:
                backup = quarantine_state_file(exc.path)
                self.state_warnings.append(f"{exc}; preserved as {backup.name}")
            except OSError as backup_error:
                self.state_warnings.append(f"{exc}; could not preserve it: {backup_error}")
            return None

    def _uncertain_add_links_matches(self, record: dict[str, Any]) -> bool:
        """Whether an uncertain Add Links record targets this session.

        Records written before the account/device binding was introduced
        carry neither ``email`` nor ``device_id``; they still match so the
        duplicate-risk warning is preserved. Records that do carry the
        fields must match the currently configured account and selected
        device, otherwise a retry would replay an ambiguous submission
        against a different destination.
        """
        if not record:
            return False
        recorded_email = text(record.get("email"))
        recorded_device = text(record.get("device_id"))
        if recorded_email and recorded_email != self.email:
            return False
        if recorded_device and recorded_device != self.active_device_id:
            return False
        return True

    def _clear_uncertain_add_links(self) -> None:
        """Drop the in-memory and persisted uncertain Add Links state."""
        self.uncertain_add_links = None
        try:
            clear_uncertain_add_links()
        except OSError as exc:
            self.state_warnings.append(f"Uncertain Add Links state could not be cleared: {exc}")

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

    @property
    def connection_enabled(self) -> bool:
        return self.config.get("connection_enabled") is not False

    def refresh_devices(self, update: bool = True) -> None:
        if self.jd is None:
            self.devices = []
            self.device = None
            return
        if update:
            remote_call(self.jd.update_devices)
        rows = remote_call(self.jd.list_devices) or []
        if not isinstance(rows, list):
            raise RuntimeError("MyJDownloader returned an invalid device list")
        if len(rows) > DEVICE_MODEL_LIMIT:
            raise RuntimeError(f"MyJDownloader returned more than {DEVICE_MODEL_LIMIT} devices")
        if any(not isinstance(item, dict) for item in rows):
            raise RuntimeError("MyJDownloader returned an invalid device entry")
        self.devices = [
            {
                "id": remote_identifier(item.get("id"), "device identifier"),
                "name": bounded_text(item.get("name"), IPC_DISPLAY_STRING_LIMIT, "JDownloader"),
                "type": bounded_text(item.get("type"), 64),
            }
            for item in rows
        ]
        if any(not item["id"] for item in self.devices):
            raise RuntimeError("MyJDownloader returned a device without an ID")
        online_ids = {item["id"] for item in self.devices}
        current_id = text(getattr(self.device, "device_id", ""))
        preferred = self.selected_id
        target = preferred if preferred in online_ids else (current_id if current_id in online_ids else "")
        if not target and self.devices:
            target = self.devices[0]["id"]
        if not target:
            self.device = None
        elif self.device is None or current_id != target:
            self.device = remote_call(self.jd.get_device, device_id=target)
            remote_call(self.device.disable_direct_connection)
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
        remote_call(jd.connect, account, credential)
        self.jd = jd
        if self.email and self.email != account:
            self.config.pop("selected_device_id", None)
        self.config["email"] = account
        self.refresh_devices(update=False)
        self.last_error = ""

    def disconnect(self) -> None:
        try:
            if self.jd is not None and remote_call(self.jd.is_connected):
                remote_call(self.jd.disconnect)
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
            total_bytes = len((json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
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
        with remote_operation_deadline(REMOTE_OPERATION_TIMEOUT, "MyJDownloader snapshot"):
            self._snapshot(refresh)

    def _snapshot(self, refresh: bool = False) -> None:
        configured = bool(self.email)
        if not configured:
            emit({
                "type": "snapshot",
                "configured": False,
                "connection_enabled": True,
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

        if not self.connection_enabled:
            self.disconnect()
            emit({
                "type": "snapshot",
                "configured": True,
                "connection_enabled": False,
                "connected": False,
                "devices": [],
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
                    "connection_enabled": True,
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
            controller = bounded_text(
                remote_call(self.device.downloadcontroller.get_current_state),
                IPC_DISPLAY_STRING_LIMIT,
                "IDLE",
            )
            speed = number(remote_call(self.device.downloadcontroller.get_speed_in_bytes))
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
                    self.download_error = bounded_text(
                        str(exc).strip() or "Could not refresh downloads", IPC_DISPLAY_STRING_LIMIT
                    )
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
                    self.grabber_error = bounded_text(
                        str(exc).strip() or "Could not refresh LinkGrabber", IPC_DISPLAY_STRING_LIMIT
                    )
                self.last_package_refresh = time.monotonic()
            active = sum(1 for item in self.cached_downloads if item["running"])
            selected_name = next(
                (item["name"] for item in self.devices if item["id"] == self.active_device_id),
                "JDownloader",
            )
            emit({
                "type": "snapshot",
                "configured": True,
                "connection_enabled": True,
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
            self.last_error = bounded_text(
                str(exc).strip() or exc.__class__.__name__, IPC_DISPLAY_STRING_LIMIT
            )
            display_error = " · ".join(value for value in (self.last_error, self.state_warning) if value)
            self.disconnect()
            emit({
                "type": "snapshot",
                "configured": True,
                "connection_enabled": True,
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
            "message": bounded_text(message, IPC_STRING_LIMIT),
            "command": bounded_text(source.get("command"), 64),
            "request_id": bounded_text(source.get("request_id"), IPC_ID_LIMIT),
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
            remote_call(self.device.linkgrabber.add_links, [{
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
        try:
            validate_request(request)
        except ValueError as exc:
            request.pop("password", None)
            self.action_result(False, f"Invalid helper request: {exc}", request)
            return
        command = request["command"].strip()
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
                self.config["connection_enabled"] = True
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

        if command == "set_connection_enabled":
            if not self.email:
                self.action_result(False, "Connect a MyJDownloader account first", request)
                return
            enabled = request.get("enabled") is True
            updated_config = dict(self.config)
            updated_config["connection_enabled"] = enabled
            persistence_warning = ""
            try:
                self.persist_config(updated_config)
            except StateCommitError as exc:
                if exc.committed:
                    persistence_warning = f"; connection setting durability warning: {exc}"
                else:
                    self.action_result(False, f"Could not save connection setting: {exc}", request)
                    return
            except OSError as exc:
                self.action_result(False, f"Could not save connection setting: {exc}", request)
                return
            self.config = updated_config
            if not enabled:
                self.disconnect()
                self.action_result(True, "MyJDownloader connection switched off" + persistence_warning, request)
                self.snapshot()
                return
            try:
                self.connect()
                self.action_result(True, "MyJDownloader connection switched on" + persistence_warning, request)
            except Exception as exc:
                self.disconnect()
                message = str(exc).strip() or "MyJDownloader connection failed"
                self.action_result(False, message + persistence_warning, request)
            self.snapshot()
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
            self._clear_uncertain_add_links()
            cleanup_warning = ""
            try:
                unlink_state_file(CONFIG_FILE)
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
                "request_id": bounded_text(request.get("request_id"), IPC_ID_LIMIT),
                "link_urls": links[:CNL_DETAIL_LINK_LIMIT],
                "hidden_link_count": max(0, len(links) - CNL_DETAIL_LINK_LIMIT),
            })
            return

        try:
            if not self.connection_enabled:
                raise RuntimeError("MyJDownloader connection is off; switch it on first")
            if self.jd is None:
                self.connect()
            if self.device is None:
                raise RuntimeError("No online JDownloader instance is available")

            if command == "select_device":
                device_id = text(request.get("device_id"))
                if not any(item["id"] == device_id for item in self.devices):
                    raise RuntimeError("The selected JDownloader instance is not online")
                candidate = remote_call(self.jd.get_device, device_id=device_id)
                remote_call(candidate.disable_direct_connection)
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
                if not self._uncertain_add_links_matches(self.uncertain_add_links or {}):
                    # The pending ambiguous submission belonged to another
                    # device; keeping it would replay the duplicate-risk
                    # warning against the wrong destination.
                    self._clear_uncertain_add_links()
                self.action_result(True, "JDownloader instance selected" + persistence_warning, request)
            elif command == "control":
                action = text(request.get("action"))
                if action == "start":
                    remote_call(self.device.downloadcontroller.start_downloads)
                elif action == "stop":
                    remote_call(self.device.downloadcontroller.stop_downloads)
                elif action == "pause":
                    remote_call(self.device.downloadcontroller.pause_downloads, True)
                elif action == "resume":
                    remote_call(self.device.downloadcontroller.pause_downloads, False)
                else:
                    raise RuntimeError("Unknown download control action")
                self.action_result(True, f"Downloads: {action}", request)
            elif command == "force_download":
                ids = id_list(request.get("package_ids"))
                remote_call(self.device.downloads.force_download, [], ids)
                self.action_result(True, "Package started", request)
            elif command in ("add_links", "retry_add_links"):
                links = text(request.get("links")).strip()
                if not links:
                    raise RuntimeError("Paste at least one download link")
                autostart = request.get("autostart") is True
                retry_token = text(request.get("retry_token"))
                if command == "retry_add_links":
                    uncertain = self.uncertain_add_links or {}
                    valid_persisted_retry = self._uncertain_add_links_matches(uncertain) \
                        and retry_token == text(uncertain.get("token")) \
                        and links == text(uncertain.get("links")) \
                        and autostart is (uncertain.get("autostart") is True)
                    if not valid_persisted_retry and request.get("duplicate_confirmed") is not True:
                        raise RuntimeError("The uncertain Add Links request changed; review it before submitting as new")
                elif self._uncertain_add_links_matches(self.uncertain_add_links or {}) \
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
                    remote_call(self.device.linkgrabber.add_links, [{
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
                        # Bind the record to the account/device that the
                        # ambiguous call targeted, so a restart (or a
                        # device switch) can never replay the duplicate
                        # risk against a different destination.
                        "email": self.email,
                        "device_id": self.active_device_id,
                    }
                    try:
                        write_uncertain_add_links(self.uncertain_add_links)
                    except OSError as write_exc:
                        self.state_warnings.append(
                            f"Uncertain Add Links state could not be persisted: {write_exc}; "
                            "duplicate-risk warning will be lost across restarts"
                        )
                    self.action_result(
                        False,
                        "Add Links outcome is uncertain: " + (str(exc).strip() or exc.__class__.__name__),
                        request,
                        uncertain=True,
                        retry_token=retry_token,
                    )
                    return
                self.uncertain_add_links = None
                clear_uncertain_add_links()
                self.action_result(True, "Links added" + (" and queued" if autostart else " to LinkGrabber"), request)
            elif command in ("cnl_accept", "cnl_retry"):
                self.submit_cnl(request, retry=command == "cnl_retry")
            elif command == "move_grabber":
                ids = id_list(request.get("package_ids"))
                remote_call(self.device.linkgrabber.move_to_downloadlist, [], ids)
                self.action_result(True, "Package moved to downloads", request)
            elif command == "rename_grabber":
                package_id = text(request.get("package_id")).strip()
                name = text(request.get("name")).strip()
                if not package_id or not name:
                    raise RuntimeError("Package ID and name are required")
                remote_call(self.device.linkgrabber.rename_package, package_id, name)
                self.action_result(True, "LinkGrabber package renamed", request)
            elif command == "remove_downloads":
                ids = id_list(request.get("package_ids"))
                remote_call(self.device.downloads.remove_links, [], ids)
                self.action_result(True, "Download entry removed; files were kept", request)
            elif command == "remove_grabber":
                ids = id_list(request.get("package_ids"))
                remote_call(self.device.linkgrabber.remove_links, [], ids)
                self.action_result(True, "LinkGrabber entry removed", request)
            else:
                raise RuntimeError("Unknown command")

            time.sleep(0.25)
            self.snapshot(refresh=True)
            self.last_poll = time.monotonic()
        except Exception as exc:
            self.action_result(False, str(exc).strip() or "JDownloader action failed", request)

    def _read_ipc_line(self) -> bytes:
        """Read one newline-terminated IPC line, bounded in size and time.

        A blocking ``readline`` would let a broken or malicious producer
        that trickles bytes without a newline stall the daemon
        indefinitely. Both the initial read and the drain use a
        select-driven deadline instead, so the poll loop, the
        Click'n'Load event drain, and further IPC stay responsive no
        matter how the caller behaves.
        """
        buffer = sys.stdin.buffer
        chunks: list[bytes] = []
        total = 0
        deadline = time.monotonic() + IPC_REQUEST_READ_TIMEOUT
        while total <= IPC_LINE_BYTE_LIMIT:
            available = deadline - time.monotonic()
            if available <= 0:
                break
            readable, _, _ = select.select([buffer], [], [], min(0.25, available))
            if not readable:
                continue
            chunk = buffer.read1(IPC_LINE_BYTE_LIMIT + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if chunk.endswith(b"\n"):
                break
        return b"".join(chunks)

    def _drain_oversized_request(self, encoded: bytes) -> None:
        """Consume the remainder of an oversized stdin line.

        ``_read_ipc_line`` returns as soon as a newline is seen or its
        byte/time budget is exhausted, so when the framing is broken the
        rest of the malformed line sits in the buffer; we read it here up
        to a bounded cap and a bounded wall-clock window so the next
        request can be parsed without inherited garbage. Anything past
        the cap is left for the upstream caller to deal with, since the
        line itself is already invalid.
        """
        if encoded.endswith(b"\n") or not encoded:
            return
        deadline = time.monotonic() + IPC_REQUEST_READ_TIMEOUT
        remaining = IPC_REQUEST_DRAIN_LIMIT - len(encoded)
        buffer = sys.stdin.buffer
        while remaining > 0:
            available = deadline - time.monotonic()
            if available <= 0:
                return
            readable, _, _ = select.select([buffer], [], [], min(0.25, available))
            if not readable:
                continue
            chunk = buffer.read1(min(IPC_LINE_BYTE_LIMIT, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            if chunk.endswith(b"\n"):
                return

    def run(self) -> None:
        try:
            self.snapshot(refresh=True)
            self.emit_cnl_state()
            self.last_poll = time.monotonic()
            while True:
                self.drain_cnl_events()
                timeout = min(0.25, max(0.0, POLL_SECONDS - (time.monotonic() - self.last_poll)))
                readable, _, _ = select.select([sys.stdin.buffer], [], [], timeout)
                if readable:
                    encoded = self._read_ipc_line()
                    if encoded == b"":
                        return
                    if len(encoded) > IPC_LINE_BYTE_LIMIT or not encoded.endswith(b"\n"):
                        # Reject this single request, then drain the rest
                        # of the malformed line so the next newline in
                        # stdin re-aligns the daemon with the caller's
                        # framing. Closing the loop would otherwise
                        # restart the helper into a crash-retry pause.
                        self._drain_oversized_request(encoded)
                        self.action_result(False, f"Invalid helper request: line exceeds {human_bytes(IPC_LINE_BYTE_LIMIT)}")
                        continue
                    try:
                        request = json.loads(encoded.decode("utf-8"))
                        encoded = b""
                        if not isinstance(request, dict):
                            raise ValueError("request must be an object")
                        try:
                            self.handle(request)
                        finally:
                            request.pop("password", None)
                            request.clear()
                    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                        encoded = b""
                        self.action_result(False, f"Invalid helper request: {exc}")
                elif time.monotonic() - self.last_poll >= POLL_SECONDS:
                    self.snapshot()
                    self.last_poll = time.monotonic()
        finally:
            self.cnl_server.stop()


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] != "daemon":
        print("usage: jdctl.py daemon", file=sys.stderr)
        return 2
    bridge = Bridge()

    def terminate(signum: int, _frame: Any) -> None:
        bridge.cnl_server.stop()
        signal.signal(signum, signal.SIG_DFL)
        if os.getpgrp() == os.getpid():
            os.killpg(os.getpgrp(), signum)
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    bridge.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
