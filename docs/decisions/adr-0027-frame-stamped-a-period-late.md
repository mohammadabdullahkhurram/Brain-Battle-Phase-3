# ADR-0027 — A camera frame read almost a period late (draft)

**What this page covers:** what happens when a camera frame is read almost a
whole frame period late, how often it happens, the options for fixing it, and
the recommendation to decide after the first test with real cameras.

- **Status:** Proposed (a draft for the owner; nothing is changed yet)
- **Date:** 2026-11-27

## Context

Each camera process stamps every frame **as it is read**, on the camera's
own steady frame clock (`timing.FrameClock`, ADR-0025): it counts how many
frame periods have passed since the last frame and moves the stamp on by that
many periods, pulled gently towards the read time. A frame read between about
¾ and 1 period late (11–17 ms at 60 fps, the camera process held up by a busy
computer) looks the same as the *next* frame read on time. The clock counts
one step too many and stamps that frame **one period (16.7 ms) late**. The
next frame proves it (it needs "0 steps") and the clock catches up from there.
Sometimes a short run of late reads stays a period behind until an on-time
read.

What it costs: that frame's time in the sidecar, `recording.xdf` and Review is
16.7 ms off — beyond the 10 ms goal between devices for that one frame. No
frame is lost (every frame is in the file), and the read time is kept in the
sidecar (`t_arrival_monotonic`). The buzzer test is barely touched: it fits
the camera's frame lattice around each tap and takes the median over five
taps.

**How often** (simulated cameras, `tools/soak_test.py`, staging heavy checks
of 2026-11-26, 12-minute + 3 × 1-minute recordings, 4 cameras at 60 fps, about
216 000 frames):

| Runner | Machine stalls (10 ms +) | Frames stamped a period late |
|---|---|---|
| GitHub Windows | 111 a minute | 43 (≈ 0.02 %) |
| GitHub macOS (cannot keep 60 Hz; not judged) | 4148 a minute | 9289 (≈ 4 %) |

`test_frame_clock` shows the same mechanism (about 1 % of frames when 3 % of
reads stall by up to 150 ms). On a lab computer that is not shared with other
virtual machines it should be far rarer — but nobody has measured that, and a
real camera's driver may deliver frames more or less evenly than the
simulation.

## Options

1. **Keep as is.** Simple, and already measured. A small share of frames
   (0.02 % on a noisy runner) is one period off; the soak counts them
   separately ("stamped a period late after a late read, not lost").
2. **The camera's own timestamps** (`CAP_PROP_POS_MSEC`). Some capture
   back-ends hand over the driver's time for each frame (Media Foundation on
   Windows, AVFoundation on a Mac). If they are real capture times on a steady
   clock, they remove the guess entirely: no frame is ever counted twice.
   Unknowns that only real cameras can answer: whether each back-end fills it
   in for a live USB camera at all (some return 0 or the read time), what clock
   it is on and how to map it to the capture clock (an offset fitted over the
   recording), whether it is steady at 60 fps with MJPG, and whether DirectShow
   (our Windows back-end) supports it.
3. **Stamp with hindsight.** Hold one sidecar row back and correct it when the
   next frame shows it was over-counted. Removes about 40 % of these (the
   single-frame cases, not the runs); costs one frame of delay in the sidecar
   and a little more code in the capture loop.
4. **Re-stamp after the recording.** Fit each camera's frame lattice from all
   its read times once the recording ends (both directions, like the buzzer
   test's `_steady_frame_times`), and rewrite the sidecar times. Fixes runs
   too; costs a pass over every sidecar at Save, and the live times (LSL, the
   live view) stay as they are now.

## Recommendation

**Decide after the first real-camera test.** Record one session with the real
cameras and look at three things: how often the soak's "stamped a period late"
count appears on the lab computer; whether `CAP_PROP_POS_MSEC` returns steady,
per-frame times on each camera and back-end; and the sidecar's read-time
spread. If the timestamps are good, option 2 is the clean fix. If they are not
and the count matters, option 4 (with 3 as a cheaper first step). If the count
is near zero on the lab computer, keep as is (option 1).

## Consequences (until decided)

- Nothing changes in the app. The known issue stays listed
  ([Known issues](../developer-guide/known-issues.md)), the soak keeps
  counting these frames separately, and `test_frame_clock` keeps its
  98.5 % bound.
- Related: [ADR-0024](adr-0024-timing-check-and-buzzer-calibration.md) (timing
  goal and buzzer calibration), [ADR-0025](adr-0025-each-camera-in-its-own-process.md)
  (each camera in its own process, `FrameClock`).
