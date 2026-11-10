# Export for analysis

**What this page covers:** what pressing "Export for analysis" produces, at
a glance. The exact file formats and columns are reference material in the
[Research & data](../research-and-data/file-formats.md) section — this page is
just "what's in the zip and how to get it."

Press **Export for analysis** in the top bar of
[Review & Label](review-and-label.md) to open the export dialog.

## What you get

A single `.zip` file containing:

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

The zip's exact file formats (manifest, EEG CSV columns, the coregistered
grid, timing guarantees) are covered in the Research & data section of these
docs, written for whoever opens the data in Python or R — not yet published
as of this documentation step. For now, the `README.txt` inside every export
bundle explains the files it ships with.
