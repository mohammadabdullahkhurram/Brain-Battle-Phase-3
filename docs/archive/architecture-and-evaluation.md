# Brain Battle — Architecture & Evaluation

> **Note (added on `develop`, post-capture-clock build):** this report
> describes `main` at merge `5c08a9b` — it predates the `develop` branch's
> unified capture-clock work. Items I1-I4 from Part 3.1 below (per-frame
> timestamps, wall-clock alignment, measured startup latency, co-registered
> EEG export) have since been implemented as Steps 1-5 of the capture-clock
> build (see `CLAUDE.md`). Current planning lives in `docs/ROADMAP.md`, not
> here. The body below is left as-written for historical reference.

For planning major changes. Grounded in the current `main` (merge `5c08a9b`).
Research goal it serves: **measuring neural synchrony between two boxers** —
a recording team captures one continuous session (2× Muse-S EEG, POV + side
cameras) with automatic phase markers; a separate labelling team loads it and
adds manual event labels; labels + a questionnaire feed the synchrony analysis;
the pipeline should eventually live on a free server.

**How to read the confidence tags:** *[verified]* = checked against source in
this pass; *[judgment]* = engineering assessment, not a code fact;
*[estimate]* = a number I computed from assumptions, stated as such. This
report is meant to be checked against independent knowledge of the codebase —
the *[verified]* claims cite files so they can be.

---

## Part 1 — What the app is and how it's built

### 1.1 Feature set *[verified]*

A single PyQt6 desktop app, three tabs behind a sidebar shell:

- **Live Monitor** — connect up to 4 UVC cameras + 2 Muse-S bands; run an
  automated baseline→rounds→breaks loop with a countdown dial and round-start
  beeps; record one continuous take per source; write phase markers.
- **Sync + Review** — auto-load the just-finished session (or open a past one),
  AUTO-SYNC the sources, play all four videos in lockstep with both EEG traces
  following the playhead, export a bundle.
- **Labelling** — open a session, scrub the synced playback, place event labels
  at the playhead (keyboard 1–9 / Space), export a labels CSV. Phase markers
  auto-load as read-only chips.

### 1.2 Capture → storage → review → label → export data flow *[verified]*

**Capture** (`live_session_tab.py`, `session_controller.py`,
`usb_camera*.py`, `eeg_module.py`):

- `SessionController` is a pure-logic 1 Hz state machine
  (`IDLE→BASELINE→ROUND↔BREAK→DONE`) that emits `phase_changed` / `tick` /
  `beep_requested` — deliberately Qt-optional so it's headless-testable.
- On start, one recording begins per source and **runs the whole session**:
  each camera `USBCameraThread` opens a `cv2.VideoWriter`; each connected Muse
  starts a CSV recorder. Nothing restarts at phase boundaries.
- Phase transitions call `SessionLayout.append_marker(...)`, writing a row to
  `phase_markers.csv`.

**Storage** (`session_storage.py`): one folder per session —

```
Brain Battle <YYYY-MM-DD HH-MM-SS>/
    continuous/
        Side Cams/*.mp4      # cv2.VideoWriter, mp4v, ~960x540, nominal fps, NO audio
        POV Cams/*.mp4
        Muse data/*.csv      # TP9,AF7,AF8,TP10,AUX,timestamps  (Unix epoch)
    phase_markers.csv        # t_session_seconds, phase_id, label, round_num, unix_time
    session_manifest.json    # app/version, config, per-phase start_unix + start_monotonic
    session_<ts>.log
```

**Review** (`brain_battle_main.py: SyncReviewTab`): `load_session_folder`
populates the six source rows from `continuous/`; AUTO-SYNC runs
`MainEditorSyncWorker`; four VLC players seek to their offsets and play together;
`EEGReviewWidget` (pyqtgraph) follows the playhead.

**Label** (`brain_battle_main.py: LabellingTab`): `_open_session_folder` loads
the same `continuous/` layout plus `phase_markers.csv` as read-only markers;
manual labels are placed at the playhead; `LabelsExporter` writes the CSV.

**Export** (`export_module.py`): `FullSessionExportWorker` builds a ZIP —
quad-grid merged MP4 (hardware encoder validated with libx264 fallback),
original videos, EEG CSVs, `labels.csv`, `sync_offsets.csv`, README.

### 1.3 Threading model *[verified]*

UI on the main thread. Off the main thread: one `USBCameraThread` per camera
(capture + encode in a tight loop), one `_MuseDirectReader` QThread per band
(BLE via `bleak`, muselsl `Muse` in-process), `MainEditorSyncWorker` for sync,
short-lived `QThread`s for EEG CSV loads, and VLC's own decode threads. Cross-
thread hand-off is via Qt queued signals with explicit array copies where a
producer buffer is reused (`_MuseDirectReader._on_eeg` documents exactly this).

