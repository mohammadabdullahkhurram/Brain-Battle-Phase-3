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
| **`ui/`** | `sidebar_shell` · `sessions_screen` · `record_flow` · `review_label_screen` · `eeg_viewer` · `settings_screen` · `docs_screen` · `design_tokens` · `widgets` · `fonts` |
| **`capture/`** | `clock` · `usb_camera` · `camera_source` · `headband_source` · `eeg_module` · `lsl_outlets` · `session_controller` · `simulation` · `contact_quality` · `contact_log` · `keep_awake` · `round_bell` |
| **`playback/`** | `pyav_player` |
| **`data/`** | `signals` · `session_storage` · `session_xdf` · `xdf_writer` · `session_history` · `labels_store` · `label_agreement` · `label_vocab` · `privacy` · `safe_io` · `export_module` · `app_settings` |
| **`support/`** | `log_setup` · `docs_nav` · `platform_utils` |
| **Tools** (`tools/`, not shipped) | `run_tests` · `generate_demo_session` · `render_diagrams` · `render_screens` · `make_round_bell` |

Threads at a glance: everything runs on the **UI (main) thread** unless its
entry says otherwise. The modules that spawn threads or subprocesses are
`usb_camera`, `eeg_module`, `pyav_player`, `review_label_screen`, `sessions_screen`,
`record_flow` (the `recording.xdf` build) and `export_module`; see the
[threading model](threading-model.md).

---

## App shell & UI screens

### `app.py`
`brain_battle/app.py` — the application shell and `main()` entry point.
`BrainBattleApp(QMainWindow)` builds the sidebar shell, constructs each screen
once, wires their signals together (`session_saved`, `help_requested`,
`exit_flow`, `capture_dir_changed`), and owns window sizing/`QSettings` restore.
`open_help(key)` routes every screen's `?` button to the Documentation screen.
`main()` creates the `QApplication`, registers fonts, and runs the event loop —
it is the target of `python3 -m brain_battle`, the `brain-battle` console
script, and the `brain_battle_main.py` shim. Runs on the UI thread. **Calls:**
`sidebar_shell`, every screen module, `app_settings`, `log_setup`, `fonts`.
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

### `sidebar_shell.py`
The app's frame (DESIGN "App structure"): a 244 px sidebar — the brand, **New
recording** as the one primary button, Sessions and Review & Label, then who is
labelling ("Your name"), the quick **Hide names** switch, the recordings-folder
card (name, free space, a red state when the folder cannot be reached), and
Documentation and Settings. It collapses to a 72 px icon rail on Review & Label
and is hidden for the record flow. `SidebarShell.add_page(..., primary=True)`
makes an entry the primary button; `set_labeller`, `set_names_hidden` and the
`identity_clicked` / `names_hidden_changed` signals connect it to the app.
**Called by:** `app`. **Gotcha:** `refresh_folder_card` must be called whenever
the recordings folder or its free space may have changed (the app calls it on
every page change).

### `sessions_screen.py`
The Sessions home library (DESIGN §1). `SessionsScreen` renders one row per
recording (click anywhere to open), the status tabs with counts, search, sort,
the interrupted-recording banner, the "folder can't be reached" banner, and a
per-row **⋯** menu (export without opening; show in Finder / Explorer). The
row's button says **Label** until the current labeller has labelled it
(`SessionInfo.labels_by`). Folder discovery and sizing run **off the UI thread**
in `_ScanWorker`/`_SizeWorker` (QThreads); the scan worker also reports whether
the folder exists at all. `filter_sort_sessions`, `status_counts`,
`length_text`, `labels_text` and `reveal_in_finder` are pure helpers. **Calls:**
`session_storage`, `safe_io`, `privacy`, `platform_utils`, `widgets`.
**Gotchas:** worker signals are connected to bound methods, never lambdas, so
Qt drops the connection if the screen is destroyed first; the banner's
dismissal is persisted via `session_storage.dismiss_interrupted`.

