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
(see [CLAUDE.md's Project situation](../project/status.md)). They are tracked in
`CLAUDE.md`'s "Deferred work" and `ROADMAP.md`'s "Deferred until hardware works".

| # | Item | What would close it |
|---|---|---|
| 0 | **EEG capture-time alignment is unverified.** EEG samples sit on the axis via `t_session = muse_timestamp − capture_t0_unix`; the arithmetic is unit-tested but the muselsl clock source + BLE latency are not confirmable without a band. Logged at every EEG session start (`eeg_module.py`). | A 2-band session with a known simultaneous event (buzzer/tap in view of a camera) — check the EEG artifact lands at the same `t_session` as the camera frame. Also discharges the co-registered export's between-brain skew. |
| 1 | **Eyes-on-glass playback never watched on real signals.** Synced playback + brain traces are verified headless (correct pixels, measured scrub perf, alignment arithmetic), but nobody has watched the tab render aligned playback on real data. | The first real capture session — open it in Review & Label and watch all four cameras + both traces follow the playhead; confirm a marker at a known physical event lines up in video and EEG. |
| — | **Raw Muse-S EEG from a sparring round has never been inspected** — noise floor, motion artifact, channel usability are all unknown. | A first real 5-minute sparring recording, before any further sync-precision or accelerometer/PPG work. |
| — | **Encoder "listed-but-no-hardware" fallback** (e.g. nvenc on a GPU-less box) is proven only by forced-probe-failure unit tests, never on real GPU-less hardware. | Running a recording/export on a machine whose listed hardware encoder can't actually encode. |

The generated demo session and simulation mode are the stand-ins for everything
here **except** the real-signal questions — they can't produce a real signal.

## Code-level tech debt (tracked in ROADMAP Phase 5)

- **Playback uses only the first sidecar row for a camera's start, not the full
  per-frame sidecar.** The drift camera (30 fps declared vs ~29.5 fps true) is a
  real, reproduced desync; using the full sidecar for playback timing is
  [ROADMAP](../project/roadmap.md) Phase 5, not hardware-blocked. (The playback
  clock in `pyav_player` already reads per-frame times correctly during review;
  the Phase 5 item is about the remaining alignment seams.)
- **`usb_camera` logs capture-rate drift but never surfaces it structurally** —
  a warning in the log, not a flag the UI or export can see.
- **Muse accelerometer and PPG are not recorded** — only EEG channels. Worth
  adding once the plain EEG is confirmed usable.
- **No per-channel EEG signal-quality view in Review** beyond the contact-quality
  heuristic — needed before anyone trusts the signal.

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
- **`docs/ARCHITECTURE_AND_EVALUATION.md` and `docs/project/history.md` reference
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
  [CLAUDE.md](../project/status.md) and
  [timing & accuracy](../research-and-data/timing-and-accuracy.md).
