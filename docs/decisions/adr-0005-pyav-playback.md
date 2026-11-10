# ADR-0005 — PyAV app-driven playback

**What this page covers:** why Review & Label decodes video itself with PyAV on
one app-driven playhead, instead of using VLC or Qt Multimedia players.

- **Status:** Accepted
- **Date:** 2026-10-27 (decided) · 2026-10-29 (integrated)

## Context

The merged Review & Label screen ([ADR-0008](adr-0008-merged-review-label.md))
needs **one** video playback approach to replace two different stacks (python-vlc
in the old Review tab, Qt Multimedia in the old Labelling tab). The deciding
problem is the **drift camera**: a camera whose true capture rate (~29.5 fps)
differs from its declared container rate (30 fps). Players that play each file at
its nominal rate show frame *i* at *i*/30, but it was captured at *i*/29.5 — the
error grows without bound (~2.7 s off at the end of a 160 s session, and worse on
longer ones). This is exactly the desync the owner saw eyes-on-glass.

## Options considered

(Measured in [PLAYBACK_PROPOSAL.md](../project/planning-documents.md).)

1. **A — python-vlc (vmem→QPainter)** — plays at nominal fps (wrong on the drift
   camera) and **requires the user to install VLC**.
2. **B — Qt Multimedia `QMediaPlayer`** — also plays at nominal fps (same drift
   error); bundles ffmpeg via PyQt6.
3. **C — app-driven decode with PyAV** — the app's own clock is the single
   playhead; for time `t` each tile shows the frame whose sidecar
   `t_session_seconds` is the latest ≤ `t`. Reads the **real per-frame times**, so
   it is correct on every camera, drift included.

## Decision

**Adopt Option C (PyAV, app-driven decode).** One playhead `t` drives every
camera via `pyav_player` (`PyAVCamera` → `MultiCamDecoder` → `DecodeWorker` →
`PlayerController`). It is the only option correct on the drift camera (0–25 ms
vs A/B's growing multi-second error), gives frame-accurate stepping and ~5 ms
seeks, bundles its own ffmpeg (no VLC install), and collapses ~620 lines across
two playback stacks into one.

## Consequences

- **Correct on the drift camera by construction** — it uses the per-frame
  sidecar, not the container's nominal rate.
- **Video, EEG, and timeline share one playhead clock** — no four-VLC-clock
  negotiation, no `axis_shift`/residual seek arithmetic; exactly what the merged
  screen's centred-playhead EEG view and frame-accurate labelling need.
- **Drops the VLC system dependency** (`av` is a pip wheel with bundled ffmpeg) —
  better for the Phase 6 double-click-app goal.
- **Decode happens off the UI thread** (`DecodeWorker`) with seek coalescing;
  `PlayerController` paces against the real monotonic clock so 1× is honest 1×.
  Measured at ~22× real-time headroom and ~2% CPU for 4×960×540 on an M1.
- Risks to re-validate on real hardware: smoothness under the full UI, behaviour
  at High 1280×720, and decode throughput on a low-end Windows laptop.

## Links

- Commits `c75e7a8` (adopt Option C), `0c777d1` (PyAV single-clock player).
- [PLAYBACK_PROPOSAL.md](../project/planning-documents.md),
  [playback-clock flow](../developer-guide/key-flows.md#the-playback-clock),
  [ADR-0001](adr-0001-capture-time-clock.md).
