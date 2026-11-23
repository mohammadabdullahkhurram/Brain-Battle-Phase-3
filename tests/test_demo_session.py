"""
Tests for tools/generate_demo_session.py — the fixture every later roadmap
phase builds against since there is no working hardware right now (see
AGENTS.md "Project situation").

Generates a demo session into a temp dir and checks:
  1. Layout matches what SyncReviewTab.load_session_folder expects.
  2. Every camera's sidecar row count equals its MP4 frame count, and the
     deliberately-drifting camera's sidecar shows the drift (fewer true
     frames than its declared-fps container would imply).
  3. EEG CSVs have the expected columns and sample rate.
  4. SyncReviewTab constructs headlessly, loads the demo session, and ALIGN
     computes the same per-camera seeks the generated start delays imply.
  5. --interrupted: no SESSION_END marker, manifest's last phase is open
     (end_unix/end_monotonic both None).
  6. A stopped-early recording: the phase in progress gets its _END and
     RECORDING_END at the stop, stopped_early in the manifest.
  7. --library matches the current app: 6 recordings, every Sessions status;
     fighter codes + colours only — no name in ANY demo file (nor this
     computer's login or name); 15 s lead-in and RECORDING_START …
     RECORDING_END; moments with and without notes; labels from the preset
     list, custom notes, two labellers on one recording (agreement works);
     timing_quality.csv everywhere and timing_calibration on all but one
     ("Timing not measured"); one stopped early, one interrupted; a weak-signal
     stretch ("Worth checking"); moments.csv, contact_quality.csv, a session
     log and every other file a real recording has; the team's
     label_list.json (new default) and fighter_colours.json.
  8. tools/demo.py's guards: never the real recordings folder, --fresh deletes
     only a folder it made, the app gets its own settings + simulated devices.

Run:  python3 tests/test_demo_session.py   (nonzero exit on any failure)
Needs: ffmpeg on PATH (video container), PyQt6 + python-vlc (stubbed if
libVLC is absent), same as the other tests/ scripts.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401  (temp settings file + a labeller name; see tests/_env.py)
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import cv2
import pandas as pd

from tools.generate_demo_session import generate_demo_session, DRIFT_CAMERA_LABEL

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


# ─── 1 & 2 & 3: layout, sidecars, EEG CSVs ──────────────────────────────────

def test_layout_and_sidecars_and_eeg():
    with tempfile.TemporaryDirectory() as tmp:
        result = generate_demo_session(out=tmp, rounds=1, round_sec=10,
                                       break_sec=3, baseline_sec=3, seed=0)
        root = result["root"]

        # (1) Layout matches what load_session_folder expects.
        cont = os.path.join(root, "continuous")
        check("continuous/Side Cams exists", os.path.isdir(os.path.join(cont, "Side Cams")))
        check("continuous/POV Cams exists", os.path.isdir(os.path.join(cont, "POV Cams")))
        check("continuous/Muse data exists", os.path.isdir(os.path.join(cont, "Muse data")))
        check("phase_markers.csv exists", os.path.isfile(os.path.join(root, "phase_markers.csv")))
        check("session_manifest.json exists", os.path.isfile(os.path.join(root, "session_manifest.json")))
        with open(os.path.join(root, "session_manifest.json"), encoding="utf-8") as f:
            _man = json.load(f)
        check("demo manifest carries demo:true", _man.get("demo") is True, str(_man.get("demo")))
        check("4 cameras generated", len(result["cameras"]) == 4, str(len(result["cameras"])))
        check("2 EEG bands generated", len(result["eeg_bands"]) == 2, str(len(result["eeg_bands"])))

        # (2) Sidecar row count == MP4 frame count, for every camera.
        drift_seen = False
        for cam in result["cameras"]:
            mp4_path = cam["path"]
            sidecar_path = os.path.splitext(mp4_path)[0] + ".timestamps.csv"
            check(f"{cam['label']}: sidecar exists", os.path.isfile(sidecar_path))

            cap = cv2.VideoCapture(mp4_path)
            mp4_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            declared_fps = cap.get(cv2.CAP_PROP_FPS)
            cap.release()

            with open(sidecar_path, encoding="utf-8") as f:
                sidecar_rows = sum(1 for _ in f) - 1  # minus header

            check(f"{cam['label']}: sidecar rows == MP4 frames",
                  sidecar_rows == mp4_frames,
                  f"sidecar={sidecar_rows} mp4={mp4_frames}")
            check(f"{cam['label']}: container declares nominal fps",
                  abs(declared_fps - cam["declared_fps"]) < 0.5, f"{declared_fps}")

            if cam["label"] == DRIFT_CAMERA_LABEL:
                drift_seen = True
                # The drift camera's sidecar reflects a slower TRUE capture
                # rate than the container's declared fps — so its last
                # sidecar row's t_session is LESS than frame_count/declared_fps
                # would predict (fewer true frames fit in the same real time
                # at the slower rate... equivalently: mp4_frames/declared_fps
                # UNDER-estimates the session's real duration for this camera).
                with open(sidecar_path, encoding="utf-8") as f:
                    rows = f.read().strip().splitlines()[1:]
                last_t_session = float(rows[-1].split(",")[1])
                implied_duration_at_declared_fps = mp4_frames / declared_fps
                check("drift camera: true capture rate differs from declared container fps",
                      abs(cam["capture_fps"] - declared_fps) > 0.1,
                      f"true={cam['capture_fps']} declared={declared_fps}")
                check("drift camera: sidecar's real elapsed time exceeds what "
                      "declared-fps playback of the same frame count implies",
                      last_t_session - cam["start_offset_sec"] > implied_duration_at_declared_fps,
                      f"sidecar_elapsed={last_t_session - cam['start_offset_sec']:.3f}s "
                      f"vs declared-fps-implied={implied_duration_at_declared_fps:.3f}s")
        check("the drift camera was actually present in this run", drift_seen)

        # (3) EEG CSVs: expected columns + sample rate.
        for band in result["eeg_bands"]:
            df = pd.read_csv(band["path"])
            check(f"{band['device_name']}: expected columns",
                  list(df.columns) == ["TP9", "AF7", "AF8", "TP10", "AUX",
                                       "timestamps", "t_session_seconds"],
                  str(list(df.columns)))
            dt = df["timestamps"].diff().dropna()
            mean_sr = 1.0 / dt.mean()
            check(f"{band['device_name']}: ~256 Hz sample rate",
                  abs(mean_sr - 256.0) < 1.0, f"{mean_sr:.2f} Hz")
            check(f"{band['device_name']}: row count matches generated sample count",
                  len(df) == band["n_samples"], f"{len(df)} vs {band['n_samples']}")

        return result


# ─── 4: headless Review-tab load + ALIGN matches generated start delays ────

def test_review_tab_loads():
    """The merged Review & Label screen opens a v3 demo on one playhead."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)
    import brain_battle.ui.review_label_screen as rl

    with tempfile.TemporaryDirectory() as tmp:
        result = generate_demo_session(out=tmp, rounds=1, round_sec=10,
                                       break_sec=3, baseline_sec=3, seed=2)
        tab = rl.ReviewLabelScreen()
        try:
            tab.load_session_folder(result["root"])
            # labels/timeline are available immediately; the player loads in
            # the background
            check("phases available immediately after open (labels-first)",
                  len(tab._phases) == len(result["markers"]))
            tab.wait_loaded()
            check("ReviewLabelScreen.load_session_folder: no exception", True)
            check("single-clock player built with a sane duration",
                  tab._player is not None and tab._duration > 0, f"{tab._duration:.1f}s")
            check("phase markers loaded from phase_markers.csv",
                  len(tab._phases) == len(result["markers"]),
                  f"{len(tab._phases)} vs {len(result['markers'])}")
            # the one playhead drives everything — seeking sets player.t
            tab._seek(1.0)
            check("seek sets the shared playhead", abs(tab._player.t - 1.0) < 1e-6,
                  f"{tab._player.t}")
        finally:
            tab.shutdown()


