# Brain Battle — Roadmap

Single source of planned work. Supersedes any "nothing else is planned"
wording elsewhere in the repo. See `CLAUDE.md`'s "Project situation" and
"Working agreement" sections before starting anything here — in particular:

- **The owner approves each phase below before it starts.** Do not start the
  next phase on your own initiative, even if the current one is finished.
- Hardware is currently broken and nobody has inspected real Muse-S EEG from
  a sparring round yet (see `CLAUDE.md`). Everything through Phase 6 is
  built and tested against the **generated demo session** from Phase 1, not
  against real cameras or headbands.
- The synchrony analysis method is undecided. Don't let any phase below
  turn into an excuse to chase sync precision tighter than "tens of ms" —
  that decision belongs to whoever ends up owning the analysis method.

Each item states *why* it's planned and the files it will most likely touch,
based on reading the current codebase. File lists are a starting point for
whoever picks up the item, not a contract.

---

## Phase 1 — Foundation

- **`requirements.txt` pinning current deps (no matplotlib).**
  *Why:* there is no pinned dependency file today — only the `pip install`
  line in `CLAUDE.md`/`README.md`, which drifts (it still listed `matplotlib`
  until this pass even though nothing has imported it since `93888f4`).
  *Files:* new `requirements.txt`; `README.md` (pip line).

- **Demo session generator** — a script that creates a realistic fake
  session folder in the v3 layout: 4 videos with `.timestamps.csv` sidecars
  (including a slight, deliberate fps drift on at least one camera so later
  drift-detection work has something real to catch), 2 EEG CSVs with a
  `t_session_seconds` column, a `phase_markers.csv`, and a
  `session_manifest.json` with a `capture_clock` origin.
  *Why:* hardware is broken and nobody has real capture data; every
  subsequent phase (data safety, the new screens, labelling, export) needs a
  repeatable fixture to build and test against instead of hand-waving.
  *Files:* new script, e.g. `tools/generate_demo_session.py`; mirrors the
  contracts in `session_storage.py` (`SessionLayout`, `PhaseRecord`,
  `_write_manifest`), `usb_camera.py` (`_open_timestamp_sidecar`/
  `_stamp_frame` CSV format), and `eeg_module.py` (`stop_recording`'s CSV
  columns: `TP9,AF7,AF8,TP10,AUX,timestamps[,t_session_seconds]`).

- **Decide later: merge `develop` → `main`** (owner's call; `main` is meant
  to be the verified, shippable baseline).
  *Why:* per `CLAUDE.md`'s branching convention, `develop` only merges to
  `main` when a version is verified and ready to ship — that has not
  happened for the capture-clock work yet (eyes-on-glass + EEG clock-source
  checks are still open), so this is explicitly not automatic.
  *Files:* none — a merge decision, not a code change.

---

## Phase 2 — Data safety

- **EEG streamed to disk in chunks during recording (not RAM-only).**
  *Why:* today `_MuseDirectReader` buffers every sample in memory and only
  writes the CSV in `stop_recording()` — a crash mid-session loses the
  entire recording for that band (~9 MB per 15 minutes, but 100% of it).
  *Files:* `eeg_module.py` (`_MuseDirectReader.start_recording`,
  `_on_eeg`, `stop_recording`), `live_session_tab.py` (recording wiring).

- **Crash-safe video container (MKV or fragmented MP4).**
  *Why:* `usb_camera.py` writes `mp4v` via `cv2.VideoWriter`, which only
  finalizes its moov atom in `release()`; an unclean process exit (crash,
  force-quit, power loss) before that point leaves an unplayable file.
  *Files:* `usb_camera.py` (`USBCameraThread.run`'s writer init/`finally`
  block), possibly `usb_camera_slot.py`; `export_module.py` if the merged
  quad-grid export needs a transcode step for the new container.

- **Labels autosaved into the session folder on every change; reopen and
  continue labelling.**
  *Why:* today placed labels live only in the `LabellingTab`'s in-memory
  widget list and are written out only when EXPORT LABELS CSV is clicked —
  closing the tab or crashing loses every unexported label.
  *Files:* `brain_battle_main.py` (`LabellingTab._place_label`,
  `LabelMarker._remove`, `_open_session_folder`), `export_module.py`
  (`LabelsExporter`, reused for the autosave write path).

