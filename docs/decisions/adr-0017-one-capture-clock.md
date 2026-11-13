# ADR-0017 — One capture clock, seen three ways

**What this page covers:** why every timestamp the app records now comes from
the computer's monotonic clock, with Unix time and LSL time derived from it by
fixed offsets — a refinement of the capture-time clock in
[ADR-0001](adr-0001-capture-time-clock.md).

- **Status:** Accepted (refines ADR-0001)
- **Date:** 2026-11-13

## Context

ADR-0001 put cameras, markers and EEG on one session axis by recording a shared
origin at the start of a recording: a monotonic reading and a wall-clock reading
taken at the same instant. Camera frames and markers were then stamped with the
monotonic clock, but EEG samples were stamped by muselsl with the **wall clock**
(`time.time()`), and mapped onto the axis by subtracting the wall-clock origin.

The wall clock can be corrected while a recording is running — by network time
synchronisation, or by someone changing the time. A correction of even a fraction
of a second mid-session would silently shift every later EEG sample against the
video. Adding LSL ([ADR-0015](adr-0015-lsl-and-xdf.md)) would have introduced a
third independently-read clock.

## Decision

**There is one clock: `time.monotonic()`.** `capture/clock.py` measures, once
per run of the app, the offset from it to the wall clock and to the LSL clock,
and from then on:

- `unix_now()` — "monotonic plus a constant" — is handed to the headband reader
  as its time source, so EEG timestamps look like Unix time but cannot step;
- `mono_to_lsl` / `unix_to_lsl` convert any stamp to LSL time for the live
  outlets and `recording.xdf`;
- `session_origin()` captures a recording's start on all three scales at once,
  and the manifest's `capture_clock` now stores `t0_lsl` beside `t0_monotonic`
  and `t0_unix`.

Because the three scales are the same clock seen through constant offsets,
converting between them is exact; there is no second clock to drift against.

## Consequences

- **A wall-clock correction during a recording no longer moves EEG against
  video.** The absolute `t_unix` values are only as right as the wall clock was
  when the app started — they are for humans; analysis uses `t_session_seconds`.
- **`t_session_seconds` is unchanged in meaning** and old recordings read exactly
  as before.
- **What this does not address:** how long Bluetooth takes to deliver a sample
  before the computer stamps it. That is still the unverified part of EEG timing
  ([Timing & accuracy](../research-and-data/timing-and-accuracy.md)), and by the
  project's standing rule no effort goes into tighter precision until the
  analysis method says it matters.
- `tests/test_xdf.py` checks that the app's LSL time matches `pylsl.local_clock()`
  to well under a millisecond and does not drift.

## Links

- [ADR-0001](adr-0001-capture-time-clock.md),
  [ADR-0015](adr-0015-lsl-and-xdf.md),
  [Timing & accuracy](../research-and-data/timing-and-accuracy.md).
