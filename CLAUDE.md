# Brain Battle — Phase 2 (v2.1.0)

PyQt6 desktop app for neuroscience boxing capture: 4 USB cameras + 2 Muse-S EEG
headbands (Bluetooth), with a live capture tab, a sync/review tab, a labelling
tab, and an export pipeline. ~11k lines across 24 .py files.

## Running

```bash
pip install PyQt6 pyqtgraph opencv-python matplotlib pylsl muselsl scipy pandas ffmpeg-python numpy python-vlc bleak
python3 brain_battle_main.py
```

System binaries required: `ffmpeg` (audio extraction, export) and VLC (review
playback via python-vlc). Captures land in `Captures/` next to the source
(override with `BRAIN_BATTLE_CAPTURE_DIR`).

## Tests (`tests/` — plain scripts, `python3 tests/<name>.py`, nonzero exit on failure)

All need ffmpeg on PATH and stub python-vlc only when libVLC is absent.

- `test_smoke.py` — the "loads and stands up" floor and **acceptance gate for
  deletions/refactors**: fresh-subprocess import of every first-party module
  (dynamic discovery), import-time `_HW_ENCODER` == post-import detection
  (the check that would have caught the four-commit videotoolbox NameError
  bug), headless construction of all three tabs + app shell, session-timeline
  order, `__version__` in the window title. Run this before and after any
  module deletion (J/K) or structural change (H).
- `test_seek_alignment.py` — ground truth for the sync seek formula: a known
  transient must land at the same playhead time in every clip after seeks
  (the formula flip-flopped twice; never change it without this). Also pins
  the simultaneous-start path to all-zero seeks.
- `test_xcorr_confidence.py` — confidence metric discrimination (high on
  shared signal, near-zero on unrelated, monotone decay under noise) plus
  threading: worker → SyncResult → badge → honest `n/a (<method>)` CSV rows.
- `test_ring_buffer.py` — `_RingBuffer` chronological ordering, including
  the over-capacity write branch (latent bug fixed in `0de80f2`).

Broader end-to-end suites from the fix sessions (31-check acceptance, hybrid
audio/no-audio sync, export error-text, real-VLC playback smoke) were
session-local scratchpad scripts and are NOT checked in — the checked-in
suites above are the durable safety net.

## Project status

**IN PROGRESS — unified capture-time clock** on the long-lived `develop`
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
- STEP 4 (retire audio xcorr — MainEditorSyncWorker is now unused by the review
  path), STEP 5 (co-registered EEG export) — pending.

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

Every remaining item is in **Deferred work** below — nothing else is planned.

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
via `cv2.VideoWriter` (`usb_camera.py`), which writes **no audio track**. So
audio-xcorr sync can only work on externally recorded footage (e.g. SD-card
DSLR files uploaded manually); in-app captures don't need xcorr anyway — they
start simultaneously and share `phase_markers.csv` time.

## Module map

| File | Lines | Purpose |
|---|---|---|
| `brain_battle_main.py` | 2168 | Entry point + `SyncReviewTab`, `LabellingTab`, `MainEditorSyncWorker`, `BrainBattleApp` shell |
| `eeg_module.py` | ~1600 | Muse-S BLE scan/connect/stream, `_RingBuffer`, `EEGLiveWidget` + `EEGReviewWidget` (both pyqtgraph) |
| `design_tokens.py` | 741 | Theme system (dark/light), QSS factories |
| `live_session_tab.py` | 584 | Live Monitor tab — capture orchestration, emits `session_saved(session_root)` |
| `export_module.py` | 531 | `VideoExportWorker` (ffmpeg quad-grid), `LabelsExporter`, `FullSessionExportWorker` (zip), progress dialog |
| `usb_camera_slot.py` | 479 | One camera slot widget (preview + record state) |
| `sync_engine.py` | 465 | `SourceOffset`/`SyncResult` dataclasses (ACTIVELY USED) + `SyncWorker` QThread (unused) |
| `vlc_module.py` | 391 | `VLCVideoSlot`, `SyncPlaybackController` — labelling-tab playback |
| `usb_camera.py` | 361 | `USBCameraThread` — cv2 capture + `cv2.VideoWriter` recording (no audio track) |
| `sync_utils/video_sync.py` | 300 | ffmpeg preview/trim helpers |
| `eeg_plot_pg.py` | 276 | pyqtgraph live EEG plot (the fast path the live widget uses) |
| `sidebar_shell.py` | 274 | Sidebar navigation shell (replaced QTabWidget) |
| `session_controller.py` | 241 | Pure-logic phase state machine (IDLE→BASELINE→ROUND↔BREAK→DONE), 1 Hz QTimer |
| `session_setup_dialog.py` | 225 | Rounds/durations config dialog |
| `session_storage.py` | 218 | On-disk session layout, manifest, `phase_markers.csv` writer |
| `sync_utils/audio_analysis.py` | 215 | Audio extraction + xcorr delay estimation (`compute_delays`, `_xcorr_delay`) |
| `circular_timer.py` | 192 | Circular countdown dial |
| `usb_camera_panel.py` | 158 | 2×2 grid of camera slots, start/stop recording fan-out |
| `sync_utils/eeg_video_sync.py` | 154 | EEG-vs-video window comparison |
| `sync_beep.py` | 125 | Round-start beep |
| `labels_config.py` | 71 | Label palette persistence (`labels_data.json`) |
| `sync_utils/infobox.py` | 20 | Tiny message-box helper |

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
  most, misaligning external uploads by ~2× the true offset). Red-first
  regression test: `tests/test_seek_alignment.py` (3019ms spread before →
  19ms after). That line has flip-flopped twice — never change it without
  running that test.

