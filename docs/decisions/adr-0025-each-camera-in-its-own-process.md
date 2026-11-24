# ADR-0025 — Each camera captures in a process of its own

**What this page covers:** why each camera's capture loop (read → timestamp →
ffmpeg → timestamp sidecar) runs in its own small process instead of a thread
of the app, what was measured to decide it, and what frame loss is left.

- **Status:** Accepted
- **Date:** 2026-11-19

## Context

The soak test (`tools/soak_test.py`: 4 simulated cameras at 60 fps + 2
headbands) lost 0.5–4 % of camera frames, in bursts of 100–500 ms that hit all
four cameras at the same instant. A camera asked for a 1-frame driver buffer
(ADR-0024: the newest frame, a constant delay) loses one frame for every
~17 ms its reader thread is not run, so a burst means the reader was not run.

What was measured (2-minute soaks, `--diagnose`; `support/stall_probe.py`):

| Experiment | Frames lost | Worst stall of the app |
|---|---|---|
| as it was (cameras as threads) | 1.4 % | 269 ms |
| previews off | 1.1 % | 285 ms |
| 30 fps | 0.3 % | 268 ms |
| encoder off (null sink) | 0.9 % | 259 ms |
| minimal frame source (no drawing) | 0.6 % | 253 ms |
| all of the above + no screen redraws | 0.8 % | 231 ms |
| thread switch interval 1 ms | 1.1 % | 287 ms |
| camera threads at high OS priority | 1.6 % | 282 ms |

- **Not garbage collection:** the longest collection was 9.6 ms, and (almost)
  no gap overlapped one.
- **Not CPU load:** the machine was ~30 % busy, the app ~⅔ of one core.
- **No single slow Python call:** no main-thread method used more than 36 ms
  of CPU while recording. Stacks dumped *during* stalls (a C watchdog,
  `faulthandler`, standing in for `py-spy dump`, which needs root on macOS)
  showed every thread at an ordinary place.
- **The machine pauses processes:** a bare Python script — four 60 Hz threads,
  no app, no Qt — also woke 30–100 ms late a few times a minute, inside and
  outside the sandbox, at the same instants as the app's stalls.
- **The app made each pause much longer:** in the same 110 s, a separate
  4-camera process at high priority stalled at most 32 ms while the app
  stalled 275 ms and lost 288 frames. With ~20 Python threads sharing one
  interpreter lock, when the operating system pauses the thread that holds
  it, every thread waits, and the pause grows.

## Decision

1. **Each camera runs in its own process** (`capture/camera_worker.py`,
   multiprocessing "spawn"): the reader, the ffmpeg writer, a control thread
   and a sender thread. Nothing in the app's main process — the screens, a
   slow save, a timer — can delay capture. Camera capture never depends on the
   UI thread.
2. `usb_camera.USBCameraThread` keeps its interface and signals. It starts the
   process, passes commands (start/stop recording, preview size) and turns the
   reports (frame times + buzzer-test energy in 50 ms batches, previews ≤ 15 a
   second at 320 px wide, recorded-frame times for LSL, stalls, errors,
   warnings for the log) into the same signals and numbers as before.
3. The capture clock is `time.perf_counter()`, one system-wide clock, so frame
   times from a camera process are on the same timeline as everything else.
4. Capture threads ask the OS to run them promptly
   (`platform_utils.raise_thread_priority`: macOS `USER_INTERACTIVE`, Windows
   `THREAD_PRIORITY_HIGHEST`).
5. A camera process whose pipe to the app breaks (the app crashed) stops and
   **finalises its video** before exiting.
6. `BRAIN_BATTLE_CAMERA_PROCESS=0` (or `USBCameraThread(isolated=False)`) runs
   the same loop as a thread — used by a test that patches the encoder, and
   by the soak test's `--in-process` comparison.

## Result

