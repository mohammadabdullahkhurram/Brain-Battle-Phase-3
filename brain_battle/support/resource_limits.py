"""
resource_limits.py — the operating system's "open files" limit
──────────────────────────────────────────────────────────────
Every open file, network socket and pipe to a helper program uses one
*descriptor*, and the operating system only lets a program hold so many at
once. macOS starts programs with a low ceiling (256); a recording with four
cameras, two headbands and the live LSL streams needs a good part of that.

Two things live here:

* ``raise_open_file_limit()`` — called once at start-up: lift the *soft* limit
  towards the hard limit (macOS / Linux). A safety net, not a licence to leak;
  on Windows there is nothing to raise and it does nothing.
* ``snapshot()`` / ``count_open_descriptors()`` — count what the process holds
  right now (files, sockets, pipes, threads, child processes). Used by
  ``tools/measure_descriptors.py`` and ``tests/test_descriptors.py``.

Qt-free; never raises.
"""
from __future__ import annotations

import logging
import os
import stat
import threading

log = logging.getLogger(__name__)

#: What the app asks for. Far above what a recording needs (see the numbers in
#: docs/developer-guide/module-reference.md), far below anything the OS minds.
TARGET_OPEN_FILES = 4096


def open_file_limit() -> tuple:
    """(soft, hard) open-file limit, or (None, None) where there is no such
    limit to read (Windows)."""
    try:
        import resource
        return resource.getrlimit(resource.RLIMIT_NOFILE)
    except Exception:
        return (None, None)


def raise_open_file_limit(target: int = TARGET_OPEN_FILES) -> tuple:
    """Raise the soft open-file limit to ``target`` (never above the hard
    limit, never lowering it). Returns (before, after) soft limits; both None
    on Windows. Logs what happened."""
    try:
        import resource
    except ImportError:                      # Windows: no rlimit to raise
        log.info("open-file limit: not applicable on this system")
        return (None, None)
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except Exception as e:
        log.warning("open-file limit: could not be read (%s)", e)
        return (None, None)
    inf = resource.RLIM_INFINITY
    if soft == inf or soft >= target:
        log.info("open-file limit: %s (already enough)",
                 "unlimited" if soft == inf else soft)
        return (soft, soft)
    want = target if hard == inf else min(target, hard)
    # macOS refuses values above kern.maxfilesperproc even when the hard limit
    # says "unlimited", so step down until one is accepted.
    for value in (want, 2048, 1024, 512):
        if value <= soft or (hard != inf and value > hard):
            continue
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (value, hard))
            log.info("open-file limit: raised from %d to %d", soft, value)
            return (soft, value)
        except (ValueError, OSError):
            continue
    log.warning("open-file limit: stays at %d (the system would not raise it)", soft)
    return (soft, soft)


def _fd_numbers() -> list:
    for d in ("/dev/fd", "/proc/self/fd"):
        try:
            return [int(n) for n in os.listdir(d) if n.isdigit()]
        except OSError:
            continue
    return []


def count_open_descriptors() -> dict:
    """{'fds', 'files', 'sockets', 'pipes', 'other'} for this process. On
    Windows (no descriptor table to list) falls back to psutil's handle count
    in 'fds' with the split left at 0."""
    out = {"fds": 0, "files": 0, "sockets": 0, "pipes": 0, "other": 0}
    nums = _fd_numbers()
    if not nums:
        try:
            import psutil
            p = psutil.Process()
            out["fds"] = p.num_handles() if hasattr(p, "num_handles") else p.num_fds()
        except Exception:
            pass
        return out
    for fd in nums:
        try:
            mode = os.fstat(fd).st_mode
        except OSError:
            continue                          # the listing's own descriptor, already closed
        out["fds"] += 1
        if stat.S_ISSOCK(mode):
            out["sockets"] += 1
        elif stat.S_ISFIFO(mode):
            out["pipes"] += 1
        elif stat.S_ISREG(mode):
            out["files"] += 1
        else:
            out["other"] += 1
    return out


def snapshot() -> dict:
    """Descriptors + threads (all native threads when psutil is there, else
    Python's) + child processes.

    ``children`` counts camera processes, ffmpeg and anything else started
    for a recording. Python's multiprocessing ``resource_tracker`` — one
    helper started with the first camera process and kept for the app's
    whole run (ADR-0025) — is counted separately as ``helpers``, so a
    recording that cleaned up after itself shows ``children`` back at 0."""
    out = count_open_descriptors()
    out["threads"] = threading.active_count()
    out["children"] = 0
    out["helpers"] = 0
    out["encoders"] = 0          # ffmpeg processes (also counted in children)
    try:
        import psutil
        p = psutil.Process()
        out["threads"] = p.num_threads()
        for c in p.children(recursive=True):
            try:
                tracker = "multiprocessing.resource_tracker" in " ".join(c.cmdline())
            except psutil.Error:
                tracker = False
            out["helpers" if tracker else "children"] += 1
            try:
                if "ffmpeg" in c.name().lower():
                    out["encoders"] += 1
            except psutil.Error:
                pass
    except Exception:
        pass
    return out


__all__ = ["TARGET_OPEN_FILES", "open_file_limit", "raise_open_file_limit",
           "count_open_descriptors", "snapshot"]
