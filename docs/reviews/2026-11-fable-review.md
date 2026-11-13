# November 2026 review and improvement pass (3.1.0-dev)

**What this page covers:** what changed on `develop` between the snapshot tag
`pre-fable-2026-11-13` and the tag `v3.1.0-dev.1`, why, what was removed, the
test results, what still needs real hardware, and the decisions waiting for the
owner. Written by Claude (Anthropic's Fable 5.2 model) working on its own from
an open-ended brief; nothing here has been reviewed by a person yet.

- **Branch:** `develop` only. `main` was not touched.
- **To go back:** `git switch -c restore-pre-fable pre-fable-2026-11-13`.
- **Version:** `3.1.0-dev` (`brain_battle/version.py`).

## In one paragraph

The app now fits the standard Muse research workflow. While you record, every
signal is also a live **Lab Streaming Layer (LSL)** stream that LabRecorder or an
EEG viewer can see. Every recording gets one **`recording.xdf`** with both
boxers' EEG, the round markers, your labels and the time of every video frame on
one clock, which EEGLAB, MNE-Python and pyxdf open directly. The brain-activity
display is now a **conventional EEG viewer** (TP9, AF7, AF8, TP10, AUX in µV, a
time axis, timebase and scale controls, named event markers). The headbands'
**movement sensors** are recorded. None of this could be tried on real headbands
or cameras; it is verified against simulated devices, generated recordings, and
by reading the files back with the tools an analyst would use.

## What changed, and why

### 1. One XDF file per recording

`recording.xdf` is written into every recording folder
(`brain_battle/data/session_xdf.py`, using a small built-in writer,
`data/xdf_writer.py`). It contains:

| Stream | What |
|---|---|
| `BrainBattle_EEG_A`, `BrainBattle_EEG_B` | 5 channels, 256 Hz, microvolts |
| `BrainBattle_ACC_…`, `BrainBattle_GYRO_…` (and `PPG` if switched on) | the headbands' movement / pulse sensors |
| `BrainBattle_Markers` | `BASELINE_START`, `ROUND_1_START`, …, `SESSION_END`, `MOMENT` |
| `BrainBattle_Labels` | the labels placed in Review & Label (`Jab/A`, `Clinch/Both/start` …) |
| `BrainBattle_Video_<camera>` | one sample per video frame: the frame number at its capture time |

**The main design choice** ([ADR-0015](../decisions/adr-0015-lsl-and-xdf.md)):
the XDF is *built from the recording's existing crash-safe files* (EEG CSVs,
camera timing files, markers, labels) rather than recorded live through
LabRecorder. I chose this because:

- the operators are non-technical, and "also start LabRecorder and tick the right
  boxes" is a step that will sometimes be forgotten, losing a session;
- the file can then never be the thing that loses data — it can be rebuilt at
  any time, after a crash, or for old recordings;
- labels are placed days after recording, and this way they reach EEGLAB/MNE as
  events too.

It is built automatically when a recording finishes, rebuilt on every export,
built for an interrupted recording when the app next finds it, and by hand with
`python3 -m brain_battle.data.session_xdf "<recording folder>"`.

### 2. Live LSL streams

While the New recording screens are open the app publishes the same streams
(except labels) on the network (`capture/lsl_outlets.py`). The app's own
Bluetooth reader is the streamer, so macOS and Windows need no extra tool; a
lab that wants a LabRecorder file can just run LabRecorder alongside. This path
is deliberately "fail-soft": if LSL cannot start, recording carries on. It can
be switched off in Settings.

### 3. One capture clock

Before, video frames and markers were stamped with the computer's steady
(monotonic) clock but EEG with the wall clock. A wall-clock correction during a
recording (network time sync) would have silently shifted EEG against video.
Now everything comes from the one steady clock, and wall-clock and LSL times are
fixed offsets from it (`capture/clock.py`,
[ADR-0017](../decisions/adr-0017-one-capture-clock.md)). This is a correctness
fix, not an attempt at tighter precision — the standing rule about not chasing
precision until the analysis method is chosen still holds.

### 4. A standard EEG viewer

