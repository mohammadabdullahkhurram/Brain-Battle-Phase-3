"""
label_vocab tests: the shared team label list stored in the recordings folder.

Run:  python3 tests/test_label_vocab.py
"""
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
    check("DESIGN names present (Jab/Clinch/Referee stop)",
          "Jab" in names and "Clinch" in names and "Referee stop" in names)
    kinds = {x["name"]: x["kind"] for x in labels}
    check("Clinch + Referee stop are duration ('lasts a while')",
          kinds["Clinch"] == lv.DURATION and kinds["Referee stop"] == lv.DURATION)
    check("Jab is a point ('moment')", kinds["Jab"] == lv.POINT)

    # editing persists and round-trips
    labels.append({"key": 9, "name": "Slip", "color": "#123456", "kind": lv.POINT})
    lv.save(d, labels)
    again = lv.load(d)
    check("saved custom label round-trips", any(x["name"] == "Slip" for x in again))

    # stored under schema_version wrapper
    raw = json.loads(open(lv.path(d), encoding="utf-8").read())
    check("file has schema_version + labels", raw.get("schema_version") == 1
          and isinstance(raw.get("labels"), list))


def test_unreadable_falls_back():
    d = tempfile.mkdtemp(prefix="bb_vocab_bad_")
    with open(lv.path(d), "w") as f:
        f.write("{ not valid json")
    labels = lv.load(d, seed_if_missing=False)
    check("unreadable list falls back to defaults", len(labels) == 8)


if __name__ == "__main__":
    test_seed_and_roundtrip()
    test_unreadable_falls_back()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