### 1.4 Key design decisions and why *[verified for the "what", mostly verified for the "why" from code comments]*

- **Continuous recording + marker timestamps** (not per-phase files). One MP4
  per camera for the whole session; phases are rows in `phase_markers.csv`. Why:
  the v1 per-round-folder model caused the review/sync half to look for folders
  the capture half stopped creating — the migration this branch just closed.
- **Audio-aware alignment split** (`MainEditorSyncWorker.run`,
  `sync_utils/audio_analysis.py`): if ≥2 clips carry an audio stream, align them
  by audio cross-correlation; otherwise assume simultaneous start (all offsets
  0). Why: in-app cameras write **no audio** (see 1.5), so xcorr is impossible
  and unnecessary for them; external SD-card footage does have audio and can be
  measured.
- **VLC vmem→QPainter rendering** on macOS (`SyncReviewTab._load_video_vmem`),
  HWND on Windows. Why: native NSViews escaped Qt's compositor and painted over
  the toolbar; vmem decodes into a ctypes buffer drawn by QPainter, fully
  clipped by Qt.
- **Direct-BLE Muse stack** (`_MuseDirectReader`, `backend="bleak"`), not a
  muselsl subprocess/LSL bridge. Why (per CHANGELOG 2.14): killed the subprocess
  stack for lower latency and simpler lifecycle; the class keeps the old LSL
  reader's Qt signal shape so the widget didn't change.
- **Session-relative monotonic marker clock**: `t_session = time.monotonic() -
  session_t0` (`live_session_tab.py:428,469`). Why: monotonic is immune to
  wall-clock adjustments during a session. *Consequence analyzed in Part 2.4.*

### 1.5 Module map *[verified against the file list]*

| Module | Role |
|---|---|
| `brain_battle_main.py` | Entry point; `SyncReviewTab`, `LabellingTab`, `MainEditorSyncWorker`, app shell |
| `sidebar_shell.py` | Sidebar navigation hosting the three tabs |
| `design_tokens.py` | Dark/light theme + QSS factories |
| `version.py` | Single authoritative `__version__` |
| `log_setup.py` | Central logging: console + per-session file handler |
| `live_session_tab.py` | Live Monitor — capture orchestration, emits `session_saved` |
| `session_controller.py` | Pure-logic phase state machine, 1 Hz timer |
| `session_storage.py` | On-disk layout, manifest, `phase_markers.csv` writer |
| `session_setup_dialog.py` | Rounds/durations config dialog |
| `circular_timer.py` | Countdown dial |
| `sync_beep.py` | Round-start beep |
| `usb_camera.py` | `USBCameraThread` — cv2 capture + `cv2.VideoWriter` (video-only) |
| `usb_camera_slot.py` | One camera slot widget (device pick, preview, record) |
| `usb_camera_panel.py` | 2×2 slot grid; start/stop fan-out |
| `eeg_module.py` | Muse BLE scan/connect/stream, ring buffer, CSV recorder, live + review EEG widgets (pyqtgraph) |
| `eeg_plot_pg.py` | pyqtgraph waveform/band-power widgets shared live + review |
| `sync_utils/audio_analysis.py` | Audio extraction, xcorr delay + confidence, audio-stream probe |
| `sync_utils/video_sync.py` | ffmpeg preview/trim helpers |
| `sync_utils/eeg_video_sync.py` | EEG-vs-video window comparison via `creation_time` |
| `sync_engine.py` | `SourceOffset`/`SyncResult` (live) + a dormant legacy sync path |
| `vlc_module.py` | `VLCVideoSlot`, `SyncPlaybackController` — labelling playback |
| `export_module.py` | Quad-grid ffmpeg merge, labels CSV, session ZIP |
| `labels_config.py` | Label palette persistence |
| `sync_utils/infobox.py` | Message-box helper |

### 1.6 The clock-domain fact that drives Part 2 *[verified — this is the crux]*

Three independent time bases exist in a session, and they are only loosely
coupled:

1. **EEG CSV `timestamps`** — muselsl wall-clock (`time.time()`-derived,
   regression-corrected per band; `eeg_module.py` direct reader). Unix epoch
   floats. The two bands share the same machine wall clock, so they are mutually
   alignable.
2. **`phase_markers.csv` `t_session_seconds`** — `time.monotonic()` relative to
   `session_t0`. A *different* clock from the EEG epoch. (The manifest stores
   both `start_unix` and `start_monotonic` per phase, so a bridge exists, but
   the review/label code does not currently use it — see 2.4.)
