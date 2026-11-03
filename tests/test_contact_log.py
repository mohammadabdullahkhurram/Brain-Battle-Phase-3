"""
Per-sensor contact-quality log (contact_log.py): the writer round-trips and
weak_periods() collapses contiguous weak rows into spans, attributes them to a
round from the phase markers, and the >30 s filter the Saved screen uses works.

Run:  python3 tests/test_contact_log.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import contact_log

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def test_weak_periods():
    root = tempfile.mkdtemp(prefix="bb_contact_")
    w = contact_log.ContactLogWriter(contact_log.path_for(root))
    # Boxer B right-ear (TP10) weak from t=12 to t=45 (33 s, inside Round 1).
    for t in range(0, 60):
        weak = 12 <= t <= 45
        bstat = {"TP9": "good", "AF7": "good", "AF8": "good",
                 "TP10": "flat" if weak else "good"}
        w.append(t, "A", "Signal good", {c: "good" for c in ("TP9", "AF7", "AF8", "TP10")})
        w.append(t, "B", "Weak contact: right ear" if weak else "Signal good", bstat)
    w.close()

    # phase markers: baseline 0, round 1 at 10, break at 40
    markers = [(0.0, "baseline", "BASELINE_START"),
               (10.0, "round", "ROUND_1_START"),
               (40.0, "break", "BREAK_1_START")]
    all_spans = contact_log.weak_periods(root, markers, min_sec=0)
    check("one weak span found for Boxer B right ear",
          len(all_spans) == 1 and all_spans[0]["boxer"] == "B"
          and all_spans[0]["sensor"] == "right ear", str(all_spans))
    span = all_spans[0]
    check("span duration ~33 s", 32 <= span["duration"] <= 34, str(span["duration"]))
    check("span attributed to the round it started in (Round 1)",
          span["round"] == "Round 1", str(span["round"]))

    # the Saved screen's >30 s filter keeps it; a 60 s minimum drops it
    check("survives the 30 s 'worth checking' filter",
          len(contact_log.weak_periods(root, markers, min_sec=30)) == 1)
    check("dropped by a 60 s filter", len(contact_log.weak_periods(root, markers, min_sec=60)) == 0)

    msg = contact_log.describe_weak(span)
    check("describe_weak is plain language naming the boxer + sensor + round",
          "Boxer B" in msg and "right ear" in msg and "Round 1" in msg, msg)


def test_empty_log():
    root = tempfile.mkdtemp(prefix="bb_contact_empty_")
    check("no log file → no weak periods, no crash",
          contact_log.weak_periods(root, [], min_sec=0) == [])


if __name__ == "__main__":
    test_weak_periods()
    test_empty_log()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
