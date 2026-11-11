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

- ~~**`requirements.txt` pinning current deps (no matplotlib).**~~ **DONE.**
  `requirements.txt` is derived from the repo's actual `import` statements
  (grepped, not copied from the old pip line) and pinned to the versions the
  `tests/` suite passed against; `matplotlib` stays excluded. `CLAUDE.md` and
  `README.md` now say `pip install -r requirements.txt`.
  *Files touched:* new `requirements.txt`; `README.md`/`CLAUDE.md` (pip line).

- ~~**Demo session generator**~~ **DONE.** `tools/generate_demo_session.py`
  builds a realistic fake v3 session folder: 4 videos with `.timestamps.csv`
  sidecars (one camera has a deliberate drift — 30fps declared container vs.
  ~29.5fps true capture rate — for Phase 5's drift-detection work), 2 EEG
  CSVs with a `t_session_seconds` column and plausible synthetic signal
  (noise, alpha, blink artifacts, round-phase motion scaling, a shared spike
  at every phase marker), a `phase_markers.csv`, and a `session_manifest.json`
  with a `capture_clock` origin. `--interrupted` produces a crash-looking
  session for Phase 2. Reuses the real writers wherever they're headlessly
  callable (`session_storage.SessionLayout`, `session_controller._build_timeline`,
  `USBCameraThread`'s sidecar methods, `_MuseDirectReader`'s chunk/flush path)
  so the fixture can't silently drift from the real format. Covered end to
  end by `tests/test_demo_session.py`. See `CLAUDE.md`'s "Demo session"
  section for usage.
  *Files touched:* new `tools/generate_demo_session.py`,
  `tests/test_demo_session.py`.

- **Decide later: merge `develop` → `main`** (owner's call; `main` is meant
  to be the verified, shippable baseline).
  *Why:* per `CLAUDE.md`'s branching convention, `develop` only merges to
  `main` when a version is verified and ready to ship — that has not
  happened for the capture-clock work yet (eyes-on-glass + EEG clock-source
  checks are still open), so this is explicitly not automatic.
  *Files:* none — a merge decision, not a code change.

---

## Phase 2 — Data safety

- ~~**EEG streamed to disk in chunks during recording (not RAM-only).**~~
  **DONE.** `_MuseDirectReader` writes the CSV header (fsync'd) on start and
  a background writer thread appends each chunk — line-buffered, flush >=1/s,
  fsync ~5s — while the BLE callback only does a non-blocking `queue.put`. A
  hard kill leaves a valid CSV with everything up to the last flush. Covered
  by `tests/test_eeg_streaming.py` (SIGKILL mid-recording).
  *Files touched:* `eeg_module.py`.

- ~~**Crash-safe video container (MKV or fragmented MP4).**~~ **DONE
  (Option A).** Each camera now records via its own ffmpeg subprocess writing a
  fragmented H.264 MP4 (fed raw frames over stdin), replacing cv2.VideoWriter
  mp4v. A fragment flushes ~every 0.5 s, so a hard kill loses at most the final
  fragment — the rest stays playable. A bounded queue + writer thread keep
  capture from ever blocking on the encoder (full queue → drop + counter); a
  dead ffmpeg stops only that camera (red slot) while the others keep recording.
  Sidecar rows == encoded frames exactly (`-fps_mode passthrough`, row written
  only after a successful stdin write). Covered by `tests/test_video_recording.py`.
  *Files touched:* `usb_camera.py` (new `FrameEncoder` + rewired
  `USBCameraThread`), `usb_camera_slot.py` (dropped-count + red on error),
  `live_session_tab.py` (record pre-flight), `tools/generate_demo_session.py`,
  `session_storage.py` (estimate rate). export_module needed no change —
  ffmpeg reads the fragmented inputs fine.

- ~~**Labels autosaved into the session folder on every change; reopen and
  continue labelling.**~~ **DONE.** User labels persist to
  `<session>/labels.json` (atomic temp+replace, schema_version, numeric
  seconds, nullable boxer/end fields for Phase 4) on every add/remove;
  opening a session restores them. Covered by `tests/test_labels_autosave.py`.
  *Files touched:* `brain_battle_main.py`.

- ~~**Crash-recovery prompt on next launch for interrupted sessions.**~~
  **DONE.** On launch the app scans the capture folder for sessions that
  never finished (phases recorded but no `SESSION_END`), flags each manifest
  `interrupted: true` (no fabricated `SESSION_END`) so it won't re-nag, and
  offers to open the most recent. Covered by `tests/test_crash_recovery.py`.
  *Files touched:* `session_storage.py`, `brain_battle_main.py`.

- ~~**Disk-space check + estimated session size before recording.**~~ **DONE.**
  `estimate_session_bytes` derives size from configured duration x per-source
  rate (rates measured from the demo files); the setup dialog shows the live
  estimate, and `_on_start_clicked` blocks below 1x free space and warns below
  2x. Covered by `tests/test_crash_recovery.py`.
  *Files touched:* `session_storage.py`, `session_setup_dialog.py`,
  `live_session_tab.py`.

---

## Phase 3 — Structure and design — **DONE (2026-11-06)**

> **`docs/DESIGN.md` (approved 2026-10-27) is the source of truth for ALL UI
> work in Phases 3–6** — screens, copy, vocabulary, tokens, layout. If code
> and DESIGN.md disagree, ask; don't guess. The items below are the build
> breakdown; DESIGN.md is what they must look and read like. Playback approach
> for the merged Review & Label screen: **Option C (PyAV, app-driven decode)**,
> recommended in `docs/PLAYBACK_PROPOSAL.md` — the only option correct on the
> drifting camera, and it collapses today's two playback stacks into one.

**All of Phase 3 is built and tested against the generated demo session +
simulated devices** (hardware is still broken — see `CLAUDE.md`'s "Project
situation"). Step 7 (this step) shipped the real Settings screen
(`settings_screen.py`), which was the last placeholder screen — the Phase-3
owner rule ("zero placeholder/temporary UI copy, zero placeholder screens")
is now met with zero exceptions (`tests/test_no_placeholders.py`). See
"DESIGN differences" below for the handful of places the build intentionally
reads slightly differently than DESIGN.md's mockup text, and why.

- ~~**Sessions home screen**~~ **DONE.** `sessions_screen.py` — a table (DESIGN
  §1's actual layout, not the cards this bullet originally sketched): date,
  boxers, length, labels, status badge, search, status filter chips + counts,
  sort, the interrupted banner, "Show in Finder", one-click open.

- ~~**Record as a guided flow**~~ **DONE.** `record_flow.py` (DESIGN §2-§5):
  Connect devices → Set up session → Record → Saved, replacing the old Live
  Monitor entirely (deleted, along with `session_setup_dialog.py` and the old
  camera grid).

- ~~**Merge Review and Labelling into one "Review & Label" screen**~~ **DONE.**
  `review_label_screen.py` + `pyav_player.py` — one PyAV-driven screen on a single
  `t_session_seconds` playhead (Option C from `docs/PLAYBACK_PROPOSAL.md`); the
  old `SyncReviewTab`/`LabellingTab`/`vlc_module`/audio-xcorr (`sync_engine`'s
  dead half) are gone.

- ~~**Fix the Review layout not fitting a laptop screen**~~ **DONE** — folded
  into the merged screen above; `tests/test_layout_fit.py` holds every screen
  (including Settings, this step) to 1440×900 / 1280×800 / the app's minimum
  window with nothing clipped.

- ~~**Settings screen**~~ **DONE (Phase 3 step 7).** `settings_screen.py`
  (DESIGN §7): recordings folder (name/path/free space, Change folder…/Show in
  Finder, a `BRAIN_BATTLE_CAPTURE_DIR`-override note), headbands (known-device
  dropdowns + BLE search, a same-headband guard), video quality (Standard/
  High/Light — resolution AND bitrate now wired into the recorder, with the
  "GB per hour" copy and the disk-space estimate both COMPUTED from that
  bitrate, not hard-coded), session defaults + round bell + Test sound, and
  the shared label list (add/rename/remove/reorder/recolour, `label_list.json`
  in the recordings folder). Everything autosaves with a brief "✓ Saved"
  confirmation. Replaces the old hard-coded `eeg_module.BOXER_LABELS`,
  `usb_camera.py`'s fixed resolution/bitrate, and the Settings placeholder
  (the last one — `tests/test_no_placeholders.py` now allows zero exceptions).

- ~~**Light-mode-only design system**~~ **DONE (Phase 3 step 2).**
  `design_tokens` is rebuilt light-only from DESIGN.md; the dark palette, theme
  toggle, `set/current/on_theme_change`, prefs file, and every `_apply_theme`
  hex-replacement map are gone (guarded by `tests/test_light_theme.py`). Inter
  is bundled (`assets/fonts/`, `fonts.register_fonts`). The existing tabs were
  light-converted so they stay readable. **Also DONE in step 2: the new shell
  scaffold** — `sidebar_shell` rebuilt to DESIGN "App structure" (232px
  sidebar + folder card with real free space, collapses to 72px on
  Review & Label, opens on Sessions), with the existing screens wired behind
  it (Sessions/Settings placeholders; New recording = Live Monitor;
  Review & Label = the Review screen + a temporary link to the old Labelling
  screen). `tools/render_screens.py` renders every page at 1440×900 / 1280×800
  / the app's minimum window (all three sizes now, as of step 7).
  **All since DONE** — the Sessions home list, the guided Record flow, the real
  merged Review & Label screen, and (step 7) the real Settings screen are all
  built; nothing in this phase is still a placeholder.

### New scope from `docs/DESIGN.md` (approved 2026-10-27) — **ALL DONE**

These came out of the approved mockups and were build requirements for the
screens above; see DESIGN.md for the exact copy/layout.

- ~~**Boxer names + manifest-based session discovery, and recording-folder
  naming `YYYY-MM-DD HH-MM <A name> vs <B name>`.**~~ **DONE** —
  `session_storage._session_folder_name` + manifest boxer fields;
  `record_flow.py`'s Set up step.
  *Why:* the Sessions list, every screen title, and the folder name show the
  boxers' names (DESIGN §1, §3–6). Names are entered at setup and stored in
  the manifest; the folder name includes them (filesystem-sanitized).
  Sessions must be **discovered by their manifest, not by a `"Brain Battle "`
  folder prefix**, since the prefix is changing — update the Phase-2 crash
  scanner accordingly.
  *Files:* `session_storage.py` (`_session_folder_name`, name sanitization,
  manifest boxer fields, `scan_interrupted_sessions` prefix check),
  `session_setup_dialog.py` / the new setup screen.

- ~~**Headband battery + per-sensor contact quality**~~ **DONE** — battery %
  + Good/Weak-contact chips with plain-language fix tips on Connect and
  Recording (`headband_source.py`, `contact_quality.py`, `record_flow.py`'s
  `HeadbandView`).

- ~~**"Mark this moment" during recording**~~ **DONE** — `record_flow.py`'s
  `mark_moment`/`RecordingStep.show_moment`; surfaced as unnamed labels to
  name in `review_label_screen.py`.

- ~~**Sessions status model**~~ **DONE** — `session_storage.session_info`
  (Interrupted / Needs labelling / Labelled / Exported) drives `sessions_screen.py`'s
  status badges + filter chips.

- ~~**Labels gain `who` = A | B | Both and point/duration types**~~ **DONE**
  — `labels_store.py` v2 schema; the who-picker + duration placement in
  `review_label_screen.py` (DESIGN §6).

### DESIGN differences (intentional, as of Phase 3 step 7)

Everywhere else the app matches DESIGN.md's screens/copy/vocabulary. These are
the handful of places the build deliberately reads differently, and why:

- **Sessions is a table, not cards.** DESIGN §1's actual column/row layout
  (Recorded/Boxers/Length/Labels/Status/actions) is a table; "cards" in this
  ROADMAP's original Phase-3 sketch predates DESIGN.md and was superseded.
- **Settings' label-list rows have ↑/↓ reorder buttons**, which DESIGN §7's
  row spec (key/swatch/name/kind/remove) doesn't list. Needed because the
  order of the list IS the 1-9 number-key mapping (DESIGN §6) — without a way
  to reorder, the key assignment would be fixed at creation order forever.
- **Settings' "Saved" confirmation is inline text next to the title** (a
  small "✓ Saved" that fades after ~1.5 s), not a toast/snackbar — DESIGN says
  only "a brief, plain confirmation," not a specific widget; inline text
  needed no new chrome and reads as part of the page.
- **`BRAIN_BATTLE_CAPTURE_DIR` now always overrides a previously-saved custom
  recordings folder**, not just the undecided-default case. DESIGN §7 says
  the env override should "disable Change" and show it as "set outside the
  app" — for that note to stay true after a user has already picked a folder
  from Settings once, the env var has to keep winning, so
  `AppSettings.capture_dir_resolved()` checks it first unconditionally.
- **Settings' headband "same device" conflict is caught on assignment**, not
  prevented from being selectable — DESIGN only states the constraint, not
  the interaction; rejecting the change with an inline message (rather than
  filtering the dropdown's contents) keeps both boxers' lists showing every
  known device.

---

## Phase 4 — Labelling — **DELIVERED (as part of the Phase 3 Review & Label build)**

> **All five items below shipped inside the Phase 3 Review & Label merge, not
> as a separate phase.** Where an item's *Files:* line names
> `brain_battle_main.py` (`LabelMarker`/`_place_label`/`get_labels`/
> `SyncPlaybackController`/`TimelineWithMarkers`), `vlc_module.py`,
> `labels_config.py` or `labels_data.json`, those are the now-removed
> predecessors — the delivered work lives in **`review_label_screen.py`**
> (transport: frame step, 0.25×/0.5×, undo/redo, editable placed-labels list),
> **`labels_store.py`** (`labels.json` v2: `who` A/B/Both, `kind`
> point/duration, numeric `t_start`/`t_end`, undo/redo) and **`label_vocab.py`**
> (one shared `label_list.json` stored in the recordings folder, not committed
> to the repo). The edit surface is the **Settings** label-list editor. Guarded
> by `tests/test_labels_v2.py`, `tests/test_label_vocab.py`, and
> `tests/test_review_tab.py`.

- **Every label tied to a boxer (A/B).** **DONE** — `labels_store` labels carry
  `who ∈ {A, B, Both}`; the CSV export includes it.
  *Why:* `LabelMarker`/`_place_label`/`get_labels()` carry no boxer field
  today — any between-boxer analysis needs to know whose event it is.
  *Files:* `brain_battle_main.py` (`LabelMarker`, `_place_label`,
  `get_labels`), `export_module.py` (`LabelsExporter`'s CSV columns).

- **Point events and duration events (start/end).** **DONE** — `kind` is
  `point` (t_end null) or `duration` (start + end); Clinch & Referee stop are
  duration by default, placed by pressing the number to start and again to end.
  *Why:* labels are points-only today (`timestamp_sec` with no end); a
  punch is naturally a point but a clinch, knockdown, or stoppage is
  naturally a duration — forcing everything into a point loses information.
  *Files:* `brain_battle_main.py` (`LabelMarker`, `_place_label`,
  `TimelineWithMarkers`/`_MarkerStrip` rendering for a duration chip),
  `export_module.py` (`LabelsExporter` CSV schema).

- **One shared label vocabulary per project stored with sessions, not next
  to the code; remove committed `labels_data.json`.** **DONE** — `label_vocab`
  stores one `label_list.json` in the recordings folder; `labels_config.py`
  and the committed `labels_data.json` were deleted.
  *Why:* `labels_data.json` is currently committed to the repo and silently
  overrides `labels_config.py`'s defaults for every machine that checks the
  repo out — one developer's custom palette (`MOVEMNET_WITH_CONTACT` [sic],
  `PUNCH_LEFT`, `PUNCH_RIGHT`, `BLOCK`) becomes everyone's default.
  *Files:* `labels_config.py` (relocate storage to project/session data,
  not the source tree), delete `labels_data.json` from the repo, the
  Settings screen (Phase 3) as the new edit surface.

- **Frame stepping, 0.25×/0.5× playback, undo/redo, editable list of
  placed labels.** **DONE** — all in `review_label_screen.py`: ← → step one
  frame, a 0.25×/0.5×/1× speed control, ⌘Z/⇧⌘Z undo/redo, and a "Placed
  labels" list (click a row to jump to it, ✕ to delete).
  *Why:* precise event placement (e.g. the exact frame of first contact)
  needs finer transport control than today's PLAY/PAUSE-only controls and
  the Space-bar toggle offer.
  *Files:* `brain_battle_main.py` (`LabellingTab` transport controls,
  `SyncPlaybackController`), `vlc_module.py`.

- **Labels stored as numbers on the `t_session_seconds` axis** (no
  HH:MM:SS string round-trip). **DONE** — `labels_store` keeps `t_start`/
  `t_end` as rounded floats on the `t_session_seconds` axis; no string
  round-trip.
  *Why:* `_place_label`/`get_labels`/`_rebuild_timeline_markers` currently
  format timestamps to an `HH:MM:SS.mmm` string and parse them back by
  splitting on `:` — a lossy, fragile round-trip for no benefit once labels
  live on the same numeric axis as everything else.
  *Files:* `brain_battle_main.py` (`LabelMarker`, `_place_label`,
  `get_labels`, `_rebuild_timeline_markers`).

---

## Phase 5 — Remaining report items and research value

- **[RAISED PRIORITY] Use the FULL per-frame sidecar for playback timing**
  (not just the first row); detect and flag capture-rate drift.
  *Why:* `_read_frame_sidecar_start` (`brain_battle_main.py`) reads only the
  sidecar's first row today — any mid-session frame-rate drift (which
  `usb_camera.py` already logs a warning for, but never surfaces
  structurally) is invisible to alignment. **Confirmed eyes-on-glass on the
  demo session (2026-10-21):** the deliberately-drifting camera visibly
  desyncs — at the same displayed frame 283, POV 01 reads
  `t_session 9.653` while POV 02 reads `9.487` (~166 ms apart and growing).
  So this is a real, reproduced alignment defect, not a theoretical one —
  worth doing earlier in Phase 5 than the items below it, or pulling forward
  into the Phase 3 Review & Label merge where the playback path is already
  being reworked.
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

- ~~**Delete dormant `sync_engine.py` code (move `SourceOffset`/`SyncResult`
  out).**~~ **DONE** (`c21fac0`, 2026-11-01 — "Merged Review & Label screen
  (DESIGN §6); delete old stacks"). The whole module was removed when the merged
  Review & Label screen replaced the old Sync/Labelling tabs — the dead
  `compute_sync`/`SyncWorker`/`_xcorr_offset`/`get_eeg_window` and the
  `SourceOffset`/`SyncResult` dataclasses are all gone, so there was nothing left
  to move. `CLAUDE.md`'s deferred work #4 is closed accordingly.

- ~~**Sync beep becomes a round bell only** (fix docstring; toggle in
  Settings).~~ **DONE.** The round-bell on/off toggle shipped in the Phase 3
  Settings screen (DESIGN §7; `settings_screen.py`, wired through `record_flow.py` —
  the old `live_session_tab.py` wiring is gone), and `sync_beep.py`'s docstring
  was rewritten to describe a round bell for the boxers with no role in
  timing/alignment (the audio-xcorr `sync_engine` it once referenced is removed,
  per the item above).

- **Extend tests to the new flows.**
  *Why:* `tests/` currently covers the capture-clock + ring-buffer + smoke
  floor only; none of Phases 2-5's new flows (autosave, drift detection,
  duration labels, quality view, the demo-session generator itself) have
  any coverage yet.
  *Files:* new `tests/test_*.py` files per flow; `tests/test_smoke.py`
  (module-discovery will need to pick up every new module).

---

## Phase 6 — Shipping

- **Ship `assets/` and `docs/` as proper package data so a non-editable
  install works.** The [package reorganisation](decisions/adr-0013-package-reorganisation.md)
  made `pip install -e .` work and centralised repo-relative lookups in
  `brain_battle/paths.py` (`REPO_ROOT`, `ASSETS_DIR`, `DOCS_DIR`), but those
  still resolve to the checkout — a non-editable `pip install .` would not find
  the bundled fonts, round-bell WAV, or `docs/` tree. Declare them as package
  data (or `importlib.resources`) and switch `paths.py` to resolve them from the
  installed location. *Files:* `pyproject.toml` (package-data / `MANIFEST.in`),
  `brain_battle/paths.py`.
- **Package as a double-click app** (py2app macOS, PyInstaller Windows) +
  build workflow. **Must bundle ffmpeg** inside the app (the camera recorder
  shells out to it per camera for crash-safe H.264, and the pre-flight blocks
  recording without it) — a non-technical user won't `brew install ffmpeg`, so
  ship the binary in the bundle and point `usb_camera`/`export_module` at the
  bundled path. Review playback no longer needs VLC — PyAV (`av`) bundles its
  own ffmpeg libraries and ships in the pip wheel, so there is nothing extra to
  bundle for playback.
  **`tools/`, `tests/`, and `simulation.py` are NOT shipped in the packaged
  app** — they are developer-only (the demo generator, headless test scripts,
  and the `BRAIN_BATTLE_SIMULATE` simulated-camera/headband backend added in
  the Phase-3 guided-recording step) and must be excluded from the
  py2app/PyInstaller bundle. With the flag unset there is no trace of
  simulation in the UI, so excluding the module is safe.
  *Why:* today the app only runs via `python3 -m brain_battle` (or
  `pip install -e .`) inside a hand-built Python environment — not realistic for
  the target non-technical researcher/assistant audience.
  *Files:* a PyInstaller spec / py2app config building on the existing
  `pyproject.toml`, a build script/CI config (none exist yet),
  `brain_battle/version.py` (build metadata); ffmpeg-path resolution in
  `capture/usb_camera.py`/`data/export_module.py` to prefer the bundled binary;
  bundle include/exclude lists that drop `tools/` and `tests/`.
  **Also bundle `docs/`** — the in-app Documentation tab (`docs_screen.py` +
  `docs_nav.py`, added alongside this item) reads `docs/nav.yml` and every
  page/diagram/screenshot it lists straight from the repo's `docs/` folder at
  runtime; a packaged build needs that folder included (content only —
  `docs/contributing/` and the raw `.mmd` diagram sources aren't referenced by
  any page and don't need to ship, just `nav.yml` + the Markdown + the
  rendered `.png`/`.svg` the pages actually link to).
  **Also bundle `assets/sounds/`** — `capture/round_bell.py` plays
  `assets/sounds/round_bell.wav` at runtime (see
  [credits](project/credits.md)); only the processed WAV needs to ship, not
  `assets/sounds/source/` or `tools/make_round_bell.py` (developer-only, like
  the rest of `tools/`).

- **Rewrite README for non-technical users.**
  *Why:* the current `README.md` is written for a developer audience (pip
  installs, architecture tables, module maps) — the target reader for the
  shipped product is a researcher who just wants to run it.
  *Files:* `README.md`.

---

## Phase 7 — Team & cloud (future, not scheduled)

The owner's longer-term goals. Not scheduled; captured here so earlier phases
don't paint us into a corner. **Constraint for ALL earlier phases: keep each
recording folder fully self-contained** (video + sidecars + EEG + markers +
manifest + `labels.json` + `history.jsonl` + any moments/questionnaire data all
inside the one dated folder) so a recording can later be uploaded or synced as
a single unit without hunting for pieces elsewhere.

- **Shared storage across every copy of the app.**
  *Why:* the real workflow is split across people and machines — one team
  records, others label, others analyse — so local-only storage can't work;
  everyone needs the same sessions, labels, and history. Planned split: **small
  data** (accounts, labels, history, questionnaires, analyst notes) in a
  **hosted database with built-in sign-in**; **video** (1–2 GB/session) in
  separate **file/object storage**. **BLOCKER before choosing any provider:**
  check with the university's data protection officer / ethics approval —
  boxers' names + brain data + questionnaire answers are **personal data under
  GDPR**, so EU or university-provided hosting may be required. Don't commit to
  a vendor before that clears.

- **Simple accounts for everyone who gets the app; roles Recorder, Labeller,
  Analyst, Admin.**
  *Why:* shared storage needs identity (who did what, who may do what), and the
  roles map directly to the workflow. Also lets `history.jsonl`'s `user_source:
  "os"` placeholder become a real account id.

- **Per-recording history shared across the team** (who recorded, who labelled,
  who opened for analysis, who exported).
  *Why:* with many people touching a session, "who changed what, when" has to
  be visible to everyone, not just on the one laptop. Builds directly on the
  local append-only `history.jsonl` from Phase-3 Part B (sync those events up).

- **Pre-round and post-round questionnaires for both boxers inside the app**
  (today these are Google Forms).
  *Why:* the questionnaire answers are part of the synchrony dataset and should
  attach to the right recording/round automatically instead of living in a
  separate Google Sheet to be matched up by hand. *Idea to evaluate:* a short
  phone form opened via a **QR code on the recording screen**, answers attached
  to the recording and round automatically.

- **Analysis mode: a READ-ONLY mode of the Review & Label screen** (same
  player/timeline; label controls hidden, existing labels locked), plus:
  per-analyst **notes/bookmarks pinned to moments** (a separate layer that
  never edits labels), **questionnaire answers shown on the timeline** per
  boxer per round, simple **per-round summaries** (e.g. punch counts per
  boxer), and **export for R/Python**.
  *Why:* analysts need to explore a frozen, labelled session and add their own
  interpretation without any risk of changing the labelling team's work.

- **"Labelling finished" step that freezes labels before analysis; per-labeller
  label sets so two labellers never overwrite each other** (also enables
  inter-rater agreement checks).
  *Why:* analysis must run on a stable label set, and two people labelling the
  same session must not clobber each other — separate per-labeller sets both
  prevent that and make agreement measurable.

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
