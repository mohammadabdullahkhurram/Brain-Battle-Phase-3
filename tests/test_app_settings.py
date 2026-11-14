"""
App settings store (app_settings.py) + the BOXER_LABELS removal.

  - defaults match today's previously-hard-coded values (so behaviour is
    unchanged until the user edits anything),
  - load → edit → save → reload round-trips,
  - the headband mapping drives boxer_labels() (the former hard-coded dict),
  - eeg_module no longer exposes a module-level BOXER_LABELS dict, only the
    boxer_labels() function that derives from settings.

Run:  python3 tests/test_app_settings.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401  (temp settings file + a labeller name; see tests/_env.py)
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def test_defaults():
    import brain_battle.data.app_settings as app_settings
    os.environ["BRAIN_BATTLE_CONFIG"] = os.path.join(tempfile.mkdtemp(), "settings.json")
    s = app_settings.load()
    cfg = s.session_config()
    check("default plan is 3x180s, 60s breaks, 60s rest",
          (cfg.num_rounds, cfg.round_sec, cfg.break_sec, cfg.baseline_sec)
          == (3, 180, 60, 60), str(cfg))
    check("round bell defaults on", s.round_bell is True)
    check("video quality defaults to standard 960x540 30",
          s.video_dims() == (960, 540, 30), str(s.video_dims()))
    check("default headbands map A/B to the known Muse IDs",
          s.boxer_labels() == {"MuseS-7538": "BOXER A", "MuseS-7564": "BOXER B"},
          str(s.boxer_labels()))
    check("capture dir falls back to the default root",
          s.capture_dir_resolved().endswith("Brain Battle Data")
          and os.path.isabs(s.capture_dir_resolved()))


def test_round_trip():
    import brain_battle.data.app_settings as app_settings
    path = os.path.join(tempfile.mkdtemp(), "settings.json")
    os.environ["BRAIN_BATTLE_CONFIG"] = path
    s = app_settings.load()
    s.round_bell = False
    s.video_quality = "high"
    s.headbands = {"A": "MuseS-9000", "B": "MuseS-9001"}
    s.session_defaults = {"num_rounds": 5, "round_sec": 120, "break_sec": 30, "baseline_sec": 10}
    s.save()
    s2 = app_settings.load()
    check("round bell persists", s2.round_bell is False)
    check("video quality persists (high = 1280x720)", s2.video_dims() == (1280, 720, 30))
    check("headband remap persists into boxer_labels",
          s2.boxer_labels() == {"MuseS-9000": "BOXER A", "MuseS-9001": "BOXER B"},
          str(s2.boxer_labels()))
    cfg = s2.session_config()
    check("session defaults persist",
          (cfg.num_rounds, cfg.round_sec, cfg.break_sec, cfg.baseline_sec) == (5, 120, 30, 10),
          str(cfg))


def test_boxer_labels_removed_from_eeg_module():
    import brain_battle.capture.eeg_module as eeg_module
    check("eeg_module has NO module-level BOXER_LABELS dict",
          not isinstance(getattr(eeg_module, "BOXER_LABELS", None), dict))
    check("eeg_module.boxer_labels() exists and returns a dict",
          callable(getattr(eeg_module, "boxer_labels", None))
          and isinstance(eeg_module.boxer_labels(), dict))
    # derives from the current settings
    os.environ["BRAIN_BATTLE_CONFIG"] = os.path.join(tempfile.mkdtemp(), "settings.json")
    import brain_battle.data.app_settings as app_settings
    s = app_settings.load(); s.headbands = {"A": "MuseS-AAAA", "B": "MuseS-BBBB"}; s.save()
    check("eeg_module.boxer_labels() reflects settings",
          eeg_module.boxer_labels() == {"MuseS-AAAA": "BOXER A", "MuseS-BBBB": "BOXER B"},
          str(eeg_module.boxer_labels()))


if __name__ == "__main__":
    test_defaults()
    test_round_trip()
    test_boxer_labels_removed_from_eeg_module()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
