"""
keep_awake.py
─────────────
Prevent the computer from sleeping while a recording is in progress (DESIGN “Recording”
safety). Best-effort and cross-platform:
  • macOS   — a `caffeinate -dimsu` subprocess for the recording's lifetime.
  • Windows — SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED |
    ES_DISPLAY_REQUIRED), cleared on stop.
  • else    — no-op (we still track the requested/released state).

`LatencyActivity` (macOS): while recording, tell the system this process is
doing latency-critical work the user started —
`NSProcessInfo.beginActivityWithOptions(UserInitiated | LatencyCritical)` —
so it is not put in App Nap and its timers are not coalesced. `KeepAwake`
holds one in the app's main process; every camera process holds its own while
it records (ADR-0025). Windows needs nothing beyond SetThreadExecutionState.
`BRAIN_BATTLE_NO_LATENCY_ACTIVITY=1` switches it off (soak-test experiments).

`is_active()` reflects whether sleep prevention is currently requested — used by
the recording screen and tests ("sleep prevention requested and released").
Pure stdlib; failures never raise (a recording must not fail because the OS
power API is unavailable).
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess

log = logging.getLogger(__name__)

# NSActivityOptions (Foundation/NSProcessInfo.h)
NS_ACTIVITY_USER_INITIATED = 0x00FFFFFF
NS_ACTIVITY_LATENCY_CRITICAL = 0xFF00000000


class LatencyActivity:
    """One "latency-critical, user-initiated" activity for THIS process
    (macOS); a no-op elsewhere. ``is_active()`` = requested; ``token`` is the
    system's handle while it is held (None when not held or not available)."""

    def __init__(self, reason: str = "Brain Battle recording"):
        self._reason = reason
        self._active = False
        self.token = None

    def is_active(self) -> bool:
        return self._active

    def begin(self) -> None:
        if self._active:
            return
        self._active = True
        if platform.system() != "Darwin" or os.environ.get("BRAIN_BATTLE_NO_LATENCY_ACTIVITY"):
            return
        try:
            from Foundation import NSProcessInfo
            self.token = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
                NS_ACTIVITY_USER_INITIATED | NS_ACTIVITY_LATENCY_CRITICAL, self._reason)
        except Exception as e:
            log.warning("could not mark the recording latency-critical (%s) — recording "
                        "continues", e)

    def end(self) -> None:
        if not self._active:
            return
        self._active = False
        token, self.token = self.token, None
        if token is not None:
            try:
                from Foundation import NSProcessInfo
                NSProcessInfo.processInfo().endActivity_(token)
            except Exception as e:
                log.warning("ending the latency-critical activity failed: %s", e)


class KeepAwake:
    def __init__(self, reason: str = "Brain Battle recording"):
        self._reason = reason
        self._active = False
        self._proc = None
        self.activity = LatencyActivity(reason)

    def is_active(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active:
            return
        self._active = True   # track intent even if the OS call is a no-op
        self.activity.begin()
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
        self.activity.end()
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


__all__ = ["KeepAwake", "LatencyActivity"]