### `record_flow.py`
The full-screen New-recording flow (DESIGN §2–§4): **Get ready → Recording →
Saved**. `RecordFlow` owns the device slots (`camera_source.CameraSlot`,
`headband_source.HeadbandSlot`) and the `session_controller.SessionController`,
and emits `session_saved`/`exit_flow`. "Get ready" is one page (`_ReadyPage`)
holding the devices (`ConnectStep`) above the plan and Start button
(`SetupStep`) — they used to be two steps
([ADR-0018](../decisions/adr-0018-november-2026-redesign.md)), which is why
`STEP_CONNECT` and `STEP_SETUP` are both `STEP_READY`. `RecordingStep` is the
phase-tinted countdown screen; `SavedStep` the summary. Supporting widgets:
`CameraTile`, `HeadbandView` (status, `ContactStrip` for the four sensors, and
the live EEG via `eeg_viewer.EEGViewer`), `PhaseBar`, `Stepper`. It records
moments (`mark_moment`), prevents sleep (`keep_awake`), logs contact quality
(`contact_log`), stops cleanly early, refuses to start into a folder that is
missing or read-only (`safe_io.folder_status`), owns the
`lsl_outlets.LslPublisher`, and — once a recording has stopped and every camera
file is closed — builds `recording.xdf` on a background thread
(`_maybe_build_xdf`; `wait_for_xdf` blocks until it is done). Runs on the UI
thread (devices run on their own threads). **Calls:** `camera_source`,
`headband_source`, `session_controller`, `session_storage`, `session_history`,
`session_xdf`, `lsl_outlets`, `clock`, `contact_log`, `keep_awake`,
`app_settings`, `safe_io`, `platform_utils`, `round_bell`, `widgets`.
**Gotcha:** `_on_session_started` takes one origin from
`clock.session_origin()` and threads it into every camera and headband — this
is the capture clock ([ADR-0001](../decisions/adr-0001-capture-time-clock.md),
[ADR-0017](../decisions/adr-0017-one-capture-clock.md)); do not stamp sources
from separate clocks.

### `review_label_screen.py`
The merged Review & Label screen (DESIGN §5). `ReviewLabelScreen` opens a
recording, shows the current labeller's labels and the timeline
**immediately**, then loads media in the background (`_MediaLoadWorker`, a
QThread) and attaches a `pyav_player.PlayerController`. Sub-widgets:
`VideoGrid`/`VideoTile` (the 2×2 grid), `BrainActivityCard` (one
`eeg_viewer.EEGViewer` per boxer plus timebase / scale / filter controls; it
can be folded away and shrinks on a small window so the videos keep the room),
`TimelineStrip` (phases, the labeller's three lanes, and a grey lane per
compared labeller). You label **as yourself**: `_open_labels` migrates an old
`labels.json` and opens `labels_store.open_for_labeller(root, name)`; other
labellers' files are read-only here, re-read every few seconds
(`_reload_others`), and compared through `label_agreement` on the Compare tab.
With no name set the panel asks for one and `_place_from_vocab` places nothing.
A save that fails (shared drive gone) keeps the labels in memory, says so, and
retries (`_save_now` / `_retry_save`). Every change autosaves and appends to
`history.jsonl` with the labeller's name. **Calls:** `pyav_player`,
`eeg_viewer`, `labels_store`, `label_agreement`, `label_vocab`, `privacy`,
`session_storage`, `session_history`, `contact_quality`, `export_module`,
`app_settings`, `platform_utils`, `widgets`. **Gotcha:** everything is on one
`t_session_seconds` playhead — there is no separate alignment step; see
[the playback-clock flow](key-flows.md#the-playback-clock).

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
The real Settings screen (DESIGN §7). `SettingsScreen` is five cards: recordings
folder, headbands (known-device dropdowns + BLE search with a same-headband
guard), video quality (resolution **and** bitrate, with the GB/hour copy and the
disk estimate both computed from the bitrate), session defaults + round bell +
Test sound, and the shared label list (`_LabelRow`: add/rename/remove/reorder/
recolour, keys renumbered 1..n). Everything autosaves with a brief "✓ Saved".
**Calls:** `app_settings`, `label_vocab`, `session_storage` (disk estimate),
`eeg_module`/`headband_source` (BLE search), `round_bell` (Test sound).
**Gotcha:** quality is stored as resolution + bitrate in `app_settings`, and the
GB/hour and disk-estimate numbers are **derived** from the bitrate (not
hardcoded) — `tests/test_settings_screen.py` asserts this.

### `docs_screen.py`
The in-app Documentation screen. Renders the Markdown under `docs/` at runtime
using Qt's `QTextDocument.setMarkdown` via `QTextBrowser` — **no QtWebEngine**.
`DocsScreen` provides the nav tree, always-visible search with highlighting, and
back/forward. `_ContentBrowser` scales images to the reading column;
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
recording/dropped/encoder state, exposing preview frames and fps via signals.
`make_slots(parent)` builds the four slots (two side, two POV). **Called by:**
`record_flow`. **Calls:** `usb_camera`, `simulation`. **Thread:** the wrapped
`USBCameraThread` runs off the UI thread; the slot itself lives on the UI thread.

### `usb_camera.py`
The camera capture + encode layer. `USBCameraThread(QThread)` opens a camera
(cv2, or a simulated capture) and runs a tight capture loop; `FrameEncoder`
spawns one `ffmpeg` subprocess per camera, feeds it raw frames over stdin
(fragmented H.264, crash-safe), and writes the per-frame sidecar
`<stem>.timestamps.csv` — one row per **encoded** frame, matching the MP4 frame
count exactly ([ADR-0004](../decisions/adr-0004-crash-safe-h264.md)).
`preflight_recording()` blocks recording if ffmpeg is missing; `_resolve_encoder`/
`_validate_encoder` pick a hardware H.264 encoder with a libx264 fallback.
**Called by:** `camera_source`, the demo generator. **Thread:** capture thread +
one encoder-writer thread + the ffmpeg child. **Gotcha:** a full frame queue
drops frames and increments a counter rather than ever blocking capture; a dead
ffmpeg stops only that camera.

### `headband_source.py`
`HeadbandSlot(QObject)` — one boxer's headband for the record flow. Wraps
`eeg_module._MuseDirectReader` (or `simulation.SimMuseReader`) and surfaces
connection, battery, per-sensor contact quality, a rolling buffer for the live
EEG display (`trace()`, all five inputs, capture-clock times), and reconnect/
append. When the flow gives it an `LslPublisher` (`publisher`), every chunk is
also pushed to the live LSL streams. **Called by:** `record_flow`. **Calls:**
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
signal. **Gotcha:** the reader's time source is `clock.unix_now` (the one
capture clock), not `time.time()`; how late Bluetooth delivers a sample is
**Not yet verified on hardware** (logged at every recording start) — see
[timing & accuracy](../research-and-data/timing-and-accuracy.md). The movement
and pulse sensors have never been received from a real band.