- **Crash-recovery prompt on next launch for interrupted sessions.**
  *Why:* a session with no `SESSION_END` marker or an unclosed final phase
  in the manifest is a session that didn't shut down cleanly; the operator
  currently has no way to discover this except by poking at folders.
  *Files:* `session_storage.py` (the manifest already carries the needed
  state — phases list, missing `end_unix`), the new Sessions home screen
  (Phase 3) as the natural surface for the prompt, or `brain_battle_main.py`
  at startup in the interim.

- **Disk-space check + estimated session size before recording.**
  *Why:* 4 cameras + 2 EEG streams over a multi-round session can be
  multi-GB (mp4v is bulky), and there is currently no pre-flight warning —
  only the camera/EEG pre-flight dialogs in `_on_start_clicked`, which don't
  check disk at all.
  *Files:* `live_session_tab.py` (`_on_start_clicked` pre-flight checks),
  `session_setup_dialog.py` (surfacing the estimate alongside the
  rounds/duration config).

---

## Phase 3 — Structure and design

- **Sessions home screen** — cards for every session in the storage folder
  (date, boxers, rounds, duration, status badge Recorded/Labelled/Exported),
  search/sort, "Show in Finder", one-click open.
  *Why:* there is no library view today — the app always jumps straight
  into Live Monitor, and finding a past session means browsing the
  filesystem by hand. This is the new entry point implied by the
  Sessions → Record → Review & Label direction in `CLAUDE.md`.
  *Files:* new module, e.g. `sessions_home_tab.py`; reads
  `session_storage.py`'s on-disk layout and `session_manifest.json`;
  `sidebar_shell.py` to register the new page as the default landing page.

- **Record as a guided flow**: device checklist with green/red per camera
  and headband → session setup with defaults → recording view with big
  timer and phase name → done screen.
  *Why:* today's Live Monitor is one dense screen combining camera grid,
  EEG widgets, and session controls at once; `_on_start_clicked`'s own
  pre-flight warnings (camera/EEG connection checks) already hint that
  operators get this wrong without a guided checklist.
  *Files:* `live_session_tab.py` (likely split into several guided-flow
  screens/steps), `usb_camera_panel.py`, `eeg_module.py`
  (`EEGLiveWidget` connection status), `session_setup_dialog.py`,
  `circular_timer.py` (reused for the recording-view timer).

