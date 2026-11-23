# Brain Battle — v3.1.0-dev (develop) · v3.0.0-beta.2 (main)

> **Agent instructions.** This file is the shared, tool-neutral instructions
> file for AI coding agents (read automatically by OpenAI Codex, and by Claude
> Code via a one-line `CLAUDE.md` that imports it). It is the **single source**
> of these instructions — do not duplicate its content elsewhere. It describes
> the **present** state of the project; `docs/archive/history.md` keeps the
> long-form narrative.

PyQt6 desktop app for neuroscience boxing capture: 4 USB cameras + 2 Muse-S EEG
headbands (Bluetooth). Four screens behind a light top header (design F) — **Sessions**
(home library), **New recording** (a guided capture flow), **Review & Label**
(one merged playback + labelling screen), and **Settings** — plus an in-app
**Documentation** screen and an export pipeline. It follows the standard Muse
research workflow: optional live **LSL** streams (off by default) and one
**`recording.xdf`** per recording (EEG + markers + labels + per-frame video
times on one clock) for EEGLAB / MNE / pyxdf. More than one person can label
the same recording (one labels file per labeller); the lab runs it on **Mac and
Windows**; the recordings folder may be shared between computers. Fighters are
**4-character codes and colours**, never names. ~18k lines in
the `brain_battle` package (`ui/` · `capture/` · `playback/` · `data/` ·
`support/`).

## Project situation

- **Hardware is currently broken.** Nothing in this project can be tested
  against real cameras or real Muse-S bands right now. All development and
  testing happens against a generated demo session (see
  `docs/ROADMAP.md` Phase 1) until hardware is available again.
- **Nobody has inspected raw Muse-S EEG from a real sparring round.** Signal
  usability — noise floor, motion artifact during actual boxing, whether the
  channels are even readable — is unknown. Don't assume the EEG is clean.