3. **Video** — **no per-frame timestamps exist at all** *[verified: `usb_camera.py`
   writes frames straight to `cv2.VideoWriter` at a nominal fps; there is no
   sidecar of frame arrival times]*. A frame's time is inferred as
   `frame_index / fps`, and `fps` is the camera's *reported* rate, which the
   code itself warns can drift (`usb_camera.py:246`).

At review time the three are aligned by convention, not measurement: EEG is
shifted so its first sample is t=0, video plays from t=0, markers start at
t=0 — three independently-defined "zeros" assumed to coincide. **This is the
single most important thing to understand before touching sync.**

---

## Part 2 — Reality vs stated intent (the honest gap analysis)

First, the two design intents, confronted directly.

### Intent A — "one combined capture to reduce sync issues"

The hardware makes a true single capture impossible: four **independent USB
camera streams** and two **independent BLE Muse streams**, no shared hardware
clock. "Single capture" can only mean one of two things, and neither removes
synchronization:

- **Real-time compositing** — mux the four camera feeds into one video as they
  arrive. This *does* lock the cameras to each other (they share one output
  timeline). But: (i) it puts heavy encode load on one laptop — four 960×540
  MJPEG-decoded feeds composited and re-encoded live, on the same machine
  already running BLE + UI — with real drop-frame risk *[judgment, but the app
  already fights camera bandwidth: `usb_camera.py` forces MJPG and drops preview
  rate to protect capture]*; (ii) it still does **not** put EEG "in" the video —
  BLE EEG is a separate stream and would still need timestamp alignment; (iii)
  it's lossy and irreversible — you can't get the individual angles back.
- **Capture-time timestamping** — keep separate files, but stamp every camera
  frame and every EEG sample from **one monotonic clock on the laptop** at the
  moment of acquisition, then align by recorded timestamps. Software-clock
  precision (tens of ms), no new hardware, free, and it *does* bring EEG and
  video into one comparable timeline because everything is stamped by the same
  clock.

**Verdict:** single-capture does not eliminate synchronization — it *relocates*
it from post-hoc audio xcorr to capture-time timestamping. The timestamping
approach is strictly better for this project (keeps angles, includes EEG,
cheap), and — critically — **its capture-side half does not exist yet** (no
frame timestamps; §1.6). This is the highest-leverage build in the report.

### Intent B — "sync including EEG files without audio, some ms latency OK, tighter is better"

Audio xcorr **cannot** align audio-less files — there is no signal to correlate.
The current app is honest about this: with <2 audio-bearing clips it records
`method = simultaneous_start` and offset 0 (`MainEditorSyncWorker.run`;
`sync_offsets.csv`). But offset 0 is an **assumption, not a measurement** — the
true skew between when each camera and each Muse actually started is unknown and
uncorrected. For in-app captures (which is the normal case, since in-app cameras
have no audio) *every* session is aligned this way.

Monotonic-clock timestamping at acquisition is exactly the right path to
ms-level alignment without audio and without new hardware: if each frame and
each EEG sample carries a capture-time stamp from the same clock, alignment is a
lookup, not a guess, and the residual error is just clock read jitter + BLE
transport latency (tens of ms). **Verdict: intent B and intent A want the same
missing thing** — a shared capture-time clock stamped onto every sample.

### The six requirements, graded

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Record all sources synchronized | **PARTIAL** | Separate files start near-simultaneously; markers give a coarse shared timeline; EEG carries wall-clock timestamps. But there is no measured cross-source alignment and no frame timestamps — sync is *assumed*, not *captured* (§1.6, Intent A/B). |
| 2 | Auto system markers (baseline/round) | **EXISTS** | `SessionController` + `append_marker` write `BASELINE_START`, `ROUND_n_START`, `BREAK_n_START`, `SESSION_END` to `phase_markers.csv` automatically. Solid. |
| 3 | One-click label-team load | **PARTIAL** | In-app, `session_saved → Sync tab.load_session_folder` auto-loads; the Label tab needs a manual OPEN SESSION FOLDER pick. Works, but a separate team on another machine must first *have* the folder (no transfer step) and the load is not verified eyes-on-glass (CLAUDE.md deferred item). |
| 4 | Sync all files incl. audio-less EEG | **PARTIAL / assumption-based** | `simultaneous_start` path exists and doesn't crash, but records offset 0 unmeasured (Intent B). The precision the research needs (ms, for *between-brain* synchrony) is not delivered by an unmeasured 0. |
| 5 | Files named/marked for direct label load | **EXISTS** | Deterministic `continuous/{Side,POV} Cams`, `Muse data`, `phase_markers.csv` layout; both tabs load it by convention. Muse files carry the device name; boxer identity is mapped in `BOXER_LABELS`. |
| 6 | Server upload, free of cost | **NEW BUILD (nothing exists)** | *[verified]* zero network/upload/HTTP/cloud code in the repo. Entirely greenfield — see Part 3's server section for what "server" can and cannot mean. |

