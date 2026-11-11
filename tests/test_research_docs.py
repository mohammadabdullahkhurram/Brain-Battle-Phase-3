"""
Research & data docs honesty tests (D2).

These keep docs/research-and-data/ truthful by checking it against the code,
not against memory:

  1. Format reference matches the code — every session_manifest.json key, every
     CSV column (markers, camera sidecars, EEG, contact log, co-registered
     EEG, labels, events), every labels.json / label_list.json field, every
     history.jsonl event type, and every export-zip entry the code ACTUALLY
     writes appears, backticked, in file-formats.md. Add a field to the code
     without documenting it and this test goes red.
  2. The Python loading examples in loading-the-data.md run green against a
     freshly generated demo export.
  3. The R examples run too, if Rscript + readr/dplyr are available; otherwise
     they are reported "not automatically tested" (per the page's own note).

A short demo session + a real export are generated ONCE in the __main__ guard
(the generator uses a ProcessPool whose children re-import this module).

Run:  python3 tests/test_research_docs.py
"""
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import brain_battle.capture.contact_log as contact_log
import brain_battle.data.label_vocab as label_vocab
import brain_battle.data.labels_store as labels_store
import brain_battle.data.session_history as session_history
import brain_battle.data.export_module as export_module
from brain_battle.ui.review_label_screen import read_phase_markers

_app = QApplication.instance() or QApplication(sys.argv)

RD_DIR = os.path.join(ROOT, "docs", "research-and-data")
FORMATS_MD = os.path.join(RD_DIR, "file-formats.md")
LOADING_MD = os.path.join(RD_DIR, "loading-the-data.md")

_failures = []
DEMO_ROOT = None
EXPORT_DIR = None         # unzipped export
ZIP_NAMES = []            # export zip arcnames

# Manifest keys that are dynamic/instance-specific, not documentable literals.
_DYNAMIC_KEY = re.compile(r"^(EEG|POV|SIDE|CAM)_\d+$")
_SKIP_KEYS = {"A", "B"}   # boxer-slot letters (the documented fields are name/headband)


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


# ── documentation helpers ───────────────────────────────────────────────────

def _formats_text():
    with open(FORMATS_MD, encoding="utf-8") as f:
        return f.read()


def _inline_code(text):
    """All inline-code span contents, joined — so a grouped span like
    `{num_rounds, round_sec}` still documents each name inside it. Fenced
    code blocks are stripped first so their backticks don't mis-pair the
    inline-span matching."""
    no_fences = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    # `[^`]*` (not `+`) so an intentionally-empty span `` is consumed rather
    # than leaving two stray backticks that shift all later pairings.
    return "  ".join(re.findall(r"`([^`]*)`", no_fences))


def _documented_backtick(text, name):
    """True if `name` appears (as a whole token) inside any inline-code span."""
    spans = _inline_code(text)
    return re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", spans) is not None


def _documented_plain(text, phrase):
    # collapse whitespace so a line-wrapped phrase still matches
    return " ".join(phrase.split()) in " ".join(text.split())


def _assert_all_documented(text, names, kind):
    missing = sorted(n for n in names if not _documented_backtick(text, n))
    check(f"every {kind} is documented in file-formats.md",
          not missing, f"undocumented: {missing}")


# ── 1. format reference matches the code ────────────────────────────────────

