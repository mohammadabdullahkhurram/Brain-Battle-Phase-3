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
- **`docs/archive/ARCHITECTURE_AND_EVALUATION.md` and `docs/archive/history.md` reference
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
