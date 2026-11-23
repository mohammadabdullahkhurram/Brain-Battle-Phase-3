# Installation

**What this page covers:** installing Brain Battle on a **Mac** or a **Windows**
computer — the lab uses both — and checking that everything is in place before
your first run.

Brain Battle is a Python desktop app. There is no double-click installer yet
(planned — see the [Roadmap](../project/roadmap.md)); you run it from a copy of
the project folder.

## What you need

| Requirement | Why |
|---|---|
| Python 3.10 or newer | The app itself |
| The packages in `requirements.txt` | The window toolkit, camera and Bluetooth support, and others |
| `ffmpeg` | Recording each camera to video, and exporting |

Python **3.12** is the version the automatic tests run on (3.13 also works).

Cameras and headbands are **not** needed to install or explore the app — see
[First run](first-run.md) for how to try it without any hardware.

## Getting the code

Testers use the **`staging`** branch — the version currently being tested
(`develop` is where work happens; `main` holds approved versions). Either clone
it with git:

```bash
git clone --branch staging https://github.com/mohammadabdullahkhurram/Brain-Battle-Phase-3.git
```

or, on the GitHub page, switch the branch menu to `staging` and use
**Code → Download ZIP**. To update later, run `git pull` in the folder and
install again (`pip install -e .`). The project's `README.md` has the same
steps, numbered, for Mac and Windows, and the same three ways to run it (below).

## Mac

1. Install [Homebrew](https://brew.sh) if you don't have it, then open
   **Terminal** and install ffmpeg:

   ```bash
   brew install ffmpeg
   ```

2. In Terminal, go to the project folder and install the app:

   ```bash
   pip3 install -e .
   ```

3. Start it:

   ```bash
   python3 -m brain_battle
   ```

The first time you open **New recording**, macOS asks whether Brain Battle (or
Terminal) may use the **camera** and **Bluetooth**. Choose **Allow** for both;
if you chose otherwise, change it in *System Settings → Privacy & Security*.

## Windows

1. Install Python 3.10 or newer from [python.org](https://www.python.org). On
   the first page of the installer tick **"Add python.exe to PATH"**.
2. Open **Terminal** (or PowerShell) from the Start menu and install ffmpeg:

   ```bash
   winget install Gyan.FFmpeg
   ```

   Close and reopen Terminal afterwards so it can find ffmpeg.
3. In Terminal, go to the project folder and install the app:

   ```bash
   pip install -e .
   ```

4. Start it:

   ```bash
   python -m brain_battle
   ```

Notes for Windows:

- Windows needs **Bluetooth switched on** (*Settings → Bluetooth & devices*) for
  the headbands. You do not need to pair them in Windows first; the app finds
  them itself.
- The first time you open **New recording**, Windows may ask whether to allow
  the app through the **firewall**. Either answer is fine for recording; choose
  Allow if other lab software on the network should see the live signals.
- If cameras are not found, check *Settings → Privacy & security → Camera* and
  make sure desktop apps may use the camera.
- The app picks the fastest video encoder your graphics card offers (NVIDIA, AMD
  or Intel) and falls back to a built-in one if none works — nothing to set up.

## Run the app

Three ways, from inside the project folder:

**a) With demo data — for testing.**

```bash
python3 tools/demo.py              # Mac
python tools\demo.py               # Windows
```

The first time it makes 6 demo recordings in **Brain Battle Demo** in your
home folder (about two minutes), then opens the app on them with simulated
cameras and headbands. You see every kind of recording — labelled by two
people, needs labels, exported, interrupted, stopped early — and New recording
works without hardware. It never touches your real recordings or settings.
To reset it: `python3 tools/demo.py --fresh`, or delete the **Brain Battle
Demo** folder.

**b) Without demo data, simulated devices** — to try New recording without
hardware:

```bash
BRAIN_BATTLE_SIMULATE=1 python3 -m brain_battle          # Mac / Linux
```

```powershell
$env:BRAIN_BATTLE_SIMULATE=1; python -m brain_battle      # Windows PowerShell
```

This uses your normal recordings folder and settings: recordings made this way
are saved there like real ones.

**c) Real use, real hardware:** `python3 -m brain_battle` (Windows:
`python -m brain_battle`).

## Verify the install

First, confirm ffmpeg can be found. In Terminal:

```bash
ffmpeg -version
```

If this prints a version, you're set. If it says the command is not found,
ffmpeg isn't installed or Terminal was not reopened after installing it —
recording is blocked without it (the app checks before every recording and
tells you plainly if it's missing).

Then start the app as above. It should open on the **Sessions** screen, empty,
with "Brain Battle" in the title bar. If it does, installation is done —
continue to [First run](first-run.md).

## The same recordings on several computers

To let several computers — Macs, Windows PCs, or a mix — work with the same
recordings, point each one at the same shared folder in **Settings →
Recordings folder**. See [Settings](../user-guide/settings.md#this-computer).

## Troubleshooting the install

See [Troubleshooting](../user-guide/troubleshooting.md) for symptom → cause →
fix if something above didn't work.
