#!/usr/bin/env python3
"""Descriptor-safe, bounded transaction for the isolated helper environment."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any


INSTALL_TIMEOUT = 600.0
OUTPUT_LINE_LIMIT = 4096
OUTPUT_TOTAL_LIMIT = 64 * 1024
LOCK_FILE_LIMIT = 1024 * 1024
OWNED_ENVIRONMENT = re.compile(r"\.venv-[0-9a-f]{64}\.installed\.[0-9]+-[0-9]+-[0-9a-f]{8}")
OWNED_LEGACY = re.compile(r"venv\.legacy\.[0-9]+-[0-9]+-[0-9a-f]{8}")
OWNED_STAGING = re.compile(r"\.venv-[0-9a-f]{64}\.new\.[0-9]+-[0-9]+-[0-9a-f]{8}")
OWNED_LINK = re.compile(r"\.venv-link\.[0-9]+-[0-9]+-[0-9a-f]{8}")
RENAME_EXCHANGE = 2


class InstallError(RuntimeError):
    pass


def open_directory(path: Path, *, create: bool, private: bool = False) -> int:
    absolute = path.absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
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
            raise InstallError(f"Directory is not owned by uid {os.getuid()}: {path}")
        if private and details.st_mode & 0o077:
            os.fchmod(descriptor, 0o700)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def regular_file_bytes(directory_fd: int, name: str, limit: int) -> bytes:
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        dir_fd=directory_fd,
    )
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise InstallError(f"Required input is not a regular file: {name}")
        if details.st_size > limit:
            raise InstallError(f"Required input is too large: {name}")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        result = b"".join(chunks)
        if len(result) > limit:
            raise InstallError(f"Required input is too large: {name}")
        return result
    finally:
        os.close(descriptor)


def realpath_from_directory_fd(directory_fd: int) -> str:
    """Return the underlying filesystem path that ``directory_fd`` refers to.

    Python's :mod:`venv` bootstrap (specifically its ``ensurepip`` child
    process) does not inherit the parent's directory descriptors, so venv
    creation cannot proceed through ``/proc/self/fd/<fd>/...``. We expose the
    real path so callers can build a staging tree that both the installer
    and its grandchildren can address without descriptor inheritance.

    The descriptor must have been opened with ``O_NOFOLLOW | O_DIRECTORY``;
    in particular the resolved path is guaranteed not to end in a symlink the
    caller just traversed.
    """
    try:
        return os.readlink(f"/proc/self/fd/{directory_fd}")
    except OSError:
        # /proc/self/fd is unavailable (e.g. sandboxed runner); the caller
        # already holds the private, owner-controlled directory descriptor, so
        # refusing here is the only safe option.
        raise InstallError("Cannot resolve staging directory: /proc/self/fd is unavailable")


def open_realpath_staging(directory_fd: int, name: str, *, mode: int) -> str:
    """Create ``name`` inside ``directory_fd`` via its real filesystem path.

    Returns the absolute path, suitable for handing to subprocesses that do
    not propagate file descriptors. The directory is private, owned by the
    current user, and lives in the same filesystem as ``directory_fd`` so
    the caller can rename it back under the descriptor later.
    """
    real_parent = realpath_from_directory_fd(directory_fd)
    staging_path = os.path.join(real_parent, name)
    try:
        os.mkdir(staging_path, mode, dir_fd=None)
    except FileExistsError:
        raise InstallError(f"Staging directory already exists: {staging_path}")
    details = os.stat(staging_path)
    if details.st_uid != os.getuid():
        os.rmdir(staging_path)
        raise InstallError(f"Staging directory is not owned by uid {os.getuid()}: {staging_path}")
    if details.st_mode & 0o077:
        os.chmod(staging_path, mode)
    return staging_path


def validate_realpath_staging(directory_fd: int, name: str) -> bool:
    """Re-verify a real-path staging entry through its directory descriptor.

    Closes the time-of-check/time-of-use gap the path-based creation above
    leaves open: the entry is re-opened with ``O_NOFOLLOW | O_DIRECTORY``
    relative to ``directory_fd`` and its ownership and mode are checked on
    the descriptor, so a same-uid symlink swap between ``readlink`` and
    ``mkdir`` cannot redirect the staging tree.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
        details = os.fstat(descriptor)
        return details.st_uid == os.getuid() and not details.st_mode & 0o077
    except OSError:
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def remove_realpath_staging(staging_path: str | None) -> None:
    """Best-effort cleanup of a realpath staging directory."""
    if not staging_path:
        return
    try:
        details = os.lstat(staging_path)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(details.st_mode):
        try:
            os.unlink(staging_path)
        except FileNotFoundError:
            return
        return
    for root_directory, subdirectories, files in os.walk(staging_path, topdown=False):
        for subdirectory in subdirectories:
            try:
                os.rmdir(os.path.join(root_directory, subdirectory))
            except OSError:
                pass
        for filename in files:
            try:
                os.unlink(os.path.join(root_directory, filename))
            except OSError:
                pass
    try:
        os.rmdir(staging_path)
    except OSError:
        pass