# ─── 5: --interrupted fixture ────────────────────────────────────────────────

def test_interrupted_session():
    with tempfile.TemporaryDirectory() as tmp:
        result = generate_demo_session(out=tmp, rounds=1, round_sec=10,
                                       break_sec=3, baseline_sec=3, seed=3,
                                       interrupted=True)
        check("interrupted: flagged in the summary", result["interrupted"] is True)

        with open(os.path.join(result["root"], "phase_markers.csv"), encoding="utf-8") as f:
            marker_rows = f.read().strip().splitlines()[1:]
        check("interrupted: no RECORDING_END marker",
              not any("RECORDING_END" in row or "SESSION_END" in row for row in marker_rows),
              f"{len(marker_rows)} marker rows")

        manifest = json.loads(
            open(os.path.join(result["root"], "session_manifest.json"), encoding="utf-8").read())
        last_phase = manifest["phases"][-1]
        check("interrupted: last phase has no end_unix",
              last_phase["end_unix"] is None, str(last_phase))
        check("interrupted: last phase has no end_monotonic",
              last_phase["end_monotonic"] is None, str(last_phase))
        check("interrupted: at least one earlier phase IS closed normally",
              any(p["end_unix"] is not None for p in manifest["phases"][:-1])
              if len(manifest["phases"]) > 1 else True)


