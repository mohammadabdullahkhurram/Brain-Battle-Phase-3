# Playback proposal — one approach for the merged Review & Label screen

Phase 3, step 1. Investigated + prototyped; **not integrated**. The merged
Review & Label screen (DESIGN §6) needs ONE video playback approach to replace
today's two different stacks. This compares three and recommends one.

Measured on: MacBook Pro (Apple M1, 8 cores), against a freshly generated
160 s demo session (4× 960×540 fragmented H.264, including the deliberately
drifting camera POV 01 — 29.5 fps true vs 30 fps declared container). The
Option C prototype is `scratchpad/playback_proto_c.py` (throwaway).

## The options

- **A — python-vlc, vmem→QPainter** (today's Review tab): four libVLC players
  decode to ctypes buffers painted by `QPainter`. Plays each container at its
  **declared/nominal fps**; seek via `set_time`.
- **B — Qt Multimedia `QMediaPlayer`** (today's Labelling tab): four players,
  Qt's ffmpeg backend. Also plays at **nominal fps**; seek via `setPosition`.
- **C — app-driven decode with PyAV**: decode each camera's H.264 file; for
  playhead time `t`, show the frame whose sidecar `t_session_seconds` is the
  latest `≤ t`. The app's own clock is the single playhead. Our videos have no
  audio, so there is no A/V-sync problem — the app clock drives everything.

## The deciding issue: correctness on the drifting camera

A and B play each file at its **nominal 30 fps**, so frame *i* is shown at local
time *i*/30 — but the drift camera actually captured frame *i* at *i*/29.5. The
error grows without bound:

| Camera | at ¼ | at ½ | at end (160 s) |
|---|---|---|---|
| **A/B** drift POV 01 | 726 ms | 1393 ms | **2725 ms** |
| **A/B** steady SIDE 01 | 209 ms | 209 ms | 209 ms (constant start offset) |
| **C** — every camera, every playhead | — | — | **0–25 ms** (≤ 1 frame) |

A and B are off by ~2.7 s on the drift camera at the end of a *160 s* session,
and worse on longer ones — exactly the desync the owner saw eyes-on-glass. This
is structural: A/B have no way to use the per-frame sidecar times; they only
know the nominal container rate. **C reads the real per-frame `t_session`, so
it is correct for every camera by construction**, drift included.

## Option C — measured

All from `scratchpad/playback_proto_c.py` on the 160 s demo:

1. **Correctness** — at playheads 5/40/80/120/158/159.9 s, every one of the 4
   cameras showed the frame whose true `t_session` was within **one frame**
   of the playhead (worst 24.5 ms), **including the drift camera at the end**
   (9.2 ms behind at 159.9 s).
2. **Seek correctness** — a seek-to-keyframe + decode-forward landed on the
   *exact* target frame: pixel-identical to a from-the-start ground-truth
   decode, at frames 0/100/1000/2500 and the drift camera's last frame.
3. **Frame stepping** — forward and backward both produce the adjacent frames;
   deterministic (same index → same pixels).
4. **Seek latency** (keyframes every 1 s, so ≤ ~30 frames to decode forward):
   **median ~5 ms, p90 ~7 ms, max ~8 ms** per camera. Instant to the user.
5. **4-stream 1× playback**: produced all-4-camera frame sets at **682 sets/s**
   flat-out — **22.7× the 30/s needed for smooth 1×**. Decode is multithreaded
   (PyAV `thread_type=AUTO`). At real-time 30 fps that is ≈ **0.17 of one core
   (~2 % of the 8-core M1)**; peak RSS **167 MB** for all four decoders.
6. **0.25× / 0.5×** need 7.5 / 15 unique frames per second — far below 1× load,
   trivially smooth.

## Packaging / install burden

| | System dependency | Ships its own ffmpeg? | Windows |
|---|---|---|---|
| **A** python-vlc | **Requires the VLC desktop app** (libVLC) installed | no — uses system libVLC | needs VLC installed |
| **B** Qt Multimedia | PyQt6's bundled ffmpeg backend (no VLC) | yes (via PyQt6) | PyQt6 wheel |
| **C** PyAV | **none** | **yes — PyAV bundles ffmpeg libs** (`av` wheel, ~45 MB) | PyAV wheel |

For the Phase 6 "double-click app for non-technical users" goal, **A's
requirement that the user install VLC is the worst fit**. C (and B) bundle
everything in a pip wheel. C removes the VLC dependency that Review playback
currently carries.

## Code size / what changes

- **Delete** both current stacks: Option A ≈ **230+ lines** in
  `brain_battle_main.py` (`VideoSurface`, `_load_video_vmem`/`_hwnd`, the vmem
  ctypes buffers + lock/unlock/display callbacks, the native-surface z-order
  hacks `make_opaque_toolbar`/deferred `raise_()`, the `set_time`/poll glue);
  Option B = **all 391 lines** of `vlc_module.py` (`VLCVideoSlot` +
  `SyncPlaybackController`). ≈ **~620 lines across two code paths** gone.
- **Add** one C-based player: a `PyAVCamera` per tile (open + sidecar +
  seek/decode, ~80 lines), a 2×2 grid painting `bgr24` ndarrays via QPainter or
  a pyqtgraph `ImageItem` (the live-camera path already uses `ImageItem`), and
  one `QTimer`-driven playhead clock. Estimate **~200–300 lines, one path**.
- Adds `av` to `requirements.txt` (bundled ffmpeg); removes the VLC
  system-dependency note for review playback.

## EEG + timeline stay locked to the playhead

C's biggest structural win: **there is one clock — the app's playhead `t`**. On
each tick the player sets every camera to its frame-for-`t`, calls
`EEGReviewWidget.set_playhead(t)` (already exists), and moves the timeline
cursor to `t`. No negotiating four VLC clocks, no `axis_shift`/`residuals_ms`
seek arithmetic, no drift heartbeat. Video, EEG, and timeline are the same
number by construction — which is also what makes the DESIGN §6 "playhead fixed
in the centre" EEG view and frame-accurate labelling straightforward.

## Recommendation

**Adopt Option C (PyAV, app-driven decode).** It is the only option that is
*correct* on the drift camera (0–25 ms vs A/B's growing multi-second error),
it gives frame-accurate stepping and ~5 ms seeks that A/B can't, it is the
lightest to package (bundles ffmpeg, drops the VLC dependency), it collapses
two playback stacks (~620 lines) into one, and it makes video/EEG/timeline
share a single playhead clock — exactly what the merged Review & Label screen
needs. 4-stream 1× at 960×540 runs with 22× headroom and ~2 % CPU on this
machine, so performance is a non-issue.

Risks to validate when integrating (not now): real-time smoothness under the
full UI (QPainter/ImageItem paint cost on top of decode — the prototype
measured decode only); behaviour at the Settings "High 1280×720" quality;
and Windows/other-hardware decode throughput (M1 has hardware H.264 decode —
confirm a low-end Windows laptop still clears 30 fps × 4, else fall back to a
lower preview resolution while scrubbing).
