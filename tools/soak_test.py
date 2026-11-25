#!/usr/bin/env python3
"""
soak_test.py — a long recording, then several in a row: does anything leak,
lag or get lost?

Runs the real app (BrainBattleApp) with SIMULATED devices — 4 cameras at the
chosen frame rate, 2 headbands with movement sensors — through the real
capture, encoding and file-writing paths:

  1. one full recording of the default plan (15 s lead-in, 60 s baseline,
     3 rounds of 3 min with 1-min breaks ≈ 12 min), then
  2. several short recordings in a row.

and reports, per recording:

  • frames: per camera, frames written vs. expected from the camera's own
    clock ("lost"), frames read more than half a period late ("late": their
    timestamps are late too), frames dropped by the encoder queue, the
    deepest the encoder queue got;
  • samples: per headband, EEG samples written vs. expected and gaps in the
    sample times (missing samples);
  • memory: resident memory every 10 s (start, end, peak, growth per minute);
  • descriptors / threads / child processes when the flow is opened and after
    each recording (they must come back to the same numbers — see
    tools/measure_descriptors.py and ADR-0022).

    python3 tools/soak_test.py                         # 12-min + 3 × 1-min, 60 fps
    python3 tools/soak_test.py --minutes 2 --then 2    # a quick version
    python3 tools/soak_test.py --json soak.json

Diagnosis (debug only — see brain_battle/support/stall_probe.py):

    --diagnose          stall detector + GC timing + thread stacks during stalls;
                        every camera gap is lined up with them
    --no-preview        cameras send no preview pictures to the screen
    --no-encoder        frames go to a null sink instead of ffmpeg (the
                        timestamp sidecars are still written)
    --minimal-source    the simulated cameras hand out a ready-made picture
                        (no drawing per frame)
    --wake-check        measure 60 Hz wake-ups first; NOT JUDGED (exit 0) on a
                        computer that cannot keep them (the CI heavy checks)
    --trace DIR         each camera process writes per-frame step timing, its
                        garbage collections and pipe sends to DIR
    --fps 30            30 instead of 60 frames per second
    --in-process        cameras as threads of the app instead of processes
                        (how capture ran before ADR-0025)

Lost frames are judged against the computer's own stalls: two small processes
run beside the app for the whole soak (``stall_probe.MachineStallProbe``) —
one that only sleeps (it sees the computer not running programs: CPU stalls)
and one that also keeps 48 MB in use, writing every page about once a second
(it sees the computer paging: memory stalls, which the first cannot see). A
frame lost while either was held up was lost to the machine (reported as
machine noise, split CPU / memory); a frame lost while the machine ran
normally is a problem. On a computer that is not short of memory the second
probe's pages stay put and it reports nothing. The header prints the RAM and
swap in use, and each recording how much the computer swapped in. The last
line is the verdict:

    VERDICT: OK — 0 frames lost outside the machine's own stalls (14 lost during
    9 machine stalls, worst 120 ms: machine noise); EEG complete; …

Exit code 0 when no frame was lost outside a machine stall, no EEG sample is
missing, the encoder dropped nothing, nothing failed to save and descriptors
came back to baseline; 1 otherwise. Not part of tools/run_tests.py (it takes ~16 minutes);
tests/test_stability.py runs a one-minute version.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


MEMORY_PROBE_MB = 48     # the memory-stall probe's working set (see stall_probe)


def _plan_for(minutes: float):
    """(rounds, round_sec, break_sec, baseline_sec) for about ``minutes``."""
    from brain_battle.capture.session_controller import SessionConfig
    if minutes >= 11:
        return SessionConfig(num_rounds=3, round_sec=180, break_sec=60, baseline_sec=60)
    total = max(30, int(minutes * 60) - 15)
    rnd = max(10, int(total * 0.5 / 2))
    return SessionConfig(num_rounds=2, round_sec=rnd, break_sec=max(5, int(total * 0.1)),
                         baseline_sec=max(5, total - 2 * rnd - max(5, int(total * 0.1))))


def camera_gaps(root: str, fps: float) -> list[tuple[float, float, str]]:
    """Every gap of more than 1.5 frame periods in any camera's frame times:
    (t_monotonic of the frame before, gap seconds, camera)."""
    import numpy as np
    import brain_battle.data.session_storage as ss
    out = []
    for stem, _mp4, side in ss.camera_files(root):
        try:
            t = ss.read_csv_tolerant(side)["t_monotonic"].to_numpy(dtype=float)
        except Exception:
            continue
        d = np.diff(t)
        for i in np.nonzero(d > 1.5 / fps)[0]:
            out.append((float(t[i]), float(d[i]), stem))
    return sorted(out)


def correlate(gaps, probe, t0_mono: float) -> dict:
    """How many camera gaps (and lost frames) coincide with a stall of the
    process, and with a garbage collection."""
    res = {"gaps": len(gaps), "gaps_with_stall": 0, "gaps_with_gc": 0,
           "stalls": len(probe.stalls),
           "stalls_over_50ms": sum(1 for _t, d in probe.stalls if d > 0.05),
           "worst_stall_ms": round(max((d for _t, d in probe.stalls), default=0) * 1000, 1),
           "gc": probe.gc_summary(), "examples": []}
    for t, gap, stem in gaps:
        a, b = t, t + gap
        st = [s for s in probe.stalls if s[0] <= b and s[0] + s[1] >= a]
        gcs = [g for g in probe.gcs if g[0] <= b and g[0] + g[1] >= a]
        res["gaps_with_stall"] += bool(st)
        res["gaps_with_gc"] += bool(gcs)
        if len(res["examples"]) < 12 and gap > 0.08:
            res["examples"].append({
                "at_s": round(t - t0_mono, 2), "camera": stem, "gap_ms": round(gap * 1000),
                "stall_ms": round(max((s[1] for s in st), default=0) * 1000),
                "gc": [f"gen{g[2]} {g[1] * 1000:.0f} ms" for g in gcs]})
    return res


def analyse_recording(root: str, fps: float) -> dict:
    """Frames / samples written vs. expected, and gaps, from the files."""
    import numpy as np
    import brain_battle.data.session_storage as ss
    out = {"cameras": {}, "headbands": {}}
    markers = ss.read_phase_markers(root)
    end = markers[-1][0] if markers else 0.0
    for stem, mp4, side in ss.camera_files(root):
        try:
            df = ss.read_csv_tolerant(side)
            t = df["t_session_seconds"].to_numpy(dtype=float)
            arr = (df["t_arrival_monotonic"].to_numpy(dtype=float)
                   if "t_arrival_monotonic" in df.columns else None)
        except Exception:
            t = np.zeros(0); arr = None
        if len(t) < 2:
            out["cameras"][stem] = {"frames": int(len(t)), "lost": None, "late": None}
            continue
        d = np.diff(t)
        per = 1.0 / fps
        expected = int(round((t[-1] - t[0]) * fps)) + 1
        # lost: frames absent from the file, counted against the camera's own
        # steady clock (it captures one every 1/fps). late: intervals of more
        # than 1.5 frame periods — a frame read late (its stamp is late too),
        # usually followed by a short interval when nothing was lost; the
        # first version of this tool counted these as "missing".
        # read late: from the read times when the sidecar has them (the frame
        # stamps are on the camera's fitted clock and do not show it)
        da = np.diff(arr) if arr is not None and len(arr) == len(t) else d
        late = int(np.sum(da > 1.5 * per))
        # stamps off the steady clock: intervals between stamps that are not
        # a whole number of frame periods (± a quarter)
        k = np.round(d / per)
        off = int(np.sum((k >= 1) & (np.abs(d - k * per) > 0.25 * per)))
        out["cameras"][stem] = {
            "frames": int(len(t)), "span_s": round(float(t[-1] - t[0]), 2),
            "expected": expected, "lost": max(0, expected - int(len(t))),
            "late": late, "stamps_off_clock": off,
            "max_gap_ms": round(float(d.max()) * 1000, 1),
            "big_gaps_at_s": [round(float(t[i]), 1) for i in np.nonzero(d > 0.08)[0][:8]]}
    for who, path in ss.eeg_files(root).items():
        try:
            t = ss.read_csv_tolerant(path)["t_session_seconds"].to_numpy(dtype=float)
        except Exception:
            t = np.zeros(0)
        if len(t) < 2:
            out["headbands"][who] = {"samples": int(len(t)), "missing": None}
            continue
        d = np.diff(t)
        per = 1.0 / 256
        missing = int(np.sum(np.maximum(0, np.round(d / per) - 1)[d > 1.5 * per]))
        out["headbands"][who] = {
            "samples": int(len(t)), "span_s": round(float(t[-1] - t[0]), 2),
            "expected": int(round((t[-1] - t[0]) * 256)) + 1, "missing": missing}
    out["recorded_s"] = round(end, 1)
    return out


SLOW_CALLS: list = []


def _wrap_for_cpu(classes, limit_ms: float = 10.0) -> None:
    """Debug: log every method call that uses more than ``limit_ms`` of its
    own thread's CPU time (``time.thread_time`` — work done, not waiting)."""
    import functools
    import inspect
    from brain_battle.capture import clock as _clock

    def wrap(cls, name, fn):
        @functools.wraps(fn)
        def inner(*a, **k):
            c0 = time.thread_time(); w0 = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                cpu = (time.thread_time() - c0) * 1000
                if cpu > limit_ms:
                    SLOW_CALLS.append((_clock.monotonic(), f"{cls.__name__}.{name}", round(cpu, 1),
                                       round((time.perf_counter() - w0) * 1000, 1)))
        return inner
    for cls in classes:
        for name, fn in list(vars(cls).items()):
            if inspect.isfunction(fn) and not name.startswith("__"):
                setattr(cls, name, wrap(cls, name, fn))