# ─── 6: marker label format (underscore form) + both-forms loader ───────────

def test_marker_label_format():
    # The demo generator mirrors live_session_tab's writer: phase markers use
    # the underscore token form (ROUND_1_START), not the legacy "ROUND 1_START".
    with tempfile.TemporaryDirectory() as tmp:
        result = generate_demo_session(out=tmp, rounds=2, round_sec=8,
                                       break_sec=3, baseline_sec=3, seed=4)
        with open(os.path.join(result["root"], "phase_markers.csv"), encoding="utf-8") as f:
            labels = [row.split(",")[2] for row in f.read().strip().splitlines()[1:]]
        expected = ["RECORDING_START", "BASELINE_START", "BASELINE_END",
                    "ROUND_1_START", "ROUND_1_END", "BREAK_1_START", "BREAK_1_END",
                    "ROUND_2_START", "ROUND_2_END", "RECORDING_END"]
        check("generated system markers: start/end of everything, underscore form",
              labels == expected, str(labels))
        times = [float(row.split(",")[0]) for row in
                 open(os.path.join(result["root"], "phase_markers.csv"),
                      encoding="utf-8").read().strip().splitlines()[1:]]
        import brain_battle.capture.session_controller as _sc
        check("RECORDING_START at 0, BASELINE_START one lead-in later",
              times[0] == 0.0 and abs(times[1] - _sc.LEAD_IN_SEC) < 1e-6, str(times[:2]))

    # The merged screen's marker parser (review_label_screen.friendly_phase) must
    # read BOTH the new underscore form and the legacy space form.
    from brain_battle.ui.review_label_screen import friendly_phase
    pairs = [
        ("ROUND_1_START", "ROUND 1_START", "Round 1"),
        ("BREAK_2_START", "BREAK 2_START", "Break"),
        ("BASELINE_START", "BASELINE_START", "Baseline"),
        ("RECORDING_START", "RECORDING_START", "Lead-in"),
        ("RECORDING_END", "RECORDING_END", "Finished"),
        ("SESSION_END", "SESSION_END", "Finished"),
    ]
    for new_form, old_form, want in pairs:
        a_new, a_old = friendly_phase(new_form), friendly_phase(old_form)
        check(f"friendly_phase reads both forms of {new_form}",
              a_new == want and a_old == want,
              f"new={a_new!r} old={a_old!r} want={want!r}")


# ─── 6: stopped early ────────────────────────────────────────────────────────

def test_stopped_early():
    with tempfile.TemporaryDirectory() as tmp:
        import brain_battle.capture.session_controller as _sc
        lead = _sc.LEAD_IN_SEC
        stop = lead + 3 + 6.5                      # mid Round 1
        r = generate_demo_session(out=tmp, rounds=2, round_sec=10, break_sec=3,
                                  baseline_sec=3, seed=5, stop_at=stop)
        rows = open(os.path.join(r["root"], "phase_markers.csv"), encoding="utf-8").read().strip().splitlines()[1:]
        labels = [x.split(",")[2] for x in rows]
        check("stopped early: ROUND_1_END then RECORDING_END at the stop",
              labels[-2:] == ["ROUND_1_END", "RECORDING_END"]
              and abs(float(rows[-2].split(",")[0]) - stop) < 1e-3, str(labels[-3:]))
        check("stopped early: nothing planned after the stop",
              "ROUND_2_START" not in labels)
        man = json.load(open(os.path.join(r["root"], "session_manifest.json"), encoding="utf-8"))
        check("stopped early: manifest stopped_early + stopped_t_session",
              man.get("stopped_early") is True and abs(man["stopped_t_session"] - stop) < 1e-3)


