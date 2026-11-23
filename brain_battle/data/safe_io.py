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
Every read of a shared file goes through ``open_shared`` / ``read_text``,
which on Windows opens it with delete-sharing, so a reader never stops another
computer's save.

Qt-free and stdlib-only. See docs/decisions/adr-0020-shared-folder-safety.md.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from brain_battle.support.platform_utils import computer_name, is_windows, is_mac

# A swap or read the OS briefly refuses (Windows: another program has the file
# open) is retried with short, growing waits for up to about five seconds. Many
# short tries matter more than long ones: a reader holds the file only for a
# moment, so the next try usually succeeds.
_RETRY_BUDGET = 5.0     # seconds in total
_RETRY_WAIT = 0.01      # first wait; grows by half each try, capped below
_RETRY_WAIT_MAX = 0.2


def _waits():
    """The successive waits between tries, adding up to ~_RETRY_BUDGET."""
    wait, spent = _RETRY_WAIT, 0.0
    while spent < _RETRY_BUDGET:
        yield wait
        spent += wait
        wait = min(wait * 1.5, _RETRY_WAIT_MAX)


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


def _replace_posix_windows(src, dst) -> bool:
    """Windows: swap ``src`` into ``dst`` with POSIX semantics
    (``SetFileInformationByHandle`` / ``FileRenameInfoEx`` with
    REPLACE_IF_EXISTS | POSIX_SEMANTICS, Windows 10 1607+ on NTFS). Unlike
    ``os.replace`` (``MoveFileEx``), this goes through while another program has
    ``dst`` open with delete-sharing (``open_shared``); that reader keeps the
    old file. Returns False when the system does not offer it (then
    ``os.replace`` is used)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               wintypes.LPVOID, wintypes.DWORD]
    k32.SetFileInformationByHandle.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    DELETE, SHARE_ALL, OPEN_EXISTING = 0x00010000, 0x1 | 0x2 | 0x4, 3
    FileRenameInfoEx = 22
    REPLACE_IF_EXISTS, POSIX_SEMANTICS = 0x1, 0x2
    handle = k32.CreateFileW(str(src), DELETE, SHARE_ALL, None, OPEN_EXISTING, 0x80, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        return False
    try:
        name = os.path.abspath(str(dst))
        enc = name.encode("utf-16-le")
        # FILE_RENAME_INFO: Flags (4, padded to 8) · RootDirectory (8) ·
        # FileNameLength (4) · FileName (WCHAR[])
        off = 8 + ctypes.sizeof(ctypes.c_void_p)
        size = off + 4 + len(enc) + 2
        buf = ctypes.create_string_buffer(size)
        ctypes.memmove(buf, ctypes.byref(wintypes.DWORD(REPLACE_IF_EXISTS | POSIX_SEMANTICS)), 4)
        ctypes.memmove(ctypes.addressof(buf) + off, ctypes.byref(wintypes.DWORD(len(enc))), 4)
        ctypes.memmove(ctypes.addressof(buf) + off + 4, enc, len(enc))
        if k32.SetFileInformationByHandle(handle, FileRenameInfoEx, buf, size):
            return True
        err = ctypes.get_last_error()
        if err in (5, 32, 33):           # really refused: let the caller retry
            raise PermissionError(13, "Access is denied", str(dst))
        return False                     # not supported here: use os.replace
    finally:
        k32.CloseHandle(handle)


def replace_with_retry(src, dst) -> None:
    """Swap ``src`` into ``dst`` in one step, waiting out a brief 'file in use'
    refusal. On Windows the swap uses POSIX semantics, so a reader that has
    ``dst`` open (with ``open_shared``) does not block it."""
    posix = is_windows()
    for wait in _waits():
        try:
            if posix:
                if _replace_posix_windows(src, dst):
                    return
                posix = False            # not offered here: plain os.replace from now on
            os.replace(src, dst)
            return
        except PermissionError:
            time.sleep(wait)
    os.replace(src, dst)        # last try: let its error reach the caller


def atomic_write_text(path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path, obj) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False))


def open_shared(path, binary: bool = False):
    """Open a shared file for reading WITHOUT stopping anyone replacing it.

    On Windows, Python's ``open`` leaves out delete-sharing, so while any
    program has the file open nobody can swap a new version into place — a
    reader that re-opens the file often could keep another computer's save
    refused (seen as a first-attempt failure on the Windows CI). Here the file
    is opened with read, write AND delete sharing (``CreateFileW``), so a
    replace goes through while it is being read; the reader keeps the version
    it opened. Elsewhere this is plain ``open``. Raises like ``open``."""
    if not is_windows():
        return open(path, "rb" if binary else "r", **({} if binary else {"encoding": "utf-8"}))
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    GENERIC_READ, SHARE_ALL = 0x80000000, 0x1 | 0x2 | 0x4
    OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL = 3, 0x80
    handle = k32.CreateFileW(str(path), GENERIC_READ, SHARE_ALL, None, OPEN_EXISTING,
                             FILE_ATTRIBUTE_NORMAL, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        err = ctypes.get_last_error()
        if err in (2, 3):                    # file / path not found
            raise FileNotFoundError(2, "No such file or directory", str(path))
        if err in (5, 32, 33):               # access denied / sharing / lock
            raise PermissionError(13, "Access is denied", str(path))
        raise ctypes.WinError(err)
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | (0 if binary else os.O_TEXT))
    if binary:
        return os.fdopen(fd, "rb")
    return os.fdopen(fd, "r", encoding="utf-8", newline=None)


def read_text(path) -> str:
    """The whole text of a shared file, read with ``open_shared``."""
    with open_shared(path) as f:
        return f.read()


def read_json(path, default=None):
    """The parsed JSON file, or ``default`` if it is missing, unreadable or not
    valid JSON. Retries briefly, since another computer may be swapping it.
    Read with ``open_shared``, so reading never blocks someone's save."""
    bad_json = 0
    for wait in _waits():
        try:
            with open_shared(path) as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except ValueError:
            bad_json += 1               # half-written by a program that doesn't swap
            if bad_json >= 3:
                return default
        except PermissionError:
            pass                        # being replaced right now: try again
        except OSError:
            return default
        time.sleep(wait)
    return default


