# Brain Battle

**v2.1.0** — desktop capture, sync, review, and labelling suite for neuroscience
boxing sessions: 4 USB cameras + 2 Muse-S EEG headbands, one time-aligned dataset.

Brain Battle is a Community Impact Project developed at Constructor University
Bremen. It records EEG from two boxers (Muse-S over Bluetooth) alongside up to
four camera angles, aligns everything onto one global timeline, lets a reviewer
place event labels (punches, rounds, stoppages), and exports the full
time-aligned dataset for analysis. It is a PyQt6 app, ~11k lines of Python.

---

## Requirements

**Python** (3.10+):

```bash
pip install PyQt6 pyqtgraph opencv-python pylsl muselsl scipy pandas ffmpeg-python numpy python-vlc bleak
```

**System dependencies:**

- `ffmpeg` on PATH — audio extraction for sync, export encoding, stream probing
  (`brew install ffmpeg` on macOS)
- **VLC** (the desktop app, which provides libVLC) — synced review playback via
  `python-vlc`

Hardware for a full capture: 2× Muse-S headbands (pre-mapped: `MuseS-7538` =
BOXER A, `MuseS-7564` = BOXER B), 2× side cameras (any UVC-capable DSLR over
USB-C, or via an HDMI→USB capture card), 2× POV cameras via a UVC FPV receiver.
All four cameras use one unified UVC ingest path — no RTMP/WiFi involved.

## Running

```bash
python3 brain_battle_main.py
```

- Captures are written to `Captures/` next to the source; override with the
  `BRAIN_BATTLE_CAPTURE_DIR` environment variable.
- The authoritative version lives in `version.py` (`__version__ = "2.1.0"`) and
  is surfaced in the window title and each session's manifest.
- Logging: console at INFO; every capture session also gets a DEBUG-level
  `session_*.log` inside its own folder (see `log_setup.py`).

## Workflow — three tabs

```
LIVE MONITOR  →  SYNC + REVIEW  →  LABELLING  →  Export
```

1. **Live Monitor** — connect cameras and headbands, START SESSION, configure
   rounds/breaks/baseline. The controller runs an automated loop
   (baseline → round → break → …) with sync beeps at each round start.
   Recording is **one continuous take** per source for the whole session;
   phase transitions are written as timestamps to `phase_markers.csv`, not as
   separate files.
