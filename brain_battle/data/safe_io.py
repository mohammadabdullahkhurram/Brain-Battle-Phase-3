"""
safe_io.py — writing files safely in a folder several computers share
─────────────────────────────────────────────────────────────────────
The recordings folder may sit on a shared or network drive that several lab
computers have open at once. Three small tools keep that safe:

  • ``atomic_write_text`` / ``atomic_write_json`` — write to a temporary file
    with a name no other computer can pick (computer + process + random), flush
    it to the disk, then swap it into place in one step. A reader sees the old
    file or the new one, never half of each. The swap is retried for a moment,
    because Windows and network drives briefly refuse it while another program
    has the file open.
  • ``FolderLock`` — a small lock file for the rare "read, change, write back"
    edits to a shared file (the recording's manifest, the team label list), so
    two computers cannot overwrite each other's change. A lock left behind by a
    computer that crashed is taken over after ``stale_after`` seconds. The lock
    is advisory and best-effort: if it cannot be taken, the edit still happens.
  • ``folder_status`` / ``is_network_path`` — is the folder there, can it be
    written to, and is it on a network drive.

``read_json`` reads tolerantly (a short retry, then ``None``), because a file on
a network drive can be momentarily unreadable while another computer swaps it.

Qt-free and stdlib-only. See docs/decisions/adr-0020-shared-folder-safety.md.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from brain_battle.support.platform_utils import computer_name, is_windows, is_mac

_RETRIES = 6            # attempts for a swap / read that the OS briefly refuses
_RETRY_WAIT = 0.05      # seconds, doubled each attempt (≈1.5 s in total)


def _tmp_name(path: Path) -> Path:
    """A temporary name beside ``path`` that no other writer will choose."""
    tag = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    return path.with_name(f".{path.name}.{tag}.tmp")


def atomic_write_bytes(path, data: bytes) -> None:
    """Replace ``path`` with ``data`` in one step. Raises OSError if the folder
    cannot be written (the caller decides what to tell the user)."""
    p = Path(path)
    tmp = _tmp_name(p)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass                      # some network filesystems refuse fsync
        replace_with_retry(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def replace_with_retry(src, dst) -> None:
    """``os.replace`` that waits out a brief 'file in use' refusal."""
    wait = _RETRY_WAIT
    for attempt in range(_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _RETRIES - 1:
                raise
            time.sleep(wait)
            wait *= 2


def atomic_write_text(path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path, obj) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False))


def read_json(path, default=None):
    """The parsed JSON file, or ``default`` if it is missing, unreadable or not
    valid JSON. Retries briefly, since another computer may be swapping it."""
    wait = _RETRY_WAIT
    for attempt in range(3):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except (OSError, ValueError):
            if attempt == 2:
                return default
            time.sleep(wait)
            wait *= 2
    return default


def append_line(path, line: str) -> None:
    """Append one complete line in a single write and flush it to the disk.
    Appending never rewrites earlier lines, so several computers can add to the
    same log without damaging each other's entries."""
    if not line.endswith("\n"):
        line += "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass


class FolderLock:
    """``with FolderLock(target_file): …`` — hold ``<target>.lock`` while doing a
    read-change-write of ``target``.

    ``acquired`` says whether the lock was really taken. If it could not be
    (read-only folder, or another computer held it for the whole ``timeout``)
    the block still runs: a lost edit to a small settings file is better than
    an app that hangs because a lock file was left behind."""

    def __init__(self, target, timeout: float = 3.0, stale_after: float = 20.0):
        self.path = Path(str(target) + ".lock")
        self.timeout = timeout
        self.stale_after = stale_after
        self.acquired = False

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(f"{computer_name()} pid {os.getpid()} at {time.time():.0f}\n")
                self.acquired = True
                return self
            except FileExistsError:
                self._break_if_stale()
            except OSError:
                return self                 # cannot create lock files here
            if time.monotonic() >= deadline:
                return self
            time.sleep(0.05)

    def _break_if_stale(self):
        try:
            age = time.time() - os.path.getmtime(self.path)
        except OSError:
            return                          # gone already
        if age > self.stale_after:
            try:
                os.unlink(self.path)
            except OSError:
                pass

    def __exit__(self, *exc):
        if self.acquired:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            self.acquired = False
        return False


