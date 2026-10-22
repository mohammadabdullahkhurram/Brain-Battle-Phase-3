"""
Labels-autosave test (Phase 2, item 3).

User labels are atomically written to <session>/labels.json on every add/remove
(schema_version, numeric seconds, nullable boxer/end fields for Phase 4). A
freshly-constructed tab that reopens the session restores them. EXPORT LABELS
CSV keeps working.

This simulates the "place labels, crash, reopen" flow headlessly:
  - tab1 adopts a session folder, places labels → labels.json written
  - a brand-new tab2 (as if after a restart) reopens the folder → labels back
  - removing a label rewrites labels.json; a third tab sees the smaller set

Run:  python3 tests/test_labels_autosave.py   (nonzero exit on any failure)
"""
import json
import os
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

import brain_battle_main as bbm

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _place(tab, label, color, t_seconds):
    """Place one user label at t_seconds (the real _place_label path)."""
    tab.active_label = label
    tab.active_color = color
    tab._fake_ts = t_seconds
    tab._place_label()


def test_autosave_and_restore():
    with tempfile.TemporaryDirectory() as root:
        labels_json = os.path.join(root, bbm.LABELS_JSON_NAME)

        # tab1 adopts the session folder and places three labels.
        tab1 = bbm.LabellingTab()
        tab1._session_root = root
        _place(tab1, "PUNCH_LEFT", "#637DFF", 12.345)
        _place(tab1, "PUNCH_RIGHT", "#06b6d4", 20.5)
        _place(tab1, "BLOCK", "#34d399", 33.0)

        check("labels.json written after placing", os.path.exists(labels_json))
        data = json.loads(open(labels_json, encoding="utf-8").read())
        check("schema_version present and current",
              data.get("schema_version") == bbm.LABELS_SCHEMA_VERSION,
              str(data.get("schema_version")))
        check("three labels saved", len(data.get("labels", [])) == 3,
              str(len(data.get("labels", []))))
        check("time_axis recorded (so Phase 3 can convert to capture axis)",
              data.get("time_axis") == bbm.LABELS_TIME_AXIS,
              repr(data.get("time_axis")))
        first = data["labels"][0]
        check("times stored as numbers, not HH:MM:SS strings",
              isinstance(first["t_seconds"], (int, float))
              and abs(first["t_seconds"] - 12.345) < 1e-6,
              repr(first.get("t_seconds")))
        check("Phase-4 fields present and null (forward-compatible)",
              "boxer" in first and first["boxer"] is None
              and "end_t_seconds" in first and first["end_t_seconds"] is None,
              str(first))

        # tab2 = a fresh tab as if after a restart: reopen restores the labels.
        tab2 = bbm.LabellingTab()
        tab2._session_root = root
        tab2._restore_labels_from_json()
        restored = tab2.get_labels()
        check("reopen restored all three labels", len(restored) == 3,
              str(len(restored)))
        names = sorted(l["label"] for l in restored)
        check("restored label names match",
              names == ["BLOCK", "PUNCH_LEFT", "PUNCH_RIGHT"], str(names))
        secs = {l["label"]: l["timestamp_sec"] for l in restored}
        check("restored times match (numeric round-trip)",
              abs(secs["PUNCH_LEFT"] - 12.345) < 1e-3
              and abs(secs["PUNCH_RIGHT"] - 20.5) < 1e-3
              and abs(secs["BLOCK"] - 33.0) < 1e-3, str(secs))

        # EXPORT LABELS CSV still works on the restored set.
        csv_path = os.path.join(root, "export.csv")
        bbm.LabelsExporter.export(tab2.get_labels(), csv_path)
        with open(csv_path, encoding="utf-8") as f:
            csv_lines = f.read().strip().splitlines()
        check("EXPORT LABELS CSV produced header + 3 rows",
              len(csv_lines) == 4 and csv_lines[0].startswith("timestamp_sec"),
              f"{len(csv_lines)} lines")

        # Removing a label rewrites labels.json with the smaller set.
        for w in tab2._user_label_markers():
            if w.label_name == "PUNCH_RIGHT":
                w._remove()
                break
        data2 = json.loads(open(labels_json, encoding="utf-8").read())
        check("remove rewrote labels.json (2 left)",
              len(data2.get("labels", [])) == 2, str(len(data2.get("labels", []))))

        # A third fresh tab sees the post-removal set.
        tab3 = bbm.LabellingTab()
        tab3._session_root = root
        tab3._restore_labels_from_json()
        names3 = sorted(l["label"] for l in tab3.get_labels())
        check("third reopen reflects the removal",
              names3 == ["BLOCK", "PUNCH_LEFT"], str(names3))


def test_phase_markers_not_autosaved():
    # Phase markers are restored from phase_markers.csv, NOT labels.json — they
    # must never be written into the user-label autosave.
    with tempfile.TemporaryDirectory() as root:
        with open(os.path.join(root, "phase_markers.csv"), "w", encoding="utf-8") as f:
            f.write("t_session_seconds,phase_id,label,round_num,unix_time\n")
            f.write("0.000,baseline,BASELINE_START,0,1.0\n")
            f.write("5.000,round,ROUND_1_START,1,1.0\n")

        tab = bbm.LabellingTab()
        tab._session_root = root
        tab._load_phase_markers_csv(os.path.join(root, "phase_markers.csv"))
        _place(tab, "PUNCH_LEFT", "#637DFF", 7.0)   # one real user label

        labels_json = os.path.join(root, bbm.LABELS_JSON_NAME)
        data = json.loads(open(labels_json, encoding="utf-8").read())
        saved = data.get("labels", [])
        check("only the user label is autosaved (phase markers excluded)",
              len(saved) == 1 and saved[0]["label"] == "PUNCH_LEFT",
              str([l["label"] for l in saved]))


if __name__ == "__main__":
    test_autosave_and_restore()
    test_phase_markers_not_autosaved()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
