# Module reference

**What this page covers:** one entry for every Python module in the repository
— what it does, its key classes/functions, who calls it and what it calls, which
thread it runs on, and the gotchas. Start with the grouped overview, then jump
to the module you need. The test-suite modules are summarised in one table at
the end.

All application code lives in the **`brain_battle` package**; the overview
groups below map to its subpackages. This page is kept honest by
`tests/test_developer_docs.py`, which fails if a first-party module has no
entry here, or if an entry names a file that no longer exists. When you add,
remove, or rename a module, update this page in the same commit (see
[AGENTS.md's "Documentation is part of every change"](../contributing/docs-guide.md#documentation-is-part-of-every-change-owner-rule)).

## Grouped overview

| Group (subpackage) | Modules |
|---|---|
| **Package top** | `app` · `version` · `paths` · (root shim `brain_battle_main`) |
| **`ui/`** | `header_shell` · `welcome_screen` · `sessions_screen` · `record_flow` · `record_screens` · `review_label_screen` · `eeg_viewer` · `settings_screen` · `docs_screen` · `design_tokens` · `widgets` · `fonts` |
| **`capture/`** | `clock` · `usb_camera` · `camera_worker` · `camera_source` · `headband_source` · `eeg_module` · `lsl_outlets` · `session_controller` · `timing` · `timing_log` · `buzzer_test` · `simulation` · `contact_quality` · `contact_log` · `keep_awake` · `round_bell` |
| **`playback/`** | `pyav_player` |
| **`data/`** | `signals` · `session_storage` · `session_xdf` · `xdf_writer` · `session_history` · `labels_store` · `label_agreement` · `label_vocab` · `fighters` · `safe_io` · `export_module` · `app_settings` |
| **`support/`** | `log_setup` · `docs_nav` · `platform_utils` · `resource_limits` · `stall_probe` · `sound` |
| **Tools** (`tools/`, not shipped) | `run_tests` · `generate_demo_session` · `demo` · `measure_descriptors` · `soak_test` · `render_diagrams` · `render_screens` · `make_round_bell` |

Threads at a glance: everything runs on the **UI (main) thread** unless its
entry says otherwise. The modules that spawn threads or subprocesses are
`usb_camera`, `eeg_module`, `pyav_player`, `review_label_screen`, `sessions_screen`,
`record_flow` (the `recording.xdf` build) and `export_module`; see the
[threading model](threading-model.md).

---

## App shell & UI screens

### `app.py`
`brain_battle/app.py` — the application shell and `main()` entry point.
`BrainBattleApp(QMainWindow)` builds the header shell, shows the first-run
screen once per computer, constructs each screen
once, wires their signals together (`session_saved`, `help_requested`,
`exit_flow`, `capture_dir_changed`), and owns window sizing/`QSettings` restore.
`open_help(key)` routes every screen's `?` button to the Documentation screen.
`main()` creates the `QApplication`, registers fonts, and runs the event loop —
it is the target of `python3 -m brain_battle`, the `brain-battle` console
script, and the `brain_battle_main.py` shim. Runs on the UI thread. **Calls:**
`header_shell`, every screen module, `app_settings`, `log_setup`, `fonts`.
**Gotcha:** it holds the single instance of each screen — navigation swaps the
visible page, it does not reconstruct screens, so screens must tolerate being
shown/hidden repeatedly (see each `showEvent`/`hideEvent`).

### `brain_battle_main.py`
A thin launcher shim at the repo root, kept for backward compatibility. It
re-exports `BrainBattleApp`, `MIN_WINDOW`, `QMessageBox`, and `main` from
`brain_battle.app` and calls `main()` under `__main__`, so `python3
brain_battle_main.py` and `import brain_battle_main` both still work. New code
should prefer `python3 -m brain_battle`.

### `paths.py`
`brain_battle/paths.py` — the single source of repo-relative paths and the
default recordings folder. `REPO_ROOT` is the checkout root (the directory
containing the `brain_battle` package); `ASSETS_DIR` and `DOCS_DIR` hang off it.
`documents_dir()` returns the OS Documents folder and `default_capture_root()`
returns `~/Documents/Brain Battle Data`
([ADR-0014](../decisions/adr-0014-documents-default-folder.md)). Every module
that needs the bundled assets, the `docs/` tree, or the default recordings
folder resolves it here rather than deriving it from its own `__file__` — so
modules can move between subpackages without breaking asset lookup. **Called
by:** `fonts`, `round_bell`, `docs_nav`, `app_settings`.

### `header_shell.py`
The app's frame (DESIGN "App structure", design F): a white **top header** —
logo, **Sessions**, then **Documentation** and the **Settings** gear on the
right — over a stack of screens. `HeaderShell.add_page(key, label, widget,
nav="left"|"right"|"gear"|"", own_header=False)`; a page with
`own_header=True` (New recording, Review & Label, the first-run screen) hides
the app header. `set_extra(idx, widget)` puts a page's own widget in the header
("Changes save automatically" on Settings). There is no sidebar, no name row
and no folder card any more. **Called by:** `app`.

### `welcome_screen.py`
The first screen on a new computer: "Where are your team's recordings?" — a
shared team folder (Choose folder…, "Found N recordings and your team's
settings") or just this computer. Shown once (when the settings file does not
exist yet); emits `done(path)` after saving the choice. **Called by:** `app`.

### `sessions_screen.py`
The Sessions home (DESIGN "Sessions", design F). `SessionsScreen`: the big blue
**New recording** button, the Recordings and Labelled counts, status pills with
counts (All · Needs labels · Labelled · Exported · Interrupted), the search box
("Search a fighter code or date"), and one card per recording: both fighters'
codes in their colours, date · rounds · length, the next-step progress line,
the status chip, ⟲ (the `HistoryDrawer`, from `history.jsonl` via
`session_history.describe`) and the right action button (`row_action`: Start
labelling / Continue labelling / Add my labels / Open). Right-click a row for
Export for analysis… and Show in Finder / Explorer. The footer names the folder,
its free space and "fighters are stored only as codes". Folder discovery runs
**off the UI thread** in `_ScanWorker` (it also reports free space and whether
the folder exists). `filter_sort_sessions`, `status_counts`, `length_text`,
`labels_text`, `row_action`, `folder_display` are pure helpers. **Gotchas:**
worker signals are connected to bound methods, never lambdas; the interrupted
banner's dismissal is persisted via `session_storage.dismiss_interrupted`.

### `record_flow.py`
The New-recording flow's logic (DESIGN "New recording"): **Get ready →
Recording → Saved**; the views are in `record_screens`. `RecordFlow` owns the
device slots (`camera_source.CameraSlot`, `headband_source.HeadbandSlot`) and the
`session_controller.SessionController` (lead-in, system markers, the bell at both
ends of every round), and emits `session_saved` / `exit_flow`. It creates the
recording (codes + colours in the manifest, the plan and bell remembered in
`app_settings`), records moments and their notes (`mark_moment` opens the
Moment popup; `set_moment_note`), runs the buzzer test (`run_buzzer_test`) and
the Test timing dialog (`open_timing_dialog`), writes `contact_quality.csv` and
`timing_quality.csv` once a second (`_log_contact` / `_log_timing`, stalls and
reconnections into the history), lets Get ready choose the devices
(`connected_cameras` / `assign_camera`, `connected_headbands` /
`assign_headband`: a device in two places swaps; remembered in `app_settings`
`camera_slots` / `headbands`), swaps the headbands, prevents sleep, stops
cleanly early, refuses a missing or read-only folder, owns the
`lsl_outlets.LslPublisher` (off by default), and builds `recording.xdf` on a
background thread once every camera file is closed (`wait_for_xdf`). Every
bookkeeping write goes through `_essential` — a failure shows on Recording and
makes the Saved headline "Recording saved, but with problems"
(`saved_headline`). Public helpers for tools/tests: `set_plan`, `set_fighters`,
`timing_rows`, `timing_line`, `calibration`, `disk_check`. **Gotcha:**
`_on_session_started` takes one origin from `clock.session_origin()` and
threads it into every camera and headband
([ADR-0017](../decisions/adr-0017-one-capture-clock.md)).

### `record_screens.py`
The views of the New-recording flow (design F): `FlowHeader` (Cancel, REC timer,
who is boxing, the three steps, Stop recording), `GetReadyScreen` (the checklist
with Test timing and Start — hard items block Start, the signal and timing items
only advise — the four `CameraTile`s, a `BoxerCard` per fighter: code + New
code, colour swatches, `HeadIcon` sensors, battery, "signal looks poor" and the
live EEG; the plan with `Stepper`s and the bell), `RecordingScreen` (cameras
with timecodes, the small countdown and `PhaseBar`, Mark this moment, a
`LiveBoxerCard` per fighter with grey automatic markers, the timing line),
`SavedScreen`, `MomentDialog` (Save note / Add note later, Enter / Esc, never
pauses recording) and `TimingDialog` (live rate / delay / jitter / dropped,
Run buzzer test). "Signal looks poor" is our heuristic (`contact_quality`); the
band does not report how well it fits. **Called by:** `record_flow`.

### `review_label_screen.py`
Review & Label (DESIGN "Review & Label", design F). `ReviewLabelScreen` opens a
recording and shows its timeline **immediately**, then loads media in the
background (`_MediaLoadWorker`, applying the buzzer-test corrections) and
attaches a `pyav_player.PlayerController`. Layout: a header (‹ Sessions, both
codes in their colours, My labels / Compare when someone else has labelled,
"All labels saved", Export for analysis); the 2×2 `VideoGrid` beside a panel
with **Label** (Who? A · code / B · code / Both; What happened? keys 1–8;
Custom note… (N); the selected label with Delete; the moment editor — the
observer's note, autosaved into the recording, and labels at the moment's
time) and **Activity** (the history); the `BrainActivityCard` (grey system
markers, red moments, labels in their colours); and the transport over a
`LaneTimeline` (Rounds · System · A · B · Both · Notes · Moments). You label
**as yourself** (`labels_store.open_for_labeller`); other labellers' files are
read-only, re-read every few seconds and compared through `label_agreement`
(notes left out) in the compare banner. A failed save keeps labels in memory
and retries. **Gotcha:** everything is on one `t_session_seconds` playhead.

### `eeg_viewer.py`
`brain_battle/ui/eeg_viewer.py` — the EEG display, a conventional EEG viewer
([ADR-0016](../decisions/adr-0016-standard-eeg-viewer.md)). `EEGViewer(QWidget)`
paints one stacked trace per channel labelled TP9 / AF7 / AF8 / TP10 / AUX, a µV
scale bar, a time axis and event markers; `set_timebase`, `set_scale` (a number
or `None` for automatic) and `set_filter` are its controls. In *review* mode the
whole recording is given once (`set_data`) and `set_view(t)` centres the window
on the playhead; in *live* mode a rolling buffer is pushed many times a second
and the window ends at the newest sample. `visible_samples()` returns exactly
what is drawn (drift removed, optional 1–45 Hz band-pass) and never modifies the
data. `BandPowerBars` is the alternative band-power picture. Drawn with
`QPainter` — no plotting library. **Called by:** `review_label_screen`,
`record_flow`. **Thread:** UI. **Gotcha:** this is the ONE module allowed to
show channel codes; `tests/test_no_placeholders.py` still forbids them
elsewhere.

### `settings_screen.py`
Settings (DESIGN "Settings", design F), four cards that save the moment they
change: **This computer** (Your name; the recordings folder + Change —
`apply_new_folder`), **Label list** and **Fighter colours** (both *shared with
the team*: chips with ×, "+ Add" / "+ Add colour"; `label_vocab`,
`fighters`), and **Advanced** (folded: head movement, pulse, cameras at 60 fps,
LSL; which headband each boxer wears with the Bluetooth search and the
same-headband guard; video quality). The round plan and the bell are set on Get
ready instead. **Calls:** `app_settings`, `label_vocab`, `fighters`,
`eeg_module` (BLE search).

### `docs_screen.py`
The in-app Documentation screen. Renders the Markdown under `docs/` at runtime
using Qt's `QTextDocument.setMarkdown` via `QTextBrowser` — **no QtWebEngine**.
`DocsScreen` provides the nav tree, always-visible search with highlighting, and
back/forward. The page fills the window width (text gets side padding only past
`READ_MAX_TEXT_WIDTH`). `_ContentBrowser` scales images to the reading column;
`_apply_typography`/`_style_code_runs` tune the reading experience in code (Qt's
Markdown importer ignores most stylesheets). `MAX_IMG_WIDTH = 720` is the
display width diagrams are sized against. **Calls:** `docs_nav`, `design_tokens`.
**Gotcha:** Qt's `QSvgRenderer` can't render Mermaid SVGs, so pages reference
`.png`, not `.svg` (see [design system](design-system.md) and
[ADR-0012](../decisions/adr-0012-in-app-docs.md)).

### `docs_nav.py`
Qt-free logic for the docs screen and its tests: `nav.yml` loading, GitHub-style
heading `slugify`, the search index (`build_search_index`/`search`/`_snippet`),
and link/image/anchor extraction for the link checker. Kept Qt-free and
headlessly testable, the same way `session_controller` and `label_vocab` are.
**Called by:** `docs_screen`, `tests/test_docs.py`. **Gotcha:** only pages listed
in `nav.yml` are "published"; a `.md` file can exist mid-draft without being
shown anywhere.

---

## Capture

### `camera_source.py`
`CameraSlot(QObject)` — one camera for the record flow. Wraps a real
`usb_camera.USBCameraThread` (or its simulated stand-in) and tracks live/
recording/dropped/encoder state, exposing preview frames and fps via signals,
plus the live timing (`timing_snapshot`), the frame-difference energy for the
buzzer test (`energy_since`), the 60 → 30 fps fallback note (`fps_note`), the
stall watchdog's state (`stalled`, `stall_events`) and the encoder queue use
(`queue_use`). `make_slots(parent)` builds the four slots (two side, two POV). **Called by:**
`record_flow`. **Calls:** `usb_camera`, `simulation`. **Thread:** the wrapped
`USBCameraThread` runs off the UI thread; the slot itself lives on the UI thread.

### `usb_camera.py`
The camera capture + encode layer. `USBCameraThread(QThread)` is one camera:
it starts that camera's **capture process** (`camera_worker`, ADR-0025) — or,
with `isolated=False` / `BRAIN_BATTLE_CAMERA_PROCESS=0`, runs the same loop in
its own thread — passes commands to it and turns its reports into the signals
and numbers the app reads (`monitor`, `energy_since`, `stall_events`,
`queue_peak`, `dropped_frames`, `recording_finalised`, `frame_hook`).
`capture_debug()` reads the soak test's debug switches; `FrameEncoder`
spawns one `ffmpeg` subprocess per camera, feeds it raw frames over stdin
(fragmented H.264, crash-safe), and writes the per-frame sidecar
`<stem>.timestamps.csv` — one row per **encoded** frame, matching the MP4 frame
count exactly ([ADR-0004](../decisions/adr-0004-crash-safe-h264.md)).
`preflight_recording()` blocks recording if ffmpeg is missing; `_resolve_encoder`/
`_validate_encoder` pick a hardware H.264 encoder with a libx264 fallback.
`_negotiate` asks for MJPG, the size, the frame rate and a 1-frame buffer, and
falls back from 60 to 30 fps (signal `fps_note`). Every frame feeds a
`timing.RateMonitor` and a small frame-difference energy log (buzzer test).
**Stall watchdog:** no frame for `STALL_SEC` (2 s) → `stalled` is emitted at
once and the camera is reopened (`_reopen`) while the encoder keeps running, so
the recording continues in the same file (`recovered` with the gap; a read that
hangs for 6 s has its device released). The encoder queue holds ~2 s of
frames at the camera's rate; its peak use per second is `queue_peak`.
**Called by:** `camera_source`, the demo generator. **Thread:** capture thread +
watchdog thread + one encoder-writer thread + the ffmpeg child. **Gotcha:** a
full frame queue drops frames and increments a counter rather than ever
blocking capture; a dead ffmpeg stops only that camera.

### `headband_source.py`
`HeadbandSlot(QObject)` — one boxer's headband for the record flow. Wraps
`eeg_module._MuseDirectReader` (or `simulation.SimMuseReader`) and surfaces
connection, battery, per-sensor contact quality, a rolling buffer for the live
EEG display (`trace()`, all five inputs, capture-clock times), and reconnect/
append. It also keeps the accelerometer (`acc_since`) and gives the live
timing (`timing_snapshot`) and recent EEG (`eeg_since`) to the buzzer test.
When the flow gives it an `LslPublisher` (`publisher`), every chunk is also
pushed to the live LSL streams. **Called by:** `record_flow`. **Calls:**
`eeg_module`, `contact_quality`, `simulation`, `app_settings`. **Thread:** the reader runs on its own QThread;
the slot lives on the UI thread.

### `eeg_module.py`
The Muse-S headband stack. `_MuseDirectReader(QThread)` is a direct Bluetooth
connection (muselsl `Muse` in-process via `bleak`, no helper programs) that
emits EEG chunks (`samples`), the optional movement / pulse sensors (`sensor`)
and battery level (`battery`) as Qt signals, and **streams them to disk** during
recording: each signal has a `_CsvStreamWriter` whose background thread appends
rows (line-buffered, periodic flush+fsync) while the Bluetooth callback only does
a non-blocking `queue.put` ([ADR-0003](../decisions/adr-0003-eeg-streamed-to-disk.md)).
EEG goes to `continuous/Muse data/`, the sensors to `continuous/Muse sensors/`
(`sensor_csv_path`). `_RingBuffer` is a pre-allocated NumPy ring buffer for live
displays; `_MuseScanThread` scans for bands; `boxer_labels()` reads the A/B
mapping from `app_settings`. **Thread:** reader + one CSV-writer thread per
signal. Every EEG packet's arrival is logged by its `packet_timer`
(`timing.PacketTimer`): samples are written with the de-jittered times of the
per-band line fit, not the raw arrival. **Gotcha:** the reader's time source is
`clock.unix_now` (the one capture clock), not `time.time()`; offline callers of
`_on_eeg` pass `arrival=`. How late Bluetooth delivers is measured by the
buzzer test but **not yet verified on hardware** — see
[timing & accuracy](../research-and-data/timing-and-accuracy.md). The movement
and pulse sensors have never been received from a real band.

### `clock.py`
`brain_battle/capture/clock.py` — the one capture clock
([ADR-0017](../decisions/adr-0017-one-capture-clock.md)). Everything is stamped
from `clock.monotonic()` — the high-resolution monotonic clock
(`time.perf_counter()`; `time.monotonic()` is only 15.6 ms-fine on Windows with
Python ≤ 3.12); this module measures once per process the constant
offsets to Unix time and to the LSL clock and converts between them
(`unix_now`, `mono_to_unix`, `mono_to_lsl`, `unix_to_lsl`). `session_origin()`
returns a recording's start on all three scales — exactly what the manifest
stores as `capture_clock`. Qt-free; pylsl is loaded lazily and its absence
degrades to "LSL time = monotonic time". It also points liblsl at
`support/lsl_api.cfg`, which silences its start-up logging and publishes over
IPv4 only ([ADR-0022](../decisions/adr-0022-lsl-outlets-open-once.md)); a
`LSLAPICFG` that is already set is left alone. **Called by:**
`eeg_module`, `lsl_outlets`, `record_flow`, `simulation`,
`tools/generate_demo_session.py`.

### `lsl_outlets.py`
`brain_battle/capture/lsl_outlets.py` — live Lab Streaming Layer outlets
([ADR-0015](../decisions/adr-0015-lsl-and-xdf.md)). `LslPublisher` lazily opens
one outlet per stream (EEG and sensors per boxer, `BrainBattle_Markers`, one
`BrainBattle_Video_<camera>` per camera) and pushes samples with explicit
LSL-clock timestamps converted from the capture clock. **Fail-soft:** if pylsl
is missing or an outlet cannot open, it logs once and every `push_*` becomes a
no-op — recording never depends on it. Qt-free and thread-safe (EEG is pushed
from the UI thread, video frames from each camera's writer thread). **Gotcha:**
outlets are opened once per run and kept in a module-level store —
`close()` only stops publishing and hands them back, because the LSL library
keeps 3 descriptors for every outlet it destroys
([ADR-0022](../decisions/adr-0022-lsl-outlets-open-once.md)). Never drop an
outlet to "clean up". **Called by:** `record_flow`, `headband_source`,
`camera_source`.

### `session_controller.py`
A pure-logic phase state machine (`IDLE→LEAD_IN→BASELINE→ROUND↔BREAK→DONE`),
driven by a 1 Hz QTimer, emitting `phase_changed` / `tick` / `marker` (every
system marker: `RECORDING_START`, `*_START` / `*_END`, `RECORDING_END`) /
`bell_requested` (start and end of every round). `SessionConfig` holds
rounds/durations; `_build_timeline` expands it to `PhaseInfo` steps, starting
with the fixed 15 s lead-in (`LEAD_IN_SEC`); `planned_markers` lists the
markers a full recording writes. Deliberately **Qt-optional /
headless-testable**. **Called by:** `record_flow`, and reused by the demo
generator. **Gotcha:** it owns timing logic only — it writes nothing to disk;
`record_flow` turns its `marker` signal into `phase_markers.csv` rows. Tests
shorten the lead-in through `LEAD_IN_SEC` (`tests/_env.py`).

### `timing.py`
How well the devices keep time, and the buzzer calibration
([ADR-0024](../decisions/adr-0024-timing-check-and-buzzer-calibration.md)).
`RateMonitor` (live rate / jitter / dropped per device), `LinearClockFit` +
`PacketTimer` (per-band de-jittered EEG times from packet arrivals, missing
packets counted; neighbouring sample times never jump — each packet may move
at most `MAX_SLEW` = 1 ms against the last, spacing within `MAX_RATE_ERROR`), `FrameClock` (per camera: each frame's time on the camera's
steady frame clock instead of its read time — frame numbers counted locally,
an asymmetric tracker of the on-time edge; ADR-0025), `detect_eeg_tap` (sub-sample tap onset, accelerometer check),
`detect_camera_tap` (sub-frame onset from frame-difference energy on the
camera's steady frame clock), `calibrate` (per-tap consensus → per-device
corrections → the manifest's `timing_calibration`), `corrections` (what the
XDF / export / Review subtract), `summary_text`. Pure logic + numpy, no Qt.
**Called by:** `eeg_module`, `usb_camera`, `camera_source`, `headband_source`,
`buzzer_test`, `record_flow`, `session_xdf`, `export_module`,
`review_label_screen`.

### `timing_log.py`
`TimingQualityWriter` — `timing_quality.csv`, one row per device per second
(rate, jitter, dropped, the applied correction, events such as a camera stall);
`append` raises on a write error so `record_flow` can show it. `read_rows`
reads it back. **Called by:** `record_flow`, the demo generator.

### `buzzer_test.py`
`BuzzerTest(QObject)` — "Run buzzer test": 5 beeps 1.5 s apart (a generated
1 kHz WAV via `QSoundEffect`), then finds the taps in every live camera and
headband and emits `timing.calibrate`'s result. Under simulation it tells the
simulated devices when the taps happen. **Called by:** `record_flow`
(`run_buzzer_test`). **Thread:** UI thread (timers); the analysis is short.

### `contact_quality.py`
A documented per-sensor EEG contact-quality heuristic (`channel_status` →
flat/clipped/noisy/good; `summarise` → a plain-language line like "Weak contact:
left ear"). `assess_window`/`assess_at` assess a dataframe window. `SENSOR_NAME`
maps channel codes to plain names. **Called by:** `headband_source`,
`record_flow`, `review_label_screen`, `contact_log`.

### `contact_log.py`
`ContactLogWriter` appends a per-sensor contact-quality row into the recording
folder during capture (append-only, line-buffered); a write that fails after
the file opened raises, so `record_flow` shows it on Recording and Saved. `weak_periods` summarises
weak spans; `describe_weak` turns one into a "worth checking" line, filtered to
spans over 30 s. **Called by:** `record_flow` (during recording), the Saved
screen, and Review. **Gotcha:** `_HEADER` is the CSV schema documented in
[file formats](../research-and-data/file-formats.md); changing it needs that doc
updated (honesty test).

### `keep_awake.py`
`KeepAwake` prevents the computer sleeping during a recording (macOS:
`caffeinate -dimsu`; Windows: `SetThreadExecutionState`). Best-effort and
cross-platform. `LatencyActivity` (macOS: `NSProcessInfo.beginActivityWithOptions`
with UserInitiated | LatencyCritical — no App Nap, no timer coalescing) is held
by `KeepAwake` and by each camera process while it records (ADR-0025);
`BRAIN_BATTLE_NO_LATENCY_ACTIVITY=1` switches it off for experiments.
**Called by:** `record_flow` (started at record start, released at stop),
`camera_worker`.

### `round_bell.py`
`RoundBell(QObject)` plays `assets/sounds/round_bell.wav` (a real boxing-bell
recording — see [credits](../project/credits.md)) through `QSoundEffect`;
`bundled_bell_path` resolves the WAV's path regardless of cwd. **Called by:**
`record_flow` (the start and end of every round). Plays through `support/sound.py`, so it is silent under `BRAIN_BATTLE_MUTE`. **Note:** this was
once `sync_beep.py` / `SyncBeep`, named for an audio-xcorr sync engine that has
since been removed (see
[ADR-0002](../decisions/adr-0002-remove-audio-xcorr.md)); it was renamed once
the name no longer described a sync role. A missing/unplayable WAV logs a
warning and leaves `play()` a no-op — never raises.

---

## Playback

### `pyav_player.py`
App-driven multi-camera playback (DESIGN §6,
[ADR-0005](../decisions/adr-0005-pyav-playback.md)). One playhead `t` on the
`t_session_seconds` axis drives every camera. Layers: `PyAVCamera` (one file:
sidecar times + frame-accurate seek/decode), `MultiCamDecoder` (the four
cameras, pure/synchronous, no Qt), `DecodeWorker` (a QThread that **coalesces**
seek requests — only the latest pending `t` is decoded), and `PlayerController`
(transport timed against the real monotonic clock so 1× is honest 1×).
**Called by:** `review_label_screen`. **Thread:** `DecodeWorker` runs off the UI
thread. **Gotcha:** the pure layers (`PyAVCamera`/`MultiCamDecoder`) import no
Qt, which is what `tests/test_player.py` exercises headlessly; the Qt layers are
defined inside a `try: import PyQt6` block.

---

## Data & storage

### `session_storage.py`
The on-disk layout and session model. `SessionLayout` writes the manifest
(`session_manifest.json`), `phase_markers.csv`, moments, stopped-early/capture-
origin/source-start records; `create_session` makes a folder named
`YYYY-MM-DD HH-MM <A> vs <B>` ([ADR-0009](../decisions/adr-0009-folder-naming.md)).
Discovery/classification (`list_sessions`, `session_info`,
`is_interrupted_session`, `scan_interrupted_sessions`) and the disk-space verdict
(`disk_space_verdict`, `estimate_session_bytes`) also live here, as do the
helpers that find and read an existing recording's files: `eeg_files` (which
EEG file is which boxer's — decided by the recording's own manifest),
`sensor_files`, `camera_files`, `read_phase_markers`, `read_csv_tolerant`
(ignores a crash-truncated last row), and `friendly_phase` / `short_phase`
(marker token → UI words). `_update_manifest` is the one atomic
read-change-write used for every later manifest edit. **Called by:**
`record_flow`, `sessions_screen`, `review_label_screen`, `export_module`,
`settings_screen`. **Gotcha:** sessions are discovered **by manifest, not folder
prefix** — do not reintroduce a prefix check.

### `signals.py`
`brain_battle/data/signals.py` — the small, import-free list of facts several
modules must agree on: the EEG channel names and rate, the extra sensors
(`SENSORS`: rate, channels, unit), the folder names, and the stream names used
both by the live LSL outlets and inside `recording.xdf`.

### `session_xdf.py`
`brain_battle/data/session_xdf.py` — builds `recording.xdf` for a recording
folder ([ADR-0015](../decisions/adr-0015-lsl-and-xdf.md)).
`build_session_xdf(root, labels=None)` reads the EEG / sensor CSVs, the camera
sidecars, `phase_markers.csv`, the manifest and every labeller's labels file and writes every
stream with timestamps `t0_lsl + t_session_seconds`, replacing the file
atomically; `ensure_session_xdf` builds it only if missing (used when an
interrupted recording is found). Also a command line:
`python3 -m brain_battle.data.session_xdf "<recording folder>"`. Qt-free.
**Called by:** `record_flow` (after a recording), `export_module`,
`session_storage.flag_new_interrupted`, the demo generator. **Gotcha:** the XDF
is *derived* — never read it back as a source inside the app.

### `xdf_writer.py`
`brain_battle/data/xdf_writer.py` — a minimal XDF 1.0 writer in pure Python +
NumPy. `XdfWriter.add_stream` / `write_samples` / `close` write the file-header,
stream-header, sample, clock-offset and stream-footer chunks for numeric and
string (marker) streams; `channels_xml` builds the standard channel description
block. Verified by reading files back with pyxdf (`tests/test_xdf.py`). **Called
by:** `session_xdf`.

### `labels_store.py`
Labels on disk and in memory. **One file per labeller**:
`<recording>/labels/<labeller>.json` (schema 3), so two people never write to
the same file ([ADR-0019](../decisions/adr-0019-one-labels-file-per-labeller.md);
the label entries themselves are unchanged from
[ADR-0006](../decisions/adr-0006-labels-v2.md)). `LabelsStore` holds one
labeller's labels with undo/redo and, bound to its file by
`open_for_labeller`, saves atomically — first merging anything that appeared in
the file since it was read (`merge_from_disk`: the same person on two
computers). `read_all` / `all_labels` / `labeller_counts` read every labeller
(and a not-yet-migrated `labels.json`) without writing; `migrate_legacy` moves
an old single file into `labels/` (original kept as `labels.json.v2.bak`);
`claim_legacy` is "These are mine"; `labeller_slug` turns a name into a safe
file name; `events_timeline` merges labels with phase markers. Pure logic, no
Qt. **Called by:** `review_label_screen`, `session_storage`, `session_xdf`,
`export_module`, the demo generator, tests. **Gotcha:** `save` creates only the
`labels/` folder, never its parents — if the recording folder is gone it must
fail, not create an empty look-alike.

### `label_agreement.py`
How far two labellers agree. `compare(a, b, …, tolerance)` matches labels that
have the same text and the same boxer and start within `tolerance` seconds (or,
for lasting labels, overlap), closest first, each at most once, and returns an
`Agreement` (matched / only-a / only-b, `percent`, and a plain-language
`sentence`). `pairwise` does it for every pair. Deliberately counts rather than
a chance-corrected statistic. Pure logic, no Qt. **Called by:**
`review_label_screen` (Compare tab), `export_module` (`agreement.csv`, README).

### `label_vocab.py`
The shared team label list, stored as `label_list.json` **in the recordings
folder** (so a shared folder shares one vocabulary), seeded from the DESIGN
default palette. `load`/`save`/`renumber`/`next_color`. Pure logic, no Qt.
**Called by:** `settings_screen` (editor), `review_label_screen` (place labels).
**Gotcha:** the list order **is** the 1–9 key mapping; `renumber` keeps keys
contiguous.

### `session_history.py`
The append-only `history.jsonl` per recording: `log_event(root, event, details)`
(one whole line per write, flush+fsync, via `safe_io.append_line`),
`read_history` (tolerant reader), and the `EVENT_*` constants. Each line records
time, who (`current_user`: the saved "Your name", else the OS login —
`user_source` says which), the computer's name, app version, event, details.
**Called by:** `record_flow`, `review_label_screen`, `sessions_screen`,
`export_module`. **Gotchas:** `EVENT_*` constants are documented in
[file formats](../research-and-data/file-formats.md) (honesty test); it never
creates the recording folder — an event for a folder that is not there is
dropped.

### `fighters.py`
Fighter codes and colours ([ADR-0023](../decisions/adr-0023-fighter-codes-and-colours.md)).
`normalise_code` / `code_ok` / `new_code` / `codes_problem` (exactly 4
characters A–Z / 0–9, the two different); the shared colour list
`fighter_colours.json` (`load_colours`, `save_colours`, `add_colour`,
`remove_colour`; Red / Blue / Black always present); `boxer_entry` (what the
manifest stores per fighter); `info` / `display` / `versus` (what screens
print — the code, or an old recording's stored name). Pure logic + small file
I/O, no Qt. **Called by:** `session_storage`, `session_xdf`, `export_module`,
the screens, `tools/generate_demo_session.py`.

### `safe_io.py`
Writing safely in a folder several computers share
([ADR-0020](../decisions/adr-0020-shared-folder-safety.md)).
`atomic_write_text` / `atomic_write_json` (unique temp name, fsync, swap, with
a short retry for Windows / network "file in use"), `read_json` (tolerant),
`append_line`, `FolderLock` + `update_json` (an advisory lock file for
read-change-write edits; stale locks are taken over; never hangs),
`folder_status` / `folder_problem_text` (is the folder there and writable, and
what to tell the user), `is_network_path` (best effort). Stdlib only. **Called
by:** `session_storage`, `labels_store`, `label_vocab`, `session_history`,
`app_settings`, `session_xdf`, `export_module`, and the screens.

### `export_module.py`
The analysis bundle. `SessionExportWorker(QThread)` builds the export zip
(`recording.xdf` rebuilt with every labeller's labels, `labels.csv` and
`events.csv` with a `labeller` column, `agreement.csv` when two or more people
labelled, `eeg_coregistered.csv`, raw EEG + sensor CSVs + videos with sidecars,
manifest, history, README) straight from the recording folder;
`include_videos=False` leaves the MP4s out (their frame-time CSVs stay). `build_coregistered_eeg`
merges both bands onto one uniform 256 Hz `t_session_seconds` grid (`_01` is
always Boxer A, `_02` Boxer B); `VideoExportWorker(QThread)` is the optional 2×2 merge
(hardware-encoder-validated with a libx264 fallback). `ExportOptionsDialog` runs
it with a progress bar and the two video choices (the 4 camera videos; one combined video).
**Called by:** `review_label_screen`, `app` (Sessions → ⋯ → Export). **Thread:** export
runs on a worker thread. **Gotcha:** the combined video is **optional and off by
default** — most analysis wants the data, not a preview.

---

## Support

### `app_settings.py`
The user-config store: one JSON file in the OS config directory holding the
headband→Boxer A/B mapping + known-headbands list, session defaults, round bell,
video quality (resolution + bitrate), whether to record the headbands' movement
and pulse sensors, whether to publish live LSL streams, the labeller's name
("Your name"), and the two name-hiding switches. `capture_dir_resolved()` resolves the
recordings folder (`BRAIN_BATTLE_CAPTURE_DIR` always wins when set); `gb_per_hour`
derives the GB/hour copy from the bitrate ([ADR-0010](../decisions/adr-0010-app-settings-store.md)).
**Called by:** `settings_screen`, `record_flow`, `sessions_screen`, `eeg_module`,
`usb_camera` path. **Gotcha:** this replaced the hardcoded
`eeg_module.BOXER_LABELS` and `usb_camera`'s fixed resolution/bitrate — read
settings, don't hardcode.

### `design_tokens.py`
The **light-only** design system (DESIGN tokens) + QSS factories (`qss_*`), the
palette accessors, boxer/phase colours, the icon set, and font helpers
(`number_font` gives equal-width digits for timers), and
`pin_light_palette(app)`, which hands Qt a light palette so a computer in dark
mode never turns an unstyled field dark. The app stylesheet makes every text
field white with dark text. There is no dark palette and no theme switching ([ADR-0007](../decisions/adr-0007-light-only-design.md),
guarded by `tests/test_light_theme.py`). **Called by:** every UI module.

### `widgets.py`
`brain_battle/ui/widgets.py` — the shared building blocks every screen uses, so
a button or a status line looks the same everywhere: text helpers (`title`,
`heading`, `body`, `muted`, `caption`), `Card`, `Banner`, the button factories
(`primary_button`, `secondary_button`, `ghost_button`, `danger_button`,
`icon_button`, `help_button`), `Segmented`, `StatusLine` (icon + text; each
state has its own icon shape), `pill`, `boxer_badge` / `boxer_chip`,
`TimerLabel` (hand-painted, equal-width digits), `Dot`, `clear_layout`,
`WrapLabel` (a wrapped label that is never squeezed shorter than its text),
`neutral_badge`, and the two components every screen must use instead of Qt's
native ones: **`AppDialog` / `ask` / `tell`** — the one in-app dialog (white
rounded card on a dimmed cover, buttons right, `warn` / `danger` variants;
frameless, never a native alert) — and **`Dropdown`** — the one app-styled
dropdown. In tests, `widgets.dialog_responder = fn` answers dialogs without
showing them and `answered_dialogs` lists what was asked.
**Called by:** every screen. **Gotcha:** `Card` gives itself a unique object
name and scopes its stylesheet to it, so its background never leaks into child
widgets. `tests/test_dialogs.py` fails on any `QMessageBox` / `QColorDialog` /
bare `QComboBox` in the app.

### `camera_worker.py`
`brain_battle/capture/camera_worker.py` — one camera's capture loop,
`CaptureLoop` (plain Python, no Qt): open + `_negotiate` (MJPG, size, frame
rate, 1-frame buffer, 60 → 30 fps fallback), read into pooled buffers, stamp
with the capture clock, frame-difference energy, the bounded encoder queue
and writer thread (`FrameEncoder`), previews (≤ 15 a second, downscaled), the
stall watchdog and reopen. It reports through one `out(kind, payload)`
function. `main(conn, …)` is the entry point of a camera process (spawned by
`USBCameraThread`): reader + writer + control + sender threads, warnings sent
to the app's log, and the video finalised if the app goes away. **Why a
process:** [ADR-0025](../decisions/adr-0025-each-camera-in-its-own-process.md).
**Thread:** its own process.

### `stall_probe.py`
`brain_battle/support/stall_probe.py` — debug only (`tools/soak_test.py
--diagnose`). `StallProbe` records every time a 5 ms sleeper thread woke more
than 20 ms late, every garbage collection's duration (`gc.callbacks`), and
dumps every thread's stack *during* a stall through
`faulthandler.dump_traceback_later` (a C watchdog that needs no interpreter
lock). Used to find the cause of camera frame loss (ADR-0025).
`wake_lateness()` measures the computer itself — how late it wakes a thread
sleeping to 60 Hz deadlines; `tools/wake_probe.py` prints it, and
`test_timing` / `test_stability` use it to decide whether a computer can
judge millisecond precision at all (`tests/_env.py` `timing_judgeable`).

### `sound.py`
`brain_battle/support/sound.py` — the one place the app makes a sound.
`play(effect, what)` plays a `QSoundEffect` (the round bell, the buzzer-test
beeps) unless `muted()`: with the environment variable `BRAIN_BATTLE_MUTE` set
(anything but empty or `0`) it only logs that the sound would have played.
`tests/_env.py`, `tools/soak_test.py` and CI set it, so tests never make a
sound. **Called by:** `round_bell`, `buzzer_test`. **Gotcha:** never call an
effect's `play()` anywhere else — `tests/test_round_bell.py` fails if you do.

### `platform_utils.py`
`brain_battle/support/platform_utils.py` — the few things that differ between
macOS and Windows, in one place: `format_date` / `format_date_time` /
`format_iso` (no `%-d`, which Windows rejects), `file_manager_name` /
`show_in_file_manager_label` / `reveal_in_file_manager` / `open_folder`
(Finder vs. Explorer), `shortcut` (⌘Z vs. Ctrl+Z), `computer_name`,
`ffmpeg_install_hint`, `raise_thread_priority` (capture threads: macOS
`USER_INTERACTIVE` quality of service, Windows `THREAD_PRIORITY_HIGHEST`). Stdlib only; every function takes an optional
`platform_name` so tests exercise both systems on one machine. See
[Cross-platform & CI](cross-platform-and-ci.md).

### `fonts.py`
`register_fonts()` registers the bundled fonts with Qt — Inter for the
interface and JetBrains Mono for code in the Documentation screen — so the UI
looks the same regardless of installed fonts; best-effort (falls back
silently), loads once. **Called by:** `app.main()` and every screen's
constructor, so a screen built on its own (a test, a screenshot) measures text
with the same fonts as the running app. **Gotcha:** `design_tokens.FONT_UI` /
`FONT_MONO` name only these bundled families; asking Qt for a family the
computer lacks makes it print a "missing font family" warning.

### `log_setup.py`
Central logging: `init_logging()` (console at INFO),
`attach_session_log(root)` (a DEBUG file handler inside each session folder)
and `detach_session_log()` (closes that file once the recording is finished and
its `recording.xdf` is built, so the app does not keep a file open in a folder
it is done with). **Called by:** `brain_battle_main` (startup), `record_flow`
(per session).

### `resource_limits.py`
`brain_battle/support/resource_limits.py` — the operating system's open-file
limit. `raise_open_file_limit()` lifts the soft limit to 4096 (or the hard
limit, if lower) on macOS / Linux and does nothing on Windows; it is called
once from `app.main()` and logs what it did. `count_open_descriptors()` /
`snapshot()` count what the process holds right now — files, sockets, pipes,
threads, child processes. `release_qthread(t)` hands a stopped capture
`QThread` to Qt for deletion (now, or when it finishes): capture threads have
their slot as Qt parent and would otherwise live as long as the app — on
Windows each keeps handles (about 20 per recording, found by the soak).
Otherwise Qt-free, never raises. **Called by:** `app.main()`,
`tools/measure_descriptors.py`, `tests/test_descriptors.py`; `release_qthread`
by `camera_source`, `headband_source` and `sessions_screen`. **Gotcha:** it is
a safety net; never call it on import or from a screen, or
`tests/test_descriptors.py` (which runs under 256 on purpose) stops testing
anything. What a recording needs, measured: about 163 with New recording open,
183 while recording — 143 of those are the 11 LSL outlets at 13 each
([ADR-0022](../decisions/adr-0022-lsl-outlets-open-once.md)).

### `version.py`
The single authoritative `__version__` (`3.0.0-dev` on `develop`). Surfaced to
the window title, the manifest's `version` field, and the human-facing README/
CHANGELOG lines. **Gotcha:** bump here and nowhere else; `tests/test_developer_docs.py`
checks the CHANGELOG's top entry matches it.

### `simulation.py`
**Dev-only, not shipped.** Simulated cameras + headbands through the **real**
capture paths, gated entirely by `BRAIN_BATTLE_SIMULATE`
([ADR-0011](../decisions/adr-0011-simulated-devices.md)). `SimCapture` is a
cv2-shaped synthetic camera; `make_muse_reader` returns a simulated reader that
feeds synthetic EEG, movement, pulse and battery data through the real reader's
callbacks (so the CSVs, the live LSL streams and `recording.xdf` all come out of
simulation too); failure injection covers a missing camera and a headband drop. **Called by:**
`usb_camera`, `camera_source`, `headband_source` (only when the flag is set).
**Gotcha:** with the flag unset there is no trace of it in the UI; Phase 6
excludes the module from the packaged app.

---

## Tools (`tools/` — developer-only, not shipped)

### `tools/run_tests.py`
Runs every `tests/test_*.py` script, several at a time, and prints one PASS /
FAIL line per script (about five and a half minutes instead of eleven).
Timing-sensitive and LSL-publishing scripts share one serial lane; a script
that fails while others are running is re-run alone before it counts as failed.
`python3 tools/run_tests.py xdf player` runs only the matching scripts. See
[testing strategy](testing-strategy.md).

### `tools/generate_demo_session.py`
Generates a realistic v3 demo session (4 camera MP4s + sidecars including one
deliberately drifting camera, 2 EEG CSVs + movement-sensor CSVs,
`phase_markers.csv`, a `demo:true` manifest, and `recording.xdf`) by **reusing the real writers**, so the fixture can't drift from the
real format. `--out` is required and refuses the real recordings folder without
`--force`. `--library` makes 6 recordings covering every Sessions status (two
labellers, custom notes, weak signal, stopped early, interrupted, one without a
buzzer test); fighter codes only, and no login or computer name in any file. See
[AGENTS.md's "Demo session"](../project/planning-documents.md) and
[simulation & demo](simulation-and-demo.md). Covered by `tests/test_demo_session.py`.

### `tools/demo.py`
One command to try the app: makes the demo library in `~/Brain Battle Demo` (or
`--out`) the first time, then starts `python -m brain_battle` on it with
`BRAIN_BATTLE_SIMULATE=1` and its own settings file in the demo folder, so real
recordings and settings are never touched. `--fresh` deletes and regenerates —
only a folder carrying its `.brain-battle-demo` marker; it refuses the real
recordings folder. Mac and Windows. Guards covered by `tests/test_demo_session.py`.

### `tools/render_diagrams.py`
Renders every `docs/diagrams/*.mmd` to `.svg` (GitHub) + `.png` (what Markdown
references) + `.hash` (freshness), via `npx @mermaid-js/mermaid-cli`. `--check`
exits nonzero if anything is stale. See [design system](design-system.md#diagrams).

### `tools/measure_descriptors.py`
Runs several simulated recordings in a row
through the real app and prints the descriptor / thread / child-process counts
at each point (app started, flow open, recording, Saved, XDF built, flow left).
Run it after touching anything that owns a file, socket, pipe, thread or
subprocess; a healthy run prints the same numbers for every recording.

### `tools/soak_test.py`
The long-recording check (Part 4): a 3-round, 12-minute simulated recording at
60 fps with 4 cameras and 2 headbands, then three short recordings, through the
real app. Prints frames delivered / not delivered by the cameras, encoder drops,
EEG samples missing, memory and descriptor / thread / child counts at each
point; exits nonzero if any limit is crossed. `--minutes`, `--switch-ms`,
`--gc-debug`. Not part of the suite (it takes ~20 min); `tests/test_stability.py`
runs a short version.

### `tools/render_screens.py`
Drives the app under `BRAIN_BATTLE_SIMULATE=1` to produce screenshots: a review
dump into `screens/`, and (`--docs`) the curated `docs/images/*.png` pointed at a
cleanly-named "Brain Battle Data" folder with no sim/demo artefacts. Guarded by
several `tests/test_docs.py` checks.

### `tools/make_round_bell.py`
Turns the raw Freesound recording (`assets/sounds/source/`) into
`assets/sounds/round_bell.wav`: mono downmix, trim-to-onset, high-pass +
high-shelf EQ, trim-to-ring-length + fade-out, then peak-normalised (via a
flat gain computed from `loudnorm`'s true-peak meter) to −1 dBTP — full
volume, just short of clipping; not a loudness target. Every setting is a
named constant in the script so it can be re-run after changing one. See
[credits](../project/credits.md). Covered by `tests/test_round_bell.py`.

---

## Test suite (`tests/`)

Plain scripts (`python3 tests/<name>.py`, nonzero exit on failure). See
[testing strategy](testing-strategy.md) for how to run them and the acceptance
gates. The `_*_runner.py` files are helper subprocesses invoked by the kill
tests, and `_env.py` (imported first by every script) points the app at a
throwaway settings file with a labeller name — neither is a standalone test.

| Test file | Guards |
|---|---|
| `test_smoke.py` | Import/construct floor; acceptance gate for deletions/refactors. |
| `test_player.py` | PyAV single-clock player: frame-for-t, pacing, scrub coalescing. |
| `test_review_tab.py` | Review & Label label side: fast open, duration flow, undo/redo, autosave. |
| `test_labels_v2.py` | Label entry schema, v1 migration, undo/redo, timeline. |
| `test_labellers.py` | One file per labeller, automatic migration + "These are mine", merge-on-save, agreement, Compare in Review & Label, moments for every labeller. |
| `test_fighters.py` | Fighter codes (4 × A–Z/0–9, different) and the shared colour list; manifest + folder name carry codes and colours; old recordings with names still open and keep their names in `recording.xdf`. |
| `test_platform.py` | Mac/Windows differences: dates without `%-d`, Finder vs. Explorer, shortcuts, paths, Windows-safe names, encoder fallback, keep-awake. |
| `test_shared_folder.py` | Atomic writes, the folder lock, several processes labelling at once, a folder that disappears, the scan cache. |
| `test_descriptors.py` | Puts itself under a 256 open-file limit, runs several recordings in a row, and checks descriptors / threads / child processes return to the first visit's numbers; LSL outlets opened once; the session log released; a failed marker / history write shown on Recording and Saved; the start-up safety net. |
| `test_label_vocab.py` | Shared `label_list.json`: seed, round-trip, tolerant load. |
| `test_contact_quality.py` | Per-sensor heuristic + plain-language summary. |
| `test_export.py` | Analysis bundle contents + events.csv ordering. |
| `test_flash_sync.py` | Flash within one frame period across tiles (slow — 12-min demo). |
| `test_no_placeholders.py` | No placeholder UI copy; no placeholder screens; channel codes only inside the EEG viewer. |
| `test_capture_clock.py` | Capture-clock reconciliation, co-registration, manifest bridge. |
| `test_xdf.py` | The XDF writer and `recording.xdf` read back with pyxdf; the one capture clock vs. `pylsl.local_clock()`. |
| `test_xdf_csv_parity.py` | Every `recording.xdf` stream also exists as its own CSV in the export, with the same samples and times (minus the buzzer-test correction). |
| `test_stability.py` | A camera that stalls is noticed within 2 s, reopened, and keeps writing the same video (history, `timing_quality.csv`, Recording banner); a headband dropped repeatedly reconnects each time; a contact-log write failure reaches the operator; a short soak (zero encoder drops, no EEG gaps). |
| `test_timing.py` | Live timing monitors, de-jittered headband times, tap detection, and the buzzer calibration recovering known simulated delays within 2 ms. |
| `test_moments.py` | Moment notes in the manifest and `moments.csv`, edited later; "Needs labels" until every moment has a note; the XDF Moments stream. |
| `test_lsl_streams.py` | The live LSL outlets as a real LSL receiver sees them, incl. a simulated headband. |
| `test_eeg_viewer.py` | The EEG viewer: window, honest µV scale, display filter, markers, the Review card's controls. |
| `test_demo_session.py` | The demo generator end to end. |
| `test_sessions.py` | Sessions discovery/status/search/filter/sort, banner, scan. |
| `test_ring_buffer.py` | Ring-buffer ordering. |
| `test_eeg_streaming.py` | Streamed EEG recorder under SIGKILL. |
| `test_video_recording.py` | Crash-safe H.264 recording. |
| `test_crash_recovery.py` | Interrupted detection + disk-space branches. |
| `test_history_log.py` | Append-only history log + hooks. |
| `test_light_theme.py` | Light-only guard (no dark tokens/theme machinery). |
| `test_layout_fit.py` | Every page fits (nothing clipped) at the three sizes. |
| `test_record_flow.py` | The guided flow end to end with simulated devices, incl. the live LSL streams and the resulting `recording.xdf`. |
| `test_app_settings.py` | The settings store + `BOXER_LABELS` removal. |
| `test_settings_screen.py` | The real Settings screen (computed bitrate, editor, end-to-end). |
| `test_contact_log.py` | Contact-log weak-period spans + the >30 s filter. |
| `test_docs.py` | The Documentation tab + docs content (D1 honesty checks). |
| `test_research_docs.py` | Research & data format reference matches code (D2). |
| `test_developer_docs.py` | Developer guide + ADR + Project honesty checks (D3 — see [testing strategy](testing-strategy.md)). |
