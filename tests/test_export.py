"""
Export-for-analysis tests: the zip contents and the events.csv ordering, plus
the export_created history event.

Demo generated ONCE in the __main__ guard (ProcessPool children re-import).

Run:  python3 tests/test_export.py
"""
import csv
import io
import os
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import brain_battle.data.labels_store as labels_store
import brain_battle.data.session_history as session_history
from brain_battle.ui.review_label_screen import read_phase_markers

_app = QApplication.instance() or QApplication(sys.argv)

_failures = []
DEMO_ROOT = None


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _make_labels():
    return [
        labels_store.new_label("Jab", "A", labels_store.POINT, 5.0, created_by="t"),
        labels_store.new_label("Clinch", "Both", labels_store.DURATION, 2.0, t_end=4.0, created_by="t"),
    ]


def test_bundle_contents():
    import brain_battle.data.export_module as em
    labels = _make_labels()
    phases = read_phase_markers(DEMO_ROOT)
    zip_path = os.path.join(tempfile.mkdtemp(prefix="bb_exp_"), "out.zip")
    worker = em.SessionExportWorker(DEMO_ROOT, labels, phases, zip_path, merge_video=False)
    worker.run()   # synchronous (no thread) for the test

    check("zip created", os.path.isfile(zip_path))
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    def has(prefix):
        return any(n == prefix or n.startswith(prefix) for n in names)
    for required in ("labels.csv", "events.csv", "eeg_coregistered.csv",
                     "session_manifest.json", "phase_markers.csv", "README.txt"):
        check(f"zip has {required}", required in names, str(required))
    check("zip has raw EEG (eeg/)", has("eeg/"))
    check("zip has videos + sidecars (videos/)",
          any(n.startswith("videos/") and n.endswith(".mp4") for n in names)
          and any(n.endswith(".timestamps.csv") for n in names))
    check("no combined video when unchecked",
          not any("combined_2x2" in n for n in names))

    # labels.csv v2 columns
    with zipfile.ZipFile(zip_path) as zf:
        txt = zf.read("labels.csv").decode("utf-8")
    hdr = next(csv.reader(io.StringIO(txt)))
    check("labels.csv has v2 columns",
          hdr == ["id", "label", "who", "kind", "t_start", "t_end", "color",
                  "created_by", "created_at"], str(hdr))

    # export_created history event
    events = [e.get("event") for e in session_history.read_history(DEMO_ROOT)]
    check("export_created history event logged", "export_created" in events)


def test_events_csv_ordering():
    import brain_battle.data.export_module as em
    labels = _make_labels()
    phases = read_phase_markers(DEMO_ROOT)
    zip_path = os.path.join(tempfile.mkdtemp(prefix="bb_exp2_"), "out.zip")
    em.SessionExportWorker(DEMO_ROOT, labels, phases, zip_path, merge_video=False).run()
    with zipfile.ZipFile(zip_path) as zf:
        rows = list(csv.DictReader(io.StringIO(zf.read("events.csv").decode("utf-8"))))

    check("events.csv columns",
          list(rows[0].keys()) == ["t_start", "t_end", "source", "who", "label"],
          str(list(rows[0].keys())))
    ts = [float(r["t_start"]) for r in rows]
    check("events.csv sorted by t_start", ts == sorted(ts))
    check("events.csv merges phases + labels",
          any(r["source"] == "phase" for r in rows) and any(r["source"] == "label" for r in rows))
    # the Clinch duration label carries its t_end
    clinch = next((r for r in rows if r["label"] == "Clinch"), None)
    check("duration label keeps t_end in events.csv",
          clinch is not None and clinch["t_end"] not in ("", None), str(clinch))


if __name__ == "__main__":
    from tools.generate_demo_session import generate_demo_session
    tmp = tempfile.mkdtemp(prefix="bb_exproot_")
    DEMO_ROOT = generate_demo_session(out=tmp, rounds=1, round_sec=10,
                                      break_sec=3, baseline_sec=3, seed=8,
                                      boxer_a="Ivy", boxer_b="Zoe")["root"]
    test_bundle_contents()
    test_events_csv_ordering()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
