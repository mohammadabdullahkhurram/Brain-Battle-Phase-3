"""
Smoke suite — the "does it load and stand up" floor.

This is deliberately NOT a behavior suite (seek/confidence/ring-buffer tests
own correctness). It guards the blind spot unit tests structurally miss:
import-time and construct-time failures. The motivating bug: for four
commits, export_module's encoder probe raised NameError AT IMPORT, was
swallowed by a broad except, and silently downgraded every run to libx264 —
while all unit tests (which exercised the same functions post-import) stayed
green. Check 1b below is the assertion that would have caught it.

This suite is the acceptance gate for deletions/refactors (H, J/K): if it
passes, every first-party module imports in a fresh process and every tab +
the app shell construct headless.

Covers:
  1. Fresh-process import of every first-party module (dynamic discovery, so
     deletions are automatically reflected). Import runs in a SUBPROCESS so
     import-time side effects genuinely execute under test.
  1b. export_module's import-time _HW_ENCODER equals what post-import
     detection returns — i.e. the import-time probe really ran and did not
     silently fall back.
  2. Construct LiveSessionTab, SyncReviewTab, LabellingTab, BrainBattleApp
     headless (no event pumping — no camera probing, no playback).
  3. Minimal pure-logic checks: session timeline phase order, config
     validation, __version__ surfaced in the window title.

Run:  python3 tests/test_smoke.py   (nonzero exit on any failure)
Needs: PyQt6 etc. installed; stubs python-vlc only if libVLC is absent.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


# ── vlc stub, in-process (only if libVLC is absent on this machine) ─────────
def _install_vlc_stub_if_needed():
    try:
        import vlc  # noqa: F401
        return False
    except Exception:
        import types

        v = types.ModuleType("vlc")

        class State:
            (NothingSpecial, Opening, Buffering, Playing,
             Paused, Stopped, Ended, Error) = range(8)

        class EventType:
            MediaPlayerTimeChanged = "t"; MediaPlayerLengthChanged = "l"

        class _CBDec:
            VideoLockCb = staticmethod(lambda f: f)
            VideoUnlockCb = staticmethod(lambda f: f)
            VideoDisplayCb = staticmethod(lambda f: f)

        class Media:
            def __init__(self, *a, **k): pass

        class _EM:
            def event_attach(self, *a, **k): pass

        class _Player:
            def __init__(self): self._t = 0; self._s = State.NothingSpecial
            def video_set_callbacks(self, *a): pass
            def video_set_format(self, *a): pass
            def set_media(self, m): pass
            def set_hwnd(self, h): pass
            def event_manager(self): return _EM()
            def play(self): self._s = State.Playing
            def pause(self): self._s = State.Paused
            def stop(self): self._s = State.Stopped
            def get_state(self): return self._s
            def set_time(self, ms): self._t = int(ms)
            def get_time(self): return self._t
            def get_length(self): return 0

        class Instance:
            def __init__(self, *a, **k): pass
            def media_player_new(self): return _Player()
            def media_new(self, uri): return Media()

        v.State, v.EventType, v.CallbackDecorators = State, EventType, _CBDec
        v.Media, v.Instance = Media, Instance
        sys.modules["vlc"] = v
        return True


# Bootstrap prepended to every fresh-process import (bare stub is enough for
# import time — only brain_battle_main imports vlc at module level).
_SUBPROC_BOOTSTRAP = (
    "import sys, os, types\n"
    f"sys.path.insert(0, {ROOT!r})\n"
    "os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')\n"
    "try:\n"
    "    import vlc\n"
    "except Exception:\n"
    "    sys.modules['vlc'] = types.ModuleType('vlc')\n"
)


def _discover_modules():
    """Every first-party module, dynamically — deletions self-update the gate."""
    mods = []
    for f in sorted(os.listdir(ROOT)):
        if f.endswith(".py"):
            mods.append(f[:-3])
    for f in sorted(os.listdir(os.path.join(ROOT, "sync_utils"))):
        if f.endswith(".py") and f != "__init__.py":
            mods.append(f"sync_utils.{f[:-3]}")
    return mods


def part1_fresh_process_imports():
    print("── 1. fresh-process import of every first-party module")
    for mod in _discover_modules():
        r = subprocess.run(
            [sys.executable, "-c", _SUBPROC_BOOTSTRAP + f"import {mod}"],
            capture_output=True, text=True, timeout=120, cwd=ROOT,
        )
        tail = (r.stderr or "").strip().splitlines()
        check(f"import {mod}", r.returncode == 0, tail[-1] if tail and r.returncode else "")

    # 1b — THE videotoolbox-bug check: the import-time cached encoder must
    # equal what a post-import detection run returns. Under the pre-0993721
    # bug, import-time gave 'libx264' (NameError swallowed by the probe's
    # except) while post-import gave the real hardware encoder — unequal.
    r = subprocess.run(
        [sys.executable, "-c", _SUBPROC_BOOTSTRAP +
         "import export_module as em\n"
         "print(em._HW_ENCODER)\n"
         "print(em._detect_hw_encoder())\n"],
        capture_output=True, text=True, timeout=120, cwd=ROOT,
    )
    lines = [l for l in r.stdout.strip().splitlines() if not l.startswith("[export]")]
    ok = r.returncode == 0 and len(lines) >= 2 and lines[-2] == lines[-1]
    check("export_module import-time _HW_ENCODER == post-import detection",
          ok, f"import-time={lines[-2] if len(lines) >= 2 else '?'} "
              f"post-import={lines[-1] if lines else '?'}")


def part2_construct_tabs_and_shell(app):
    print("── 2. headless construction of tabs + app shell")
    import brain_battle_main as bbm
    from live_session_tab import LiveSessionTab

    for name, ctor in (("LiveSessionTab", LiveSessionTab),
                       ("SyncReviewTab", bbm.SyncReviewTab),
                       ("LabellingTab", bbm.LabellingTab),
                       ("SessionsPlaceholder", lambda: bbm.SessionsPlaceholder("/tmp", lambda: None)),
                       ("SettingsPlaceholder", bbm.SettingsPlaceholder)):
        try:
            w = ctor()
            check(f"construct {name}", True)
            if hasattr(w, "shutdown"):
                w.shutdown()
        except Exception as e:
            check(f"construct {name}", False, f"{type(e).__name__}: {e}")

    try:
        win = bbm.BrainBattleApp()
        check("construct BrainBattleApp (full shell)", True)
    except Exception as e:
        check("construct BrainBattleApp (full shell)", False, f"{type(e).__name__}: {e}")
        return None
    return win


def part3_pure_logic(win):
    print("── 3. pure-logic quick checks")
    from session_controller import SessionConfig, Phase, _build_timeline
    from version import __version__

    tl = _build_timeline(SessionConfig(num_rounds=3, round_sec=180,
                                       break_sec=60, baseline_sec=60))
    got = [(p.phase, p.round_num) for p in tl]
    want = [(Phase.BASELINE, 0), (Phase.ROUND, 1), (Phase.BREAK, 1),
            (Phase.ROUND, 2), (Phase.BREAK, 2), (Phase.ROUND, 3)]
    check("timeline: baseline -> rounds with breaks between, none after last",
          got == want, str([p.label for p in tl]))

    tl0 = _build_timeline(SessionConfig(num_rounds=1, baseline_sec=0))
    check("timeline: no baseline / single round edge case",
          [(p.phase, p.round_num) for p in tl0] == [(Phase.ROUND, 1)],
          str([p.label for p in tl0]))

    bad = SessionConfig(num_rounds=0)
    check("config validation rejects num_rounds=0", bad.validate() is not None)

    if win is not None:
        check("__version__ surfaced in window title",
              win.windowTitle() == f"Brain Battle  v{__version__}",
              repr(win.windowTitle()))
        # New shell: the four pages exist and the app opens on Sessions.
        check("shell has Sessions / New recording / Review & Label / Settings pages",
              all(hasattr(win, a) for a in
                  ("IDX_SESSIONS", "IDX_RECORD", "IDX_REVIEW", "IDX_SETTINGS")))
        check("app opens on Sessions",
              win._shell.current_index() == win.IDX_SESSIONS,
              f"current={win._shell.current_index()} sessions={getattr(win,'IDX_SESSIONS','?')}")
    else:
        check("__version__ surfaced in window title", False, "no app shell")


if __name__ == "__main__":
    part1_fresh_process_imports()

    stubbed = _install_vlc_stub_if_needed()
    if stubbed:
        print("  (libVLC absent — construct checks use the vlc stub)")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    win = part2_construct_tabs_and_shell(app)
    part3_pure_logic(win)
    if win is not None:
        try:
            win.live_tab.shutdown(); win.sync_tab.shutdown()
        except Exception:
            pass

    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print(f"  FAIL: {f}")
    sys.exit(1 if _failures else 0)
