# ADR-0016 — A standard EEG viewer, with channel names

**What this page covers:** why the brain-activity display now looks like a
conventional EEG viewer and shows the electrode names TP9, AF7, AF8, TP10 and
AUX — reversing the earlier design rule that those names must never appear.

- **Status:** Accepted (amends [ADR-0007](adr-0007-light-only-design.md)'s
  vocabulary rule and DESIGN.md)
- **Date:** 2026-11-13

## Context

The approved design (DESIGN.md, 2026-10-27) treated EEG channel names as jargon:
the brain-activity card drew four thin unlabelled lines per boxer, with no
units, no axis and an automatic scale, and a test forbade "TP9" and friends
anywhere in the UI. That suited an operator who only needs to know "is a signal
arriving?".

It does not suit the people who must judge the signal. Nobody on the project has
yet seen Muse EEG recorded during sparring, so the first real recordings will be
examined closely by someone who knows EEG — and an EEG reader cannot assess a
trace with no channel names, no amplitude scale and no time axis. The owner
asked for the display to look like standard lab software (the BrainVision LSL
Viewer was the reference).

## Decision

**The EEG display is a conventional EEG viewer** (`ui/eeg_viewer.py`), used in
Review & Label and on the Connect and Recording steps:

- one stacked trace per channel, labelled **TP9, AF7, AF8, TP10** and **AUX**;
- amplitude in **µV**, with a scale bar that always states the current scale;
- one shared **time axis**;
- **timebase** (2–30 s) and **scale** (10–1000 µV, or automatic) controls, and a
  switchable 1–45 Hz **display filter**;
- **event markers** — round and break starts, marked moments, placed labels —
  as vertical lines with their names along the top.

Plain language is kept *alongside* the standard names rather than replaced by
them: each channel label carries its location in small print when there is room
("left ear"), the tooltip spells out all five, and the contact-quality chips and
messages still say "Weak contact: right ear". The channel-code ban in
`tests/test_no_placeholders.py` now applies everywhere *except* the viewer.

The viewer is drawn with Qt's own painter, like every other custom widget in the
app, so the unused `pyqtgraph` dependency was removed.

## Consequences

- **A reviewer can read the signal**: which electrode, how many microvolts, at
  what time, relative to which event.
- **The display takes more room.** The Review & Label card grew from about 190
  to 300 pixels (480 with the "taller" button); on the smallest supported window
  the left column scrolls. The compact Recording-step variant shows the four skin
  electrodes only and replaces the four contact chips with one status line.
- **Display-only processing** (drift removal, the filter) is clearly labelled as
  such and never touches recorded data — `tests/test_eeg_viewer.py` asserts it.
- **DESIGN.md was edited** to match; this ADR is the record of why.

## Links

- [Review & Label](../user-guide/review-and-label.md#brain-activity-eeg),
  [Design system](../developer-guide/design-system.md),
  [ADR-0015](adr-0015-lsl-and-xdf.md).
