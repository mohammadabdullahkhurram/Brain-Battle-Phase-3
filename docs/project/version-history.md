# Version history

**What this page covers:** how Brain Battle got from v1 to v3 at a glance — the
three recording-model eras and what changed between them. The blow-by-blow dev
log is in [`CHANGELOG.md`](../../CHANGELOG.md); the long-form architectural
narrative is in [Project history](../archive/history.md).

## The authoritative version

The single source of truth for the version is `brain_battle/version.py`
(`__version__`). Today: **`main` and `develop` are both `3.0.0-beta.2`** — the
handover/cleanup release. `3.0.0-beta.1` was the first tagged pre-release, cut
when `develop` merged to `main` alongside the
[package reorganisation](../decisions/adr-0013-package-reorganisation.md);
`beta.2` added the tool-neutral `AGENTS.md`, the `*Screen` renames, and the
Documents default folder. The beta label becomes `3.0.0` when the remaining
pre-release work lands.

> The `CHANGELOG.md` headings numbered "Phase 2.N" are historical **dev-log**
> numbering, not release versions — they intentionally do not track
> `__version__`. The 3.x entries at the top are the current dev log.

## The three eras

| Era | Recording model | Where |
|---|---|---|
| **v1.x** | Per-phase recording: separate folders and clips per round (`baseline/`, `Round 1/`, each with `Side Cams/`, `POV Cams/`, `Muse data/`). | superseded |
| **v2.x** | One continuous take: one MP4 per camera and one CSV per band for the whole session, with `phase_markers.csv` for the boundaries. `main` was 2.1.0 before the 3.0 beta. | superseded |
| **v3.0** | The continuous take **plus the capture-time clock**: per-frame sidecars and a `t_session_seconds` column so alignment is measured, not assumed. A deliberate breaking change. | current (`main` & `develop`, v3.0.0-beta.2) |

### v1 → v2: the continuous-take migration

The app moved from a **per-phase** model (a folder and clips per round) to a
**one continuous take** model (one MP4 per camera, one CSV per band, phases as
rows in `phase_markers.csv`). The capture half was changed first; the migration
gap — the review/sync half still globbing nonexistent `Round {n}/` folders — is
where most of the early bugs lived, and closing it is what the v2 cleanup phase
was about. A structural consequence that still holds: all in-app cameras record
**no audio track**, which is why audio-based sync could never work for in-app
captures. See [Project history](../archive/history.md) for the detail.

### v2 → v3: the capture-time clock (breaking)

**3.0.0 is a deliberate breaking change.** The review/label path aligns by the
capture-time clock — `capture_clock` in the manifest plus `t_session_seconds` in
the camera sidecars and EEG CSVs ([ADR-0001](../decisions/adr-0001-capture-time-clock.md)).
Pre-3.0 sessions recorded without that data are **not supported** — there is no
legacy-fallback alignment path (one alignment path, not two). Alongside it, v3
rebuilt the app onto the four DESIGN screens, removed audio-xcorr, VLC, and the
old Review/Labelling tabs, and added the data-safety, settings, labelling, and
documentation work. Record fresh with 3.0.

### v3.0 → v3.1: the standard EEG workflow (not breaking)

**3.1.0-dev** (on `develop`, tag `v3.1.0-dev.1`) makes Brain Battle a normal
citizen of the Muse / Lab Streaming Layer tool-chain: every signal is a live LSL
stream, every recording gets one `recording.xdf`, the EEG display is a
conventional viewer with channel names and microvolts, the headbands' movement
sensors are recorded, and all timestamps come from one monotonic clock
([ADR-0015](../decisions/adr-0015-lsl-and-xdf.md),
[ADR-0016](../decisions/adr-0016-standard-eeg-viewer.md),
[ADR-0017](../decisions/adr-0017-one-capture-clock.md)). Recordings made with 3.0
still open unchanged, and can be given an XDF with
`python3 -m brain_battle.data.session_xdf "<recording folder>"`.

## Documentation steps (within 3.0.0-dev)

The documentation was itself sequenced, as steps **D1–D3**, interleaved before
Phase 5:

- **D1** — Getting started + User guide (11 pages), the in-app Documentation
  screen, and the diagram/screenshot pipeline.
- **D2** — Research & data (6 pages) + the format-reference honesty test.
- **D3** — Developer guide, Decisions (ADRs), Project, the "docs stay current"
  rule, and the developer-docs honesty test (this step).

Each is a dated entry at the top of [`CHANGELOG.md`](../../CHANGELOG.md).
