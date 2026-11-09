# ADR-0003 — Stream EEG to disk during recording

**What this page covers:** why EEG is written to disk in chunks as it arrives,
rather than buffered in RAM and saved at the end.

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

A recording can be long, and the machine can crash, be force-quit, or lose power
mid-session. If EEG were held in memory and only written when recording stops, a
crash would lose the **entire** EEG recording — the one signal the research is
actually about. The camera side was being made crash-safe at the same time
([ADR-0004](adr-0004-crash-safe-h264.md)); the EEG side needed the same
guarantee.

## Options considered

1. **Buffer in RAM, write on stop** — simplest, but loses everything on a crash.
2. **Write each BLE chunk synchronously in the callback** — crash-safe, but disk
   I/O in the BLE callback risks dropping samples under back-pressure.
3. **Stream to disk on a background writer thread** — the BLE callback only
   enqueues; a writer thread appends and flushes.

## Decision

**Stream EEG to disk during recording on a background writer thread.**
`_MuseDirectReader` writes the CSV header (fsync'd) on start; the BLE callback
does only a non-blocking `queue.put`; a background writer thread appends each
chunk, line-buffered, flushing at least once a second and fsync'ing periodically.
A hard kill leaves a valid CSV with everything up to the last flush.

## Consequences

- **A crash loses at most ~1 second of EEG**, not the whole recording.
- **The BLE callback never blocks on disk** — it copies the sample array (the
  producer buffer is reused) and enqueues; a full queue drops a chunk with a
  logged warning rather than stalling the read loop.
- Verified under `SIGKILL` mid-recording (`tests/test_eeg_streaming.py`).
- This is where the EEG `t_session_seconds` column is written
  (`= muse_timestamp − capture_t0_unix`), inheriting
  [ADR-0001](adr-0001-capture-time-clock.md)'s **Not yet verified on hardware**
  caveat about the muselsl clock source.

## Links

- Commit `c163a53` (stream EEG to disk during recording, crash-safe).
- [File formats — EEG CSV](../research-and-data/file-formats.md),
  [threading model](../developer-guide/threading-model.md).
