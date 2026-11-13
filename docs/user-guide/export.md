# Export for analysis

**What this page covers:** what pressing "Export for analysis" produces, at
a glance. The exact file formats and columns are reference material in the
[Research & data](../research-and-data/file-formats.md) section — this page is
just "what's in the zip and how to get it."

Press **Export for analysis** in the top bar of
[Review & Label](review-and-label.md) to open the export dialog.

## What you get

A single `.zip` file containing:

- `recording.xdf` — everything on one timeline in one file: both boxers'
  brain activity, the round markers, your labels, and the timing of every
  video frame. This is the standard format for EEG software (EEGLAB,
  MNE-Python); most analysts will start here
- `labels.csv` and `events.csv` — every label you placed
- both boxers' brain activity merged onto one shared timeline
  (`eeg_coregistered.csv`)
- the raw brain recordings and camera videos, each with its own timing file
- the recording's details, round markers, and history

There's also a checkbox, **"Also make a combined video of all four cameras
(slow)"** — off by default, since most analysis work wants the data, not a
preview video, and merging four videos into one is slow.

## Steps

1. From Review & Label, press **Export for analysis**.
2. Check (or leave unchecked) **"Also make a combined video of all four
   cameras (slow)."**
3. Confirm or change where the `.zip` saves to ("Save to:" with a
   **Change…** button) — it defaults to the recording's own folder.
4. Press **Export**.

A progress bar tracks the export; when it finishes, the `.zip` is ready
wherever you chose to save it.

## Where to go from here

Hand the zip to whoever is doing the analysis and point them at
[Loading the data](../research-and-data/loading-the-data.md) — it has
copy-paste examples for Python, MNE, EEGLAB and R. The exact contents of every
file are in the [File format reference](../research-and-data/file-formats.md),
and the `README.txt` inside every export summarises what it ships with.

Every recording folder also has its own `recording.xdf`, made automatically
when the recording finishes; exporting refreshes it so it includes your latest
labels.
