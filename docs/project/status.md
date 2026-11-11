# Current status

**What this page covers:** where the project is right now — what's complete,
what's next, the working agreement that governs how changes are made, and the
hardware situation that shapes all of it.

## In one line

**Phase 3 and the documentation are complete, and the app has been reorganised
into the `brain_battle` package** (`main` and `develop` are both
`3.0.0-beta.1`, the first tagged pre-release — see
[ADR-0013](../decisions/adr-0013-package-reorganisation.md)). Phase 5 is next,
gated on owner approval. The hardware is broken, so everything is built and
tested against a generated demo session and simulated devices.

## What's complete

- **The unified capture-time clock** — alignment is driven by recorded timestamps
  on one `t_session_seconds` axis ([ADR-0001](../decisions/adr-0001-capture-time-clock.md)).
- **The four DESIGN screens** — Sessions (home library), New recording (the guided
  Connect → Set up → Record → Saved flow), Review & Label (one merged playback +
  labelling screen), and Settings (everything formerly hardcoded).
- **Labelling** (what would have been Phase 4) was delivered inside the Review &
  Label build — `labels.json` v2 with who A/B/Both, point/duration, undo/redo, a
  shared team vocabulary, and an analysis-bundle export.
- **Data safety** — EEG streamed to disk, crash-safe fragmented H.264,
  crash-recovery on next launch, a disk-space pre-flight.
- **The in-app Documentation screen** and the documentation itself — Getting
  started, User guide, Research & data, and (this step, D3) Developer guide,
  Decisions, and Project.
- **The `brain_battle` package** — all modules reorganised into
  `ui/ · capture/ · playback/ · data/ · support/`, installable with
  `pip install -e .`, run with `python3 -m brain_battle`
  ([ADR-0013](../decisions/adr-0013-package-reorganisation.md)).

The old Review/Labelling tabs, VLC, audio-xcorr, and the Live Monitor are gone;
see [Version history](version-history.md) and [Project history](../archive/history.md).

## What's next

**Phase 5** — the remaining report/research-value items: using the full per-frame
sidecar for playback timing + drift detection, configurable record resolution/fps,
a single Export button with optional merged video, per-round clip export, Muse
accelerometer/PPG, and an EEG signal-quality view. **Phases 6 and 7** (shipping;
team & cloud) follow. See the [Roadmap](roadmap.md). Per the working agreement, no
phase starts without the owner's approval.

## The working agreement

Brain Battle is developed under an explicit agreement that governs every change:

- **The owner approves each `ROADMAP.md` phase before it starts.** A session never
  starts the next phase on its own initiative, even if the current one is finished.
- **Commit locally per logical change. Never push unless explicitly told to.**
- **Run everything in `tests/` before and after every change, and report
  pass/fail** — not just "I ran the tests".
- **`DESIGN.md` is the source of truth for all UI work.** If code and DESIGN.md
  disagree, ask; don't guess.
- **Documentation is part of every change** — a change isn't done until its docs
  are updated (see [docs-guide.md](../contributing/docs-guide.md#documentation-is-part-of-every-change-owner-rule)).

## The hardware situation

**The hardware is currently broken.** Nothing can be tested against real cameras
or real Muse-S bands right now. Two consequences a newcomer must internalise:

1. **Nobody has inspected raw Muse-S EEG from a real sparring round** — the noise
   floor, motion artifact during actual boxing, and whether the channels are even
   readable are all unknown. Don't assume the EEG is clean.
2. **The synchrony analysis method is undecided and has no owner yet** — so the
   app targets tens-of-ms alignment and does not invest in tighter precision until
   someone confirms it matters.

Everything therefore builds and tests against the
[demo session and simulation mode](../developer-guide/simulation-and-demo.md). The
checks that genuinely need hardware are listed, not hidden — see
[known limitations](../research-and-data/limitations.md) and
[known issues](../developer-guide/known-issues.md).
