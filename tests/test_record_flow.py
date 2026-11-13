"""
Guided recording flow (record_flow.RecordFlow) end to end, with SIMULATED
devices (BRAIN_BATTLE_SIMULATE) driving the REAL capture paths.

Covers:
  1. Full run through all four steps → a valid recording (camera MP4s +
     sidecars, EEG CSVs, phase markers, manifest with names + moments, history,
     contact log) that shows in Sessions with the right status and opens in
     Review & Label with its markers and moments. Also: sleep prevention is
     requested while recording and released after.
  2. Stop early → SESSION_END written (clean), flagged stopped_early (NOT
     interrupted), Sessions shows "Stopped at …".
  3. Missing camera → only 3 cameras, the 4th tile missing, "5 of 6 devices
     ready".
  4. Headband disconnect mid-recording → Reconnect brings it back; the EEG CSV
     continues (append).
  5. Disk-blocked start → recording does not begin, stays on Set up.
  6. Quit-while-recording asks for confirmation (and honours No).
  7. Every flow step fits at 1440×900, 1280×800 and the minimum size.
  8. With BRAIN_BATTLE_SIMULATE unset there is no simulation text in the UI.

Real-time (short durations); a few tens of seconds total.

Run:  python3 tests/test_record_flow.py
"""
import glob
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    import vlc  # noqa: F401
except Exception:
    import types
    sys.modules["vlc"] = types.ModuleType("vlc")

from PyQt6.QtWidgets import QApplication, QWidget, QMainWindow, QAbstractScrollArea
from PyQt6.QtCore import QPoint, QRect

_app = QApplication.instance() or QApplication(sys.argv)

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def ss_markers(root):
    import brain_battle.data.session_storage as ss
    return ss.read_phase_markers(root)


