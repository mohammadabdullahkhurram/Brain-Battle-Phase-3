# Brain Battle — v3.0.0-dev (develop)

PyQt6 desktop app for neuroscience boxing capture: 4 USB cameras + 2 Muse-S EEG
headbands (Bluetooth), with a live capture tab, a sync/review tab, a labelling
tab, and an export pipeline. ~11k lines across 24 .py files.

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
pip install -r requirements.txt
python3 brain_battle_main.py
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
python3 tools/generate_demo_session.py                    # default: 2 rounds, 160s, seed 0
python3 tools/generate_demo_session.py --out /tmp/demo --rounds 3 --seed 7
python3 tools/generate_demo_session.py --interrupted       # looks like a mid-round crash
```

It writes a real v3 session folder — 4 camera MP4s + sidecars (one camera
deliberately drifts: 30fps declared container vs. ~29.5fps true capture
rate, for Phase 5's drift-detection work), 2 boxer EEG CSVs with plausible
synthetic signal, `phase_markers.csv`, and `session_manifest.json` — by
reusing the real writers (`session_storage`, `USBCameraThread`'s sidecar
methods, `_MuseDirectReader`'s chunk/flush path) wherever they're headlessly
callable, so it can't silently drift from the format the app actually
reads/writes. Deterministic for a given `--seed`; the default config
generates in well under a minute.

**This is also the eyes-on-glass check** deferred work item #1 has been
waiting for: every camera shows a full-frame white flash and every EEG
channel gets a sharp spike at each phase-marker instant, so you can load a
generated session into Review, hit ALIGN, and visually confirm all four
cameras + both EEG panes land on the same instant at each flash/spike — the
same check a real capture session needs, just without needing hardware to
produce the fixture. `tests/test_demo_session.py` automates the arithmetic
side of this (sidecar/MP4 frame counts, EEG columns/rate, and that ALIGN's
computed seeks match the generator's own start delays exactly).

## Tests (`tests/` — plain scripts, `python3 tests/<name>.py`, nonzero exit on failure)

All need ffmpeg on PATH; playback/decoding is PyAV (`av`), no VLC.

- `test_smoke.py` — the "loads and stands up" floor and **acceptance gate for
  deletions/refactors**: fresh-subprocess import of every first-party module,
  import-time `_HW_ENCODER` == post-import detection, headless construction of
  the tabs (LiveSessionTab, ReviewLabelTab, SessionsTab) + app shell,
  `__version__` in the window title. Run before and after any deletion/refactor.
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
- `test_no_placeholders.py` — acceptance gate: no placeholder/temporary UI copy,
  no EEG channel codes or "UPLOAD" text, only the Settings placeholder survives.
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

## Project status

**Current state (Phase 3, `develop` = `3.0.0-dev`).** The unified capture-time
clock shipped and the app was rebuilt onto the three DESIGN screens. Review &
Label is one merged PyAV-driven screen (single `t_session_seconds` playhead);
labels are `labels.json` v2 with undo/redo and a shared vocabulary; Sessions is
the home library; export produces an analysis bundle. The old Review/Labelling
tabs, VLC, audio-xcorr (`sync_engine.py`), and `labels_config.py` are gone. What
remains hardware-blocked is the eyes-on-glass/real-signal verification (deferred
items below). The historical step-by-step narrative that follows is kept for
context — where it names `SyncReviewTab`/`LabellingTab`/`vlc_module`/
`sync_engine`, those are the now-removed predecessors of `review_label_tab.py` +
`pyav_player.py`.

---

**History — unified capture-time clock** on the long-lived `develop`
branch (`main` stays the stable baseline; develop merges to main only when a
version is verified and ready to ship). Goal: one monotonic capture-time
reference stamped on every source so alignment is MEASURED (tens-of-ms target
— deliberately NOT sub-ms; no genlock/LSL). See
`docs/ARCHITECTURE_AND_EVALUATION.md` for the design.

**Versions: `develop` = `3.0.0-dev`, `main` = `2.1.0`.** 3.0.0 is a
DELIBERATE BREAKING CHANGE: the review/label path aligns by the capture-time
clock (`capture_clock` in the manifest + `t_session_seconds` in the camera
sidecars and EEG CSVs). Pre-3.0 sessions recorded without that data are NOT
supported — there is no legacy-fallback alignment path (one alignment path,
not two). Record fresh with 3.0. Becomes `3.0.0` when develop merges to main.

Step status:

- **STEP 1 — camera frame timestamps: DONE, verified.** Each recording now
  writes a per-camera sidecar `<mp4-stem>.timestamps.csv`
  (`frame_index, t_session_seconds, t_monotonic, t_unix`) alongside the
  (unchanged) MP4. A shared origin `(t0_monotonic, t0_unix)` is captured once
  at recording-start in `live_session_tab._on_session_started` and threaded
  through `usb_panel → slot → USBCameraThread`, so all cameras stamp on one
  clock. Verified against the real `USBCameraThread.run()` loop (file-backed
  VideoCapture, no camera on this box): 13/13 — row-per-frame, count == MP4
  frames, monotonic-increasing, unix sane, shared origin identical across
  cameras. NOT yet verified: real capture cadence (needs a physical camera).
- **STEP 2 — unified clock bridge + EEG-side stamping: DONE (EEG half
  UNVERIFIED).** The shared origin is persisted to the manifest as
  `capture_clock: {t0_monotonic, t0_unix}` (`session_storage.set_capture_origin`),
  and each band's measured first-sample time to `source_starts`
  (`record_source_start`) — startup skew is now recorded, not assumed. The EEG
  recorder writes a `t_session_seconds` column (`= timestamps - origin_unix`)
  so EEG sits on the same axis as camera frames and markers. Reconciliation
  math + manifest bridge unit-tested in `tests/test_capture_clock.py` (9/9,
  incl. the real reader flush path). **UNVERIFIED**: the EEG *clock source*
  (muselsl wall-clock accuracy + BLE latency) is not confirmable without a
  real band — labeled in `eeg_module.py` (reconciliation comment),
  `live_session_tab.py` (comment + a startup `log.warning`), and Deferred work
  below. The arithmetic is verified; the EEG position on the axis is not.
- **STEP 3 — align review/label by capture-time stamps: DONE (logic-verified,
  NOT eyes-verified).** Clean break, no legacy fallback (v3.0).
  - `EEGReviewWidget.load` now places EEG on the shared axis via its
    `t_session_seconds` column (minus the caller's axis_shift) instead of its
    own first sample — this is the §2.4 EEG-vs-marker fix, and it benefits
    BOTH tabs (same widget class).
  - **SyncReviewTab fully converted:** `load_session_folder` reads
    `capture_clock.t0_unix` (manifest) + each camera's measured start (frame
    sidecar); `axis_shift = max(start)` = playhead-0; AUTO-SYNC is now
    synchronous — it READS the alignment (video seek = axis_shift − start,
    method `capture_clock`), no audio worker. Markers placed at
    `t_session − axis_shift`; EEG loaded with `offset_sec = axis_shift`. All
    three co-register (unit-tested, `test_capture_clock.py`).
  - **LabellingTab: EEG-vs-marker §2.4 fixed** by the widget change (it loads
    EEG at offset 0, markers at raw `t_session` → both on raw `t_session`).
    Its distinct `SyncPlaybackController` + fractional-slider VIDEO timeline is
    NOT converted to the capture axis in this step — a scoped follow-up (small
    residual video-vs-marker skew = camera startup latency, tens of ms). See
    Deferred work.
  - **UNVERIFIED / not-eyes-verified:** this is the review/label path that has
    never been watched on real data. Logic + construction + suites are green
    (necessary, NOT sufficient — they cannot prove the tab renders aligned
    playback). Plus the EEG-axis position still carries Step 2's UNVERIFIED
    clock-source flag. Both fold into the deferred visual check.
- **STEP 4 — retire audio xcorr: DONE.** All sessions come from the recording
  app (capture_clock present), so audio cross-correlation is obsolete. Deleted
  `MainEditorSyncWorker`, the manual-upload branch of the align path,
  `sync_utils/audio_analysis.py`, `sync_utils/eeg_video_sync.py`, and the two
  now-dangling tests (`test_xcorr_confidence.py`, `test_seek_alignment.py` —
  the D′ seek-formula invariant is preserved in
  `test_capture_clock.py::test_review_alignment_coregisters_all_three`).
  Removed the dead confidence computation + `SourceRow.set_confidence` badge.
  KEPT `sync_engine.SourceOffset`/`SyncResult` (load-bearing in export) and the
  honest `sync_offsets.csv` `n/a (capture_clock)` output. The tab is now
  **Review** (was "Sync + Review"); `_run_sync` → `_align`, "AUTO-SYNC" button
  → "ALIGN". Single alignment method: capture-time stamps.
- **STEP 5 — co-registered EEG export: DONE (alignment UNVERIFIED per Step 2).**
  `export_module.build_coregistered_eeg` merges both boxers' EEG onto ONE
  uniform 256 Hz `t_session_seconds` grid (union span): columns
  `t_session_seconds, <chan>_01, <chan>_02, phase_id, phase_label, marker`,
  NaN where a band has no sample. Written to the bundle as
  `eeg_coregistered.csv` alongside the raw per-band files; the tab passes the
  raw-axis phase markers via `_read_markers_for_export`. Verified against
  synthetic bands (`test_capture_clock.py::test_coregistered_preserves_between_brain_skew`):
  a known 0.35s between-brain skew survives, markers land at the right
  `t_session`, phase forward-fills, NaN outside range, CSV round-trips — plus
  an end-to-end export producing the zip. **INHERITED UNVERIFIED**: the
  between-brain alignment is only as accurate as the Step-2 EEG clock
  placement (deferred #0/#1) — labeled in the CSV's README section and the
  `build_coregistered_eeg` docstring.

**This completes the unified-capture-clock build phase.** What remains from
this specific phase is the eyes-on-glass verification with real capture data
(deferred #0/#1) and the scoped follow-ups below. Everything planned after
this phase — the Sessions/Record/Review & Label redesign, data-safety fixes,
labelling improvements, shipping — is tracked in `docs/ROADMAP.md`, gated on
owner approval per phase (see "Working agreement" above).

---

### Prior phase — FINISHED

Both phases — functional fixes and cleanup — are complete and merged to main
(branch `fix/v2-migration-review-path`, 15 commits, merge `5c08a9b`; plus the
architecture report `2695f7d`). The migration gap that started that work (the
review/sync path still reading the v1 per-round layout) is closed. Coverage:

| Commits | Work |
|---|---|
| `8aaa14a` `faa8f38` `bfa0a36` `16f91a2` | Functional fixes A/B, D′, E, C |
| `0993721` | E follow-up (videotoolbox NameError, silently broken 4 commits) |
| `0de80f2` `e16aab3` | Cleanup G (ring buffer), I+F (storage API, `__version__`) |
| `bbc0769` `5ff48db` `7697642` `93888f4` | Smoke gate, J deletions, K logging, H pyqtgraph port |
| `43ece84` `fc13bd8` `d1ec67b` (+ this one) | CLAUDE.md + README documentation |

This phase's own loose ends are in **Deferred work** below. Everything
planned beyond that — the product direction summarized above — lives in
`docs/ROADMAP.md`, not here.

## CRITICAL CONTEXT — the v1.x → v2.x migration gap

The app was migrated from a v1.x **per-phase recording** model (separate
folders and clips per round: `baseline/`, `Round 1/`, each with `Side Cams/`,
`POV Cams/`, `Muse data/`) to a v2.x **one continuous take** model:

```
Brain Battle <timestamp>/
    continuous/
        Side Cams/*.mp4      # one MP4 per camera, whole session
        POV Cams/*.mp4
        Muse data/*.csv      # one CSV per Muse, whole session
    phase_markers.csv        # phase boundaries as timestamps (t_session_seconds, phase_id, label, round_num, unix_time)
    session_manifest.json
```

- The **CAPTURE half is fully migrated**: `session_controller.py`,
  `session_storage.py`, `live_session_tab.py` record one continuous take and
  write `phase_markers.csv`. Per-round folders are never created
  (`session_storage.py:207-211` documents this explicitly).
- The **REVIEW/SYNC half is now migrated too** (fixed in `8aaa14a`): the Sync
  tab's `load_session_folder` reads `continuous/` + `phase_markers.csv`,
  mirroring the Labelling tab's `_open_session_folder`, and the tab has its
  own OPEN SESSION FOLDER button. The historical migration gap — the Sync tab
  globbing nonexistent `Round {n}/` folders and returning silently — is where
  most of the original bugs lived; keep both loaders in step if the session
  layout ever changes again.

A second structural consequence: all four in-app cameras (side AND POV) record
**no audio track** — originally via `cv2.VideoWriter`, now via a per-camera
ffmpeg subprocess (`usb_camera.FrameEncoder`, video-only). So audio-xcorr sync
could only ever work on externally recorded footage (e.g. SD-card DSLR files
uploaded manually); in-app captures don't need xcorr anyway — they start
simultaneously and share `phase_markers.csv` time.

## Module map

Current architecture: three DESIGN screens behind a light sidebar shell —
**Sessions** (home library), **New recording** (capture), **Review & Label**
(one merged screen) — plus Settings. Review & Label playback is app-driven on a
single `t_session_seconds` playhead via **PyAV** (`pyav_player.py`): there is no
VLC, no Qt-Multimedia, and no audio-xcorr. Labels are `labels.json` v2
(`labels_store.py`) with a shared team vocabulary (`label_vocab.py`); export is
an analysis bundle (`export_module.SessionExportWorker`).

| File | Purpose |
|---|---|
| `brain_battle_main.py` | Entry point, `BrainBattleApp` shell (window sizing + QSettings), `SettingsPlaceholder` |
| `review_label_tab.py` | The merged Review & Label screen (DESIGN §6): labels-first open, PyAV video grid, jargon-free brain card, timeline, labelling |
| `pyav_player.py` | `PyAVCamera` / `MultiCamDecoder` / `DecodeWorker` / `PlayerController` — single-clock decode off the UI thread |
| `labels_store.py` | `labels.json` v2 (schema, v1 migration, undo/redo, events timeline) |
| `label_vocab.py` | Shared team label list (`label_list.json` in the recordings folder) |
| `contact_quality.py` | Per-sensor EEG contact-quality heuristic (plain language) |
| `sessions_tab.py` | Sessions home library (discovery, status, search/filter/sort, interrupted banner, background scan + sizing) |
| `eeg_module.py` | Muse-S BLE scan/connect/stream, `_RingBuffer`, `EEGLiveWidget` (pyqtgraph) |
| `eeg_plot_pg.py` | pyqtgraph live EEG plots (plain sensor names, no channel codes) |
| `design_tokens.py` | **Light-only** design system (docs/DESIGN.md tokens) + QSS factories |
| `live_session_tab.py` | New-recording/capture orchestration, emits `session_saved(session_root)` |
| `export_module.py` | `SessionExportWorker` + `ExportOptionsDialog` (analysis zip), `VideoExportWorker` (optional 2×2 merge), `build_coregistered_eeg` |
| `session_storage.py` | On-disk layout, manifest (boxers, capture_clock, demo flag), `phase_markers.csv`, Sessions discovery/classification |
| `session_history.py` | Append-only `history.jsonl` per recording — flush+fsync, tolerant reader |
| `usb_camera.py` | `USBCameraThread` (cv2) + `FrameEncoder` — crash-safe fragmented H.264 per-camera ffmpeg subprocess + row-per-frame sidecar; no audio |
| `usb_camera_slot.py` / `usb_camera_panel.py` | One camera slot widget / 2×2 grid + start-stop fan-out |
| `sidebar_shell.py` | DESIGN shell: 232px sidebar, collapses to a 72px rail on Review & Label; app opens on Sessions |
| `session_controller.py` | Pure-logic phase state machine (headless-testable), 1 Hz QTimer |
| `session_setup_dialog.py` | Rounds/durations + boxer-name config dialog |
| `circular_timer.py` | Circular countdown dial · `sync_beep.py` round-start beep · `fonts.py` bundled Inter · `sync_utils/infobox.py` message-box helper |

(Removed with the merged screen: `vlc_module.py`, `sync_engine.py`,
`labels_config.py` + `labels_data.json`, and `brain_battle_main`'s old
`SyncReviewTab` / `LabellingTab` / `MainEditorSyncWorker` / `EEGReviewWidget`.)

## Issue backlog

### DONE — all ten grounding items (commits on fix/v2-migration-review-path)

- **A/B [HIGH] — DONE in `8aaa14a`.** Live→Review handoff ported to the
  `continuous/` layout (was globbing v1 `Round {n}/` and returning silently);
  alignment is audio-aware (<2 audio clips → simultaneous_start with offsets
  0, 2+ → xcorr among the audio-bearing subset); `_clamp_ms` NaN/inf-guarded;
  `_xcorr_delay` returns 0 on empty input; OPEN SESSION FOLDER button and
  phase markers added to the Sync tab.

- **D′ [MED] — DONE in `faa8f38`.** Seek formula reverted to
  `max_norm_delay - d` (the old code seeked the latest-starting clip the
  most, misaligning by ~2× the true offset). The formula now lives in the
  capture-clock `_align` path (`seek = axis_shift - start`); its invariant is
  guarded by `test_capture_clock.py::test_review_alignment_coregisters_all_three`
  (the original audio-based `test_seek_alignment.py` was retired in Step 4).

- **E [MED] — DONE in `bfa0a36`.** Export failures now surface the tail of
  ffmpeg's stderr + exit code (deque captured during the progress loop);
  `_detect_hw_encoder` requires a real test-encode probe, not just an
  `-encoders` listing, falling through to libx264.

- **C [MED] — DONE in `16f91a2`, SUPERSEDED in Step 4.** Real xcorr confidence
  was wired through the worker → `SourceOffset` → a `set_confidence` badge.
  Step 4 removed the xcorr source, so the computed confidence and the badge
  are gone; `SourceOffset.confidence` is now always `None` under capture-clock
  alignment and `sync_offsets.csv` records the honest `n/a (capture_clock)`
  sentinel (that CSV behaviour is the surviving, live part). The discrimination
  test `test_xcorr_confidence.py` was deleted with the feature.

- **G [LOW] — DONE in `0de80f2`.** Ring buffer over-capacity ordering: the
  `n >= capacity` branch of `_RingBuffer.extend` left `_n_written` unaligned,
  so `to_array` mis-rotated. Latent (Muse chunks ≤12 into a 7680 buffer).
  Guarding test: `tests/test_ring_buffer.py`.

- **I + F [LOW] — DONE in `e16aab3`.** I: deleted the uncalled v1 per-phase
  path API (`phase_dir`/`side_cams_dir`/`pov_cams_dir`/`muse_dir`/
  `_phase_folder_name`) and rewrote `session_storage.py`'s stale docstrings
  to the continuous/ model. F: single `version.py` `__version__ = "2.1.0"`,
  surfaced to the window title, the session manifest (was `"Phase 2"`), the
  README, and the CHANGELOG header. Guarded by `tests/test_smoke.py`
  (window-title check).

- **Smoke gate — ADDED in `bbc0769`.** `tests/test_smoke.py`: the
  import/construct floor and acceptance gate for deletions/refactors (the
  check that would have caught the videotoolbox bug, verified RED against
  the pre-`0993721` revision). E's follow-up fix itself is `0993721`.

- **J [CLEANUP] — DONE in `5ff48db`.** Deleted `sync_utils/eeg_module.py`
  (1207-line orphan, zero importers; its "(v3)" docstring label was stale —
  root `eeg_module.py` got all later work), the fully-dead
  `from sync_engine import SyncWorker, SyncResult` line, and the
  never-created `_drift_hb`/`_nudge_drift` machinery. `sync_engine.py` stays
  for its live dataclasses. Guarded by the smoke gate (dynamic module
  discovery auto-shrank 24→23).

- **K [DIAGNOSTICS] — DONE in `7697642`.** Central logging in
  `log_setup.py` (console at INFO; `attach_session_log()` adds a DEBUG file
  handler in each session folder). All 56 `print()` calls → module loggers;
  36 silent `except` blocks triaged (real-failure-hiding → warning/error,
  teardown races → debug/comment); control flow unchanged everywhere. Level
  policy in `log_setup.py`'s docstring. Most failure-path log lines are
  verified-by-inspection (triggers need real hardware/corrupt data).

- **H [LOW] — DONE in `93888f4`.** `EEGReviewWidget` ported matplotlib →
  pyqtgraph mirroring `eeg_plot_pg.py`: trace uploaded once at `load()`,
  playhead ticks move an `InfiniteLine` + shift the ViewBox; band-power
  welch recomputes per ≥0.2 s movement (`_BP_STEP_SEC`). Measured: waveform
  scrub 118→15 ms/tick, band-power renderer 102→11 ms; the old widget could
  not service the app's 100 ms tick. API unchanged; removed the repo's last
  matplotlib import. Rendering correctness deferred — see below.

### KNOWN LIMITATIONS (inherent — not fixable by more work here)

- **Sync confidence measures internal consistency, not correctness.** A high
  score means the correlation peak dominates its window — coincident loud
  transients in genuinely unrelated audio can still score high (inherent to
  envelope xcorr, not fixable by the metric).
- **Encoder validation's "listed-but-no-hardware" fallback** (e.g. nvenc on a
  GPU-less box) is proven by forced-probe-failure unit tests only; it has
  never been exercised on real GPU-less hardware.

## Deferred work — loose ends from the capture-clock build

Everything below is specifically blocked on real hardware, or was
deliberately scoped out of the Step 1-5 capture-clock build above, each with
why it's deferred and what unblocks it. This list is NOT the project's
backlog — that's `docs/ROADMAP.md`. Several items below (e.g. #0/#1, the
EEG/video alignment checks) reappear there as "Deferred until hardware
works," since they can't be closed by writing more code.

0. **UNVERIFIED: EEG capture-time alignment** (Step 2 of the unified-clock
   phase). EEG samples are placed on the shared session axis by
   `t_session = muse_timestamp - capture_t0_unix`. The *arithmetic* is
   unit-tested (`tests/test_capture_clock.py`), but the EEG *clock source* is
   not: muselsl derives its timestamps from `time.time()` corrected by a
   per-band regression, and BLE transport adds latency — neither is
   confirmable without a real Muse band. *Why it matters:* between-brain
   synchrony timing depends on where each band sits on the axis. *Unblocked
   by:* a real 2-band session — record a known simultaneous physical event
   (e.g. both boxers tap in view of a camera at one buzzer), then check the
   EEG artifact lands at the same `t_session` as the camera frame. Until then,
   the app logs this warning at every EEG session start. Labeled in
   `eeg_module.py`, `live_session_tab.py`. **The exported
   `eeg_coregistered.csv` (Step 5) inherits this** — its between-brain skew is
   measured-but-hardware-unverified; that same buzzer check discharges it too.

1. **Eyes-on-glass visual check** — synced playback (decode, 4-cam lockstep,
   markers, **capture-clock alignment from Step 3**) and the pyqtgraph
   review-EEG rendering. *Why deferred:* verified headless to correct-pixels
   + measured scrub perf + alignment ARITHMETIC (unit tests), but nobody has
   *watched* whether the tab renders aligned playback — offscreen Qt can't put
   pixels on glass, and synthetic fixtures aren't real signals. *Unblocked
   by:* the first real capture session. **The procedure:**
   `python3 brain_battle_main.py` → Sync + Review → OPEN SESSION FOLDER → pick
   a v3 session → AUTO-SYNC → confirm all four cams show video and PLAY runs
   them in lockstep → scrub and watch both EEG panes follow → click a
   phase-marker chip and confirm the playhead + EEG land at that event. The
   sharp check: a marker at a known physical event (round buzzer) should line
   up with that event in the video AND the EEG artifact (this also discharges
   deferred #0's EEG-clock check).

2. **LabellingTab video timeline not on the capture axis** (Step 3 scoped it
   out). Its EEG-vs-marker skew IS fixed, but its `SyncPlaybackController` +
   fractional slider still plays video from local 0, leaving a small
   video-vs-marker residual = camera startup latency (tens of ms). *Why
   deferred:* the label tab uses a different playback model than the review
   tab, and converting it blind on an unwatched path is risky. *Unblocked by:*
   the eyes-on-glass session (see how much the residual actually matters for
   labelling) + porting the review tab's axis_shift approach into the label
   tab's controller.

3. **Drift-correction decision** — there is NO active playback drift
   correction; lockstep rests solely on issuing `play()` to all four players
   in the same event-loop turn (the removed `_drift_hb` heartbeat was dead
   code, so nothing was lost). Held to ~250 ms — libVLC clock quantization —
   over short windows in the real-VLC smoke. *Why deferred:* whether VLC
   clocks actually drift apart is unknowable without long recordings.
   *Unblocked by:* a real multi-minute session — play it end to end and watch
   for growing desync; if it drifts, reintroduce a heartbeat (QTimer ~2 s →
   nudge players toward the reference clock).

4. **`sync_engine.py` dataclass extraction** — `SourceOffset`/`SyncResult`
   are live (imported locally in `_on_sync_finished`); everything else in the
   module (`SyncWorker`, `compute_sync`, the zeros-on-failure audio loader)
   is unreachable and deliberately un-instrumented by K. *Why deferred:* pure
   refactor, no behavior at stake — doable anytime. Tracked as its own item
   in `docs/ROADMAP.md` Phase 5 (move the two dataclasses into their own
   module or `brain_battle_main`, delete the rest, run the smoke gate).

5. ~~**matplotlib references outside this file**~~ — **DONE in Phase 1.**
   `requirements.txt` (derived from actual imports, grep-verified) excludes
   it; `CLAUDE.md` and `README.md` both now say `pip install -r
   requirements.txt` instead of listing packages by hand. Only the
   historical comment/docstring mentions above (H's own entry) remain, which
   is correct — they describe what H removed, not a current dependency.

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
