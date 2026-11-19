"""
Recording stability (Part 4): no lag, no disconnects, no lost data.

  1. Camera stall watchdog: a simulated camera stops delivering frames
     mid-recording. Within 2 s the Recording screen knows (slot.stalled, the
     health line); the camera is reopened and keeps recording into the SAME
     file; history has camera_stalled + camera_recovered; timing_quality.csv
     has a "stalled" and a "reopened after" row.
  2. Headband dropped three times in one recording, reconnected each time:
     one EEG file, one header, times only go forward, every segment's rows
     are there, history logs headband_reconnected.
  3. contact_quality.csv failing AFTER it opened (drive unplugged, disk full)
     reaches the operator: RecordFlow.write_problems(), the Recording screen
     text, and the Saved headline "Recording saved, but with problems".
  4. The encoder queue is sized for 2 s at the camera's rate and its use is
     reported (queue_use).
  5. A short soak (tools/soak_test.py, 1 minute + 1 more): no EEG samples
     missing, no encoder drops, descriptors back to the same numbers.

Run:  python3 tests/test_stability.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401
import csv
import glob
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

import brain_battle.data.session_history as sh
from brain_battle.capture.session_controller import SessionConfig

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def pump(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        _app.processEvents(); time.sleep(0.01)


def _fresh(**extra):
    os.environ["BRAIN_BATTLE_SIMULATE"] = "1"
    os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = tempfile.mkdtemp(prefix="bb_stab_")
    for k in ("BRAIN_BATTLE_SIMULATE_STALL_CAMERA", "BRAIN_BATTLE_SIMULATE_DROP_HEADBAND",
              "BRAIN_BATTLE_SIMULATE_MISSING_CAMERA"):
        os.environ.pop(k, None)
    os.environ.update(extra)
    from brain_battle.ui.record_flow import RecordFlow
    flow = RecordFlow()
    flow.start(); pump(2.5)
    return flow


def _events(root):
    return [e["event"] for e in sh.read_history(root)]


def test_camera_stall_watchdog():
    flow = _fresh(BRAIN_BATTLE_SIMULATE_STALL_CAMERA="SIDE_02:6:3")
    try:
        flow.set_plan(SessionConfig(num_rounds=1, round_sec=14, break_sec=0, baseline_sec=2))
        flow.set_fighters("K7Q2", "M3XA")
        slot = next(s for s in flow._cams if s.stem == "SIDE_02")
        check("stall camera is live before recording", slot.is_live)
        flow.begin_recording()
        t_flag = None
        t0 = time.monotonic()
        while flow.is_recording() and time.monotonic() - t0 < 40:
            pump(0.05)
            if t_flag is None and slot.stalled:
                t_flag = time.monotonic()
                check("the Recording health line names the stalled camera",
                      "Side camera 2" in flow.health_text() and "reconnecting" in flow.health_text(),
                      flow.health_text())
        pump(1.0)
        root = flow._saved_root
        kinds = [k for _t, k, _s in slot.stall_events]
        stall_t = next((t for t, k, _s in slot.stall_events if k == "stalled"), None)
        rec = next((s for t, k, s in slot.stall_events if k == "recovered"), None)
        check("watchdog noticed the stall (stalled, then recovered)",
              "stalled" in kinds and "recovered" in kinds, str(slot.stall_events))
        # the stall starts 6 s after the camera was first opened; flagged within 2 s + slack
        check("stall reported within ~2 s", t_flag is not None and rec is not None and rec < 6.0,
              f"gap {rec}")
        sidecars = glob.glob(os.path.join(root, "continuous", "*", "session_SIDE_02.timestamps.csv"))
        mp4s = glob.glob(os.path.join(root, "continuous", "*", "session_SIDE_02*.mp4"))
        t = [float(r["t_session_seconds"]) for r in csv.DictReader(open(sidecars[0]))]
        gaps = [b - a for a, b in zip(t, t[1:])]
        check("one video file for the camera — recording continued in the same file",
              len(mp4s) == 1 and len(sidecars) == 1, str(mp4s))
        check("frames before AND after the stall in that file (one gap of ~3 s)",
              max(gaps) > 2.0 and t[-1] > t[gaps.index(max(gaps))] + 2.0, f"max gap {max(gaps):.2f}")
        ev = _events(root)
        check("history: camera_stalled and camera_recovered",
              "camera_stalled" in ev and "camera_recovered" in ev, str(ev))
        rows = list(csv.DictReader(open(os.path.join(root, "timing_quality.csv"))))
        evs = [r["event"] for r in rows if r["device"] == "SIDE_02" and r["event"]]
        check("timing_quality.csv: 'stalled' and 'reopened after' rows",
              "stalled" in evs and any(e.startswith("reopened after") for e in evs), str(evs))
        check("the other cameras were not disturbed",
              all(not s.stall_events for s in flow._cams if s.stem != "SIDE_02"))
    finally:
        flow.shutdown()


def test_repeated_headband_drops():
    flow = _fresh(BRAIN_BATTLE_SIMULATE_DROP_HEADBAND="3")
    try:
        flow.set_plan(SessionConfig(num_rounds=1, round_sec=30, break_sec=0, baseline_sec=2))
        flow.set_fighters("K7Q2", "M3XA")
        flow.begin_recording()
        band = flow._bands["B"]
        drops = 0
        t0 = time.monotonic()
        while drops < 3 and time.monotonic() - t0 < 40:
            pump(0.1)
            if band.lost:
                drops += 1
                pump(0.5)
                band.reconnect()            # the operator's Reconnect button
                deadline = time.monotonic() + 8
                while not band.connected and time.monotonic() < deadline:
                    pump(0.1)
        check("headband B dropped and was reconnected three times", drops == 3, str(drops))
        pump(1.5)
        flow.stop_and_save(); pump(1.0)
        root = flow._saved_root
        files = glob.glob(os.path.join(root, "continuous", "Muse data", "*7564*.csv"))
        check("still ONE EEG file for headband B", len(files) == 1, str(files))
        lines = open(files[0], encoding="utf-8").read().splitlines()
        headers = [ln for ln in lines if ln.startswith("TP9")]
        ts = [float(ln.split(",")[-1]) for ln in lines[1:] if ln and not ln.startswith("TP9")]
        check("one header row (appended, not restarted)", len(headers) == 1, str(len(headers)))
        check("times only go forward across the reconnections",
              all(b > a for a, b in zip(ts, ts[1:])), "")
        gaps = sorted((b - a for a, b in zip(ts, ts[1:])), reverse=True)[:3]
        check("rows from every segment (three gaps where the band was away)",
              len(gaps) == 3 and all(g > 0.3 for g in gaps) and ts[-1] > 9.0,
              f"{[round(g, 2) for g in gaps]} last {ts[-1]:.1f}")
        ev = _events(root)
        check("history logs every reconnection", ev.count("headband_reconnected") >= 3, str(ev))
    finally:
        flow.shutdown()


def test_contact_log_failure_reaches_the_operator():
    flow = _fresh()
    try:
        flow.set_plan(SessionConfig(num_rounds=1, round_sec=8, break_sec=0, baseline_sec=2))
        flow.set_fighters("K7Q2", "M3XA")
        flow.begin_recording(); pump(2.0)
        w = flow._contact_writer
        check("contact log open while recording", w is not None and w.is_open)

        class _Gone:
            def write(self, *_a):
                raise OSError(5, "Input/output error")      # the drive went away
            def flush(self):
                raise OSError(5, "Input/output error")
            def close(self):
                pass
            def fileno(self):
                raise OSError(5, "Input/output error")
        w._f = _Gone()
        pump(2.5)
        check("the failure is remembered as a write problem",
              "the sensor-contact notes" in flow.write_problems(), str(flow.write_problems()))
        check("the Recording screen says so (and that recording continues)",
              "sensor-contact notes" in flow.write_problem_text()
              and "keep recording" in flow.write_problem_text(), flow.write_problem_text())
        check("the writer stops trying after the failure", w.failed)
        t0 = time.monotonic()
        while flow.is_recording() and time.monotonic() - t0 < 30:
            pump(0.1)
        pump(1.0)
        check("Saved says 'Recording saved, but with problems'",
              flow.saved_headline() == "Recording saved, but with problems", flow.saved_headline())
        check("the videos and brain recordings were still saved",
              len(glob.glob(os.path.join(flow._saved_root, "continuous", "Muse data", "*.csv"))) == 2)
        check("queue sized for 2 s at 60 fps and its use reported",
              all(s.queue_use[1] in (0, 120) for s in flow._cams))
    finally:
        flow.shutdown()


def test_short_soak():
    out = os.path.join(tempfile.mkdtemp(), "soak.json")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "soak_test.py"),
                        "--minutes", "1", "--then", "1", "--then-minutes", "0.75",
                        "--json", out], capture_output=True, text=True, timeout=600)
    rep = json.load(open(out)) if os.path.isfile(out) else {}
    recs = rep.get("recordings", [])
    check("soak: both recordings ran", len(recs) == 2, r.stdout[-400:])
    miss_s = sum(h.get("missing") or 0 for rec in recs for h in rec["files"]["headbands"].values())
    drops = sum(sum(rec["encoder_queue_dropped"].values()) for rec in recs)
    frames = sum(c.get("frames") or 0 for rec in recs for c in rec["files"]["cameras"].values())
    miss_f = sum(c.get("lost") or 0 for rec in recs for c in rec["files"]["cameras"].values())
    late_f = sum(c.get("late") or 0 for rec in recs for c in rec["files"]["cameras"].values())
    print(f"SHORT SOAK: frames {frames}, lost {miss_f} "
          f"({100.0 * miss_f / max(1, frames + miss_f):.2f} %), read late {late_f} "
          f"({100.0 * late_f / max(1, frames):.2f} %), encoder drops {drops}, "
          f"EEG samples missing {miss_s}")
    check("soak: no EEG samples missing", miss_s == 0, str(miss_s))
    check("soak: no frame dropped by the encoder queue (ffmpeg never blocked capture)",
          drops == 0, str(drops))
    check("soak: under 2 % of frames lost (absent against the camera's clock)",
          miss_f <= 0.02 * max(1, frames + miss_f), f"{miss_f}/{frames}")
    d = [x for x in rep.get("descriptors", []) if x["point"].endswith("left the flow")]
    check("soak: descriptors back to the same numbers after each recording",
          len(d) == 2 and abs(d[0]["fds"] - d[1]["fds"]) <= 6
          and d[0]["children"] == d[1]["children"] == 0, str([(x["fds"], x["children"]) for x in d]))
    check("soak: nothing failed to save", all(not rec["write_problems"] for rec in recs))


if __name__ == "__main__":
    test_camera_stall_watchdog()
    test_repeated_headband_drops()
    test_contact_log_failure_reaches_the_operator()
    test_short_soak()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