- **Timing target: under 10 ms between all devices after correction** (the
  professor's requirement; it replaces the earlier "don't invest in tighter
  sync" note). Cameras record at 60 fps with MJPG and a 1-frame buffer,
  headband timestamps are de-jittered per band, every recording has a
  `timing_quality.csv`, and the **buzzer test** on Get ready measures each
  device's delay and stores corrections (`timing_calibration`) that the XDF,
  `eeg_coregistered.csv` and Review apply
  ([ADR-0024](docs/decisions/adr-0024-timing-check-and-buzzer-calibration.md)).
  Verified only with simulated devices (recovered within ~1 ms); the first real
  session must confirm it. The synchrony analysis method itself is still
  undecided.
- Therefore: build and test everything against the generated demo session
  described in `docs/ROADMAP.md`, not against hardware that doesn't exist
  yet or data nobody has seen.

## Working agreement

- The owner approves each `docs/ROADMAP.md` phase before it starts. Never
  start the next phase on your own initiative.
- Commit locally per logical change, with a clear message.
- Run everything in `tests/` before and after every change
  (`python3 tools/run_tests.py` — one PASS/FAIL line per script), and report
  pass/fail per script — not just "I ran the tests."
- Follow the **branch policy** below for anything that reaches `main`.

### Branch policy

- **`develop` is the only branch anyone works on.** Every change — new work and
  bug fixes alike — is made and committed on `develop`.
- **`staging` is for the owner and a teammate to test.** It only ever receives
  merges from `develop`; nobody commits on it directly.
- **`main` holds approved versions only.** It only ever receives merges from
  `staging`, after the owner has approved what was tested there.
- **Bugs found while testing are fixed on `develop`,** and then a new version
  is merged from `develop` to `staging` (with a new `rc` tag, below).
- The flow: work on `develop` → run the full suite → commit per logical change
  → merge `develop` into `staging` and tag it → the owner and teammate test →
  on approval, merge `staging` into `main` and tag it. Never push to `staging`
  or `main` without the owner asking for it.

### Versions & tags

Git tags are how we find our way back to any earlier version of the project.

- **New tags are made only when a version moves to another branch:**
  - to `staging` → a release candidate, `vX.Y.Z-rc.N` (`v3.1.0-rc.1`,
    `v3.1.0-rc.2`, …);
  - to `main` → the release itself, `vX.Y.Z` (e.g. `v3.1.0`).
- **No more snapshot (`pre-…`) tags and no more `-dev.N` tags.** The existing
  ones stay as history.
- **Never delete a version tag (`v…`) or a milestone tag (`milestone/…`)**, and
  never move any tag.
- **Every new tag goes at the top of "Main checkpoints" in `README.md`**, with
  its date, short commit hash and a 2–3 sentence description. A tag that is no
  longer relevant moves down to "Older tags".

Tags are annotated (`git tag -a <name> <commit> -m "..."`) and pushed with
`git push origin <name>`.

**Look at an old version** (read-only; don't commit here):

```bash
git tag -l -n1            # list tags with their descriptions
git checkout <tag>        # e.g. git checkout v2.1.0
git checkout develop      # come back
```

**Start a branch from an old version** (to restore or build on it):

```bash
git switch -c restore-<name> <tag>
```

> **Warning — resetting a shared branch to a tag rewrites history.** Running
> `git reset --hard <tag>` on `develop` or `main` (and force-pushing) discards
> every commit after the tag for everyone. Only do this after asking the owner.
> Prefer a `restore-<name>` branch or a revert commit.

### Documentation is part of every change

**A change isn't done until its docs are.** Documentation ships in the same
change as the code it describes — not as a later step. The honesty tests
(`tests/test_docs.py`, `test_research_docs.py`, `test_developer_docs.py`) enforce
this, so skipping it turns the suite red. Checklist:

- **User guide** — update when the UI or any user-facing wording changes.
- **File formats** (`docs/research-and-data/file-formats.md`) — update when any
  recording/export file changes (a manifest key, CSV column, history event, zip
  entry); backtick the new name. (`test_research_docs.py`)
- **Module reference** (`docs/developer-guide/module-reference.md`) — update
  when a module is added, removed, or renamed.
  (`test_developer_docs.py`)
- **A new ADR** (`docs/decisions/`) — write one for any significant technical
  decision, and list it in `nav.yml`. (`test_developer_docs.py`)
- **Diagrams** — re-render (`python3 tools/render_diagrams.py`) when a `.mmd`
  changes. (`test_docs.py`)
- **CHANGELOG** — add an entry for every change; the top entry must match
  `brain_battle/version.py`. (`test_developer_docs.py`)

See `docs/contributing/docs-guide.md` "Documentation is part of every change" for
how each item is enforced.

## Product direction

**`docs/DESIGN.md` is the source of truth for ALL UI work** — the actual
screens, copy, vocabulary, tokens, and layout. (First approved 2026-10-27;
rewritten 2026-11-13 for the redesign, ADR-0018, and awaiting the owner's
review with the rest of `develop`.) If code and DESIGN.md disagree, ask; don't guess.
`docs/ROADMAP.md` is the build plan/sequencing; DESIGN.md is what it must look
and read like. Summary of where the product is headed:

- **Audience is non-technical researchers and research assistants**, not
  developers. UI copy should be plain language, no jargon ("alignment",
  "capture clock", "xcorr" are implementation details, not UI vocabulary).
  **One deliberate exception:** the brain-activity display is a conventional
  EEG viewer and shows the standard electrode names (TP9, AF7, AF8, TP10, AUX),
  microvolts and a time axis, because the people who must judge the signal need
  them ([ADR-0016](docs/decisions/adr-0016-standard-eeg-viewer.md)). Channel
  codes stay out of every other piece of UI.
- **Light mode only.** There is no dark theme or theme toggle.
- **Three screens plus Settings:** Sessions (home library of past/current
  sessions) → New recording (Get ready → Recording → Saved) → Review & Label
  (one merged screen — recording and labelling are not separate tabs), plus a
  Settings screen (your name, folder, the shared label list and fighter
  colours, Advanced). A top header, no sidebar; a first-run screen asks once
  where the team's recordings are.
- **Alignment is fully automatic and invisible to the user** — there is
  nothing to click to "sync."
- **Several labellers, no accounts.** A labeller is a typed name ("Your name",
  asked once). Each labeller's labels are their own file in the recording; you
  only ever edit your own ([ADR-0019](docs/decisions/adr-0019-one-labels-file-per-labeller.md)).
- **Mac and Windows.** Anything platform-specific goes through
  `support/platform_utils.py`; never write "Finder" or a `%-d` date code in a
  screen (`tests/test_platform.py` fails if you do).
- **Fighters are codes and colours, never names** — `K7Q2`, 4 × A–Z/0–9, in
  the manifest, folder name, `recording.xdf` and exports; nothing to hide, so
  name hiding is gone. Old recordings with names still open
  ([ADR-0023](docs/decisions/adr-0023-fighter-codes-and-colours.md), superseding
  ADR-0021).
- **Recording starts 15 s before the baseline** (`LEAD_IN_SEC`; tests use 2 s
  via `tests/_env.py`); system markers `RECORDING_START` … `RECORDING_END` are
  written automatically; moments carry an optional note; custom notes are
  labels with `type: "note"`.
- **The built-in `recording.xdf` is the primary analysis route** (owner
  decision); LabRecorder is an optional, advanced extra and the docs say so.

## Running

```bash
pip install -e .          # supported install for now (reads pyproject.toml)
python3 -m brain_battle    # or: brain-battle  (or the shim: python3 brain_battle_main.py)
python3 tools/run_tests.py # every test script, several at a time (~5½ min)
python3 -m brain_battle.data.session_xdf "<recording folder>"   # (re)build its recording.xdf
```

System binaries required: `ffmpeg` (per-camera H.264 **recording**, plus
export/probing — recording is blocked at pre-flight without it). Review &
Label playback is in-process via **PyAV** (`av`, bundles its own ffmpeg) — no
VLC / python-vlc dependency. Recordings land in `~/Documents/Brain Battle Data`
by default (change it in Settings, or override with `BRAIN_BATTLE_CAPTURE_DIR`);
the default is defined only in `brain_battle/paths.py`
([ADR-0014](docs/decisions/adr-0014-documents-default-folder.md)).

## Demo session

Hardware is broken (see "Project situation" above), so every phase of
`docs/ROADMAP.md` builds and tests against a **generated demo session**
instead:

```bash
python3 tools/generate_demo_session.py --out /tmp/demo              # one session (2 rounds, seed 0)
python3 tools/generate_demo_session.py --out /tmp/demo --rounds 3 --seed 7
python3 tools/generate_demo_session.py --out /tmp/demo --interrupted  # looks like a mid-round crash
python3 tools/generate_demo_session.py --out /tmp/library --library   # 6 recordings, every Sessions status
python3 tools/demo.py              # that library in ~/Brain Battle Demo + the app on it (simulated devices)
```

`--out` is **required** — a demo must never land in your real recordings
folder by accident. The generator also **refuses** an `--out` that equals the
configured recordings folder unless you pass `--force` (the
`_refuse_real_folder` guard), so demo data can't be mistaken for real captures.
`--library` fills `--out` with 6 recordings covering every status — two
labellers on one (Compare), custom notes, moments with and without notes, a
weak-signal stretch ("Worth checking"), one stopped early without a buzzer test
("Timing not measured"), one interrupted, one exported; fighter codes only, no
name of any kind in any file. `tools/demo.py` makes it once and opens the app
on it (`--fresh` regenerates); it never touches real recordings or settings.

Each run writes a real v3 session folder — 4 camera MP4s + sidecars (one camera
deliberately drifts: 30fps declared container vs. ~29.5fps true capture rate),
2 boxer EEG CSVs with plausible synthetic signal plus their movement-sensor
CSVs, `phase_markers.csv`, `session_manifest.json` (`demo:true`) and
`recording.xdf` — by reusing the real writers (`session_storage`,
`usb_camera.FrameEncoder`, `_MuseDirectReader`'s chunk/flush path,
`session_xdf.build_session_xdf`) wherever they're headlessly callable, so it can't silently drift from the format the app actually
reads/writes. Deterministic for a given `--seed`; the default config generates
in well under a minute.

**This is also the eyes-on-glass fixture** deferred work item #1 has been
waiting for: every camera shows a full-frame white flash and every EEG channel
gets a sharp spike at each phase-marker instant, so you can open a generated
session from **Sessions → Review & Label** and visually confirm all four
cameras + both brain-activity traces land on the same instant at each
flash/spike (alignment is automatic — there is nothing to click to "sync"). It
is the same check a real capture session needs, without needing hardware to
produce the fixture. `tests/test_demo_session.py` automates the arithmetic side
(sidecar/MP4 frame counts, EEG columns/rate, and that the computed alignment
seeks match the generator's own start delays exactly).

## Tests (`tests/` — plain scripts, `python3 tests/<name>.py`, nonzero exit on failure)

Every script imports `tests/_env.py` first (a throwaway settings file with a
labeller name, so a run never touches real settings; and its own LSL tag, so a
script only ever subscribes to its own streams — use `_env.lsl_inlet`). The whole suite also runs
on macOS and Windows in GitHub Actions (`.github/workflows/tests.yml`).

All need ffmpeg on PATH; playback/decoding is PyAV (`av`), no VLC. Run them all
with `python3 tools/run_tests.py` (several at a time, one PASS/FAIL line per
script, ~5½ min; `python3 tools/run_tests.py xdf player` runs only matching
scripts; `test_timing` and `test_stability` always run last, alone; a script
that fails while others are running is re-run alone before it counts as failed; a script still running after 15 minutes is stopped and counts
as failed).


What each script covers is in its own docstring and in
`docs/developer-guide/testing-strategy.md`. `test_smoke.py` is the acceptance
gate for deletions/refactors; `test_no_placeholders.py`, `test_light_theme.py`,
`test_platform.py` and the three docs-honesty scripts are the rule gates.

## Project status

**`develop` = `3.1.0-dev` (last tag `v3.1.0-dev.3`); `main` = the reviewed
`3.0.0-beta.2`.** `develop` has the four DESIGN screens (Sessions, New
recording, Review & Label, Settings) plus the 3.1 work: live LSL streams and
one `recording.xdf` per recording, a standard EEG viewer, one capture clock
(`clock.monotonic()` = `time.perf_counter()`), several labellers per recording,
Mac + Windows, a shared recordings folder, the open-files fix (pylsl 1.18.7),
and — since the last tag — design F (top header, new screens), fighter codes
and colours (ADR-0023), the 15 s lead-in and system markers, moment notes and
custom notes, the bell at both ends of a round, the timing check and buzzer
calibration (ADR-0024), a camera watchdog and a soak test (`tools/soak_test.py`). GitHub Actions is
green on macOS and Windows (the two old Windows first-attempt flakes were fixed
at the cause on 2026-11-23; see `docs/developer-guide/cross-platform-and-ci.md`). Hardware is broken, so
everything runs against **simulated** devices (`capture/simulation.py`,
`BRAIN_BATTLE_SIMULATE`, dev-only). Phase 4 (labelling) shipped inside the
Review & Label build. Pass-by-pass history: `CHANGELOG.md` and
`docs/project/status.md`; long-form history: **`docs/archive/history.md`**.

**What's next:** the owner reviews `develop` (start with
`docs/reviews/2026-11-redesign.md`); if happy, it is merged to `staging` (tag
`v3.1.0-rc.1`) for the owner and a teammate to test, then from `staging` to
`main` (tag `v3.1.0`). The most valuable next step is not code: **one real
recording** with the buzzer/tap check. Then the rest of **Phase 5** in
`docs/ROADMAP.md`. **Phases 6 (shipping) and 7 (team & cloud) start only on
the owner's explicit go-ahead.** Nothing starts on its own initiative.

## Module map

Everything lives in the **`brain_battle` package**, grouped by responsibility
into `ui/` · `capture/` · `playback/` · `data/` · `support/` (run it with
`python3 -m brain_battle`; `brain_battle_main.py` at the repo root is a thin
launcher shim). The per-module reference — one entry per `.py`, enforced by
`tests/test_developer_docs.py` — is `docs/developer-guide/module-reference.md`;
each module's docstring says the same in brief.

## Deferred work — hardware-blocked verification

Everything below is blocked specifically on real hardware — it cannot be closed
by writing more code. This is NOT the project backlog (that's
`docs/ROADMAP.md`); these are the loose ends that need a real capture session.

0. **UNVERIFIED: EEG capture-time alignment.** EEG samples are placed on the
   shared session axis by `t_session = sample_time - capture_t0_unix`, where
   both come from the one capture clock (`capture/clock.py`). The *arithmetic*
   is unit-tested (`tests/test_capture_clock.py`, `tests/test_xdf.py`), but two
   things are not: muselsl spreads each packet's arrival time over its samples
   with a per-band regression, and BLE transport adds latency — neither is
   confirmable without a real Muse band. *Why it matters:* between-brain
   synchrony timing depends on where each band sits on the axis. The app logs
   this warning at every EEG session start (labeled in `capture/eeg_module.py`).
   *Unblocked by:* a real 2-band session — record a known simultaneous physical
   event (e.g. both boxers tap in view of a camera at one buzzer), then check
   the EEG artifact lands at the same `t_session` as the camera frame. **The
   exported `eeg_coregistered.csv` and `recording.xdf` inherit this** — their
   between-brain skew is measured-but-hardware-unverified; the same buzzer check
   discharges it too.

3. **UNVERIFIED: a real Windows computer and a real network drive.** Windows is
   covered only by the GitHub Actions run with simulated devices. Cameras
   (DirectShow), Bluetooth (WinRT), hardware encoders, Explorer, display
   scaling, and a Mac + PC sharing one SMB folder have never been tried. List:
   `docs/developer-guide/cross-platform-and-ci.md`.

1. **Eyes-on-glass visual check — real signals.** Synced playback (decode,
   4-cam lockstep, automatic capture-clock alignment) and the brain-activity
   traces are verified headless to correct-pixels + measured scrub perf +
   alignment arithmetic (unit tests), but nobody has *watched* the screen render
   aligned playback on real data — offscreen Qt can't put pixels on glass, and
   synthetic fixtures aren't real signals. *Unblocked by:* the first real
   capture session. **Procedure:** `python3 -m brain_battle` → open a
   session from **Sessions** → it lands in **Review & Label** (alignment is
   automatic — nothing to click to "sync") → press play and confirm all four
   cameras run in lockstep → scrub and watch both brain-activity traces follow
   → click a phase marker and confirm the playhead + traces land at that event.
   The sharp check: a marker at a known physical event (round buzzer) should
   line up with that event in the video AND the EEG artifact (this also
   discharges #0's EEG-clock check). The generated demo session (above) is the
   stand-in fixture for everything here except the real-signal question.

2. **UNVERIFIED: movement / pulse sensors and live LSL with real headbands.**
   The accelerometer, gyroscope and pulse (PPG) recording go through muselsl's
   standard callbacks and are exercised end to end with *simulated* headbands,
   but have never been received from a real band here; the pulse sensor also
   assumes the headband's "p50" preset (`_MuseDirectReader.run`), which is why
   it is off by default. Nobody knows yet whether the extra Bluetooth traffic is
   comfortable with two bands. *Unblocked by:* a short real session with the
   switches on (Settings → Extra signals and sharing) and LabRecorder running
   alongside; check the `Muse sensors/` CSVs fill at ~52 Hz and the EEG stays
   stable. If not, switch them off in Settings — no code change needed.

Not hardware-blocked, but noted: the drift camera the demo generates (30fps
declared vs ~29.5fps true) is handled by playback and by `recording.xdf` (both
use every frame's own time) and is now reported to the operator; the one
remaining seam — the optional combined 2×2 export video — is tracked in
`docs/ROADMAP.md` Phase 5, not here.

## Conventions & gotchas

- **Each camera captures in a process of its own** (`capture/camera_worker.py`,
  ADR-0025) so nothing in the app can delay it. Anything a camera process must
  know goes through `BRAIN_BATTLE_*` environment variables or the command pipe
  — patching a module in the app (or a test) does not reach it; use
  `USBCameraThread(isolated=False)` in a test that must patch capture code.
- Review & Label playback is in-process via **PyAV** (`playback/pyav_player.py`):
  one `t_session_seconds` playhead, decode off the UI thread, no VLC/Qt-Multimedia
  players and no native-surface z-order hacks.
- The per-phase white flash is NOT perfectly simultaneous across the four
  cameras — each camera captures on its own phase, so the first frame at/after a
  phase marker lands up to one frame period (~34 ms) apart. This is expected and
  correct; `tests/test_flash_sync.py` asserts the spread stays within one frame.
- Light mode only — there is no theme machinery (no dark palette, no
  `_apply_theme`); `test_light_theme.py` guards that it stays gone.
- `capture/session_controller.py` is deliberately Qt-optional (headless-testable).
- `phase_markers.csv` labels look like `BASELINE_START`, `ROUND_1_START`,
  `BREAK_1_START`, `SESSION_END`; `t_session_seconds` is monotonic time since
  session start — the natural sync base for in-app captures.
- Repo-relative paths (assets, docs, the default recordings folder) are all
  resolved through `brain_battle/paths.py` — don't re-derive them from a
  module's own `__file__`.
- **One clock.** Stamp anything new with `clock.monotonic()` (from
  `capture/clock.py`) and convert through that module; never `time.monotonic()`
  (on Windows with Python ≤ 3.12 it ticks only every 15.6 ms and has a
  different zero) and never `time.time()` for a timestamp that will be compared
  with another source. `tests/test_capture_clock.py` checks the stamping sites.
- **`recording.xdf` is derived, never a source.** The CSVs, sidecars, markers,
  manifest and `labels.json` are the originals; the XDF is rebuilt from them.
  Don't read it back inside the app, and don't add data that exists only there.
- **LSL is fail-soft.** Nothing in recording may depend on an outlet opening.
- **Never destroy an LSL outlet to "clean up".** liblsl keeps 3 descriptors for
  every outlet destroyed; outlets are opened once per run and reused
  (`capture/lsl_outlets.py`, ADR-0022). After touching anything that owns a
  file, socket, pipe, thread or subprocess, run
  `python3 tools/measure_descriptors.py` — every recording must print the same
  numbers.
- **A write of the recording's own bookkeeping goes through
  `RecordFlow._essential(...)`**, so a failure reaches the operator (banner on
  Recording, a non-clean Saved screen) instead of only the log.
- **Name only bundled fonts** (`dt.FONT_UI`, `dt.FONT_MONO`); a family the
  computer lacks makes Qt print a "missing font family" warning.
- **One labels file per labeller; never write another labeller's file.** Read
  everyone with `labels_store.read_all`; write only through
  `open_for_labeller(root, <current name>)`.
- **Write shared files through `data/safe_io.py`** (atomic, and locked for
  read-change-write). Never create a recording's folder as a side effect of
  saving into it — if it is gone, fail and say so.
- **Connect worker-thread signals to bound methods, not lambdas** — a lambda
  keeps firing into a destroyed widget (this crashed Sessions).
- **Build screens from `ui/widgets.py`**, with at most one primary button each.
- **Unscoped Qt stylesheets leak.** `w.setStyleSheet("background: …")` applies
  to every descendant; give the widget an object name and write
  `#name { … }` instead (this caused grey boxes behind labels until 3.1).
