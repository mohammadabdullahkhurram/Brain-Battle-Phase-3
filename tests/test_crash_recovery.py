"""
Crash recovery + disk-space check tests (Phase 2, item 4).

Part A — crash recovery (pure session_storage logic):
  - a demo session generated with --interrupted is detected as interrupted;
    a cleanly-finished one is not,
  - marking a session writes `interrupted: true` into its manifest WITHOUT
    fabricating a SESSION_END marker, and a marked session is not re-detected.

Part B — disk-space pre-flight (session_storage.disk_space_verdict, the pure
decision shared by the setup screen and the record flow):
  - estimate scales with duration and source count,
  - free >= 2x estimate → "ok",
  - estimate <= free < 2x estimate → "low" (warn),
  - free < estimate → "blocked".

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
    # The pre-flight decision is a pure function (session_storage.disk_space_verdict)
    # shared by the setup screen and the record flow — test it directly, no Qt.
    cfg = SessionConfig(num_rounds=1, round_sec=60, break_sec=0, baseline_sec=0)
    est = ss.estimate_session_bytes(cfg, 4, 2)
    check("free >> estimate: ok",
          ss.disk_space_verdict(cfg, 4, 2, est * 10) == "ok")
    check("estimate <= free < 2x estimate: low (warn)",
          ss.disk_space_verdict(cfg, 4, 2, int(est * 1.5)) == "low")
    check("free just below estimate: blocked",
          ss.disk_space_verdict(cfg, 4, 2, int(est * 0.5)) == "blocked")
    check("free exactly at estimate: not blocked (warns — no headroom)",
          ss.disk_space_verdict(cfg, 4, 2, est) == "low")


if __name__ == "__main__":
    test_crash_recovery()
    test_disk_estimate_scaling()
    test_disk_space_thresholds()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
