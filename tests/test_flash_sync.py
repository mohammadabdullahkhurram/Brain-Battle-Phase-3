"""
Flash-simultaneity check (verify only — no behaviour change).

The owner noticed the per-phase white flash is not perfectly simultaneous across
the four cameras. That is expected: each camera captures frames on its own
phase, so the first frame at/after a phase marker lands up to one frame period
apart. This test confirms the spread is bounded by one frame period (≤ 34 ms) at
EVERY phase marker of a generated 12-minute recording — it does NOT try to make
them identical.

The 12-minute demo is generated into a temporary folder and removed afterwards.

Run:  python3 tests/test_flash_sync.py
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

import brain_battle.playback.pyav_player as pp
from brain_battle.ui.review_label_screen import _discover_cameras, read_phase_markers

_failures = []
ONE_FRAME = 1.0 / 29.5   # the slowest (drift) camera's frame period ≈ 33.9 ms


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _flash_time(cam, mt):
    """t_session of the first full-white (flash) frame at/after marker time mt,
    detected from the decoded pixels."""
    base = cam.index_for_t(mt)
    if base is None:
        base = 0
    for i in range(max(0, base), min(base + 8, cam.n_frames)):
        frame = cam.decode_index(i)
        if frame is not None and float(frame.mean()) > 180.0:   # mostly white
            return cam.t_for_index(i)
    return None


def test_flash_within_one_frame():
    from tools.generate_demo_session import generate_demo_session
    tmp = tempfile.mkdtemp(prefix="bb_flash_")
    try:
        r = generate_demo_session(out=tmp, rounds=3, round_sec=180, break_sec=60,
                                  baseline_sec=60, seed=42,
                                  boxer_a="Sam", boxer_b="Alex")
        root = r["root"]
        check("generated a 12-min recording", abs(r["total_duration_sec"] - 720) < 1,
              f"{r['total_duration_sec']:.0f}s")
        specs = _discover_cameras(root)
        cams = [pp.PyAVCamera(p, n) for p, n in specs]
        # Only markers after every camera has started recording carry a flash on
        # all four tiles — the baseline marker at t≈0 happens before the first
        # frame is captured, so there is legitimately no flash to see there.
        started = max(c.first_t for c in cams)
        markers = [m for m in read_phase_markers(root)
                   if not m[2].upper().startswith("SESSION") and m[0] >= started]
        check("has phase markers to check", len(markers) >= 4, str(len(markers)))

        worst = 0.0
        all_ok = True
        for (mt, _pid, label) in markers:
            times = [_flash_time(c, mt) for c in cams]
            found = [t for t in times if t is not None]
            if len(found) != len(cams):
                all_ok = False
                check(f"flash seen on all 4 tiles at {label} ({mt:.1f}s)", False,
                      f"{len(found)}/{len(cams)} tiles")
                continue
            spread = max(found) - min(found)
            worst = max(worst, spread)
            if spread > ONE_FRAME + 1e-6:
                all_ok = False
                check(f"flash within one frame at {label} ({mt:.1f}s)", False,
                      f"spread {spread*1000:.1f} ms")
        check("every phase-marker flash is within one frame period across tiles",
              all_ok, f"worst spread {worst*1000:.1f} ms (one frame ≈ {ONE_FRAME*1000:.1f} ms)")
        for c in cams:
            c.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_flash_within_one_frame()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