def _collect_keys(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _collect_keys(v, out)
    elif isinstance(obj, list):
        for item in obj[:1]:        # one representative element is enough
            _collect_keys(item, out)


def test_manifest_keys_documented():
    text = _formats_text()
    with open(os.path.join(DEMO_ROOT, "session_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    keys = set()
    _collect_keys(manifest, keys)
    keys = {k for k in keys if k not in _SKIP_KEYS and not _DYNAMIC_KEY.match(k)}
    # sanity: we actually found the interesting nested keys
    for expected in ("capture_clock", "t0_unix", "source_starts", "first_t_session",
                     "config", "baseline_sec", "phases", "planned_duration"):
        check(f"manifest sample key present in fixture: {expected}", expected in keys)
    _assert_all_documented(text, keys, "manifest key")


def _csv_header(path):
    with open(path, encoding="utf-8") as f:
        return next(csv.reader(f))


def test_phase_markers_documented():
    text = _formats_text()
    p = os.path.join(DEMO_ROOT, "phase_markers.csv")
    cols = _csv_header(p)
    _assert_all_documented(text, cols, "phase_markers.csv column")
    with open(p, encoding="utf-8") as f:
        labels = {row["label"] for row in csv.DictReader(f)}
    # every marker label the fixture produced (2 rounds → BASELINE/ROUND/BREAK/END)
    _assert_all_documented(text, labels, "phase-marker label")


def test_camera_sidecar_documented():
    text = _formats_text()
    side = None
    for sub in ("Side Cams", "POV Cams"):
        d = os.path.join(DEMO_ROOT, "continuous", sub)
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.endswith(".timestamps.csv"):
                    side = os.path.join(d, fn); break
        if side:
            break
    check("a camera sidecar exists in the fixture", side is not None)
    if side:
        _assert_all_documented(text, _csv_header(side), "camera sidecar column")


def test_eeg_csv_documented():
    text = _formats_text()
    muse = os.path.join(DEMO_ROOT, "continuous", "Muse data")
    eeg = next((os.path.join(muse, fn) for fn in sorted(os.listdir(muse))
                if fn.endswith(".csv")), None)
    check("an EEG CSV exists in the fixture", eeg is not None)
    if eeg:
        _assert_all_documented(text, _csv_header(eeg), "EEG CSV column")
    # plain-language sensor names
    import brain_battle.capture.contact_quality as contact_quality
    missing = [v for v in contact_quality.SENSOR_NAME.values()
               if not _documented_plain(text, v)]
    check("every plain-language sensor name is documented", not missing, str(missing))


def test_contact_log_documented():
    text = _formats_text()
    _assert_all_documented(text, contact_log._HEADER, "contact_quality.csv column")
    _assert_all_documented(text, ["good", "flat", "clipped", "noisy"],
                           "contact-quality status value")


def test_labels_json_documented():
    text = _formats_text()
    sample = labels_store.new_label("Jab", "A", labels_store.POINT, 1.0)
    _assert_all_documented(text, sample.keys(), "labels.json label field")
    _assert_all_documented(text, ["schema_version", "time_axis", "labels"],
                           "labels.json top-level key")
    _assert_all_documented(text, labels_store.WHO_VALUES, "labels 'who' value")
    _assert_all_documented(text, [labels_store.POINT, labels_store.DURATION],
                           "labels 'kind' value")


def test_label_list_documented():
    text = _formats_text()
    _assert_all_documented(text, label_vocab.DEFAULT_LABELS[0].keys(),
                           "label_list.json field")


def test_history_events_documented():
    text = _formats_text()
    events = [getattr(session_history, n) for n in dir(session_history)
              if n.startswith("EVENT_")]
    _assert_all_documented(text, events, "history.jsonl event type")
    _assert_all_documented(text, ["time", "user", "user_source", "app_version",
                                  "event", "details"], "history.jsonl line field")
    # the ad-hoc banner-dismissal event is documented too
    check("interrupted_dismissed documented", "interrupted_dismissed" in text)


def test_export_entries_documented():
    text = _formats_text()
    entries = set()
    for n in ZIP_NAMES:
        entries.add(n.split("/")[0] + "/" if "/" in n else n)
    _assert_all_documented(text, entries, "export-zip entry")
    # the derived CSV columns
    _assert_all_documented(text, export_module._LABELS_V2_COLUMNS, "labels.csv column")
    _assert_all_documented(text, ["t_start", "t_end", "source", "who", "label"],
                           "events.csv column")
    coreg = _csv_header(os.path.join(EXPORT_DIR, "eeg_coregistered.csv"))
    _assert_all_documented(text, coreg, "eeg_coregistered.csv column")


# ── 2. Python examples run green against a real export ──────────────────────

def _code_blocks(md_path, lang):
    with open(md_path, encoding="utf-8") as f:
        text = f.read()
    return re.findall(rf"```{lang}\n(.*?)```", text, re.DOTALL)


def test_python_examples_run():
    blocks = _code_blocks(LOADING_MD, "python")
    check("loading-the-data.md has Python examples", len(blocks) >= 3, str(len(blocks)))
    script = "\n".join(blocks).replace("/path/to/your/unzipped/export", EXPORT_DIR)
    ns = {}
    try:
        exec(compile(script, "<loading-the-data.md python>", "exec"), ns)
        ok, detail = True, ""
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    check("every Python example runs against a demo export", ok, detail)
    # the examples actually produced the joined table
    check("the join example produced rows",
          ok and "joined" in ns and len(ns["joined"]) >= 1,
          str(len(ns.get("joined", []))) if ok else "")


def test_r_examples():
    blocks = _code_blocks(LOADING_MD, "r")
    check("loading-the-data.md has R examples", len(blocks) >= 1, str(len(blocks)))
    rscript = shutil.which("Rscript")
    if not rscript:
        print("INFO  R examples: Rscript not available — not automatically tested")
        return
    probe = subprocess.run([rscript, "-e", "library(readr); library(dplyr)"],
                           capture_output=True)
    if probe.returncode != 0:
        print("INFO  R examples: readr/dplyr not installed — not automatically tested")
        return
    script = "\n".join(blocks).replace("/path/to/your/unzipped/export", EXPORT_DIR)
    res = subprocess.run([rscript, "-e", script], capture_output=True, text=True)
    check("every R example runs against a demo export", res.returncode == 0,
          res.stderr[-400:])


if __name__ == "__main__":
    from tools.generate_demo_session import generate_demo_session
    tmp = tempfile.mkdtemp(prefix="bb_rd_root_")
    DEMO_ROOT = generate_demo_session(out=tmp, rounds=2, round_sec=10,
                                      break_sec=4, baseline_sec=4, seed=5,
                                      boxer_a="Ivy", boxer_b="Zoe")["root"]
    # a real export with a couple of labels so events/joins have data
    labels = [
        labels_store.new_label("Jab", "A", labels_store.POINT, 6.0, created_by="t"),
        labels_store.new_label("Clinch", "Both", labels_store.DURATION, 8.0,
                               t_end=10.0, created_by="t"),
    ]
    phases = read_phase_markers(DEMO_ROOT)
    zip_path = os.path.join(tempfile.mkdtemp(prefix="bb_rd_zip_"), "out.zip")
    export_module.SessionExportWorker(DEMO_ROOT, labels, phases, zip_path,
                                      merge_video=False).run()
    with zipfile.ZipFile(zip_path) as zf:
        ZIP_NAMES = zf.namelist()
        EXPORT_DIR = tempfile.mkdtemp(prefix="bb_rd_unzip_")
        zf.extractall(EXPORT_DIR)

    test_manifest_keys_documented()
    test_phase_markers_documented()
    test_camera_sidecar_documented()
    test_eeg_csv_documented()
    test_contact_log_documented()
    test_labels_json_documented()
    test_label_list_documented()
    test_history_events_documented()
    test_export_entries_documented()
    test_python_examples_run()
    test_r_examples()

    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
