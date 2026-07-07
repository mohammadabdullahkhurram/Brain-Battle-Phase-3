"""
usb_camera.py
─────────────
USB-C and capture-card camera ingest for Brain Battle.

The original Brain Battle pipeline only supported RTMP-over-MonaServer for live
camera feeds — which works, but is heavy: it requires four MonaServer instances,
manual per-camera ``ffmpeg`` push commands, and a non-enterprise WiFi network
(eduroam blocks RTMP). The spec actually calls for *two USB-C side cameras* and
*two POV FPV cameras through a receiver*, and in practice FPV receivers
present themselves as a USB UVC device over a capture card.

This module gives Brain Battle a first-class USB-camera path:

  * Enumerate available video-capture devices on macOS / Windows / Linux
  * Per-camera ``QThread`` grabbing frames via OpenCV (``cv2.VideoCapture``),
    emitting each frame as a signal for live preview
  * Optional in-thread MP4 recording to a given path, start/stop controlled
    from the UI thread — boundary-aligned with Brain Battle phase transitions
  * Safe shutdown that finalises the MP4 container so the file is playable
    even if the operator crashes the app

Why OpenCV and not PyAV?
    PyAV is technically superior for fine-grained codec control, but OpenCV's
    VideoCapture is battle-tested across platforms and is the de-facto choice
    for consumer USB webcams (see the multiwebcam / Optogenetics Core / e-con
    Systems references in the research notes). For the boxing-session use-case
    — 1080p30 from UVC devices — OpenCV is more than fast enough.

Platform notes
    * macOS: AVFoundation. Device indices come from AVCaptureDevice order.
    * Windows: DirectShow via ``CAP_DSHOW`` is more reliable than the default
      MSMF backend (MSMF often stalls on first frame). We force DSHOW there.
    * Linux: V4L2. ``/dev/videoN`` indices.
"""

from __future__ import annotations

import logging
import platform
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal, QObject

log = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────────

# Preview frames per second to emit toward the UI. Capture continues at the
# camera's native rate (which feeds the disk writer at full quality); only
# the preview signal is throttled. 20 Hz is below the threshold where the
# human eye perceives stutter for a small preview tile, well above any
# meaningful sampling rate for situational awareness, and reduces the
# main-thread queued-signal load by roughly 33-66% versus 30/60 fps cameras.
PREVIEW_FPS_CAP      = 20
PREVIEW_INTERVAL_SEC = 1.0 / PREVIEW_FPS_CAP


# ─── Backend selection ────────────────────────────────────────────────────────

def _preferred_backend() -> int:
    """Pick the OpenCV capture backend most reliable for the current OS."""
    sys = platform.system()
    if sys == "Windows":
        return cv2.CAP_DSHOW      # DirectShow — faster and more reliable
                                   # for UVC devices than the default MSMF.
    if sys == "Darwin":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_V4L2            # Linux / everything else


# ─── Device enumeration ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class CameraDevice:
    """A single discovered capture device. ``index`` feeds VideoCapture."""
    index:       int
    name:        str           # friendly display name (best-effort per OS)
    backend:     int = 0       # OpenCV backend constant


def list_cameras(max_probe: int = 8) -> list[CameraDevice]:
    """
    Return the capture devices currently attached.

    Cross-platform device enumeration is genuinely thorny — AVFoundation has
    a listing API, DirectShow has one, V4L2 has ``/dev/video*``. Rather than
    shell out to ``ffmpeg -list_devices`` on every call, we probe the first
    N indices by attempting to open them; any that succeed are added. The
    platform-specific cost is a small startup delay which only happens when
    the operator opens the camera-picker UI.
    """
    backend = _preferred_backend()
    found: list[CameraDevice] = []
    for idx in range(max_probe):
        cap = cv2.VideoCapture(idx, backend)
        if not cap.isOpened():
            cap.release()
            continue
        # Name: best-effort. OpenCV doesn't expose friendly names portably,
        # so we use a generic numbered label. On Linux we try to read the
        # V4L2 sysfs name for a more meaningful label.
        name = _friendly_name(idx)
        cap.release()
        found.append(CameraDevice(index=idx, name=name, backend=backend))
    return found


def _friendly_name(idx: int) -> str:
    """Best-effort human-readable device label."""
    if platform.system() == "Linux":
        try:
            path = Path(f"/sys/class/video4linux/video{idx}/name")
            if path.is_file():
                return path.read_text().strip() or f"Camera {idx}"
        except Exception:
            pass
    return f"Camera {idx}"


