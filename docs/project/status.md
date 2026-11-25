# Current status

**What this page covers:** where the project is right now — what's complete,
what's next, the working agreement that governs how changes are made, and the
hardware situation that shapes all of it.

## In one line

**Phase 3 and the documentation are complete, and `develop` (`3.1.0-dev`) adds
two passes on top.** The first: the standard EEG workflow — live Lab Streaming
Layer streams, one `recording.xdf` per recording, a conventional EEG viewer,
and the headbands' movement sensors
([write-up](../reviews/2026-11-fable-review.md)). The second: a redesign of
every screen (design F, a top header), more than one labeller per recording,
fighters as codes and colours, Mac and Windows support, a recordings folder
shared between computers, the timing check and buzzer calibration, and each
camera in a process of its own ([write-up](../reviews/2026-11-redesign.md)).
`main` is the reviewed `3.0.0-beta.2`; `develop` awaits the owner's review
before it moves to `staging` for testing and then to `main`. The hardware is broken, so everything is built and tested
against a generated demo session and simulated devices.

## What's complete

- **The unified capture-time clock** — alignment is driven by recorded timestamps
  on one `t_session_seconds` axis ([ADR-0001](../decisions/adr-0001-capture-time-clock.md)).
- **The four DESIGN screens** — Sessions (home library), New recording (Get
  ready → Recording → Saved), Review & Label (one merged playback + labelling
  screen), and Settings (everything formerly hardcoded) — redesigned in
  November 2026 ([ADR-0018](../decisions/adr-0018-november-2026-redesign.md)).
- **Labelling** (what would have been Phase 4) was delivered inside the Review &
  Label build — labels with who A/B/Both, point/duration, undo/redo, a shared
  team vocabulary, and an analysis-bundle export.
- **Several labellers per recording** — each person's labels in their own file,
  comparison with plain-language agreement, automatic migration of older
  recordings ([ADR-0019](../decisions/adr-0019-one-labels-file-per-labeller.md)).
- **Fighters are 4-character codes and colours, never names** — in the
  manifest, folder name, `recording.xdf` and exports; older recordings with
  names still open ([ADR-0023](../decisions/adr-0023-fighter-codes-and-colours.md),
  superseding ADR-0021).
- **Timing under 10 ms between devices after correction** — 60 fps cameras,
  de-jittered headband timestamps, a `timing_quality.csv` per recording, and
  the buzzer test on Get ready that measures each device's delay and stores
  corrections ([ADR-0024](../decisions/adr-0024-timing-check-and-buzzer-calibration.md));
  verified with simulated devices only. Each camera captures in a process of
  its own ([ADR-0025](../decisions/adr-0025-each-camera-in-its-own-process.md)).
- **The app's own dialogs and dropdowns** — no native alert anywhere, always
  light even in dark mode ([ADR-0026](../decisions/adr-0026-in-app-dialogs-and-dropdowns.md)).
- **Mac and Windows**, with the test suite run automatically on both, and **a
  recordings folder shared between computers**
  ([ADR-0020](../decisions/adr-0020-shared-folder-safety.md),
  [Cross-platform & CI](../developer-guide/cross-platform-and-ci.md)).
- **Data safety** — EEG streamed to disk, crash-safe fragmented H.264,
  crash-recovery on next launch, a disk-space pre-flight.
- **The in-app Documentation screen** and the documentation itself — Getting
  started, User guide, Research & data, and (this step, D3) Developer guide,
  Decisions, and Project.
- **The standard EEG workflow (3.1.0-dev, on `develop`)** — every signal is
  published live as an LSL stream and saved as `recording.xdf`, which EEGLAB,
  MNE-Python and pyxdf open directly, with video frames, EEG and markers on one
  clock ([ADR-0015](../decisions/adr-0015-lsl-and-xdf.md),
  [ADR-0017](../decisions/adr-0017-one-capture-clock.md)); the brain-activity
  display is a conventional EEG viewer
  ([ADR-0016](../decisions/adr-0016-standard-eeg-viewer.md)); the headbands'
  accelerometer, gyroscope and (optionally) pulse sensor are recorded; and a
  camera that runs off its nominal frame rate is reported.
- **The `brain_battle` package** — all modules reorganised into
  `ui/ · capture/ · playback/ · data/ · support/`, installable with
  `pip install -e .`, run with `python3 -m brain_battle`
  ([ADR-0013](../decisions/adr-0013-package-reorganisation.md)).

The old Review/Labelling tabs, VLC, audio-xcorr, and the Live Monitor are gone;
see [Version history](version-history.md) and [Project history](../archive/history.md).

## What's next

**First, real hardware.** Most open questions can only be answered by one real
recording: the buzzer/tap timing check, a first look at Muse EEG during
sparring, and whether the movement sensors and live LSL streams behave with two
real headbands (see [known limitations](../research-and-data/limitations.md)).

**Second, a real Windows computer and a real shared drive.** Windows has only
been exercised by the automatic test run with simulated devices; see
[Cross-platform & CI](../developer-guide/cross-platform-and-ci.md).

**Then the rest of Phase 5** — per-round clip export, a whole-recording EEG
signal-quality summary, and making the optional combined video drift-proof.
**Phases 6 and 7** (shipping; team & cloud) follow. See the [Roadmap](roadmap.md). Per the working agreement, no
phase starts without the owner's approval.

## The working agreement

Brain Battle is developed under an explicit agreement that governs every change:

- **The owner approves each `ROADMAP.md` phase before it starts.** A session never
  starts the next phase on its own initiative, even if the current one is finished.
- **Commit locally per logical change.** Never push to `staging` or `main`
  unless the owner asks for it.
- **Run everything in `tests/` before and after every change, and report
  pass/fail** — not just "I ran the tests".
- **`DESIGN.md` is the source of truth for all UI work.** If code and DESIGN.md
  disagree, ask; don't guess.
- **Work only on `develop`.** `staging` receives merges from `develop` for the
  owner and a teammate to test; `main` receives merges from `staging` once
  approved. Bugs found in testing are fixed on `develop` ([branch policy](../developer-guide/setup-and-workflow.md)).
- **Documentation is part of every change** — a change isn't done until its docs
  are updated (see [docs-guide.md](../contributing/docs-guide.md#documentation-is-part-of-every-change-owner-rule)).

## The hardware situation

**The hardware is currently broken.** Nothing can be tested against real cameras
or real Muse-S bands right now. Two consequences a newcomer must internalise:

1. **Nobody has inspected raw Muse-S EEG from a real sparring round** — the noise
   floor, motion artifact during actual boxing, and whether the channels are even
   readable are all unknown. Don't assume the EEG is clean.
2. **The synchrony analysis method is undecided** — the timing target is the
   professor's requirement of under 10 ms between all devices after correction
   ([ADR-0024](../decisions/adr-0024-timing-check-and-buzzer-calibration.md)),
   met so far only with simulated devices; the first real session must confirm
   it.

Everything therefore builds and tests against the
[demo session and simulation mode](../developer-guide/simulation-and-demo.md). The
checks that genuinely need hardware are listed, not hidden — see
[known limitations](../research-and-data/limitations.md) and
[known issues](../developer-guide/known-issues.md).
