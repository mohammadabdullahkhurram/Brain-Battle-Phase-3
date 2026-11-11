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
[CLAUDE.md's "Documentation is part of every change"](../contributing/docs-guide.md#documentation-is-part-of-every-change-owner-rule)).

## Grouped overview

| Group (subpackage) | Modules |
|---|---|
| **Package top** | `app` · `version` · `paths` · (root shim `brain_battle_main`) |
| **`ui/`** | `sidebar_shell` · `sessions_screen` · `record_flow` · `review_label_screen` · `settings_screen` · `docs_screen` · `design_tokens` · `fonts` |
| **`capture/`** | `usb_camera` · `camera_source` · `headband_source` · `eeg_module` · `eeg_plot_pg` · `session_controller` · `simulation` · `contact_quality` · `contact_log` · `keep_awake` · `round_bell` |
| **`playback/`** | `pyav_player` |
| **`data/`** | `session_storage` · `session_history` · `labels_store` · `label_vocab` · `export_module` · `app_settings` |
| **`support/`** | `log_setup` · `docs_nav` |
| **Tools** (`tools/`, not shipped) | `generate_demo_session` · `render_diagrams` · `render_screens` · `make_round_bell` |

Threads at a glance: everything runs on the **UI (main) thread** unless its
entry says otherwise. The modules that spawn threads or subprocesses are
`usb_camera`, `eeg_module`, `pyav_player`, `review_label_screen`, `sessions_screen`,
and `export_module`; see the [threading model](threading-model.md).

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
The DESIGN "App structure" shell: a 232 px sidebar (logo, the screen buttons, a
recordings-folder card with a live free-space bar, Settings at the bottom) that
collapses to a 72 px icon rail on Review & Label and is fully hidden for the
record flow. `SidebarShell.add_page(...)` registers each page with its collapse/
hide behaviour; `SidebarItem` is one nav button. **Called by:** `brain_battle_main`.
**Gotcha:** the folder card's free-space bar colour comes from `_disk_status`
and must be refreshed (`refresh_folder_card`) whenever the recordings folder or
its free space changes.

### `sessions_screen.py`
The Sessions home library (DESIGN §1). `SessionsScreen` renders the session table,
search, status-filter chips with counts, sort, the interrupted-recording
banner, and "Show in Finder". Folder discovery and sizing run **off the UI
thread** in `_ScanWorker`/`_SizeWorker` (QThreads) so a big folder never freezes
the list. `filter_sort_sessions`, `status_counts`, and `reveal_in_finder` are
pure helpers (headlessly tested). **Calls:** `session_storage` (discovery,
status, sizing). **Gotcha:** the banner's dismissal is persisted via
`session_storage.dismiss_interrupted`, so dismissing is durable across launches.

### `record_flow.py`
The full-screen guided New-recording flow (DESIGN §2–§5): Connect devices → Set
up session → Record → Saved. `RecordFlow` owns the device slots
(`camera_source.CameraSlot`, `headband_source.HeadbandSlot`) and the
`session_controller.SessionController`, and emits `session_saved`/`exit_flow`.
The four steps are `ConnectStep`, `SetupStep`, `RecordingStep`, `SavedStep`;
supporting widgets include `CameraTile`, `HeadbandView`, `MiniTrace`,
`PhaseBar`, and `Stepper`. It records moments (`mark_moment`), prevents sleep
(`keep_awake`), logs contact quality (`contact_log`), and stops cleanly early.
Runs on the UI thread (devices run on their own threads). **Calls:**
`camera_source`, `headband_source`, `session_controller`, `session_storage`,
`session_history`, `contact_log`, `keep_awake`, `app_settings`,
`round_bell`. **Gotcha:** `begin_recording` captures one shared origin `(t0_mono,
t0_unix)` and threads it into every camera and headband — this is the capture
clock ([ADR-0001](../decisions/adr-0001-capture-time-clock.md)); do not stamp
sources from separate clocks.

### `review_label_screen.py`
The merged Review & Label screen (DESIGN §6). `ReviewLabelScreen` opens a session,
shows labels and the timeline **immediately**, then loads media in the
background (`_MediaLoadWorker`, a QThread) and attaches a
`pyav_player.PlayerController`. Sub-widgets: `VideoGrid`/`VideoTile` (the 2×2
grid), `BrainActivityCard`/`BrainTrace` (jargon-free EEG), `TimelineStrip` (the
scrubbable phases/labels timeline). Labelling drives `labels_store.LabelsStore`
(add/undo/redo), autosaves `labels.json`, and appends `history.jsonl`. **Calls:**
`pyav_player`, `labels_store`, `label_vocab`, `session_storage`,
`session_history`, `contact_quality`, `export_module`. **Gotcha:** everything is
on one `t_session_seconds` playhead — there is no separate alignment step; see
[the playback-clock flow](key-flows.md#the-playback-clock).

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
connection, battery, per-sensor contact quality, a live trace, and reconnect/
append. **Called by:** `record_flow`. **Calls:** `eeg_module`,
`contact_quality`, `simulation`. **Thread:** the reader runs on its own QThread;
the slot lives on the UI thread.

### `eeg_module.py`
The Muse-S EEG stack. `_MuseDirectReader(QThread)` is a direct BLE connection
(muselsl `Muse` in-process via `bleak`, no subprocess/LSL) that streams EEG and
**streams it to disk** during recording: a background CSV-writer thread appends
each chunk (line-buffered, periodic flush+fsync) while the BLE callback only does
a non-blocking `queue.put` ([ADR-0003](../decisions/adr-0003-eeg-streamed-to-disk.md)).
`_RingBuffer` is a pre-allocated NumPy ring buffer for the live view;
`EEGLiveWidget` is the live display; `DevicePickerDialog` scans/claims a device;
`boxer_labels()` reads the A/B mapping from `app_settings`. `_MuseScanThread`
scans for bands. **Thread:** reader + CSV-writer threads. **Gotcha:** the EEG
CSV's `t_session_seconds` column is `muse_timestamp - capture_t0_unix`; the EEG
clock source itself is **Not yet verified on hardware** (logged at every session
start) — see [timing & accuracy](../research-and-data/timing-and-accuracy.md).

### `eeg_plot_pg.py`
pyqtgraph live plots: `WaveformPlot` (4-channel scrolling waveform) and
`BandPowerPlot` (per-channel relative band power δ θ α β γ). Plain sensor names,
no channel codes. Replaced the old matplotlib canvas for speed. **Called by:**
`eeg_module`. **Thread:** UI thread (fed by reader signals).

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
(`disk_space_verdict`, `estimate_session_bytes`) also live here. **Called by:**
`record_flow`, `sessions_screen`, `review_label_screen`, `export_module`,
`settings_screen`. **Gotcha:** sessions are discovered **by manifest, not folder
prefix** — do not reintroduce a prefix check.

### `labels_store.py`
`labels.json` schema v2 + an in-memory `LabelsStore` with undo/redo (each
mutating call records an inverse op). `new_label(...)` makes a label
(`who ∈ {A, B, Both}`, `kind ∈ {point, duration}`, numeric `t_start`/`t_end`);
`migrate_v1_to_v2` upgrades old files (writing a `.v1.bak`); `events_timeline`
merges labels with phase markers. Pure logic, no Qt
([ADR-0006](../decisions/adr-0006-labels-v2.md)). **Called by:**
`review_label_screen`, `export_module`, the demo generator, tests.

### `label_vocab.py`
The shared team label list, stored as `label_list.json` **in the recordings
folder** (so a shared folder shares one vocabulary), seeded from the DESIGN
default palette. `load`/`save`/`renumber`/`next_color`. Pure logic, no Qt.
**Called by:** `settings_screen` (editor), `review_label_screen` (place labels).
**Gotcha:** the list order **is** the 1–9 key mapping; `renumber` keeps keys
contiguous.

### `session_history.py`
The append-only `history.jsonl` per recording: `log_event(root, event, details)`
(flush+fsync), `read_history` (tolerant reader), and the `EVENT_*` constants.
Each line records time, OS user, app version, event, details. **Called by:**
`record_flow`, `review_label_screen`, `sessions_screen`. **Gotcha:** `EVENT_*`
constants are documented in [file formats](../research-and-data/file-formats.md)
(honesty test); `user_source: "os"` is a placeholder for future accounts.

### `export_module.py`
The analysis bundle. `SessionExportWorker(QThread)` builds the export zip
(labels.csv v2, events.csv, `eeg_coregistered.csv`, raw EEG + videos with
sidecars, manifest, history, README) straight from the recording folder;
`build_coregistered_eeg` merges both bands onto one uniform 256 Hz
`t_session_seconds` grid; `VideoExportWorker(QThread)` is the optional 2×2 merge
(hardware-encoder-validated with a libx264 fallback). `ExportOptionsDialog` runs
it with a progress bar. **Called by:** `review_label_screen`. **Thread:** export
runs on a worker thread. **Gotcha:** the combined video is **optional and off by
default** — most analysis wants the data, not a preview.

---

## Support

### `app_settings.py`
The user-config store: one JSON file in the OS config directory holding the
headband→Boxer A/B mapping + known-headbands list, session defaults, round bell,
and video quality (resolution + bitrate). `capture_dir_resolved()` resolves the
recordings folder (`BRAIN_BATTLE_CAPTURE_DIR` always wins when set); `gb_per_hour`
derives the GB/hour copy from the bitrate ([ADR-0010](../decisions/adr-0010-app-settings-store.md)).
**Called by:** `settings_screen`, `record_flow`, `sessions_screen`, `eeg_module`,
`usb_camera` path. **Gotcha:** this replaced the hardcoded
`eeg_module.BOXER_LABELS` and `usb_camera`'s fixed resolution/bitrate — read
settings, don't hardcode.

### `design_tokens.py`
The **light-only** design system (DESIGN tokens) + QSS factories (`qss_*`), the
palette accessors, boxer/phase colours, and bundled-font helpers. There is no
dark palette and no theme switching ([ADR-0007](../decisions/adr-0007-light-only-design.md),
guarded by `tests/test_light_theme.py`). **Called by:** every UI module.

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
cv2-shaped synthetic camera; `make_muse_reader` returns a simulated reader;
failure injection covers a missing camera and a headband drop. **Called by:**
`usb_camera`, `camera_source`, `headband_source` (only when the flag is set).
**Gotcha:** with the flag unset there is no trace of it in the UI; Phase 6
excludes the module from the packaged app.

---

## Tools (`tools/` — developer-only, not shipped)

### `tools/generate_demo_session.py`
Generates a realistic v3 demo session (4 camera MP4s + sidecars including one
deliberately drifting camera, 2 EEG CSVs, `phase_markers.csv`, a `demo:true`
manifest) by **reusing the real writers**, so the fixture can't drift from the
real format. `--out` is required and refuses the real recordings folder without
`--force`. See [CLAUDE.md's "Demo session"](../project/planning-documents.md) and
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
tests, not standalone tests.

| Test file | Guards |
|---|---|
| `test_smoke.py` | Import/construct floor; acceptance gate for deletions/refactors. |
| `test_player.py` | PyAV single-clock player: frame-for-t, pacing, scrub coalescing. |
| `test_review_tab.py` | Review & Label label side: fast open, duration flow, undo/redo, autosave. |
| `test_labels_v2.py` | `labels_store` v2 schema, v1→v2 migration, undo/redo, timeline. |
| `test_label_vocab.py` | Shared `label_list.json`: seed, round-trip, tolerant load. |
| `test_contact_quality.py` | Per-sensor heuristic + plain-language summary. |
| `test_export.py` | Analysis bundle contents + events.csv ordering. |
| `test_flash_sync.py` | Flash within one frame period across tiles (slow — 12-min demo). |
| `test_no_placeholders.py` | No placeholder/jargon UI copy; no placeholder screens. |
| `test_capture_clock.py` | Capture-clock reconciliation, co-registration, manifest bridge. |
| `test_demo_session.py` | The demo generator end to end. |
| `test_sessions.py` | Sessions discovery/status/search/filter/sort, banner, scan. |
| `test_ring_buffer.py` | Ring-buffer ordering. |
| `test_eeg_streaming.py` | Streamed EEG recorder under SIGKILL. |
| `test_video_recording.py` | Crash-safe H.264 recording. |
| `test_crash_recovery.py` | Interrupted detection + disk-space branches. |
| `test_history_log.py` | Append-only history log + hooks. |
| `test_light_theme.py` | Light-only guard (no dark tokens/theme machinery). |
| `test_layout_fit.py` | Every page fits (nothing clipped) at the three sizes. |
| `test_record_flow.py` | The guided flow end to end with simulated devices. |
| `test_app_settings.py` | The settings store + `BOXER_LABELS` removal. |
| `test_settings_screen.py` | The real Settings screen (computed bitrate, editor, end-to-end). |
| `test_contact_log.py` | Contact-log weak-period spans + the >30 s filter. |
| `test_docs.py` | The Documentation tab + docs content (D1 honesty checks). |
| `test_research_docs.py` | Research & data format reference matches code (D2). |
| `test_developer_docs.py` | Developer guide + ADR + Project honesty checks (D3 — see [testing strategy](testing-strategy.md)). |