Full soaks (12-minute recording + three 1-minute recordings, 4 cameras at
60 fps) on the development Mac, frames **lost** = absent from the file
against the camera's own steady clock. A bare 4-thread process ran beside
each soak to record how noisy the machine was at the time:

| Run | Machine noise (bare process, ~19 min) | 12-min recording, lost per camera | 1-min recordings, lost per camera |
|---|---|---|---|
| cameras as threads (before) | — | 606–608 (1.4 %) | 10–256 |
| camera processes, quiet machine | 15 stalls, worst 47 ms | 3–8 (0.01–0.02 %) | 0, 0, 0 |
| camera processes, an earlier run | 40 stalls, worst 150 ms | 85–91 (0.2 %) | 0, 3–4, 0 |
| camera processes, very busy machine | 561 stalls, worst 359 ms | ~3000 (6.7 %) | 21–522 |
| camera processes + frame-clock stamps, the committed run | 41 stalls, worst 369 ms | 152–173 (0.4 %), mostly one burst at 395–402 s | 0; 0–1; 1–4 |

So the loss now follows the machine's own pauses rather than the app's.
That run's output was `docs/reviews/2026-11-soak.txt` (now replaced by the 2026-11-24 run, see below); in it `stamps_off_clock` is 0 on every camera in every recording (every late read was put back on the camera's clock).

(The soak first counted every interval longer than 1.5 frame periods as lost
frames. A frame read late and the next one on time give one long interval and
one short one with nothing lost, so the tool now reports **lost** and
**late** separately.)

## What is left

1. **A few frames lost on a busy machine** (3–8 per camera in 12 minutes on
   the development Mac). The operating system still pauses whole processes
   now and then — a bare process on that Mac misses a 60 Hz deadline by
   25–150 ms a few times a minute — and a pause longer than one frame period
   loses a frame with a 1-frame buffer. No change inside the app can stop
   that.
2. **Frames read late** — fixed after this ADR was first written. About 0.6 %
   of frames in the 12-minute recording were read more than half a period
   late, so their read times were 10–35 ms late. Each frame is now stamped on
   the camera's own steady frame clock (`timing.FrameClock`): a small tracker
   of the on-time edge, with frame numbers counted from the previous frame
   (two reads less than 1.5 periods apart are consecutive frames). In a
   2-minute soak, every interval between stamps was a whole number of frame
   periods (`stamps_off_clock` 0 on all four cameras); on a simulated busy
   reader, ≥ 98.5 % of stamps lie within 3 ms of the true frame clock, against
   up to a period for read times. The read time is kept in the sidecar's
   `t_arrival_monotonic`. (A first version — one robust straight line
   through every frame's number and read time, as for the headbands — was
   more precise when it worked but could bend itself away after a wrong frame
   count, so it was not used.)

Ways to remove the rest of the losses, both needing real cameras to confirm:

- a **deeper driver buffer** with the camera's own frame timestamps
  (`CAP_PROP_POS_MSEC` where the driver supplies them), so a paused reader
  catches up without losing frames;
- a **quieter recording computer** (no browser, no background indexing) —
  the cheapest thing to try at the first real session.

## Frame loss on a computer short of memory (2026-11-24)

The open item "frames lost while the computer itself was not paused" was
traced with `tools/soak_test.py --trace` (per-frame timing inside each camera
process). Experiments, 2-minute soaks at 60 fps, lost frames / of which outside
the bare probe's stalls:

| Variant | Lost | Outside |
|---|---|---|
| as it was | 190 · 89 | 46 · 37 |
| minimal source (no drawing per frame) | 126 | 49 |
| encoder off (null sink) | 109 | 43 |

