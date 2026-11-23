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
[AGENTS.md's Project situation](../project/status.md)). All of them need `ffmpeg`
on `PATH`; playback/decoding is PyAV (`av`), not VLC.

### Run one, or all

```bash
python3 tests/test_smoke.py
```

```bash
python3 tools/run_tests.py              # everything, one PASS/FAIL line per script
python3 tools/run_tests.py xdf player   # only scripts whose name contains a word
```

`tools/run_tests.py` runs several scripts at once (about five and a half
minutes for everything, instead of eleven one after another). Scripts that
measure real time or publish LSL streams under fixed names are kept in a single
serial lane, and a script that fails while others are running is re-run on its
own before it counts as failed — so a busy laptop doesn't produce false alarms.
The two scripts that measure millisecond timing under a real-time load —
`test_timing` (buzzer calibration within 2 ms) and `test_stability` (the
short soak) — are listed in `ALONE` and run **after** everything else, one at
a time with nothing beside them, also in CI. Both first measure **how late
this computer wakes a thread** sleeping to 60 Hz deadlines (`MACHINE:` line,
`tests/_env.py` `timing_judgeable`). If more than 10 % of wake-ups come over
half a frame late, frames are lost and stamps drift whatever the app does, so
their millisecond checks (delays recovered within 2 ms, under 2 % of frames
lost, the other cameras not disturbed) print `NOT JUDGED` with the
measurement instead of failing; every other check still counts. GitHub's
macOS runner is such a computer (a median 16 ms late, about 71 % of wake-ups
over half a frame); a desk Mac and the Windows runner are not (~0 %), so
there the checks are judged as before (owner's decision, 2026-11-20).
Full output for every script is kept in a log folder printed at the end.

`tests/test_flash_sync.py` is **slow** — it generates and verifies a 12-minute
demo session, about five minutes on its own; everything else is quick. The
`tests/_*_runner.py` files are helper subprocesses launched by the kill tests
(`test_eeg_streaming`, `test_video_recording`, `test_crash_recovery`,
`test_history_log`), not standalone tests.

## Inventory

| Test | Guards | Speed |
|---|---|---|
| `test_smoke.py` | Import/construct floor; the deletion/refactor gate | fast |
| `test_player.py` | PyAV player: frame-for-t, pacing, scrub coalescing | fast |
| `test_review_tab.py` | Review & Label labels side: fast open, duration, undo/redo | fast |
| `test_labels_v2.py` | Label entry schema + v1 migration | fast |
| `test_labellers.py` | One file per labeller, migration + claiming, merge-on-save, agreement, Compare | moderate |
| `test_fighters.py` | Codes + colours validated; the shared colour list; manifest / folder / XDF carry codes; old recordings with names still open | moderate |
| `test_platform.py` | Mac/Windows differences; forbids `%-d` and hard-coded "Finder" (gate) | fast |
| `test_shared_folder.py` | Atomic writes, the lock, several processes at once, a folder that disappears | moderate |
| `test_descriptors.py` | Open files: several recordings in a row under a 256 limit return to baseline; failed writes are shown | moderate |
| `test_label_vocab.py` | Shared `label_list.json` | fast |
| `test_contact_quality.py` | Per-sensor contact heuristic | fast |
| `test_export.py` | Analysis-bundle contents + ordering | moderate |
| `test_flash_sync.py` | Flash within one frame across tiles | **slow** |
| `test_no_placeholders.py` | No placeholder/jargon UI copy (gate) | fast |
| `test_capture_clock.py` | Capture-clock arithmetic + co-registration | fast |
| `test_xdf.py` | XDF writer + `recording.xdf` read back with pyxdf; the one capture clock | fast |
| `test_xdf_csv_parity.py` | Every stream in an export's `recording.xdf` has its CSV (same samples, values, times minus the correction); `moments.csv` + `timing_quality.csv` exported | moderate |
| `test_stability.py` | Camera watchdog (stall → reopened → same file, reported), repeated headband drops, contact-log failures through `RecordFlow._essential`, and a short soak with simulated devices; the full soak is `tools/soak_test.py` | slow |
| `test_timing.py` | Rate / jitter / dropped monitors, the per-band de-jitter fit, camera frame stamps on the steady frame clock (`FrameClock`, simulated busy reader, drifting camera), tap detectors, calibration on synthetic data (40 runs) and end to end with simulated devices that inject known delays + jitter (recovered within 2 ms) | moderate |
| `test_moments.py` | Moments as {t, note} in the manifest and `moments.csv`; notes added later; "Needs labels" until every moment has a note; XDF `BrainBattle_Moments` | moderate |
| `test_lsl_streams.py` | Live LSL outlets as a real LSL receiver sees them | moderate |
| `test_eeg_viewer.py` | EEG viewer: window, honest µV scale, filter, markers, controls | fast |
| `test_demo_session.py` | The demo generator end to end | moderate |
| `test_sessions.py` | Sessions discovery/status/search/sort/banner | moderate |
| `test_ring_buffer.py` | Ring-buffer ordering | fast |
| `test_eeg_streaming.py` | Streamed EEG recorder under SIGKILL | moderate |
| `test_video_recording.py` | Crash-safe H.264 recording | moderate |
| `test_crash_recovery.py` | Interrupted detection + disk branches | moderate |
| `test_history_log.py` | Append-only history log + hooks | moderate |
| `test_light_theme.py` | Light-only guard (gate) | fast |
| `test_dialogs.py` | No native alert / message box / colour dialog / system dropdown; the app's `AppDialog` and `Dropdown`; text fields, the custom note box and the history drawer light even when the computer is in dark mode (gate) | fast |
| `test_layout_fit.py` | Nothing clipped at three sizes, including squeezed wrapped text, the Sessions rows (and with the history drawer open), the simulated camera text kept out of the label area, neutral Settings badges (gate) | moderate |
| `test_record_flow.py` | The guided flow end to end (simulated), including the in-app questions (stop, leave, low disk) and choosing the camera / headband for each slot (swap, remembered) | moderate |
| `test_app_settings.py` | Settings store + `BOXER_LABELS` removal | fast |
| `test_settings_screen.py` | Real Settings screen, computed bitrate, end-to-end | moderate |
| `test_contact_log.py` | Contact-log weak-period spans | fast |
| `test_docs.py` | Documentation tab + docs content honesty (gate) | moderate |
| `test_research_docs.py` | Research & data format reference matches code (gate) | moderate |
| `test_developer_docs.py` | Developer guide + ADR + Project honesty (gate) | fast |

Every script starts with `import _env` (`tests/_env.py`), which points the app
at a throwaway settings file holding a labeller name. A test run therefore
never reads or changes a real user's settings, and Review & Label has someone
to save labels under. It also sets `BRAIN_BATTLE_MUTE=1`: the round bell and
the buzzer-test beeps go through `support/sound.py` and only log that they
would have played, so a test run (and the soak, and CI) never makes a sound.

It also gives the script its own LSL tag (`BRAIN_BATTLE_LSL_TAG`), which the
app adds to the source id of every stream it publishes. A test that subscribes
to a live stream must use `_env.lsl_inlet(name, outlet=…)` (or
`_env.lsl_resolve`): they find the stream by name **and** that source id — so
never another script's or a running app's stream of the same name — retry
until a deadline instead of one fixed timeout, and wait until the outlet has
seen the subscriber before anything is pushed.

The whole suite also runs on **macOS and Windows** for every push, through
GitHub Actions — see [Cross-platform & CI](cross-platform-and-ci.md).

## The acceptance gates

A few tests are **gates** — they encode a rule the project refuses to regress on.
Run them before and after any change in their area:

- **`test_smoke.py`** — the "loads and stands up" floor: fresh-subprocess import
  of every first-party module, headless construction of every screen + the app
  shell, `__version__` in the window title. **Run before and after every
  deletion/refactor.**
- **`test_no_placeholders.py`** — zero placeholder/temporary UI copy, zero
  placeholder screens, and EEG channel codes nowhere except the EEG viewer.
- **`test_layout_fit.py`** — every page fits all three window sizes with nothing
  clipped.
- **The docs honesty gates** — `test_docs.py` (links/images/diagrams resolve,
  search works, nothing clipped), `test_research_docs.py` (the file-format
  reference matches what the code writes), and `test_developer_docs.py` (every
  module has a reference entry and vice-versa; every ADR is indexed and in
  `nav.yml`; every non-internal page is in `nav.yml`; no "coming in a later
  documentation step" text remains; the CHANGELOG's top entry matches
  `version.py`). These are what keep the docs from drifting from the code — see
  [AGENTS.md's "Documentation is part of every change"](../contributing/docs-guide.md#documentation-is-part-of-every-change-owner-rule).

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
5. For a new file a recording contains, read it back with the tool an analyst
   would use (as `test_xdf.py` does with pyxdf) rather than with the code that
   wrote it.
6. If you add a new module, `test_smoke.py`'s module discovery and
   `test_developer_docs.py`'s module-reference check will both require it to be
   wired in and documented — that's intentional.