### `clock.py`
`brain_battle/capture/clock.py` — the one capture clock
([ADR-0017](../decisions/adr-0017-one-capture-clock.md)). Everything is stamped
from `time.monotonic()`; this module measures once per process the constant
offsets to Unix time and to the LSL clock and converts between them
(`unix_now`, `mono_to_unix`, `mono_to_lsl`, `unix_to_lsl`). `session_origin()`
returns a recording's start on all three scales — exactly what the manifest
stores as `capture_clock`. Qt-free; pylsl is loaded lazily and its absence
degrades to "LSL time = monotonic time". It also points liblsl at
`support/lsl_api.cfg` to silence its start-up logging. **Called by:**
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
from the UI thread, video frames from each camera's writer thread). **Called
by:** `record_flow`, `headband_source`, `camera_source`.

### `session_controller.py`
A pure-logic phase state machine (`IDLE→BASELINE→ROUND↔BREAK→DONE`), driven by a
1 Hz QTimer, emitting `phase_changed`/`tick`/`beep_requested`. `SessionConfig`
holds rounds/durations; `_build_timeline` expands it to `PhaseInfo` steps.
Deliberately **Qt-optional / headless-testable**. **Called by:** `record_flow`,
and reused by the demo generator. **Gotcha:** it owns timing logic only — it
writes nothing to disk; `record_flow` turns its signals into `phase_markers.csv`
rows.

### `contact_quality.py`
A documented per-sensor EEG contact-quality heuristic (`channel_status` →
flat/clipped/noisy/good; `summarise` → a plain-language line like "Weak contact:
left ear"). `assess_window`/`assess_at` assess a dataframe window. `SENSOR_NAME`
maps channel codes to plain names. **Called by:** `headband_source`,
`record_flow`, `review_label_screen`, `contact_log`.

### `contact_log.py`
`ContactLogWriter` appends a per-sensor contact-quality row into the recording
folder during capture (append-only, line-buffered). `weak_periods` summarises
weak spans; `describe_weak` turns one into a "worth checking" line, filtered to
spans over 30 s. **Called by:** `record_flow` (during recording), the Saved
screen, and Review. **Gotcha:** `_HEADER` is the CSV schema documented in
[file formats](../research-and-data/file-formats.md); changing it needs that doc
updated (honesty test).

### `keep_awake.py`
`KeepAwake` prevents the computer sleeping during a recording (macOS:
`caffeinate -dimsu`; Windows: `SetThreadExecutionState`). Best-effort and
cross-platform. **Called by:** `record_flow` (started at record start, released
at stop).

### `round_bell.py`
`RoundBell(QObject)` plays `assets/sounds/round_bell.wav` (a real boxing-bell
recording — see [credits](../project/credits.md)) through `QSoundEffect`;
`bundled_bell_path` resolves the WAV's path regardless of cwd. **Called by:**
`record_flow` (round start) and `settings_screen` (Test sound). **Note:** this was
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

### `privacy.py`
Boxer names and hiding them. `shown_name` / `versus` are what screens call to
print a name (honouring "Hide names on screen"); `scrub_manifest`, `scrub_text`
and `neutral_recording_name` make the name-free copies a "Replace boxer names"
export contains. Pure logic, no Qt. **Called by:** `sessions_screen`,
`review_label_screen`, `session_xdf`, `export_module`. See
[Personal data note](../research-and-data/personal-data.md).

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
manifest, history, README) straight from the recording folder. With
`hide_names=True` the XDF, manifest copy, history copy, README and suggested
file name carry no boxer names (`privacy`); the recording itself is untouched. `build_coregistered_eeg`
merges both bands onto one uniform 256 Hz `t_session_seconds` grid (`_01` is
always Boxer A, `_02` Boxer B); `VideoExportWorker(QThread)` is the optional 2×2 merge
(hardware-encoder-validated with a libx264 fallback). `ExportOptionsDialog` runs
it with a progress bar and the two choices (replace names; combined video).
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
(`number_font` gives equal-width digits for timers). There is no
dark palette and no theme switching ([ADR-0007](../decisions/adr-0007-light-only-design.md),
guarded by `tests/test_light_theme.py`). **Called by:** every UI module.

