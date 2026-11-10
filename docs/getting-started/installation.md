# Installation

**What this page covers:** installing Brain Battle's dependencies on macOS
(the primary development platform) and notes for Windows, plus a step to
verify everything is in place before your first run.

Brain Battle is a Python desktop app (PyQt6) — there's no installer package
yet (see `docs/ROADMAP.md` Phase 6 for the planned double-click build). You
run it from a checkout of the repository.

## What you need

| Requirement | Why |
|---|---|
| Python 3.10 or newer | The app itself |
| The packages in `requirements.txt` | PyQt6, OpenCV, the Muse-S Bluetooth stack, and others — see that file |
| `ffmpeg` on your system `PATH` | Recording each camera to video, and building the export bundle |

Camera and headband hardware is **not** required to install or explore the
app — see [First run](first-run.md) for how to try it without any hardware
attached.

## macOS

Install [Homebrew](https://brew.sh) if you don't have it, then install
ffmpeg:

```bash
brew install ffmpeg
```

From the repository root, install the Python packages:

```bash
pip install -e .
```

Then continue to [Verify the install](#verify-the-install) below.

## Windows

1. Install Python 3.10+ from [python.org](https://www.python.org) (check
   "Add python.exe to PATH" during setup).
2. Install ffmpeg and make sure `ffmpeg.exe` is on your `PATH` — the
   [ffmpeg.org download page](https://ffmpeg.org/download.html) has
   Windows builds; after extracting, add the `bin` folder to your system
   `PATH` environment variable.

From the repository root, install the Python packages:

```bash
pip install -e .
```

Then continue to [Verify the install](#verify-the-install) below.

Windows camera capture uses the DirectShow backend automatically
(`usb_camera.py` picks the right OpenCV backend per platform) — nothing to
configure by hand.

## Verify the install

First, confirm ffmpeg is on your `PATH`:

```bash
ffmpeg -version
```

If this prints a version banner, you're set. If it says "command not found,"
ffmpeg isn't installed or isn't on `PATH` — recording is blocked without it
(the app checks this itself before every recording and tells you plainly if
it's missing).

Then launch the app:

```bash
python3 -m brain_battle
```

It should open on the **Sessions** screen, empty, titled "Brain Battle" —
that confirms PyQt6 and the rest of the dependencies installed correctly.
If the app opens, installation is done — continue to
[First run](first-run.md).

## Troubleshooting the install

See the [Troubleshooting](../user-guide/troubleshooting.md) page for
symptom → cause → fix if something above didn't work.
