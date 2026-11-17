# Brain Battle

Brain Battle is a desktop app for a neuroscience study of boxing, made at
Constructor University Bremen. During a sparring session it records **four
cameras** and **two Muse-S EEG headbands** (one per boxer) at the same time, on
one shared clock. Afterwards you can watch everything back together, mark what
happened (a punch thrown, landed or blocked, a clinch…), and export the data
for analysis. Fighters are stored only as **4-character codes and colours**
(like `K7Q2` in red) — never by name. It runs
on Mac and Windows, and has a **demo mode** that needs no cameras or headbands —
that is what you use to try it out.

> **Which version to test:** there is no `staging` branch yet, so testers use
> **`develop`** (the commands below fetch it). Once the owner has approved
> `develop`, a `staging` branch will be made for testing.

> **Tested only in simulation so far.** Everything works with *simulated*
> cameras and headbands, and the automatic tests pass on Mac and Windows. Real
> Muse headbands, real cameras and real Windows PCs have **not** been tried
> yet. If you test on one of those, please tell us what happened.

## What it looks like

- **Sessions** — every recording (`● K7Q2 vs ● M3XA`), its status, and a big
  **New recording** button.
- **New recording** — *Get ready* (a checklist: headbands, signal, cameras,
  codes and colours, a buzzer **timing test**, space), *Recording* (starts
  15 s before the baseline; rounds and breaks are marked automatically; the
  bell rings at the start and end of each round; **M** marks a moment with an
  optional note), *Saved*.
- **Review & Label** — the four cameras, both boxers' EEG, labels, custom
  notes and the notes on marked moments; compare with other labellers.
- **Settings** — your name, the recordings folder, the team's label list and
  fighter colours, and Advanced options.

## Run it on Mac

1. **Install Python 3.12.** Download the macOS installer from
   <https://www.python.org/downloads/macos/> (pick the newest *3.12.x*;
   3.13 also works) and run it.
2. **Install ffmpeg** (the app uses it to save video). Install Homebrew from
   <https://brew.sh> if you don't have it, then open **Terminal** and run:
   `brew install ffmpeg`