def update_json(path, change, default=None) -> bool:
    """Read ``path``, let ``change(obj)`` edit it (return False to abandon),
    and write it back atomically — all under a ``FolderLock``. Returns True if
    the file was written."""
    with FolderLock(path):
        obj = read_json(path, default)
        if obj is None or change(obj) is False:
            return False
        try:
            atomic_write_json(path, obj)
            return True
        except OSError:
            return False


# ── is the folder there? ─────────────────────────────────────────────────────

FOLDER_OK = "ok"
FOLDER_MISSING = "missing"          # not there (drive unplugged / not connected)
FOLDER_READ_ONLY = "read_only"      # there, but this computer cannot write to it


def folder_status(path, create: bool = False) -> str:
    """One of FOLDER_OK / FOLDER_MISSING / FOLDER_READ_ONLY. With ``create`` a
    missing folder is made first (the default recordings folder on first run)."""
    if not path:
        return FOLDER_MISSING
    try:
        if create:
            os.makedirs(path, exist_ok=True)
        if not os.path.isdir(path):
            return FOLDER_MISSING
        probe = os.path.join(path, f".brain-battle-write-test-{uuid.uuid4().hex[:8]}")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.unlink(probe)
        return FOLDER_OK
    except FileNotFoundError:
        return FOLDER_MISSING
    except OSError:
        return FOLDER_READ_ONLY if os.path.isdir(path) else FOLDER_MISSING


def folder_problem_text(path, status: str) -> str:
    """What happened and what to do, in plain words ('' when all is well)."""
    if status == FOLDER_MISSING:
        return (f"The recordings folder can't be reached: {path}. If it is on a shared or "
                f"external drive, check that the drive is connected, then press Try again. "
                f"You can also choose a different folder in Settings.")
    if status == FOLDER_READ_ONLY:
        return (f"This computer is not allowed to save into the recordings folder: {path}. "
                f"Ask whoever manages the shared drive for permission to write to it, or "
                f"choose a different folder in Settings.")
    return ""


_NETWORK_FS = ("smbfs", "afpfs", "nfs", "webdav", "cifs", "smb", "nfs4", "fuse.sshfs", "9p")


def is_network_path(path) -> bool:
    """Best-effort: is this folder on a network drive? (False when unsure.)"""
    try:
        p = os.path.abspath(str(path))
        if is_windows():
            if p.startswith("\\\\"):
                return True
            import ctypes
            drive = os.path.splitdrive(p)[0] + "\\"
            return ctypes.windll.kernel32.GetDriveTypeW(drive) == 4      # DRIVE_REMOTE
        if is_mac():
            import subprocess
            out = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=3).stdout
            best, best_fs = "", ""
            for line in out.splitlines():           # "//u@host/share on /Volumes/x (smbfs, …)"
                if " on " not in line or "(" not in line:
                    continue
                mount = line.split(" on ", 1)[1].rsplit(" (", 1)[0]
                fs = line.rsplit("(", 1)[1].split(",")[0].strip(") ")
                if (p == mount or p.startswith(mount.rstrip("/") + "/")) and len(mount) > len(best):
                    best, best_fs = mount, fs
            return best_fs in _NETWORK_FS
        with open("/proc/mounts", encoding="utf-8") as f:
            best, best_fs = "", ""
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and (p == parts[1] or p.startswith(parts[1].rstrip("/") + "/")) \
                        and len(parts[1]) > len(best):
                    best, best_fs = parts[1], parts[2]
            return best_fs in _NETWORK_FS
    except Exception:
        return False


__all__ = ["atomic_write_bytes", "atomic_write_text", "atomic_write_json", "read_json",
           "append_line", "replace_with_retry", "FolderLock", "update_json",
           "folder_status", "folder_problem_text", "is_network_path",
           "FOLDER_OK", "FOLDER_MISSING", "FOLDER_READ_ONLY"]
