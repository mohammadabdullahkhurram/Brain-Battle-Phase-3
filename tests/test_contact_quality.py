"""
contact_quality heuristic tests.

Run:  python3 tests/test_contact_quality.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd
import brain_battle.capture.contact_quality as cq

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def test_channel_status():
    rng = np.random.default_rng(0)
    n = 512
    good = rng.normal(0, 12.0, n)                       # ~12 µV AC
    flat = np.full(n, 42.0) + rng.normal(0, 0.05, n)    # tiny wobble on a DC offset
    noisy = rng.normal(0, 300.0, n)                     # huge std
    clipped = np.where(np.arange(n) % 3 == 0, 1200.0, rng.normal(0, 10, n))
    check("good signal → good", cq.channel_status(good) == "good",
          cq.channel_status(good))
    check("flat signal → flat", cq.channel_status(flat) == "flat",
          cq.channel_status(flat))
    check("noisy signal → noisy", cq.channel_status(noisy) == "noisy",
          cq.channel_status(noisy))
    check("railed signal → clipped", cq.channel_status(clipped) == "clipped",
          cq.channel_status(clipped))
    check("too-few samples → flat", cq.channel_status([1.0]) == "flat")


def test_summary_wording():
    check("all good → 'Signal good'",
          cq.summarise({c: "good" for c in cq.CHANNELS}) == "Signal good")
    s = cq.summarise({"TP9": "flat", "AF7": "good", "AF8": "noisy", "TP10": "good"})
    check("weak names use DESIGN vocabulary + order",
          s == "Weak contact: left ear, right forehead", s)


def test_assess_window_and_at():
    rng = np.random.default_rng(1)
    n = 512
    df = pd.DataFrame({
        "t_session_seconds": np.arange(n) / 256.0,
        "TP9": rng.normal(0, 10, n),
        "AF7": np.full(n, 0.0),               # flat
        "AF8": rng.normal(0, 10, n),
        "TP10": rng.normal(0, 10, n),
    })
    summary, statuses = cq.assess_window(df)
    check("assess_window flags the flat channel",
          statuses["AF7"] == "flat" and "left forehead" in summary, summary)
    summary2, _ = cq.assess_at(df, t_center=1.0, half=0.5)
    check("assess_at returns a window summary", "left forehead" in summary2, summary2)


if __name__ == "__main__":
    test_channel_status()
    test_summary_wording()
    test_assess_window_and_at()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