3. **Get the code** — either way works:
   - **With git** (run `xcode-select --install` once if `git` is missing):
     ```bash
     cd ~
     git clone --branch develop https://github.com/mohammadabdullahkhurram/Brain-Battle-Phase-3.git
     cd Brain-Battle-Phase-3
     ```
   - **Without git:** open the
     [GitHub page](https://github.com/mohammadabdullahkhurram/Brain-Battle-Phase-3),
     switch the branch menu from `main` to **`develop`**, click
     **Code → Download ZIP**, unzip it into your home folder, and in Terminal
     `cd` into the unzipped folder.
4. **Install the app** (takes a few minutes the first time):
   ```bash
   python3 -m pip install -e .
   ```
5. **Start it in demo mode** (simulated cameras and headbands):
   ```bash
   BRAIN_BATTLE_SIMULATE=1 python3 -m brain_battle
   ```
6. **Make some demo recordings** to look at and label. In a second Terminal
   window, inside the same folder:
   ```bash
   python3 tools/generate_demo_session.py --library --out ~/BrainBattleDemo
   ```
   Then start the app pointed at that folder:
   ```bash
   BRAIN_BATTLE_SIMULATE=1 BRAIN_BATTLE_CAPTURE_DIR=~/BrainBattleDemo python3 -m brain_battle
   ```
   (Or choose `BrainBattleDemo` as the recordings folder in the app's
   **Settings**.)
7. **Update to the latest version later:** in Terminal, inside the folder,
   run `git pull` and then `python3 -m pip install -e .` again. Without git,
   download the ZIP again (step 3) and repeat step 4.

## Run it on Windows

1. **Install Python 3.12.** Download the Windows installer from
   <https://www.python.org/downloads/windows/> (pick the newest *3.12.x*).
   On the first screen of the installer, **tick "Add python.exe to PATH"**,
   then click *Install Now*.
2. **Install ffmpeg.** Open **PowerShell** (Start menu → type "PowerShell")
   and run `winget install Gyan.FFmpeg`. Close PowerShell and open it again
   afterwards.
3. **Get the code** — either way works:
   - **With git** (install it from <https://git-scm.com/download/win> with the
     default options):
     ```powershell
     cd $HOME
     git clone --branch develop https://github.com/mohammadabdullahkhurram/Brain-Battle-Phase-3.git
     cd Brain-Battle-Phase-3
     ```
   - **Without git:** open the
     [GitHub page](https://github.com/mohammadabdullahkhurram/Brain-Battle-Phase-3),
     switch the branch menu from `main` to **`develop`**, click
     **Code → Download ZIP**, unzip it into your user folder, and in
     PowerShell `cd` into the unzipped folder.
4. **Install the app** (takes a few minutes the first time):
   ```powershell
   python -m pip install -e .
   ```
5. **Start it in demo mode** (simulated cameras and headbands):
   ```powershell
   $env:BRAIN_BATTLE_SIMULATE = "1"
   python -m brain_battle
   ```
   (`$env:…` lasts until you close that PowerShell window.)
6. **Make some demo recordings** to look at and label:
   ```powershell
   python tools\generate_demo_session.py --library --out "$HOME\BrainBattleDemo"
   $env:BRAIN_BATTLE_CAPTURE_DIR = "$HOME\BrainBattleDemo"
   python -m brain_battle
   ```
   (Or choose `BrainBattleDemo` as the recordings folder in the app's
   **Settings**.)
7. **Update to the latest version later:** in PowerShell, inside the folder,
   run `git pull` and then `python -m pip install -e .` again. Without git,
   download the ZIP again (step 3) and repeat step 4.

Live signals for other programs (LSL) are **off** by default (Settings →
Advanced). If you switch them on, Windows or macOS may ask whether Python may
use the network — either answer is fine for recording.

## If something goes wrong

| Problem | Fix |
|---|---|
| `python` / `python3` "not found" or "not recognized" | Python is not on your PATH. Windows: run the installer again, choose *Modify*, and tick "Add Python to environment variables" — or type `py` instead of `python`. Mac: use `python3`, not `python`. |
| Recording won't start and the app says ffmpeg is missing | Install ffmpeg (step 2), then **close and reopen** Terminal / PowerShell and start the app again. |
| `pip install` stops with an error | Run `python -m pip install --upgrade pip` (Mac: `python3 -m pip …`) and try step 4 again. Check you are inside the `Brain-Battle-Phase-3` folder (it contains `pyproject.toml`). |
| The Sessions screen is empty | The app is looking at a different folder. The foot of Sessions shows which one; change it in **Settings**, or start the app with `BRAIN_BATTLE_CAPTURE_DIR` as in step 6. |

More answers: the **Troubleshooting** and **FAQ** pages in the app's
**Documentation** screen ([docs/user-guide/troubleshooting.md](docs/user-guide/troubleshooting.md)).

## How to report a problem

Send a message (or open an issue on GitHub) with:

1. **What you did** — the steps, in order, and which screen you were on.
2. **What happened** — and what you expected instead. Copy any error message
   exactly.
3. **A screenshot** of the app when it went wrong.
4. **The log:**
   - the text in the Terminal / PowerShell window the app was started from
     (copy the last 30 or so lines), and
   - if it happened during or after a recording, the file
     `session_<date>_<time>.log` inside that recording's folder (Sessions →
     right-click the recording → *Show in Finder* / *Show in Explorer*).
5. **Your computer** — Mac or Windows, and the version shown in the window
   title (e.g. `3.1.0-dev`).

## Documentation (for developers)

The full documentation — user guide, file formats, how timing works,
architecture, decisions — is readable **inside the app** (the
**Documentation** button at the top) and as Markdown under
[`docs/`](docs/). Developers should start with
[`AGENTS.md`](AGENTS.md) (current state, branch and tag rules, test map),
[Developer setup & workflow](docs/developer-guide/setup-and-workflow.md) and
[`CHANGELOG.md`](CHANGELOG.md). Run the tests with `python3 tools/run_tests.py`.

The round bell is "Boxing Bell.wav" by
[Benboncan](https://freesound.org/people/Benboncan/sounds/66951/) (CC-BY 4.0);
fonts and other credits are in [Credits & licences](docs/project/credits.md).

## Checkpoints

Each checkpoint is a git tag: a saved version you can always go back to. To
look at one (read-only), inside the code folder run, for example:

```bash
git checkout v3.0.0-beta.2      # go to that version
git checkout develop            # come back to the version under test
```

Dates are the date of the tagged commit. Newest first.

### Main checkpoints

**`v3.1.0-dev.3`** — 2026-11-16 — `339f941` — *current `develop`*
The 3.1 work on top of 3.0: live Lab Streaming Layer streams, one
`recording.xdf` per recording, a standard EEG viewer, a redesign of every
screen, several labellers per recording, hiding boxer names, Windows support,
a shared recordings folder, and the "Too many open files" fix. Known limits:
the Windows automatic tests were not green yet, and on Windows its timestamps
were rounded to 15.6 ms (both fixed after this tag, on `develop`). Go back to
it to compare with the version before the November 17 fixes.

**`v3.0.0-beta.2`** — 2026-11-11 — `67e1676` — *current `main`*
The reviewed, approved version: the four screens (Sessions, New recording,
Review & Label, Settings), the capture-time clock, labelling, crash-safe
recording and the in-app documentation, plus a clean-up and handover. Known
limits: no LSL streams or `recording.xdf`, one labeller per recording, macOS
only in practice, and never tried on real hardware. Go back to it if the 3.1
work causes a problem and you need the last approved version.

**`v3.0.0-beta.1`** — 2026-11-10 — `10d1aa9`
The first pre-release of the 3.0 line: the capture-time clock, the four DESIGN
screens, in-app docs and the `brain_battle` package layout. Known limits: the
rough edges that beta.2 cleaned up (screen names, default recordings folder,
docs layout). Go back to it only to see 3.0 as first released.

**`v2.1.0`** — 2026-07-07 — `2695f7d` (tagged later, on 2026-11-13)
The July 2026 handover version, the last `main` before 3.0. It records one
continuous take per session, but aligns cameras and EEG without the
capture-time clock, and its recordings do **not** open in 3.0 or later. Go back
to it to open recordings made before 3.0, or to see the original design.

---

### Older tags

**`v3.1.0-dev.2`** — 2026-11-14 — `efe6adf`
The redesign of every screen, several labellers per recording, hiding boxer
names, Mac + Windows support and shared-folder safety, on top of
`v3.1.0-dev.1`. Known limits: recordings in a row ran out of open files after
two or three ("Too many open files"), and the Windows tests did not finish. Go
back to it only to see the redesign before the open-files fix.

**`v3.1.0-dev.1`** — 2026-11-13 — `c07cfb7`
The standard EEG workflow added to 3.0: live LSL streams, `recording.xdf`, a
conventional EEG viewer, the headbands' movement sensors and one capture clock.
Known limits: the old screen design, one labeller per recording, no Windows
testing. Go back to it to see the LSL / XDF work on its own, before the
redesign.

**`milestone/docs-complete`** — 2026-11-09 — `7175f48` (tagged 2026-11-13)
The point where the in-app documentation was finished (all six sections,
search, Help buttons). Known limits: the app as it was before
`v3.0.0-beta.1`'s final touches. Go back to it to see the documentation
milestone on its own.

**`milestone/phase-3-redesign`** — 2026-11-06 — `c5187ad` (tagged 2026-11-13)
Roadmap Phase 3 done: the four DESIGN screens and the merged Review & Label
screen with PyAV playback. Known limits: documentation not yet complete. Go
back to it to see the end of Phase 3.

**`milestone/phase-2-data-safety`** — 2026-10-22 — `77c13bd` (tagged 2026-11-13)
Roadmap Phase 2 done: crash-safe video recording, EEG streamed to disk and
crash recovery. Known limits: the old user interface. Go back to it to see the
data-safety work before the redesign.

**`milestone/phase-1-foundation`** — 2026-10-20 — `82fe258` (tagged 2026-11-13)
Roadmap Phase 1 done: the demo-session generator and pinned dependencies — the
basis for working without hardware. Known limits: everything else is still the
2.x app. Go back to it to see where the 3.0 work started.
