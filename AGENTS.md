# Brain Battle — v3.1.0-dev (develop) · v3.0.0-beta.2 (main)

> **Agent instructions.** This file is the shared, tool-neutral instructions
> file for AI coding agents (read automatically by OpenAI Codex, and by Claude
> Code via a one-line `CLAUDE.md` that imports it). It is the **single source**
> of these instructions — do not duplicate its content elsewhere. It describes
> the **present** state of the project; `docs/project/history.md` keeps the
> long-form narrative.

PyQt6 desktop app for neuroscience boxing capture: 4 USB cameras + 2 Muse-S EEG
headbands (Bluetooth). Four screens behind a light sidebar shell — **Sessions**
(home library), **New recording** (a guided capture flow), **Review & Label**
(one merged playback + labelling screen), and **Settings** — plus an in-app
**Documentation** screen and an export pipeline. It follows the standard Muse
research workflow: live **LSL** streams while recording and one
**`recording.xdf`** per recording (EEG + markers + labels + per-frame video
times on one clock) for EEGLAB / MNE / pyxdf. More than one person can label
the same recording (one labels file per labeller); the lab runs it on **Mac and
Windows**; the recordings folder may be shared between computers. ~14k lines in
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
- **The synchrony analysis method is undecided and has no owner yet.** This
  matters directly for how much more sync-precision work is worth doing:
  phase-based measures (PLV, coherence) need alignment accurate to a few ms;
  power/envelope-based measures tolerate tens of ms (what the current
  capture-clock design already targets). **Don't invest in tighter sync
  precision until this is answered** — it would be optimizing a number nobody
  has confirmed matters.
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

- **`main` is the stable, reviewed version.** It only ever advances when the
  owner has reviewed and approved the work, and each such merge is marked with
  a version tag (e.g. `v3.0.0-beta.2`).
- **`develop` is the staging area.** All new work happens on `develop` first.
- The flow: work on `develop` → run the full suite → commit per logical change
  → the owner reviews → on approval, merge `develop` into `main` (a merge
  commit) and tag the release. Don't push to `main` without that approval.

### Versions & tags

Git tags are how we find our way back to any earlier version of the project.

- **Every merge into `main` gets a version tag** — `vX.Y.Z` or `vX.Y.Z-beta.N`
  (e.g. `v3.0.0-beta.2`), matching `brain_battle/version.py`.
- **A snapshot tag is created on `develop` before any large or autonomous
  change** — `pre-<task>-<date>` (e.g. `pre-fable-2026-11-13`), so there is
  always a known-good point to return to.
- **`milestone/<name>` tags mark finished roadmap phases** (e.g.
  `milestone/phase-2-data-safety`, `milestone/phase-3-redesign`).

Tags are annotated (`git tag -a <name> <commit> -m "..."`) and pushed with
`git push origin --tags`. Never move or delete an existing tag.

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
- **Module reference** (`docs/developer-guide/module-reference.md`) + this file's
  module map — update when a module is added, removed, or renamed.
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
  Settings screen for everything that used to be hardcoded in source
  (boxer↔headband mapping, resolution/fps, label vocabulary, round bell).
- **Alignment is fully automatic and invisible to the user** — there is
  nothing to click to "sync."
- **Several labellers, no accounts.** A labeller is a typed name ("Your name",
  asked once). Each labeller's labels are their own file in the recording; you
  only ever edit your own ([ADR-0019](docs/decisions/adr-0019-one-labels-file-per-labeller.md)).
- **Mac and Windows.** Anything platform-specific goes through
  `support/platform_utils.py`; never write "Finder" or a `%-d` date code in a
  screen (`tests/test_platform.py` fails if you do).
