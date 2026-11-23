"""
label_vocab tests: the shared team label list stored in the recordings folder.

Run:  python3 tests/test_label_vocab.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401  (temp settings file + a labeller name; see tests/_env.py)
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import brain_battle.data.label_vocab as lv

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def test_seed_and_roundtrip():
    d = tempfile.mkdtemp(prefix="bb_vocab_")
    # first load seeds the file from DESIGN defaults
    labels = lv.load(d)
    check("seeds 8 default labels", len(labels) == 8, str(len(labels)))
    check("keys are 1..8", [x["key"] for x in labels] == list(range(1, 9)))
    check("seed wrote label_list.json", os.path.isfile(lv.path(d)))
    names = [x["name"] for x in labels]
    check("the professor's default list, keys 1-8, in order",
          names == ["Punch initiated", "Punch landed", "Punch missed", "Punch blocked",
                    "Punch dodged", "Clinch", "Knockdown", "Referee break"], str(names))
    kinds = {x["name"]: x["kind"] for x in labels}
    check("Clinch + Referee break are duration ('lasts a while')",
          kinds["Clinch"] == lv.DURATION and kinds["Referee break"] == lv.DURATION)
    check("Punch landed is a point ('moment')", kinds["Punch landed"] == lv.POINT)

    # editing persists and round-trips
    labels.append({"key": 9, "name": "Slip", "color": "#123456", "kind": lv.POINT})
    lv.save(d, labels)
    again = lv.load(d)
    check("saved custom label round-trips", any(x["name"] == "Slip" for x in again))

    # stored under schema_version wrapper
    raw = json.loads(open(lv.path(d), encoding="utf-8").read())
    check("file has schema_version + labels", raw.get("schema_version") == 1
          and isinstance(raw.get("labels"), list))


def test_existing_team_list_not_overwritten():
    d = tempfile.mkdtemp(prefix="bb_vocab_team_")
    old = [{"key": 1, "name": "Jab", "color": "#3D4FD6", "kind": lv.POINT},
           {"key": 2, "name": "Referee stop", "color": "#15171C", "kind": lv.DURATION}]
    lv.save(d, old)
    got = lv.load(d)
    check("an existing team list is kept as it is (not replaced by the new defaults)",
          [x["name"] for x in got] == ["Jab", "Referee stop"], str(got))


def test_old_default_replaced_once():
    """A list that is still exactly the OLD default (Jab … Referee stop) was
    never edited: it becomes the new default once, and the file's history
    says so. Afterwards (even if someone sets the old list again) it is kept."""
    d = tempfile.mkdtemp(prefix="bb_vocab_old_")
    old_names = ["Jab", "Cross", "Hook", "Uppercut", "Block", "Clinch", "Knockdown", "Referee stop"]
    lv.save(d, [{"key": i + 1, "name": n, "color": "#3D4FD6",
                 "kind": lv.DURATION if n in ("Clinch", "Referee stop") else lv.POINT}
                for i, n in enumerate(old_names)])
    got = [x["name"] for x in lv.load(d)]
    check("the untouched old default is replaced by the new default",
          got == [x["name"] for x in lv.DEFAULT_LABELS], str(got))
    on_disk = [x["name"] for x in json.load(open(lv.path(d), encoding="utf-8"))["labels"]]
    check("…and saved", on_disk == got)
    h = lv.history(d)
    check("…and logged in the list's history (who, when, from, to)",
          len(h) == 1 and h[0]["event"] == lv.EVENT_DEFAULT_REPLACED
          and h[0]["details"]["from"] == old_names and h[0]["details"]["to"] == got
          and h[0].get("time") and h[0].get("user"), str(h))
    # once only: put the old list back on purpose → kept
    lv.save(d, [{"key": i + 1, "name": n, "color": "#3D4FD6", "kind": lv.POINT}
                for i, n in enumerate(old_names)])
    check("once only: the old list set again later is kept",
          [x["name"] for x in lv.load(d)] == old_names and len(lv.history(d)) == 1)
    # the short-lived 3.1-dev default (Punch thrown …) is an untouched default too
    d2 = tempfile.mkdtemp(prefix="bb_vocab_dev_")
    lv.save(d2, [{"key": i + 1, "name": n, "color": "#2155D6", "kind": lv.POINT}
                 for i, n in enumerate(lv.OLD_DEFAULTS[1])])
    check("the previous dev default (Punch thrown …) is replaced too",
          [x["name"] for x in lv.load(d2)][0] == "Punch initiated")
    # an edited old list (one label renamed) is the team's own: kept
    d3 = tempfile.mkdtemp(prefix="bb_vocab_edit_")
    edited = old_names[:7] + ["Ref stop"]
    lv.save(d3, [{"key": i + 1, "name": n, "color": "#3D4FD6", "kind": lv.POINT}
                 for i, n in enumerate(edited)])
    check("an edited list is kept", [x["name"] for x in lv.load(d3)] == edited
          and lv.history(d3) == [])


def test_custom_notes():
    import brain_battle.data.labels_store as ls
    import brain_battle.data.label_agreement as la
    n = ls.new_note("  A keeps   pressing forward ", "A", 236.0, created_by="Maya K")
    check("a custom note is a label of type 'note' with its text",
          n["type"] == "note" and n["label"] == "Note" and n["text"] == "A keeps pressing forward"
          and n["who"] == "A" and n["t_start"] == 236.0 and n["created_by"] == "Maya K")
    lab = ls.new_label("Clinch", "Both", ls.DURATION, 10.0, t_end=12.0)
    check("an ordinary label is type 'label' with no text", lab["type"] == "label" and lab["text"] == "")
    d = tempfile.mkdtemp(prefix="bb_vocab_notes_")
    labels = lv.load(d)
    check("a note never enters the label list", all(x["name"] != "Note" for x in labels))
    a = [ls.new_label("Punch landed", "A", ls.POINT, 5.0), ls.new_note("hello", "A", 9.0)]
    b = [ls.new_label("Punch landed", "A", ls.POINT, 5.2), ls.new_note("other words", "B", 30.0)]
    ag = la.pairwise({"Maya K": a, "Jonas R": b})[0]
    check("agreement ignores notes (1 of 1, 100%)", ag.total == 1 and ag.percent == 100,
          f"{ag.total} {ag.percent}")
    import brain_battle.data.session_xdf as sx
    ev = sx.label_events(a)
    check("XDF label events: a note is 'Note/<who>: <text>'",
          ev == [(5.0, "Punch landed/A"), (9.0, "Note/A: hello")], str(ev))
    import brain_battle.data.export_module as em, csv
    p = os.path.join(d, "labels.csv")
    em._write_labels_csv_v2([dict(x, labeller="Maya K") for x in a], p)
    rows = list(csv.DictReader(open(p, encoding="utf-8")))
    check("labels.csv has type + text columns; the note's text is there",
          rows[1]["type"] == "note" and rows[1]["text"] == "hello" and rows[0]["type"] == "label",
          str(rows))
    old = {k: v for k, v in a[0].items() if k not in ("type", "text")}
    em._write_labels_csv_v2([old], p)
    check("a label saved before notes existed is written as type 'label'",
          list(csv.DictReader(open(p, encoding="utf-8")))[0]["type"] == "label")


def test_unreadable_falls_back():
    d = tempfile.mkdtemp(prefix="bb_vocab_bad_")
    with open(lv.path(d), "w") as f:
        f.write("{ not valid json")
    labels = lv.load(d, seed_if_missing=False)
    check("unreadable list falls back to defaults", len(labels) == 8)


if __name__ == "__main__":
    test_seed_and_roundtrip()
    test_unreadable_falls_back()
    test_existing_team_list_not_overwritten()
    test_old_default_replaced_once()
    test_custom_notes()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