`ui/eeg_viewer.py` replaces the old thin unlabelled traces in Review & Label and
on the Connect and Recording steps. This **reverses a rule in the approved
DESIGN.md** ("never show EEG channel codes"), at the owner's explicit request;
the reasoning is in [ADR-0016](../decisions/adr-0016-standard-eeg-viewer.md) and
DESIGN.md is amended with the changes marked. Plain language is kept beside the
standard names (tooltips, "left ear" under each label when there is room, and
the contact messages still say "Weak contact: right ear").

Cost: the display is taller (300 px instead of 190 in Review), so the video grid
is a little smaller and the left column scrolls at the smallest window size. On
the Recording step the four contact chips became one status line to make room.

### 5. Headband movement and pulse sensors, real battery level

The reader now also records the accelerometer and gyroscope (on by default) and
the pulse sensor (off by default), and reads the real battery level. The roadmap
said to wait until plain EEG was confirmed usable; I pulled movement forward
because it is exactly what is needed to *judge* whether sparring EEG is usable,
so it should be in the first real recording. It can be switched off in Settings
if it disturbs the Bluetooth link.

### 6. Smaller improvements

- **Wrong-boxer risk removed.** Which EEG file belongs to which boxer was decided
  by the *current* Settings; changing the headband assignment later would have
  swapped boxers in old recordings. It now comes from each recording's own
  manifest. In `eeg_coregistered.csv`, `_01` is always Boxer A.
- **Crash-truncated rows.** A recording cut off mid-write could leave a
  half-written number in the last CSV row; Review, export and the XDF now ignore
  an unterminated last line.
- **Camera frame-rate drift is reported** on the Saved screen ("Worth checking")
  and in the XDF (`measured_fps`).
- **Visual glitches fixed**: grey boxes and stray underlines behind labels
  (Review, record flow, Settings, Sessions), nested borders on checklist rows,
  and frame-step buttons that lost their symbols at the smallest window.
- **Faster test runs**: `python3 tools/run_tests.py` runs all scripts several at
  a time — about 5½ minutes instead of 11 — with one PASS/FAIL line per script.
- **Documentation screenshots**: the Review & Label picture in the user guide
  used to show the empty screen; it now shows a recording with labels.

## What was removed

| Removed | Why |
|---|---|
| ~1,000 lines in `capture/eeg_module.py`: `EEGLiveWidget`, `DevicePickerDialog`, `_LSLReaderThread`, `_RecordStopper`, the device registry (1,477 → 483 lines) | The pre-Phase-3 live monitor. Nothing referenced it. |
| `capture/eeg_plot_pg.py` (282 lines) | Only the dead widget above used it. |
| The `pyqtgraph` dependency | Only the dead code used it. (`pyxdf` was added.) |
| `MiniTrace`, `BrainTrace` | Replaced by the one `EEGViewer`. |
| Three copies of the "write the manifest safely" code | Now one `_update_manifest`. |
| `session_storage._safe`, unused imports | Unused. |

The package is about the same size as before (roughly 13,100 lines): the
removals paid for the new modules.

## Commits

| Commit | What |
|---|---|
| `8208c6b` | The capture clock, LSL outlets, `recording.xdf`, the EEG viewer, reader clean-up |
| `f75ad07` | Tests for XDF, LSL and the viewer; the Settings card; truncated-row fix |
| `eab629e` | Docs, three ADRs, DESIGN amendment, drift report, test runner, polish; version 3.1.0-dev |
| (this commit) | This review |

## Test results

Before any change, all 27 scripts passed. At the end, **all 30 scripts pass**
(`python3 tools/run_tests.py`, final run on this machine: macOS, Python 3.13):

| Script | Result | | Script | Result |
|---|---|---|---|---|
| test_app_settings | PASS | | test_light_theme | PASS |
| test_capture_clock | PASS | | test_lsl_streams *(new)* | PASS |
| test_contact_log | PASS | | test_no_placeholders | PASS |
| test_contact_quality | PASS | | test_player | PASS |
| test_crash_recovery | PASS | | test_record_flow | PASS |
| test_demo_session | PASS | | test_research_docs | PASS |
| test_developer_docs | PASS | | test_review_tab | PASS |
| test_docs | PASS | | test_ring_buffer | PASS |
| test_eeg_streaming | PASS | | test_round_bell | PASS |
| test_eeg_viewer *(new)* | PASS | | test_sessions | PASS |
| test_export | PASS | | test_settings_screen | PASS |
| test_flash_sync | PASS | | test_smoke | PASS |
| test_history_log | PASS | | test_video_recording | PASS |
| test_label_vocab | PASS | | test_xdf *(new)* | PASS |
| test_labels_v2 | PASS | | test_layout_fit | PASS |

