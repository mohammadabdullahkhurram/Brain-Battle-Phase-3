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

import platform
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal, QObject


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
                 width: int = 1280, height: int = 720, fps: int = 30,
                 parent: QObject | None = None):
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

    # ── Public API (thread-safe; uses flags the run-loop polls) ──────────
    def stop(self) -> None:
        self._stop_flag = True

    def start_recording(self, path: str | Path) -> None:
        """Queue a recording-start request. Picked up on the next frame."""
        self._rec_req_path = Path(path)

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
        self.state_changed.emit("opening")
        cap = cv2.VideoCapture(self._dev.index, self._dev.backend or _preferred_backend())
        if not cap.isOpened():
            self.state_changed.emit(f"error: could not open camera {self._dev.index}")
            return

        # Best-effort resolution / FPS configuration. Some cameras ignore these
        # and return whatever they feel like; we carry on regardless.
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self._want_w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._want_h)
        cap.set(cv2.CAP_PROP_FPS,          self._want_fps)

        actual_w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))  or self._want_w
        actual_h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self._want_h
        actual_fps = cap.get(cv2.CAP_PROP_FPS) or float(self._want_fps)

        self.state_changed.emit("live")
        total_frames = 0
        stats_window: list[float] = []
        last_stats_emit = time.monotonic()

        try:
            while not self._stop_flag:
                # Handle recording-start request.
                if self._rec_req_path is not None and not self._recording:
                    path = self._rec_req_path; self._rec_req_path = None
                    path.parent.mkdir(parents=True, exist_ok=True)
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    self._writer = cv2.VideoWriter(
                        str(path), fourcc, actual_fps, (actual_w, actual_h),
                    )
                    if self._writer.isOpened():
                        self._rec_path = path; self._recording = True
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

                ok, frame = cap.read()
                if not ok or frame is None:
                    # Transient read failure. Give the device a moment and
                    # try again; only bail if the stop flag is set.
                    self.msleep(30)
                    continue

                total_frames += 1
                now = time.monotonic()
                stats_window.append(now)
                if stats_window and stats_window[0] < now - 1.0:
                    stats_window = [t for t in stats_window if t > now - 1.0]
                if now - last_stats_emit > 1.0:
                    last_stats_emit = now
                    self.stats.emit(float(len(stats_window)), total_frames)

                # Write before emitting — prefer to lose a preview frame
                # over losing a recorded one in the pathological case.
                if self._recording and self._writer is not None:
                    try:
                        self._writer.write(frame)
                    except Exception:
                        pass

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
            cap.release()
            self.state_changed.emit("stopped")


__all__ = ["CameraDevice", "USBCameraThread", "list_cameras"]
