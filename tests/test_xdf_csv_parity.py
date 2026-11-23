"""
Every stream in recording.xdf also exists as its own CSV — in the recording
folder and in the export (Part 2.8 of the professor's changes).

A demo recording (with moments + notes, labels from two labellers including a
custom note, and a buzzer-test calibration) is exported; then every stream in
the export's recording.xdf is matched to its CSV:

    BrainBattle_EEG_<A|B>            eeg/session_<band>.csv
    BrainBattle_<ACC|GYRO|PPG>_<A|B> sensors/session_<band>_<KIND>.csv
    BrainBattle_Markers              phase_markers.csv
    BrainBattle_Moments              moments.csv
    BrainBattle_Labels_<labeller>    labels.csv (rows of that labeller)
    BrainBattle_Video_<camera>       videos/session_<camera>.timestamps.csv

Same number of samples, same values, and the same times — the XDF's minus the
buzzer-test correction, the CSV's exactly as recorded. A stream with no CSV in
this table fails the test, so a new stream cannot be added without its CSV.
Also checked: timing_quality.csv is in the recording and in the export.

Run:  python3 tests/test_xdf_csv_parity.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401
import csv
import io
import os
import sys
import tempfile
import time
import zipfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

import brain_battle.capture.timing as timing
import brain_battle.data.export_module as em
import brain_battle.data.labels_store as ls
import brain_battle.data.session_storage as ss
import brain_battle.data.session_xdf as sx
import generate_demo_session as gen

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


CAL = {"SIDE_01": 3, "SIDE_02": -1, "POV_01": 10, "POV_02": 5, "EEG_A": -20, "EEG_B": -15}


def _rows(zf, name):
    with zf.open(name) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))


def test_parity():
    out = tempfile.mkdtemp(prefix="bb_parity_")
    r = gen.generate_demo_session(out=out, rounds=1, round_sec=6, break_sec=0, baseline_sec=2,
                                  moments=[(3.0, "B covered up"), (5.5, "")], calibration=CAL)
    root = r["root"]
    for who, items in (("Maya K", [("Punch landed", "A", 4.0, None), ("Clinch", "Both", 5.0, 6.5)]),
                       ("Jonas R", [("Punch initiated", "B", 4.2, None)])):
        st = ls.open_for_labeller(root, who)
        for name, side, t0, t1 in items:
            st.add(ls.new_label(name, side, ls.DURATION if t1 else ls.POINT, t0, t_end=t1,
                                created_by=who))
        if who == "Maya K":
            st.add(ls.new_note("A keeps pressing forward", "A", 6.0, created_by=who))
        st.save()
    check("timing_quality.csv is in the recording",
          os.path.isfile(os.path.join(root, "timing_quality.csv")))
    check("moments.csv is in the recording", os.path.isfile(os.path.join(root, "moments.csv")))

    zip_path = os.path.join(out, "export.zip")
    w = em.SessionExportWorker(root, None, None, zip_path)
    done = {}
    w.finished.connect(lambda p: done.setdefault("ok", p))
    w.error.connect(lambda m: done.setdefault("err", m))
    w.start()
    deadline = time.monotonic() + 120
    while not done and time.monotonic() < deadline:
        _app.processEvents(); time.sleep(0.02)
    w.wait(5000)
    check("export finished", "ok" in done, str(done))
    if "ok" not in done:
        return

    import pyxdf
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        check("export has timing_quality.csv and moments.csv",
              {"timing_quality.csv", "moments.csv"} <= names)
        tmp_xdf = os.path.join(out, "recording.xdf")
        with open(tmp_xdf, "wb") as f:
            f.write(zf.read("recording.xdf"))
        streams, _ = pyxdf.load_xdf(tmp_xdf, dejitter_timestamps=False)
        manifest = ss._read_manifest(root)
        t0 = float(manifest["capture_clock"]["t0_lsl"])
        corr = timing.corrections(manifest)
        eeg = ss.eeg_files(root)
        labels_rows = _rows(zf, "labels.csv")
        unmapped = []
        for s in streams:
            name = s["info"]["name"][0]
            ts = np.asarray(s["time_stamps"], dtype=float) - t0
            vals = s["time_series"]
            csv_name = None
            if name.startswith("BrainBattle_EEG_"):
                who = name[-1]
                csv_name = f"eeg/{os.path.basename(eeg[who])}"
                rows = _rows(zf, csv_name)
                tt = np.array([float(x["t_session_seconds"]) for x in rows]) - corr.get(f"EEG_{who}", 0)
                tp9 = np.array([float(x["TP9"]) for x in rows])
                ok = len(rows) == len(ts) and np.allclose(ts, tt, atol=1e-5) \
                    and np.allclose(np.asarray(vals)[:, 0], tp9, atol=1e-3)
            elif any(name.startswith(f"BrainBattle_{k}_") for k in ("ACC", "GYRO", "PPG")):
                kind, who = name.split("_")[1], name[-1]
                stem = os.path.splitext(os.path.basename(eeg[who]))[0]
                csv_name = f"sensors/{stem}_{kind}.csv"
                rows = _rows(zf, csv_name)
                tt = np.array([float(x["t_session_seconds"]) for x in rows]) - corr.get(f"EEG_{who}", 0)
                ok = len(rows) == len(ts) and np.allclose(ts, tt, atol=1e-5)
            elif name == "BrainBattle_Markers":
                csv_name = "phase_markers.csv"
                rows = _rows(zf, csv_name)
                ok = [x["label"] for x in rows] == [v[0] for v in vals] and np.allclose(
                    ts, [float(x["t_session_seconds"]) for x in rows], atol=1e-5)
            elif name == "BrainBattle_Moments":
                csv_name = "moments.csv"
                rows = _rows(zf, csv_name)
                ok = [x["note"] for x in rows] == [v[0] for v in vals] and np.allclose(
                    ts, [float(x["t_session_seconds"]) for x in rows], atol=1e-5)
            elif name.startswith("BrainBattle_Labels_"):
                who = s["info"]["desc"][0]["brain_battle"][0]["labeller"][0]
                csv_name = "labels.csv"
                mine = [x for x in labels_rows if x["labeller"] == who]
                want = sorted([float(x["t_start"]) for x in mine]
                              + [float(x["t_end"]) for x in mine if x["t_end"]])
                ok = np.allclose(sorted(ts), want, atol=1e-5)
                notes = [v[0] for v in vals if v[0].startswith("Note/")]
                ok = ok and notes == [f"Note/{x['who']}: {x['text']}" for x in mine
                                      if x["type"] == "note"]
            elif name.startswith("BrainBattle_Video_"):
                stem = name[len("BrainBattle_Video_"):]
                csv_name = f"videos/session_{stem}.timestamps.csv"
                rows = _rows(zf, csv_name)
                tt = np.array([float(x["t_session_seconds"]) for x in rows]) - corr.get(stem, 0)
                ok = len(rows) == len(ts) and np.allclose(ts, tt, atol=1e-5) \
                    and [int(v[0]) for v in vals] == [int(x["frame_index"]) for x in rows]
            else:
                unmapped.append(name)
                continue
            check(f"{name} = {csv_name} (samples, values, times minus correction)",
                  csv_name in names and ok)
        check("every XDF stream has its CSV (no stream without one)", not unmapped, str(unmapped))
        kinds = {s["info"]["name"][0].split("_")[1] for s in streams}
        check("the export covered every kind of stream",
              {"EEG", "ACC", "GYRO", "Markers", "Moments", "Labels", "Video"} <= kinds, str(kinds))
        d = {s["info"]["name"][0]: s for s in streams}["BrainBattle_Video_POV_01"]
        bb = d["info"]["desc"][0]["brain_battle"][0]
        check("an XDF stream names the correction applied to it",
              abs(float(bb["timing_correction_ms"][0]) - corr["POV_01"] * 1000) < 0.01,
              bb.get("timing_correction_ms"))


if __name__ == "__main__":
    test_parity()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