- **Merge Review and Labelling into one "Review & Label" screen**, one
  video stack, all on the capture-clock axis (fixes the hardcoded 0 offsets
  in today's Labelling tab).
  *Why:* the two tabs currently run two entirely different video-playback
  stacks (python-vlc/vmem in `SyncReviewTab` vs. Qt Multimedia in
  `vlc_module.VLCVideoSlot`) and two different, sometimes-inconsistent
  alignment conventions — `LabellingTab._open_session_folder` hardcodes
  `offset_sec=0.0` for every source, and even the Review→Label handoff
  hardcodes EEG's offset to `0.0` instead of the Review tab's `axis_shift`.
  One screen with one playback stack removes an entire class of skew bugs.
  *Files:* `brain_battle_main.py` (`SyncReviewTab` + `LabellingTab` merge —
  pick one of the two playback stacks and delete the other),
  `vlc_module.py`, `sidebar_shell.py`.

- **Settings screen**: storage folder, headband → Boxer A/B assignment
  (replaces hardcoded `BOXER_LABELS`), video resolution/fps, default
  durations, label vocabulary, round-bell on/off.
  *Why:* `BOXER_LABELS = {"MuseS-7538": "BOXER A", "MuseS-7564": "BOXER B"}`
  (`eeg_module.py`), the camera resolution/fps defaults (`usb_camera.py`),
  the label palette (`labels_config.py`/`labels_data.json`), and the sync
  beep are all hardcoded in source today — a non-technical researcher has no
  way to change any of them without editing code.
  *Files:* new `settings_tab.py`; `eeg_module.py` (`BOXER_LABELS`),
  `usb_camera.py` (`USBCameraThread.__init__` defaults), `labels_config.py`,
  `sync_beep.py`, `session_setup_dialog.py` (default durations).

- **Light-mode-only design system**: remove dark tokens, theme toggle,
  hex string-replacement theming and prefs file; large targets, WCAG AA
  contrast, never colour-only meaning, plain-language labels and errors,
  helpful empty states.
  *Why:* the dark/light theming today works by string-replacing hex colors
  through each tab's `_apply_theme()` method, requires an app restart to
  actually apply (`SidebarShell._toggle_theme`'s own comment explains why),
  and the new target audience doesn't need a theme choice at all — one
  simpler, accessible light design replaces it.
  *Files:* `design_tokens.py` (strip the dark palette, `set_theme`/
  `on_theme_change`, the four-surface-level dark treatments), `sidebar_shell.py`
  (`_toggle_theme`, theme button, `.brain_battle_prefs.json` handling),
  every tab's `_apply_theme`/`_restyle` method (`brain_battle_main.py`,
  `live_session_tab.py`, `usb_camera_slot.py`, `eeg_module.py`, etc.).

---

## Phase 4 — Labelling

- **Every label tied to a boxer (A/B).**
  *Why:* `LabelMarker`/`_place_label`/`get_labels()` carry no boxer field
  today — any between-boxer analysis needs to know whose event it is.
  *Files:* `brain_battle_main.py` (`LabelMarker`, `_place_label`,
  `get_labels`), `export_module.py` (`LabelsExporter`'s CSV columns).

- **Point events and duration events (start/end).**
  *Why:* labels are points-only today (`timestamp_sec` with no end); a
  punch is naturally a point but a clinch, knockdown, or stoppage is
  naturally a duration — forcing everything into a point loses information.
  *Files:* `brain_battle_main.py` (`LabelMarker`, `_place_label`,
  `TimelineWithMarkers`/`_MarkerStrip` rendering for a duration chip),
  `export_module.py` (`LabelsExporter` CSV schema).

- **One shared label vocabulary per project stored with sessions, not next
  to the code; remove committed `labels_data.json`.**
  *Why:* `labels_data.json` is currently committed to the repo and silently
  overrides `labels_config.py`'s defaults for every machine that checks the
  repo out — one developer's custom palette (`MOVEMNET_WITH_CONTACT` [sic],
  `PUNCH_LEFT`, `PUNCH_RIGHT`, `BLOCK`) becomes everyone's default.
  *Files:* `labels_config.py` (relocate storage to project/session data,
  not the source tree), delete `labels_data.json` from the repo, the
  Settings screen (Phase 3) as the new edit surface.

- **Frame stepping, 0.25×/0.5× playback, undo/redo, editable list of
  placed labels.**
  *Why:* precise event placement (e.g. the exact frame of first contact)
  needs finer transport control than today's PLAY/PAUSE-only controls and
  the Space-bar toggle offer.
  *Files:* `brain_battle_main.py` (`LabellingTab` transport controls,
  `SyncPlaybackController`), `vlc_module.py`.

- **Labels stored as numbers on the `t_session_seconds` axis** (no
  HH:MM:SS string round-trip).
  *Why:* `_place_label`/`get_labels`/`_rebuild_timeline_markers` currently
  format timestamps to an `HH:MM:SS.mmm` string and parse them back by
  splitting on `:` — a lossy, fragile round-trip for no benefit once labels
  live on the same numeric axis as everything else.
  *Files:* `brain_battle_main.py` (`LabelMarker`, `_place_label`,
  `get_labels`, `_rebuild_timeline_markers`).

---

## Phase 5 — Remaining report items and research value

- **Use the FULL per-frame sidecar for playback timing** (not just the
  first row); detect and flag capture-rate drift.
  *Why:* `_read_frame_sidecar_start` (`brain_battle_main.py`) reads only the
  sidecar's first row today — any mid-session frame-rate drift (which
  `usb_camera.py` already logs a warning for, but never surfaces
  structurally) is invisible to alignment.
  *Files:* `brain_battle_main.py` (`_read_frame_sidecar_start`, the
  `_align` path), `usb_camera.py` (where drift is logged but not reported).

- **Downscale live preview frames before emitting; configurable record
  resolution/fps.**
  *Why:* `usb_camera.py` hardcodes 960×540/30fps and a fixed preview-FPS
  cap; the Settings screen (Phase 3) needs a real control to back it.
  *Files:* `usb_camera.py` (`USBCameraThread.__init__` defaults,
  `PREVIEW_FPS_CAP`), `usb_camera_slot.py`, `usb_camera_panel.py`.

- **Single "Export for analysis" button; merged 2×2 video optional, off by
  default; add one master events file (markers + labels).**
  *Why:* today's export always attempts the slow quad-grid video merge and
  ships phase markers and labels as separate files; most analysis work
  wants the data, not a preview video, and wants markers+labels on one
  timeline rather than cross-referenced by hand.
  *Files:* `export_module.py` (`FullSessionExportWorker`,
  `VideoExportWorker`, `build_coregistered_eeg`), `brain_battle_main.py`
  (`_export_synced_data`).

- **Optional per-round clip export using markers.**
  *Why:* `phase_markers.csv` already has exact round boundaries; cutting
  per-round clips for sharing/review is a natural, cheap extension.
  *Files:* `export_module.py` (new worker using `phase_markers` +
  `ffmpeg -ss`/`-t`), `brain_battle_main.py` (`_read_markers_for_export`
  already has the needed data).

