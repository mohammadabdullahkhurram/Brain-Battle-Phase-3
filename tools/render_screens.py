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
      screens/ dump. Docs mode also strips dev/sim artefacts a real user would
      never see: the simulated camera's "SIM CAM N" overlay becomes a neutral
      text-free picture, and the demo-library "Demo" badges are cleared from
      the fixture manifests (see BRAIN_BATTLE_SIM_PICTURE=neutral /
      _strip_demo_flag_from_fixtures).

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
_DOCS_FOLDER_NAME = "Brain Battle Data"
# A short, stable parent so the path shown in the Settings picture is tidy:
# /private/tmp on macOS, the system temp folder elsewhere (Windows).
_DOCS_PARENT = "/private/tmp" if os.path.isdir("/private/tmp") else tempfile.gettempdir()
DOCS_RECORDINGS_DIR = os.path.join(_DOCS_PARENT, _DOCS_FOLDER_NAME)

_TMP_CFG = os.path.join(tempfile.mkdtemp(prefix="bb_render_cfg_"), "settings.json")
os.environ["BRAIN_BATTLE_CONFIG"] = _TMP_CFG


def _strip_demo_flag_from_fixtures(folder):
    """Docs mode only: clear `demo: true` in every session manifest under
    `folder`, so the committed Sessions screenshot shows ordinary recordings
    rather than ones wearing the in-app "Demo" badge. The app still badges real
    demo data (see sessions_screen); these render-tool fixtures simply aren't demo
    data once rewritten. Render-tool-only — the app's manifest read/write path
    is untouched."""
    import json
    for name in os.listdir(folder):
        mf = os.path.join(folder, name, "session_manifest.json")
        if not os.path.isfile(mf):
            continue
        try:
            with open(mf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if data.get("demo"):
            data["demo"] = False
            tmp = mf + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, mf)


if DOCS_MODE:
    os.environ["BRAIN_BATTLE_SIM_PICTURE"] = "neutral"   # reaches the camera processes
    # A normal saved-setting recordings folder, cleanly named, no env override.
    shutil.rmtree(DOCS_RECORDINGS_DIR, ignore_errors=True)
    os.makedirs(DOCS_RECORDINGS_DIR, exist_ok=True)
    with open(_TMP_CFG, "w", encoding="utf-8") as _f:
        import json as _json
        _json.dump({"capture_dir": DOCS_RECORDINGS_DIR, "labeller_name": "Maya K"}, _f)
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
    # The library generator stamps every session demo:true; clear it so the
    # committed Sessions screenshot doesn't show "Demo" badges (a dev artefact).
    _strip_demo_flag_from_fixtures(DOCS_RECORDINGS_DIR)
else:
    _TMP_CAP = tempfile.mkdtemp(prefix="bb_render_cap_")
    os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = _TMP_CAP
    with open(_TMP_CFG, "w", encoding="utf-8") as _f:
        import json as _json
        _json.dump({"labeller_name": "Maya K"}, _f)

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
    "02_get-ready": "get-ready",
    "04_recording": "recording",
    "05_saved": "saved",
    "06_review-and-label": "review-and-label",
    "06b_review-compare": "review-compare",
    "07_settings": "settings",
    "09_export": "export",
    "00_welcome": "welcome",
    "03_timing": "timing",
}


def _pump(app, secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        app.processEvents(); time.sleep(0.02)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    from brain_battle.ui.fonts import register_fonts
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

    # ── the first-run screen ──
    win.show()
    win._shell.set_current_index(win.IDX_WELCOME)
    _pump(app, 0.3)
    grab("00_welcome")

    # ── Sessions, empty (no recordings yet) ──
    win.show()
    win._shell.set_current_index(win.IDX_SESSIONS)
    _pump(app, 0.3)
    grab("01a_sessions-empty")

    # ── Settings (Review & Label is captured further down, with a recording open) ──
    win._shell.set_current_index(win.IDX_SETTINGS)
    _pump(app, 0.3)
    grab("07_settings")

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
    from brain_battle.capture.session_controller import SessionConfig
    flow.set_plan(SessionConfig(num_rounds=1, round_sec=30, break_sec=0, baseline_sec=4))
    flow.set_fighters("K7Q2", "M3XA", "Red", "Blue")
    flow._get_ready.on_tick(); flow._get_ready._recompute()
    _pump(app, 0.4)
    grab("02_get-ready")

    # ── the Test timing dialog, after a buzzer test (rendered on its own) ──
    done = {}
    flow.run_buzzer_test(on_done=lambda cal: done.setdefault("cal", cal))
    t0 = time.time()
    while "cal" not in done and time.time() - t0 < 20:
        _pump(app, 0.1)
    from brain_battle.ui.record_screens import TimingDialog
    td = TimingDialog(flow, recording=False)
    td.resize(800, td.sizeHint().height()); td.show(); _pump(app, 0.6); td.refresh(); _pump(app, 0.2)
    fn = os.path.join(OUT, "timing.png" if DOCS_MODE else "03_timing_dialog.png")
    td.grab().save(fn); saved.append(fn); td.done(0)

    flow.begin_recording()
    # run into the round, mark a moment, then grab mid-round
    t0 = time.time()
    marked = False
    while time.time() - t0 < 30:
        _pump(app, 0.1)
        if not marked and time.time() - t0 > 17:
            flow.mark_moment(); marked = True
            flow.set_moment_note(0, "B dropped the guard after a combination")
            if flow._moment_dialog is not None:
                flow._moment_dialog.close()
        if flow._controller.current_phase_info and \
                flow._controller.current_phase_info.phase.value == "round" and time.time() - t0 > 23:
            break
    flow._recording_step.on_tick()
    grab("04_recording")

    flow.stop_and_save()
    _pump(app, 0.8)
    flow.wait_for_xdf(30)                           # so the checklist is complete
    _pump(app, 0.2)
    grab("05_saved")

    # ── Review & Label with that recording open, a few labels placed ──
    review = win._review_page
    win._shell.set_current_index(win.IDX_REVIEW)
    # a second labeller's labels, so the screen shows what comparing looks like
    import brain_battle.data.labels_store as _ls
    other = _ls.open_for_labeller(flow._saved_root, "Jonas R")
    for t, name, who, col in ((19.7, "Punch initiated", "A", "#2155D6"),
                              (20.9, "Punch landed", "A", "#0F7A62"),
                              (22.4, "Punch blocked", "B", "#465063")):
        other.add(_ls.new_label(name, who, "point", t, color=col, created_by="Jonas R"))
    other.save()
    review.load_session_folder(flow._saved_root)
    review.wait_loaded()
    for t, key in ((19.6, 0), (20.4, 1), (21.3, 4)):
        review._seek(t); _pump(app, 0.1)
        if key < len(review._vocab):
            review._place_from_vocab(review._vocab[key])
    review._seek(22.0)
    review.start_note(); review._note_text.setPlainText("A pressing forward, B backing off")
    review.add_note()
    review._seek(20.0)
    _pump(app, 0.8)
    grab("06_review-and-label")
    review._tabs.set_value("compare", emit=True)
    _pump(app, 0.3)
    grab("06b_review-compare")
    review._tabs.set_value("mine", emit=True)

    # ── the export window (rendered on its own) ──
    from brain_battle.data.export_module import ExportOptionsDialog
    dlg = ExportOptionsDialog(flow._saved_root)
    dlg.resize(560, dlg.sizeHint().height()); dlg.show(); _pump(app, 0.3)
    fn = os.path.join(OUT, "export.png" if DOCS_MODE else "09_export_dialog.png")
    dlg.grab().save(fn); saved.append(fn); dlg.close()

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