def _slow_report(t0_mono: float) -> None:
    from collections import Counter
    cnt, tot, worst = Counter(), Counter(), {}
    for _t, name, cpu, _w in SLOW_CALLS:
        cnt[name] += 1; tot[name] += cpu; worst[name] = max(worst.get(name, 0), cpu)
    print("  CALLS OVER 10 ms CPU (count, total ms, worst ms):", flush=True)
    for name, _ in tot.most_common(12):
        print(f"    {name}: {cnt[name]} × , {tot[name]:.0f} ms total, worst {worst[name]:.0f} ms",
              flush=True)
    SLOW_CALLS.clear()


def _thread_cpu(proc) -> dict:
    """{native thread id: cpu seconds} for this process."""
    return {t.id: t.user_time + t.system_time for t in proc.threads()}


def _thread_cpu_report(proc, before: dict) -> None:
    """CPU seconds per thread during the recording, by thread name (Python
    threads; Qt's own threads show as 'other')."""
    import threading
    names = {t.native_id: t.name for t in threading.enumerate()}
    now = _thread_cpu(proc)
    rows = {}
    for tid, sec in now.items():
        name = names.get(tid, "other (Qt / native)")
        key = "camera reader (QThread)" if name.startswith("Dummy") else name
        rows[key] = rows.get(key, 0.0) + sec - before.get(tid, 0.0)
    print("  CPU seconds per thread:", ", ".join(
        f"{k}: {v:.1f}" for k, v in sorted(rows.items(), key=lambda kv: -kv[1])[:12]), flush=True)


