# ADR-0015 — Live LSL streams and one XDF file per recording

**What this page covers:** why the app now publishes its signals as Lab
Streaming Layer (LSL) streams and writes a `recording.xdf` for every recording,
and why that file is built from the recording's other files instead of being
recorded live through LabRecorder.

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

The standard way to work with a Muse headband in research (see the Krigolson
Lab's *Working with Muse* guide) is:

    headband → a streamer (BlueMuse, uvicMuse, muselsl) → LSL stream →
    LabRecorder → one .xdf file → EEGLAB or Python

Analysts know this tool-chain, and XDF is the one format that EEGLAB, MNE-Python
and pyxdf all open with every stream already on one clock. Until now Brain
Battle wrote its own files only (per-headband CSVs, per-camera timestamp CSVs,
a marker CSV), which are easy to read but are not what an EEG analyst expects,
and offered no way for other lab software to see the signals live.

Two earlier facts constrain the answer. The app talks to the headbands directly
over Bluetooth ([ADR-0003](adr-0003-eeg-streamed-to-disk.md)) because running a
separate streamer program per headband was unreliable with two bands. And the
operators are non-technical: asking them to start LabRecorder, tick the right
streams and press Record before every sparring session is a step that will
sometimes be forgotten — and a forgotten step means a lost recording.

## Options considered

1. **Follow the tool-chain literally** — external streamer + LabRecorder, the
   app only adds markers. Standard, but needs two more programs running
   correctly for every session, brings back the two-streamers-per-adapter
   problem, and leaves video outside the XDF.
2. **Record the XDF live inside the app** from the incoming samples. One fewer
   program, but the XDF becomes a second live recording path that must itself
   be crash-safe, and it cannot contain labels (they are placed days later).
3. **Publish LSL streams live, and build the XDF from the recording's
   crash-safe files when the recording ends.** Chosen.

## Decision

**Both halves of the standard workflow are provided, without depending on
either for the safety of the data.**

- **Live LSL outlets** (`capture/lsl_outlets.py`). While the New recording
  screens are open the app publishes `BrainBattle_EEG_A` / `_B`, the headbands'
  movement and pulse sensors, `BrainBattle_Markers` (rounds, breaks, marked
  moments) and one `BrainBattle_Video_<camera>` stream per camera carrying each
  recorded frame's number at its capture time. Any LSL program — LabRecorder,
  the BrainVision LSL Viewer, a student's script — sees them. So the app *is*
  the streamer in the standard chain, on macOS and Windows alike, and a
  researcher who wants a LabRecorder file can simply run LabRecorder alongside.
  The outlets are fail-soft: if LSL cannot start, recording carries on.
- **`recording.xdf` in every recording folder** (`data/session_xdf.py`, written
  by the small built-in `data/xdf_writer.py`). It is built from the EEG CSVs,
  the camera sidecars, `phase_markers.csv`, the manifest and `labels.json`:
  automatically when a recording finishes, again on every export (so it carries
  the labels placed so far), and for an interrupted recording the next time the
  app finds it. It can also be rebuilt by hand:
  `python3 -m brain_battle.data.session_xdf "<recording folder>"`.

Stream names are identical in both (`data/signals.py`), and both use LSL-clock
timestamps derived from the one capture clock
([ADR-0017](adr-0017-one-capture-clock.md)), so a LabRecorder file and the
built-in file agree.

## Consequences

- **The crash-safe files stay the source of truth.** The XDF can never be the
  thing that loses data: it is derived, replaced atomically, and reproducible.
  The cost is that the same samples exist twice on disk (about 3 MB of XDF per
  2.5 minutes with two bands).
- **Video is aligned frame by frame inside the XDF.** Each camera stream has one
  sample per frame at that frame's true capture time, so the drifting-camera
  case needs no special handling by the analyst.
- **Labels reach EEGLAB and MNE as events** (`BrainBattle_Labels`), which a live
  recording could not offer.
- **New dependency:** `pyxdf` (for tests and the documented examples). `pylsl`
  was already required by muselsl. `pyqtgraph` was dropped in the same change.
- **Opening network outlets** may make macOS or Windows ask once whether to
  allow incoming connections. It can be switched off in Settings ("Share live
  signals with other lab software").
- **Not yet verified on hardware:** everything about real headbands — see
  [Known limitations](../research-and-data/limitations.md). The file format and
  the clock arithmetic are verified by reading files back with pyxdf
  (`tests/test_xdf.py`) and by subscribing to the live streams with ordinary LSL
  inlets (`tests/test_lsl_streams.py`).

## Links

- [Loading the data](../research-and-data/loading-the-data.md) (Python, MNE,
  EEGLAB), [File format reference](../research-and-data/file-formats.md#recordingxdf),
  [ADR-0003](adr-0003-eeg-streamed-to-disk.md),
  [ADR-0017](adr-0017-one-capture-clock.md).
- XDF specification: <https://github.com/sccn/xdf/wiki/Specifications>.