# ─── 7: the library matches the current app ─────────────────────────────────

REAL_RECORDING_FILES = {"session_manifest.json", "phase_markers.csv", "moments.csv",
                        "history.jsonl", "contact_quality.csv", "timing_quality.csv",
                        "recording.xdf"}


def test_library():
    import getpass, socket, csv as _csv
    import brain_battle.data.session_storage as ss
    import brain_battle.data.label_vocab as lv
    import brain_battle.data.labels_store as ls
    import brain_battle.data.label_agreement as la
    import brain_battle.data.fighters as fighters
    from brain_battle.capture.session_controller import LEAD_IN_DEFAULT
    from brain_battle.ui.review_label_screen import recording_notes
    from tools.generate_demo_session import generate_library
    tmp = tempfile.mkdtemp(prefix="bb_demo_lib_")
    rows = generate_library(tmp, seed=0)
    infos = {i.root: i for i in ss.list_sessions(tmp)}
    check("library: 6 recordings", len(rows) == 6 and len(infos) == 6, str(len(infos)))
    statuses = {i.status for i in infos.values()}
    check("library: every Sessions status",
          {ss.STATUS_NEEDS_LABELLING, ss.STATUS_LABELLED, ss.STATUS_EXPORTED,
           ss.STATUS_INTERRUPTED} <= statuses, str(statuses))
    check("library: the team's label_list.json is the new default",
          [e["name"] for e in lv.load(tmp, seed_if_missing=False)]
          == [e["name"] for e in lv.DEFAULT_LABELS])
    check("library: the team's fighter_colours.json is there",
          os.path.isfile(fighters.colours_path(tmp)))
    preset = {e["name"] for e in lv.DEFAULT_LABELS}
    cal_missing = stopped = interrupted = 0
    two_labellers = weak = with_note = without_note = notes_custom = False
    missing_files = {}
    for root, info in infos.items():
        man = json.load(open(os.path.join(root, "session_manifest.json"), encoding="utf-8"))
        boxers = man.get("boxers") or {}
        check(f"{os.path.basename(root)}: codes + colours only",
              all(fighters.code_ok(b.get("code")) and b.get("colour") and "name" not in b
                  for b in boxers.values()), str(boxers))
        check(f"{os.path.basename(root)}: 15 s lead-in",
              man.get("lead_in_sec") == LEAD_IN_DEFAULT == 15)
        labels = [r["label"] for r in _csv.DictReader(open(os.path.join(root, "phase_markers.csv"),
                                                          encoding="utf-8"))]
        ok_markers = labels and labels[0] == "RECORDING_START" and "BASELINE_START" in labels
        if not man.get("interrupted"):
            ok_markers = ok_markers and labels[-1] == "RECORDING_END"
        check(f"{os.path.basename(root)}: automatic markers RECORDING_START … "
              f"{'(cut off)' if man.get('interrupted') else 'RECORDING_END'}", ok_markers,
              str(labels))
        files = set(os.listdir(root))
        miss = REAL_RECORDING_FILES - files
        if not any(f.startswith("session_") and f.endswith(".log") for f in files):
            miss.add("session_*.log")
        if miss:
            missing_files[os.path.basename(root)] = sorted(miss)
        cal_missing += not man.get("timing_calibration")
        stopped += bool(man.get("stopped_early")) and not man.get("interrupted")
        interrupted += bool(man.get("interrupted"))
        for m in man.get("moments") or []:
            with_note |= bool(m.get("note")); without_note |= not m.get("note")
        everyone = ls.read_all(root)
        if len(everyone) >= 2:
            two_labellers = True
            names = list(everyone)
            ag = la.compare(everyone[names[0]], everyone[names[1]], names[0], names[1], 0.5)
            check("library: two labellers on one recording agree on some labels, not all",
                  0 < len(ag.matched) < ag.total, ag.sentence(False))
        for who, labs in everyone.items():
            for lab in labs:
                if lab.get("type") == "note":
                    notes_custom = True
                else:
                    check(f"label from the preset list ({lab['label']})", lab["label"] in preset)
        weak |= any(n.startswith("Worth checking") for n in
                    recording_notes(root, man, ss.read_phase_markers(root)))
    check("library: every recording has every file a real recording has",
          not missing_files, str(missing_files))
    check("library: one recording without a buzzer test ('Timing not measured')", cal_missing == 1)
    check("library: one stopped early", stopped == 1)
    check("library: one interrupted", interrupted == 1)
    check("library: moments with notes and without", with_note and without_note)
    check("library: two labellers on one recording", two_labellers)
    check("library: custom notes", notes_custom)
    check("library: a weak-signal stretch ('Worth checking')", weak)
    # no name anywhere — not a fighter name, not this computer's login or name
    user = getpass.getuser().lower()
    host = socket.gethostname().split(".")[0].lower()
    hits = []
    for dp, _dn, fn in os.walk(tmp):
        for f in fn:
            if f.endswith((".mp4", ".xdf")):
                continue
            text = open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read()
            low = text.lower()
            for word in {user, host} - {""}:
                if len(word) >= 3 and word in low:
                    hits.append((f, word))
            if '"boxers"' in text:
                for b in (json.loads(text).get("boxers") or {}).values():
                    if "name" in b:
                        hits.append((f, "boxer name"))
    check("library: no name in any demo file (fighters, this computer's login or name)",
          not hits, str(hits[:5]))
    for root in infos:
        man = json.load(open(os.path.join(root, "session_manifest.json"), encoding="utf-8"))
        if not man.get("interrupted"):
            continue
        check("interrupted: as the app leaves it once found (recording.xdf rebuilt)",
              os.path.isfile(os.path.join(root, "recording.xdf")))


