#!/usr/bin/env python3
"""
measure_descriptors.py — how many open files does a recording really need?

Runs the New-recording flow with SIMULATED devices (4 cameras, 2 headbands,
LSL streams on) through the real capture paths, several recordings in a row,
and prints what the process holds open at each point: file descriptors (split
into files / sockets / pipes / other), threads, and child processes.

    python3 tools/measure_descriptors.py                 # 3 short recordings
    python3 tools/measure_descriptors.py --recordings 5
    python3 tools/measure_descriptors.py --no-lsl        # the same without LSL
    python3 tools/measure_descriptors.py --json out.json

Use it when "Too many open files" shows up, or after touching anything that
owns a file, socket, pipe, thread or subprocess. A healthy run returns to
(close to) the "flow opened" numbers after every recording.
The counting itself lives in brain_battle/support/resource_limits.py.
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--recordings", type=int, default=3)
    ap.add_argument("--no-lsl", action="store_true")
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["BRAIN_BATTLE_SIMULATE"] = "1"
    os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = tempfile.mkdtemp(prefix="bb_fd_cap_")
    os.environ["BRAIN_BATTLE_CONFIG"] = os.path.join(tempfile.mkdtemp(), "settings.json")

    from brain_battle.support import resource_limits as rl
    rows = []

    def snap(label):
        s = rl.snapshot()
        rows.append({"point": label, **s})
        print(f"{label:<44} fds {s['fds']:>4}  (files {s['files']:>3} · sockets "
              f"{s['sockets']:>3} · pipes {s['pipes']:>3} · other {s['other']:>3})  "
              f"threads {s['threads']:>3}  children {s['children']:>2}", flush=True)

    soft, hard = rl.open_file_limit()
    print(f"open-file limit: soft {soft}, hard {hard}")
    snap("python started")

    import brain_battle.data.app_settings as app_settings
    s = app_settings.load()
    s.labeller_name = "Tester"
    s.lsl_streams = not args.no_lsl
    s.save()

    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    def pump(secs):
        t0 = time.time()
        while time.time() - t0 < secs:
            app.processEvents(); time.sleep(0.02)

    from brain_battle.app import BrainBattleApp
    win = BrainBattleApp()
    pump(1.0)
    snap("app started (Sessions)")
    flow = win.record_flow

    for n in range(1, args.recordings + 1):
        flow.start(); pump(2.5)
        snap(f"#{n} flow opened, devices connected")
        from brain_battle.capture.session_controller import SessionConfig
        flow.set_plan(SessionConfig(num_rounds=1, round_sec=10, break_sec=2, baseline_sec=2))
        flow.set_fighters("K7Q2", "M3XA")
        flow.begin_recording()
        pump(6.0)
        snap(f"#{n} recording")
        t0 = time.time()
        while flow.is_recording() and time.time() - t0 < 40:
            pump(0.1)
        pump(0.8)
        snap(f"#{n} stopped (Saved screen)")
        flow.wait_for_xdf(30); pump(0.3)
        snap(f"#{n} recording.xdf built")
        flow._leave_to_sessions(); pump(1.5)
        snap(f"#{n} left the flow")

    flow.shutdown(); pump(0.5)
    snap("flow shut down")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