**Grounded build list** distilled from the above (detail/effort in Part 3):

1. **Capture-time frame timestamps** (unblocks 1 + 4, both intents) — the core.
2. **Unify the review/label alignment onto the wall clock** using the manifest's
   `start_unix`/`start_monotonic` bridge, so markers, EEG epoch, and video share
   one origin (fixes the "three zeros" gap, 2.4 below).
3. **A transfer/handoff step** for the label team (requirement 3 + 6).
4. **Boxer-synchrony-oriented export** (both EEG streams on one aligned axis) —
   currently exports raw per-band CSVs; the analysis wants them co-registered.

### 2.4 A concrete latent bug in the alignment convention *[verified — judgment on severity]*

Because EEG is aligned by "first EEG sample = 0" and markers by "session_t0 = 0"
(§1.6), and those two instants are **not the same moment** (EEG recording starts
after `session_t0`, and the first BLE chunk arrives later still), the phase
markers shown on the EEG-bearing timeline can be offset from the EEG by the
startup latency — easily hundreds of ms to seconds. The manifest already stores
the `start_unix`↔`start_monotonic` pair needed to correct this; the review/label
code just doesn't use it. This is low-severity for coarse review but **directly
material to between-boxer synchrony timing** and should be fixed alongside any
timestamping work.

---

## Part 3 — Improvement survey (bounded, ranked by proportionality)

Constraints held fixed throughout: **offline-capable recording, single laptop,
USB cameras + BLE Muse, two-team pipeline, payload = neural synchrony, free
hosting.** Every item states the problem it solves, rough cost, and a
proportionality verdict.

### 3.1 Incremental — within the current architecture (recommended first)

| # | Change | Solves | Effort | Verdict |
|---|---|---|---|---|
| I1 | **Per-frame capture timestamps**: write a sidecar `<cam>.timestamps.csv` (frame_index, monotonic_t, unix_t) from `USBCameraThread` at write time | Intents A+B, req 1+4 — turns assumed sync into measured sync | **M** (write path exists; add a stamp + a sidecar per camera) | **Strongly warranted.** Highest leverage, small blast radius, no new deps. The one change that makes the research timing defensible. |
| I2 | **Align review/label on the wall clock** via the manifest bridge (map monotonic markers ↔ EEG Unix epoch) | 2.4 bug; correct marker↔EEG placement | **S–M** | **Warranted.** Small, fixes a real timing error, pairs naturally with I1. |
| I3 | **Stamp `session_t0` to both clocks at the exact recording-start instant** and record each source's first-sample time | Removes the startup-latency skew at its source | **S** | **Warranted**, cheap, complements I2. |
| I4 | **Co-registered EEG export**: emit both bands resampled onto one shared time axis (+ the markers) as an analysis-ready file | Req + the actual synchrony payload | **M** | **Warranted** — the export currently stops short of what the analysis needs. |
| I5 | **Record at H.264 instead of mp4v** (or transcode on export) | File size for transfer/hosting (mp4v is bulky — a multi-camera session is plausibly multi-GB *[estimate]*) | **S–M** (encoder already validated in `export_module`) | **Warranted if** transfer/hosting is on the roadmap; otherwise nice-to-have. |
| I6 | **Auto-handoff polish**: after capture, also stage the Label tab / write a manifest the label team opens in one action | Req 3 smoothness | **S** | Warranted, low cost. |
| I7 | **Eyes-on-glass verification** of playback + review-EEG (the CLAUDE.md deferred check) with the first real session | De-risks everything above | **S** (30-min session) | Do this before building on the sync path. |

### 3.2 Architectural — bigger swaps (do NOT undertake lightly)

