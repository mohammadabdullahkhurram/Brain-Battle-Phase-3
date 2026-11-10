# Brain Battle — v3.0.0-beta.1 (main · develop)

PyQt6 desktop app for neuroscience boxing capture: 4 USB cameras + 2 Muse-S EEG
headbands (Bluetooth). Four screens behind a light sidebar shell — **Sessions**
(home library), **New recording** (a guided capture flow), **Review & Label**
(one merged playback + labelling screen), and **Settings** — plus an in-app
**Documentation** screen and an export pipeline. ~13k lines in the
`brain_battle` package (`ui/` · `capture/` · `playback/` · `data/` ·
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
- Commit locally per logical change. Never push unless explicitly told to.
- Run everything in `tests/` before and after every change, and report
  pass/fail — not just "I ran the tests."

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
  `version.py`. (`test_developer_docs.py`)

See `docs/contributing/docs-guide.md` "Documentation is part of every change" for
how each item is enforced.

## Product direction

**`docs/DESIGN.md` (approved 2026-10-27) is the source of truth for ALL UI
work** — the actual screens, copy, vocabulary, tokens, and layout from the
approved mockups. If code and DESIGN.md disagree, ask; don't guess.
`docs/ROADMAP.md` is the build plan/sequencing; DESIGN.md is what it must look
and read like. Summary of where the product is headed:

- **Audience is non-technical researchers and research assistants**, not
  developers. UI copy should be plain language, no jargon ("alignment",
  "capture clock", "xcorr" are implementation details, not UI vocabulary).
- **Light mode only.** The dark theme, the theme toggle, and the
  restart-to-apply theme-switch flow are going away.
- **Three screens, not three tabs with sync internals exposed:** Sessions
  (home library of past/current sessions) → Record (a guided capture flow)
  → Review & Label (one merged screen — recording and labelling stop being
  separate tabs with separate, sometimes-inconsistent alignment logic), plus
  a Settings screen for everything that's currently hardcoded in source
  (boxer↔headband mapping, resolution/fps, label vocabulary, round bell).
- **The separate Sync/Review tab goes away as a distinct concept** —
  alignment becomes fully automatic and invisible to the user; there is
  nothing to click to "sync."

## Running

```bash
pip install -e .          # supported install for now (reads pyproject.toml)
python3 -m brain_battle    # or: brain-battle  (or the shim: python3 brain_battle_main.py)
```

System binaries required: `ffmpeg` (per-camera H.264 **recording**, plus
export/probing — recording is now blocked at pre-flight without it). Review &
Label playback is in-process via **PyAV** (`av`, bundles its own ffmpeg) — no
VLC / python-vlc dependency anymore. Captures land in `Captures/` next to the
source (override with `BRAIN_BATTLE_CAPTURE_DIR`).

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
deliberately drifts: 30fps declared container vs. ~29.5fps true capture rate,
for Phase 5's drift-detection work), 2 boxer EEG CSVs with plausible synthetic
signal, `phase_markers.csv`, and `session_manifest.json` (`demo:true`) — by
reusing the real writers (`session_storage`, `USBCameraThread`'s sidecar
methods, `_MuseDirectReader`'s chunk/flush path) wherever they're headlessly
callable, so it can't silently drift from the format the app actually
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

All need ffmpeg on PATH; playback/decoding is PyAV (`av`), no VLC.

- `test_smoke.py` — the "loads and stands up" floor and **acceptance gate for
  deletions/refactors**: fresh-subprocess import of every first-party module,
  import-time `_HW_ENCODER` == post-import detection, headless construction of
  the screens (RecordFlow, ReviewLabelTab, SessionsTab, SettingsTab) + the
  `BrainBattleApp` shell, `__version__` in the window title. Run before/after
  any deletion/refactor.
- `test_player.py` — the PyAV single-clock player: frame-for-t correctness vs a
  ground-truth decode (incl. the drift camera's last frame), real-time pacing
  (±2% over ~10 s), scrub coalescing (50 seeks → a couple of decodes), paint fps.
- `test_review_tab.py` — the merged screen's label side: fast labels-first open
  (<0.5 s, media in the background), duration flow + Esc, undo/redo, keyboard
  focus rules, autosave + history, seeded vocabulary.
- `test_labels_v2.py` — `labels_store` v2 schema, v1→v2 migration (+ `.v1.bak`),
  undo/redo, events timeline.
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
  copy, no EEG channel codes or "UPLOAD" text, zero placeholder screens left
  (the Settings placeholder shipped as a real screen in Phase 3 step 7).
- `test_capture_clock.py` — capture-clock reconciliation arithmetic, two-band
  relative skew, marker/EEG co-registration, manifest bridge round-trip, the
  real EEG reader flush path, and the co-registered EEG export.
- `test_demo_session.py` — `tools/generate_demo_session.py` end to end: sidecar
  rows == MP4 frames (incl. the drift camera), EEG columns/rate, a headless
  `ReviewLabelTab.load_session_folder`, the `--interrupted` fixture, marker-token
  forms, `demo:true` manifests, and the generator's refuse-real-folder guard.
- `test_sessions.py` — Sessions discovery/status/search/filter/sort, the
  interrupted banner + dismissal persistence, off-UI-thread scanning, demo
  badges, and the strict layout at both sizes.
- `test_ring_buffer.py` / `test_eeg_streaming.py` / `test_video_recording.py` /
  `test_crash_recovery.py` / `test_history_log.py` — ring-buffer ordering; the
  streamed EEG recorder under SIGKILL; crash-safe H.264 recording; interrupted-
  session detection + disk-space branches; the append-only history log + its
  hooks (recording start/finish, opened_for_review, labels add/remove, driven
  through ReviewLabelTab).
- `test_light_theme.py` — light-only guard (no dark tokens / theme machinery).
- `test_layout_fit.py` — every shell page fits (nothing clipped) at 1440×900,
  1280×800 AND the minimum window size; a visible scrollbar is chrome, not an
  overflow.
- `test_record_flow.py` — the guided flow end to end with simulated devices:
  all four steps → a valid recording (sidecars, EEG CSVs, markers, manifest
  with names + moments, history, contact log) shown in Sessions and opened in
  Review with markers + moments; stop early; missing camera; headband
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
- `test_docs.py` — the in-app Documentation tab + `docs/` content (Phase 3
  step D1): every `nav.yml` page exists and renders; every image/diagram it
  references exists; internal links/anchors resolve; diagram freshness
  (`.mmd` vs. its rendered `.svg`/`.png`/`.hash`); search finds a known
  phrase per page + highlights it; Help (?) buttons route to the right
  page/anchor from Sessions, each record-flow step, Review & Label, and
  Settings; strict layout fit at the three sizes; and the placeholder
  gate's scope contract (it must not scan `docs/*.md`).

## Project status

**Current state: Phase 3 COMPLETE, reorganised into the `brain_battle` package
(`main` = `develop` = `3.0.0-beta.1`).** The unified
capture-time clock shipped and the app was rebuilt onto the four DESIGN
screens — Sessions, New recording, Review & Label, and now Settings. Review &
Label is one merged PyAV-driven screen (single `t_session_seconds` playhead);
labels are `labels.json` v2 with undo/redo and a shared vocabulary; Sessions is
the home library; export produces an analysis bundle. The old Review/Labelling
tabs, VLC, audio-xcorr (`sync_engine.py`), and `labels_config.py` are gone. **New
recording is the guided flow** (`record_flow.py`, DESIGN §2–§5: Connect →
Set up → Record → Saved), replacing the Live Monitor (`live_session_tab.py`,
`session_setup_dialog.py`, the old camera grid — all removed). It records moments
(M), prevents sleep, logs per-sensor contact quality, and stops cleanly early;
moments surface in Review as unnamed labels. **Settings is now the real screen**
(`settings_tab.py`, DESIGN §7 — Phase 3 step 7, the final placeholder removed):
recordings folder (Change folder…/Show in Finder, a `BRAIN_BATTLE_CAPTURE_DIR`
override note), headbands (known-device dropdowns + BLE search, a same-headband
guard), video quality (Standard/High/Light — resolution AND bitrate wired into
the recorder, with the GB/hour copy and disk-space estimate both computed from
that bitrate), session defaults + round bell + Test sound, and the shared label
list (add/rename/remove/reorder/recolour). Everything autosaves with a brief
"✓ Saved" confirmation and applies to the next recording. The former hard-coded
`eeg_module.BOXER_LABELS` and `usb_camera.py`'s fixed resolution/bitrate are
gone — all of it now lives in the `app_settings` store. Hardware is broken, so
the whole flow is built + tested against **simulated** devices driven through the
real capture paths (`simulation.py`, `BRAIN_BATTLE_SIMULATE`, dev-only, not
shipped). What remains hardware-blocked is the eyes-on-glass/real-signal
verification (deferred items below) — see `docs/ROADMAP.md`'s Phase 3 "DESIGN
differences" note for the handful of places this build intentionally reads
differently than DESIGN.md's mockup text.

**In-app documentation (step D1 of 3, inserted before Phase 5) is also
shipped.** A new **Documentation** screen (`docs_tab.py` + `docs_nav.py`,
sidebar item just above Settings) renders the Markdown under `docs/` at
runtime — no QtWebEngine, Qt's own `QTextDocument.setMarkdown` — with a
nav tree, always-visible search (titles/headings/body, with highlighting),
and back/forward. Help (?) buttons on Sessions, each New-recording step,
Review & Label, and Settings open that screen's page directly. `docs/nav.yml`
lists only finished pages; `docs/contributing/docs-guide.md` is the plan for
all six sections (Diátaxis + ADRs) and the style rules every page follows.
D1 wrote *Getting started* and *User guide* (11 pages); *Research & data*,
*Developer guide*, *Decisions*, and *Project* land in D2/D3. Diagrams are
Mermaid source (`docs/diagrams/*.mmd`) rendered via `tools/render_diagrams.py`
to both `.svg` (GitHub) and `.png` (what Markdown actually references — Qt's
`QSvgRenderer` can't render Mermaid's CSS-class styling or
`foreignObject` labels, verified empirically; see docs-guide.md). Screenshots
are generated, not hand-captured: `tools/render_screens.py --docs`.

Documentation steps **D2** and **D3** come next, then **Phase 5** — each gated
on owner approval per the working agreement (never start one on your own
initiative). D2/D3 add the remaining documentation sections (*Research & data*,
*Developer guide*, *Decisions*, *Project*); Phase 5 is the remaining
report/research-value items in `docs/ROADMAP.md`.

**Phase 4 (Labelling) was delivered as part of the Phase 3 Review & Label
build**, not as a separate phase: every label is tied to a boxer (A/B/Both),
labels are point or duration events on the numeric `t_session_seconds` axis, a
shared team vocabulary is stored with the sessions (not committed to the repo),
and frame-stepping, 0.25×/0.5× playback, undo/redo and an editable list of
placed labels all ship in `review_label_tab.py` + `labels_store.py` +
`label_vocab.py`. `docs/ROADMAP.md`'s Phase 4 section is marked accordingly.

For the long-form history behind the current architecture — the unified
capture-time clock build (STEPs 1–5), the prior functional-fixes / cleanup
phase and its grounding backlog, and the v1.x→v2.x recording-model migration —
see **`docs/project/history.md`**. CLAUDE.md describes the present; that page
keeps the narrative.

## Module map

Current architecture: everything lives in the **`brain_battle` package**,
grouped by responsibility into `ui/` · `capture/` · `playback/` · `data/` ·
`support/`. Four DESIGN screens sit behind a light sidebar shell —
**Sessions** (home library), **New recording** (a full-screen guided flow,
`ui/record_flow.py`), **Review & Label** (one merged screen), and **Settings**
(`ui/settings_tab.py`). Review & Label playback is app-driven on a single
`t_session_seconds` playhead via **PyAV** (`playback/pyav_player.py`): there is
no VLC, no Qt-Multimedia, and no audio-xcorr. Labels are `labels.json` v2
(`data/labels_store.py`) with a shared team vocabulary (`data/label_vocab.py`);
export is an analysis bundle (`export_module.SessionExportWorker`). Hardware is
broken, so the flow can be driven by **simulated** cameras + headbands through
the real capture paths, gated entirely by `BRAIN_BATTLE_SIMULATE`
(`capture/simulation.py`, dev-only, not shipped).

Run it with `python3 -m brain_battle` (or the `brain-battle` console script
after `pip install -e .`); `brain_battle_main.py` at the repo root is a thin
launcher shim.

| File (under `brain_battle/`) | Purpose |
|---|---|
| `app.py` | `BrainBattleApp` shell (window sizing + QSettings, page wiring) + `main()` entry point |
| `version.py` | Single authoritative `__version__` (window title, manifest, pyproject reads it) |
| `paths.py` | Single source of repo-relative paths: `REPO_ROOT`, `ASSETS_DIR`, `DOCS_DIR`, default `Captures/` |
| `ui/sidebar_shell.py` | DESIGN shell: 232px sidebar, 72px rail on Review & Label, fully hidden for the full-screen record flow; app opens on Sessions |
| `ui/record_flow.py` | The guided New-recording flow (DESIGN §2–§5): Connect → Set up → Record → Saved; owns the device slots + SessionController; emits `session_saved` / `exit_flow` |
| `ui/review_label_tab.py` | The merged Review & Label screen (DESIGN §6): labels-first open, PyAV video grid, jargon-free brain card, timeline, labelling |
| `ui/sessions_tab.py` | Sessions home library (discovery, status, search/filter/sort, interrupted banner, background scan + sizing) |
| `ui/settings_tab.py` | The real Settings screen (DESIGN §7): recordings folder, headbands, video quality, session defaults + bell, shared label-list editor; autosaves with a "✓ Saved" confirmation |
| `ui/docs_tab.py` | The in-app Documentation screen: Markdown rendering via `QTextBrowser`/`QTextDocument.setMarkdown` (no QtWebEngine), nav tree, always-visible search with highlighting, back/forward |
| `ui/design_tokens.py` | **Light-only** design system (docs/DESIGN.md tokens) + QSS factories |
| `ui/fonts.py` | Registers the bundled Inter font (from `assets/fonts/` via `paths.ASSETS_DIR`) |
| `capture/usb_camera.py` | `USBCameraThread` (cv2, or a simulated capture) + `FrameEncoder` — crash-safe fragmented H.264 per-camera ffmpeg subprocess + row-per-frame sidecar; no audio; preview downscale |
| `capture/camera_source.py` | `CameraSlot` — one camera for the flow (wraps `USBCameraThread`: preview, fps, recording/dropped/encoder state) |
| `capture/headband_source.py` | `HeadbandSlot` — one boxer's headband (wraps `_MuseDirectReader`: connection, battery, per-sensor contact, live trace, reconnect/append) |
| `capture/eeg_module.py` | Muse-S BLE scan/connect/stream, `_RingBuffer`, `EEGLiveWidget` (pyqtgraph) |
| `capture/eeg_plot_pg.py` | pyqtgraph live EEG plots (plain sensor names, no channel codes) |
| `capture/session_controller.py` | Pure-logic phase state machine (headless-testable), 1 Hz QTimer |
| `capture/simulation.py` | **Dev-only, not shipped** — `BRAIN_BATTLE_SIMULATE` simulated cameras + headbands through the real capture paths; failure injection (missing camera, headband drop) |
| `capture/contact_quality.py` | Per-sensor EEG contact-quality heuristic (plain language) |
| `capture/contact_log.py` | Per-sensor contact-quality log written during recording + weak-period summary for Saved/Review |
| `capture/keep_awake.py` | Prevent sleep while recording (caffeinate / SetThreadExecutionState) |
| `capture/round_bell.py` | `RoundBell` — round-start boxing bell via `QSoundEffect` (was `sync_beep.py` / `SyncBeep`) |
| `playback/pyav_player.py` | `PyAVCamera` / `MultiCamDecoder` / `DecodeWorker` / `PlayerController` — single-clock decode off the UI thread |
| `data/session_storage.py` | On-disk layout, manifest (boxers, capture_clock, demo, moments, stopped_early), `phase_markers.csv`, Sessions discovery/classification/disk verdict |
| `data/session_history.py` | Append-only `history.jsonl` per recording — flush+fsync, tolerant reader |
| `data/labels_store.py` | `labels.json` v2 (schema, v1 migration, undo/redo, events timeline) |
| `data/label_vocab.py` | Shared team label list (`label_list.json` in the recordings folder) |
| `data/export_module.py` | `SessionExportWorker` + `ExportOptionsDialog` (analysis zip), `VideoExportWorker` (optional 2×2 merge), `build_coregistered_eeg` |
| `data/app_settings.py` | User-config store: headband→Boxer A/B mapping + known-headbands list (replaces hard-coded `BOXER_LABELS`), session defaults, round bell, video quality (resolution + bitrate), capture dir (`BRAIN_BATTLE_CAPTURE_DIR` always wins when set) |
| `support/log_setup.py` | Logging configuration (console + file handlers) |
| `support/docs_nav.py` | Qt-free docs logic: `docs/nav.yml` loading, heading slugify, search index, internal link/image/anchor extraction — used by `ui/docs_tab.py` and `tests/test_docs.py` |
| `../brain_battle_main.py` | Thin launcher shim at the repo root — re-exports from `brain_battle.app`; `python3 brain_battle_main.py` still works |

(Removed with the guided flow: `live_session_tab.py` (Live Monitor),
`session_setup_dialog.py`, `usb_camera_panel.py` / `usb_camera_slot.py` (the old
Live-Monitor camera grid). Removed earlier with the merged screen:
`vlc_module.py`, `sync_engine.py`, `labels_config.py` + `labels_data.json`, and
`brain_battle_main`'s old `SyncReviewTab` / `LabellingTab` /
`MainEditorSyncWorker` / `EEGReviewWidget`.)

## Deferred work — hardware-blocked verification

Everything below is blocked specifically on real hardware — it cannot be closed
by writing more code. This is NOT the project backlog (that's
`docs/ROADMAP.md`); these are the loose ends that need a real capture session.
Items that were only code-scoped follow-ups — the old labelling-tab video
timeline, VLC playback-drift correction, and the `sync_engine` dataclass
extraction — are now **closed**: that code was removed when Review & Label
merged onto PyAV and the old Sync/Labelling tabs (and `vlc_module.py`,
`sync_engine.py`) went away.

0. **UNVERIFIED: EEG capture-time alignment.** EEG samples are placed on the
   shared session axis by `t_session = muse_timestamp - capture_t0_unix`. The
   *arithmetic* is unit-tested (`tests/test_capture_clock.py`), but the EEG
   *clock source* is not: muselsl derives its timestamps from `time.time()`
   corrected by a per-band regression, and BLE transport adds latency — neither
   is confirmable without a real Muse band. *Why it matters:* between-brain
   synchrony timing depends on where each band sits on the axis. The app logs
   this warning at every EEG session start (labeled in `eeg_module.py`).
   *Unblocked by:* a real 2-band session — record a known simultaneous physical
   event (e.g. both boxers tap in view of a camera at one buzzer), then check
   the EEG artifact lands at the same `t_session` as the camera frame. **The
   exported `eeg_coregistered.csv` inherits this** — its between-brain skew is
   measured-but-hardware-unverified; the same buzzer check discharges it too.

1. **Eyes-on-glass visual check — real signals.** Synced playback (decode,
   4-cam lockstep, automatic capture-clock alignment) and the brain-activity
   traces are verified headless to correct-pixels + measured scrub perf +
   alignment arithmetic (unit tests), but nobody has *watched* the tab render
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

Not hardware-blocked, but noted: the drift camera the demo generates (30fps
declared vs ~29.5fps true) is a real, reproduced alignment defect — using the
full per-frame sidecar for playback timing (rather than the first row only) is
tracked in `docs/ROADMAP.md` Phase 5, not here.


## Conventions & gotchas

- Review & Label playback is in-process via **PyAV** (`pyav_player.py`): one
  `t_session_seconds` playhead, decode off the UI thread, no VLC/Qt-Multimedia
  players and no native-surface z-order hacks.
- The per-phase white flash is NOT perfectly simultaneous across the four
  cameras — each camera captures on its own phase, so the first frame at/after a
  phase marker lands up to one frame period (~34 ms) apart. This is expected and
  correct; `tests/test_flash_sync.py` asserts the spread stays within one frame.
- Light mode only — there is no theme machinery (no dark palette, no
  `_apply_theme`); `test_light_theme.py` guards that it stays gone.
- `session_controller.py` is deliberately Qt-optional (headless-testable).
- `phase_markers.csv` labels look like `BASELINE_START`, `ROUND_1_START`,
  `BREAK_1_START`, `SESSION_END`; `t_session_seconds` is monotonic time since
  session start — the natural sync base for in-app captures.
