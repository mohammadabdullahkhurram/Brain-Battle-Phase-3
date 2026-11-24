# Known issues & tech debt

**What this page covers:** everything currently deferred, hardware-blocked, or
known-stale — in one place, so a successor doesn't rediscover it. The research-
facing version of the hardware items is on the
[known limitations](../research-and-data/limitations.md) page; this page is the
developer view, and adds the code-level debt and the doc-drift found while
writing the Developer guide. Nothing here is fixed in documentation step D3 — it
is **listed, not fixed**.

## Hardware-blocked (can't be closed by writing code)

These need a working camera rig and Muse-S bands, which the project doesn't have
(see [AGENTS.md's Project situation](../project/status.md)). They are tracked in
`AGENTS.md`'s "Deferred work" and `ROADMAP.md`'s "Deferred until hardware works".

| # | Item | What would close it |
|---|---|---|
| 0 | **EEG capture-time alignment is unverified.** EEG samples sit on the axis via `t_session = sample_time − capture_t0_unix`, both from the one capture clock; the arithmetic is unit-tested but how late Bluetooth delivers a sample is not confirmable without a band. Logged at every EEG session start (`eeg_module.py`). | A 2-band session with a known simultaneous event (buzzer/tap in view of a camera) — check the EEG artifact lands at the same `t_session` as the camera frame. Also discharges the co-registered export's between-brain skew. |
| 1 | **Eyes-on-glass playback never watched on real signals.** Synced playback + brain traces are verified headless (correct pixels, measured scrub perf, alignment arithmetic), but nobody has watched the tab render aligned playback on real data. | The first real capture session — open it in Review & Label and watch all four cameras + both traces follow the playhead; confirm a marker at a known physical event lines up in video and EEG. |
| — | **Raw Muse-S EEG from a sparring round has never been inspected** — noise floor, motion artifact, channel usability are all unknown. | A first real 5-minute sparring recording, before any further sync-precision work. |
| — | **The movement and pulse sensors have never been received from a real band**, and the pulse sensor depends on an untested headband mode (preset 50). The extra Bluetooth traffic with two bands is also untested. | A short real session with them switched on in Settings; if the link gets less stable, switch them off there. |
| — | **Live LSL streams have only met simulated headbands** and same-computer receivers. | Run LabRecorder beside a real session; try a second computer on the lab network. |
| — | **Encoder "listed-but-no-hardware" fallback** (e.g. nvenc on a GPU-less box) is proven only by forced-probe-failure unit tests, never on real GPU-less hardware. | Running a recording/export on a machine whose listed hardware encoder can't actually encode. |

The generated demo session and simulation mode are the stand-ins for everything
here **except** the real-signal questions — they can't produce a real signal.

## Not verified on Windows or on a network drive

The app is developed on macOS. Windows is covered by the automatic test run
with simulated devices, and by nothing else: cameras, Bluetooth, hardware
encoders, Explorer, display scaling and a real shared drive are all untried
there. The list is in
[Cross-platform & CI](cross-platform-and-ci.md#never-tried-on-a-real-windows-machine).
These are listed, not fixed, because they need a real machine.

**The automatic test run is green on macOS and Windows** since 2026-11-17. The
causes that were fixed (a coarse clock on Windows, non-atomic appends on
Windows, a test that could hang, LSL tests picking up each other's streams) are
listed in [Cross-platform & CI](cross-platform-and-ci.md#fixed-on-2026-11-17-ci-green-on-both-systems).
The two Windows scripts that failed now and then on the first attempt
(`test_shared_folder` under a reader that never lets go, `test_smoke` at
shutdown) were fixed at the cause on 2026-11-23 — see
[the two first-attempt failures](cross-platform-and-ci.md#the-two-first-attempt-failures-on-windows-fixed-2026-11-23).

## Open files ("Too many open files") — fixed in 3.1.0-dev.3, with a cost

Up to `v3.1.0-dev.2`, every visit to New recording used up 33 more of the 256
open files macOS allows a program, so the second or third recording in a row
failed to write its manifest and markers — and still showed a clean Saved
screen. The cause, the measurements and the fix are in
[ADR-0022](../decisions/adr-0022-lsl-outlets-open-once.md). What is left:

- **The LSL library leak is fixed upstream.** liblsl 1.17.4 (pylsl 1.18.1)
  kept 3 descriptors for every outlet it destroyed; liblsl 1.18 (pylsl 1.18.7,
  pinned since 2026-11-17) keeps none — measured, see
  [ADR-0022's update](../decisions/adr-0022-lsl-outlets-open-once.md#update-2026-11-17-a-newer-liblsl-no-longer-leaks).
  The app still opens each outlet once and reuses it. The bundled liblsl is a
  pre-release build (`1.18.0.b7`), measured on macOS only.
- **About 143 descriptors stay held** for the rest of the run once New
  recording has been opened (11 streams × 13), and the streams stay listed on
  the network, silent, until the app quits.
- **A recording peaks at about 183 of 256.** The app raises its own limit to
  4096 at start-up, so this only matters if that is refused. The number of
  sockets per stream depends on the computer's network interfaces, so another
  machine may measure a few more or fewer; run
  `python3 tools/measure_descriptors.py` to see.
- **Real headbands and cameras were not measured** (hardware is broken). A real
  Bluetooth connection and a real camera use descriptors the simulated ones do
  not; expect a handful each, not dozens.
- `tests/test_descriptors.py` runs four recordings in a row under a 256 limit
  and fails if the counts do not come back to the first visit's.

## Camera frames lost on a computer short of memory (explained; re-measure)

The "0.15 % of frames lost while the computer itself was not paused" (2026-11-23)
is explained (2026-11-24, `tools/soak_test.py --trace`): the development Mac
(8 GB) was **out of memory** — 15.8 GB of swap in use, ~60 MB of free pages,
thousands of pages decompressed a second with nothing else running. During
every lost-frame stall the camera process used ~5 % of one core: it was not
working, it was **waiting for memory** (fresh pages, compressed pages), and so
were ffmpeg and the app at the same instants. The bare stall probe holds
almost no memory, so it could not see these stalls and they were counted as
"outside the machine's stalls".

What changed: the soak now runs a second probe that holds 48 MB (memory
stalls); the camera path allocates far less at the worst moments (LIFO frame
pool: 3–4× fewer page-faulting frames; ffmpeg launched by the writer thread
and the encoder name passed in — the reader no longer freezes ~0.5 s at the
start of every recording); all camera-process threads run at the reader's
priority. Not ruled out: some remaining single-frame losses outside both
probes' stalls (14–31 per 1-minute recording, 4 cameras). The full soak on that
Mac was not a valid measurement — it swapped in 11.4 GB during the 12-minute
recording (`docs/reviews/2026-11-soak.txt`). **Next:** re-run the full soak
on a computer that is not swapping (browser closed, or the lab computer).
Recording is complete either way (frame times are exact, gaps are in
`timing_quality.csv`); EEG is complete.

## Code-level tech debt (tracked in ROADMAP Phase 5)

- **Other labellers' labels arrive by polling** (every five seconds while a
  recording is open), not by a file-change notification — simple and reliable
  on network drives, at the cost of a short delay.
- **A labeller is only a typed name.** A misspelling makes a second labeller,
  and nothing stops someone typing another person's name. Real accounts are
  Phase 7.
- **`record_flow.py` keeps the names `ConnectStep` / `SetupStep` and the
  aliases `STEP_CONNECT` / `STEP_SETUP`** for the two halves of the one "Get
  ready" screen. Harmless, but the names describe the old four-step flow.
- **Code comments still cite the old DESIGN section numbers** in places
  (Review & Label was §6 and is now §5).

- **The optional combined 2×2 export video still aligns cameras by their first
  frame only.** Playback (`pyav_player`) and `recording.xdf` both use every
  frame's own time, so a camera running at 29.5 fps instead of 30 is handled
  there; the combined video would slowly drift for such a camera. It is a
  preview, off by default.
- **`SessionController` counts one-second timer ticks** rather than comparing
  against the clock, so if the UI thread stalls for several seconds a phase runs
  that much long. The markers always record when a phase *really* started, so
  the data stays right; only the on-screen schedule would slip.
- **No per-channel EEG signal-quality summary for a whole recording** beyond the
  contact-quality log and looking at the EEG display — worth adding once real
  recordings exist to calibrate it against.
- **A non-editable install still cannot find `assets/` and `docs/`** (ROADMAP
  Phase 6).

Closed in 3.1.0-dev: capture-rate drift is now reported (Saved screen "Worth
checking", and `measured_fps` in each XDF video stream); the headbands' movement
and pulse sensors can be recorded.

## Documentation drift found while writing the Developer guide

Surfaced while documenting. The first three were cleaned up in a short follow-up
after D3; the fourth is intentional and stays:

- **Resolved — `ROADMAP.md` Phase 5's "Delete dormant `sync_engine.py`" item.**
  `sync_engine.py` was removed with the Review & Label merge (`c21fac0`,
  2026-11-01); the ROADMAP item is now marked DONE and struck from the open list.
- **Resolved — `sync_beep.py`'s module docstring.** It used to claim the beep
  left a spike "that the audio-based cross-correlation sync engine
  (`sync_engine.py`) can lock onto". The docstring was rewritten to describe a
  round bell for the boxers only, with no role in timing or alignment and an
  on/off toggle in Settings.
- **Resolved — `README.md`'s stale architecture.** The old three-tab description
  and the per-module tables (naming removed modules like `usb_camera_slot.py`)
  were removed; the README is now a short, accurate landing page that points at
  these docs. The full non-technical rewrite remains ROADMAP Phase 6.
- **`docs/archive/architecture-and-evaluation.md` and `docs/archive/history.md` reference
  removed modules** (`SyncReviewTab`, `live_session_tab.py`, `vlc_module.py`,
  etc.). This is **intentional** — both are explicitly historical documents with
  a note at the top saying so; they are not drift, but a reader should know the
  names they mention no longer exist in the tree.

## Not debt, but worth knowing

- **The per-phase white flash is not perfectly simultaneous across cameras** —
  each camera captures on its own phase, so the first frame at/after a marker can
  land up to one frame period (~34 ms) apart. This is expected and correct;
  `tests/test_flash_sync.py` asserts the spread stays within one frame.
- **Sync precision beyond "tens of ms" is deliberately not pursued** until
  whoever owns the synchrony analysis method confirms it's needed — see
  [AGENTS.md](../project/status.md) and
  [timing & accuracy](../research-and-data/timing-and-accuracy.md).