2. **Sync + Review** — auto-loads the finished session (or use OPEN SESSION
   FOLDER for a past one). AUTO-SYNC aligns the sources — see
   [Synchronisation](#synchronisation) — then all four videos play in lockstep
   with both EEG traces following the playhead. Phase markers appear as
   colour-coded chips on the timeline.
3. **Labelling** — load the session (OPEN SESSION FOLDER), place event labels
   at the playhead from a configurable palette (keys `1`–`9` select+place,
   `Space` toggles play/pause). Phase markers auto-load as read-only entries.
4. **Export** — from the Sync tab: a ZIP with a quad-grid merged MP4
   (hardware-encoded where a working encoder is detected), original videos,
   EEG CSVs, labels CSV, and per-source sync offsets.

## Synchronisation

Alignment is **audio-aware**, decided per sync run:

- **In-app captures** have no audio track (OpenCV's `VideoWriter` writes
  video-only), and they don't need one — all sources start simultaneously, so
  every offset is 0 and phases come from `phase_markers.csv`
  (method: `simultaneous_start`).
- **External footage** (e.g. SD-card DSLR files uploaded manually) is aligned
  by audio cross-correlation among the audio-bearing clips only: bandpassed
  envelope xcorr at 4 kHz, ±30 s search window. Each measured offset carries a
  real **confidence** score (baseline-corrected peak prominence, 0–1) shown as
  a ✓/~/? badge per source (method: `xcorr`).

`sync_offsets.csv` in every export records the true per-source method and the
measured confidence — or an explicit `n/a (<method>)` where alignment was
assumed rather than measured. The seek formula that maps offsets to per-player
seeks is guarded by `tests/test_seek_alignment.py`; do not change it without
running that test.

## Architecture

**Shell & design**

| Module | Purpose |
|---|---|
| `brain_battle_main.py` | Entry point; `SyncReviewTab`, `LabellingTab`, `MainEditorSyncWorker`, app shell |
| `sidebar_shell.py` | Sidebar navigation shell hosting the three tabs |
| `design_tokens.py` | Dark/light theme system, QSS factories |
| `version.py` | Single authoritative `__version__` |
| `log_setup.py` | Central logging config: console + per-session file handler |

**Live capture**

| Module | Purpose |
|---|---|
| `live_session_tab.py` | Live Monitor tab — capture orchestration, emits `session_saved` |
| `session_controller.py` | Pure-logic phase state machine (baseline→rounds→breaks), 1 Hz timer |
| `session_storage.py` | On-disk session layout, manifest, `phase_markers.csv` writer |
| `session_setup_dialog.py` | Rounds/durations config dialog |
| `circular_timer.py` | Circular countdown dial |
| `sync_beep.py` | Round-start beep |

**Cameras**

| Module | Purpose |
|---|---|
| `usb_camera.py` | `USBCameraThread` — cv2 capture + `cv2.VideoWriter` recording (video-only, no audio) |
| `usb_camera_slot.py` | One camera slot widget (device pick, preview, record state) |
| `usb_camera_panel.py` | 2×2 grid of slots; start/stop recording fan-out |

**EEG**

| Module | Purpose |
|---|---|
| `eeg_module.py` | Muse-S BLE scan/connect/stream, ring buffer, CSV recorder, `EEGLiveWidget` + `EEGReviewWidget` (both pyqtgraph) |
| `eeg_plot_pg.py` | pyqtgraph waveform/band-power plot widgets shared by live + review |

**Sync, playback, export**

| Module | Purpose |
|---|---|
| `sync_utils/audio_analysis.py` | Audio extraction, xcorr delay + confidence, audio-stream probing |
| `sync_utils/video_sync.py` | ffmpeg preview/trim helpers |
| `sync_utils/eeg_video_sync.py` | EEG-vs-video window comparison via `creation_time` metadata |
| `sync_engine.py` | `SourceOffset`/`SyncResult` dataclasses used by the sync/export path (the rest of the module is a dormant legacy path) |
| `vlc_module.py` | `VLCVideoSlot`, `SyncPlaybackController` — labelling-tab playback |
| `export_module.py` | Quad-grid ffmpeg merge (validated hardware encoder with libx264 fallback), labels CSV, session ZIP |
| `sync_utils/infobox.py` | Tiny message-box helper |

**Labels**

| Module | Purpose |
|---|---|
| `labels_config.py` | Label palette persistence (`labels_data.json`) |

## Data & output formats

Each capture session creates one folder:

```
Brain Battle <YYYY-MM-DD HH-MM-SS>/
    continuous/
        Side Cams/*.mp4        # one MP4 per side camera, whole session
        POV Cams/*.mp4         # one MP4 per POV camera, whole session
        Muse data/*.csv        # one CSV per headband, whole session
    phase_markers.csv          # phase boundaries as timestamps (see below)
    session_manifest.json      # app/version, config, per-phase start/end times
    session_<timestamp>.log    # DEBUG-level diagnostic log for the session
```

Recording never stops between phases; downstream tools derive phase boundaries
from `phase_markers.csv`, **not** from folders. (The v1.x per-round folder
model — `baseline/`, `Round 1/`, … — is gone.)

- **`phase_markers.csv`** — `t_session_seconds, phase_id, label, round_num,
  unix_time`; one row per transition (`BASELINE_START`, `ROUND_1_START`,
  `BREAK_1_START`, …, `SESSION_END`). `t_session_seconds` is monotonic time
  since session start — the natural alignment base for in-app captures.
- **EEG CSV** — the in-app recorder writes header
  `TP9,AF7,AF8,TP10,AUX,timestamps` at 256 Hz (`timestamps` = Unix-epoch
  floats). The review widgets look columns up by name, so externally recorded
  muselsl CSVs (`timestamps` first, `Right AUX`) also load.
- **Labels CSV** — `timestamp_sec, timestamp_str, label, color`.
- **`sync_offsets.csv`** — `source, offset_sec, method, confidence,
  is_reference` per video/EEG source; confidence is measured (xcorr) or an
  explicit `n/a (<method>)` sentinel.
- **Export ZIP** — `merged_video.mp4` (2×2 quad grid), `labels.csv`,
  `sync_offsets.csv`, `eeg/eeg_0N.csv`, `videos/video_0N.*` (originals),
  `README.txt`.

## Testing

Plain scripts, no framework; each exits nonzero on failure. All need `ffmpeg`
on PATH and stub `python-vlc` only when libVLC is absent.

```bash
python3 tests/test_smoke.py            # import/construct floor — gate for deletions/refactors:
                                       # fresh-subprocess import of every module, encoder-probe
                                       # invariant, headless construction of all tabs + shell
python3 tests/test_seek_alignment.py   # sync seek-formula ground truth: a known transient must
                                       # land at the same playhead time in every clip
python3 tests/test_xcorr_confidence.py # confidence discrimination (high shared / low unrelated /
                                       # decays under noise) + threading into badge and CSV
python3 tests/test_ring_buffer.py      # EEG ring-buffer chronological ordering incl. the
                                       # over-capacity write branch
```

## Known limitations — verify with real data

- **Rendering needs eyes-on-glass.** Synced playback (decode, lockstep,
  markers, sync) and the pyqtgraph review-EEG widget are verified headless up
  to correct pixels in the render buffer and measured scrub performance — but
  neither has been *watched* with real capture data. First real session:
  launch the app, open it, play + scrub, watch all four cams and both EEG
  panes.
- **No active playback drift correction.** Lockstep rests on starting all four
  players simultaneously (held to ~250 ms clock quantisation over short
  windows). Whether long multi-minute sessions drift is an open question that
  needs real footage to answer.
- **Sync confidence measures internal consistency, not correctness** — 
  coincidentally aligned loud transients in unrelated audio can score high
  (inherent to envelope xcorr).
- **Encoder fallback on GPU-less machines** (e.g. an nvenc build with no
  NVIDIA card) is covered by forced-failure tests, not exercised on real such
  hardware.

## Project information

| | |
|---|---|
| Project | Brain Battle — Neuroscience Sports Capture System |
| Institution | Constructor University Bremen (Community Impact Project) |
| Version | v2.1.0 (authoritative: `version.py`) |
| GitHub | https://github.com/mohammadabdullahkhurram/Brain-Battle-Phase-2-v2.1.0 |
| Hardware | 2× Muse-S (`MuseS-7538`, `MuseS-7564`), 2× DSLR (USB-C UVC), 2× POV (FPV receiver → USB) |

Development history lives in `CHANGELOG.md` (historical "Phase 2.N" dev-log
numbering) and `CLAUDE.md` (current project context, issue backlog, test map).
