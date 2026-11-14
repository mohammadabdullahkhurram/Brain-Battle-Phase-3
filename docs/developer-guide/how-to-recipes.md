# How-to recipes

**What this page covers:** step-by-step recipes for the changes developers make
most often, each pointing at the exact modules, tests, and docs to touch. The
recurring theme: a change to a format, module, setting, or screen is not done
until its **docs and tests** are updated too — the honesty gates will fail
otherwise, on purpose.

## Add a field to a recording/export file

1. Add the field where the file is written — e.g. a manifest key in
   `session_storage.SessionLayout._write_manifest`, a CSV column in
   `usb_camera.FrameEncoder._write_sidecar_row`, `contact_log._HEADER`, an EEG
   column in `eeg_module`, a `labels_store` field, or an export entry in
   `export_module`.
2. Document it in
   [`research-and-data/file-formats.md`](../research-and-data/file-formats.md) —
   wrap the new field/column/key name in `` `backticks` ``.
3. Run `tests/test_research_docs.py`. It generates a **real** recording and
   export and fails unless every field the code writes is documented, backticked,
   on that page. **This is why the doc update isn't optional** — add a field
   without documenting it and the test goes red.
4. Add a [CHANGELOG](../project/version-history.md) entry.

## Add a setting

1. Add it to the store in `app_settings.py` (a default, an accessor, and
   round-trip through `to_json`/`load`).
2. Add a control to the right card in `settings_screen.py`; autosave it with the
   "✓ Saved" flash like the others.
3. Read it where it takes effect (e.g. `record_flow`, the recorder) — **read the
   setting, don't hardcode**.
4. Update `tests/test_app_settings.py` (store round-trip) and
   `tests/test_settings_screen.py` (the control + an end-to-end that a change
   reaches the next recording).
5. If it changes the UI wording, update [Settings](../user-guide/settings.md).

## Add a label kind (point vs duration) or vocabulary entry

- The **vocabulary** (the team's label list) is data, not code: it lives in
  `label_list.json` in the recordings folder and is edited in Settings
  (`settings_screen.py` → `label_vocab`). Adding a label the team uses is a Settings
  action, not a code change.
- A genuinely new **kind** (beyond `point`/`duration`) is a schema change in
  `labels_store.py`: add the constant, handle it in `new_label`, the placement
  logic in `review_label_screen._handle_duration`/`_commit_label`, the timeline
  rendering (`TimelineStrip`), and the export (`export_module._write_events_csv`).
  Update `tests/test_labels_v2.py`, the labels-file section of
  [file-formats.md](../research-and-data/file-formats.md), and — if it's a
  significant model change — write an ADR.

## Add a screen

1. Build the screen as a `QWidget` using `design_tokens` factories (no hex
   literals); emit a `help_requested` signal if it has a `?` button.
2. Register it in `brain_battle_main.BrainBattleApp._build` via
   `SidebarShell.add_page(...)` with its collapse/hide behaviour, and wire its
   signals.
3. If it has a Help button, route it in `brain_battle_main.open_help` to a docs
   page.
4. Make it fit: `tests/test_layout_fit.py` must pass at all three sizes.
5. Add it to `tests/test_smoke.py`'s construction floor.
6. Document it: a [User guide](../user-guide/sessions.md) page if it's user
   facing, and an entry in the [module reference](module-reference.md) (the
   developer-docs honesty test requires every module to be documented).

## Add a docs page or diagram

1. Write the `.md` under the right section folder; start with `# Title` and a
   **"What this page covers"** line.
2. For a diagram, write `docs/diagrams/<name>.mmd` (declare a `fontSize`, keep it
   narrow), then `python3 tools/render_diagrams.py`, and reference the **`.png`**.
3. Add the page to `docs/nav.yml` under the right section — only listed pages are
   shown in the app, and `tests/test_developer_docs.py` requires every
   non-internal page to be listed.
4. If the page is searchable, add a known phrase to `_KNOWN_PHRASES` in
   `tests/test_docs.py` (it asserts every nav page has one).
5. Run `tests/test_docs.py` (renders, links/images/diagrams resolve, search,
   layout fit). See the [docs guide](../contributing/docs-guide.md#checklist-for-a-new-page).

## Change the video quality levels

1. The levels are resolution + bitrate in `app_settings.py` (`video_dims`,
   `video_mbps`); the Settings card is `settings_screen._build_quality_card`.
2. The GB/hour copy and the disk estimate are **computed from the bitrate**
   (`app_settings.gb_per_hour`, `session_storage.estimate_session_bytes`) — don't
   hardcode a GB number.
3. The recorder reads the resolution/bitrate when a recording starts
   (`usb_camera`/`camera_source`).
4. Update `tests/test_settings_screen.py` (it asserts the GB/hour and disk numbers
   are derived, not hard-coded) and the [Settings](../user-guide/settings.md) doc
   wording.

## Remove or rename a module

1. Make the change and update every importer. Run `tests/test_smoke.py` (the
   deletion/refactor gate) before and after.
2. Update the [module reference](module-reference.md): add/rename the entry, or
   remove it. `tests/test_developer_docs.py` fails if a root `.py` file has no
   entry, **or** if an entry names a file that no longer exists — so a stale entry
   is caught too.
3. Update `AGENTS.md`'s module map and add a CHANGELOG entry.
