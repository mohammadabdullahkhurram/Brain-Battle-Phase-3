"""
keep_awake.py
─────────────
Prevent the computer from sleeping while a recording is in progress (DESIGN §4
safety). Best-effort and cross-platform:
  • macOS   — a `caffeinate -dimsu` subprocess for the recording's lifetime.
  • Windows — SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED |
    ES_DISPLAY_REQUIRED), cleared on stop.
  • else    — no-op (we still track the requested/released state).

`is_active()` reflects whether sleep prevention is currently requested — used by
the recording screen and tests ("sleep prevention requested and released").
Pure stdlib; failures never raise (a recording must not fail because the OS
power API is unavailable).
"""
from __future__ import annotations

import logging
import platform
import subprocess

log = logging.getLogger(__name__)


class KeepAwake:
    def __init__(self, reason: str = "Brain Battle recording"):
        self._reason = reason
        self._active = False
        self._proc = None

    def is_active(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active:
            return
        self._active = True   # track intent even if the OS call is a no-op
        sysname = platform.system()
        try:
            if sysname == "Darwin":
                # -d display, -i idle, -m disk, -s system, -u user-active assert
                self._proc = subprocess.Popen(
                    ["caffeinate", "-dimsu"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif sysname == "Windows":
                import ctypes
                ES_CONTINUOUS = 0x80000000
                ES_SYSTEM_REQUIRED = 0x00000001
                ES_DISPLAY_REQUIRED = 0x00000002
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
            else:
                log.info("keep-awake: no power API for %s — not preventing sleep", sysname)
        except Exception as e:
            log.warning("keep-awake start failed (%s) — recording continues", e)

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        sysname = platform.system()
        try:
            if sysname == "Darwin" and self._proc is not None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=2)
                except Exception:
                    self._proc.kill()
                self._proc = None
            elif sysname == "Windows":
                import ctypes
                ES_CONTINUOUS = 0x80000000
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception as e:
            log.warning("keep-awake stop failed: %s", e)


__all__ = ["KeepAwake"]
