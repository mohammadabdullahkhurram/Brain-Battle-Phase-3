# ADR-0006 — Labels v2 schema (who A/B/Both, point vs duration)

**What this page covers:** why labels gained a boxer field and a point/duration
distinction, and why they live on the numeric session axis.

- **Status:** Accepted
- **Date:** 2026-10-29

## Context

The old labels were points only (`timestamp_sec`, no end) with **no boxer
field**, stored by formatting a timestamp to an `HH:MM:SS.mmm` string and parsing
it back by splitting on `:`. For between-brain synchrony analysis, a label must
say **whose** event it is, and some events are naturally durations (a clinch, a
knockdown, a referee stoppage), not instants. The string round-trip was also
lossy and fragile once everything else lived on the numeric `t_session_seconds`
axis ([ADR-0001](adr-0001-capture-time-clock.md)).

## Options considered

1. **Keep points-only, add a boxer column** — cheap, but can't represent a
   clinch's duration.
2. **A full v2 schema** — `who ∈ {A, B, Both}`, `kind ∈ {point, duration}`,
   numeric `t_start`/`t_end`, with a migration from v1 and undo/redo.

## Decision

**Adopt `labels.json` v2** (`labels_store.py`): each label carries `who`
(A/B/Both), `kind` (point or duration), and numeric `t_start`/`t_end` on the
`t_session_seconds` axis. A point has `t_end` null; a duration is placed by
pressing the number once to start and again to end. The store has undo/redo (each
mutating call records an inverse op). v1 files migrate automatically, writing a
`.v1.bak`. The shared team vocabulary is separate
([ADR-0010](adr-0010-app-settings-store.md) covers where config lives;
`label_vocab` stores `label_list.json` in the recordings folder).

## Consequences

- **Enables between-boxer analysis** — every event knows whose it is and whether
  it's an instant or a span.
- **No lossy string round-trip** — labels are numbers on the same axis as
  everything else, so they co-register with video/EEG/markers directly.
- **A schema version + migration path** means old sessions keep working (with a
  backup) rather than breaking.
- Delivered as part of the merged Review & Label build
  ([ADR-0008](adr-0008-merged-review-label.md)); the edit surface for the
  vocabulary is Settings. Verified by `tests/test_labels_v2.py`.

## Links

- Commit `8ef75cb` (labels.json v2 store: schema, v1 migration, undo/redo).
- [File formats — labels.json](../research-and-data/file-formats.md),
  [label-autosave flow](../developer-guide/key-flows.md#placing-a-label-autosave-undo).
