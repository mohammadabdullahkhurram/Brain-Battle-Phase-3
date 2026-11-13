"""
clock.py — the one capture clock
────────────────────────────────
Everything Brain Battle records is stamped from ONE clock: the computer's
monotonic clock (``time.monotonic()``), which never jumps when the wall clock
is corrected (NTP, daylight saving, a user changing the time mid-session).

Two other time scales are needed by the files we write, and both are exposed
here as fixed offsets from that one clock, measured once per process:

  • **Unix time** — the EEG CSV ``timestamps`` column and the sidecars'
    ``t_unix``. ``unix_now()`` is "monotonic + a constant", so it reads like
    wall-clock time but cannot step during a recording.
  • **LSL time** — ``pylsl.local_clock()``, the time scale of Lab Streaming
    Layer and of every XDF file. ``mono_to_lsl`` / ``unix_to_lsl`` convert our
    stamps to it, so the live LSL outlets and ``recording.xdf`` sit on the same
    clock a LabRecorder running on this computer would use.

Because all three are the same clock seen through constant offsets, converting
between them is exact by construction — there is no second clock to drift
against. (See docs/decisions/adr-0015-lsl-and-xdf.md.)

Qt-free and import-cheap: pylsl is only loaded the first time an LSL
conversion is asked for, and its absence degrades to "LSL time = monotonic
time" rather than an error.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

# Quieten liblsl's very chatty start-up logging. Must be set before pylsl is
# first imported anywhere, which is why it lives in this module: everything
# that touches LSL imports the clock first.
os.environ.setdefault("LSLAPICFG", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "support", "lsl_api.cfg"))

_lock = threading.Lock()
_mono0: Optional[float] = None     # anchor: monotonic reading …
_unix0: Optional[float] = None     # … and the wall clock at that same instant
_lsl_minus_mono: Optional[float] = None
_lsl_ok: Optional[bool] = None


def _bracket(read_other) -> tuple[float, float]:
    """(monotonic, other) read at the same instant, as tightly as the OS
    allows: take a few bracketed reads and keep the tightest one."""
    best = None
    for _ in range(5):
        a = time.monotonic()
        other = read_other()
        b = time.monotonic()
        if best is None or (b - a) < best[0]:
            best = (b - a, (a + b) / 2.0, other)
    return best[1], best[2]


def _anchor() -> tuple[float, float]:
    global _mono0, _unix0
    if _mono0 is None:
        with _lock:
            if _mono0 is None:
                m, u = _bracket(time.time)
                _unix0, _mono0 = u, m
    return _mono0, _unix0


def monotonic() -> float:
    """The capture clock itself."""
    return time.monotonic()


def mono_to_unix(t_monotonic: float) -> float:
    m0, u0 = _anchor()
    return u0 + (float(t_monotonic) - m0)


def unix_to_mono(t_unix: float) -> float:
    m0, u0 = _anchor()
    return m0 + (float(t_unix) - u0)


def unix_now() -> float:
    """Unix-style time derived from the monotonic clock (never steps). This is
    the ``time_func`` handed to the headband reader."""
    return mono_to_unix(time.monotonic())


def _lsl_offset() -> float:
    """LSL-clock minus monotonic-clock, measured once. 0.0 when pylsl is not
    available (LSL time then simply equals monotonic time)."""
    global _lsl_minus_mono, _lsl_ok
    if _lsl_minus_mono is None:
        with _lock:
            if _lsl_minus_mono is None:
                try:
                    import pylsl
                    m, l = _bracket(pylsl.local_clock)
                    _lsl_minus_mono, _lsl_ok = l - m, True
                except Exception:
                    _lsl_minus_mono, _lsl_ok = 0.0, False
    return _lsl_minus_mono


def lsl_available() -> bool:
    _lsl_offset()
    return bool(_lsl_ok)


def mono_to_lsl(t_monotonic: float) -> float:
    return float(t_monotonic) + _lsl_offset()


def unix_to_lsl(t_unix: float) -> float:
    return mono_to_lsl(unix_to_mono(t_unix))


def session_origin() -> dict:
    """Capture a recording's time origin NOW, on all three scales. This dict is
    what the manifest stores as ``capture_clock``."""
    t0 = time.monotonic()
    return {"t0_monotonic": t0, "t0_unix": mono_to_unix(t0), "t0_lsl": mono_to_lsl(t0)}


__all__ = ["monotonic", "unix_now", "mono_to_unix", "unix_to_mono",
           "mono_to_lsl", "unix_to_lsl", "lsl_available", "session_origin"]
