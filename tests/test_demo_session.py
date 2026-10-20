"""
Tests for tools/generate_demo_session.py — the fixture every later roadmap
phase builds against since there is no working hardware right now (see
CLAUDE.md "Project situation").

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

Run:  python3 tests/test_demo_session.py   (nonzero exit on any failure)
Needs: ffmpeg on PATH (video container), PyQt6 + python-vlc (stubbed if
libVLC is absent), same as the other tests/ scripts.
"""
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

def test_review_tab_loads_and_aligns():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import vlc  # noqa: F401
    except Exception:
        import types
        sys.modules["vlc"] = types.ModuleType("vlc")
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)
    import brain_battle_main as bbm

    with tempfile.TemporaryDirectory() as tmp:
        result = generate_demo_session(out=tmp, rounds=1, round_sec=10,
                                       break_sec=3, baseline_sec=3, seed=2)
        tab = bbm.SyncReviewTab()
        try:
            tab.load_session_folder(result["root"])
            check("SyncReviewTab.load_session_folder: no exception", True)
            check("SyncReviewTab: capture_clock read from manifest",
                  tab._capture_t0_unix is not None)

            # Camera order in result["cameras"] matches _SLOT_SPECS order,
            # which is also the order SourceRows 0-3 are populated in.
            generated_starts = [c["start_offset_sec"] for c in result["cameras"]]
            check("SyncReviewTab: measured cam_starts match generated start offsets",
                  all(abs(a - b) < 1e-6 for a, b in zip(tab._cam_starts, generated_starts)),
                  f"loaded={tab._cam_starts} generated={generated_starts}")

            tab._align()
            check("ALIGN: produced a sync_result", tab.sync_result is not None)

            axis_shift = max(generated_starts)
            # _align()/_clamp_ms truncate via int(), not round() — match that.
            expected_seek_ms = [int((axis_shift - s) * 1000) for s in generated_starts]
            check("ALIGN: per-camera seeks match axis_shift - start_offset",
                  tab._residuals_ms == expected_seek_ms,
                  f"got={tab._residuals_ms} want={expected_seek_ms}")

            check("ALIGN: loaded markers from phase_markers.csv",
                  len(tab._phase_markers) == len(result["markers"]),
                  f"{len(tab._phase_markers)} vs {len(result['markers'])}")
        finally:
            # SyncReviewTab.shutdown() stops loader/sync threads but not the
            # VLC media players themselves (brain_battle_main.py) — stop them
            # explicitly here so libVLC's async decode threads aren't still
            # touching files after this function's tempdir is removed.
            for p in tab.media_players:
                if p is not None:
                    try:
                        p.stop()
                    except Exception:
                        pass
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
        check("interrupted: no SESSION_END marker",
              not any("SESSION_END" in row for row in marker_rows),
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


if __name__ == "__main__":
    test_layout_and_sidecars_and_eeg()
    test_review_tab_loads_and_aligns()
    test_interrupted_session()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