### `widgets.py`
`brain_battle/ui/widgets.py` — the shared building blocks every screen uses, so
a button or a status line looks the same everywhere: text helpers (`title`,
`heading`, `body`, `muted`, `caption`), `Card`, `Banner`, the button factories
(`primary_button`, `secondary_button`, `ghost_button`, `danger_button`,
`icon_button`, `help_button`), `Segmented`, `StatusLine` (icon + text; each
state has its own icon shape), `pill`, `boxer_badge` / `boxer_chip`,
`TimerLabel` (hand-painted, equal-width digits), `Dot`, `clear_layout`.
**Called by:** every screen. **Gotcha:** `Card` gives itself a unique object
name and scopes its stylesheet to it, so its background never leaks into child
widgets.

### `platform_utils.py`
`brain_battle/support/platform_utils.py` — the few things that differ between
macOS and Windows, in one place: `format_date` / `format_date_time` /
`format_iso` (no `%-d`, which Windows rejects), `file_manager_name` /
`show_in_file_manager_label` / `reveal_in_file_manager` / `open_folder`
(Finder vs. Explorer), `shortcut` (⌘Z vs. Ctrl+Z), `computer_name`,
`ffmpeg_install_hint`. Stdlib only; every function takes an optional
`platform_name` so tests exercise both systems on one machine. See
[Cross-platform & CI](cross-platform-and-ci.md).

### `fonts.py`
`register_fonts()` registers the bundled Inter font with Qt so the UI looks the
same regardless of installed fonts; best-effort (falls back silently). **Called
by:** `brain_battle_main` at startup.

### `log_setup.py`
Central logging: `init_logging()` (console at INFO) and
`attach_session_log(root)` (a DEBUG file handler inside each session folder).
**Called by:** `brain_battle_main` (startup), `record_flow` (per session).

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
`--force`. See [AGENTS.md's "Demo session"](../project/planning-documents.md) and
[simulation & demo](simulation-and-demo.md). Covered by `tests/test_demo_session.py`.

### `tools/render_diagrams.py`
Renders every `docs/diagrams/*.mmd` to `.svg` (GitHub) + `.png` (what Markdown
references) + `.hash` (freshness), via `npx @mermaid-js/mermaid-cli`. `--check`
exits nonzero if anything is stale. See [design system](design-system.md#diagrams).

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
| `test_privacy.py` | Names kept in `recording.xdf` by default; a name-free export has no name in any file; hide names on screen. |
| `test_platform.py` | Mac/Windows differences: dates without `%-d`, Finder vs. Explorer, shortcuts, paths, Windows-safe names, encoder fallback, keep-awake. |
| `test_shared_folder.py` | Atomic writes, the folder lock, several processes labelling at once, a folder that disappears, the scan cache. |
| `test_label_vocab.py` | Shared `label_list.json`: seed, round-trip, tolerant load. |
| `test_contact_quality.py` | Per-sensor heuristic + plain-language summary. |
| `test_export.py` | Analysis bundle contents + events.csv ordering. |
| `test_flash_sync.py` | Flash within one frame period across tiles (slow — 12-min demo). |
| `test_no_placeholders.py` | No placeholder UI copy; no placeholder screens; channel codes only inside the EEG viewer. |
| `test_capture_clock.py` | Capture-clock reconciliation, co-registration, manifest bridge. |
| `test_xdf.py` | The XDF writer and `recording.xdf` read back with pyxdf; the one capture clock vs. `pylsl.local_clock()`. |
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
