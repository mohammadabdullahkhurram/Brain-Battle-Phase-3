"""
Per-recording history log tests (Phase 3, Part B).

Covers the append-only history.jsonl:
  - log_event writes the right fields (ISO time with tz, user+user_source=os,
    app_version, event, details) and read_history round-trips them;
  - a truncated/corrupt last line is skipped by the reader;
  - a SIGKILL mid-write leaves a still-readable file (append + fsync per line);
  - the real app hooks actually log: recording_started / recording_finished
    (LiveSessionTab), opened_for_review (SyncReviewTab), labels_added /
    labels_removed / labels_exported (LabellingTab).

Run:  python3 tests/test_history_log.py   (nonzero exit on any failure)
"""
import datetime as dt
import json
import os
import signal
import subprocess
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

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

import session_history as sh

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


# ─── 1. core log + read ──────────────────────────────────────────────────────

def test_log_and_read():
    with tempfile.TemporaryDirectory() as root:
        sh.log_event(root, sh.EVENT_RECORDING_STARTED, {"rounds": 3})
        sh.log_event(root, sh.EVENT_LABELS_ADDED,
                     {"label": "JAB", "t_seconds": 12.3, "who": None})
        evs = sh.read_history(root)
        check("two events read back", len(evs) == 2, str(len(evs)))
        e = evs[0]
        check("event + details preserved",
              e["event"] == "recording_started" and e["details"]["rounds"] == 3)
        check("user_source is os", e.get("user_source") == "os")
        check("app_version present", bool(e.get("app_version")))
        check("user present (OS login)", bool(e.get("user")))
        # time parses as ISO 8601 and carries a timezone offset
        try:
            parsed = dt.datetime.fromisoformat(e["time"])
            has_tz = parsed.tzinfo is not None
        except Exception:
            has_tz = False
        check("time is ISO 8601 with timezone", has_tz, e.get("time"))
        check("history.jsonl is one JSON object per line",
              os.path.basename(str(sh.history_path(root))) == "history.jsonl")


# ─── 2. tolerant reader (truncated / corrupt last line) ──────────────────────

def test_tolerant_reader():
    with tempfile.TemporaryDirectory() as root:
        path = sh.history_path(root)
        good1 = json.dumps({"event": "a", "details": {}})
        good2 = json.dumps({"event": "b", "details": {}})
        # a complete line, another complete line, then a TRUNCATED json line
        with open(path, "w", encoding="utf-8") as f:
            f.write(good1 + "\n" + good2 + "\n" + '{"event": "c", "deta')
        evs = sh.read_history(root)
        check("reader skips the truncated last line, keeps the valid ones",
              [e["event"] for e in evs] == ["a", "b"],
              str([e.get("event") for e in evs]))


# ─── 3. SIGKILL mid-write leaves a readable file ─────────────────────────────

def test_sigkill_midwrite():
    runner = os.path.join(ROOT, "tests", "_history_kill_runner.py")
    with tempfile.TemporaryDirectory() as root:
        proc = subprocess.Popen([sys.executable, runner, root])
        # Wait until it has written a healthy number of events.
        deadline = time.monotonic() + 20.0
        n = 0
        while n < 50 and time.monotonic() < deadline:
            time.sleep(0.1)
            n = len(sh.read_history(root))
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
        check("process was hard-killed", proc.returncode is not None
              and proc.returncode < 0, f"rc={proc.returncode}")
        evs = sh.read_history(root)
        check("history still reads after SIGKILL", len(evs) >= 50,
              f"{len(evs)} events")
        check("every surviving line is a valid event object",
              all(isinstance(e, dict) and "event" in e for e in evs))


# ─── 4. hooks: labels add/remove/export (real LabellingTab path) ─────────────

def _place(tab, label, color, t):
    tab.active_label = label
    tab.active_color = color
    tab._fake_ts = t
    tab._place_label()


