# Brain Battle

Brain Battle is a PyQt6 desktop app for a neuroscience boxing study: it records a
sparring session from **4 USB cameras + 2 Muse-S EEG headbands** onto one shared
clock, lets a researcher review and label it on a single playhead, and exports a
time-aligned analysis bundle. It follows the standard Muse research workflow:
every signal is published live as a **Lab Streaming Layer (LSL)** stream and
every recording gets one **`recording.xdf`** — EEG, round markers, labels and
each video frame's time on one clock — that opens directly in EEGLAB, MNE-Python
and pyxdf. More than one person can label the same recording and compare their
labels; it runs on **macOS and Windows**; and the recordings folder can be
shared between computers. It is built at Constructor University Bremen as a
Community Impact Project. Three screens plus Settings: **Sessions → New recording
→ Review & Label**.

> **3.0.0 is a breaking change.** Alignment is driven by a capture-time clock
> (per-frame and per-EEG-sample timestamps against a shared origin). Sessions
> recorded before 3.0 lack this data and are **not** supported — record fresh
> with 3.0.

## Install & run

Python 3.10+, plus `ffmpeg` on your `PATH` (per-camera H.264 recording, export,
probing): `brew install ffmpeg` on macOS, `winget install Gyan.FFmpeg` on
Windows. Review & Label playback is in-process via PyAV — no VLC needed. Full
steps for both systems: [Installation](docs/getting-started/installation.md).

```bash
pip install -e .          # editable install (the supported install for now)
python3 -m brain_battle    # or: brain-battle  (or: python3 brain_battle_main.py)
```

`pip install -e .` reads `pyproject.toml` and installs the pinned
`requirements.txt` set; the app finds its bundled `assets/` and `docs/`
relative to the checkout, so the editable install is the supported one for now
(proper packaging is ROADMAP Phase 6). `python3 brain_battle_main.py` still
works as a thin launcher shim.

Recordings are written to `~/Documents/Brain Battle Data` by default (change it
in Settings, or set `BRAIN_BATTLE_CAPTURE_DIR`). With no hardware, develop against the demo
session and simulation mode (see the Developer guide):

```bash
BRAIN_BATTLE_SIMULATE=1 python3 -m brain_battle
python3 tools/generate_demo_session.py --out /tmp/demo
python3 tools/run_tests.py                 # every test script, PASS/FAIL per script
```

## Documentation

Full documentation lives under [`docs/`](docs/) and is also readable **inside the
app** (the Documentation screen in the sidebar renders the same Markdown):

- **[Getting started](docs/getting-started/overview.md)** — what it is, install, first run, a quick tour.
- **[User guide](docs/user-guide/sessions.md)** — how to use each screen, troubleshooting, FAQ.
- **[Research & data](docs/research-and-data/recording-folder.md)** — the recording folder, every file format, timing & accuracy, and [loading the data](docs/research-and-data/loading-the-data.md) in Python (pandas, pyxdf, MNE), EEGLAB and R.
- **[Developer guide](docs/developer-guide/architecture.md)** — architecture, a reference entry per module, key flows, the threading model, testing, and how to extend it.
- **[Decisions (ADRs)](docs/decisions/index.md)** — why the app is built the way it is.
- **[Project](docs/project/goals.md)** — goals & research context, current status, roadmap, version history, glossary.

The `docs/` tree is the authoritative, maintained description of the app.

The round bell is "Boxing Bell.wav" by [Benboncan](https://freesound.org/people/Benboncan/sounds/66951/)
(CC-BY 4.0); see [Credits & licences](docs/project/credits.md) for this and
the bundled Inter font.

## Current status

**`main` is the reviewed `3.0.0-beta.2`; `develop` is `3.1.0-dev`** (tag
`v3.1.0-dev.2`) and awaits the owner's review. It adds, in two passes: the LSL /
XDF workflow, a conventional EEG viewer and the headbands' movement sensors
([write-up](docs/reviews/2026-11-fable-review.md)); then a redesign of every
screen, several labellers per recording, hiding boxer names, Windows support
and shared-folder safety, with the tests run on macOS and Windows by GitHub
Actions ([write-up](docs/reviews/2026-11-redesign.md)). The four DESIGN
screens, the capture-time clock, labelling, data safety, and the in-app docs all
shipped in 3.0.
**The hardware is currently broken**, so everything is
built and tested against a generated demo session and simulated devices — the
real-signal and eyes-on-glass checks remain open. See
[Current status](docs/project/status.md) and
[Known limitations](docs/research-and-data/limitations.md).

## Project

| | |
|---|---|
| Institution | Constructor University Bremen (Community Impact Project) |
| Author | Mohammad Abdullah Khurram · supervisor Prof. Jakob Fruchtmann |
| Version | `main` v3.0.0-beta.2 · `develop` v3.1.0-dev (authoritative: `brain_battle/version.py`) |
| GitHub | https://github.com/mohammadabdullahkhurram/Brain-Battle-Phase-3 |

Development history is in [`CHANGELOG.md`](CHANGELOG.md); current project context,
backlog, and the test map are in [`AGENTS.md`](AGENTS.md); planned work is in
[`docs/ROADMAP.md`](docs/ROADMAP.md).
