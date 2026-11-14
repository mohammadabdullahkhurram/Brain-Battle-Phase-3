"""
Mac and Windows: the things that differ between them.

  1. Dates are formatted without the "%-d" strftime code (Windows' Python
     rejects it) — and no source file uses "%-d" / "%-m" / "%-H" … at all.
  2. The file manager is called Finder on macOS and Explorer on Windows, the
     reveal command is right for each, and no screen hard-codes "Finder".
  3. Keyboard shortcuts are written the way each system writes them.
  4. Settings and recordings folders resolve to the right place on each system.
  5. Folder and file names made by the app are legal on Windows.
  6. The video encoder is chosen from the right candidates per system and
     always falls back to libx264; a missing ffmpeg gives install help for
     this system.
  7. Keeping the computer awake never raises, whatever the system.
  8. The bundled fonts, bell sound and LSL settings file exist.

Run:  python3 tests/test_platform.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401

import datetime as dt
import os
import re
import sys

import brain_battle.support.platform_utils as pu

ROOT = _env.ROOT
_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _sources(*dirs):
    for d in dirs:
        for dp, _dn, fns in os.walk(os.path.join(ROOT, d)):
            if "__pycache__" in dp:
                continue
            for fn in fns:
                if fn.endswith(".py"):
                    yield os.path.join(dp, fn)


def test_dates():
    d = dt.datetime(2026, 11, 9, 7, 5)
    check("format_date has no leading zero on the day", pu.format_date(d) == "Mon 9 Nov 2026",
          pu.format_date(d))
    check("format_date_time", pu.format_date_time(d) == "Mon 9 Nov 2026, 07:05")
    check("format_date without the weekday", pu.format_date(d, weekday=False) == "9 Nov 2026")
    check("format_iso reads a manifest's started_at",
          pu.format_iso("2026-11-09T07:05:00") == "Mon 9 Nov 2026, 07:05"
          and pu.format_iso("not a date", fallback="?") == "?")
    bad = []
    pat = re.compile(r"%-[a-zA-Z]|%#[a-zA-Z]")       # platform-only strftime flags
    me = os.path.abspath(__file__)
    for path in _sources("brain_battle", "tools", "tests"):
        if os.path.abspath(path) == me:
            continue
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            if pat.search(line) and "platform_utils" not in path:
                bad.append(f"{os.path.relpath(path, ROOT)}:{i}")
    check("no source file uses a platform-only strftime code such as %-d", bad == [], str(bad[:5]))


def test_file_manager():
    check("called Finder on macOS, Explorer on Windows",
          pu.file_manager_name("darwin") == "Finder" and pu.file_manager_name("win32") == "Explorer"
          and pu.show_in_file_manager_label("win32") == "Show in Explorer"
          and pu.show_in_file_manager_label("darwin") == "Show in Finder")
    p = os.path.join(ROOT, "tests")
    check("macOS reveal: open -R <path>", pu.reveal_command(p, "darwin") == ["open", "-R", p])
    win = pu.reveal_command(p, "win32")
    check("Windows reveal: one string, /select, glued to the quoted path",
          isinstance(win, str) and win == f'explorer /select,"{p}"', str(win))
    check("reveal / open never raise on a missing path",
          pu.reveal_in_file_manager("") is False and pu.open_folder("") is False)
    bad = []
    for path in _sources("brain_battle"):
        if path.endswith("platform_utils.py"):
            continue
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            code = line.split("#")[0]
            for m in re.finditer(r'"([^"]*)"', code):
                if re.search(r"\bFinder\b", m.group(1)) and "Explorer" not in m.group(1) \
                        and '"""' not in line:
                    bad.append(f"{os.path.relpath(path, ROOT)}:{i}")
    check('no UI string hard-codes "Finder"', bad == [], str(bad[:5]))


def test_shortcuts():
    check("shortcut wording per system",
          pu.shortcut("Z", platform_name="darwin") == "⌘Z"
          and pu.shortcut("Z", shift=True, platform_name="darwin") == "⇧⌘Z"
          and pu.shortcut("Z", platform_name="win32") == "Ctrl+Z"
          and pu.shortcut("H", shift=True, platform_name="win32") == "Ctrl+Shift+H")
    check("computer_name is a short non-empty name", 0 < len(pu.computer_name()) <= 60)


