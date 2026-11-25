"""
stall_probe.py — debug-only: find out when (and why) the whole process stalls
─────────────────────────────────────────────────────────────────────────────
Never imported by the app: ``tools/soak_test.py --diagnose`` starts it, and
``tools/wake_probe.py`` / ``tests/_env.timing_judgeable`` use
``wake_lateness``.

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

``MachineStallProbe`` is a bare process that only sleeps: its late wake-ups are
the computer's own stalls, and ``judge_lost_frames`` splits lost camera frames
into those inside such a stall (machine noise) and those outside (a problem);
``tools/soak_test.py`` always runs it. ``MachineStallProbe(touch_mb=48)`` is
the same process holding a working set like any real program (every page of
48 MB written about once a second): when the computer is short of memory and
compresses or swaps those pages, IT is held up too — the bare probe, with
almost no memory, cannot see such a stall (found 2026-11-24 on an 8 GB Mac
with 16 GB of swap in use).

``MachineStallProbe.fine()`` sleeps 1 ms and notes every wake-up 2 ms late:
the short pauses the default probe cannot see (``tools/soak_test.py`` uses it
to explain each lost frame). ``NOISY_STALLS_PER_MIN``: a computer that stalls
more often than this is too noisy to judge (the heavy checks say NOT JUDGED).

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


# ── the machine's own stalls (a bare process that only sleeps) ─────────────────

MACHINE_STALL_SEC = 0.010     # a wake-up this late could already cost a 60 fps frame
FINE_TICK_SEC = 0.001         # the fine probe: sleeps 1 ms …
FINE_STALL_SEC = 0.002        # … and notes every wake-up 2 ms late (MachineStallProbe.fine())
NOISY_STALLS_PER_MIN = 20.0   # more machine stalls than this per minute: too noisy to judge


PAGE_TOUCH_PERIOD_SEC = 1.0    # the memory probe writes each of its pages this often


def _bare_loop(stop, out_q, tick: float, threshold: float, touch_mb: float = 0.0) -> None:
    """Child process: sleep ``tick`` again and again; note every wake-up more
    than ``threshold`` late as (start on the capture clock, seconds late). It
    does nothing else, so its stalls are the computer's, not the app's.

    ``touch_mb`` > 0: it also holds that much memory and writes one byte in
    each of its pages every ``PAGE_TOUCH_PERIOD_SEC`` (a few pages per tick),
    and a tick whose sleep + page writes ran more than ``threshold`` over is a
    stall too — what a computer that is paging does to every real program."""
    from brain_battle.capture import clock as _clock
    import mmap
    page = mmap.PAGESIZE
    buf = bytearray(int(touch_mb * 1024 * 1024)) if touch_mb > 0 else None
    n_pages = len(buf) // page if buf is not None else 0
    per_tick = max(1, int(round(n_pages * tick / PAGE_TOUCH_PERIOD_SEC))) if n_pages else 0
    nxt = 0
    for i in range(n_pages):
        buf[i * page] = 1                       # resident to start with
    stalls = []
    while not stop.is_set():
        t = time.perf_counter()
        time.sleep(tick)
        for _ in range(per_tick):
            buf[nxt * page] = (buf[nxt * page] + 1) & 0xFF
            nxt = (nxt + 1) % n_pages
        late = time.perf_counter() - t - tick
        if late > threshold:
            stalls.append((_clock.monotonic() - late - tick, late + tick))
    out_q.put(stalls)


class MachineStallProbe:
    """A bare process beside the app that only sleeps and wakes (``_bare_loop``):
    every time IT is woken late, the whole computer paused — nothing the app did.
    ``tools/soak_test.py`` runs one for the whole soak and judges lost camera
    frames against it: a frame lost while the computer was paused is the
    machine's noise; one lost while it ran normally is a problem of the app.
    Times are on the capture clock (``clock.monotonic()`` is the system-wide
    performance counter, the same in every process), like the cameras' frame
    times. ``stop()`` returns the list of (start, duration_s)."""

    def __init__(self, tick: float = TICK_SEC, threshold: float = MACHINE_STALL_SEC,
                 touch_mb: float = 0.0):
        import multiprocessing as mp
        self._ctx = mp.get_context("spawn")
        self._stop = self._ctx.Event()
        self._q = self._ctx.Queue()
        self.touch_mb = float(touch_mb)
        self._proc = self._ctx.Process(target=_bare_loop, name="machine-stall-probe",
                                       args=(self._stop, self._q, tick, threshold, self.touch_mb),
                                       daemon=True)
        self.stalls: list[tuple[float, float]] = []

    @classmethod
    def fine(cls) -> "MachineStallProbe":
        """The fine probe: 1 ms sleeps, every wake-up 2 ms late noted — it sees
        the short pauses the default probe (5 ms sleeps, 10 ms threshold) cannot.
        Used to explain a lost frame, not to excuse one on its own."""
        return cls(tick=FINE_TICK_SEC, threshold=FINE_STALL_SEC)

    def start(self) -> "MachineStallProbe":
        self._proc.start()
        return self

    def alive(self) -> bool:
        return self._proc.is_alive()

    def stop(self, timeout: float = 10.0) -> list:
        self._stop.set()
        try:
            self.stalls = list(self._q.get(timeout=timeout))
        except Exception:
            self.stalls = []
        self._proc.join(timeout=timeout)
        if self._proc.is_alive():
            self._proc.terminate()
        return self.stalls


def overlapping(stalls, a: float, b: float) -> list:
    """The stalls (start, duration_s) that overlap the interval [a, b]."""
    return [(s, d) for s, d in stalls if s <= b and s + d >= a]


def stalls_per_minute(stalls, seconds: float) -> float:
    """How many machine stalls per minute over ``seconds`` of watching."""
    return len(stalls) / max(seconds / 60.0, 1e-9)


def judge_lost_frames(gaps, stalls, fps: float, margin: float | None = None) -> dict:
    """Split lost camera frames into those inside one of the machine's own
    stalls (noise) and those outside (a problem). ``gaps`` are (t_start,
    duration_s, camera) intervals between two frames read more than 1.5
    periods apart, on the capture clock; each loses round(duration·fps) − 1
    frames. A gap counts as inside a stall when a stall overlaps it (widened by
    ``margin``, a frame period by default: a frame due just after a pause is
    also missed)."""
    per = 1.0 / fps
    margin = per if margin is None else margin
    res = {"lost_in_stalls": 0, "lost_outside": 0, "gaps_in_stalls": 0, "gaps_outside": 0,
           "machine_stalls": len(stalls),
           "worst_machine_stall_ms": round(max((d for _t, d in stalls), default=0) * 1000, 1),
           "outside_examples": []}
    for t, d, cam in gaps:
        lost = max(0, int(round(d / per)) - 1)
        if lost == 0:
            continue
        a, b = t - margin, t + d + margin
        inside = any(s <= b and s + sd >= a for s, sd in stalls)
        key = "in_stalls" if inside else "outside"
        res[f"lost_{key}"] += lost
        res[f"gaps_{key}"] += 1
        if not inside and len(res["outside_examples"]) < 8:
            res["outside_examples"].append({"camera": cam, "gap_ms": round(d * 1000), "lost": lost})
    return res


__all__ = ["StallProbe", "TICK_SEC", "STALL_SEC", "wake_lateness", "MachineStallProbe",
           "MACHINE_STALL_SEC", "FINE_TICK_SEC", "FINE_STALL_SEC", "NOISY_STALLS_PER_MIN",
           "judge_lost_frames", "overlapping", "stalls_per_minute", "PAGE_TOUCH_PERIOD_SEC"]