- **E [MED] — DONE in `bfa0a36`.** Export failures now surface the tail of
  ffmpeg's stderr + exit code (deque captured during the progress loop);
  `_detect_hw_encoder` requires a real test-encode probe, not just an
  `-encoders` listing, falling through to libx264.

- **C [MED] — DONE in `16f91a2`.** Real xcorr confidence
  (baseline-corrected peak-to-second-peak prominence, `_peak_confidence`);
  wired through worker → `SourceOffset` → the `set_confidence` badge;
  `sync_offsets.csv` records the true value or a labeled
  `n/a (<method>)` sentinel for unmeasured sources. Discrimination test:
  `tests/test_xcorr_confidence.py`.

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

## Deferred work — THE one place to look

Everything not finished lives here, each with why it's deferred and what
unblocks it. Nothing else is planned or half-open elsewhere in this file.

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
   `eeg_module.py`, `live_session_tab.py`.

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

2. **Drift-correction decision** — there is NO active playback drift
   correction; lockstep rests solely on issuing `play()` to all four players
   in the same event-loop turn (the removed `_drift_hb` heartbeat was dead
   code, so nothing was lost). Held to ~250 ms — libVLC clock quantization —
   over short windows in the real-VLC smoke. *Why deferred:* whether VLC
   clocks actually drift apart is unknowable without long recordings.
   *Unblocked by:* a real multi-minute session — play it end to end and watch
   for growing desync; if it drifts, reintroduce a heartbeat (QTimer ~2 s →
   nudge players toward the reference clock).

3. **`sync_engine.py` dataclass extraction** — `SourceOffset`/`SyncResult`
   are live (imported locally in `_on_sync_finished`); everything else in the
   module (`SyncWorker`, `compute_sync`, the zeros-on-failure audio loader)
   is unreachable and deliberately un-instrumented by K. *Why deferred:* pure
   refactor, no behavior at stake — doable anytime. Move the two dataclasses
   into their own module (or `brain_battle_main`), delete the rest, run the
   smoke gate.

4. **matplotlib removal from requirements** — nothing imports it since H
   (grep-verified; only comment/docstring mentions remain). *Why deferred:*
   one-line change bundled better with the next environment touch — doable
   anytime. Drop it from the README pip line and any environment files.

## Conventions & gotchas

- Review playback uses **python-vlc** with a vmem→QPainter pipeline on macOS
  and HWND on Windows (`SyncReviewTab._load_video*`); labelling playback uses
  `vlc_module.VLCVideoSlot`. Careful with native-surface z-order hacks
  (`make_opaque_toolbar`, deferred `raise_()`).
- Theme changes restyle by string-replacing hex colors in child stylesheets
  (`_apply_theme`) — new hardcoded colors must be added to those maps.
- `session_controller.py` is deliberately Qt-optional (headless-testable).
- `phase_markers.csv` labels look like `BASELINE_START`, `ROUND_1_START`,
  `BREAK_1_START`, `SESSION_END`; `t_session_seconds` is monotonic time since
  session start — the natural sync base for in-app captures.