def _apply_variants(args) -> None:
    """The experiment switches (debug only). They are environment variables so
    they reach the camera processes."""
    debug = []
    if args.no_preview:
        debug.append("no-preview")
    if args.no_encoder:
        debug.append("null-encoder")
    if args.trace:
        debug.append("trace")
        os.environ["BRAIN_BATTLE_CAPTURE_TRACE_DIR"] = args.trace
    if debug:
        os.environ["BRAIN_BATTLE_CAPTURE_DEBUG"] = ",".join(debug)
    if args.minimal_source:
        os.environ["BRAIN_BATTLE_SIM_PICTURE"] = "plain"
    if args.in_process:
        os.environ["BRAIN_BATTLE_CAMERA_PROCESS"] = "0"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--minutes", type=float, default=12.0, help="length of the long recording")
    ap.add_argument("--then", type=int, default=3, help="short recordings afterwards")
    ap.add_argument("--then-minutes", type=float, default=1.0)
    ap.add_argument("--fps", type=int, default=60, choices=(30, 60))
    ap.add_argument("--json", default="")
    ap.add_argument("--gc-debug", action="store_true", help="print every slow garbage collection")
    ap.add_argument("--diagnose", action="store_true")
    ap.add_argument("--stack-file", default="")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--no-encoder", action="store_true")
    ap.add_argument("--minimal-source", action="store_true")
    ap.add_argument("--wake-check", action="store_true",
                    help="measure 60 Hz wake-ups first; on a computer that cannot keep them, "
                         "print NOT JUDGED and exit 0 (CI runners)")
    ap.add_argument("--trace", default="", metavar="DIR",
                    help="each camera process writes its per-frame timing to DIR (debug)")
    ap.add_argument("--in-process", action="store_true",
                    help="cameras as threads of the app (how it was before ADR-0025)")
    ap.add_argument("--no-ui-tick", action="store_true",
                    help="no screen redraws while recording (the live EEG, the countdown)")
    ap.add_argument("--switch-ms", type=float, default=None,
                    help="override the thread switch interval (experiments)")
    args = ap.parse_args(argv)
    if args.switch_ms:
        sys.setswitchinterval(args.switch_ms / 1000.0)
    if args.gc_debug:
        import gc
        _gc_t = {}
        def _cb(phase, info):
            if phase == "start":
                _gc_t["t"] = time.perf_counter()
            elif phase == "stop":
                dt = (time.perf_counter() - _gc_t.get("t", time.perf_counter())) * 1000
                if dt > 20:
                    print(f"    [gc gen{info['generation']}: {dt:.0f} ms, collected {info['collected']}]", flush=True)
        gc.callbacks.append(_cb)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["BRAIN_BATTLE_SIMULATE"] = "1"
    os.environ["BRAIN_BATTLE_MUTE"] = "1"         # the soak rings many bells: keep it silent
    cap_dir = tempfile.mkdtemp(prefix="bb_soak_")
    os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = cap_dir
    os.environ["BRAIN_BATTLE_CONFIG"] = os.path.join(tempfile.mkdtemp(), "settings.json")

    import psutil
    from brain_battle.support import resource_limits as rl
    from brain_battle.support.stall_probe import MachineStallProbe, judge_lost_frames
    judgeable, wake_text = True, ""
    if args.wake_check:
        from brain_battle.support.stall_probe import wake_lateness
        w = wake_lateness(5.0)
        judgeable = w["over_half"] <= 0.10          # the rule of tests/_env.timing_judgeable
        wake_text = (f"this computer wakes a 60 Hz thread {w['median']:.1f} ms late (p99 "
                     f"{w['p99']:.1f} ms; {100 * w['over_half']:.0f} % over half a frame)")
        print(f"MACHINE: {wake_text} — {'judged' if judgeable else 'NOT JUDGED'}", flush=True)
    machine = MachineStallProbe().start()       # the computer's own stalls, all soak long
    machine_mem = MachineStallProbe(touch_mb=MEMORY_PROBE_MB).start()   # … and its paging
    from brain_battle.capture.usb_camera import isolated_capture
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    print(f"Machine: {psutil.cpu_count()} cores ({psutil.cpu_count(logical=False)} physical), "
          f"{sys.platform}; {vm.total / 2**30:.0f} GB RAM ({vm.available / 2**30:.1f} GB available), "
          f"swap in use {sw.used / 2**30:.1f} GB; cameras in "
          f"{'their own processes' if isolated_capture() else 'threads of the app'}", flush=True)
    _apply_variants(args)
    probe = None
    if args.diagnose:
        from brain_battle.support.stall_probe import StallProbe
        probe = StallProbe(stack_file=args.stack_file or None)
        probe.start()
        import brain_battle.ui.record_flow as _rf
        import brain_battle.ui.record_screens as _rs
        import brain_battle.capture.camera_source as _cs
        import brain_battle.capture.headband_source as _hs
        import brain_battle.capture.session_controller as _sc
        import brain_battle.ui.eeg_viewer as _ev
        _wrap_for_cpu([_rf.RecordFlow, _rs.RecordingScreen, _rs.GetReadyScreen, _rs.LiveBoxerCard,
                       _rs.CameraTile, _rs.PhaseBar, _cs.CameraSlot, _hs.HeadbandSlot,
                       _sc.SessionController, _ev.EEGViewer])
    import brain_battle.data.app_settings as app_settings
    s = app_settings.load()
    s.labeller_name = "Soak"
    s.record_60fps = args.fps == 60
    s.record_motion = True
    s.save()

    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    proc = psutil.Process()
    mem = []

    def pump(secs):
        t0 = time.time()
        while time.time() - t0 < secs:
            app.processEvents(); time.sleep(0.02)

    from brain_battle.app import BrainBattleApp
    win = BrainBattleApp()
    pump(1.0)
    flow = win.record_flow
    report = {"fps": args.fps, "recordings": [], "descriptors": []}
    ok = True
    desc_ok = True

    def snap(label):
        sn = rl.snapshot()
        sn["children"] = max(0, sn["children"] - machine.alive() - machine_mem.alive())  # the probes are not the app's
        import gc as _gc
        from PyQt6.QtCore import QThread as _QT
        _qts = [type(o).__name__ for o in _gc.get_objects() if isinstance(o, _QT)]
        sn["qthreads"] = len(_qts)
        sn["qthread_kinds"] = {k: _qts.count(k) for k in sorted(set(_qts))}
        report["descriptors"].append({"point": label, **sn})
        print(f"  {label:<36} fds {sn['fds']:>4}  threads {sn['threads']:>3}  "
              f"children {sn['children']:>2}  Qt threads {sn['qthreads']:>3} {sn['qthread_kinds']}", flush=True)
        return sn

    plans = [_plan_for(args.minutes)] + [_plan_for(args.then_minutes)] * args.then
    baseline = None
    for n, cfg in enumerate(plans, start=1):
        print(f"\nRecording {n} of {len(plans)} …", flush=True)
        flow.start(); pump(3.0)
        snap(f"#{n} flow opened")
        flow.set_plan(cfg)
        flow.set_fighters("K7Q2", "M3XA", "Red", "Blue")
        rss0 = proc.memory_info().rss
        t_start = time.time()
        from brain_battle.capture import clock as _clock
        t0_mono = _clock.monotonic()
        n_stalls0 = len(probe.stalls) if probe else 0
        swap_in0 = psutil.swap_memory().sin
        flow.begin_recording()
        if args.no_ui_tick:
            flow._tick.stop()
        thread_cpu0 = _thread_cpu(proc) if probe is not None else {}
        queue_peak = {}
        last_mem = 0.0
        cpu = []
        child_procs = {}
        psutil.cpu_percent(None); proc.cpu_percent(None)
        while flow.is_recording():
            pump(0.5)
            for slot in flow._cams:
                peak, capq = slot.queue_use
                queue_peak[slot.stem] = max(queue_peak.get(slot.stem, 0), peak)
            if time.time() - last_mem >= 10:
                last_mem = time.time()
                mem.append((n, round(time.time() - t_start, 1), proc.memory_info().rss))
                cams = enc = 0.0
                for ch in proc.children(recursive=True):
                    try:
                        if ch.pid not in child_procs:
                            child_procs[ch.pid] = ch; ch.cpu_percent(None); continue
                        v = child_procs[ch.pid].cpu_percent(None)
                        if "ffmpeg" in ch.name().lower():
                            enc += v
                        else:
                            cams += v
                    except psutil.Error:
                        pass
                cpu.append((psutil.cpu_percent(None), proc.cpu_percent(None), cams, enc))
        dur = time.time() - t_start
        if probe is not None:
            _slow_report(t0_mono)
            _thread_cpu_report(proc, thread_cpu0)
        dropped = {s.stem: s.dropped for s in flow._cams}
        pump(1.0)
        flow.wait_for_xdf(60); pump(0.5)
        root = flow._saved_root
        files = analyse_recording(root, float(args.fps))
        rss1 = proc.memory_info().rss
        flow._leave_to_sessions(); pump(2.0)
        after = snap(f"#{n} left the flow")
        import gc as _gc
        _gc.collect(); pump(3.0)
        snap(f"#{n} left the flow, after gc")
        if cpu[1:]:
            c = cpu[1:]
            rec_cpu = {"machine_pct": round(sum(x[0] for x in c) / len(c), 1),
                       "app_pct": round(sum(x[1] for x in c) / len(c), 1),
                       "camera_processes_pct": round(sum(x[2] for x in c) / len(c), 1),
                       "encoders_pct": round(sum(x[3] for x in c) / len(c), 1),
                       "machine_cores": psutil.cpu_count()}
            print(f"  CPU: machine {rec_cpu['machine_pct']} % of {rec_cpu['machine_cores']} "
                  f"cores; the app {rec_cpu['app_pct']} %, camera processes "
                  f"{rec_cpu['camera_processes_pct']} %, ffmpeg {rec_cpu['encoders_pct']} % "
                  f"(100 % = one core)", flush=True)
        else:
            rec_cpu = {}
        rec = {"n": n, "wall_s": round(dur, 1), "root": root, "files": files, "cpu": rec_cpu,
               "encoder_queue_dropped": dropped, "queue_peak": queue_peak,
               "rss_start_mb": round(rss0 / 2**20, 1), "rss_end_mb": round(rss1 / 2**20, 1),
               "write_problems": flow.write_problems()}
        report["recordings"].append(rec)
        miss_f = sum((c.get("lost") or 0) for c in files["cameras"].values())
        late_f = sum((c.get("late") or 0) for c in files["cameras"].values())
        miss_s = sum((h.get("missing") or 0) for h in files["headbands"].values())
        rec["machine_swapped_in_mb"] = round((psutil.swap_memory().sin - swap_in0) / 2**20, 1)
        print(f"  the computer swapped in {rec['machine_swapped_in_mb']} MB during this recording",
              flush=True)
        print(f"  recorded {files['recorded_s']} s · frames lost {miss_f} · frames late {late_f} "
              f"· encoder drops "
              f"{sum(dropped.values())} · EEG samples missing {miss_s} · queue peak "
              f"{max(queue_peak.values() or [0])} · memory {rec['rss_start_mb']} → "
              f"{rec['rss_end_mb']} MB", flush=True)
        for stem, c in files["cameras"].items():
            print(f"    {stem}: {c}", flush=True)
        for who, h in files["headbands"].items():
            print(f"    EEG {who}: {h}", flush=True)
        if probe is not None:
            cor = correlate(camera_gaps(root, float(args.fps)), probe, t0_mono)
            rec["diagnosis"] = cor
            print(f"  DIAGNOSIS: {cor['gaps']} camera gaps, {cor['gaps_with_stall']} during a "
                  f"process stall, {cor['gaps_with_gc']} during a garbage collection; "
                  f"stalls this recording {len(probe.stalls) - n_stalls0} (all so far "
                  f"{cor['stalls']}, >50 ms {cor['stalls_over_50ms']}, worst "
                  f"{cor['worst_stall_ms']} ms); gc {cor['gc']}", flush=True)
            for ex in cor["examples"]:
                print(f"    gap {ex}", flush=True)
        rec["_gaps"] = camera_gaps(root, float(args.fps))
        if miss_s or sum(dropped.values()) or rec["write_problems"]:
            ok = False
        # Each visit must leave the same as the first one left (the open flow
        # itself holds the camera processes and their pipes).
        baseline = baseline or after
        if abs(after["fds"] - baseline["fds"]) > 6 or after["children"] != baseline["children"]:
            ok = False
            desc_ok = False

    flow.shutdown(); pump(0.5)
    if probe is not None:
        probe.stop()
    cpu_stalls = machine.stop()
    mem_stalls = machine_mem.stop()
    stalls = sorted(cpu_stalls + mem_stalls)
    lost_out = lost_in = lost_cpu_only_out = 0
    for rec in report["recordings"]:
        gaps = rec.pop("_gaps")
        j = judge_lost_frames(gaps, stalls, float(args.fps))
        jc = judge_lost_frames(gaps, cpu_stalls, float(args.fps))   # as judged before 2026-11-24
        j["lost_outside_cpu_stalls_only"] = jc["lost_outside"]
        rec["lost_frames_judged"] = j
        lost_out += j["lost_outside"]; lost_in += j["lost_in_stalls"]
        lost_cpu_only_out += jc["lost_outside"]
        print(f"  #{rec['n']}: frames lost outside the machine's stalls {j['lost_outside']}"
              f" ({j['gaps_outside']} gaps), inside {j['lost_in_stalls']} ({j['gaps_in_stalls']} "
              f"gaps); judged by CPU stalls alone: {jc['lost_outside']} outside"
              f"{'; e.g. ' + str(j['outside_examples'][:3]) if j['outside_examples'] else ''}",
              flush=True)
    if lost_out:
        ok = False
    eeg_missing = sum((h.get("missing") or 0) for rec in report["recordings"]
                      for h in rec["files"]["headbands"].values())
    drops = sum(sum(rec["encoder_queue_dropped"].values()) for rec in report["recordings"])
    saves = sum(len(rec["write_problems"]) for rec in report["recordings"])
    worst = round(max((d for _t, d in stalls), default=0) * 1000)
    report["verdict"] = {"ok": ok, "lost_outside_machine_stalls": lost_out,
                         "lost_in_machine_stalls": lost_in, "machine_stalls": len(stalls),
                         "machine_cpu_stalls": len(cpu_stalls), "machine_memory_stalls": len(mem_stalls),
                         "lost_outside_cpu_stalls_only": lost_cpu_only_out,
                         "worst_machine_stall_ms": worst, "eeg_missing": eeg_missing,
                         "encoder_drops": drops, "save_problems": saves}
    long = [m for m in mem if m[0] == 1]
    if len(long) >= 3:
        mins = (long[-1][1] - long[0][1]) / 60.0
        growth = (long[-1][2] - long[0][2]) / 2**20 / max(mins, 1e-9)
        report["memory_growth_mb_per_min_long"] = round(growth, 2)
        report["memory_peak_mb"] = round(max(m[2] for m in mem) / 2**20, 1)
        print(f"\nMemory during the long recording: {long[0][2] / 2**20:.0f} → "
              f"{long[-1][2] / 2**20:.0f} MB ({growth:+.2f} MB/min), peak "
              f"{report['memory_peak_mb']} MB", flush=True)
    report["memory_samples"] = mem
    report["ok"] = ok
    kinds = f"{len(cpu_stalls)} CPU + {len(mem_stalls)} memory"
    noise = (f"{lost_in} lost during {kinds} machine stalls, worst {worst} ms: machine noise"
             if lost_in else f"{kinds} machine stalls, worst {worst} ms")
    print(f"\nVERDICT: {'OK' if ok else 'PROBLEMS'} — {lost_out} frame"
          f"{'' if lost_out == 1 else 's'} lost outside the machine's own stalls ({noise}); "
          f"EEG {'complete' if not eeg_missing else f'{eeg_missing} samples missing'}; "
          f"encoder drops {drops}; save problems {saves}; descriptors "
          f"{'back to baseline' if desc_ok else 'NOT back to baseline'}.", flush=True)
    report["judged"] = judgeable
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    if not judgeable:
        print(f"NOT JUDGED — {wake_text}: frames are lost here whatever the app does", flush=True)
        return 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
