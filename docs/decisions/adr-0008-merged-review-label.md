# ADR-0008 — One merged Review & Label screen

**What this page covers:** why the separate Review (sync) tab and Labelling tab
were merged into one screen with no visible "sync" step.

- **Status:** Accepted
- **Date:** 2026-11-01

## Context

The app had two separate tabs: a **Sync/Review** tab (four VLC players, an
auto-sync worker, EEG following the playhead) and a **Labelling** tab (Qt
Multimedia players, place labels at the playhead). They had **separate,
sometimes-inconsistent alignment logic** and two different playback stacks, and
they exposed "sync" as something the user had to think about and click. Once
[ADR-0001](adr-0001-capture-time-clock.md) made alignment automatic from recorded
timestamps, there was nothing left to "sync" manually — and
[ADR-0005](adr-0005-pyav-playback.md) gave one playback stack that could serve
both jobs.

## Options considered

1. **Keep two tabs**, but share the alignment code between them — removes the
   inconsistency but keeps two screens and the "sync" concept.
2. **Merge into one screen** on a single playhead, with alignment automatic and
   invisible.

## Decision

**Merge Review and Labelling into one "Review & Label" screen**
(`review_label_screen.py` + `pyav_player.py`), driven by a single
`t_session_seconds` playhead. The separate Sync/Review concept goes away entirely
— alignment is automatic and there is nothing to click to "sync". Labels, the
video grid, the brain-activity card, and the timeline all move on the same
playhead. The old `SyncReviewTab`, `LabellingTab`, `MainEditorSyncWorker`,
`EEGReviewWidget`, `vlc_module.py`, and `sync_engine.py` are deleted.

## Consequences

- **One alignment path, one playback stack, one screen** — no two-codepath
  inconsistency, ~620 lines of playback glue removed
  ([ADR-0005](adr-0005-pyav-playback.md)).
- **"Sync" disappears from the user's mental model** — matching the DESIGN goal of
  a non-technical audience.
- **Labels-first open** — the screen shows labels and the timeline immediately and
  loads media in the background, so it's usable in well under half a second
  (`tests/test_review_tab.py`).
- Phase 4 (labelling) was delivered **inside** this merge rather than as a
  separate phase ([ADR-0006](adr-0006-labels-v2.md)).

## Links

- Commit `c21fac0` (merged Review & Label screen; delete old stacks).
- [open-in-review flow](../developer-guide/key-flows.md#opening-a-recording-in-review-label),
  [Review & Label user guide](../user-guide/review-and-label.md).
