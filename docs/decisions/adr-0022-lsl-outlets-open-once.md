# ADR-0022 — LSL streams are opened once per run and reused

**What this page covers:** why the app stopped closing its live LSL streams
when you leave New recording, why they are published over IPv4 only, and what
that fixed ("Too many open files").

- **Status:** Accepted (awaiting the owner's review with the rest of `develop`)
- **Date:** 2026-11-16

## Context

Every open file, network socket and pipe to a helper program uses one
*descriptor*, and the operating system caps how many a program may hold. macOS
starts programs with a cap of **256**.

Measured with `tools/measure_descriptors.py` (4 simulated cameras, 2 simulated
headbands, LSL on, three recordings in a row), before this decision:

| Point | 1st | 2nd | 3rd |
|---|---|---|---|
| New recording open, devices connected | 207 | 241 | 274 |
| Recording | 227 | 260 | 293 |
| After leaving New recording | 42 | 75 | 108 |

Two things were wrong:

1. **A leak of 33 descriptors per visit.** The app publishes 11 LSL streams
   (EEG, accelerometer and gyroscope for each boxer, the markers, one per
   camera). It destroyed them when you left New recording and made new ones
   when you came back. The LSL library (liblsl 1.17.4, the one `pylsl` 1.18
   ships) does not give back everything an outlet opened: **3 descriptors stay
   open for every outlet destroyed**, measured in isolation with nothing but
   `pylsl`. 11 × 3 = 33. App code cannot close them.
2. **A high cost even without the leak.** One outlet holds 17 descriptors
   (11 sockets, 6 pipes), so the streams alone took 187 of the 256, and the
   first recording already peaked at 227.

The second recording in a row crossed 256. From then on anything that needed a
new file failed: the manifest, `phase_markers.csv`, new LSL streams. The
failures were only written to the log, so the Saved screen still showed a clean
success.

## Decision

- **Each LSL outlet is opened once per run of the app and reused.** They live
  in a module-level store in `capture/lsl_outlets.py`. `LslPublisher.close()`
  stops publishing and hands the outlets back; the next publisher picks the
  same ones up. An outlet is replaced only when its description has to change
  (a different headband on the same boxer), which costs 3 descriptors each time
  it happens.
- **Streams are published over IPv4 only** (`[ports] IPv6 = disable` in
  `support/lsl_api.cfg`). One outlet then holds 13 descriptors instead of 17.
- **The app raises its own open-file limit at start-up**
  (`support/resource_limits.py`, to 4096 or the system's hard limit if that is
  lower). This is a safety net, not the fix: the regression test runs *without*
  it, under 256.
- **A write that fails is shown.** A failed manifest, marker or history write
  puts a red banner on the Recording screen and the Saved screen says
  "Recording saved, but with problems" instead of showing a clean success.

After (same measurement, under a real 256 limit):

| Point | 1st | 2nd | 3rd |
|---|---|---|---|
| New recording open, devices connected | 163 | 163 | 163 |
| Recording | 183 | 183 | 183 |
| After leaving New recording | 151 | 151 | 151 |

## Consequences

- **The streams stay listed after you leave New recording**, until the app
  quits. They carry no data while nothing is being captured. LabRecorder will
  show `BrainBattle_…` streams as available whenever the app has been to New
  recording once. Previously they disappeared.
- **Switching "share live signals" off in Settings** stops the app publishing
  from the next visit to New recording; streams that were already opened in
  this run stay listed, silent, until the app is restarted.
- **About 143 descriptors stay held for the rest of the run** once New
  recording has been opened (11 outlets × 13). That is the price of not
  leaking; it is constant, not growing.
- **IPv4 only.** Programs on the same computer or the same network find the
  streams as before. A network that carries *only* IPv6 would not; set your own
  `LSLAPICFG` to override the app's file (the app never replaces one that is
  already set).
- A recording peaks at about 183 descriptors, which fits under 256 with room to
  spare, and no longer depends on how many recordings came before it.

## Alternatives considered

- **Only raise the limit.** Hides the leak: at 33 per visit a long day of
  recordings still climbs without end, and the limit cannot be raised on every
  system.
- **Close the leaked descriptors ourselves.** They belong to the library;
  there is no handle to them, and closing descriptors by number would risk
  closing someone else's.
- **Run LSL in a helper process** that can be thrown away. Works, but adds a
  process, a data channel and a failure mode to the recording path for a
  convenience feature.
- **Fewer streams** (one stream for all four cameras). Changes the published
  layout that `recording.xdf` mirrors ([ADR-0015](adr-0015-lsl-and-xdf.md)).
- **Wait for a fixed liblsl.** Not checked against newer builds; the app should
  not depend on it either way.
