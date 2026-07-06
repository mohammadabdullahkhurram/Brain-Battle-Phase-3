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

## Project status (2026-07-06)

**Both phases are COMPLETE** on branch `fix/v2-migration-review-path`:
functional fixes (A/B, D′, E, C — verified, including a real-libVLC playback
smoke of the Live→Review handoff) and cleanup (G/I/F, smoke gate, J
deletions, K logging sweep, H pyqtgraph port). What remains is deferred, not
planned work: the eyes-on-glass visual check (playback + review-EEG
rendering, needs real capture data), the drift-correction decision, and the
`sync_engine.py` dataclass extraction — see KNOWN LIMITATIONS / backlog
notes below.

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

### DONE (functional-fix phase, commits on fix/v2-migration-review-path)

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

### KNOWN LIMITATIONS (discovered during the fixes — by design or unverifiable here)

- **Sync confidence measures internal consistency, not correctness.** A high
  score means the correlation peak dominates its window — coincident loud
  transients in genuinely unrelated audio can still score high (inherent to
  envelope xcorr, not fixable by the metric).
- **Encoder validation's "listed-but-no-hardware" fallback** (e.g. nvenc on a
  GPU-less box) is proven by forced-probe-failure unit tests only; it has
  never been exercised on real GPU-less hardware.
- **VLC playback smoke verified up to correct decoded pixels in the render
  buffer** (offscreen Qt, real libVLC, 4-cam lockstep ~250ms = clock
  quantization). The final on-screen blit has not been eyes-verified.
- **Review-EEG rendering (post-H pyqtgraph port) is likewise not
  eyes-verified** — constructs, runs all public methods, and is measurably
  fast on synthetic data, but whether the waveform/band-power LOOK right
  while scrubbing needs real capture data on glass. One deferred visual
  check together with the item above: launch the app, open a real session,
  play + scrub, watch all four cams and both EEG panes.

### OPEN (cleanup phase: G/I/F, then H, then J/K)

- **F [LOW] Version strings disagree everywhere.** Manifest writes
  `"Phase 2"` (`session_storage.py:170`); folder says v2.1.0; README says
  `v1.0 // Software Phase 2` (line 4), "Phase 2.2" (line 677), and
  "Phase 2.4 / v1.4" (lines 728-729); CHANGELOG goes to Phase 2.17. No
  `__version__` constant exists anywhere.

- **G [LOW] Ring buffer latent ordering bug.** `_RingBuffer.extend`
  (`eeg_module.py:95-117`): the `n >= capacity` branch (`:102-107`) writes
  chronological data at index 0 but leaves `_n_written` unaligned, so
  `to_array` (`:122-135`) rotates samples out of order. Never triggered today
  (Muse chunks ≤ 12 into a 7680 buffer). Fix: set `_n_written = capacity` in
  that branch (subsequent writes/reads then stay consistent).

- **H [LOW] Review-EEG lag — DONE (rendering unverified).** `EEGReviewWidget`
  ported to pyqtgraph mirroring `eeg_plot_pg.py`: full trace uploaded once at
  `load()`, playhead ticks move an `InfiniteLine` + shift the ViewBox;
  band-power welch recomputes only per ≥0.2 s playhead movement. Benchmarked
  on 3 min of synthetic 4-channel EEG at the app's real 100 ms tick rate:
  waveform 118→15 ms/tick, band-power 102→11 ms/tick from the renderer alone
  (→4.6 ms with the throttle), load 403→44 ms. The old widget could not keep
  up with the 100 ms tick; the new one has ~7× headroom. API unchanged —
  no other file needed edits. **Rendering correctness is NOT eyes-verified**
  (needs real capture data on glass) — bundled with the deferred visual
  check of the Live→Review handoff in KNOWN LIMITATIONS.
  Side effect: this removed the repo's last matplotlib import — matplotlib
  is now droppable from requirements (only comment/docstring mentions
  remain; not yet removed from README's pip line).

- **I [LOW] session_storage.py docstring/API vestiges.** Top-of-file
  docstring (`session_storage.py:1-27`) still describes the v1.x per-phase /
  "SD-card footage" model; `phase_dir`/`side_cams_dir`/`pov_cams_dir`/
  `muse_dir` (`:99-119`) return `Round N/` paths that are never created.

- **J [CLEANUP] Orphans — DONE.** Deleted `sync_utils/eeg_module.py`
  (1207-line orphan, grep-confirmed zero importers; its "(v3)" docstring
  label was stale — git showed it untouched since the initial folder
  restructure while root `eeg_module.py` got all later work). Removed the
  fully-dead `from sync_engine import SyncWorker, SyncResult` line (both
  names unused — the live path re-imports `SourceOffset`/`SyncResult`
  locally in `_on_sync_finished`). `sync_engine.py` itself STAYS for those
  dataclasses. Also deleted the never-created `_drift_hb`/`_nudge_drift`
  machinery — see the lockstep note below.

- **DEFERRED [DECISION] Long-session playback lockstep rests solely on
  simultaneous `play()`** — no active drift correction; the removed
  `_drift_hb` heartbeat was dead code (guarded but never created), so
  nothing was lost. Held to ~250ms (libVLC clock quantization) over short
  windows in the real-VLC smoke. Revisit whether drift correction is needed
  once real multi-minute capture data exists to observe actual drift.

- **K [DIAGNOSTICS] Logging — DONE.** Central config in `log_setup.py`
  (console at INFO; `attach_session_log()` adds a DEBUG file handler inside
  each session folder). All `print()` calls replaced with module loggers.
  Silent `except` blocks triaged: real-failure-hiding handlers (probes,
  manifest/marker writes, session-end recording stops, frame writes) now log
  at warning/error; legitimate-quiet teardown races log at debug or carry an
  intent comment; control flow unchanged everywhere. Level policy lives in
  `log_setup.py`'s docstring. Most failure-path log lines are
  verified-by-inspection (their triggers need real hardware/corrupt data).

- **NOTE [CLEANUP-CANDIDATE] `sync_engine.py` is half-orphaned.** Its
  `SourceOffset`/`SyncResult` dataclasses are load-bearing (imported locally
  in `_on_sync_finished`), but everything else — `SyncWorker`, `compute_sync`,
  `_load_video`'s zeros-on-failure audio fallback — is unreachable since the
  J deletions (nothing imports the module except that dataclass import). K
  deliberately did NOT instrument the dead path. A future focused cleanup
  could move the two dataclasses out and delete the rest.

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
