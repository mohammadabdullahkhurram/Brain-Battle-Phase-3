# Brain Battle

**v3.0.0-dev** (on `develop`; `main` ships v2.1.0) — desktop capture, sync,
review, and labelling suite for neuroscience boxing sessions: 4 USB cameras +
2 Muse-S EEG headbands, one time-aligned dataset.

> **3.0.0 is a breaking change.** Alignment is now driven by a capture-time
> clock (per-frame and per-EEG-sample timestamps against a shared origin).
> Sessions recorded before 3.0 lack this data and are **not** supported —
> there is no legacy fallback. Record fresh with 3.0.

Brain Battle is a Community Impact Project developed at Constructor University
Bremen. It records EEG from two boxers (Muse-S over Bluetooth) alongside up to
four camera angles, aligns everything onto one global timeline, lets a reviewer
place event labels (punches, rounds, stoppages), and exports the full
time-aligned dataset for analysis. It is a PyQt6 app, ~11k lines of Python.

---

## Requirements

**Python** (3.10+):

```bash
pip install -r requirements.txt
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
- The authoritative version lives in `version.py` (`__version__`) and is
  surfaced in the window title and each session's manifest.
- Logging: console at INFO; every capture session also gets a DEBUG-level
  `session_*.log` inside its own folder (see `log_setup.py`).

## Workflow — three tabs

```
LIVE MONITOR  →  REVIEW  →  LABELLING  →  Export
```

1. **Live Monitor** — connect cameras and headbands, START SESSION, configure
   rounds/breaks/baseline. The controller runs an automated loop
   (baseline → round → break → …) with sync beeps at each round start.
   Recording is **one continuous take** per source for the whole session;
   phase transitions are written as timestamps to `phase_markers.csv`, not as
   separate files.
2. **Review** — auto-loads the finished session (or use OPEN SESSION
   FOLDER for a past one). ALIGN reads the sources' alignment — see
   [Synchronisation](#synchronisation) — then all four videos play in lockstep
   with both EEG traces following the playhead. Phase markers appear as
   colour-coded chips on the timeline.
3. **Labelling** — load the session (OPEN SESSION FOLDER), place event labels
   at the playhead from a configurable palette (keys `1`–`9` select+place,
   `Space` toggles play/pause). Phase markers auto-load as read-only entries.
4. **Export** — from the Review tab: a ZIP with a quad-grid merged MP4
   (hardware-encoded where a working encoder is detected), original videos,
   EEG CSVs, a co-registered EEG file (both boxers on one shared time axis),
   labels CSV, and per-source sync offsets.

## Synchronisation

Alignment is driven by a **capture-time clock**, not audio — this is a
breaking change from pre-3.0 versions (see the warning at the top of this
file). At recording start, one shared origin `(t0_monotonic, t0_unix)` is
captured once and threaded into every camera and every connected Muse-S, so
every source stamps itself against the same clock:

- Each camera writes a per-frame sidecar `<mp4-stem>.timestamps.csv`
  (`frame_index, t_session_seconds, t_monotonic, t_unix`) next to its MP4.
- Each EEG recording gets a `t_session_seconds` column alongside its raw
  Unix-epoch `timestamps`.
- `phase_markers.csv` was already on this axis (`t_session_seconds` relative
  to the shared origin).

ALIGN in the Review tab reads — not discovers — the alignment: each camera's
measured start (from its sidecar's first row) becomes its offset, the
latest-starting source becomes the playhead-zero reference, and every other
player seeks by `(playhead_zero − its own start)`. There is nothing to
correlate and no audio involved; in-app cameras never record audio in the
first place (OpenCV's `VideoWriter` is video-only).

`sync_offsets.csv` in every export records `method = capture_clock` and the
honest sentinel `confidence = n/a (capture_clock)` for every source — there
is no correlation score to report under this method. The seek formula is
guarded by `tests/test_capture_clock.py`; do not change it without running
that test.

**Known gap:** the EEG *clock source* (muselsl's wall-clock derivation + BLE
transport latency) has not been verified against a real Muse-S band, and the
Labelling tab's own video transport is not yet converted onto this shared
axis. See `CLAUDE.md`'s "Deferred work" section and `docs/ROADMAP.md`.

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
| `sync_engine.py` | `SourceOffset`/`SyncResult` dataclasses used by the sync/export path (the rest of the module — audio cross-correlation — is dormant, dead code; see `docs/ROADMAP.md`) |
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
  is_reference` per video/EEG source; `method` is always `capture_clock` and
  `confidence` is always the honest sentinel `n/a (capture_clock)` — there is
  no correlation score under this alignment method.
- **Export ZIP** — `merged_video.mp4` (2×2 quad grid), `labels.csv`,
  `sync_offsets.csv`, `eeg/eeg_0N.csv`, `eeg_coregistered.csv` (both boxers on
  one shared time axis), `videos/video_0N.*` (originals), `README.txt`.

## Testing

Plain scripts, no framework; each exits nonzero on failure. All need `ffmpeg`
on PATH and stub `python-vlc` only when libVLC is absent.

```bash
python3 tests/test_smoke.py          # import/construct floor — gate for deletions/refactors:
                                     # fresh-subprocess import of every module, encoder-probe
                                     # invariant, headless construction of all tabs + shell
python3 tests/test_capture_clock.py  # capture-clock alignment: reconciliation arithmetic,
                                     # marker/EEG co-registration, manifest round-trip, the
                                     # review-tab seek-formula invariant, co-registered EEG export
python3 tests/test_ring_buffer.py    # EEG ring-buffer chronological ordering incl. the
                                     # over-capacity write branch
```

## Known limitations — verify with real data

- **Rendering needs eyes-on-glass.** Synced playback (decode, lockstep,
  markers, capture-clock alignment) and the pyqtgraph review-EEG widget are
  verified headless up to correct pixels in the render buffer, measured scrub
  performance, and alignment arithmetic — but nobody has *watched* it with
  real capture data. First real session: launch the app, open it, play +
  scrub, watch all four cams and both EEG panes.
- **The EEG clock source is unverified.** Alignment arithmetic is unit-tested,
  but whether muselsl's wall-clock timestamps + BLE transport latency place
  EEG accurately on the shared axis has not been checked against a real
  Muse-S band. See `CLAUDE.md`'s "Deferred work."
- **No active playback drift correction.** Lockstep rests on starting all four
  players simultaneously (held to ~250 ms clock quantisation over short
  windows). Whether long multi-minute sessions drift is an open question that
  needs real footage to answer.
- **Encoder fallback on GPU-less machines** (e.g. an nvenc build with no
  NVIDIA card) is covered by forced-failure tests, not exercised on real such
  hardware.

## Project information

| | |
|---|---|
| Project | Brain Battle — Neuroscience Sports Capture System |
| Institution | Constructor University Bremen (Community Impact Project) |
| Version | develop v3.0.0-dev · main v2.1.0 (authoritative: `version.py`) |
| GitHub | https://github.com/mohammadabdullahkhurram/Brain-Battle-Phase-3 |
| Hardware | 2× Muse-S (`MuseS-7538`, `MuseS-7564`), 2× DSLR (USB-C UVC), 2× POV (FPV receiver → USB) |

Development history lives in `CHANGELOG.md` (historical "Phase 2.N" dev-log
numbering) and `CLAUDE.md` (current project context, issue backlog, test map).
Planned future work lives in `docs/ROADMAP.md`.
