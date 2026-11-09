"""
render_screens.py — headless screenshots of the app, driven through the same
simulated-device scenario two ways:

  python3 tools/render_screens.py [out_dir]
      Every page + every guided-flow step, at all three DESIGN/minimum-window
      sizes, into a gitignored screens/ folder for visual review.

  python3 tools/render_screens.py --docs
      A CURATED subset — one screenshot per screen, at 1280×800 only — into
      docs/images/ with doc-friendly filenames, for the User guide / Quick
      tour pages. These ARE committed (small, compressed PNGs), unlike the
      screens/ dump.

Runs with BRAIN_BATTLE_SIMULATE=1 so the Connect / Recording steps show live
simulated cameras + headbands (one weak sensor on Boxer B), and points the
capture dir + settings at throwaway temp paths so nothing lands in the real
recordings folder. The short recording it makes to capture the Recording /
Saved steps (and the "Sessions with recordings" shot) is deleted before exit.

Not a test; purely for visual review / doc generation.
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

DOCS_MODE = "--docs" in sys.argv

# The committed doc images must show a realistic recordings folder — a plain,
# human-readable name, no throwaway temp-folder names (e.g. "bb_render_cap_ab12").
# So docs mode points the app at a cleanly-named folder and configures it the
# normal way (a saved setting), NOT via BRAIN_BATTLE_CAPTURE_DIR — the env
# override would make Settings show its "set outside the app" note, which a
# real user wouldn't see. `tests/test_docs.py` asserts this name is clean.
DOCS_RECORDINGS_DIR = "/private/tmp/Brain Battle Data"

_TMP_CFG = os.path.join(tempfile.mkdtemp(prefix="bb_render_cfg_"), "settings.json")
os.environ["BRAIN_BATTLE_CONFIG"] = _TMP_CFG

if DOCS_MODE:
    # A normal saved-setting recordings folder, cleanly named, no env override.
    shutil.rmtree(DOCS_RECORDINGS_DIR, ignore_errors=True)
    os.makedirs(DOCS_RECORDINGS_DIR, exist_ok=True)
    with open(_TMP_CFG, "w", encoding="utf-8") as _f:
        import json as _json
        _json.dump({"capture_dir": DOCS_RECORDINGS_DIR}, _f)
    _TMP_CAP = DOCS_RECORDINGS_DIR
    # Fill it with a small demo library so the Sessions image shows several
    # real statuses (Needs labelling, Labelled, Exported, Interrupted). Run
    # the generator as an isolated SUBPROCESS: it uses a ProcessPoolExecutor,
    # whose spawned workers would otherwise re-import this module (and its
    # QApplication setup). --force bypasses the generator's refuse-real-folder
    # guard (the configured folder here is this very temp folder).
    import subprocess as _sp
    _gen = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generate_demo_session.py")
    _sp.run([sys.executable, _gen, "--out", DOCS_RECORDINGS_DIR, "--library", "--force"],
            check=True)
else:
    _TMP_CAP = tempfile.mkdtemp(prefix="bb_render_cap_")
    os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = _TMP_CAP

from PyQt6.QtWidgets import QApplication
_out_args = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = _out_args[0] if _out_args else (
    os.path.join(ROOT, "docs", "images") if DOCS_MODE else os.path.join(ROOT, "screens"))
# DESIGN targets (1440×900, 1280×800) plus the app's actual minimum window —
# the same three sizes test_layout_fit.py holds every screen to. Docs mode
# only ever wants the one 1280×800 shot per screen.
DESIGN_SIZES = [(1440, 900), (1280, 800)]
# Doc screenshot filename (no size suffix) for each screens/-mode name that
# has a curated doc equivalent; names absent here are screens/-only.
_DOCS_NAMES = {
    "01b_sessions-with-recordings": "sessions",
    "02_connect-devices": "connect-devices",
    "03_set-up-session": "set-up-session",
    "04_recording": "recording",
    "05_saved": "saved",
    "06_review-and-label": "review-and-label",
    "07_settings": "settings",
}


def _pump(app, secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        app.processEvents(); time.sleep(0.02)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    from fonts import register_fonts
    register_fonts()
    import brain_battle_main as bbm

    sizes = [(1280, 800)] if DOCS_MODE else (DESIGN_SIZES + [bbm.MIN_WINDOW])
    os.makedirs(OUT, exist_ok=True)
    win = bbm.BrainBattleApp()
    saved = []

    def grab(name):
        if DOCS_MODE and name not in _DOCS_NAMES:
            return
        win.resize(*sizes[0]); win.show()
        _pump(app, 0.3)
        for w, h in sizes:
            win.resize(w, h)
            _pump(app, 0.25)
            if DOCS_MODE:
                fn = os.path.join(OUT, f"{_DOCS_NAMES[name]}.png")
                # Smaller committed files: PNG quality maps to zlib
                # compression (lower = smaller/slower); screenshots of flat
                # UI compress well even at a low quality setting.
                win.grab().save(fn, "PNG", 30)
            else:
                fn = os.path.join(OUT, f"{name}_{w}x{h}.png")
                win.grab().save(fn)
            saved.append(fn)

    # ── Sessions, empty (no recordings yet) ──
    win.show()
    win._shell.set_current_index(win.IDX_SESSIONS)
    _pump(app, 0.3)
    grab("01a_sessions-empty")

    # ── Review & Label / Settings (no session open yet, but both render) ──
    for idx, name in ((win.IDX_REVIEW, "06_review-and-label"),
                      (win.IDX_SETTINGS, "07_settings")):
        win._shell.set_current_index(idx)
        _pump(app, 0.3)
        grab(name)

    # ── Documentation: a page with a screenshot, a page with a diagram, a
    # search result (not captured in --docs mode — this is screens/-only
    # visual review, not committed doc content) ──
    if not DOCS_MODE:
        win._shell.set_current_index(win.IDX_DOCS)
        _pump(app, 0.2)
        win._docs_page.open_page("sessions")
        _pump(app, 0.2)
        grab("08a_docs-page-with-screenshot")

        win._docs_page.open_page("overview")
        _pump(app, 0.2)
        grab("08b_docs-page-with-diagram")

        win._docs_page._search_box.setText("Reconnect")
        _pump(app, 0.2)
        # Open the first result so the content shows the page scrolled to the
        # match with every "Reconnect" highlighted (the results list, count and
        # section stay visible in the left pane).
        win._docs_page._open_first_result()
        _pump(app, 0.2)
        grab("08c_docs-search-results")
        win._docs_page._search_box.setText("")

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

    # ── Sessions, now with the recording just made ──
    win._shell.set_current_index(win.IDX_SESSIONS)   # triggers an async rescan
    _pump(app, 1.2)
    grab("01b_sessions-with-recordings")

    try:
        win.record_flow.shutdown(); win._review_page.shutdown()
        win._sessions_page.shutdown()
    except Exception:
        pass
    shutil.rmtree(_TMP_CAP, ignore_errors=True)
    shutil.rmtree(os.path.dirname(_TMP_CFG), ignore_errors=True)
    print(f"rendered {len(saved)} screenshots into {OUT}")
    for fn in saved:
        print("  ", os.path.relpath(fn, ROOT))


if __name__ == "__main__":
    main()
