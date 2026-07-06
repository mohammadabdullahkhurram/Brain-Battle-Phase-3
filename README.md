# BRAIN BATTLE
### Neuroscience Sports Capture System
**Constructor University Bremen — Community Impact Project**
`v2.1.0  //  Software Phase 2`

---

## Table of Contents

1. [Overview](#1-overview)
2. [File Structure](#2-file-structure)
3. [Installation](#3-installation)
4. [Hardware Setup](#4-hardware-setup)
5. [Tab 01 — Live Monitor](#5-tab-01--live-monitor)
6. [Tab 02 — Sync + Review](#6-tab-02--sync--review)
7. [Tab 03 — Labelling](#7-tab-03--labelling)
8. [Label Configuration](#8-label-configuration)
9. [Sync Engine — Technical Reference](#9-sync-engine--technical-reference)
10. [Module Reference](#10-module-reference)
11. [Troubleshooting](#11-troubleshooting)
12. [Known Limitations](#12-known-limitations)
13. [Dependencies](#13-dependencies)
14. [Project Information](#14-project-information)

---

## 1. Overview

Brain Battle is a CIP (Community Impact Project) developed at Constructor University Bremen. The system captures, synchronises, reviews, and labels neuroscience data collected during boxing sessions — specifically EEG brain activity from Muse-S headbands worn by two boxers, combined with multi-angle camera footage from up to four cameras.

The goal is to provide researchers with a complete, time-aligned dataset: every punch, clinch, knockdown, or round event is timestamped against both the EEG signals and all camera angles, enabling post-session analysis of the neurological correlates of combat sports performance.

### 1.1 What the System Records

- EEG brainwave data (delta, theta, alpha, beta, gamma bands) from **2x Muse-S headbands** worn by both boxers simultaneously
- Video footage from up to **4 cameras**: 2x DSLR cameras and 2x action/360 cameras
- **Event labels** placed manually during review: punch types, round markers, dominant fighter periods, referee stoppages
- **Synchronisation offsets** between all sources so every timestamp maps to a common global timeline

### 1.2 Workflow at a Glance

```
1. Hardware setup      →  Connect Muse-S headbands, plug in USB cameras
2. Tab 01 LIVE MONITOR →  START SESSION → configure rounds/break → automated capture loop
                          (baseline → round → break → round → … with sync beeps and
                          per-round folders auto-created)
3. Tab 02 SYNC+REVIEW  →  Round 1 files auto-load post-session; AUTO-SYNC + playback
4. Tab 03 LABELLING    →  Place event labels on the timeline
5. Export              →  Quad-grid merged video + labels CSV + ZIP archive
```

---

## 2. File Structure

All files must be in the same directory.

```
Software Phase 2/
├── brain_battle_main.py      # Main application — Sync + Review, Labelling, main window
├── live_session_tab.py       # LIVE MONITOR tab — automated round-loop orchestration
├── session_controller.py     # State machine: BASELINE → ROUND → BREAK → … → DONE
├── session_setup_dialog.py   # Modal dialog for rounds / durations / baseline
├── session_storage.py        # Creates the Brain Battle <timestamp>/ tree + manifest
├── circular_timer.py         # Apple-style circular dial widget (QPainter)
├── sync_beep.py              # 1 kHz sync beep generator + QSoundEffect player
├── usb_camera.py             # USB/UVC camera ingest thread (OpenCV VideoCapture)
├── usb_camera_slot.py        # Per-camera live-preview widget (pyqtgraph ImageItem)
├── usb_camera_panel.py       # 2×2 USB camera grid with SIDE/POV routing
├── sync_engine.py            # Audio cross-correlation sync engine + SyncWorker QThread
├── eeg_module.py             # Live EEG streaming (EEGLiveWidget) + review (EEGReviewWidget)
├── eeg_plot_pg.py            # pyqtgraph-backed waveform + band-power panels (fast live path)
├── vlc_module.py             # VLC video playback slots + SyncPlaybackController (Tabs 02/03)
├── export_module.py          # ffmpeg quad-grid video export, labels CSV, ZIP packaging
├── design_tokens.py          # Design system — palette, typography, QSS factories, Lucide icons
├── sidebar_shell.py          # Left-sidebar navigation (Figma/Linear/VS Code pattern)
├── labels_config.py          # Static label definitions — read on startup, never modified
├── labels_data.json          # Auto-generated — live label palette state (created on first run)
├── Captures/                 # Auto-created — structured per-session capture output (see §5.7)
└── Muse Recordings/          # Legacy EEG output folder (unused by new automated workflow)
```

> **Note:** `labels_data.json` is created automatically the first time you add or remove a label in the app. If it does not exist, the app falls back to the defaults in `labels_config.py`.

> **Note:** `Captures/` is created automatically on the first session. Override the location by setting the `BRAIN_BATTLE_CAPTURE_DIR` environment variable — useful for server deployments where captures should be written to a dedicated data drive.

---

## 3. Installation

### 3.1 System Requirements

- macOS (primary target), Windows, or Linux
- Python 3.10 or higher
- `ffmpeg` installed and on PATH
- VLC Media Player installed (for video playback in Tabs 02 and 03)
- Bluetooth adapter (for Muse-S EEG headband connection)

### 3.2 Python Dependencies

```bash
pip install PyQt6 pyqtgraph opencv-python matplotlib pylsl muselsl scipy pandas ffmpeg-python numpy python-vlc
```

New additions (Phase 2+):
- **`pyqtgraph`** — fast live EEG waveform / band-power rendering + per-frame camera display. Replaces matplotlib for the performance-critical live path (≥60 FPS vs 5-15 FPS for matplotlib).
- **`opencv-python`** — USB/capture-card camera ingest via `cv2.VideoCapture` with per-OS backend selection.

`matplotlib` is retained for the post-session review / labelling tabs where static plot quality matters more than frame rate.

On macOS, install `ffmpeg` and VLC via Homebrew:

```bash
brew install ffmpeg
brew install --cask vlc
```

### 3.3 Running the App

```bash
python3 brain_battle_main.py
```

---

## 4. Hardware Setup

### 4.1 Muse-S EEG Headbands

Brain Battle is pre-configured for two specific Muse-S devices:

| Device Name  | Role    | Widget   |
|--------------|---------|----------|
| `MuseS-7538` | BOXER A | EEG 1    |
| `MuseS-7564` | BOXER B | EEG 2    |

**To connect a headband:**

1. Power on the Muse-S (hold button until LED blinks)
2. In Tab 01, click **DEVICE** on the EEG widget
3. Click **SCAN FOR DEVICES** — scan takes up to 20 seconds
4. Select the device from the list and click **CONNECT**
5. Status dot turns green when the LSL stream is live

> **Warning:** If the device disconnects mid-recording, the app shows a red status indicator and automatically saves whatever EEG data was captured. Click **RECONNECT** to re-establish the stream.

### 4.2 DSLR Side Cameras (USB-C)

The two side cameras connect to the laptop directly over **USB-C**. Modern DSLRs (Lumix, Sony, Fujifilm, Canon) present themselves as UVC webcams when plugged in — no capture card required.

**Steps:**

1. Power on the camera and put it in **Streaming / Webcam mode** (consult camera manual — on Lumix this is "Live Streaming via USB")
2. Connect to the laptop with USB-C
3. macOS: QuickTime → New Movie Recording → check the camera appears in the dropdown
4. Windows: Camera app → verify the camera appears in the device list
5. In Brain Battle, on a **SIDE** slot click **SELECT** → pick the camera → **CONNECT**

If your camera does not support UVC streaming mode natively, use a **generic HDMI → USB capture card** (Elgato Cam Link 4K, etc.) between the camera's HDMI output and a USB port. The app sees the capture card as a standard UVC device.

### 4.3 POV Cameras (via FPV Receiver)

The POV cameras stream from an **FPV receiver attached to the laptop** (e.g. RapidFire, DJI Goggles RX module, or any standard diversity receiver with USB output). The receiver presents as a UVC capture device.

**Steps:**

1. Power on the FPV receiver and make sure it's paired to the POV transmitter
2. Connect the receiver's USB output to the laptop
3. On a **POV** slot click **SELECT** → pick the receiver → **CONNECT**

The receiver typically enumerates as "USB Video Device" or a vendor-specific name depending on the model. Use the **RESCAN ALL** button if the receiver doesn't appear on first open.

> **Note:** Because all four cameras are now standard UVC devices, Brain Battle uses a single unified ingest path (`usb_camera.USBCameraThread`). No MonaServer, no RTMP, no WiFi involved — works on any network including eduroam.

---

## 5. Tab 01 — Live Monitor

The Live Monitor tab runs an **automated round-based capture loop** with USB / UVC camera ingest for all four video streams. After you configure the session once, the software drives baseline → rounds → breaks without operator intervention, automatically segmenting recordings per round and saving them into a structured directory tree.

### 5.1 Workflow at a Glance

```
[ START SESSION ] ─→ setup dialog ─→ BASELINE ─→ ROUND 1 ─→ BREAK 1 ─→ ROUND 2 ─→ … ─→ DONE
                         │                ↑                               ↑
                  prompts for:       sync beep                       sync beep
                  - rounds (N)        + auto-record                  + auto-record
                  - round min         + folder created               + folder created
                  - break sec
                  - baseline sec
```

Each phase automatically starts and stops per-round EEG recordings and camera recordings. The circular Apple-style dial shows the current phase, countdown, and round position. A loud 1 kHz sync beep fires at **BASELINE start** and at **every ROUND start** — this is both an audible cue for the fighters and an alignment marker that the post-session audio-based sync engine can lock onto.

### 5.2 Layout

OBS-style: cameras dominate the centre, session controls and EEG live in a right-hand rail. Styled with Constructor University brand colours (Sangria `#9D0F01` primary, Prussian Blue `#00204D` secondary, warm off-white `#F4F3EE` text on tuned dark backgrounds).

```
┌──────────────────────────────────────────────────┬────────────────────┐
│   [ toolbar: LIVE MONITOR  ·  phase chip  ·  START SESSION ]          │
├──────────────────────────────────────────────────┼────────────────────┤
│                                                  │   SESSION          │
│   ┌────────────────┐  ┌────────────────┐         │   ● circular       │
│   │  SIDE 01       │  │  SIDE 02       │         │     timer          │
│   │  (USB-C UVC)   │  │  (USB-C UVC)   │         │                    │
│   └────────────────┘  └────────────────┘         │   NEXT · BREAK 2   │
│                                                  │   …/Brain Battle…  │
│   ┌────────────────┐  ┌────────────────┐         ├────────────────────┤
│   │  POV  01       │  │  POV  02       │         │   EEG STREAMS  2/2 │
│   │  (receiver)    │  │  (receiver)    │         │   ● EEG 1          │
│   └────────────────┘  └────────────────┘         │   [4-channel live] │
│                                                  │   ● EEG 2          │
│                                                  │   [4-channel live] │
└──────────────────────────────────────────────────┴────────────────────┘
```

### 5.3 Camera Ingest

All four cameras are **USB / UVC devices** — side cameras via USB-C, POV cameras through an FPV receiver's USB output. No RTMP, no MonaServer, no WiFi.

Under the hood, `usb_camera.USBCameraThread` uses OpenCV's `VideoCapture` with per-OS backend selection (DirectShow on Windows, AVFoundation on macOS, V4L2 on Linux). Each slot runs its own `QThread` reading frames in a tight loop, delivering them to a pyqtgraph `ImageItem` for display at up to 60 FPS. Per-round recording writes an H.264 MP4 directly from the capture thread via `cv2.VideoWriter`, so phase transitions finalise cleanly without subprocess ceremony.

Slot layout: **SIDE 01 / SIDE 02** → `Side Cams/` subfolder, **POV 01 / POV 02** → `POV Cams/` subfolder.

Device picker: click **SELECT** on any slot → `CameraPickerDialog` enumerates available devices. Slots already claimed by other sibling slots are greyed out so the same camera cannot be opened twice.

### 5.4 EEG Streams

Two `EEGLiveWidget` panels stack vertically in the right rail — one per Muse-S headband. Each has WAVE / BANDS view modes, a DEVICE picker, and a CONNECT button. The rail header shows `N / 2 CONNECTED` in green when both devices are live.

- **WAVE** — 4-channel (TP9 / AF7 / AF8 / TP10) rolling 10-second trace, rendered via pyqtgraph at ≥60 FPS (previous matplotlib implementation ran at 5-15 FPS).
- **BANDS** — relative δ / θ / α / β / γ power per channel, computed from the last 4 s via Welch PSD.

Connect each headband via **DEVICE** → **SCAN FOR DEVICES** → **CONNECT** before starting a session. If no headbands are connected when you press **START SESSION**, the app will warn and ask for confirmation — cameras will still record, but no EEG data will be captured.

### 5.5 Session Setup Dialog

Clicking **START SESSION** opens a modal dialog with four fields:

| Field                | Range         | Default  | Notes                                       |
|----------------------|---------------|----------|---------------------------------------------|
| Number of rounds     | 1 – 20        | 3        | Total rounds in the session                 |
| Round duration       | 0.1 – 30 min  | 3.0 min  | Per-round recording window                  |
| Break duration       | 0 – 600 sec   | 60 sec   | Between rounds. No break after the last round. |
| Baseline             | 0 – 600 sec   | 60 sec   | Set to 0 to skip the baseline phase         |

A live summary shows the total estimated runtime as you adjust the fields.

### 5.6 Phase Loop Behaviour

Once you click **BEGIN SESSION**, the software runs autonomously:

1. **BASELINE** (default 60 s, skippable) — cyan dial. Sync beep at start. EEG + cameras record into `baseline/`.
2. **ROUND N** — yellow dial. Sync beep at start. EEG + cameras record into `Round N/`. Previous phase's recordings are finalised (MP4 moov atom written, EEG CSV flushed) before the new phase starts.
3. **BREAK N** — amber dial. No recording, no beep. Rest window only.
4. Loop 2–3 until the last round, then enter **DONE** (green dial, status banner turns green, tab resets for the next session after 6 s).

**STOP SESSION** is available at any time. It finalises the currently active phase and writes the manifest; remaining rounds/breaks are skipped.

### 5.7 Output Directory Layout

Every session creates a timestamped folder under `Captures/` (or `$BRAIN_BATTLE_CAPTURE_DIR` if set):

```
Captures/
└── Brain Battle 2026-04-17 14-30-05/
    ├── baseline/
    │   ├── Side Cams/      BASELINE_SIDE_01.mp4, BASELINE_SIDE_02.mp4
    │   ├── POV Cams/       BASELINE_POV_01.mp4,  BASELINE_POV_02.mp4
    │   └── Muse data/      BASELINE_Muse_S_7538.csv, BASELINE_Muse_S_7564.csv
    ├── Round 1/
    │   ├── Side Cams/      ROUND_1_SIDE_01.mp4, ROUND_1_SIDE_02.mp4
    │   ├── POV Cams/       ROUND_1_POV_01.mp4,  ROUND_1_POV_02.mp4
    │   └── Muse data/      ROUND_1_Muse_S_7538.csv, ROUND_1_Muse_S_7564.csv
    ├── Round 2/
    │   └── …
    └── session_manifest.json
```

**`session_manifest.json`** records the full session config plus each phase's start/end wall-clock and monotonic timestamps. This is what the post-session sync engine uses to split any SD-card camera footage (from cameras not ingesting through the live pipeline) back into per-round segments.

### 5.8 Sync Beep

1-second 1 kHz sine burst with 5 ms linear fade-in/out at 48 kHz, played through `QSoundEffect` for low-latency fire-and-forget. The WAV is generated on first run and cached in the system temp folder. Because the beep is recorded by every camera audio track simultaneously, it gives the post-session audio-correlation sync engine a sharp per-round alignment spike to lock onto.

### 5.9 Recording Robustness

- **USB camera recording happens inside each capture thread.** The thread picks up recording start/stop requests between frames, so phase transitions never miss frames at the boundary. `cv2.VideoWriter.release()` writes a clean MP4 moov atom on stop.
- **EEG recording is per-widget.** Only headbands with an active Muse-S connection will produce CSVs. Unconnected slots are skipped cleanly.
- **Graceful degradation.** Missing cameras (unconnected USB slots, ffmpeg not on PATH) do not abort the session — whichever cameras are live keep recording; the rest produce no file for that phase.
- **Disk-full and permission errors surface up-front** — the full directory tree is created eagerly before the first phase starts, so any failure aborts the session cleanly rather than halfway through.

---

## 6. Tab 02 — Sync + Review

Upload all recorded files here to align them to a common timeline and review footage with synchronised EEG overlay.

> **Auto-populate from a live capture.** When a session finishes in Tab 01, this tab automatically loads the files from its **Round 1** folder (two DSLR + two POV MP4s + two Muse CSVs). Other rounds can be loaded manually via the UPLOAD buttons — or re-run AUTO-SYNC after swapping files. The status banner shows which session/round is currently loaded.

### 6.1 Uploading Sources

Six source rows in the left panel — four for video, two for EEG CSV:

| Source         | Accepted Formats        |
|----------------|-------------------------|
| DSLR 01 / 02   | `.mp4`, `.mov`, `.avi`  |
| POV 01/02 | `.mp4`, `.mov`, `.avi`  |
| MUSE-S 01 / 02 | `.csv` (Muse-S output)  |

At least **two video files** must be uploaded before AUTO-SYNC can run. EEG files are optional.

### 6.2 AUTO-SYNC

Click **AUTO-SYNC** to compute synchronisation offsets. The sync engine runs in a background thread and reports progress via the status bar.

#### Video-to-Video Sync

1. Extracts mono audio from each video via `ffmpeg-python`
2. Designates the longest video as the reference
3. Computes **FFT cross-correlation** of each video's audio against the reference using `scipy.signal`
4. The cross-correlation peak position gives the time offset in seconds
5. Normalised peak height is used as the confidence metric

#### EEG-to-Video Sync

Muse-S CSVs contain Unix timestamps. Camera files contain `creation_time` metadata (written at record-start). The EEG offset is:

```
eeg_global_offset = (video_creation_time_unix - eeg_first_timestamp_unix) - video_xcorr_offset
```

#### Confidence Badges

| Badge        | Meaning                                          |
|--------------|--------------------------------------------------|
| `✓` (green)  | High confidence — sharp peak, reliable offset    |
| `~` (yellow) | Medium confidence — usable but review recommended |
| `?` (red)    | Low confidence — poor audio overlap              |

### 6.3 Synchronised Review

After sync completes, video slots are populated and the EEG overlay strips are loaded.

| Control             | Description                                              |
|---------------------|----------------------------------------------------------|
| `▶  PLAY / ⏸  PAUSE`| Play/pause all four video slots in lockstep              |
| Timeline slider     | Scrub all videos and EEG playheads simultaneously        |
| Timecode display    | Current global timeline position in `HH:MM:SS.mmm`      |
| Duration display    | Total session duration from the longest source           |

The `SyncPlaybackController` polls the reference player every 100ms and re-seeks drifted players if drift exceeds **150ms**.

### 6.4 Exporting Synced Data

Click **EXPORT SYNCED DATA** to package the session as a named ZIP. The ZIP contains:

```
export_name.zip
├── merged_video.mp4    # Quad-grid 2×2 composite, H.264 encoded
├── labels.csv          # Event labels placed in Tab 03
├── sync_offsets.csv    # Per-source offsets, methods, confidence scores
├── eeg/
│   ├── eeg_01.csv      # Original Muse-S recording — Boxer A
│   └── eeg_02.csv      # Original Muse-S recording — Boxer B
├── videos/
│   ├── video_01.mp4    # Original camera files
│   └── ...
└── README.txt          # Session metadata
```

> **Note:** The quad-grid video merge runs via `ffmpeg` in a background thread with a progress dialog. For long sessions this can take several minutes. If `ffmpeg` is not found the video merge is skipped; all other files are still zipped.

### 6.5 Proceed to Labelling

Click **PROCEED TO LABELLING** to carry all loaded videos, EEG data, and sync offsets into Tab 03. No re-uploading is needed.

---

## 7. Tab 03 — Labelling

Frame-by-frame review with event label placement. All four camera angles and both EEG streams play back simultaneously.

### 7.1 Label Palette

The left panel shows the label palette. Click any label to select it. Predefined labels:

| Label              | Colour               | Description                 |
|--------------------|----------------------|-----------------------------|
| `ROUND_START`      | `#22c55e` (green)    | Start of a boxing round     |
| `ROUND_END`        | `#ef4444` (red)      | End of a boxing round       |
| `PUNCH_JAB`        | `#3b82f6` (blue)     | Jab punch thrown            |
| `PUNCH_CROSS`      | `#60a5fa` (lt. blue) | Cross punch thrown          |
| `PUNCH_HOOK`       | `#818cf8` (indigo)   | Hook punch thrown           |
| `PUNCH_UPPERCUT`   | `#a78bfa` (purple)   | Uppercut thrown             |
| `CLINCH`           | `#f59e0b` (amber)    | Fighters in clinch          |
| `KNOCKDOWN`        | `#ef4444` (red)      | Boxer knocked down          |
| `CORNER_CUT`       | `#6b7280` (grey)     | Corner cut between rounds   |
| `REF_STOPPAGE`     | `#f97316` (orange)   | Referee stops the fight     |
| `DOMINANT_BOXER_A` | `#06b6d4` (teal)     | Boxer A is dominant         |
| `DOMINANT_BOXER_B` | `#ec4899` (pink)     | Boxer B is dominant         |

### 7.2 Adding and Removing Labels

- **Add:** Type a name in the input field at the bottom of the palette and press **Enter** or **+**. Names are auto-uppercased, spaces become underscores. A colour is auto-assigned from a 12-colour rotation pool (never repeats a colour already in use).
- **Remove:** Click **×** next to any chip. The change is saved immediately to `labels_data.json`.

> **Warning:** Removing a label from the palette does not remove already-placed instances of that label in the current session.

### 7.3 Placing Labels

1. Select a label from the palette
2. Navigate to the event using the timeline slider or playback controls
3. Click **PLACE LABEL AT PLAYHEAD**

Each entry in the **PLACED LABELS** panel shows a colour dot, timestamp (`HH:MM:SS.mmm`), and label name. Click **×** to remove. The counter in the header updates automatically.

### 7.4 Playback Controls

| Control             | Description                                    |
|---------------------|------------------------------------------------|
| `⏮`                 | Rewind to beginning (seeks to `t=0`)           |
| `▶  PLAY / ⏸  PAUSE`| Play/pause all videos and EEG streams          |
| `⏭`                 | Skip forward                                   |
| Timeline slider     | Seek all sources to any point                  |

### 7.5 Exporting Labels

Click **EXPORT LABELS CSV** in the toolbar. Output columns:

| Column          | Description                                             |
|-----------------|---------------------------------------------------------|
| `timestamp_sec` | Event time as a float (seconds from session start)      |
| `timestamp_str` | Human-readable `HH:MM:SS.mmm` format                   |
| `label`         | Label name string                                       |
| `color`         | Hex colour code of the label                            |

---

## 8. Label Configuration

Labels are managed through two files:

### 8.1 `labels_config.py` — Static Defaults

Defines the default label set loaded when `labels_data.json` does not exist. **This file is never modified by the running application.** Edit it directly to change the factory defaults.

To add a permanent default label, append to `_DEFAULT_LABELS`:

```python
('MY_LABEL', '#hexcolour'),
```

To reset to factory defaults, delete `labels_data.json`.

### 8.2 `labels_data.json` — Live State

Auto-generated JSON array of `[name, colour]` pairs representing the current palette:

```json
[
  ["ROUND_START", "#22c55e"],
  ["PUNCH_JAB", "#3b82f6"],
  ...
]
```

Written by `save()` in `labels_config.py` every time a label is added or removed.

### 8.3 Auto-Colour Pool

New labels are assigned the next unused colour from this rotation:

```
#e8ff00  #06b6d4  #34d399  #f97316
#a78bfa  #f43f5e  #38bdf8  #fb923c
#4ade80  #c084fc  #fbbf24  #67e8f9
```

---

## 9. Sync Engine — Technical Reference

### 9.1 Algorithm

Implemented in `sync_engine.py`, runs inside a `SyncWorker(QThread)`.

**Step 1 — Audio Extraction**
`ffmpeg-python` extracts mono audio from each video as a `float32` PCM array resampled to 44,100 Hz. `creation_time` metadata is extracted for EEG alignment.

**Step 2 — Reference Selection**
The longest video is the reference. All other offsets are computed relative to it.

**Step 3 — Cross-Correlation**
```python
# Simplified
correlation = scipy.signal.fftconvolve(ref_audio, other_audio[::-1])
offset_samples = correlation.argmax() - (len(other_audio) - 1)
offset_sec = offset_samples / sample_rate
confidence = correlation.max() / correlation.std()
```

**Step 4 — EEG Alignment**
```
eeg_global_offset = (video_creation_time_unix - eeg_first_timestamp_unix)
                    - video_xcorr_offset
```

### 9.2 Global Timeline Conversion

```python
# Source-local  →  global timeline
global_t = source_local_t - source_offset

# Global timeline  →  video seek position
local_seek_time = global_t + video_offset
```

### 9.3 SyncResult Object

```python
@dataclass
class SyncResult:
    video_offsets:    list[SourceOffset]
    eeg_offsets:      list[SourceOffset]
    global_duration:  float              # seconds

@dataclass
class SourceOffset:
    source_id:    str
    offset_sec:   float
    confidence:   float
    method:       str    # 'xcorr' or 'metadata'
    is_reference: bool
```

---

## 10. Module Reference

### `session_controller.py`

| Class / Function         | Description                                                           |
|--------------------------|-----------------------------------------------------------------------|
| `Phase`                  | Enum — IDLE / BASELINE / ROUND / BREAK / DONE                         |
| `SessionConfig`          | Dataclass — `num_rounds`, `round_sec`, `break_sec`, `baseline_sec`    |
| `PhaseInfo`              | Dataclass — one entry in the timeline (phase, label, duration, round_num) |
| `SessionController`      | QObject state machine. Emits `phase_changed`, `tick`, `beep_requested`, `session_started`, `session_finished`. Drives a 1 Hz QTimer. |

### `session_setup_dialog.py`

| Class                   | Description                                                            |
|-------------------------|------------------------------------------------------------------------|
| `SessionSetupDialog`    | Modal QDialog — rounds / round duration / break / baseline inputs. Returns a `SessionConfig` on accept. Shows live estimated total runtime. |

### `session_storage.py`

| Class / Function        | Description                                                            |
|-------------------------|------------------------------------------------------------------------|
| `SessionLayout`         | Owns the filesystem layout for one session. Path accessors: `phase_dir()`, `side_cams_dir()`, `pov_cams_dir()`, `muse_dir()`, `manifest_path()`. |
| `PhaseRecord`           | Dataclass — one manifest entry per recorded phase.                    |
| `create_session(...)`   | Eagerly creates `Brain Battle <timestamp>/` tree with all subfolders and an initial `session_manifest.json`. Fails loud on permission/disk errors. |

### `usb_camera.py`

| Class / Function        | Description                                                            |
|-------------------------|------------------------------------------------------------------------|
| `CameraDevice`          | Dataclass — device index, display name, OpenCV backend constant.      |
| `list_cameras(max_probe)` | Enumerates attached UVC devices by probing `cv2.VideoCapture` across backends. |
| `USBCameraThread`       | QThread. Reads frames via OpenCV in a tight loop; emits `frame_ready`, `state_changed`, `stats` signals. Accepts non-blocking `start_recording(path)` / `stop_recording()` requests picked up between frames. Per-OS backend: DirectShow (Windows), AVFoundation (macOS), V4L2 (Linux). |

### `usb_camera_slot.py`

| Class                   | Description                                                            |
|-------------------------|------------------------------------------------------------------------|
| `CameraPickerDialog`    | Modal device picker. Greys out devices already claimed by sibling slots. |
| `USBCameraSlot`         | Widget hosting one `USBCameraThread` + live pyqtgraph `ImageItem` preview. Phase-aware `start_recording(path)` / `stop_recording()` API, per-slot status dot, FPS indicator, REC pill. |

### `usb_camera_panel.py`

| Class                   | Description                                                            |
|-------------------------|------------------------------------------------------------------------|
| `USBCameraPanel`        | 2×2 grid hosting four `USBCameraSlot`s. Routes SIDE 01/02 → `Side Cams/`, POV 01/02 → `POV Cams/`. Start/stop recording across all live slots at phase boundaries. |

### `circular_timer.py`

| Class                   | Description                                                            |
|-------------------------|------------------------------------------------------------------------|
| `CircularTimer`         | QPainter-based Apple-style countdown dial. Color-coded per phase (cyan BASELINE · Sangria ROUND · amber BREAK · green DONE). `set_phase()` + `set_time()` API. |

### `sync_beep.py`

| Class / Function        | Description                                                            |
|-------------------------|------------------------------------------------------------------------|
| `SyncBeep`              | QSoundEffect-backed beep player. 1 s 1 kHz sine with 5 ms fade in/out. |
| `ensure_beep_wav()`     | Generates the WAV lazily in the system temp dir if missing.            |

### `live_session_tab.py`

| Class                   | Description                                                            |
|-------------------------|------------------------------------------------------------------------|
| `LiveSessionTab`        | Tab 01 widget. OBS-style layout: cameras centre-stage, session rail on right. Drives `SessionController` + `USBCameraPanel` at every phase boundary. Emits `session_saved(path)` on completion. Styled via `design_tokens`. |

### `eeg_plot_pg.py`

| Class                   | Description                                                            |
|-------------------------|------------------------------------------------------------------------|
| `WaveformPlot`          | 4-channel scrolling live EEG trace using pyqtgraph. Replaces matplotlib for the performance-critical live path (≥60 FPS vs 5-15 FPS). |
| `BandPowerPlot`         | 4-panel δ/θ/α/β/γ bar chart. Same performance advantage.              |

### `eeg_module.py`

| Class / Function      | Description                                                                 |
|-----------------------|-----------------------------------------------------------------------------|
| `EEGLiveWidget`       | Live EEG display. Handles scanning, connection, streaming, and recording.   |
| `EEGReviewWidget`     | Offline EEG viewer. Loads a DataFrame; responds to `set_playhead()`.        |
| `_LSLReaderThread`    | QThread reading from `pylsl.StreamInlet`. Detects disconnect after 5s silence.|
| `_MuseScanThread`     | QThread running `muselsl list` as subprocess to find nearby devices.        |
| `DevicePickerDialog`  | Modal device selector. Already-claimed devices are greyed out.              |
| `_RecordStopper`      | QThread sending `SIGINT` to `muselsl record` to flush CSV before exit.      |

### `design_tokens.py`

| Symbol                 | Description                                                                 |
|------------------------|-----------------------------------------------------------------------------|
| Palette constants      | `SANGRIA`, `PRUSSIAN`, `OFF_WHITE`, `ERROR`, `SUCCESS`, etc. — the Constructor University brand palette and the tuned dark background scale. |
| `PHASE_COLORS`         | Dict mapping phase id → colour. Semantic phase distinction (cyan BASELINE · Sangria ROUND · amber BREAK · green DONE). |
| `FONT_MONO`, `FONT_SANS` | Font stacks. Monospace for data readouts, sans-serif (Inter / SF Pro) for UI chrome. |
| `qss_primary_button()`, `qss_secondary_button()`, `qss_danger_button()`, `qss_ghost_button()`, `qss_chip()`, `qss_input()`, `qss_panel()`, etc. | Stylesheet factories — the single source of truth for how every widget is styled. |

### `vlc_module.py`

| Class / Function            | Description                                                          |
|-----------------------------|----------------------------------------------------------------------|
| `VLCVideoSlot`              | VLC-backed video panel. Handles load/play/pause/seek with offsets.   |
| `SyncPlaybackController`    | Owns ≤4 slots. 100ms timer re-syncs drifted players (tolerance 150ms).|
| `_attach_player_to_widget`  | Platform-aware surface binding: `set_nsobject` / `set_hwnd` / `set_xwindow`.|

### `export_module.py`

| Class                       | Description                                                             |
|-----------------------------|-------------------------------------------------------------------------|
| `VideoExportWorker`         | QThread. `ffmpeg filter_complex` 2×2 grid merge. Parses `time=` for progress. |
| `LabelsExporter`            | Synchronous CSV writer (`timestamp_sec`, `timestamp_str`, `label`, `color`). |
| `FullSessionExportWorker`   | QThread. Orchestrates video + labels + offsets + EEG + originals → ZIP. |
| `ExportProgressDialog`      | Modal progress dialog. Live status + progress bar. Closeable on finish.  |

**ffmpeg export command structure:**
```bash
ffmpeg -ss <offset> -t <duration> -i video1 \
       -ss <offset> -t <duration> -i video2 \
       -ss <offset> -t <duration> -i video3 \
       -ss <offset> -t <duration> -i video4 \
       -filter_complex "
         [0:v]scale=960:540,pad=960:540,setsar=1[v0];
         [1:v]scale=960:540,pad=960:540,setsar=1[v1];
         [2:v]scale=960:540,pad=960:540,setsar=1[v2];
         [3:v]scale=960:540,pad=960:540,setsar=1[v3];
         [v0][v1][v2][v3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[out]
       " \
       -map "[out]" -c:v libx264 -preset fast -crf 23 output.mp4
```

---

## 11. Troubleshooting

| Issue                          | Resolution                                                                 | Notes                          |
|--------------------------------|----------------------------------------------------------------------------|--------------------------------|
| EEG device not found           | Run `muselsl list` in terminal. Check Bluetooth is on and headband is powered. | Scan timeout is 20 seconds     |
| EEG recording not saved        | Ensure `ffmpeg` is installed. Check `Captures/` folder permissions.        | App creates folders automatically |
| Stream disconnects mid-session | App saves data to that point. Click **RECONNECT** in the EEG slot.         | Red banner appears              |
| USB camera not listed          | Click **RESCAN ALL** in the USB panel header, or **SELECT** → **RESCAN** per slot. | Probes indices 0-7             |
| USB camera stuck "OPENING…"    | Another app (QuickTime, Zoom, etc.) is holding the device. Close it and reconnect. | MSMF on Windows is particularly strict |
| USB camera low FPS             | USB bandwidth shared — plug each camera into a different USB controller, not a hub. | USB 3.0 ports strongly recommended |
| Bottom-edge green line in video slot | Fixed in Phase 2.2. `VideoSurface` now initialises vmem buffers to opaque black and crops the last row defensively. | See `VideoSurface.paintEvent` |
| AUTO-SYNC fails                | Ensure videos have audio tracks. Check `ffmpeg` is installed and on PATH.  |                                |
| Video playback blank           | Ensure VLC app is installed. `python-vlc` requires the VLC bundle.         | `brew install --cask vlc`      |
| `labels_data.json` error       | Delete `labels_data.json` — app recreates from defaults on next launch.    |                                |
| Export ZIP missing video       | `ffmpeg` required for video merge. Other files still exported without it.  | `brew install ffmpeg`          |
| `Monospace` font warning       | Harmless Qt warning. Replace `Monospace` with `Courier New` in stylesheet if it bothers you. | Does not affect functionality  |

---

## 12. Known Limitations

- **VLC dependency:** `python-vlc` wraps VLC's shared libraries from the installed app bundle. VLC must be installed as a macOS `.app`, not just the Python package.
- **Audio-based sync:** The cross-correlation sync assumes all recordings share an audible common event. The automated sync beep (§5.8) handles this automatically for in-session captures; for externally-recorded footage include a clear audio cue within the first 30 seconds.
- **No-audio sources:** If a camera has no audio track, the sync engine assigns a zero offset and marks confidence as low.
- **USB bandwidth:** Four simultaneous 1080p30 feeds can saturate a single USB controller. On Windows / macOS, prefer laptops with more than one USB 3.0 controller (most do — check `System Information → USB` on macOS) or use a Thunderbolt hub.
- **Export speed:** The quad-grid video merge uses `-preset fast` and `-crf 23`. For a 10-minute session expect 2–5 minutes of encoding time on a modern Mac.

---

## 13. Dependencies

| Package           | Version     | Purpose                                            |
|-------------------|-------------|----------------------------------------------------|
| `PyQt6`           | ≥ 6.4       | GUI framework                                      |
| `pyqtgraph`       | ≥ 0.13.7    | Fast live EEG + camera-preview rendering           |
| `opencv-python`   | ≥ 4.8       | USB / UVC camera ingest via `cv2.VideoCapture`     |
| `matplotlib`      | ≥ 3.7       | Offline EEG review plots (Tabs 02 / 03)            |
| `pylsl`           | ≥ 1.16      | Lab Streaming Layer — receives EEG data            |
| `muselsl`         | ≥ 2.2       | Muse-S BLE streaming and recording CLI             |
| `scipy`           | ≥ 1.10      | FFT cross-correlation for sync engine              |
| `pandas`          | ≥ 2.0       | EEG CSV loading and manipulation                   |
| `ffmpeg-python`   | ≥ 0.2       | Python bindings for ffmpeg (audio extraction)      |
| `numpy`           | ≥ 1.24      | Audio array processing                             |
| `python-vlc`      | ≥ 3.0.18    | Python bindings for VLC (video playback)           |
| `ffmpeg` (binary) | ≥ 5.0       | Required by ffmpeg-python and muselsl              |
| VLC (app)         | ≥ 3.0       | Required by python-vlc for video rendering         |

Install all Python packages:
```bash
pip install PyQt6 pyqtgraph opencv-python numpy scipy pandas matplotlib pylsl muselsl ffmpeg-python python-vlc
```

---

## 14. Project Information

| Field        | Value                                                  |
|--------------|--------------------------------------------------------|
| Project name | Brain Battle — Neuroscience Sports Capture System      |
| Institution  | Constructor University Bremen                          |
| Type         | Community Impact Project (CIP)                         |
| Phase        | Software Phase 2 — continuous-recording capture + review |
| Version      | v2.1.0 (authoritative: `version.py` `__version__`)     |
| GitHub       | https://github.com/Brain-Battle                        |
| Hardware     | 2× Muse-S (MuseS-7538, MuseS-7564), 2× DSLR (USB-C UVC), 2× POV (FPV receiver → USB) |

---

*Brain Battle — Constructor University Bremen*