def remove_tree(parent_fd: int, name: str) -> None:
    details = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(details.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        return
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        for entry in os.scandir(f"/proc/self/fd/{directory_fd}"):
            child = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(child.st_mode):
                remove_tree(directory_fd, entry.name)
            else:
                os.unlink(entry.name, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)
    os.rmdir(name, dir_fd=parent_fd)


def rename_exchange(directory_fd: int, first: str, second: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.renameat2(
        directory_fd,
        os.fsencode(first),
        directory_fd,
        os.fsencode(second),
        RENAME_EXCHANGE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


class BoundedRunner:
    def __init__(self, deadline: float, inherited_fds: tuple[int, ...]) -> None:
        self.deadline = deadline
        self.inherited_fds = inherited_fds
        self.child: subprocess.Popen[bytes] | None = None
        self.output_bytes = 0
        self.output_notice = False

    def terminate(self) -> None:
        if self.child is None or self.child.poll() is not None:
            return
        try:
            os.killpg(self.child.pid, signal.SIGTERM)
            self.child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(self.child.pid, signal.SIGKILL)
            self.child.wait()
        except ProcessLookupError:
            pass

    def _write(self, stream: Any, source: bytes) -> None:
        if self.output_bytes >= OUTPUT_TOTAL_LIMIT:
            if not self.output_notice:
                print("Installer output limit reached; further command output was discarded.", file=sys.stderr, flush=True)
                self.output_notice = True
            return
        decoded = source.decode("utf-8", "replace")
        for line in decoded.splitlines():
            line = line[:OUTPUT_LINE_LIMIT]
            remaining = OUTPUT_TOTAL_LIMIT - self.output_bytes
            line = line[:remaining]
            if line:
                print(line, file=stream, flush=True)
                self.output_bytes += len(line.encode("utf-8", "replace")) + 1

    def run(self, arguments: list[str], *, environment: dict[str, str] | None = None) -> None:
        if self.deadline - time.monotonic() <= 0:
            raise InstallError("Helper installation exceeded its absolute deadline")
        self.child = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            pass_fds=self.inherited_fds,
            env=environment,
        )
        selector = selectors.DefaultSelector()
        assert self.child.stdout is not None and self.child.stderr is not None
        selector.register(self.child.stdout, selectors.EVENT_READ, sys.stdout)
        selector.register(self.child.stderr, selectors.EVENT_READ, sys.stderr)
        try:
            while selector.get_map():
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    self.terminate()
                    raise InstallError("Helper installation exceeded its absolute deadline")
                for key, _mask in selector.select(min(0.25, remaining)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if chunk:
                        self._write(key.data, chunk)
                    else:
                        selector.unregister(key.fileobj)
            code = self.child.wait(timeout=max(0.1, self.deadline - time.monotonic()))
        except BaseException:
            self.terminate()
            raise
        finally:
            selector.close()
            self.child = None
        if code != 0:
            raise InstallError(f"Installer command failed with exit {code}: {arguments[0]}")


def write_marker(root_fd: int, staging_name: str, lock_hash: str) -> None:
    directory_fd = os.open(
        staging_name,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=root_fd,
    )
    try:
        descriptor = os.open(
            "omajdownload-requirements.sha256",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        try:
            os.write(descriptor, (lock_hash + "\n").encode("ascii"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def validate_active_symlink(root_fd: int) -> None:
    target = os.readlink("venv", dir_fd=root_fd)
    if "/" in target or not OWNED_ENVIRONMENT.fullmatch(target):
        raise InstallError("Existing venv symlink is not an owned OmaJD-Remote environment")
    details = os.stat(target, dir_fd=root_fd, follow_symlinks=False)
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
        raise InstallError("Existing venv target is not a private owned directory")


def prune(root_fd: int, active_name: str) -> None:
    candidates: list[tuple[float, str]] = []
    for entry in os.scandir(f"/proc/self/fd/{root_fd}"):
        if entry.name == active_name or not (OWNED_ENVIRONMENT.fullmatch(entry.name) or OWNED_LEGACY.fullmatch(entry.name)):
            continue
        details = os.stat(entry.name, dir_fd=root_fd, follow_symlinks=False)
        if stat.S_ISDIR(details.st_mode) and details.st_uid == os.getuid():
            candidates.append((details.st_mtime, entry.name))
    candidates.sort(reverse=True)
    for _mtime, name in candidates[1:]:
        remove_tree(root_fd, name)
    os.fsync(root_fd)


def install(plugin_dir: Path, data_root: Path) -> str:
    plugin_fd = open_directory(plugin_dir, create=False)
    root_fd = -1
    staging_name = ""
    staging_real = ""
    environment_name = ""
    next_name = ""
    published = False
    suffix = ""
    try:
        lock_data = regular_file_bytes(plugin_fd, "requirements.lock", LOCK_FILE_LIMIT)
        lock_hash = hashlib.sha256(lock_data).hexdigest()
        root_fd = open_directory(data_root, create=True, private=True)
        suffix = f"{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        staging_name = f".venv-{lock_hash}.new.{suffix}"
        environment_name = f".venv-{lock_hash}.installed.{suffix}"
        next_name = f".venv-link.{suffix}"
        # ``python -m venv`` boots a grand-child ``python -m ensurepip`` that
        # does not inherit the parent's directory descriptor, so the staging
        # tree cannot live under ``/proc/self/fd/<root_fd>/...``. Create it
        # via the real filesystem path under the same private directory and
        # rename it back under the descriptor once venv and pip are done.
        staging_real = open_realpath_staging(root_fd, staging_name, mode=0o700)
        if not validate_realpath_staging(root_fd, staging_name):
            raise InstallError("Staging directory is not a private current-user directory")
        root_path = f"/proc/self/fd/{root_fd}"
        plugin_path = f"/proc/self/fd/{plugin_fd}"
        runner = BoundedRunner(time.monotonic() + INSTALL_TIMEOUT, (root_fd, plugin_fd))
        runner.run([sys.executable, "-m", "venv", staging_real])
        runner.run([
            f"{staging_real}/bin/pip", "install", "--disable-pip-version-check",
            "--require-hashes", "-r", f"{plugin_path}/requirements.lock",
        ])
        runner.run([
            f"{staging_real}/bin/python", f"{plugin_path}/scripts/verify_environment.py",
            f"{plugin_path}/requirements.lock",
        ])
        runner.run([f"{staging_real}/bin/python", "-c", "import myjdapi; from Crypto.Cipher import AES"])
        # Move the validated staging tree back under the directory descriptor
        # for the descriptor-safe publication phase.
        os.rename(staging_real, staging_name, src_dir_fd=None, dst_dir_fd=root_fd)
        staging_real = ""
        write_marker(root_fd, staging_name, lock_hash)
        os.rename(staging_name, environment_name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        staging_name = ""
        os.fsync(root_fd)
        os.symlink(environment_name, next_name, dir_fd=root_fd)

        try:
            active = os.stat("venv", dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            os.rename(next_name, "venv", src_dir_fd=root_fd, dst_dir_fd=root_fd)
            next_name = ""
            published = True
        else:
            if active.st_uid != os.getuid():
                raise InstallError("Existing venv is not owned by the current user")
            if stat.S_ISLNK(active.st_mode):
                validate_active_symlink(root_fd)
                os.replace(next_name, "venv", src_dir_fd=root_fd, dst_dir_fd=root_fd)
                next_name = ""
                published = True
            elif stat.S_ISDIR(active.st_mode):
                legacy_name = f"venv.legacy.{suffix}"
                rename_exchange(root_fd, next_name, "venv")
                published = True
                os.rename(next_name, legacy_name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
                next_name = ""
            else:
                raise InstallError("Existing venv is a special file")
        os.fsync(root_fd)
        prune(root_fd, environment_name)
        return f"OmaJD-Remote helper installed and verified in {data_root / environment_name}"
    finally:
        if staging_real:
            remove_realpath_staging(staging_real)
        if root_fd >= 0:
            for name in (staging_name,):
                if not name:
                    continue
                try:
                    if OWNED_STAGING.fullmatch(name) or OWNED_LINK.fullmatch(name):
                        remove_tree(root_fd, name)
                except FileNotFoundError:
                    pass
            if next_name:
                try:
                    details = os.stat(next_name, dir_fd=root_fd, follow_symlinks=False)
                    if published and stat.S_ISDIR(details.st_mode):
                        os.rename(
                            next_name,
                            f"venv.legacy.{suffix}",
                            src_dir_fd=root_fd,
                            dst_dir_fd=root_fd,
                        )
                    elif OWNED_LINK.fullmatch(next_name):
                        remove_tree(root_fd, next_name)
                except FileNotFoundError:
                    pass
            if environment_name and not published:
                try:
                    remove_tree(root_fd, environment_name)
                except FileNotFoundError:
                    pass
            try:
                os.fsync(root_fd)
            except OSError:
                pass
            os.close(root_fd)
        os.close(plugin_fd)


def setup_development(plugin_dir: Path, environment_path: Path) -> str:
    if environment_path.name in {"", ".", ".."}:
        raise InstallError("Development environment must have a specific directory name")
    plugin_fd = open_directory(plugin_dir, create=False)
    parent_fd = open_directory(environment_path.parent, create=True)
    staging_real = ""
    try:
        try:
            details = os.stat(environment_path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            os.mkdir(environment_path.name, 0o700, dir_fd=parent_fd)
        else:
            if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
                raise InstallError("Development environment is not a current-user directory")
        # ``python -m venv`` does not propagate directory descriptors to its
        # ``ensurepip`` grand-child, so build the venv via the real filesystem
        # path and rename the finished tree back under the directory
        # descriptor.
        staging_real = open_realpath_staging(parent_fd, environment_path.name, mode=0o700)
        # Re-open the staging directory through the descriptor and verify
        # ownership/mode with no-follow semantics before handing the real
        # path to subprocesses, closing the window between the readlink
        # resolution and the path-based mkdir above.
        if not validate_realpath_staging(parent_fd, environment_path.name):
            raise InstallError("Development environment staging is not a private current-user directory")
        root_path = f"/proc/self/fd/{parent_fd}/{environment_path.name}"
        plugin_path = f"/proc/self/fd/{plugin_fd}"
        runner = BoundedRunner(time.monotonic() + INSTALL_TIMEOUT, (parent_fd, plugin_fd))
        runner.run([sys.executable, "-m", "venv", staging_real])
        runner.run([
            f"{staging_real}/bin/pip", "install", "--disable-pip-version-check",
            "--require-hashes", "-r", f"{plugin_path}/requirements.lock",
        ])
        environment = dict(os.environ)
        environment["PYTHON_BIN"] = f"{root_path}/bin/python"
        runner.run([f"{plugin_path}/scripts/check.sh"], environment=environment)
        os.rename(staging_real, environment_path.name, src_dir_fd=None, dst_dir_fd=parent_fd)
        staging_real = ""
        environment_fd = os.open(
            environment_path.name,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            os.fchmod(environment_fd, 0o700)
            os.fsync(environment_fd)
        finally:
            os.close(environment_fd)
    finally:
        if staging_real:
            remove_realpath_staging(staging_real)
        os.close(parent_fd)
        os.close(plugin_fd)
    return f"Development environment is ready at {environment_path}"


def main() -> int:
    development = len(sys.argv) == 4 and sys.argv[1] == "--development"
    if (not development and len(sys.argv) != 3) or (development and len(sys.argv) != 4):
        print("usage: secure_install.py [--development] PLUGIN_DIR TARGET_DIR", file=sys.stderr)
        return 2
    if os.getpgrp() != os.getpid():
        os.setsid()

    def terminate(signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        if development:
            message = setup_development(Path(sys.argv[2]), Path(sys.argv[3]))
        else:
            message = install(Path(sys.argv[1]), Path(sys.argv[2]))
    except (InstallError, OSError, subprocess.SubprocessError) as exc:
        print(f"Helper installation failed: {exc}", file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
