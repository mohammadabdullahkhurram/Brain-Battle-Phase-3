# Personal data note

**What this page covers:** what personal data a recording contains, exactly
where the boxers' names are stored, what the two "hide names" switches do and
do not do, and a reminder to follow your institution's rules before sharing.
This is not legal advice.

## What a recording contains

A Brain Battle recording — and any export made from it — contains **personal
data about the boxers**:

- Their **names**, if you typed them (they are optional).
- Their **brain activity** (the EEG recordings) and head-movement signals,
  which are biometric data about identifiable people.
- **Video** of identifiable people.

It also contains the **labellers' names** ("Your name") — in each labels file,
in `history.jsonl`, and in the `labeller` column of an export.

## Where the boxers' names are

By default the names are kept everywhere the team needs them:

| Place | Where exactly |
|---|---|
| The recording's folder name | `2026-10-27 14-05 Sam vs Alex` |
| `session_manifest.json` | `boxers.A.name`, `boxers.B.name` |
| `recording.xdf` | `boxer_a_name`, `boxer_b_name` in every stream's description, `boxer_name` on a boxer's own streams, and the folder name in `recording` |
| An export | The copies of the files above, the export's `README.txt`, and the zip's own file name |

The names are **not** in the EEG or movement CSVs, the videos' file names, the
labels files, `labels.csv` or `events.csv` — those say only "A", "B" or "Both".

## Hiding the names

**In an export.** Tick **Replace boxer names with Boxer A / Boxer B** in the
export window (Settings decides whether it starts ticked). That export then has
no boxer name anywhere in it:

- its `recording.xdf` is built without the name fields, and `recording` holds
  only the date and time;
- its `session_manifest.json` copy says "Boxer A" and "Boxer B" and carries
  `names_hidden: true`;
- any mention of the names in its `history.jsonl` copy is replaced;
- its `README.txt` and the zip's suggested file name carry no names.

This is checked automatically: a test builds a name-free export and searches
every file in it for the names.

**On screen.** **Hide names** (in the sidebar and in Settings; ⇧⌘H on a Mac,
Ctrl+Shift+H on Windows) shows "Boxer A" and "Boxer B" in Sessions and Review &
Label — for presenting or sharing your screen.

## What hiding names does not do

- It does **not** hide the boxers in the **videos**. Faces are personal data;
  a name-free export is not an anonymous one.
- It does **not** change the recording on disk. The folder is still called
  "… Sam vs Alex" and its own files still hold the names. To hand over a
  recording without names, hand over a name-free **export**, not the folder.
- It does **not** remove the **labellers'** names.
- EEG itself can identify a person in principle. Removing a name is
  pseudonymisation, not anonymisation.

## Before you share or move it

Treat a recording folder or an export zip the way your institution tells you to
treat human-subjects research data:

- Follow your **university's / ethics board's data protection rules** (for many
  institutions this means GDPR or an equivalent) before copying, sharing, or
  uploading a recording or export.
- Share only with people the study's consent and ethics approval actually
  cover.
- Prefer institution-approved storage over personal drives or general-purpose
  file-sharing services. A shared recordings folder should be on storage your
  institution approves for this kind of data.

This page is a reminder, not legal advice. When in doubt, ask whoever is
responsible for data protection on your study before the data leaves your
machine.
