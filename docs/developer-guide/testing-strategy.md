# Testing strategy

**What this page covers:** how the test suite is organised, how to run one test
or all of them (and which one is slow), the acceptance gates that must stay
green, and how to add a test.

## How the suite works

Tests are **plain scripts**, not a pytest project: each
`tests/test_<name>.py` runs standalone, prints `PASS`/`FAIL` per check, and exits
nonzero on any failure. They run headless (`QT_QPA_PLATFORM=offscreen`) and most
drive the **real** code paths under `BRAIN_BATTLE_SIMULATE=1` (simulated devices)
or against a **generated demo session** — because the hardware is broken (see
[CLAUDE.md's Project situation](../project/status.md)). All of them need `ffmpeg`
on `PATH`; playback/decoding is PyAV (`av`), not VLC.

### Run one, or all

```bash
python3 tests/test_smoke.py
```

```bash
for t in tests/test_*.py; do python3 "$t" || echo "FAILED: $t"; done
```

`tests/test_flash_sync.py` is **slow** — it generates and verifies a 12-minute
demo session — so a full run takes a few minutes; everything else is quick. The
`tests/_*_runner.py` files are helper subprocesses launched by the kill tests
(`test_eeg_streaming`, `test_video_recording`, `test_crash_recovery`,
`test_history_log`), not standalone tests.

## Inventory

| Test | Guards | Speed |
|---|---|---|
| `test_smoke.py` | Import/construct floor; the deletion/refactor gate | fast |
| `test_player.py` | PyAV player: frame-for-t, pacing, scrub coalescing | fast |
| `test_review_tab.py` | Review & Label labels side: fast open, duration, undo/redo | fast |
| `test_labels_v2.py` | `labels_store` v2 schema + v1→v2 migration | fast |
| `test_label_vocab.py` | Shared `label_list.json` | fast |
| `test_contact_quality.py` | Per-sensor contact heuristic | fast |
| `test_export.py` | Analysis-bundle contents + ordering | moderate |
| `test_flash_sync.py` | Flash within one frame across tiles | **slow** |
| `test_no_placeholders.py` | No placeholder/jargon UI copy (gate) | fast |
| `test_capture_clock.py` | Capture-clock arithmetic + co-registration | fast |
| `test_demo_session.py` | The demo generator end to end | moderate |
| `test_sessions.py` | Sessions discovery/status/search/sort/banner | moderate |
| `test_ring_buffer.py` | Ring-buffer ordering | fast |
| `test_eeg_streaming.py` | Streamed EEG recorder under SIGKILL | moderate |
| `test_video_recording.py` | Crash-safe H.264 recording | moderate |
| `test_crash_recovery.py` | Interrupted detection + disk branches | moderate |
| `test_history_log.py` | Append-only history log + hooks | moderate |
| `test_light_theme.py` | Light-only guard (gate) | fast |
| `test_layout_fit.py` | Nothing clipped at three sizes (gate) | moderate |
| `test_record_flow.py` | The guided flow end to end (simulated) | moderate |
| `test_app_settings.py` | Settings store + `BOXER_LABELS` removal | fast |
| `test_settings_screen.py` | Real Settings screen, computed bitrate, end-to-end | moderate |
| `test_contact_log.py` | Contact-log weak-period spans | fast |
| `test_docs.py` | Documentation tab + docs content honesty (gate) | moderate |
| `test_research_docs.py` | Research & data format reference matches code (gate) | moderate |
| `test_developer_docs.py` | Developer guide + ADR + Project honesty (gate) | fast |

## The acceptance gates

A few tests are **gates** — they encode a rule the project refuses to regress on.
Run them before and after any change in their area:

- **`test_smoke.py`** — the "loads and stands up" floor: fresh-subprocess import
  of every first-party module, headless construction of every screen + the app
  shell, `__version__` in the window title. **Run before and after every
  deletion/refactor.**
- **`test_no_placeholders.py`** — zero placeholder/temporary UI copy, no jargon
  or EEG channel codes in UI strings, zero placeholder screens.
- **`test_layout_fit.py`** — every page fits all three window sizes with nothing
  clipped.
- **The docs honesty gates** — `test_docs.py` (links/images/diagrams resolve,
  search works, nothing clipped), `test_research_docs.py` (the file-format
  reference matches what the code writes), and `test_developer_docs.py` (every
  module has a reference entry and vice-versa; every ADR is indexed and in
  `nav.yml`; every non-internal page is in `nav.yml`; no "coming in a later
  documentation step" text remains; the CHANGELOG's top entry matches
  `version.py`). These are what keep the docs from drifting from the code — see
  [CLAUDE.md's "Documentation is part of every change"](../contributing/docs-guide.md#documentation-is-part-of-every-change-owner-rule).

## How to add a test

1. Copy the header of an existing test (the `QT_QPA_PLATFORM`/`BRAIN_BATTLE_*`
   setup and the `check(name, cond, detail)` helper).
2. Prefer driving a **pure-logic** function (Qt-free — like
   `session_controller`, `labels_store`, `docs_nav`) so the test needs no event
   loop; where a widget is unavoidable, construct it offscreen.
3. For anything involving a recording, generate a demo session with
   `tools/generate_demo_session.generate_demo_session(...)` rather than inventing
   a fixture by hand — it reuses the real writers, so it can't drift.
4. Exit nonzero on failure (the `sys.exit(1 if _failures else 0)` pattern).
5. If you add a new module, `test_smoke.py`'s module discovery and
   `test_developer_docs.py`'s module-reference check will both require it to be
   wired in and documented — that's intentional.
