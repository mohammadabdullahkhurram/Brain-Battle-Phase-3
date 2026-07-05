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
(override with `BRAIN_BATTLE_CAPTURE_DIR`). There is no test suite.

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
- The **REVIEW/SYNC half is only PARTIALLY migrated**. The Labelling tab's
  `_open_session_folder` (`brain_battle_main.py:1718-1806`) was migrated and
  correctly reads `continuous/` + `phase_markers.csv` — **use it as the
  reference pattern**. The Sync tab's `load_session_folder`
  (`brain_battle_main.py:1027-1071`) was NOT migrated and still globs
  `Round {n}/` folders that no longer exist. Most known bugs live in this gap.

A second structural consequence: all four in-app cameras (side AND POV) record
via `cv2.VideoWriter` (`usb_camera.py`), which writes **no audio track**. So
audio-xcorr sync can only work on externally recorded footage (e.g. SD-card
DSLR files uploaded manually); in-app captures don't need xcorr anyway — they
start simultaneously and share `phase_markers.csv` time.

## Module map

| File | Lines | Purpose |
|---|---|---|
| `brain_battle_main.py` | 2168 | Entry point + `SyncReviewTab`, `LabellingTab`, `MainEditorSyncWorker`, `BrainBattleApp` shell |
| `eeg_module.py` | 1600 | Muse-S BLE scan/connect/stream, `_RingBuffer`, `EEGLiveWidget` (pyqtgraph), `EEGReviewWidget` (matplotlib) |
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
| `sync_utils/eeg_module.py` | 1207 | **ORPHANED** — zero imports anywhere; older copy of eeg widgets |
| `sync_utils/infobox.py` | 20 | Tiny message-box helper |

## Confirmed issues (verified 2026-07-05, line numbers exact)

- **A [HIGH] Live→Review handoff silently broken for v2.x sessions.**
  `session_saved → sync_tab.load_session_folder(path, round_num=1)`
  (`brain_battle_main.py:2092-2094`). `load_session_folder` (`:1027`) globs
  `Round {n}/` (`:1043`) and silently returns if absent (`:1044-1045`) — but
  v2.x never creates round folders (`session_storage.py:204-211`). After every
  real capture, the Sync tab loads nothing with no error. Reference fix
  pattern: `LabellingTab._open_session_folder` (`:1718-1806`).

- **B [HIGH] Sync crashes on audio-less clips.** `_run_sync`
  (`brain_battle_main.py:900-912`) passes all sources to `compute_delays`
  with no audio-track filtering. In-app MP4s (cv2.VideoWriter) have no audio
  stream → `ffmpeg -vn` pipe yields an empty buffer
  (`sync_utils/audio_analysis.py:83-84`) → `np.convolve` in `_envelope`
  (`:99`) raises `ValueError: a cannot be empty` (reached via `_xcorr_delay`
  `:122-123`). A NaN delay would also crash `_clamp_ms`
  (`brain_battle_main.py:37-38`, `int(nan)`).

- **D′ [MED] Seek-offset sign inversion in synced playback.** Verified
  empirically: `_xcorr_delay` (`sync_utils/audio_analysis.py:112-137`)
  matches its docstring — positive = other starts LATER. Under that
  convention, the current seek formula `seek_ms = norm_delays`
  (`brain_battle_main.py:337`) is inverted: it makes the latest-starting clip
  seek the most, misaligning clips by 2×offset. The "previous formula"
  (`max_norm_delay - d`) that the comment at `:334-336` calls inverted was
  actually correct. Fix in the worker, not in `_xcorr_delay`.
  (Original finding D — "xcorr sign inverted vs docstring" — was NOT
  reproduced; docstring and implementation agree.)

- **C [MED] Sync confidence indicator is dead code.** `_xcorr_delay` returns
  only the bare delay (peak magnitude discarded). `_on_sync_finished`
  hardcodes confidence 1.0 for video (`brain_battle_main.py:927`) and 0.95
  for EEG (`:929`). `SourceRow.set_confidence` (`:284-291`) has zero call
  sites. Exported `sync_offsets.csv` confidence values are fabricated.

- **E [MED] Export shows a blank error on failure; encoder detection is
  optimistic.** The progress loop drains stderr via `readline()` to EOF
  (`export_module.py:252-256`), so `proc.stderr.read()` at `:275` returns
  empty → error dialog says "ffmpeg failed:" with nothing after it. Also
  `_detect_hw_encoder` (`:57-93`) only checks ffmpeg's `-encoders` listing,
  not that the GPU is usable — it can pick `h264_nvenc` with no NVIDIA card
  and fail at runtime with the same blank error.

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

- **H [LOW] Review-EEG lag.** `EEGReviewWidget` (`eeg_module.py:1355`) is
  matplotlib; `set_playhead` (`:1526`) calls `set_xlim` per tick (`:1534`)
  forcing full re-raster, and band-power mode recomputes a spectrum over a
  10 s window each tick (`:1537`, `:1583-1599`). The LIVE widget was already
  ported to pyqtgraph (`eeg_plot_pg.py`) — mirror that.

- **I [LOW] session_storage.py docstring/API vestiges.** Top-of-file
  docstring (`session_storage.py:1-27`) still describes the v1.x per-phase /
  "SD-card footage" model; `phase_dir`/`side_cams_dir`/`pov_cams_dir`/
  `muse_dir` (`:99-119`) return `Round N/` paths that are never created.

- **J [CLEANUP] Orphans.** `sync_utils/eeg_module.py` (1207 lines) has zero
  imports — deletable. `SyncWorker` import at `brain_battle_main.py:25` is
  unused, BUT `SourceOffset` and `SyncResult` from `sync_engine` ARE used by
  the active sync/export path (`:925-940`) — don't delete `sync_engine.py`
  wholesale.

- **K [DIAGNOSTICS] No logging.** Zero use of the `logging` module; 55
  `print()` calls and 34 `except: pass`-style blocks swallow errors silently.

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
