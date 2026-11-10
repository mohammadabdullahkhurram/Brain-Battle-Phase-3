# ADR-0004 — Crash-safe fragmented H.264 via per-camera ffmpeg

**What this page covers:** why each camera records through its own ffmpeg
subprocess writing fragmented H.264, instead of OpenCV's `cv2.VideoWriter`.

- **Status:** Accepted
- **Date:** 2026-10-22

## Context

The original recorder used `cv2.VideoWriter` with the `mp4v` codec. A standard
MP4 writes its index (`moov` atom) only when the file is closed cleanly — so a
crash or force-quit mid-recording left an **unplayable** video with everything
lost. `mp4v` is also bulky (a multi-camera session is plausibly multi-GB), which
matters for later transfer/hosting. Recording must survive a crash the same way
EEG now does ([ADR-0003](adr-0003-eeg-streamed-to-disk.md)), and must never block
capture.

## Options considered

1. **Keep `cv2.VideoWriter` mp4v** — simple, but not crash-safe and bulky.
2. **MKV** (which tolerates truncation) — crash-tolerant, but a less universal
   container for downstream tools.
3. **Fragmented MP4 (H.264) via a per-camera ffmpeg subprocess** — fragments
   flush periodically, so a kill loses at most the final fragment; H.264 is
   compact and universal.

## Decision

**Each camera records via its own `ffmpeg` subprocess writing fragmented H.264
MP4** (`usb_camera.FrameEncoder`), fed raw frames over stdin. A fragment flushes
roughly every 0.5 s, so a hard kill loses at most the final fragment — the rest
stays playable. A bounded queue + a writer thread keep capture from ever blocking
on the encoder; the sidecar row is written only after a successful stdin write,
so **sidecar rows equal encoded frames exactly**. The encoder is chosen with a
hardware-first probe and a libx264 fallback.

## Consequences

- **A crash leaves playable video** for every camera up to the last fragment.
- **Capture never blocks** — a full queue drops a frame and bumps a counter; a
  dead ffmpeg stops only that camera (its tile goes red) while the others keep
  recording.
- **Introduces a hard dependency on `ffmpeg` on `PATH`** — recording is blocked at
  pre-flight without it (`preflight_recording`); Phase 6 must bundle ffmpeg in the
  packaged app.
- Sidecar-rows-equal-frames is what makes [ADR-0001](adr-0001-capture-time-clock.md)'s
  per-frame timing exact. Verified by `tests/test_video_recording.py`.

## Links

- Commit `030dddc` (record crash-safe fragmented H.264 via per-camera ffmpeg).
- [File formats — camera videos + sidecars](../research-and-data/file-formats.md),
  [ROADMAP Phase 6](../project/roadmap.md).
