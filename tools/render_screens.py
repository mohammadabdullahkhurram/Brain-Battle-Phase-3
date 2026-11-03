"""
render_screens.py — headless screenshots of every app page + every guided-flow
step, into a gitignored screens/ folder for visual review (DESIGN: fits
1440×900, usable at 1280×800).

Runs with BRAIN_BATTLE_SIMULATE=1 so the Connect / Recording steps show live
simulated cameras + headbands (one weak sensor on Boxer B), and points the
capture dir + settings at throwaway temp paths so nothing lands in the real
recordings folder. The short recording it makes to capture the Recording /
Saved steps is deleted before exit.

    python3 tools/render_screens.py [out_dir]

Not a test; purely for visual review.
"""
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BRAIN_BATTLE_SIMULATE"] = "1"
_TMP_CAP = tempfile.mkdtemp(prefix="bb_render_cap_")
_TMP_CFG = os.path.join(tempfile.mkdtemp(prefix="bb_render_cfg_"), "settings.json")
os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = _TMP_CAP
os.environ["BRAIN_BATTLE_CONFIG"] = _TMP_CFG

from PyQt6.QtWidgets import QApplication

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "screens")
SIZES = [(1440, 900), (1280, 800)]


def _pump(app, secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        app.processEvents(); time.sleep(0.02)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    from fonts import register_fonts
    register_fonts()
    import brain_battle_main as bbm

    os.makedirs(OUT, exist_ok=True)
    win = bbm.BrainBattleApp()
    saved = []

    def grab(name):
        win.resize(*SIZES[0]); win.show()
        _pump(app, 0.3)
        for w, h in SIZES:
            win.resize(w, h)
            _pump(app, 0.25)
            fn = os.path.join(OUT, f"{name}_{w}x{h}.png")
            win.grab().save(fn)
            saved.append(fn)

    # ── shell pages ──
    win.show()
    for idx, name in ((win.IDX_SESSIONS, "01_sessions"),
                      (win.IDX_REVIEW, "06_review-and-label"),
                      (win.IDX_SETTINGS, "07_settings")):
        win._shell.set_current_index(idx)
        _pump(app, 0.3)
        grab(name)

    # ── guided flow ──
    win._shell.set_current_index(win.IDX_RECORD)   # starts the flow (scans devices)
    flow = win.record_flow
    _pump(app, 1.8)                                 # let sim cameras + bands connect
    flow._connect_step.on_tick()
    grab("02_connect-devices")

    flow.goto_setup()
    su = flow._setup_step
    su._st_rest.set_value(4); su._st_round.set_value(30); su._st_break.set_value(0)
    su._st_rounds.set_value(1); su._name_a.setText("Sam"); su._name_b.setText("Alex")
    su._recompute()
    _pump(app, 0.2)
    grab("03_set-up-session")

    flow.begin_recording()
    # run into the round, mark a moment, then grab mid-round
    t0 = time.time()
    marked = False
    while time.time() - t0 < 10:
        _pump(app, 0.1)
        if not marked and time.time() - t0 > 7:
            flow.mark_moment(); marked = True
        if flow._controller.current_phase_info and \
                flow._controller.current_phase_info.phase.value == "round" and time.time() - t0 > 8:
            break
    flow._recording_step.on_tick()
    grab("04_recording")

    flow.stop_and_save()
    _pump(app, 0.8)
    grab("05_saved")

    try:
        win.record_flow.shutdown(); win._review_page.shutdown()
    except Exception:
        pass
    shutil.rmtree(_TMP_CAP, ignore_errors=True)
    shutil.rmtree(os.path.dirname(_TMP_CFG), ignore_errors=True)
    print(f"rendered {len(saved)} screenshots into {OUT}")
    for fn in saved:
        print("  ", os.path.relpath(fn, ROOT))


if __name__ == "__main__":
    main()