- **Record Muse accelerometer and PPG alongside EEG.**
  *Why:* the Muse-S exposes these channels and movement artifact/
  physiological covariates matter for a boxing synchrony study — but only
  once the plain EEG channels are confirmed usable (see "Deferred until
  hardware works" below).
  *Files:* `eeg_module.py` (`_MuseDirectReader`, `EEGLiveWidget`/
  `_RingBuffer` — currently EEG-channel-only).

- **EEG signal-quality view per channel in review.**
  *Why:* per `CLAUDE.md`'s "Project situation," nobody has inspected real
  Muse-S EEG from a sparring round — a quality view (noise/flatline/contact
  indicator) is needed before anyone trusts what they're looking at.
  *Files:* `eeg_module.py` (`EEGReviewWidget`), `eeg_plot_pg.py`.

- **Delete dormant `sync_engine.py` code (move `SourceOffset`/`SyncResult`
  out).**
  *Why:* already flagged in `CLAUDE.md`'s deferred work #4 and confirmed
  still dead on `develop` — `compute_sync`/`SyncWorker`/`_xcorr_offset`/
  `get_eeg_window` have zero callers; only the two dataclasses are live.
  *Files:* `sync_engine.py` (delete everything but `SourceOffset`/
  `SyncResult`, or move them into their own module), `brain_battle_main.py`
  (update the import), `tests/test_smoke.py` (module-discovery count).

- **Sync beep becomes a round bell only** (fix docstring; toggle in
  Settings).
  *Why:* `sync_beep.py`'s docstring still claims the beep helps "the
  audio-based cross-correlation sync engine... lock onto" it — that engine
  is dead code on `develop`, and in-app cameras never recorded audio in the
  first place, so the claim no longer applies to anything.
  *Files:* `sync_beep.py` (docstring), the Settings screen (Phase 3) for
  the on/off toggle, `live_session_tab.py` (wiring).

- **Extend tests to the new flows.**
  *Why:* `tests/` currently covers the capture-clock + ring-buffer + smoke
  floor only; none of Phases 2-5's new flows (autosave, drift detection,
  duration labels, quality view, the demo-session generator itself) have
  any coverage yet.
  *Files:* new `tests/test_*.py` files per flow; `tests/test_smoke.py`
  (module-discovery will need to pick up every new module).

---

## Phase 6 — Shipping

- **Package as a double-click app** (py2app macOS, PyInstaller Windows) +
  build workflow.
  *Why:* today the app only runs via `python3 brain_battle_main.py` inside
  a hand-built Python environment — not realistic for the target
  non-technical researcher/assistant audience.
  *Files:* new `setup.py`/PyInstaller spec files, a build script/CI config
  (none exist yet), `version.py` (build metadata).

- **Rewrite README for non-technical users.**
  *Why:* the current `README.md` is written for a developer audience (pip
  installs, architecture tables, module maps) — the target reader for the
  shipped product is a researcher who just wants to run it.
  *Files:* `README.md`.

---

## Deferred until hardware works

These cannot be closed by writing more code — they need a working camera
rig and Muse-S bands, which the project doesn't currently have.

- **First real 5-minute sparring recording; inspect raw EEG before further
  sync work.**
  *Why:* per "Project situation," nobody has inspected real Muse-S EEG from
  a sparring round — this gates whether the capture-clock investment is
  even measuring something usable, and whether Phase 5's accelerometer/PPG
  and quality-view items are worth building yet.
  *Files:* none (data collection); informs `eeg_module.py`/
  `export_module.py` decisions afterward.

- **Eyes-on-glass playback check; EEG clock-source verification
  (buzzer/tap test).**
  *Why:* `CLAUDE.md` deferred work items #0/#1 — logic-verified by unit
  tests but never watched on real data; the arithmetic is confirmed, the
  EEG's actual position on the shared axis is not.
  *Files:* none directly; validates `brain_battle_main.py`'s Review
  alignment path and `eeg_module.py`'s `EEGReviewWidget`.

- **NVIDIA LocateAnything auto-detection** (only after labels and EEG
  quality are proven).
  *Why:* speculative future automation (e.g. auto-detecting events from
  video) is not worth building before the manual labelling workflow and
  basic EEG-quality trust are established — building it earlier risks
  automating something nobody has confirmed is measurable yet.
  *Files:* none yet — a new, separate module whenever this is undertaken.
