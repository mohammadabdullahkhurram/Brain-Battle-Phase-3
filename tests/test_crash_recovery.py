"""
Crash recovery + disk-space check tests (Phase 2, item 4).

Part A — crash recovery (pure session_storage logic):
  - a demo session generated with --interrupted is detected as interrupted;
    a cleanly-finished one is not,
  - marking a session writes `interrupted: true` into its manifest WITHOUT
    fabricating a SESSION_END marker, and a marked session is not re-detected.

Part B — disk-space pre-flight (LiveSessionTab._check_disk_space):
  - estimate scales with duration and source count,
  - free >= 2x estimate proceeds silently,
  - estimate <= free < 2x estimate warns (and honours the operator's choice),
  - free < estimate blocks.
Both QMessageBox and shutil.disk_usage are stubbed so the decision logic is
exercised headlessly without modal dialogs or depending on real free space.

Run:  python3 tests/test_crash_recovery.py   (nonzero exit on any failure)
"""
import json
import os
import shutil
import sys
import tempfile

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

import session_storage as ss
from session_controller import SessionConfig
from tools.generate_demo_session import generate_demo_session

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


# ─── Part A: crash recovery ──────────────────────────────────────────────────

def test_crash_recovery():
    with tempfile.TemporaryDirectory() as cap:
        interrupted = generate_demo_session(
            out=cap, rounds=1, round_sec=8, break_sec=0, baseline_sec=4,
            seed=1, interrupted=True)
        clean = generate_demo_session(
            out=cap, rounds=1, round_sec=8, break_sec=0, baseline_sec=4, seed=2)

        check("interrupted session detected",
              ss.is_interrupted_session(interrupted["root"]))
        check("clean session NOT detected",
              not ss.is_interrupted_session(clean["root"]))

        found = ss.scan_interrupted_sessions(cap)
        check("scan finds exactly the interrupted session",
              len(found) == 1 and found[0].root == interrupted["root"],
              str([os.path.basename(f.root) for f in found]))
        check("scan descriptor carries a started_at for the prompt",
              bool(found[0].started_at), repr(found[0].started_at))

        ok = ss.mark_session_interrupted(interrupted["root"])
        check("mark_session_interrupted succeeded", ok)
        manifest = json.loads(open(
            os.path.join(interrupted["root"], "session_manifest.json"),
            encoding="utf-8").read())
        check("manifest now flagged interrupted: true",
              manifest.get("interrupted") is True, str(manifest.get("interrupted")))

        # It must NOT fabricate a SESSION_END — the recording really never finished.
        markers = open(os.path.join(interrupted["root"], "phase_markers.csv"),
                       encoding="utf-8").read()
        check("no fake SESSION_END marker was written",
              "SESSION_END" not in markers)

        check("a flagged session is not re-detected (won't nag)",
              not ss.is_interrupted_session(interrupted["root"]))
        check("scan after marking finds nothing",
              len(ss.scan_interrupted_sessions(cap)) == 0)


# ─── Part B: disk-space pre-flight ──────────────────────────────────────────

class _FakeStdButton:
    # ints so `Yes | No` (as the real code builds its button set) works
    Yes = 1
    No = 2


class _FakeMsgBox:
    StandardButton = _FakeStdButton
    question_answer = _FakeStdButton.Yes
    calls = {"critical": 0, "question": 0}

    @staticmethod
    def critical(*a, **k):
        _FakeMsgBox.calls["critical"] += 1
        return _FakeStdButton.Yes

    @staticmethod
    def question(*a, **k):
        _FakeMsgBox.calls["question"] += 1
        return _FakeMsgBox.question_answer


class _FakeUsage:
    def __init__(self, free):
        self.total = free * 2
        self.used = free
        self.free = free


def test_disk_estimate_scaling():
    base = SessionConfig(num_rounds=1, round_sec=60, break_sec=0, baseline_sec=0)
    longer = SessionConfig(num_rounds=2, round_sec=60, break_sec=0, baseline_sec=0)
    e1 = ss.estimate_session_bytes(base, n_cameras=4, n_eeg=2)
    e2 = ss.estimate_session_bytes(longer, n_cameras=4, n_eeg=2)
    check("estimate doubles with double duration", e2 == 2 * e1, f"{e1} {e2}")
    e_fewer = ss.estimate_session_bytes(base, n_cameras=2, n_eeg=1)
    check("estimate is smaller with fewer sources", e_fewer < e1, f"{e_fewer} < {e1}")
    check("human_bytes formats MB", ss.human_bytes(264_000_000).endswith("MB"),
          ss.human_bytes(264_000_000))


def test_disk_space_thresholds():
    import live_session_tab as lst
    from live_session_tab import LiveSessionTab

    tab = LiveSessionTab()
    cfg = SessionConfig(num_rounds=1, round_sec=60, break_sec=0, baseline_sec=0)
    estimate = ss.estimate_session_bytes(cfg, n_cameras=4, n_eeg=2)

    real_msgbox = lst.QMessageBox
    real_disk_usage = shutil.disk_usage
    lst.QMessageBox = _FakeMsgBox
    try:
        # 1) plenty of room -> proceed, no dialogs.
        _FakeMsgBox.calls = {"critical": 0, "question": 0}
        shutil.disk_usage = lambda p: _FakeUsage(estimate * 10)
        proceed = tab._check_disk_space("/tmp", cfg, 4, 2)
        check("free >> estimate: proceeds with no dialog",
              proceed is True and _FakeMsgBox.calls == {"critical": 0, "question": 0},
              str(_FakeMsgBox.calls))

        # 2) between 1x and 2x -> warn; operator says Yes -> proceed.
        _FakeMsgBox.calls = {"critical": 0, "question": 0}
        _FakeMsgBox.question_answer = _FakeStdButton.Yes
        shutil.disk_usage = lambda p: _FakeUsage(int(estimate * 1.5))
        proceed = tab._check_disk_space("/tmp", cfg, 4, 2)
        check("low space + Yes: warns then proceeds",
              proceed is True and _FakeMsgBox.calls["question"] == 1
              and _FakeMsgBox.calls["critical"] == 0, str(_FakeMsgBox.calls))

        # 3) between 1x and 2x -> warn; operator says No -> abort.
        _FakeMsgBox.calls = {"critical": 0, "question": 0}
        _FakeMsgBox.question_answer = _FakeStdButton.No
        shutil.disk_usage = lambda p: _FakeUsage(int(estimate * 1.5))
        proceed = tab._check_disk_space("/tmp", cfg, 4, 2)
        check("low space + No: warns then aborts",
              proceed is False and _FakeMsgBox.calls["question"] == 1,
              str(_FakeMsgBox.calls))

        # 4) not even 1x -> block.
        _FakeMsgBox.calls = {"critical": 0, "question": 0}
        shutil.disk_usage = lambda p: _FakeUsage(int(estimate * 0.5))
        proceed = tab._check_disk_space("/tmp", cfg, 4, 2)
        check("free < estimate: blocks with a critical message",
              proceed is False and _FakeMsgBox.calls["critical"] == 1
              and _FakeMsgBox.calls["question"] == 0, str(_FakeMsgBox.calls))
    finally:
        lst.QMessageBox = real_msgbox
        shutil.disk_usage = real_disk_usage
        tab.shutdown()


if __name__ == "__main__":
    test_crash_recovery()
    test_disk_estimate_scaling()
    test_disk_space_thresholds()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
