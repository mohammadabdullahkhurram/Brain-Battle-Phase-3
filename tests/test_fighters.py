"""
Fighter codes and colours (data/fighters.py, ADR-0023).

  • codes: exactly 4 characters A–Z / 0–9, upper-cased while typing, the two
    must differ; "New code" makes a valid one that avoids the other code.
  • colours: Red / Blue / Black presets always present; added colours are
    shared through fighter_colours.json in the recordings folder; names and
    hex values validated; presets cannot be removed; two fighters' colours
    must differ.
  • the manifest stores code + colour (never a name) and the folder is named
    "YYYY-MM-DD HH-MM K7Q2 vs M3XA".
  • an OLD recording whose manifest has names still opens: display() shows the
    name, session_info() lists it, recording.xdf keeps the name.

Run:  python3 tests/test_fighters.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401
import datetime as _dt
import json
import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import brain_battle.data.fighters as fighters
import brain_battle.data.session_storage as ss
from brain_battle.capture.session_controller import SessionConfig

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def test_codes():
    check("code: K7Q2 is valid", fighters.code_ok("K7Q2"))
    for bad in ("k7q2", "K7Q", "K7Q22", "K-Q2", "", None, "ÄBCD"):
        check(f"code: {bad!r} is rejected", not fighters.code_ok(bad))
    check("code: typing is upper-cased, cleaned and capped at 4",
          fighters.normalise_code(" k7-q2x ") == "K7Q2", fighters.normalise_code(" k7-q2x "))
    rng = random.Random(3)
    codes = [fighters.new_code(rng=rng) for _ in range(200)]
    check("New code: always 4 valid characters", all(fighters.code_ok(c) for c in codes))
    check("New code: avoids the other fighter's code",
          all(fighters.new_code(avoid=[c], rng=random.Random(i)) != c
              for i, c in enumerate(codes[:50])))
    check("codes: two different valid codes are fine", fighters.codes_problem("K7Q2", "M3XA") is None)
    check("codes: identical codes refused",
          fighters.codes_problem("K7Q2", "K7Q2") == "The two codes must be different")
    check("codes: a short code is named", "Boxer B" in (fighters.codes_problem("K7Q2", "M3") or ""))


def test_colours():
    d = tempfile.mkdtemp(prefix="bb_colours_")
    cols = fighters.load_colours(d)
    check("colours: presets Red/Blue/Black when no file",
          [c["name"] for c in cols] == ["Red", "Blue", "Black"]
          and [c["color"] for c in cols] == ["#D3241B", "#1B45C4", "#111111"], str(cols))
    cols, err = fighters.add_colour(cols, "green", "#0F7A62")
    check("colours: add Green (name capitalised)", err is None and cols[-1]["name"] == "Green", err)
    _, err = fighters.add_colour(cols, "GREEN", "#000000")
    check("colours: duplicate name refused", err == "That colour name already exists", err)
    _, err = fighters.add_colour(cols, "Teal", "teal")
    check("colours: bad hex refused", err == "Pick a colour", err)
    fighters.save_colours(d, cols)
    check("colours: saved to fighter_colours.json in the recordings folder",
          os.path.isfile(os.path.join(d, "fighter_colours.json")))
    again = fighters.load_colours(d)
    check("colours: shared list reloads with the added colour",
          [c["name"] for c in again] == ["Red", "Blue", "Black", "Green"], str(again))
    check("colours: presets cannot be removed",
          [c["name"] for c in fighters.remove_colour(again, "Red")] == [c["name"] for c in again])
    check("colours: an added colour can be removed",
          "Green" not in [c["name"] for c in fighters.remove_colour(again, "Green")])
    # a hand-edited file with the presets missing still gets them back
    with open(os.path.join(d, "fighter_colours.json"), "w") as f:
        json.dump({"colours": [{"name": "Gold", "color": "#C9A227"}]}, f)
    check("colours: presets always present even if the file lacks them",
          [c["name"] for c in fighters.load_colours(d)] == ["Red", "Blue", "Black", "Gold"])
    check("colours: the two fighters' colours must differ",
          fighters.colours_problem("Red", "red") is not None
          and fighters.colours_problem("Red", "Blue") is None)


def test_manifest_and_folder():
    base = tempfile.mkdtemp(prefix="bb_codes_")
    d = _dt.datetime(2026, 11, 17, 14, 5)
    a = fighters.boxer_entry("K7Q2", "Red", "#D3241B", "MuseS-7538")
    b = fighters.boxer_entry("M3XA", "Blue", "#1B45C4", "MuseS-7564")
    lay = ss.create_session(base, SessionConfig(), started_at=d, boxer_a=a, boxer_b=b)
    check("folder: named 'YYYY-MM-DD HH-MM K7Q2 vs M3XA'",
          os.path.basename(str(lay.root)) == "2026-11-17 14-05 K7Q2 vs M3XA",
          os.path.basename(str(lay.root)))
    man = json.load(open(lay.manifest_path(), encoding="utf-8"))
    check("manifest: code, colour, colour_hex, headband per fighter",
          man["boxers"]["A"] == {"code": "K7Q2", "colour": "Red", "colour_hex": "#D3241B",
                                 "headband": "MuseS-7538"}, str(man["boxers"]["A"]))
    check("manifest: never a name", "name" not in json.dumps(man["boxers"]))
    info = ss.session_info(lay.root)
    check("Sessions: shows the codes and colours",
          (info.boxer_a, info.boxer_b, info.colour_a, info.colour_b)
          == ("K7Q2", "M3XA", "#D3241B", "#1B45C4"))
    check("display: code", fighters.versus(man) == "K7Q2 vs M3XA")


def test_old_recording_with_names():
    base = tempfile.mkdtemp(prefix="bb_legacy_")
    d = _dt.datetime(2026, 10, 29, 16, 20)
    lay = ss.create_session(base, SessionConfig(), started_at=d,
                            boxer_a={"name": "Sam", "headband": "MuseS-7538"},
                            boxer_b={"name": "Alex", "headband": "MuseS-7564"})
    check("old recording: folder name keeps the names",
          os.path.basename(str(lay.root)) == "2026-10-29 16-20 Sam vs Alex")
    info = ss.session_info(lay.root)
    check("old recording: still listed, with its names",
          info.readable and (info.boxer_a, info.boxer_b) == ("Sam", "Alex"))
    man = ss._read_manifest(lay.root)
    check("old recording: display() shows the stored names",
          fighters.versus(man) == "Sam vs Alex")
    check("no codes and no names: 'Boxer A' / 'Boxer B'",
          fighters.versus({}) == "Boxer A vs Boxer B")


def test_old_recording_xdf_keeps_names():
    import subprocess
    out = tempfile.mkdtemp(prefix="bb_legacy_xdf_")
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import generate_demo_session as gen
    r = gen.generate_demo_session(out=out, rounds=1, round_sec=5, break_sec=0,
                                  baseline_sec=2, legacy_names=("Sam", "Alex"))
    import pyxdf
    streams, _ = pyxdf.load_xdf(r["xdf"])
    desc = streams[0]["info"]["desc"][0]["brain_battle"][0]
    check("old recording: recording.xdf builds and keeps the names",
          desc.get("boxer_a_name") == ["Sam"] and desc.get("boxer_b_name") == ["Alex"],
          str({k: v for k, v in desc.items() if k.startswith("boxer")}))
    r2 = gen.generate_demo_session(out=out, rounds=1, round_sec=5, break_sec=0, baseline_sec=2)
    streams, _ = pyxdf.load_xdf(r2["xdf"])
    eeg = next(s for s in streams if s["info"]["name"][0] == "BrainBattle_EEG_A")
    d2 = eeg["info"]["desc"][0]["brain_battle"][0]
    check("new recording: XDF carries codes + colours, no names",
          d2.get("boxer_a_code") == ["K7Q2"] and d2.get("boxer_b_code") == ["M3XA"]
          and d2.get("boxer_code") == ["K7Q2"] and d2.get("boxer_colour") == ["Red"]
          and "boxer_a_name" not in d2, str({k: v for k, v in d2.items() if k.startswith("boxer")}))


if __name__ == "__main__":
    test_codes()
    test_colours()
    test_manifest_and_folder()
    test_old_recording_with_names()
    test_old_recording_xdf_keeps_names()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