| # | Change | Solves | Effort | Verdict |
|---|---|---|---|---|
| A1 | **Adopt LSL end-to-end** (LabRecorder-style): push camera frame events and EEG into LSL, record with LSL's clock sync | A unified, well-trodden timestamping model | **L** | **Judgment: partially warranted but likely overkill.** LSL solves exactly the timestamping problem — but I1–I3 get 90% of the benefit for a fraction of the cost and no new moving parts. Consider LSL only if the lab already standardizes on it. muselsl is already an LSL citizen, so the EEG half is cheap; the camera half (LSL video markers) is the real work. |
| A2 | **Real-time 4-cam compositing to one file** | Intent A's literal reading | **L**, + drop-frame risk on one laptop | **Not warranted.** Loses individual angles, doesn't include EEG, adds the exact CPU pressure the code already fights. Timestamping (I1) achieves the intent's *purpose* without the downsides. |
| A3 | **Hardware genlock / timecode** (shared clock across cameras) | Frame-accurate video sync | **L** + new hardware, breaks "no new hardware / free" | **Not warranted** for this payload — EEG synchrony needs tens-of-ms, not sub-frame, alignment. Over-engineering. |
| A4 | **Rebuild labelling/review as a hosted web app** | Requirement 6's "server," maximally | **XL — a re-architecture** | **Not warranted now.** See the server note below; this is the single largest change on the table and is not what "upload to a server" requires. |

**On "better technology" temptations:** A1/A3 sound more rigorous and are the
kind of thing that reads as an upgrade. Against *this* payload and these
constraints they are mostly newer-sounds-better. The honest ranking is: do
I1–I4 first; they are where the research validity actually lives.

### 3.3 The server question — the distinction that's easy to conflate

**A Python desktop GUI that touches USB cameras and Bluetooth cannot be "hosted
on a server."** The recording app *must* run locally on the recording machine —
the server has no cameras and no BLE radio. So "put the pipeline on a free
server" splits into two very different things:

**(a) Central file storage the label team downloads from — cheap, this is the
real ask.** After capture, the session folder is uploaded to shared storage; the
label team pulls it and runs the (still-local) labelling app. This is a small
upload feature (requirement 6), not a re-architecture. Real free-tier options
*[verified as products; limits are as commonly documented and should be
re-checked before committing]*:

| Option | Free tier (approx., verify) | Fit |
|---|---|---|
| **Google Drive** (API or client) | 15 GB shared across a Google account | Simple, familiar; a multi-GB session (esp. mp4v) eats it fast — pair with I5. |
| **GitHub Releases / Git LFS** | LFS 1 GB storage + 1 GB/mo bandwidth free | Too small for raw video; fine for CSVs/labels only. |
| **Backblaze B2** | 10 GB free storage | Cheap beyond free tier; real S3-style API. |
| **Cloudflare R2** | 10 GB storage, no egress fees | Good if downloads are frequent (egress-free). |
| **Hugging Face datasets / Zenodo** | Large/generous for research data | Good for *archival* of finished labelled sets, not hot working storage. |
| **A university-provided share** (Nextcloud/SharePoint) | Institution-dependent | Often the pragmatic free answer; check Constructor U provisioning. |

Recommendation *[judgment]*: treat (a) as "add an upload step + adopt H.264"
(I5 + a thin uploader). Match the store to realistic session size — which is why
I5 matters — and keep EEG/labels (tiny) separable from video (large) so the
label team can pull the light data even on a small tier.

**(b) Rebuilding the labelling/review UI as a hosted web app — this is A4, one
of the largest possible changes**, and it is *not* what "upload to a server"
means. It would re-implement the pyqtgraph/VLC review surface in the browser,
move video serving + EEG rendering server-side or to WASM, and re-solve the
sync/playback problem in a new stack. Only consider it if remote,
install-free labelling by many people becomes a hard requirement — and even
then, staged after (a) proves the data pipeline.

---

## Summary — what I'm certain of vs judgment

**Certain (verified in code):** the three-clock-domain structure and the absence
of per-frame timestamps (§1.6); in-app cameras write no audio and always take
the `simultaneous_start` offset-0 path (§2, Intent B); no network/server code
exists (req 6); the marker/EEG "two zeros" latent skew (§2.4); the recording
codec is mp4v; the module map and data flow.

**Judgment (verify against the lab's needs before acting):** that tens-of-ms
timestamping suffices for between-brain synchrony (depends on the analysis
method — confirm with whoever owns the synchrony math); the drop-frame risk of
live compositing (plausible from how hard the code already works to protect
capture bandwidth, but not measured); the proportionality rankings in Part 3;
the free-tier limits (products are real, exact numbers drift — re-check).

**Single highest-leverage change:** capture-time frame timestamps (I1) plus the
wall-clock alignment fix (I2/I3). Together they convert the app from *assuming*
synchronization to *recording* it — which is the difference between a demo and
data the synchrony research can stand on. Everything grander (LSL, genlock,
hosted web UI) is either a smaller-benefit variation of this or a separate
project.