# ─── Capture thread ───────────────────────────────────────────────────────────

class USBCameraThread(QThread):
    """
    One capture thread per camera. Reads frames in a tight loop, emits them
    to the UI, and optionally writes each frame to an MP4.

    Signals
    ────────
    * ``frame_ready(np.ndarray)`` — latest BGR frame, emitted at capture rate.
      The UI should receive it, convert once to ``QImage``, and display.
    * ``state_changed(str)``     — textual status for UI diagnostics:
      ``"opening"``, ``"live"``, ``"error: …"``, ``"stopped"``.
    * ``stats(fps, frames_total)`` — rolling FPS + total frame count, 1 Hz.

    Lifecycle
    ────────
    ``start()`` begins the capture. Call ``stop()`` from the UI thread to
    request an orderly shutdown; the thread quits when the current frame
    returns or a 2-second watchdog expires, whichever comes first.
    Call ``start_recording(path)`` / ``stop_recording()`` at any time — the
    thread picks up the request between frames, so it is safe to call across
    threads.
    """

    frame_ready   = pyqtSignal(np.ndarray)
    state_changed = pyqtSignal(str)
    stats         = pyqtSignal(float, int)  # fps, total_frames

    def __init__(self, device: CameraDevice,
                 width: int = 960, height: int = 540, fps: int = 30,
                 parent: QObject | None = None):
        # Default 960×540 (qHD, 16:9). This is ~56% the pixel count of 720p
        # but visually nearly indistinguishable on the small preview tiles
        # we render. The pixel cost adds up across 4 cameras: at 720p each
        # frame is ~1.4 MB; at 540 it's ~780 KB. Four cameras at 30 fps
        # means ~94 MB/sec vs ~166 MB/sec of pixel data moving through
        # Qt's signal queue + JPEG decode. The chosen res still captures
        # enough detail for boxing analysis (faces, gloves, body posture)
        # while keeping low-end laptops responsive. Callers can override
        # with explicit width/height when needed.
        super().__init__(parent)
        self._dev       = device
        self._want_w    = width
        self._want_h    = height
        self._want_fps  = fps

        self._stop_flag:       bool = False
        self._recording:       bool = False
        self._rec_path:        Optional[Path] = None
        self._writer:          Optional[cv2.VideoWriter] = None
        self._rec_req_path:    Optional[Path] = None     # start request
        self._rec_stop_req:    bool = False              # stop request
        # Capture-time clock origin (t0_monotonic, t0_unix) shared across all
        # camera threads so every frame stamp sits on one reference; set per
        # recording via start_recording(clock=...). None → each thread uses
        # its own first recorded frame as the origin (still self-consistent).
        self._rec_req_clock:   Optional[tuple[float, float]] = None
        self._rec_clock:       Optional[tuple[float, float]] = None
        self._ts_file = None                             # timestamp sidecar handle
        self._rec_frame_idx:   int = 0

    # ── Public API (thread-safe; uses flags the run-loop polls) ──────────
    def stop(self) -> None:
        self._stop_flag = True

    def start_recording(self, path: str | Path,
                        clock: Optional[tuple[float, float]] = None) -> None:
        """Queue a recording-start request. Picked up on the next frame.

        `clock` is the shared capture-time origin (t0_monotonic, t0_unix)
        captured once at session recording-start; passing the same tuple to
        every camera thread puts all their frame stamps on one reference.
        """
        self._rec_req_path = Path(path)
        self._rec_req_clock = clock

    def stop_recording(self) -> None:
        """Queue a recording-stop request. Picked up on the next frame."""
        self._rec_stop_req = True

    @property
    def device(self) -> CameraDevice:
        return self._dev

    @property
    def is_recording(self) -> bool:
        return self._recording

    # ── Thread body ──
    def run(self) -> None:  # noqa: C901 — single run-loop, hard to split further
        write_err_logged = False  # one frame-write warning per capture run
        self.state_changed.emit("opening")
        cap = cv2.VideoCapture(self._dev.index, self._dev.backend or _preferred_backend())
        if not cap.isOpened():
            self.state_changed.emit(f"error: could not open camera {self._dev.index}")
            return

        # ── Camera format negotiation ──────────────────────────────────────
        # MUST set FOURCC=MJPG BEFORE resolution/fps. Without this, most USB
        # webcams default to raw YUYV (16-bit uncompressed). At 1280×720 YUYV
        # that's ~55 MB/sec per camera over USB — with 4 cameras on a USB 2.0
        # bus (480 Mbps theoretical = 60 MB/sec total) you simply cannot fit,
        # and the cameras drop to 5-15 fps. With MJPG (motion-JPEG), each
        # camera consumes ~3-5 Mbps and 4 cameras fit comfortably even on
        # USB 2.0. This is the single most important camera setting for
        # achieving the target framerate across heterogeneous hardware.
        #
        # The set order is critical: FOURCC first, THEN resolution/fps.
        # Setting them in the other order silently fails on macOS
        # AVFoundation and many Windows DirectShow drivers — the camera
        # negotiates a format with the first call and ignores later changes.
        try:
            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        except Exception:
            pass  # Not every backend exposes FOURCC; carry on with default.

        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self._want_w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._want_h)
        cap.set(cv2.CAP_PROP_FPS,          self._want_fps)

        # Reduce the driver's internal frame buffer to 1 so we always pull
        # the latest frame, not an old one queued by the OS. Saves latency
        # and reduces memory pressure with 4 cameras. Not supported by
        # every backend; the call silently fails where it doesn't apply.
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        actual_w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))  or self._want_w
        actual_h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self._want_h
        actual_fps = cap.get(cv2.CAP_PROP_FPS) or float(self._want_fps)
        if abs(actual_fps - self._want_fps) > 0.5:
            log.warning("cam idx=%s requested %s fps but reports %.2f fps — "
                        "may drift across long sessions vs other cameras",
                        self._dev.index, self._want_fps, actual_fps)

        self.state_changed.emit("live")
        total_frames = 0
        stats_window: list[float] = []
        last_stats_emit = time.monotonic()
        last_preview_emit = 0.0  # forces first frame to emit immediately
        # The first frame is the source of truth for VideoWriter dimensions.
        # cap.get() values can be 0/NaN on macOS AVFoundation before warmup,
        # which causes VideoWriter to "open" but silently reject frames →
        # 257-byte MP4 files with header but no data. We capture the first
        # frame's shape and use that for all writer init from this point on.
        first_frame_shape: tuple[int, int] | None = None  # (h, w)

        try:
            while not self._stop_flag:
                # Handle recording-start request — DEFERRED until we have a
                # valid first frame, so the writer always gets correct dims.
                if self._rec_req_path is not None and not self._recording \
                        and first_frame_shape is not None:
                    path = self._rec_req_path; self._rec_req_path = None
                    clock = self._rec_req_clock; self._rec_req_clock = None
                    path.parent.mkdir(parents=True, exist_ok=True)
                    fh, fw = first_frame_shape
                    # Sanity-check FPS: cap.get() returns NaN/0 on some
                    # backends. Default to 30 when invalid.
                    safe_fps = float(actual_fps) if actual_fps and actual_fps > 5 else 30.0
                    if safe_fps > 120: safe_fps = 30.0  # bogus-high reading
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    self._writer = cv2.VideoWriter(
                        str(path), fourcc, safe_fps, (fw, fh),
                    )
                    if self._writer.isOpened():
                        self._rec_path = path; self._recording = True
                        self._rec_fps = safe_fps  # for downstream use
                        self._rec_size = (fw, fh)
                        self._rec_clock = clock
                        self._rec_frame_idx = 0
                        self._open_timestamp_sidecar(path)
                    else:
                        self._writer = None
                        self.state_changed.emit("error: could not open video writer")

                # Handle recording-stop request.
                if self._rec_stop_req and self._recording:
                    self._rec_stop_req = False
                    try:
                        if self._writer is not None:
                            self._writer.release()
                    finally:
                        self._writer = None
                        self._recording = False
                        self._rec_path = None
                        self._close_timestamp_sidecar()

                ok, frame = cap.read()
                if not ok or frame is None:
                    # Transient read failure. Give the device a moment and
                    # try again; only bail if the stop flag is set.
                    self.msleep(30)
                    continue

                # Lock in first-frame shape on first successful read.
                if first_frame_shape is None:
                    first_frame_shape = (frame.shape[0], frame.shape[1])

                total_frames += 1
                now = time.monotonic()
                stats_window.append(now)
                if stats_window and stats_window[0] < now - 1.0:
                    stats_window = [t for t in stats_window if t > now - 1.0]
                if now - last_stats_emit > 1.0:
                    last_stats_emit = now
                    self.stats.emit(float(len(stats_window)), total_frames)

                # Write to disk before emitting — prefer to lose a preview
                # frame over losing a recorded one in the pathological case.
                # Recording stays at full capture rate; only the preview is
                # throttled below.
                if self._recording and self._writer is not None:
                    try:
                        if frame.shape[:2] == first_frame_shape:
                            self._writer.write(frame)
                        else:
                            # Resize on the fly if the camera surprises us
                            # mid-stream. Rare but possible on hot-plug.
                            resized = cv2.resize(frame, (first_frame_shape[1], first_frame_shape[0]))
                            self._writer.write(resized)
                        # Stamp the frame we just wrote. `now` is this frame's
                        # grab time on the process-wide monotonic clock — the
                        # same clock every camera thread reads, so all sidecars
                        # share one axis. Stamp AFTER a successful write so the
                        # sidecar row count matches the MP4 frame count.
                        self._stamp_frame(now)
                    except Exception as e:
                        # Dropped recorded frames = data loss, but a dying
                        # camera raises per-frame — log once per capture run.
                        if not write_err_logged:
                            write_err_logged = True
                            log.warning("frame write failed (further drops "
                                        "this run suppressed): %r", e)

                # ── Preview emit throttling ───────────────────────────────
                # Cap preview emits at ~20 Hz (50 ms). Even if the camera
                # delivers 30/60 fps, the eye cannot perceive a difference
                # above ~20 fps on a small preview tile, and every emit
                # carries the full frame across the Qt thread boundary →
                # a queued-connection copy of ~1 MB at 720p × 4 cameras.
                # At native 30 fps × 4 cameras the system was moving
                # ~120 MB/sec through Qt's event queue just to keep
                # previews live. Throttling to 20 Hz cuts that to ~80 MB/sec
                # and frees the main thread to handle other work (EEG
                # plots, button clicks). Recording above is unaffected —
                # the disk writer still gets every captured frame.
                if (now - last_preview_emit) >= PREVIEW_INTERVAL_SEC:
                    last_preview_emit = now
                    self.frame_ready.emit(frame)
        finally:
            # Always release the writer and the device, even on exception.
            try:
                if self._writer is not None:
                    self._writer.release()
            except Exception:
                pass
            self._writer = None
            self._recording = False
            self._close_timestamp_sidecar()
            cap.release()
            self.state_changed.emit("stopped")

    # ── Capture-time frame timestamps (additive; MP4 writing is unchanged) ──
    #
    # For every frame written to the MP4 we append one row to a sidecar CSV
    # next to it: <mp4-stem>.timestamps.csv. This turns the video from
    # "frames at a nominal fps" into "frames at known capture instants", so
    # downstream alignment is measured against a shared clock rather than
    # assumed (see docs/ARCHITECTURE_AND_EVALUATION.md §1.6, Intent A/B).

    def _open_timestamp_sidecar(self, mp4_path: Path) -> None:
        ts_path = mp4_path.parent / (mp4_path.stem + ".timestamps.csv")
        try:
            self._ts_file = open(ts_path, "w", encoding="utf-8", newline="")
            # t_session_seconds — same name/axis as phase_markers.csv, so
            # frames and markers line up directly. t_monotonic/t_unix are the
            # raw process clock + wall clock at grab time.
            self._ts_file.write("frame_index,t_session_seconds,t_monotonic,t_unix\n")
        except OSError as e:
            self._ts_file = None
            log.warning("could not open timestamp sidecar %s: %s", ts_path, e)

    def _stamp_frame(self, t_monotonic: float) -> None:
        if self._ts_file is None:
            return
        clock = self._rec_clock
        if clock is not None:
            t0_mono, t0_unix = clock
        else:
            # No shared origin passed — anchor to this recording's first frame
            # so the sidecar is still internally consistent.
            if self._rec_frame_idx == 0:
                self._rec_clock = (t_monotonic, time.time())
                t0_mono, t0_unix = self._rec_clock
            else:
                t0_mono, t0_unix = self._rec_clock
        t_session = t_monotonic - t0_mono
        t_unix = t0_unix + t_session
        try:
            self._ts_file.write(
                f"{self._rec_frame_idx},{t_session:.6f},{t_monotonic:.6f},{t_unix:.6f}\n")
        except OSError:
            pass  # a write failure here must not stop capture
        self._rec_frame_idx += 1

    def _close_timestamp_sidecar(self) -> None:
        if self._ts_file is not None:
            try:
                self._ts_file.flush(); self._ts_file.close()
            except OSError:
                pass
            self._ts_file = None
        self._rec_clock = None


__all__ = ["CameraDevice", "USBCameraThread", "list_cameras"]
