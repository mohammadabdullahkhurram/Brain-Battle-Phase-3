"""
The round bell: a real boxing-bell recording (assets/sounds/round_bell.wav,
CC-BY, see docs/project/credits.md) replacing the old synthesized 1 kHz beep.

Covers:
  1. round_bell.wav exists with the expected duration and true peak (peak-
     normalised to -1 dBTP — full volume, no clipping — as produced by
     tools/make_round_bell.py; the owner turns it down with the laptop's
     volume if needed, rather than the app being quiet by default).
  2. SessionController's bell_requested fires at the START and END of every
     round (including the last), never for the lead-in, baseline or a break,
     and not when a round is stopped early.
  3. SettingsScreen's Test sound button plays the bundled file, headless.
  4. A missing WAV logs a warning and stays silent — RoundBell never raises.
  5. The credits entry for the bell (and the bundled Inter font) exists in
     docs/project/credits.md and nav.yml.

Run:  python3 tests/test_round_bell.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401  (temp settings file + a labeller name; see tests/_env.py)
import json
import os
import re
import subprocess
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

BELL_WAV = os.path.join(ROOT, "assets", "sounds", "round_bell.wav")

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _ffmpeg_loudnorm_measure(path):
    # loudnorm's measurement pass used purely as a true-peak/loudness meter
    # (no second pass applying its own gain) — matches tools/make_round_bell.py.
    r = subprocess.run(
        ["ffmpeg", "-i", path, "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\}", r.stderr, flags=re.DOTALL)
    return json.loads(m.group(0)) if m else {}


def _sample_peak_dbfs(path):
    r = subprocess.run(
        ["ffmpeg", "-i", path, "-af", "astats", "-f", "null", "-"],
        capture_output=True, text=True)
    hits = re.findall(r"Peak level dB:\s*(-?[\d.]+)", r.stderr)
    return float(hits[-1]) if hits else float("nan")


def _ffprobe_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", path],
        capture_output=True, text=True)
    return float(r.stdout.strip())


# ─── 1. round_bell.wav: duration, peak, loudness ───────────────────────────

def test_round_bell_wav_specs():
    check("round_bell.wav exists", os.path.isfile(BELL_WAV), BELL_WAV)
    if not os.path.isfile(BELL_WAV):
        return

    duration = _ffprobe_duration(BELL_WAV)
    check("duration is within the 1.5-2.5s natural-ring target",
          1.5 <= duration <= 2.5, f"duration={duration:.3f}s")

    measured = _ffmpeg_loudnorm_measure(BELL_WAV)
    tp = float(measured.get("input_tp", "nan"))
    check("true peak is peak-normalised to ~-1 dBTP (full volume)",
          -1.5 <= tp <= -0.5, f"measured={tp} dBTP")

    peak = _sample_peak_dbfs(BELL_WAV)
    check("no clipping: sample peak stays below 0 dBFS",
          peak < 0.0, f"sample peak={peak} dBFS")


# ─── 2. the bell rings at baseline + each round start, never on a break ────

def test_bell_timing():
    """The bell rings at the START and at the END of every round — including
    the last one, whose end also finishes the recording — and never for the
    lead-in, the baseline or a break."""
    from brain_battle.capture.session_controller import SessionController, SessionConfig, Phase

    sc = SessionController()
    sc.configure(SessionConfig(num_rounds=3, round_sec=5, break_sec=5, baseline_sec=5))

    events = []          # ("bell", marker-so-far) and ("marker", text) in order
    sc.bell_requested.connect(lambda: events.append(("bell", None)))
    sc.marker.connect(lambda text, pid, rn: events.append(("marker", text)))
    finished = []
    sc.session_finished.connect(lambda: finished.append(True))

    sc.start()
    # Walk the timeline directly (pure state-machine logic — no need to wait
    # out a real 1 Hz timer for a timing-only test). The last _advance() runs
    # off the end of the timeline and finishes the recording.
    for _ in range(len(sc.phase_plan())):
        sc._advance()

    # each bell immediately follows the marker that caused it
    rung_after = [events[i - 1][1] for i, e in enumerate(events) if e[0] == "bell"]
    check("bell rings at the start and end of every round, in order",
          rung_after == ["ROUND_1_START", "ROUND_1_END", "ROUND_2_START", "ROUND_2_END",
                         "ROUND_3_START", "ROUND_3_END"], str(rung_after))
    check("the last round's end rings too (before RECORDING_END)",
          events[-1] == ("marker", "RECORDING_END") and events[-2] == ("bell", None)
          and events[-3] == ("marker", "ROUND_3_END"), str(events[-3:]))
    check("no bell for the lead-in, the baseline or a break",
          not any(m and (m.startswith("BASELINE") or m.startswith("BREAK")
                         or m == "RECORDING_START") for m in rung_after), str(rung_after))
    check("the recording finished after the last round", finished == [True])


def test_bell_not_on_early_stop():
    """Stopping early writes RECORDING_END but no _END for the unfinished round
    and does not ring the bell."""
    from brain_battle.capture.session_controller import SessionController, SessionConfig
    sc = SessionController()
    sc.configure(SessionConfig(num_rounds=2, round_sec=5, break_sec=5, baseline_sec=5))
    events = []
    sc.bell_requested.connect(lambda: events.append("bell"))
    sc.marker.connect(lambda text, pid, rn: events.append(text))
    sc.start(); sc._advance(); sc._advance()       # lead-in → baseline → round 1
    events.clear()
    sc.stop()
    check("early stop: only RECORDING_END, no ROUND_1_END, no bell",
          events == ["RECORDING_END"], str(events))


# ─── 3. Settings' Test sound plays the bundled file ────────────────────────

def test_settings_test_sound_plays_bundled_bell():
    os.environ["BRAIN_BATTLE_CONFIG"] = os.path.join(tempfile.mkdtemp(), "settings.json")
    os.environ.pop("BRAIN_BATTLE_CAPTURE_DIR", None)
    import brain_battle.data.app_settings as aps
    s = aps.load(); s.capture_dir = tempfile.mkdtemp(prefix="bb_round_bell_cap_"); s.save()

    import brain_battle.capture.round_bell as round_bell
    from brain_battle.ui.settings_screen import SettingsScreen

    st = SettingsScreen()
    try:
        st._on_test_sound()
        ok = True
    except Exception as e:
        ok = False
        print("    test-sound raised:", e)
    check("Test sound doesn't crash headless", ok)
    check("Test sound points at the bundled round bell, not a synthesized tone",
          ok and st._beep.path() == round_bell.bundled_bell_path(),
          getattr(st, "_beep", None) and st._beep.path())


# ─── 4. a missing WAV never crashes RoundBell ───────────────────────────────

def test_missing_bell_file_stays_silent():
    import brain_battle.capture.round_bell as round_bell

    original = round_bell.bundled_bell_path
    round_bell.bundled_bell_path = lambda: "/nonexistent/round_bell.wav"
    try:
        b = round_bell.RoundBell()
        b.play()   # must not raise
        ok = True
    except Exception as e:
        ok = False
        print("    missing-file playback raised:", e)
    finally:
        round_bell.bundled_bell_path = original
    check("RoundBell with a missing WAV never crashes", ok)
    check("RoundBell falls back to no sound effect when the file is missing",
          ok and b._sfx is None)


# ─── 5. the credits entry exists ───────────────────────────────────────────

def test_credits_page_exists():
    path = os.path.join(ROOT, "docs", "project", "credits.md")
    check("docs/project/credits.md exists", os.path.isfile(path), path)
    if not os.path.isfile(path):
        return
    text = open(path, encoding="utf-8").read()
    for needle in ("Benboncan", "CC-BY", "66951", "round_bell.wav",
                   "Inter", "SIL OFL"):
        check(f"credits.md mentions {needle!r}", needle in text)

    nav_text = open(os.path.join(ROOT, "docs", "nav.yml"), encoding="utf-8").read()
    check("credits page is listed in nav.yml", "project/credits.md" in nav_text)


if __name__ == "__main__":
    test_round_bell_wav_specs()
    test_bell_timing()
    test_bell_not_on_early_stop()
    test_settings_test_sound_plays_bundled_bell()
    test_missing_bell_file_stays_silent()
    test_credits_page_exists()

    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