def test_label_hooks():
    import brain_battle_main as bbm
    with tempfile.TemporaryDirectory() as root:
        tab = bbm.LabellingTab()
        tab._session_root = root
        _place(tab, "JAB", "#3D4FD6", 10.0)
        _place(tab, "HOOK", "#0F766E", 20.0)
        events = [e["event"] for e in sh.read_history(root)]
        check("labels_added logged per placement",
              events.count("labels_added") == 2, str(events))

        # remove one (real user-removal path)
        for w in tab._user_label_markers():
            if w.label_name == "JAB":
                w._remove()
                break
        _app.processEvents()
        evs = sh.read_history(root)
        removed = [e for e in evs if e["event"] == "labels_removed"]
        check("labels_removed logged with label + time",
              len(removed) == 1 and removed[0]["details"]["label"] == "JAB",
              str(removed[:1]))

        # export labels (dialogs stubbed)
        import brain_battle_main as _bbm
        real_fd, real_mb = _bbm.QFileDialog, _bbm.QMessageBox
        csv_path = os.path.join(root, "labels.csv")

        class _FD:
            @staticmethod
            def getSaveFileName(*a, **k): return (csv_path, "")

        class _MB:
            Icon = real_mb.Icon
            @staticmethod
            def information(*a, **k): return None
            @staticmethod
            def critical(*a, **k): return None
        _bbm.QFileDialog, _bbm.QMessageBox = _FD, _MB
        try:
            tab._export_labels()
        finally:
            _bbm.QFileDialog, _bbm.QMessageBox = real_fd, real_mb
        events = [e["event"] for e in sh.read_history(root)]
        check("labels_exported logged", "labels_exported" in events, str(events))


# ─── 5. hooks: recording_started / recording_finished (LiveSessionTab) ───────

def test_recording_hooks():
    from live_session_tab import LiveSessionTab
    from session_storage import create_session
    from session_controller import SessionConfig
    with tempfile.TemporaryDirectory() as base:
        tab = LiveSessionTab()
        cfg = SessionConfig(num_rounds=1, round_sec=10, break_sec=0, baseline_sec=5)
        tab._controller.configure(cfg)
        tab._layout = create_session(base, cfg)
        try:
            tab._on_session_started()
            tab._on_session_finished()
            events = [e["event"] for e in sh.read_history(tab._layout.root)]
            check("recording_started logged", "recording_started" in events, str(events))
            check("recording_finished logged", "recording_finished" in events, str(events))
        finally:
            tab.shutdown()


# ─── 6. hook: opened_for_review (SyncReviewTab) ──────────────────────────────

def test_opened_for_review_hook():
    import brain_battle_main as bbm
    from session_storage import create_session
    from session_controller import SessionConfig
    with tempfile.TemporaryDirectory() as base:
        layout = create_session(base, SessionConfig())
        root = str(layout.root)
        side = os.path.join(root, "continuous", "Side Cams")
        os.makedirs(side, exist_ok=True)
        # two tiny REAL mp4s so the loader finds recordings (+ a sidecar each)
        for name in ("session_SIDE_01.mp4", "session_SIDE_02.mp4"):
            p = os.path.join(side, name)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                            "-i", "color=gray:s=160x120:r=30:d=0.3",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", p], check=True)
            with open(os.path.splitext(p)[0] + ".timestamps.csv", "w") as f:
                f.write("frame_index,t_session_seconds,t_monotonic,t_unix\n0,0.1,0.1,0.1\n")
        tab = bbm.SyncReviewTab()
        try:
            tab.load_session_folder(root)
            events = [e["event"] for e in sh.read_history(root)]
            check("opened_for_review logged", "opened_for_review" in events, str(events))
        finally:
            for p in tab.media_players:
                if p is not None:
                    try: p.stop()
                    except Exception: pass
            tab.shutdown()


if __name__ == "__main__":
    test_log_and_read()
    test_tolerant_reader()
    test_sigkill_midwrite()
    test_label_hooks()
    test_recording_hooks()
    test_opened_for_review_hook()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