So neither the simulator nor ffmpeg was the cause. The trace showed: no gen-2
collection in any camera process (worst collection 9–12 ms, never during a
loss); sidecar writes ≤ 17 ms; the command pipe is a blocking read in its own
thread. Losses came in bursts in all four processes at once, and during every
slow iteration **the whole camera process used ~5 % of one core** (e.g. 150 ms
wall, 5 ms CPU) — it was waiting, not working. The development Mac (M1, 8 GB)
had 15.8 GB of swap in use and decompressed thousands of pages a second while
idle: a process touching fresh or compressed memory waits for the kernel, and
slow iterations carried 95 page faults — one fresh 1.5 MB frame — whenever the
12-buffer frame pool ran dry behind a backlog to ffmpeg (262 per camera in 2
minutes). The bare stall probe holds almost no memory and never saw these
stalls. (Two other ideas were tested and rejected: a quadratic copy in
`Connection.send` — a blocking socket write sends everything at once; and
pre-filling a large pool — 80 MB per camera got compressed too and made it
worse: 2 350 faulting frames per camera.)

Changes: (1) the frame pool is last-in-first-out, so two or three buffers stay
hot (faulting frames 262 → 62–109 per camera); (2) the writer thread launches
ffmpeg and the app passes the probed encoder name (`BRAIN_BATTLE_REC_ENCODER`),
so the reader no longer freezes ~0.5 s (≈ 30 frames) when a recording starts;
(3) every thread of a camera process runs at the reader's priority (a GIL
holder paused on an efficiency core would hold the reader up; a precaution —
its effect could not be separated from the machine's); (4) the soak judges
against a second probe that holds 48 MB (memory stalls) as well as the bare
one. On that Mac, judged by both probes, 14–31 frames per 1-minute recording
(4 cameras) were still lost outside any stall; the full soak there swapped in
11.4 GB during the 12-minute recording and is not a valid measurement
(`docs/reviews/2026-11-soak.txt`). It must be re-run on a computer that is not
swapping.

## App Nap and timer coalescing (tested 2026-11-19)

Could macOS App Nap or timer coalescing cause the remaining pauses? Two A/B
tests, 2-minute soaks alternated so machine noise cannot fake a result, with
the bare noise probe beside each run:

| Test | Frames lost | Machine noise (late wakes) | Lost per noise unit |
|---|---|---|---|
| `defaults write org.python.python NSAppSleepDisabled -bool YES` — off (2 runs) | 10 + 2993 | 5 + 509 | ~5.8 |
| … App Nap disabled (2 runs) | 0 + 245 | 0 + 55 | ~4.5 |
| latency-critical activity on (4 runs) | 300 | 69 | 4.3 |
| … off (4 runs) | 780 | 194 | 4.0 |

Losses follow the machine's noise the same way with or without it: App Nap /
timer coalescing is **not** the cause here. (The `defaults` change also
applied to the noise probe — same bundle id — so the second test, which
affects only the recording processes, is the cleaner one. The `defaults`
change was removed afterwards.)

The activity is **kept anyway**: while recording, the main process
(`keep_awake.KeepAwake`) and every camera process (`CaptureLoop`) hold
`NSProcessInfo.beginActivityWithOptions(UserInitiated | LatencyCritical)`
(`keep_awake.LatencyActivity`), released when recording stops. It costs
nothing, and macOS normally naps an app whose windows are hidden or covered —
which the offscreen soak cannot reproduce, so the test cannot rule out a
benefit in real use. Windows needs nothing beyond `SetThreadExecutionState`.

## Consequences

- One more process per camera while Get ready / Recording is open (they exit
  when the screen is left); `tools/measure_descriptors.py` and
  `tests/test_descriptors.py` check they all go.
- The app's main process uses much less CPU and memory while recording
  (about 19 % of a core and 170 MB, against 66 % and 350 MB).
- Simulation settings reach the camera processes through environment
  variables (`BRAIN_BATTLE_SIMULATE_*`, `BRAIN_BATTLE_SIM_PICTURE`);
  scheduled buzzer taps are forwarded; the simulated cameras' true delivery
  delays come back with each frame (`test_timing` needs them).
- A packaged app (Phase 6) must call `multiprocessing.freeze_support()` —
  `app.main()` already does.
