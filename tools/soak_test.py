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

  • frames: per camera, frames written vs. expected, frames dropped by the
    encoder queue, gaps in the frame times (frames the camera never delivered),
    the deepest the encoder queue got;
  • samples: per headband, EEG samples written vs. expected and gaps in the
    sample times (missing samples);
  • memory: resident memory every 10 s (start, end, peak, growth per minute);
  • descriptors / threads / child processes when the flow is opened and after
    each recording (they must come back to the same numbers — see
    tools/measure_descriptors.py and ADR-0022).

    python3 tools/soak_test.py                         # 12-min + 3 × 1-min, 60 fps
    python3 tools/soak_test.py --minutes 2 --then 2    # a quick version
    python3 tools/soak_test.py --json soak.json

Exit code 0 when nothing was dropped or missing and descriptors came back to
baseline; 1 otherwise. Not part of tools/run_tests.py (it takes ~16 minutes);
tests/test_stability.py runs a one-minute version.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _plan_for(minutes: float):
    """(rounds, round_sec, break_sec, baseline_sec) for about ``minutes``."""
    from brain_battle.capture.session_controller import SessionConfig
    if minutes >= 11:
        return SessionConfig(num_rounds=3, round_sec=180, break_sec=60, baseline_sec=60)
    total = max(30, int(minutes * 60) - 15)
    rnd = max(10, int(total * 0.5 / 2))
    return SessionConfig(num_rounds=2, round_sec=rnd, break_sec=max(5, int(total * 0.1)),
                         baseline_sec=max(5, total - 2 * rnd - max(5, int(total * 0.1))))


def analyse_recording(root: str, fps: float) -> dict:
    """Frames / samples written vs. expected, and gaps, from the files."""
    import numpy as np
    import brain_battle.data.session_storage as ss
    out = {"cameras": {}, "headbands": {}}
    markers = ss.read_phase_markers(root)
    end = markers[-1][0] if markers else 0.0
    for stem, mp4, side in ss.camera_files(root):
        try:
            t = ss.read_csv_tolerant(side)["t_session_seconds"].to_numpy(dtype=float)
        except Exception:
            t = np.zeros(0)
        if len(t) < 2:
            out["cameras"][stem] = {"frames": int(len(t)), "missing": None}
            continue
        d = np.diff(t)
        per = 1.0 / fps
        missing = int(np.sum(np.maximum(0, np.round(d / per) - 1)[d > 1.5 * per]))
        out["cameras"][stem] = {
            "frames": int(len(t)), "span_s": round(float(t[-1] - t[0]), 2),
            "expected": int(round((t[-1] - t[0]) * fps)) + 1,
            "missing": missing, "max_gap_ms": round(float(d.max()) * 1000, 1),
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--minutes", type=float, default=12.0, help="length of the long recording")
    ap.add_argument("--then", type=int, default=3, help="short recordings afterwards")
    ap.add_argument("--then-minutes", type=float, default=1.0)
    ap.add_argument("--fps", type=int, default=60, choices=(30, 60))
    ap.add_argument("--json", default="")
    ap.add_argument("--gc-debug", action="store_true", help="print every slow garbage collection")
    args = ap.parse_args(argv)
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
    cap_dir = tempfile.mkdtemp(prefix="bb_soak_")
    os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = cap_dir
    os.environ["BRAIN_BATTLE_CONFIG"] = os.path.join(tempfile.mkdtemp(), "settings.json")

    import psutil
    from brain_battle.support import resource_limits as rl
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

    def snap(label):
        sn = rl.snapshot()
        report["descriptors"].append({"point": label, **sn})
        print(f"  {label:<36} fds {sn['fds']:>4}  threads {sn['threads']:>3}  "
              f"children {sn['children']:>2}", flush=True)
        return sn

    plans = [_plan_for(args.minutes)] + [_plan_for(args.then_minutes)] * args.then
    baseline = None
    for n, cfg in enumerate(plans, start=1):
        print(f"\nRecording {n} of {len(plans)} …", flush=True)
        flow.start(); pump(3.0)
        opened = snap(f"#{n} flow opened")
        baseline = baseline or opened
        flow.set_plan(cfg)
        flow.set_fighters("K7Q2", "M3XA", "Red", "Blue")
        rss0 = proc.memory_info().rss
        t_start = time.time()
        flow.begin_recording()
        queue_peak = {}
        last_mem = 0.0
        while flow.is_recording():
            pump(0.5)
            for slot in flow._cams:
                peak, capq = slot.queue_use
                queue_peak[slot.stem] = max(queue_peak.get(slot.stem, 0), peak)
            if time.time() - last_mem >= 10:
                last_mem = time.time()
                mem.append((n, round(time.time() - t_start, 1), proc.memory_info().rss))
        dur = time.time() - t_start
        dropped = {s.stem: s.dropped for s in flow._cams}
        pump(1.0)
        flow.wait_for_xdf(60); pump(0.5)
        root = flow._saved_root
        files = analyse_recording(root, float(args.fps))
        rss1 = proc.memory_info().rss
        flow._leave_to_sessions(); pump(2.0)
        after = snap(f"#{n} left the flow")
        rec = {"n": n, "wall_s": round(dur, 1), "root": root, "files": files,
               "encoder_queue_dropped": dropped, "queue_peak": queue_peak,
               "rss_start_mb": round(rss0 / 2**20, 1), "rss_end_mb": round(rss1 / 2**20, 1),
               "write_problems": flow.write_problems()}
        report["recordings"].append(rec)
        miss_f = sum((c.get("missing") or 0) for c in files["cameras"].values())
        miss_s = sum((h.get("missing") or 0) for h in files["headbands"].values())
        print(f"  recorded {files['recorded_s']} s · frames missing {miss_f} · encoder drops "
              f"{sum(dropped.values())} · EEG samples missing {miss_s} · queue peak "
              f"{max(queue_peak.values() or [0])} · memory {rec['rss_start_mb']} → "
              f"{rec['rss_end_mb']} MB", flush=True)
        for stem, c in files["cameras"].items():
            print(f"    {stem}: {c}", flush=True)
        for who, h in files["headbands"].items():
            print(f"    EEG {who}: {h}", flush=True)
        if miss_f or miss_s or sum(dropped.values()) or rec["write_problems"]:
            ok = False
        if abs(after["fds"] - baseline["fds"]) > 6 or after["children"] != baseline["children"]:
            ok = False

    flow.shutdown(); pump(0.5)
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
    print(f"\n{'OK' if ok else 'PROBLEMS'}: see above.", flush=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
