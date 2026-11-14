"""
Boxer names: kept by default, hidden on request.

  1. recording.xdf carries the boxer names by default (so the team can keep
     track), and none at all when built with hide_names.
  2. An export with "Replace boxer names with Boxer A / Boxer B" has no name
     anywhere in it — not in the XDF, the manifest copy, the history copy, the
     README, any CSV, any file name inside the zip, or the zip's own suggested
     name — while a normal export keeps them. The recording on disk is unchanged.
  3. The Settings default decides what the export window starts with.
  4. "Hide names on screen" removes names from Sessions and Review & Label.

Run:  python3 tests/test_privacy.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401

import json
import os
import sys
import tempfile
import zipfile

import pyxdf
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

import brain_battle.data.app_settings as app_settings
import brain_battle.data.labels_store as ls
import brain_battle.data.privacy as privacy
import brain_battle.data.session_history as sh
import brain_battle.data.session_storage as ss
import brain_battle.data.session_xdf as session_xdf

_app = QApplication.instance() or QApplication(sys.argv)
_failures = []
DEMO_ROOT = None
NAME_A, NAME_B = "Ivy Okonkwo", "Zoe"


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _has_name(blob) -> bool:
    text = blob if isinstance(blob, str) else blob.decode("utf-8", "ignore")
    low = text.lower()
    return any(n.lower() in low for n in ("Ivy", "Okonkwo", "Zoe"))


def _desc(stream):
    return stream["info"]["desc"][0]["brain_battle"][0]


def test_helpers():
    check("shown_name", privacy.shown_name("A", "Sam") == "Sam"
          and privacy.shown_name("A", "Sam", hide=True) == "Boxer A"
          and privacy.shown_name("B", None) == "Boxer B")
    check("versus", privacy.versus("Sam", "Alex") == "Sam vs Alex"
          and privacy.versus("Sam", "Alex", True) == "Boxer A vs Boxer B")
    check("neutral_recording_name drops the names, keeps date/time and a (2) suffix",
          privacy.neutral_recording_name("2026-10-27 14-05 Sam vs Alex") == "2026-10-27 14-05"
          and privacy.neutral_recording_name("2026-10-27 14-05 Sam vs Alex (2)") == "2026-10-27 14-05 (2)"
          and privacy.neutral_recording_name("odd folder") == "recording")
    check("scrub_text replaces whole names only, any capitalisation, longest first",
          privacy.scrub_text("Sam Lee beat SAM; Samantha watched.", {"A": "Sam Lee", "B": "Sam"})
          == "Boxer A beat Boxer B; Samantha watched.")
    m = privacy.scrub_manifest({"boxers": {"A": {"name": "Sam", "headband": "MuseS-1"},
                                           "B": {"name": None, "headband": "MuseS-2"}}})
    check("scrub_manifest replaces names, keeps headbands, flags names_hidden",
          m["boxers"]["A"] == {"name": "Boxer A", "headband": "MuseS-1"}
          and m["boxers"]["B"]["name"] == "Boxer B" and m["names_hidden"] is True)


def test_xdf_names():
    out = os.path.join(tempfile.mkdtemp(prefix="bb_priv_xdf_"), "with.xdf")
    session_xdf.build_session_xdf(DEMO_ROOT, out_path=out)
    streams, _ = pyxdf.load_xdf(out, dejitter_timestamps=False)
    by = {s["info"]["name"][0]: s for s in streams}
    eeg_a = _desc(by["BrainBattle_EEG_A"])
    check("XDF keeps the boxer names by default",
          eeg_a["boxer_a_name"][0] == NAME_A and eeg_a["boxer_b_name"][0] == NAME_B
          and eeg_a["boxer_name"][0] == NAME_A
          and _desc(by["BrainBattle_EEG_B"])["boxer_name"][0] == NAME_B)
    check("…and the recording's own name", _has_name(eeg_a["recording"][0]))

    hidden = os.path.join(os.path.dirname(out), "without.xdf")
    session_xdf.build_session_xdf(DEMO_ROOT, out_path=hidden, hide_names=True)
    check("with hide_names the file contains no boxer name at all (raw bytes)",
          not _has_name(open(hidden, "rb").read()))
    streams, _ = pyxdf.load_xdf(hidden, dejitter_timestamps=False)
    d = _desc(next(s for s in streams if s["info"]["name"][0] == "BrainBattle_EEG_A"))
    check("…but still says which boxer and which headband",
          d["boxer"][0] == "A" and bool(d["headband"][0]) and "boxer_name" not in d)


def _export(hide):
    import brain_battle.data.export_module as em
    zip_path = os.path.join(tempfile.mkdtemp(prefix="bb_priv_zip_"),
                            em.default_export_name(DEMO_ROOT, hide_names=hide))
    em.SessionExportWorker(DEMO_ROOT, None, None, zip_path, hide_names=hide).run()
    return zip_path


def test_export_hides_names():
    manifest_before = open(os.path.join(DEMO_ROOT, "session_manifest.json"), "rb").read()
    xdf_path = os.path.join(DEMO_ROOT, "recording.xdf")
    session_xdf.build_session_xdf(DEMO_ROOT)
    xdf_before = open(xdf_path, "rb").read()
    sh.log_event(DEMO_ROOT, "note", {"text": f"{NAME_A} looked tired; zoe did not"})

    normal = _export(False)
    with zipfile.ZipFile(normal) as zf:
        check("a normal export keeps the names (manifest, README, XDF)",
              _has_name(zf.read("session_manifest.json")) and _has_name(zf.read("README.txt"))
              and _has_name(zf.read("recording.xdf")) and _has_name(os.path.basename(normal)))

    hidden = _export(True)
    check("the suggested file name of a name-free export has no names",
          not _has_name(os.path.basename(hidden)), os.path.basename(hidden))
    with zipfile.ZipFile(hidden) as zf:
        names = zf.namelist()
        leaks = [n for n in names if not n.endswith(".mp4") and _has_name(zf.read(n))]
        check("NO file in a name-free export contains a boxer name", leaks == [], str(leaks))
        check("no file NAME inside it does either", not any(_has_name(n) for n in names))
        m = json.loads(zf.read("session_manifest.json"))
        check("its manifest copy says Boxer A / Boxer B and names_hidden",
              m["boxers"]["A"]["name"] == "Boxer A" and m["boxers"]["B"]["name"] == "Boxer B"
              and m["names_hidden"] is True)
        readme = zf.read("README.txt").decode("utf-8")
        check("its README says the names were replaced, and that videos still show the boxers",
              "replaced with Boxer A / Boxer B" in readme and "videos still show" in readme)
        check("labellers' names are kept (they are not the boxers)",
              "Maya K" in zf.read("labels.csv").decode("utf-8"))
        check("the history copy is scrubbed too",
              "Boxer A looked tired; Boxer B did not" in zf.read("history.jsonl").decode("utf-8"))
    check("the recording on disk is unchanged by a name-free export",
          open(os.path.join(DEMO_ROOT, "session_manifest.json"), "rb").read() == manifest_before
          and _has_name(open(xdf_path, "rb").read()) and len(open(xdf_path, "rb").read()) > 0
          and xdf_before[:64] == open(xdf_path, "rb").read()[:64])
    ev = [e for e in sh.read_history(DEMO_ROOT) if e["event"] == "export_created"]
    check("history records whether names were hidden and whose labels went out",
          [e["details"].get("names_hidden") for e in ev[-2:]] == [False, True]
          and ev[-1]["details"].get("labellers") == ["Maya K"], str(ev[-2:]))


def test_dialog_default_follows_settings():
    import brain_battle.data.export_module as em
    s = app_settings.load(); s.hide_names_in_exports = False; s.save()
    d = em.ExportOptionsDialog(DEMO_ROOT)
    check("export window: names kept unless the Settings default says otherwise",
          not d._hide_cb.isChecked() and _has_name(os.path.basename(d._dest)))
    d._hide_cb.setChecked(True)
    check("ticking the box changes the suggested file name at once",
          not _has_name(os.path.basename(d._dest)), d._dest)
    s.hide_names_in_exports = True; s.save()
    d2 = em.ExportOptionsDialog(DEMO_ROOT)
    check("Settings default 'replace names' pre-ticks the box",
          d2._hide_cb.isChecked() and not _has_name(os.path.basename(d2._dest)))
    s.hide_names_in_exports = False; s.save()


def _screen_text(widget) -> str:
    bits = [w.text() for w in widget.findChildren(QLabel)] + \
           [w.text() for w in widget.findChildren(QPushButton)]
    return " | ".join(bits)


def test_hide_on_screen():
    import brain_battle.ui.sessions_screen as st
    import brain_battle.ui.review_label_screen as rl
    page = st.SessionsScreen(os.path.dirname(DEMO_ROOT))
    page._on_scanned(ss.list_sessions(os.path.dirname(DEMO_ROOT)))
    check("Sessions shows names normally", _has_name(_screen_text(page)))
    page.set_names_hidden(True)
    txt = _screen_text(page)
    check("Sessions with names hidden shows Boxer A / Boxer B instead",
          not _has_name(txt) and "A · Boxer A" in txt and "B · Boxer B" in txt, txt[:200])
    page.shutdown()

    tab = rl.ReviewLabelScreen()
    try:
        tab.load_session_folder(DEMO_ROOT); tab.wait_loaded()
        check("Review & Label shows names normally", _has_name(_screen_text(tab)))
        tab.set_names_hidden(True)
        txt = _screen_text(tab)
        check("Review & Label with names hidden shows none",
              not _has_name(txt) and "Boxer A vs Boxer B" in tab._title.text(), tab._title.text())
        tab.set_names_hidden(False)
        check("…and they come back", _has_name(tab._title.text()))
    finally:
        tab.shutdown()


if __name__ == "__main__":
    from tools.generate_demo_session import generate_demo_session
    _env.set_labeller("Maya K")
    DEMO_ROOT = generate_demo_session(out=tempfile.mkdtemp(prefix="bb_privacy_"), rounds=1,
                                      round_sec=6, break_sec=2, baseline_sec=2, seed=5,
                                      boxer_a=NAME_A, boxer_b=NAME_B)["root"]
    store = ls.open_for_labeller(DEMO_ROOT, "Maya K")
    store.add(ls.new_label("Jab", "A", ls.POINT, 3.0, created_by="Maya K")); store.save()
    test_helpers()
    test_xdf_names()
    test_export_hides_names()
    test_dialog_default_follows_settings()
    test_hide_on_screen()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
