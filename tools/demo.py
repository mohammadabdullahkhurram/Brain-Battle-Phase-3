"""
demo.py — try the app with demo recordings and simulated devices, in one command
──────────────────────────────────────────────────────────────────────────────────

    python3 tools/demo.py                 # make the demo library once, then open the app on it
    python3 tools/demo.py --fresh         # delete the demo library and make it again
    python3 tools/demo.py --out <folder>  # use another demo folder

The first run fills a demo folder (default: "Brain Battle Demo" in your home
folder) with 6 recordings that look like the current app made them
(``tools/generate_demo_session.py --library``; about two minutes), then starts
Brain Battle on that folder with **simulated cameras and headbands**
(``BRAIN_BATTLE_SIMULATE=1``), so New recording works without hardware too.
Later runs reuse the folder.

It never touches your real recordings or settings: the app runs with its own
settings file inside the demo folder, refuses a demo folder that is your real
recordings folder, and ``--fresh`` deletes only a folder this tool made (it
carries a ``.brain-battle-demo`` marker file). Works on macOS and Windows.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKER = ".brain-battle-demo"
SETTINGS = ".brain-battle-demo-settings.json"


def default_folder() -> str:
    return os.path.join(os.path.expanduser("~"), "Brain Battle Demo")


def real_recordings_folder() -> str:
    """The recordings folder this computer's Brain Battle really uses (read
    before anything is pointed at the demo)."""
    sys.path.insert(0, ROOT)
    from brain_battle.data import app_settings
    return os.path.abspath(app_settings.load().capture_dir_resolved())


def is_demo_folder(folder: str) -> bool:
    return os.path.isfile(os.path.join(folder, MARKER))


def make_library(folder: str) -> None:
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, MARKER), "w", encoding="utf-8") as f:
        f.write("Made by tools/demo.py — demo recordings, safe to delete.\n")
    cmd = [sys.executable, os.path.join(ROOT, "tools", "generate_demo_session.py"),
           "--out", folder, "--library"]
    print("Making the demo recordings (about two minutes, only the first time)…", flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def app_env(folder: str) -> dict:
    """The app's environment: simulated devices and its own settings file in
    the demo folder (recordings folder = the demo folder)."""
    settings = os.path.join(folder, SETTINGS)
    if not os.path.isfile(settings):
        with open(settings, "w", encoding="utf-8") as f:
            json.dump({"capture_dir": folder}, f)
    env = dict(os.environ)
    env["BRAIN_BATTLE_SIMULATE"] = "1"
    env["BRAIN_BATTLE_CONFIG"] = settings
    env.pop("BRAIN_BATTLE_CAPTURE_DIR", None)      # the settings file names the folder
    return env


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=default_folder(),
                    help="the demo folder (default: 'Brain Battle Demo' in your home folder)")
    ap.add_argument("--fresh", action="store_true", help="delete the demo folder and make it again")
    ap.add_argument("--no-app", action="store_true", help="only make the demo recordings")
    args = ap.parse_args(argv)
    folder = os.path.abspath(os.path.expanduser(args.out))

    if folder == real_recordings_folder():
        print(f"Refusing: {folder} is your real recordings folder. Pick another --out.",
              file=sys.stderr)
        return 2
    if args.fresh and os.path.isdir(folder):
        if not is_demo_folder(folder):
            print(f"Refusing to delete {folder}: it was not made by tools/demo.py "
                  f"(no {MARKER} file in it).", file=sys.stderr)
            return 2
        print(f"Deleting the old demo folder {folder}…", flush=True)
        shutil.rmtree(folder)
    if not os.path.isdir(folder) or not is_demo_folder(folder):
        if os.path.isdir(folder) and os.listdir(folder):
            print(f"Refusing: {folder} already has other files in it. Pick another --out.",
                  file=sys.stderr)
            return 2
        make_library(folder)
    print(f"Demo recordings: {folder}")
    if args.no_app:
        return 0
    print("Starting Brain Battle with simulated cameras and headbands…", flush=True)
    return subprocess.run([sys.executable, "-m", "brain_battle"], env=app_env(folder),
                          cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