def test_paths():
    import platform
    import brain_battle.data.app_settings as aps
    import brain_battle.paths as paths
    saved_env = os.environ.pop("BRAIN_BATTLE_CONFIG", None)
    real_system = platform.system
    try:
        platform.system = lambda: "Windows"
        os.environ["APPDATA"] = os.path.join("C:\\Users\\lab", "AppData", "Roaming")
        check("Windows settings live under %APPDATA%\\Brain Battle",
              aps.config_path().startswith(os.environ["APPDATA"]) and "Brain Battle" in aps.config_path(),
              aps.config_path())
        platform.system = lambda: "Darwin"
        check("macOS settings live under Application Support",
              os.path.join("Library", "Application Support", "Brain Battle") in aps.config_path())
    finally:
        platform.system = real_system
        if saved_env is not None:
            os.environ["BRAIN_BATTLE_CONFIG"] = saved_env
    check("default recordings folder is Documents/Brain Battle Data on both",
          paths.default_capture_root().endswith(os.path.join("Documents", "Brain Battle Data"))
          or sys.platform.startswith("linux"))


def test_windows_safe_names():
    import brain_battle.data.session_storage as ss
    import brain_battle.data.labels_store as ls
    illegal = set('<>:"/\\|?*')
    name = ss._session_folder_name(dt.datetime(2026, 1, 2, 3, 4), 'Sam: "The <Jab>"', "CON")
    check("a recording folder name has no character Windows forbids, and no trailing dot/space",
          not (set(name) & illegal) and not name.endswith((" ", ".")), name)
    check("reserved Windows device names are avoided", ss.sanitize_name("CON") == "_CON"
          and ss.sanitize_name("nul") == "_nul" and ss.sanitize_name("Sam.") == "Sam")
    slug = ls.labeller_slug('Maya: K/"x" ?')
    check("a labeller's file name is Windows-safe", not (set(slug) & illegal), slug)
    from brain_battle.capture.eeg_module import muse_friendly
    check("the EEG file name is Windows-safe",
          not (set(f"session_{muse_friendly('MuseS-7538')}.csv") & illegal))


def test_encoder_and_ffmpeg_help():
    import brain_battle.data.export_module as em
    real = sys.platform
    try:
        sys.platform = "win32"
        check("Windows tries NVIDIA, AMD then Intel hardware encoders",
              em._candidate_encoders() == ["h264_nvenc", "h264_amf", "h264_qsv"])
        sys.platform = "darwin"
        check("macOS tries VideoToolbox", em._candidate_encoders() == ["h264_videotoolbox"])
    finally:
        sys.platform = real
    for enc in ("libx264", "h264_nvenc", "h264_amf", "h264_qsv", "h264_videotoolbox", "unknown"):
        if not em._encoder_args(enc):
            check(f"encoder flags for {enc}", False)
    check("every encoder has flags; an unknown one gets the libx264 flags",
          em._encoder_args("unknown") == em._encoder_args("libx264"))
    import shutil
    real_which = shutil.which
    try:
        shutil.which = lambda _name: None
        check("with no ffmpeg at all the encoder falls back to libx264",
              em._detect_hw_encoder() == "libx264")
    finally:
        shutil.which = real_which
    check("the chosen encoder is one this system could use",
          em._HW_ENCODER in em._candidate_encoders() + ["libx264"], em._HW_ENCODER)
    check("ffmpeg install help is specific to each system",
          "brew install ffmpeg" in pu.ffmpeg_install_hint("darwin")
          and "winget install" in pu.ffmpeg_install_hint("win32"))


def test_keep_awake_never_raises():
    import platform
    from brain_battle.capture.keep_awake import KeepAwake
    real_system = platform.system
    try:
        for name in ("Windows", "Darwin", "Linux", "SomethingElse"):
            platform.system = lambda n=name: n
            k = KeepAwake()
            k.start()            # on the "wrong" system the OS call fails — must be swallowed
            on = k.is_active()
            k.stop()
            check(f"keep-awake start/stop is safe when the system reports {name}",
                  on and not k.is_active())
    finally:
        platform.system = real_system


def test_bundled_files():
    import brain_battle.paths as paths
    fonts = os.path.join(str(paths.ASSETS_DIR), "fonts")
    check("bundled Inter fonts exist",
          all(os.path.isfile(os.path.join(fonts, f)) for f in
              ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf")))
    check("bundled bell sound exists",
          os.path.isfile(os.path.join(str(paths.ASSETS_DIR), "sounds", "round_bell.wav")))
    check("LSL settings file is packaged",
          os.path.isfile(os.path.join(ROOT, "brain_battle", "support", "lsl_api.cfg")))
    wf = os.path.join(ROOT, ".github", "workflows", "tests.yml")
    text = open(wf, encoding="utf-8").read() if os.path.isfile(wf) else ""
    check("the GitHub Actions workflow runs the suite on macOS and Windows",
          "macos-latest" in text and "windows-latest" in text and "tools/run_tests.py" in text)


if __name__ == "__main__":
    test_dates()
    test_file_manager()
    test_shortcuts()
    test_paths()
    test_windows_safe_names()
    test_encoder_and_ffmpeg_help()
    test_keep_awake_never_raises()
    test_bundled_files()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
