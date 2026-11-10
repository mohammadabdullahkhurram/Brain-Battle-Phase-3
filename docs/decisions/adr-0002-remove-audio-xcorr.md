# ADR-0002 — Remove audio cross-correlation sync

**What this page covers:** why the audio cross-correlation alignment path was
deleted entirely, leaving one alignment method.

- **Status:** Accepted
- **Date:** 2026-07-07

## Context

An earlier version aligned video sources by **audio cross-correlation**: extract
each clip's audio, correlate to find the delay, and offset accordingly. But
in-app cameras record **no audio track** (they write video-only via ffmpeg), so
there is no signal to correlate. The code was honest about this — with fewer than
two audio-bearing clips it recorded `method = simultaneous_start` and offset 0 —
but offset 0 is an **assumption, not a measurement**, and for in-app captures
*every* session took that path. Meanwhile [ADR-0001](adr-0001-capture-time-clock.md)
introduced capture-time timestamps that make alignment a lookup.

## Options considered

1. **Keep both paths** — capture-time stamps for in-app sessions, audio xcorr for
   externally recorded footage.
2. **Remove audio xcorr entirely** — one alignment method for everything that
   comes from the recording app.

## Decision

**Remove audio cross-correlation.** All sessions now come from the recording app,
which writes `capture_clock` + per-source timestamps, so xcorr is obsolete. Delete
`MainEditorSyncWorker`, the manual-upload branch of the align path,
`sync_utils/audio_analysis.py`, and `sync_utils/eeg_video_sync.py`, along with the
tests that only existed for the xcorr path. One alignment method: capture-time
stamps.

## Consequences

- **Simpler, honest alignment** — no "confidence" badge that measured only
  internal consistency, no two-codepath alignment that could disagree.
- **Loses the ability to align externally recorded (audio-bearing) footage** —
  acceptable, because the product records its own sources; external footage isn't
  part of the pipeline.
- Cleared the way for the single-playhead playback model
  ([ADR-0005](adr-0005-pyav-playback.md)); the remaining `sync_engine.py`
  dataclasses were later removed too when the merged Review & Label screen landed
  ([ADR-0008](adr-0008-merged-review-label.md)).
- The round beep's old justification ("leaves a spike the xcorr engine locks
  onto") no longer applies — it is now purely a round bell (a stale `sync_beep.py`
  docstring is noted in [known issues](../developer-guide/known-issues.md)).

## Links

- Commit `af385bf` (remove obsolete audio-xcorr sync; single capture-clock path).
- [history.md](../project/history.md) (STEP 4),
  [ADR-0001](adr-0001-capture-time-clock.md).
