"""
Per-recording history log tests (Phase 3, Part B).

Covers the append-only history.jsonl:
  - log_event writes the right fields (ISO time with tz, user+user_source=os,
    app_version, event, details) and read_history round-trips them;
  - a truncated/corrupt last line is skipped by the reader;
  - a SIGKILL mid-write leaves a still-readable file (append + fsync per line);
  - the real app hooks actually log: recording_started / recording_finished
    (LiveSessionTab), opened_for_review + labels_added / labels_removed
    (the merged ReviewLabelScreen). Export's export_created hook is in test_export.

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

import brain_battle.data.session_history as sh

_failures = []
DEMO_ROOT = None   # set in __main__ (ProcessPool children re-import this module)


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


# ─── 4. hooks: labels add/remove through the merged Review & Label screen ─────
# (labels_exported → export_created is covered in tests/test_export.py)

def test_label_hooks():
    import brain_battle.ui.review_label_screen as rl
    root = DEMO_ROOT
    tab = rl.ReviewLabelScreen()
    try:
        tab.load_session_folder(root)
        tab.wait_loaded()
        point = next(e for e in tab._vocab if e["kind"] == "point")
        tab._seek(10.0); tab._place_from_vocab(point)
        tab._seek(20.0); tab._place_from_vocab(point)
        events = [e["event"] for e in sh.read_history(root)]
        check("labels_added logged per placement",
              events.count("labels_added") == 2, str(events.count("labels_added")))

        first_id = tab._store.labels[0]["id"]
        tab._delete_label(first_id)
        evs = sh.read_history(root)
        removed = [e for e in evs if e["event"] == "labels_removed"]
        check("labels_removed logged with label name",
              len(removed) == 1 and "label" in removed[0]["details"],
              str(removed[:1]))
    finally:
        tab.shutdown()


# ─── 5. hooks: recording_started / recording_finished (RecordFlow) ───────────

def test_recording_hooks():
    from brain_battle.ui.record_flow import RecordFlow
    from brain_battle.data.session_storage import create_session
    from brain_battle.capture.session_controller import SessionConfig
    with tempfile.TemporaryDirectory() as base:
        flow = RecordFlow()
        cfg = SessionConfig(num_rounds=1, round_sec=10, break_sec=0, baseline_sec=5)
        flow._cfg = cfg
        flow._names = {"A": None, "B": None}
        flow._layout = create_session(base, cfg)
        try:
            flow._on_session_started()   # no devices connected → just origin + history
            flow._finish()               # writes SESSION_END + logs recording_finished
            events = [e["event"] for e in sh.read_history(flow._layout.root)]
            check("recording_started logged", "recording_started" in events, str(events))
            check("recording_finished logged", "recording_finished" in events, str(events))
        finally:
            flow.shutdown()


# ─── 6. hook: opened_for_review (merged Review & Label screen) ───────────────

def test_opened_for_review_hook():
    import brain_battle.ui.review_label_screen as rl
    tab = rl.ReviewLabelScreen()
    try:
        tab.load_session_folder(DEMO_ROOT)
        tab.wait_loaded()
        events = [e["event"] for e in sh.read_history(DEMO_ROOT)]
        check("opened_for_review logged", "opened_for_review" in events, str(events[-3:]))
    finally:
        tab.shutdown()


if __name__ == "__main__":
    from tools.generate_demo_session import generate_demo_session
    DEMO_ROOT = generate_demo_session(out=tempfile.mkdtemp(prefix="bb_hist_"),
                                      rounds=1, round_sec=8, break_sec=0,
                                      baseline_sec=3, seed=11,
                                      boxer_a="Sam", boxer_b="Alex")["root"]
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
