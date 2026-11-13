# Decisions (ADRs)

**What this page covers:** the index of Architecture Decision Records — one page
per significant technical decision, explaining *why* the app is built the way it
is. Read these when a design choice looks surprising; the reasoning (and what it
cost) is here rather than scattered through commit messages.

## What an ADR is

Each record follows [Michael Nygard's
format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions):
**Status**, **Date**, **Context** (the forces at play), **Options considered**,
**Decision** (what we chose), **Consequences** (what it costs or enables), and
**Links** to the commits and docs. Most of these were already-made decisions
written up after the fact, so most are **Accepted**. ADRs are immutable once
accepted: a later reversal gets a new ADR that supersedes the old one, which is
marked accordingly rather than edited away.

When you make a significant technical decision, add an ADR here in the same
change and list it in `nav.yml` — `tests/test_developer_docs.py` fails if an ADR
file is missing from this index or from `nav.yml`.

## The records

| ADR | Decision | Date | Status |
|---|---|---|---|
| [ADR-0001](adr-0001-capture-time-clock.md) | Capture-time clock for alignment | 2026-07-07 | Accepted |
| [ADR-0002](adr-0002-remove-audio-xcorr.md) | Remove audio cross-correlation sync | 2026-07-07 | Accepted |
| [ADR-0003](adr-0003-eeg-streamed-to-disk.md) | Stream EEG to disk during recording | 2026-10-21 | Accepted |
| [ADR-0004](adr-0004-crash-safe-h264.md) | Crash-safe fragmented H.264 via per-camera ffmpeg | 2026-10-22 | Accepted |
| [ADR-0005](adr-0005-pyav-playback.md) | PyAV app-driven playback | 2026-10-27 | Accepted |
| [ADR-0006](adr-0006-labels-v2.md) | Labels v2 schema (who A/B/Both, point vs duration) | 2026-10-29 | Accepted |
| [ADR-0007](adr-0007-light-only-design.md) | Light-only design | 2026-10-27 | Accepted |
| [ADR-0008](adr-0008-merged-review-label.md) | One merged Review & Label screen | 2026-11-01 | Accepted |
| [ADR-0009](adr-0009-folder-naming.md) | Date + names folder naming, manifest-based discovery | 2026-10-29 | Accepted |
| [ADR-0010](adr-0010-app-settings-store.md) | App settings store | 2026-11-03 | Accepted |
| [ADR-0011](adr-0011-simulated-devices.md) | Simulated devices for dev/test | 2026-11-03 | Accepted |
| [ADR-0012](adr-0012-in-app-docs.md) | In-app docs: Markdown + pre-rendered diagrams | 2026-11-09 | Accepted |
| [ADR-0013](adr-0013-package-reorganisation.md) | Reorganise into the `brain_battle` package | 2026-11-10 | Accepted |
| [ADR-0014](adr-0014-documents-default-folder.md) | Default recordings folder in the user's Documents | 2026-11-11 | Accepted |
| [ADR-0015](adr-0015-lsl-and-xdf.md) | Live LSL streams and one XDF file per recording | 2026-11-13 | Accepted |
| [ADR-0016](adr-0016-standard-eeg-viewer.md) | A standard EEG viewer, with channel names | 2026-11-13 | Accepted |
| [ADR-0017](adr-0017-one-capture-clock.md) | One capture clock, seen three ways | 2026-11-13 | Accepted |

The sources behind these decisions are the planning documents
([DESIGN.md, ROADMAP.md, PLAYBACK_PROPOSAL.md, ARCHITECTURE_AND_EVALUATION.md,
history.md](../project/planning-documents.md)) and the git history.