def append_line(path, line: str) -> None:
    """Append one complete line in a single write and flush it to the disk.
    Appending never rewrites earlier lines, so several computers can add to the
    same log without damaging each other's entries.

    The line goes to the operating system as ONE write to the end of the file.
    On macOS / Linux that is what ``O_APPEND`` does. On Windows, Python's append
    mode only *emulates* it (move to the end, then write), so two programs
    appending at the same moment can write over each other; there the file is
    opened with append-only access instead, which Windows guarantees lands at
    the end. The file is created if needed, but never its folder."""
    if not line.endswith("\n"):
        line += "\n"
    data = line.encode("utf-8")
    if is_windows():
        _append_bytes_windows(str(path), data)
        return
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o666)
    try:
        view = memoryview(data)
        while view:                     # a local disk writes it all at once
            view = view[os.write(fd, view):]
        try:
            os.fsync(fd)
        except OSError:
            pass                        # some network filesystems refuse fsync
    finally:
        os.close(fd)


def _append_bytes_windows(path: str, data: bytes) -> None:
    """Windows: one write with FILE_APPEND_DATA access only, so the system puts
    it at the end of the file even with other writers (WriteFile ignores the
    position for such a handle). Raises OSError like ``open`` would."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    k32.WriteFile.restype = wintypes.BOOL
    k32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    FILE_APPEND_DATA, SYNCHRONIZE = 0x0004, 0x00100000
    SHARE_ALL = 0x1 | 0x2 | 0x4             # read | write | delete
    OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL = 4, 0x80
    invalid = wintypes.HANDLE(-1).value
    handle = None
    for wait in _waits():                   # a sharing refusal is brief
        handle = k32.CreateFileW(path, FILE_APPEND_DATA | SYNCHRONIZE, SHARE_ALL, None,
                                 OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, None)
        if handle not in (None, invalid):
            break
        err = ctypes.get_last_error()
        if err not in (32, 33):             # ERROR_SHARING_VIOLATION / LOCK_VIOLATION
            raise ctypes.WinError(err)
        time.sleep(wait)
    else:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        written = wintypes.DWORD(0)
        if not k32.WriteFile(handle, data, len(data), ctypes.byref(written), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(data):
            raise OSError(f"short write to {path}: {written.value} of {len(data)} bytes")
        k32.FlushFileBuffers(handle)        # best effort, like fsync
    finally:
        k32.CloseHandle(handle)


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
           "open_shared", "read_text",
           "append_line", "replace_with_retry", "FolderLock", "update_json",
           "folder_status", "folder_problem_text", "is_network_path",
           "FOLDER_OK", "FOLDER_MISSING", "FOLDER_READ_ONLY"]
