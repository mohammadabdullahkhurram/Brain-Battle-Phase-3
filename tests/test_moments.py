"""
Marked moments and their notes (session_storage, DESIGN "Mark this moment").

  • a moment is stored as {t, note} in the manifest AND in moments.csv;
  • a note can be added straight away, later while still recording, or in
    Review (set_moment_note — under the folder lock, moments.csv rewritten);
  • a moment without a note keeps the recording in "Needs labels", even when
    it has labels; once every moment has a note it is "Labelled";
  • recording.xdf's BrainBattle_Moments stream holds the same rows as
    moments.csv;
  • older manifests whose moments have no "note" still read ("" note).

Run:  python3 tests/test_moments.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401
import csv
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import brain_battle.data.session_storage as ss
import brain_battle.data.labels_store as ls
from brain_battle.capture.session_controller import SessionConfig

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _csv_rows(root):
    with open(os.path.join(root, "moments.csv"), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_moment_notes():
    base = tempfile.mkdtemp(prefix="bb_moments_")
    lay = ss.create_session(base, SessionConfig(), boxer_a="K7Q2", boxer_b="M3XA")
    root = str(lay.root)
    lay.add_moment(207.25, note="  B dropped   guard after combination ")
    lay.add_moment(373.5)                          # "Add note later"
    man = json.load(open(lay.manifest_path(), encoding="utf-8"))
    check("manifest: moments carry t + note (tidied)",
          [(m["t_session_seconds"], m["note"]) for m in man["moments"]]
          == [(207.25, "B dropped guard after combination"), (373.5, "")], str(man["moments"]))
    rows = _csv_rows(root)
    check("moments.csv: t_session_seconds + note, one row per moment",
          list(rows[0]) == ["t_session_seconds", "note"]
          and [(r["t_session_seconds"], r["note"]) for r in rows]
          == [("207.250", "B dropped guard after combination"), ("373.500", "")], str(rows))
    check("one moment still has no note", ss.moments_without_note(root) == 1)

    lay.set_moment_note(1, "Ref warned B")         # the popup, still recording
    check("note added while still recording reaches moments.csv",
          _csv_rows(root)[1]["note"] == "Ref warned B")
    check("Review: change a note by its time", ss.set_moment_note(root, 373.5, "Ref warned B twice"))
    check("Review: unknown time is refused", not ss.set_moment_note(root, 999.0, "x"))
    check("Review's edit is in the manifest and moments.csv",
          ss.read_moments(root)[1]["note"] == "Ref warned B twice"
          and _csv_rows(root)[1]["note"] == "Ref warned B twice")


def test_status_needs_labels_until_every_moment_has_a_note():
    base = tempfile.mkdtemp(prefix="bb_moments_status_")
    lay = ss.create_session(base, SessionConfig(), boxer_a="K7Q2", boxer_b="M3XA")
    root = str(lay.root)
    lay.add_moment(10.0)
    store = ls.open_for_labeller(root, "Maya K")
    store.add(ls.new_label("Punch landed", "A", ls.POINT, 12.0, created_by="Maya K"))
    store.save()
    info = ss.session_info(root)
    check("labelled but a moment has no note → Needs labels",
          info.status == ss.STATUS_NEEDS_LABELLING and info.moments_open == 1
          and info.moments == 1, info.status)
    ss.set_moment_note(root, 10.0, "clean jab")
    ss._INFO_CACHE.clear()
    check("every moment noted + labels → Labelled",
          ss.session_info(root).status == ss.STATUS_LABELLED)


def test_old_manifest_without_notes():
    base = tempfile.mkdtemp(prefix="bb_moments_old_")
    lay = ss.create_session(base, SessionConfig())
    root = str(lay.root)
    ss._update_manifest(root, lambda m: m.update(moments=[{"t_session_seconds": 4.0,
                                                            "unix_time": 1.0}]))
    check("an older moment without a note reads as an empty note",
          ss.read_moments(root) == [{"t_session_seconds": 4.0, "note": ""}])


def test_xdf_moments_stream_matches_csv():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import generate_demo_session as gen
    import pyxdf
    out = tempfile.mkdtemp(prefix="bb_moments_xdf_")
    r = gen.generate_demo_session(out=out, rounds=1, round_sec=6, break_sec=0, baseline_sec=2,
                                  moments=[(3.5, "B covered up"), (6.0, "")])
    streams, _ = pyxdf.load_xdf(r["xdf"], dejitter_timestamps=False)
    by = {s["info"]["name"][0]: s for s in streams}
    mo = by.get("BrainBattle_Moments")
    t0 = float(by["BrainBattle_Markers"]["info"]["desc"][0]["brain_battle"][0]["session_start"][0])
    rows = _csv_rows(r["root"])
    check("XDF BrainBattle_Moments = moments.csv (times + notes)",
          mo is not None and [v[0] for v in mo["time_series"]] == [x["note"] for x in rows]
          and all(abs((ts - t0) - float(x["t_session_seconds"])) < 1e-3
                  for ts, x in zip(mo["time_stamps"], rows)), str(rows))
    check("XDF BrainBattle_Markers holds only the system markers (no MOMENT)",
          "MOMENT" not in [v[0] for v in by["BrainBattle_Markers"]["time_series"]])


if __name__ == "__main__":
    test_moment_notes()
    test_status_needs_labels_until_every_moment_has_a_note()
    test_old_manifest_without_notes()
    test_xdf_moments_stream_matches_csv()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
