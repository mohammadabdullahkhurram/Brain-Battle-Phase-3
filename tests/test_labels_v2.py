"""
labels_store v2 tests: schema, v1 migration (+ .v1.bak), undo/redo, events
timeline. Pure logic — no Qt.

Run:  python3 tests/test_labels_v2.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import labels_store as ls

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def test_new_label_schema():
    lab = ls.new_label("Jab", "A", ls.POINT, 4.2, created_by="sam")
    for k in ("id", "label", "who", "kind", "t_start", "t_end", "color",
              "created_by", "created_at"):
        check(f"new_label has field {k}", k in lab)
    check("point label has null t_end", lab["t_end"] is None)
    check("who preserved", lab["who"] == "A")
    dur = ls.new_label("Clinch", "Both", ls.DURATION, 10.0, t_end=14.0)
    check("duration label keeps t_end", dur["t_end"] == 14.0)
    bad = ls.new_label("X", "nonsense", ls.POINT, 1.0)
    check("invalid who falls back to Both", bad["who"] == "Both")


def test_v1_migration():
    d = tempfile.mkdtemp(prefix="bb_lblmig_")
    path = os.path.join(d, "labels.json")
    v1 = {
        "schema_version": 1,
        "time_axis": "labelling_playhead_offset0",
        "labels": [
            {"t_seconds": 3.0, "label": "JAB", "color": "#111", "boxer": None,
             "end_t_seconds": None},
            {"t_seconds": 7.0, "label": "CLINCH", "color": "#222", "boxer": "A",
             "end_t_seconds": 9.5},
        ],
    }
    with open(path, "w") as f:
        json.dump(v1, f)

    store, migrated = ls.load(path)
    check("v1 file reports migrated=True", migrated is True)
    check("migrated 2 labels", len(store) == 2, str(len(store)))
    on_disk = json.loads(open(path, encoding="utf-8").read())
    check("file rewritten to schema v2", on_disk.get("schema_version") == 2)
    check("file time_axis is t_session_seconds",
          on_disk.get("time_axis") == "t_session_seconds")
    check(".v1.bak kept", os.path.isfile(path + ".v1.bak"))
    bak = json.loads(open(path + ".v1.bak", encoding="utf-8").read())
    check(".v1.bak is the original v1", bak.get("schema_version") == 1)

    labs = store.labels
    clinch = next(x for x in labs if x["label"] == "CLINCH")
    check("duration inferred from end_t_seconds", clinch["kind"] == ls.DURATION
          and clinch["t_end"] == 9.5)
    check("v1 boxer carried to who", clinch["who"] == "A")
    jab = next(x for x in labs if x["label"] == "JAB")
    check("point inferred (no end)", jab["kind"] == ls.POINT and jab["t_end"] is None)

    # loading the migrated file again is a no-op (already v2)
    store2, migrated2 = ls.load(path)
    check("second load not migrated", migrated2 is False and len(store2) == 2)


def test_undo_redo():
    store = ls.LabelsStore()
    a = store.add(ls.new_label("Jab", "A", ls.POINT, 1.0))
    b = store.add(ls.new_label("Cross", "B", ls.POINT, 2.0))
    check("two labels added", len(store) == 2)

    store.delete(a["id"])
    check("after delete, one label", len(store) == 1 and store.by_id(a["id"]) is None)

    # undo the delete → a comes back
    store.undo()
    check("undo delete restores label", store.by_id(a["id"]) is not None and len(store) == 2)

    # undo the add of b → b gone
    store.undo()
    check("undo add removes label", store.by_id(b["id"]) is None and len(store) == 1)

    # redo → b back
    store.redo()
    check("redo re-adds label", store.by_id(b["id"]) is not None and len(store) == 2)

    # a fresh mutation clears redo
    store.undo()                       # remove b again
    store.add(ls.new_label("Hook", "A", ls.POINT, 3.0))
    check("new mutation clears redo", store.can_redo() is False)


def test_duration_end_is_undoable():
    """The duration flow commits ONE label on the second press; undo removes it."""
    store = ls.LabelsStore()
    store.add(ls.new_label("Clinch", "Both", ls.DURATION, 10.0, t_end=14.0))
    check("duration label present", len(store) == 1)
    store.undo()
    check("undo of duration-end removes it", len(store) == 0)


def test_events_timeline():
    labels = [
        ls.new_label("Jab", "A", ls.POINT, 5.0),
        ls.new_label("Clinch", "Both", ls.DURATION, 2.0, t_end=4.0),
    ]
    markers = [(0.0, "baseline", "BASELINE_START"), (3.0, "round", "ROUND_1_START")]
    rows = ls.events_timeline(labels, markers)
    check("events rows = markers + labels", len(rows) == 4, str(len(rows)))
    check("events sorted by t_start",
          [r["t_start"] for r in rows] == sorted(r["t_start"] for r in rows))
    # at equal t_start, phase precedes label (none here, but check tie rule)
    tie = ls.events_timeline([ls.new_label("X", "A", ls.POINT, 3.0)],
                             [(3.0, "round", "ROUND_1_START")])
    check("phase precedes label at same t", tie[0]["source"] == "phase")
    row = next(r for r in rows if r["label"] == "Clinch")
    check("duration event keeps t_end", row["t_end"] == 4.0 and row["source"] == "label")


if __name__ == "__main__":
    test_new_label_schema()
    test_v1_migration()
    test_undo_redo()
    test_duration_end_is_undoable()
    test_events_timeline()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