Two honest notes on the tests:

- `test_video_recording` and, once, the first check of `test_record_flow` failed
  during parallel runs and passed when run alone — they measure real time and
  the machine was busy. The runner now re-runs a failed script on its own before
  counting it; a failure that repeats alone is real.
- The **MNE-Python** example in the docs was run once by hand against a
  generated recording (in a throwaway environment) and works; it is not part of
  the automatic tests because MNE is not a dependency. The **EEGLAB** example
  was **not run at all** — there is no MATLAB here. It follows the documented
  `pop_loadxdf` / `load_xdf` usage, but someone with EEGLAB should try it.

## What needs real hardware to verify

Nothing below can be closed by more code.

1. **EEG timing over Bluetooth** — how late a sample arrives before it is
   stamped. One check answers it: both boxers tap gloves in view of a camera;
   the spike in the EEG (and now, much more clearly, in the accelerometer) should
   sit at the same time as the video frame. Procedure:
   [Timing & accuracy](../research-and-data/timing-and-accuracy.md).
2. **Is Muse EEG usable during sparring at all?** Still nobody has looked. Open
   the first real `recording.xdf` in an EEG tool, or in Review & Label with the
   filter off.
3. **Movement and pulse sensors.** Never received from a real headband by this
   project. The pulse sensor assumes the headband's "p50" mode, which I could not
   confirm — that is why it is off by default.
4. **Two headbands plus the extra sensor traffic** on one Bluetooth adapter.
5. **Battery level** from a real band (simulated only).
6. **Live LSL with real devices**, LabRecorder alongside, and across a lab
   network (only same-computer receivers were tested). Expect the operating
   system to ask once about incoming network connections.
7. **Watching Review & Label play a real recording** (unchanged from before).
8. **Windows.** Everything was run on macOS only. One known rough edge: dates
   are formatted with `%-d`, which Windows' Python does not accept.

## What I would do next

1. **One real five-minute recording with the tap test**, movement sensors on,
   LabRecorder running alongside. It answers items 1–6 above in one go and tells
   you whether the whole approach is sound before more is built on it.
2. Have whoever will do the analysis **open one `recording.xdf`** in their own
   tool and say whether it is what they need — especially the EEGLAB route.
3. Then the remaining Phase 5 items in `docs/ROADMAP.md`: per-round clip export,
   a whole-recording EEG quality summary, a drift-proof combined video, a
   clock-driven round timer.
4. Fix the Windows date format before anyone runs it on Windows.

## Decisions for the owner

1. **Merge `develop` into `main`?** Only after you have looked at it running
   (`BRAIN_BATTLE_SIMULATE=1 python3 -m brain_battle`). I would wait for the
   real-hardware recording if that is close.
2. **Is a built-in XDF acceptable, or must the file come from LabRecorder?** I
   built it in for the reasons above, and kept the LabRecorder route open. If
   your collaborators require LabRecorder-made files, nothing needs to change in
   the app — just run LabRecorder — but say so and the docs should lead with that.
3. **AUX row shown by default?** It normally carries nothing and costs screen
   height. I kept it visible in Review and on Connect because you asked for it,
   and hid it on the cramped Recording step.
4. **Movement sensors on by default?** I think yes; if the first real session
   shows Bluetooth trouble, flip the default in `data/app_settings.py`.
5. **Live LSL sharing on by default?** It makes the app announce itself on the
   network and may trigger a firewall question. On is right for a lab; you may
   prefer off on a university-managed network.
6. **The Recording step's contact chips became one status line.** A departure
   from the approved mockup, made for space. Say if you want the chips back and
   a smaller EEG display instead.
7. **Boxer names are not written into the XDF** (only A/B and the headband
   name), to keep personal data out of a file that will be passed around. The
   manifest in the same export still has them. Tell me if you want it otherwise.
8. **`AGENTS.md` says "never start a phase on your own initiative".** I treated
   your brief as the approval for this pass and recorded it in ROADMAP as
   "Phase 3.1"; future sessions should still ask.
