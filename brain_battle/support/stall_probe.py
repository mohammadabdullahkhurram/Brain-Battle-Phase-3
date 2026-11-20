"""
stall_probe.py — debug-only: find out when (and why) the whole process stalls
─────────────────────────────────────────────────────────────────────────────
Not used by the app in normal running. ``tools/soak_test.py --diagnose`` (or
``BRAIN_BATTLE_STALL_PROBE=1``) starts it.

Three instruments, all stamped with the one capture clock
(``clock.monotonic()``) so they line up with the cameras' frame times:

  • **stall detector** — a thread that sleeps 5 ms in a loop and records every
    time it woke more than 20 ms late (``stalls``: (t, overshoot_s)). A late
    wake means the thread could not get the GIL, or the process was not run.
  • **garbage collection** — every collection's generation, duration and how
    much it collected, from ``gc.callbacks`` (``gcs``: (t_start, dur_s, gen,
    collected)).
  • **stacks during a stall** — the probe arms
    ``faulthandler.dump_traceback_later`` before each sleep; its watchdog is a
    C thread that needs no GIL, so if the probe has not woken after
    ``dump_after`` seconds every Python thread's stack is written to
    ``stack_file`` *while the stall is happening* (what ``py-spy dump`` would
    show; py-spy itself needs root on macOS).

    probe = StallProbe(stack_file="stalls.txt"); probe.start()
    ...
    probe.stop(); probe.stalls, probe.gcs

``wake_lateness()`` measures the computer itself: how late it wakes a thread
sleeping to 60 Hz deadlines (``tools/wake_probe.py``; the timing tests use it
to decide whether this computer can judge millisecond precision at all).
"""
from __future__ import annotations

import faulthandler
import gc
import threading
import time
from typing import Optional

from brain_battle.capture import clock

TICK_SEC = 0.005
STALL_SEC = 0.020


class StallProbe:
    def __init__(self, stack_file: Optional[str] = None, dump_after: float = 0.08):
        self.stalls: list[tuple[float, float]] = []
        self.gcs: list[tuple[float, float, int, int]] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._gc_t0 = 0.0
        self._stack_path = stack_file
        self._stack_f = None
        self._dump_after = float(dump_after)

    # ── garbage collection ──
    def _on_gc(self, phase, info) -> None:
        if phase == "start":
            self._gc_t0 = clock.monotonic()
        else:
            t0 = self._gc_t0
            self.gcs.append((t0, clock.monotonic() - t0, int(info.get("generation", -1)),
                             int(info.get("collected", 0))))

    # ── the stall detector ──
    def _run(self) -> None:
        while not self._stop.is_set():
            if self._stack_f is not None:
                faulthandler.dump_traceback_later(self._dump_after, repeat=False,
                                                  file=self._stack_f)
            t = time.perf_counter()
            time.sleep(TICK_SEC)
            late = time.perf_counter() - t - TICK_SEC
            if self._stack_f is not None:
                faulthandler.cancel_dump_traceback_later()
            if late > STALL_SEC:
                self.stalls.append((clock.monotonic() - late, late))

    def start(self) -> None:
        if self._stack_path:
            self._stack_f = open(self._stack_path, "w", encoding="utf-8")
        gc.callbacks.append(self._on_gc)
        self._thread = threading.Thread(target=self._run, name="stall-probe", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        try:
            gc.callbacks.remove(self._on_gc)
        except ValueError:
            pass
        if self._stack_f is not None:
            faulthandler.cancel_dump_traceback_later()
            self._stack_f.close()
            self._stack_f = None

    # ── summaries ──
    def stalls_between(self, t0: float, t1: float) -> list[tuple[float, float]]:
        return [s for s in self.stalls if t0 <= s[0] <= t1]

    def gc_summary(self) -> dict:
        out = {}
        for _t, d, g, _c in self.gcs:
            e = out.setdefault(g, {"n": 0, "total_ms": 0.0, "max_ms": 0.0})
            e["n"] += 1
            e["total_ms"] += d * 1000
            e["max_ms"] = max(e["max_ms"], d * 1000)
        return {g: {k: round(v, 1) if isinstance(v, float) else v for k, v in e.items()}
                for g, e in sorted(out.items())}


def wake_lateness(seconds: float = 3.0, period: float = 1 / 60) -> dict:
    """Sleep to ``period`` deadlines for ``seconds`` and measure how late each
    wake-up was (ms): ``median``, ``p99``, ``worst``, ``over_half`` (share of
    wake-ups more than half a period late) and ``lost`` (count more than a whole
    period late — what would lose a frame). A camera reader is such a sleeper."""
    late = []
    t0 = time.perf_counter()
    n = 1
    while time.perf_counter() - t0 < seconds:
        due = t0 + n * period
        wait = due - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        late.append((time.perf_counter() - due) * 1000.0)
        n += 1
    late.sort()
    k = max(1, len(late))
    half = period * 500.0
    return {"n": len(late), "median": late[k // 2], "p99": late[min(k - 1, int(k * 0.99))],
            "worst": late[-1], "over_half": sum(1 for x in late if x > half) / k,
            "lost": sum(1 for x in late if x > 2 * half)}


__all__ = ["StallProbe", "TICK_SEC", "STALL_SEC", "wake_lateness"]