# ─── 8: tools/demo.py guards ────────────────────────────────────────────────

def test_demo_tool_guards():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import demo
    check("demo: default folder is 'Brain Battle Demo' in the home folder",
          demo.default_folder() == os.path.join(os.path.expanduser("~"), "Brain Battle Demo"))
    real = demo.real_recordings_folder()
    check("demo: refuses the real recordings folder", demo.main(["--out", real, "--no-app"]) == 2)
    other = tempfile.mkdtemp(prefix="bb_not_demo_")
    open(os.path.join(other, "precious.txt"), "w").write("x")
    check("demo: --fresh refuses to delete a folder it did not make",
          demo.main(["--out", other, "--fresh", "--no-app"]) == 2
          and os.path.isfile(os.path.join(other, "precious.txt")))
    check("demo: refuses a non-empty folder it did not make",
          demo.main(["--out", other, "--no-app"]) == 2)
    d = tempfile.mkdtemp(prefix="bb_demo_env_")
    open(os.path.join(d, demo.MARKER), "w").write("x")
    env = demo.app_env(d)
    settings = json.load(open(env["BRAIN_BATTLE_CONFIG"], encoding="utf-8"))
    check("demo: the app gets simulated devices and its own settings in the demo folder",
          env["BRAIN_BATTLE_SIMULATE"] == "1" and env["BRAIN_BATTLE_CONFIG"].startswith(d)
          and settings["capture_dir"] == d and "BRAIN_BATTLE_CAPTURE_DIR" not in env)


def test_generator_safety():
    """The CLI must never default a demo into the real recordings folder."""
    from tools.generate_demo_session import _build_arg_parser, _refuse_real_folder
    from brain_battle.data.app_settings import default_capture_root as _default_capture_root

    # --out is required
    raised = False
    try:
        _build_arg_parser().parse_args(["--seed", "1"])
    except SystemExit:
        raised = True
    check("CLI requires --out", raised)

    real = _default_capture_root()
    check("refuses the real recordings folder without --force",
          _refuse_real_folder(real, False) is not None)
    check("allows the real folder with --force",
          _refuse_real_folder(real, True) is None)
    other = os.path.join(tempfile.gettempdir(), "bb_demo_elsewhere")
    check("allows any other folder", _refuse_real_folder(other, False) is None)


if __name__ == "__main__":
    test_layout_and_sidecars_and_eeg()
    test_review_tab_loads()
    test_interrupted_session()
    test_marker_label_format()
    test_stopped_early()
    test_generator_safety()
    test_demo_tool_guards()
    test_library()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