- **Boxer names are kept by default** (manifest, `recording.xdf`, exports) and
  can be hidden per export or on screen
  ([ADR-0021](docs/decisions/adr-0021-boxer-names-and-hiding.md)).
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
python3 tools/generate_demo_session.py --out /tmp/library --library   # ~5 sessions, every Sessions status
```

`--out` is **required** — a demo must never land in your real recordings
folder by accident. The generator also **refuses** an `--out` that equals the
configured recordings folder unless you pass `--force` (the
`_refuse_real_folder` guard), so demo data can't be mistaken for real captures.
`--library` fills `--out` with ~5 short sessions covering every status
(interrupted, needs labelling, labelled, exported, no-name) — handy for the
Sessions screen.

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
labeller name, so a run never touches real settings). The whole suite also runs
on macOS and Windows in GitHub Actions (`.github/workflows/tests.yml`).

All need ffmpeg on PATH; playback/decoding is PyAV (`av`), no VLC. Run them all
with `python3 tools/run_tests.py` (several at a time, one PASS/FAIL line per
script, ~5½ min; `python3 tools/run_tests.py xdf player` runs only matching
scripts; a script that fails while others are running is re-run alone before it
counts as failed).

- `test_xdf.py` — the XDF writer and `recording.xdf` read back with **pyxdf**
  (bit-exact EEG, exact frame times, markers, labels-as-events, the
  crash-truncated-row case, older recordings), `eeg_files` boxer↔file mapping
  from the manifest, `camera_frame_rates`, and the one capture clock against
  `pylsl.local_clock()`.
- `test_lsl_streams.py` — the live LSL outlets as an ordinary pylsl inlet sees
  them (stream descriptions, intact samples, LSL-clock timestamps), a disabled
  publisher, and a simulated headband publishing through the real path.
- `test_eeg_viewer.py` — the EEG viewer: window follows playhead / newest
  sample, the µV scale is what the scale bar says, the display filter never
  touches data, markers are painted, the Review card's controls.

- `test_smoke.py` — the "loads and stands up" floor and **acceptance gate for
  deletions/refactors**: fresh-subprocess import of every first-party module,
  import-time `_HW_ENCODER` == post-import detection, headless construction of
  the screens (RecordFlow, ReviewLabelScreen, SessionsScreen, SettingsScreen) + the
  `BrainBattleApp` shell, `__version__` in the window title. Run before/after
  any deletion/refactor.
- `test_player.py` — the PyAV single-clock player: frame-for-t correctness vs a
  ground-truth decode (incl. the drift camera's last frame), real-time pacing
  (±2% over ~10 s), scrub coalescing (50 seeks → a couple of decodes), paint fps.
- `test_review_tab.py` — the merged screen's label side: fast labels-first open
  (<0.5 s, media in the background), duration flow + Esc, undo/redo, keyboard
  focus rules, autosave + history, seeded vocabulary.
- `test_labels_v2.py` — label entry schema, v1 migration (+ `.v1.bak`),
  undo/redo, events timeline.
- `test_labellers.py` — several labellers: one file each, automatic migration of
  an old `labels.json` + "These are mine", merge-on-save (one person, two
  computers), agreement, Compare in Review & Label, the ask-your-name gate,
  moments for every labeller, per-labeller counts in Sessions.
- `test_privacy.py` — boxer names in `recording.xdf` by default; a name-free
  export has no name in ANY file or file name; the Settings default; hide names
  on screen.
- `test_platform.py` — Mac/Windows: dates without `%-d` (and no such code
  anywhere in the source), Finder vs. Explorer (and no hard-coded "Finder"),
  shortcut wording, settings/recordings paths, Windows-safe names, encoder
  candidates + libx264 fallback, keep-awake never raising, the CI workflow.
- `test_shared_folder.py` — atomic writes, the folder lock, five processes
  labelling one recording at once, concurrent XDF builds, a folder that
  disappears (Sessions banner, Review keeps unsaved labels and retries, no
  recording into it), the scan cache.
- `test_label_vocab.py` — the shared `label_list.json` (seed from DESIGN
  defaults, round-trip, tolerant load).
- `test_contact_quality.py` — the per-sensor heuristic (flat/clipped/noisy/good)
  and its plain-language summary.
- `test_export.py` — the analysis bundle: zip contents (labels.csv v2,
  events.csv, eeg_coregistered.csv, raw EEG + videos with sidecars, manifest,
  history, README) and events.csv ordering.
- `test_flash_sync.py` — verify-only: at every phase marker of a 12-min demo the
  white flash is within one frame period (≤34 ms) across the four tiles.
- `test_no_placeholders.py` — acceptance gate: ZERO placeholder/temporary UI
  copy, no "UPLOAD" text, zero placeholder screens left, and EEG channel codes
  nowhere except `ui/eeg_viewer.py`.
- `test_capture_clock.py` — capture-clock reconciliation arithmetic, two-band
  relative skew, marker/EEG co-registration, manifest bridge round-trip, the
  real EEG reader flush path, and the co-registered EEG export.
- `test_demo_session.py` — `tools/generate_demo_session.py` end to end: sidecar
  rows == MP4 frames (incl. the drift camera), EEG columns/rate, a headless
  `ReviewLabelScreen.load_session_folder`, the `--interrupted` fixture, marker-token
  forms, `demo:true` manifests, and the generator's refuse-real-folder guard.
- `test_sessions.py` — Sessions discovery/status/search/filter/sort, the
  interrupted banner + dismissal persistence, off-UI-thread scanning, demo
  badges, and the strict layout at both sizes.
- `test_ring_buffer.py` / `test_eeg_streaming.py` / `test_video_recording.py` /
  `test_crash_recovery.py` / `test_history_log.py` — ring-buffer ordering; the
  streamed EEG recorder under SIGKILL; crash-safe H.264 recording; interrupted-
  session detection + disk-space branches; the append-only history log + its
  hooks (recording start/finish, opened_for_review, labels add/remove, driven
  through ReviewLabelScreen).
- `test_light_theme.py` — light-only guard (no dark tokens / theme machinery).
- `test_layout_fit.py` — every shell page fits (nothing clipped) at 1440×900,
  1280×800 AND the minimum window size; a visible scrollbar is chrome, not an
  overflow.
- `test_record_flow.py` — the guided flow end to end with simulated devices:
  all four steps → a valid recording (sidecars, EEG + movement CSVs, markers,
  manifest with names + moments, history, contact log) shown in Sessions and
  opened in Review with markers + moments; the live LSL streams seen by a real
  inlet during it, and the resulting `recording.xdf` read back with pyxdf; stop early; missing camera; headband
  drop+reconnect; disk-blocked start; quit-while-recording confirmation; sleep
  prevention requested/released; per-step layout fit at all three sizes; and no
  simulation text when `BRAIN_BATTLE_SIMULATE` is unset.
- `test_app_settings.py` — the settings store (defaults, round-trip) and the
  `BOXER_LABELS` removal (now `eeg_module.boxer_labels()` from settings).
- `test_settings_screen.py` — the real Settings screen (DESIGN §7): bitrate
  computed (not hard-coded) into the GB/hour copy, the disk estimate, and the
  recorder's own `-b:v`; known-headbands + the same-headband guard; every
  section's autosave; the label-list editor (add/rename/remove/reorder/
  recolour, keys renumbered 1..n); and a headless end-to-end — change
  defaults/quality/label list in Settings, start a (simulated) recording, and
  confirm Set up/the recorder/the disk estimate/Review & Label all reflect it.
- `test_contact_log.py` — per-sensor contact log weak-period spans + the >30 s
  "worth checking" filter.
- `test_round_bell.py` — `assets/sounds/round_bell.wav`'s duration/true-peak/
  loudness; `SessionController.beep_requested` fires at baseline + every round
  start and never on a break; Settings' Test sound plays the bundled file; a
  missing WAV stays silent instead of crashing; the credits entry exists.
- `test_docs.py` — the in-app Documentation screen + `docs/` content: every
  `nav.yml` page exists and renders; every image/diagram it references exists;
  internal links/anchors resolve; diagram freshness (`.mmd` vs. its rendered
  `.svg`/`.png`/`.hash`); search finds a known phrase per page + highlights it;
  Help (?) buttons route to the right page/anchor from Sessions, each
  record-flow step, Review & Label, and Settings; strict layout fit at the
  three sizes; and the placeholder gate's scope contract (it must not scan
  `docs/*.md`).

## Project status

**Current state: `develop` = `3.1.0-dev` (tag `v3.1.0-dev.2`), awaiting the
owner's review; `main` = the reviewed `3.0.0-beta.2`.** The owner will test
`develop` before deciding on a merge. Phase 3 and the
documentation were complete at 3.0.0-beta.2: the unified capture-time clock
shipped and the app was built onto the four DESIGN screens — Sessions, New
recording, Review & Label, and Settings — all inside the `brain_battle` package.
Review & Label is one merged PyAV-driven screen (single `t_session_seconds`
playhead); labels are `labels.json` v2 with undo/redo and a shared vocabulary;
Sessions is the home library; export produces an analysis bundle. New recording
is the guided flow (`ui/record_flow.py`, DESIGN §2–§5: Connect → Set up → Record
→ Saved); it records moments (M), prevents sleep, logs per-sensor contact
quality, and stops cleanly early. Settings (`ui/settings_screen.py`, DESIGN §7)
owns the recordings folder, headbands, video quality, session defaults + round
bell, extra signals + LSL sharing, and the shared label list — all autosaving.

**3.1.0-dev (the November 2026 improvement pass —
`docs/reviews/2026-11-fable-review.md`) added the standard EEG workflow:**
- **One capture clock** (`capture/clock.py`, ADR-0017): everything is stamped
  from `time.monotonic()`; Unix and LSL time are fixed offsets from it.
- **Live LSL streams** (`capture/lsl_outlets.py`, ADR-0015): EEG / movement /
  pulse per boxer, markers, and a frame-time stream per camera — visible to
  LabRecorder and LSL viewers while the New recording screens are open.
- **`recording.xdf` per recording** (`data/session_xdf.py` + the built-in
  `data/xdf_writer.py`): derived from the crash-safe files after recording,
  rebuilt with labels on export, built for crashed recordings when found.
- **A conventional EEG viewer** (`ui/eeg_viewer.py`, ADR-0016) in Review & Label
  and on the Connect / Recording steps.
- **Headband movement sensors recorded** (accelerometer + gyroscope by default,
  pulse opt-in) and real battery telemetry.
- ~1,000 lines of dead legacy EEG-widget code and the `pyqtgraph` dependency
  removed.

**3.1.0-dev, second pass (the November 2026 redesign —
`docs/reviews/2026-11-redesign.md`, tag `v3.1.0-dev.2`, snapshot
`pre-redesign-2026-11-13`):**
- **Every screen redesigned** (ADR-0018; `docs/DESIGN.md` rewritten;
  `ui/widgets.py` is the shared component set). New recording is three screens:
  Get ready → Recording → Saved.
- **Several labellers per recording** (ADR-0019): `labels/<labeller>.json`,
  "Your name", Compare + agreement, automatic migration of `labels.json`.
- **Boxer names kept, hideable** (ADR-0021): in `recording.xdf` by default;
  per-export and on-screen hiding (`data/privacy.py`).
- **Mac + Windows** (`support/platform_utils.py`, CI on both) and **a shared
  recordings folder** (ADR-0020, `data/safe_io.py`).

Hardware is broken, so the whole flow is built + tested against **simulated**
devices driven through the real capture paths (`capture/simulation.py`,
`BRAIN_BATTLE_SIMULATE`, dev-only, not shipped).

**The in-app documentation is complete** — all six sections (Getting started,
User guide, Research & data, Developer guide, Decisions/ADRs, Project) render in
the Documentation screen (`ui/docs_screen.py` + `support/docs_nav.py`) from the
Markdown under `docs/` at runtime (no QtWebEngine — Qt's own
`QTextDocument.setMarkdown`), with a nav tree, always-visible search, and Help
(?) buttons that jump to the right page. Diagrams are Mermaid source
(`docs/diagrams/*.mmd`) rendered via `tools/render_diagrams.py` to `.svg`
(GitHub) and `.png` (what Markdown references); screenshots are generated via
`tools/render_screens.py --docs`.

**Phase 4 (Labelling) was delivered inside the Phase 3 Review & Label build**:
every label is tied to a boxer (A/B/Both), labels are point or duration events
on the numeric `t_session_seconds` axis, a shared team vocabulary is stored with
the sessions (not committed to the repo), and frame-stepping, 0.25×/0.5×
playback, undo/redo and an editable list of placed labels all ship in
`ui/review_label_screen.py` + `data/labels_store.py` + `data/label_vocab.py`.

**What's next:** the owner reviews `develop` (start with
`docs/reviews/2026-11-redesign.md` — it lists the decisions waiting for
them; the earlier pass is `docs/reviews/2026-11-fable-review.md`) and, if happy, merges it to `main` with a version tag. The most valuable
next step is not code: **one real recording** with the buzzer/tap check. After
that, the rest of **Phase 5** in `docs/ROADMAP.md` (per-round clip export, a
whole-recording EEG quality summary, a drift-proof combined video, a
clock-driven round timer). **Phases 6 (shipping) and 7 (team & cloud — a server is
planned; design notes are in ROADMAP Phase 7) start only on the owner's
explicit go-ahead.** Nothing starts on its own initiative.

For the long-form history behind the current architecture — the unified
capture-time clock build, the package reorganisation, the prior functional-fixes
/ cleanup phase, and the v1.x→v2.x recording-model migration — see
**`docs/project/history.md`** (archived narrative under `docs/archive/`).

## Module map

Everything lives in the **`brain_battle` package**, grouped by responsibility
into `ui/` · `capture/` · `playback/` · `data/` · `support/`. Run it with
`python3 -m brain_battle` (or the `brain-battle` console script after
`pip install -e .`); `brain_battle_main.py` at the repo root is a thin launcher
shim.

| File (under `brain_battle/`) | Purpose |
|---|---|
| `app.py` | `BrainBattleApp` shell (window sizing + QSettings, page wiring) + `main()` entry point |
| `version.py` | Single authoritative `__version__` (window title, manifest, pyproject reads it) |
| `paths.py` | Single source of repo-relative paths (`REPO_ROOT`, `ASSETS_DIR`, `DOCS_DIR`) + the default recordings folder (`~/Documents/Brain Battle Data`) |
| `ui/sidebar_shell.py` | The app's frame: 244px sidebar (New recording as the primary button, who is labelling, Hide names, folder card), 72px rail on Review & Label, hidden for the full-screen record flow; app opens on Sessions |
| `ui/widgets.py` | The shared component set every screen uses: text helpers, `Card`, `Banner`, button factories, `Segmented`, `StatusLine`, pills, boxer badges, `TimerLabel` |
| `ui/record_flow.py` | The New-recording flow (DESIGN §2–§4): Get ready (devices `ConnectStep` + plan `SetupStep` on one page) → Recording → Saved; owns the device slots + SessionController; emits `session_saved` / `exit_flow` |
| `ui/review_label_screen.py` | The merged Review & Label screen (DESIGN §5): labels-first open, PyAV video grid, the foldable EEG card, the pinned transport + timeline, labelling as yourself, Compare with another labeller (`ReviewLabelScreen`) |
| `ui/eeg_viewer.py` | The EEG display — a conventional EEG viewer: stacked TP9/AF7/AF8/TP10/AUX traces in µV, scale bar, time axis, event markers; review (playhead-centred) and live (scrolling) modes; `BandPowerBars`. The one module allowed to show channel codes |
| `ui/sessions_screen.py` | Sessions home library (clickable rows, status tabs, search/sort, who labelled, per-row export, interrupted + folder-unreachable banners, background scan + sizing) (`SessionsScreen`) |
| `ui/settings_screen.py` | The Settings screen (DESIGN §7): section list; You (your name), recordings folder, headbands, video quality, session defaults + bell, extra signals + LSL sharing, names and privacy, shared label-list editor; autosaves (`SettingsScreen`) |
| `ui/docs_screen.py` | The in-app Documentation screen: Markdown rendering via `QTextBrowser`/`QTextDocument.setMarkdown` (no QtWebEngine), nav tree, always-visible search with highlighting, back/forward (`DocsScreen`) |
| `ui/design_tokens.py` | **Light-only** design system (docs/DESIGN.md tokens) + QSS factories |
| `ui/fonts.py` | Registers the bundled Inter font (from `assets/fonts/` via `paths.ASSETS_DIR`) |
| `capture/usb_camera.py` | `USBCameraThread` (cv2, or a simulated capture) + `FrameEncoder` — crash-safe fragmented H.264 per-camera ffmpeg subprocess + row-per-frame sidecar; no audio; preview downscale |
| `capture/camera_source.py` | `CameraSlot` — one camera for the flow (wraps `USBCameraThread`: preview, fps, recording/dropped/encoder state) |
| `capture/headband_source.py` | `HeadbandSlot` — one boxer's headband (wraps `_MuseDirectReader`: connection, battery, per-sensor contact, live EEG buffer, reconnect/append, pushes to the LSL publisher) |
| `capture/clock.py` | The one capture clock: `time.monotonic()` plus fixed offsets to Unix and LSL time; `session_origin()` |
| `capture/eeg_module.py` | Muse-S Bluetooth scan/connect/stream (`_MuseDirectReader`: EEG, movement + pulse sensors, battery), crash-safe `_CsvStreamWriter`, `_RingBuffer` |
| `capture/lsl_outlets.py` | `LslPublisher` — live LSL outlets (EEG/sensors per boxer, markers, per-camera frame times); fail-soft |
| `capture/session_controller.py` | Pure-logic phase state machine (headless-testable), 1 Hz QTimer |
| `capture/simulation.py` | **Dev-only, not shipped** — `BRAIN_BATTLE_SIMULATE` simulated cameras + headbands (EEG, movement, pulse, battery) through the real capture paths, so the CSVs, LSL streams and XDF all come out of simulation; failure injection (missing camera, headband drop) |
| `capture/contact_quality.py` | Per-sensor EEG contact-quality heuristic (plain language) |
| `capture/contact_log.py` | Per-sensor contact-quality log written during recording + weak-period summary for Saved/Review |
| `capture/keep_awake.py` | Prevent sleep while recording (caffeinate / SetThreadExecutionState) |
| `capture/round_bell.py` | `RoundBell` — round-start boxing bell via `QSoundEffect` |
| `playback/pyav_player.py` | `PyAVCamera` / `MultiCamDecoder` / `DecodeWorker` / `PlayerController` — single-clock decode off the UI thread |
| `data/signals.py` | Shared facts: EEG channels + rate, extra sensors, folder names, LSL/XDF stream names |
| `data/session_storage.py` | On-disk layout, manifest (boxers, capture_clock, demo, moments, stopped_early), `phase_markers.csv`, finding a recording's files (`eeg_files`, `camera_files`, `read_phase_markers`, `read_csv_tolerant`, `camera_frame_rates`), Sessions discovery/classification/disk verdict |
| `data/session_xdf.py` | `build_session_xdf` — one `recording.xdf` per recording from its crash-safe files (+ a command line) |
| `data/xdf_writer.py` | Minimal pure-Python XDF 1.0 writer (numeric + marker streams), verified with pyxdf |
| `data/session_history.py` | Append-only `history.jsonl` per recording — flush+fsync, tolerant reader |
| `data/labels_store.py` | Labels: one file per labeller (`labels/<labeller>.json`, schema 3), `LabelsStore` with undo/redo + merge-on-save, reading all labellers, migrating / claiming an old `labels.json`, events timeline |
| `data/label_agreement.py` | How far two labellers agree: matching within a tolerance, counts, a plain sentence |
| `data/privacy.py` | Boxer names: what to show on screen, and the name-free copies for an export |
| `data/safe_io.py` | Shared-folder-safe file writing: atomic writes, `FolderLock`, tolerant reads, folder status, network-drive detection |
| `data/label_vocab.py` | Shared team label list (`label_list.json` in the recordings folder) |
| `data/export_module.py` | `SessionExportWorker` + `ExportOptionsDialog` (analysis zip incl. `recording.xdf` rebuilt with every labeller's labels, `agreement.csv`, optional name-free export), `VideoExportWorker` (optional 2×2 merge), `build_coregistered_eeg` |
| `data/app_settings.py` | Per-computer settings: headband→Boxer A/B mapping + known-headbands list, session defaults, round bell, video quality, movement/pulse recording + LSL sharing switches, the labeller's name, the name-hiding switches, capture dir (`BRAIN_BATTLE_CAPTURE_DIR` always wins when set) |
| `support/log_setup.py` | Logging configuration (console + file handlers) |
| `support/platform_utils.py` | Everything that differs between macOS and Windows: date formatting, Finder/Explorer, shortcut wording, computer name, ffmpeg install hint |
| `support/docs_nav.py` | Qt-free docs logic: `docs/nav.yml` loading, heading slugify, search index, internal link/image/anchor extraction — used by `ui/docs_screen.py` and `tests/test_docs.py` |
| `../brain_battle_main.py` | Thin launcher shim at the repo root — re-exports from `brain_battle.app`; `python3 brain_battle_main.py` still works |

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
- **One clock.** Stamp anything new with `time.monotonic()` and convert through
  `capture/clock.py`; never call `time.time()` for a timestamp that will be
  compared with another source.
- **`recording.xdf` is derived, never a source.** The CSVs, sidecars, markers,
  manifest and `labels.json` are the originals; the XDF is rebuilt from them.
  Don't read it back inside the app, and don't add data that exists only there.
- **LSL is fail-soft.** Nothing in recording may depend on an outlet opening.
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
