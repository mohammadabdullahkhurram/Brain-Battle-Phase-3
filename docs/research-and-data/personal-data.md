# Personal data note

**What this page covers:** what personal data a recording contains, how
fighters are identified (codes and colours, never names), and a reminder to
follow your institution's rules before sharing. This is not legal advice.

## What a recording contains

A Brain Battle recording — and any export made from it — contains **personal
data about the fighters**:

- Their **brain activity** (the EEG recordings) and head-movement signals,
  which are biometric data about identifiable people.
- **Video** of identifiable people.

It does **not** contain their names. Each fighter is identified by a
**4-character code** (capital letters and digits, for example `K7Q2`) and the
**colour** they wear, both chosen on Get ready before the match
([ADR-0023](../decisions/adr-0023-fighter-codes-and-colours.md)). Keep the list
that links codes to people **outside the app**, as your data-protection plan
requires.

It also contains the **labellers' names** ("Your name") — in each labels file,
in `history.jsonl`, and in the `labeller` column of an export.

## Where the codes are

| Place | Where exactly |
|---|---|
| The recording's folder name | `2026-11-17 14-05 K7Q2 vs M3XA` |
| `session_manifest.json` | `boxers.A.code`, `boxers.A.colour` (and the same for B) |
| `recording.xdf` | `boxer_a_code`, `boxer_b_code`, `boxer_a_colour`, `boxer_b_colour` in every stream's description; `boxer_code` / `boxer_colour` on a fighter's own streams |
| An export | The copies of the files above, the export's `README.txt`, and the zip's file name |

The EEG and movement CSVs, the labels files, `labels.csv` and `events.csv` say
only "A", "B" or "Both".

## Recordings made before codes

Recordings made with an earlier version may still hold the **names** that were
typed then (`boxers.A.name` in the manifest, `boxer_a_name` in its
`recording.xdf`, and in the folder name). The app opens them unchanged and
never rewrites them. If such a recording must be shared without names, rename
or re-export it by hand according to your data-protection rules.

## What codes do not do

- They do **not** hide the fighters in the **videos**. Faces are personal data.
- They do **not** remove the **labellers'** names.
- EEG itself can identify a person in principle. A code is pseudonymisation,
  not anonymisation.

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