def pump(secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        _app.processEvents(); time.sleep(0.02)


def _fresh_env(**extra):
    """Point capture dir + settings at throwaway temp paths; enable simulation."""
    cap = tempfile.mkdtemp(prefix="bb_flow_cap_")
    os.environ["BRAIN_BATTLE_SIMULATE"] = "1"
    os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = cap
    os.environ["BRAIN_BATTLE_CONFIG"] = os.path.join(tempfile.mkdtemp(), "settings.json")
    for k in ("BRAIN_BATTLE_SIMULATE_MISSING_CAMERA", "BRAIN_BATTLE_SIMULATE_DROP_HEADBAND"):
        os.environ.pop(k, None)
    for k, v in extra.items():
        os.environ[k] = v
    return cap


def _new_flow():
    from brain_battle.ui.record_flow import RecordFlow
    return RecordFlow()


def _setup_short(flow, rest=2, rnd=10, brk=2, rounds=2, a="Sam", b="Alex"):
    """Enter Set up, then force a short session (steppers clamp round ≥ 10s)."""
    flow.goto_setup()
    su = flow._setup_step
    su._st_rest.set_value(rest); su._st_round.set_value(rnd)
    su._st_break.set_value(brk); su._st_rounds.set_value(rounds)
    su._name_a.setText(a); su._name_b.setText(b); su._recompute()


# ─── 1. end to end ────────────────────────────────────────────────────────────

def test_end_to_end():
    _fresh_env()
    flow = _new_flow()
    try:
        flow.start(); pump(1.8)
        check("step 1: all 6 devices connect",
              sum(s.is_live for s in flow._cams) == 4
              and sum(b.connected for b in flow._bands.values()) == 2)
        # live LSL streams are on the network from the Connect step onwards
        import pylsl
        live = {i.name(): i for i in pylsl.resolve_streams(wait_time=1.5)
                if i.name().startswith("BrainBattle_")}
        check("LSL: both EEG streams, the markers and 4 camera streams are published",
              {"BrainBattle_EEG_A", "BrainBattle_EEG_B", "BrainBattle_Markers"} <= set(live)
              and sum(n.startswith("BrainBattle_Video_") for n in live) == 4, str(sorted(live)))
        marker_inlet = pylsl.StreamInlet(live["BrainBattle_Markers"]) if "BrainBattle_Markers" in live else None
        if marker_inlet:
            marker_inlet.open_stream(timeout=3)
        _setup_short(flow)
        flow.begin_recording()
        check("sleep prevention requested while recording", flow._keep_awake.is_active())
        marked = False
        t0 = time.time()
        while flow.is_recording() and time.time() - t0 < 45:
            pump(0.1)
            if not marked and time.time() - t0 > 6:
                flow.mark_moment(); marked = True
        pump(0.6)
        check("sleep prevention released after recording", not flow._keep_awake.is_active())
        check("flow landed on the Saved step", flow._stack.currentIndex() == flow.STEP_SAVED)

        root = flow._saved_root
        man = json.load(open(os.path.join(root, "session_manifest.json")))
        check("manifest has both boxer names",
              man["boxers"]["A"]["name"] == "Sam" and man["boxers"]["B"]["name"] == "Alex")
        check("manifest records the marked moment", len(man.get("moments", [])) == 1)
        check("manifest has the capture clock", man["capture_clock"]["t0_unix"] is not None)
        check("manifest records EEG source starts",
              any(k.startswith("EEG_") for k in man.get("source_starts", {})))
        check("4 camera MP4s + 4 sidecars written",
              len(glob.glob(root + "/continuous/**/*.mp4", recursive=True)) == 4
              and len(glob.glob(root + "/continuous/**/*.timestamps.csv", recursive=True)) == 4)
        check("2 EEG CSVs written",
              len(glob.glob(root + "/continuous/Muse data/*.csv")) == 2)
        check("movement-sensor CSVs written next to the EEG (2 bands x ACC+GYRO)",
              len(glob.glob(root + "/continuous/Muse sensors/*.csv")) == 4)
        check("manifest stores the LSL-clock origin", man["capture_clock"].get("t0_lsl") is not None)

        # the live LSL marker stream carried the same events, on the LSL clock
        if marker_inlet:
            got, got_ts = [], []
            while True:
                smp, ts = marker_inlet.pull_sample(timeout=0.3)
                if smp is None:
                    break
                got.append(smp[0]); got_ts.append(ts + marker_inlet.time_correction(timeout=1))
            check("LSL markers: every phase start, the moment and SESSION_END arrived",
                  got[0] == "BASELINE_START" and got[-1] == "SESSION_END" and "MOMENT" in got
                  and "ROUND_2_START" in got, str(got))
            check("LSL marker times are session-start + t_session (within 5 ms)",
                  abs((got_ts[-1] - got_ts[0])
                      - (ss_markers(root)[-1][0] - ss_markers(root)[0][0])) < 0.005,
                  f"{got_ts[-1] - got_ts[0]:.4f}")

        # recording.xdf: built in the background from the finished files
        check("recording.xdf build finished", flow.wait_for_xdf(30))
        check("Saved screen reports the XDF", "saved" in flow._saved_step._xdf_row.findChildren(
            __import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel)[1].text())
        import pyxdf
        streams, _ = pyxdf.load_xdf(os.path.join(root, "recording.xdf"))
        by = {st["info"]["name"][0]: st for st in streams}
        check("XDF holds EEG x2, ACC/GYRO x2, markers and 4 video-frame streams",
              {"BrainBattle_EEG_A", "BrainBattle_EEG_B", "BrainBattle_ACC_A", "BrainBattle_GYRO_B",
               "BrainBattle_Markers"} <= set(by)
              and sum(n.startswith("BrainBattle_Video_") for n in by) == 4, str(sorted(by)))
        t0_lsl = man["capture_clock"]["t0_lsl"]
        mk = by["BrainBattle_Markers"]
        check("XDF markers: first is BASELINE_START, right after the session start",
              mk["time_series"][0][0] == "BASELINE_START" and 0 <= mk["time_stamps"][0] - t0_lsl < 0.5,
              f"{mk['time_stamps'][0] - t0_lsl:.4f}")
        side = glob.glob(root + "/continuous/Side Cams/session_SIDE_01.timestamps.csv")[0]
        n_rows = sum(1 for _ in open(side)) - 1
        vid = by["BrainBattle_Video_SIDE_01"]
        check("XDF video stream has one sample per recorded frame",
              len(vid["time_stamps"]) == n_rows and int(vid["time_series"][-1][0]) == n_rows - 1,
              f"{len(vid['time_stamps'])} vs {n_rows}")
        eeg = by["BrainBattle_EEG_A"]
        dur = eeg["time_stamps"][-1] - eeg["time_stamps"][0]
        check("XDF EEG is continuous at ~256 Hz for the whole session",
              abs(len(eeg["time_stamps"]) / dur - 256) < 3 and dur > 20, f"{len(eeg['time_stamps']) / dur:.1f} Hz")

        check("phase markers + contact log + history written",
              os.path.isfile(root + "/phase_markers.csv")
              and os.path.isfile(root + "/contact_quality.csv")
              and os.path.isfile(root + "/history.jsonl"))

        import brain_battle.data.session_history as sh
        events = [e["event"] for e in sh.read_history(root)]
        check("history logged started + moment + finished",
              "recording_started" in events and "moment_marked" in events
              and "recording_finished" in events, str(events))

        # shows in Sessions with a sensible status (needs labelling, clean)
        import brain_battle.data.session_storage as ss
        info = ss.session_info(root)
        check("Sessions status is needs_labelling (clean, no labels)",
              info.status == ss.STATUS_NEEDS_LABELLING, info.status)
        check("Sessions not interrupted", not info.interrupted)

        # opens in Review & Label with markers + the moment surfaced
        from brain_battle.ui.review_label_screen import ReviewLabelScreen, read_phase_markers
        rt = ReviewLabelScreen()
        try:
            rt.load_session_folder(root); rt.wait_loaded()
            check("Review sees the phase markers", len(read_phase_markers(root)) >= 4)
            check("Review surfaces the marked moment to name", len(rt._moments) == 1)
        finally:
            rt.shutdown()
    finally:
        flow.shutdown(); pump(0.3)


# ─── 2. stop early ────────────────────────────────────────────────────────────

def test_stop_early():
    _fresh_env()
    flow = _new_flow()
    try:
        flow.start(); pump(1.8)
        _setup_short(flow, rest=2, rnd=20, brk=2, rounds=2)
        flow.begin_recording()
        t0 = time.time()
        while flow.is_recording() and time.time() - t0 < 6:
            pump(0.1)
        flow.stop_and_save()
        pump(0.6)
        root = flow._saved_root
        man = json.load(open(os.path.join(root, "session_manifest.json")))
        check("stop early flags stopped_early", man.get("stopped_early") is True)
        check("SESSION_END marker still written (clean stop, not a crash)",
              "SESSION_END" in open(root + "/phase_markers.csv").read())
        import brain_battle.data.session_storage as ss
        info = ss.session_info(root)
        check("stopped-early session is NOT interrupted", not info.interrupted)
        check("Sessions shows where it stopped", info.stopped_sec is not None)
    finally:
        flow.shutdown(); pump(0.3)


# ─── 3. missing camera ────────────────────────────────────────────────────────

def test_missing_camera():
    _fresh_env(BRAIN_BATTLE_SIMULATE_MISSING_CAMERA="1")
    flow = _new_flow()
    try:
        flow.start(); pump(1.8)
        live = sum(s.is_live for s in flow._cams)
        check("only 3 cameras connect", live == 3, f"live={live}")
        missing = [s for s in flow._cams if s.device is None]
        check("the 4th slot has no device (shows the red 'not found' tile)", len(missing) == 1)
        flow._connect_step.on_tick()
        check("summary reads '5 of 6 devices ready'",
              "5 of 6" in flow._connect_step._summary.text(),
              flow._connect_step._summary.text())
        check("explanation says the missing camera won't be recorded",
              "camera" in flow._connect_step._explain.text().lower())
    finally:
        flow.shutdown(); pump(0.3)


# ─── 4. headband disconnect + reconnect ───────────────────────────────────────

def test_headband_drop_reconnect():
    _fresh_env(BRAIN_BATTLE_SIMULATE_DROP_HEADBAND="3")
    flow = _new_flow()
    try:
        flow.start(); pump(1.8)
        _setup_short(flow, rest=2, rnd=20, brk=2, rounds=1)
        flow.begin_recording()
        # wait for Boxer B's band to drop (~3 s into recording)
        t0 = time.time()
        while not flow._bands["B"].lost and time.time() - t0 < 10:
            pump(0.1)
        check("Boxer B headband drops mid-recording", flow._bands["B"].lost)
        check("health line names the lost headband",
              "headband" in flow.health_text().lower() and not flow.health_ok(),
              flow.health_text())
        # reconnect it
        flow.reconnect_band("B")
        t0 = time.time()
        while not flow._bands["B"].connected and time.time() - t0 < 8:
            pump(0.1)
        check("Reconnect brings Boxer B back", flow._bands["B"].connected and not flow._bands["B"].lost)
        pump(1.0)
        flow.stop_and_save(); pump(0.6)
        root = flow._saved_root
        bpath = flow._band_rec_paths.get("B")
        check("Boxer B EEG CSV exists and has rows after the drop+reconnect",
              bpath and os.path.isfile(bpath) and sum(1 for _ in open(bpath)) > 1)
    finally:
        flow.shutdown(); pump(0.3)


# ─── 5. disk-blocked start ─────────────────────────────────────────────────────

class _FakeMsg:
    class StandardButton:
        Yes = 1; No = 2
    calls = {"critical": 0, "question": 0}

    @staticmethod
    def critical(*a, **k): _FakeMsg.calls["critical"] += 1; return 1
    @staticmethod
    def question(*a, **k): _FakeMsg.calls["question"] += 1; return 2  # No


def test_disk_blocked():
    _fresh_env()
    import brain_battle.ui.record_flow as record_flow
    flow = _new_flow()
    real_msg = record_flow.QMessageBox
    real_usage = shutil.disk_usage

    class _TinyUsage:
        total = 1000; used = 999; free = 1   # 1 byte free → blocked
    try:
        flow.start(); pump(1.8)
        _setup_short(flow)
        _FakeMsg.calls = {"critical": 0, "question": 0}
        record_flow.QMessageBox = _FakeMsg
        shutil.disk_usage = lambda p: _TinyUsage()
        flow.begin_recording()
        check("disk-blocked: recording does NOT start", not flow.is_recording())
        check("disk-blocked: stayed on the Set up step",
              flow._stack.currentIndex() == flow.STEP_SETUP)
        check("disk-blocked: showed a blocking message", _FakeMsg.calls["critical"] == 1)
    finally:
        record_flow.QMessageBox = real_msg
        shutil.disk_usage = real_usage
        flow.shutdown(); pump(0.3)


# ─── 6. quit while recording asks for confirmation ─────────────────────────────

def test_quit_confirmation():
    _fresh_env()
    import brain_battle_main as bbm
    import brain_battle.app as _app   # closeEvent resolves QMessageBox here
    from PyQt6.QtGui import QCloseEvent
    win = bbm.BrainBattleApp()
    real_msg = _app.QMessageBox
    try:
        win.show()
        win._shell.set_current_index(win.IDX_RECORD)
        pump(1.8)
        _setup_short(win.record_flow, rest=2, rnd=30, brk=0, rounds=1)
        win.record_flow.begin_recording()
        check("recording is in progress", win.record_flow.is_recording())
        _app.QMessageBox = _FakeMsg
        # user answers No → close is vetoed
        _FakeMsg.calls = {"critical": 0, "question": 0}
        ev = QCloseEvent()
        win.closeEvent(ev)
        check("quit while recording asks, and No vetoes the close",
              _FakeMsg.calls["question"] == 1 and not ev.isAccepted())
        win.record_flow.stop_and_save(); pump(0.5)
    finally:
        _app.QMessageBox = real_msg
        try:
            win.record_flow.shutdown(); win._review_page.shutdown(); win._sessions_page.shutdown()
        except Exception:
            pass
        pump(0.3)


# ─── 7. every flow step fits ───────────────────────────────────────────────────

def _overflows(win, page):
    W, H = win.width(), win.height()
    out = []
    for w in page.findChildren(QWidget):
        if not w.isVisible() or w.width() <= 0 or w.height() <= 0:
            continue
        if "ScrollBar" in w.__class__.__name__:
            continue
        if w.visibleRegion().isEmpty():
            continue
        tl = w.mapTo(win, QPoint(0, 0)); R = QRect(tl, w.size())
        if not R.intersects(QRect(0, 0, W, H)):
            continue
        sa = None; p = w.parent()
        while p is not None:
            if isinstance(p, QAbstractScrollArea):
                sa = p; break
            p = p.parent()
        bad = []
        if R.left() < -2:
            bad.append("off-left")
        if R.right() > W + 2:
            bad.append("off-right")
        if R.bottom() > H + 2 and sa is None:
            bad.append("below-window")
        if bad:
            out.append((w.__class__.__name__, bad))
    return out


def test_every_step_fits():
    _fresh_env()
    flow = _new_flow()
    win = QMainWindow(); win.setCentralWidget(flow)
    try:
        flow.start(); pump(1.8)
        _setup_short(flow)
        flow._cfg = flow._setup_step.config()
        flow._recording_step.reset_for_recording()
        flow._saved_step.populate({"meta": "demo", "checklist": [("✓", "x", None)],
                                    "worth_checking": ["y"], "folder_line": "z"})
        for W, H in [(1440, 900), (1280, 800), (1180, 720)]:
            win.resize(W, H); win.show(); pump(0.2)
            for step, name in [(flow.STEP_CONNECT, "Connect"), (flow.STEP_SETUP, "Set up"),
                               (flow.STEP_RECORD, "Recording"), (flow.STEP_SAVED, "Saved")]:
                flow._set_step(step); pump(0.2)
                ov = _overflows(win, flow._stack.currentWidget())
                check(f"[{W}x{H}] step '{name}' nothing clipped", len(ov) == 0, str(ov[:3]))
    finally:
        flow.shutdown(); pump(0.3)


# ─── 8. no simulation trace when the flag is unset ─────────────────────────────

def test_no_simulation_when_unset():
    for k in ("BRAIN_BATTLE_SIMULATE", "BRAIN_BATTLE_SIMULATE_MISSING_CAMERA",
              "BRAIN_BATTLE_SIMULATE_DROP_HEADBAND"):
        os.environ.pop(k, None)
    import importlib
    import brain_battle.capture.simulation as simulation
    importlib.reload(simulation)
    check("simulation disabled when the flag is unset", not simulation.enabled())
    # Build the flow WITHOUT starting it (so no real-hardware scan) and confirm
    # no simulation wording is baked into the UI.
    flow = _new_flow()
    try:
        texts = []
        for w in flow.findChildren(QWidget):
            t = getattr(w, "text", None)
            if callable(t):
                try:
                    texts.append(t())
                except Exception:
                    pass
        joined = " ".join(texts).lower()
        check("no 'simul' text anywhere in the flow UI", "simul" not in joined,
              [x for x in texts if "simul" in x.lower()][:3])
    finally:
        flow.shutdown(); pump(0.2)


if __name__ == "__main__":
    test_end_to_end()
    test_stop_early()
    test_missing_camera()
    test_headband_drop_reconnect()
    test_disk_blocked()
    test_quit_confirmation()
    test_every_step_fits()
    test_no_simulation_when_unset()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
