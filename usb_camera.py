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
import os
import platform
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal, QObject

log = logging.getLogger(__name__)


# ─── Recording encoder config ──────────────────────────────────────────────────
#
# Target bitrate for the 960×540 H.264 recording. 540p30 boxing footage is
# high-motion (fast exchanges, moving bodies); 4 Mbps shows visible blocking on
# the hardest frames, 6 Mbps is clean but ~50% larger. 5 Mbps is the balance —
# visibly clean at this resolution while staying ~0.6 MB/s per camera (~2.4 MB/s
# across four), well inside the pre-flight disk estimate. -maxrate/-bufsize cap
# the peaks so a burst of motion can't blow past the plan.
REC_BITRATE       = "5M"
REC_MAXRATE       = "6M"
REC_BUFSIZE       = "10M"
# Bounded hand-off queue (capture thread → encoder writer thread). ~2 s at
# 30 fps: long enough to ride out a brief encoder hiccup, short enough that a
# genuinely stalled encoder drops frames instead of ballooning memory.
REC_QUEUE_MAXSIZE = 60


def _ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


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


# ─── ffmpeg frame encoder (crash-safe fragmented H.264) ─────────────────────────

def _resolve_encoder() -> str:
    """The H.264 encoder to record with — REUSES export_module's probe
    (hardware encoder validated once, libx264 fallback). Imported lazily so
    importing usb_camera doesn't trigger the probe (keeps module import cheap,
    which the smoke gate relies on)."""
    try:
        from export_module import _HW_ENCODER
        return _HW_ENCODER
    except Exception as e:
        log.warning("could not read probed encoder (%r) — using libx264", e)
        return "libx264"


def _encoder_rate_args(encoder: str) -> list[str]:
    """Encoder-specific rate-control flags around the shared target bitrate.
    (The encoder NAME comes from export_module's probe; only these recorder
    rate flags live here — the detection is not duplicated.)"""
    rate = ["-b:v", REC_BITRATE, "-maxrate", REC_MAXRATE, "-bufsize", REC_BUFSIZE]
    if encoder == "libx264":
        # ultrafast keeps 4 software encodes cheap on a laptop; zerolatency
        # avoids frame reordering/lookahead so capture isn't held up.
        return ["-preset", "ultrafast", "-tune", "zerolatency", *rate]
    return rate


def preflight_recording() -> tuple[bool, str, str]:
    """Check recording is possible BEFORE a session starts. Returns
    (ok, encoder, message). ok=False means block the start and show `message`
    (plain language). Validates both that ffmpeg is on PATH and that the chosen
    encoder actually runs (reusing export_module's real test-encode probe)."""
    if _ffmpeg_path() is None:
        return (False, "", "ffmpeg was not found on this computer. Recording "
                           "needs it — install ffmpeg and try again.")
    encoder = _resolve_encoder()
    try:
        from export_module import _validate_encoder
        ff = _ffmpeg_path()
        if not _validate_encoder(ff, encoder):
            # Chosen encoder failed its test-encode; try plain libx264 before
            # giving up (the probe only validates hardware encoders).
            if encoder != "libx264" and _validate_encoder(ff, "libx264"):
                return (True, "libx264", "")
            return (False, encoder,
                    "No working video encoder was found on this computer, so "
                    "the session can't be recorded. Check the ffmpeg install.")
    except Exception as e:
        log.warning("encoder validation raised (%r) — allowing start", e)
    return (True, encoder, "")


class FrameEncoder:
    """Owns one ffmpeg subprocess writing a crash-safe fragmented H.264 MP4,
    fed raw BGR frames over stdin, plus the per-frame timestamp sidecar.

    Crash-safety: fragmented MP4 (`+frag_keyframe+empty_moov+default_base_moof`,
    `-frag_duration 500000`) flushes a self-contained fragment ~every 0.5 s, so
    a hard kill loses at most the final in-progress fragment — everything before
    it stays playable (unlike mp4v, whose moov atom is only written at close).

    Sidecar invariant: `write_frame` appends the sidecar row ONLY after that
    frame's bytes were accepted by ffmpeg's stdin, so sidecar rows == frames
    handed to the encoder. Dropped frames never reach here, so they get no row.

    This class does NO queueing or frame-dropping — the caller (USBCameraThread)
    owns the bounded queue and the drop policy; a demo/offline caller can drive
    write_frame() synchronously. stdin.write blocks when ffmpeg is slow, which
    is the back-pressure signal the caller's queue turns into drops.
    """

    def __init__(self, path, width: int, height: int, fps: float,
                 clock: Optional[tuple[float, float]] = None,
                 encoder: Optional[str] = None):
        self.path = str(path)
        self._w = int(width)
        self._h = int(height)
        self._fps = float(fps)
        self._clock = clock
        # Explicit encoder name skips the export_module probe (used by the demo
        # generator's worker processes, which resolve it once in the parent).
        self._want_encoder = encoder
        self._proc: Optional[subprocess.Popen] = None
        self._ts_file = None
        self._frame_idx = 0
        self._encoder = ""
        self._stderr_tail: "deque[str]" = deque(maxlen=200)
        self._stderr_thread: Optional[threading.Thread] = None

    # ── lifecycle ──
    def start(self) -> bool:
        ff = _ffmpeg_path()
        if ff is None:
            log.error("[cam-enc] ffmpeg not found — cannot record %s", self.path)
            return False
        self._encoder = self._want_encoder or _resolve_encoder()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        cmd = (
            [ff, "-hide_banner", "-loglevel", "error", "-nostats", "-y",
             "-f", "rawvideo", "-pix_fmt", "bgr24",
             "-s", f"{self._w}x{self._h}", "-r", f"{self._fps:g}",
             "-i", "pipe:0",
             "-an",
             "-c:v", self._encoder,
             "-pix_fmt", "yuv420p",
             "-fps_mode", "passthrough",
             "-g", str(max(1, int(round(self._fps))))]       # keyframe ~every 1 s
            + _encoder_rate_args(self._encoder)
            + ["-movflags", "+frag_keyframe+empty_moov+default_base_moof",
               "-frag_duration", "500000",
               self.path]
        )
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as e:
            log.error("[cam-enc] could not launch ffmpeg for %s: %s", self.path, e)
            self._proc = None
            return False
        # Drain stderr continuously into a bounded tail so the pipe can't fill
        # and stall the encoder; the tail is logged if ffmpeg dies.
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True, name="cam-enc-stderr")
        self._stderr_thread.start()
        self._open_sidecar()
        return True

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode(errors="replace").rstrip()
                if line:
                    self._stderr_tail.append(line)
        except (OSError, ValueError):
            pass

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def write_frame(self, frame: np.ndarray, t_monotonic: float) -> bool:
        """Feed one frame to ffmpeg and, on success, stamp its sidecar row.
        Returns False if ffmpeg's pipe is gone (process died) — the caller
        should stop this camera. Blocks while ffmpeg's stdin is full (the
        back-pressure the caller converts into drops)."""
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        # Ensure exact dims + contiguous BGR bytes for the rawvideo pipe.
        if frame.shape[0] != self._h or frame.shape[1] != self._w:
            frame = cv2.resize(frame, (self._w, self._h))
        if not frame.flags["C_CONTIGUOUS"]:
            frame = np.ascontiguousarray(frame)
        try:
            proc.stdin.write(frame.tobytes())
        except (BrokenPipeError, OSError, ValueError):
            return False
        self._write_sidecar_row(t_monotonic)   # only AFTER a successful write
        return True

    def close(self, timeout: float = 5.0) -> Optional[int]:
        """Normal stop: close stdin (ffmpeg flushes + finalises on EOF), wait
        up to `timeout`, then kill if it hangs. Idempotent. Returns ffmpeg's
        exit code (or None)."""
        proc = self._proc
        self._proc = None
        rc = None
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except OSError:
                pass
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                log.warning("[cam-enc] ffmpeg did not exit in %.0fs — killing (%s)",
                            timeout, self.path)
                proc.kill()
                try:
                    rc = proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    rc = None
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1.0)
            self._stderr_thread = None
        self._close_sidecar()
        return rc

    def stderr_tail(self, n: int = 8) -> str:
        return "\n".join(list(self._stderr_tail)[-n:])

    # ── sidecar (row-per-encoded-frame) ──
    def _open_sidecar(self) -> None:
        p = Path(self.path)
        ts_path = p.parent / (p.stem + ".timestamps.csv")
        try:
            # Line-buffered so each complete row reaches the OS on its newline,
            # keeping the sidecar crash-consistent with the fragmented video.
            self._ts_file = open(ts_path, "w", buffering=1, encoding="utf-8", newline="")
            self._ts_file.write("frame_index,t_session_seconds,t_monotonic,t_unix\n")
        except OSError as e:
            self._ts_file = None
            log.warning("could not open timestamp sidecar %s: %s", ts_path, e)

    def _write_sidecar_row(self, t_monotonic: float) -> None:
        if self._ts_file is None:
            self._frame_idx += 1
            return
        clock = self._clock
        if clock is not None:
            t0_mono, t0_unix = clock
        else:
            # No shared origin — anchor to this recording's first frame so the
            # sidecar is still internally consistent.
            if self._frame_idx == 0:
                self._clock = (t_monotonic, time.time())
            t0_mono, t0_unix = self._clock
        t_session = t_monotonic - t0_mono
        t_unix = t0_unix + t_session
        try:
            self._ts_file.write(
                f"{self._frame_idx},{t_session:.6f},{t_monotonic:.6f},{t_unix:.6f}\n")
        except OSError:
            pass  # a sidecar write failure must not stop capture
        self._frame_idx += 1

    def _close_sidecar(self) -> None:
        if self._ts_file is not None:
            try:
                self._ts_file.flush()
                os.fsync(self._ts_file.fileno())
                self._ts_file.close()
            except OSError:
                pass
            self._ts_file = None


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
    record_error  = pyqtSignal(str)         # this camera's recording failed mid-session

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
        self._rec_req_path:    Optional[Path] = None     # start request
        self._rec_stop_req:    bool = False              # stop request
        # Capture-time clock origin (t0_monotonic, t0_unix) shared across all
        # camera threads so every frame stamp sits on one reference; set per
        # recording via start_recording(clock=...). None → each thread uses
        # its own first recorded frame as the origin (still self-consistent).
        self._rec_req_clock:   Optional[tuple[float, float]] = None
        # Crash-safe recording: a bounded queue hands frames from this capture
        # thread to a writer thread that feeds the FrameEncoder's ffmpeg stdin.
        # Capture never blocks on the encoder — a full queue drops the frame.
        self._encoder:         Optional[FrameEncoder] = None
        self._rec_queue:       Optional[queue.Queue] = None
        self._rec_stop_event:  Optional[threading.Event] = None
        self._writer_thread:   Optional[threading.Thread] = None
        self._dropped_frames:  int = 0
        self._last_drop_log:   float = 0.0

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

    @property
    def dropped_frames(self) -> int:
        """Frames dropped this recording because the encoder couldn't keep up
        (bounded-queue overflow). 0 is the healthy case."""
        return self._dropped_frames

    # ── Thread body ──
    def run(self) -> None:  # noqa: C901 — single run-loop, hard to split further
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
                # valid first frame, so the encoder always gets correct dims.
                if self._rec_req_path is not None and not self._recording \
                        and first_frame_shape is not None:
                    path = self._rec_req_path; self._rec_req_path = None
                    clock = self._rec_req_clock; self._rec_req_clock = None
                    fh, fw = first_frame_shape
                    # Sanity-check FPS: cap.get() returns NaN/0 on some
                    # backends. Default to 30 when invalid.
                    safe_fps = float(actual_fps) if actual_fps and actual_fps > 5 else 30.0
                    if safe_fps > 120: safe_fps = 30.0  # bogus-high reading
                    self._start_recording_encoder(path, fw, fh, safe_fps, clock)

                # Handle recording-stop request.
                if self._rec_stop_req and self._recording:
                    self._rec_stop_req = False
                    self._stop_recording_encoder()

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

                # Hand the frame to the encoder writer thread WITHOUT blocking:
                # capture must keep its cadence even if ffmpeg stalls. A full
                # queue means the encoder can't keep up — drop the frame, count
                # it, and (rate-limited) log it. `frame` is a fresh array per
                # cap.read() in OpenCV-Python, so enqueuing it by reference is
                # safe even though the writer consumes it later — the same
                # assumption the preview emit below already relies on.
                if self._recording and self._rec_queue is not None:
                    try:
                        self._rec_queue.put_nowait((frame, now))
                    except queue.Full:
                        self._dropped_frames += 1
                        if now - self._last_drop_log >= 1.0:
                            self._last_drop_log = now
                            log.warning("cam idx=%s: encoder queue full — dropping "
                                        "frames (total this run: %d)",
                                        self._dev.index, self._dropped_frames)

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
            # Always stop recording (finalise ffmpeg + sidecar) and release the
            # device, even on exception — so no orphaned ffmpeg survives.
            try:
                if self._recording or self._writer_thread is not None:
                    self._stop_recording_encoder()
            except Exception:
                pass
            cap.release()
            self.state_changed.emit("stopped")

    # ── Crash-safe recording: encoder + writer thread ──
    #
    # Every frame handed to the encoder appends one row to a sidecar CSV next
    # to the MP4 (<mp4-stem>.timestamps.csv), written by FrameEncoder ONLY
    # after that frame's bytes reached ffmpeg's stdin — so sidecar rows ==
    # encoded frames (dropped frames get neither). This turns the video from
    # "frames at a nominal fps" into "frames at known capture instants"
    # (docs/ARCHITECTURE_AND_EVALUATION.md §1.6).

    def _start_recording_encoder(self, path, fw, fh, fps, clock) -> None:
        enc = FrameEncoder(path, fw, fh, fps, clock)
        if not enc.start():
            self.state_changed.emit("error: could not start video encoder")
            self.record_error.emit(
                "Could not start recording for this camera (video encoder "
                "failed to launch).")
            return
        self._encoder = enc
        self._rec_queue = queue.Queue(maxsize=REC_QUEUE_MAXSIZE)
        self._rec_stop_event = threading.Event()
        self._dropped_frames = 0
        self._last_drop_log = 0.0
        self._rec_path = Path(path)
        self._writer_thread = threading.Thread(
            target=self._encoder_writer_loop,
            args=(enc, self._rec_queue, self._rec_stop_event),
            name="cam-enc-writer", daemon=True,
        )
        self._writer_thread.start()
        self._recording = True

    def _encoder_writer_loop(self, enc, q, stop_event) -> None:
        """Feed queued frames to the encoder. Blocks on ffmpeg's stdin when the
        encoder is slow (that's the back-pressure the capture loop turns into
        drops). Runs until stopped and drained, or until ffmpeg dies."""
        died = False
        while True:
            try:
                item = q.get(timeout=0.2)
            except queue.Empty:
                item = None
            if item is not None:
                frame, t_mono = item
                if not enc.write_frame(frame, t_mono):
                    died = True
                    break
            # Detect an encoder that exited on its own (crash / error) even
            # during a gap in frames — not just on the next failed write.
            elif not stop_event.is_set() and not enc.is_alive():
                died = True
                break
            if stop_event.is_set() and q.empty():
                break
        if died:
            self._handle_encoder_death(enc)
        else:
            enc.close()   # normal stop: flush + finalise the fragmented MP4

    def _handle_encoder_death(self, enc) -> None:
        """One camera's ffmpeg died mid-session. Stop ONLY this camera, keep
        its partial (still-playable, fragmented) file + sidecar, and signal the
        slot — the other cameras and EEG keep recording."""
        tail = enc.stderr_tail()
        log.error("cam idx=%s: ffmpeg encoder died mid-recording — keeping the "
                  "partial file. ffmpeg stderr tail:\n%s", self._dev.index, tail)
        try:
            enc.close(timeout=2.0)   # reap the dead process + close the sidecar
        except Exception:
            pass
        self._recording = False
        if self._encoder is enc:
            self._encoder = None
        self.record_error.emit(
            "Recording for this camera stopped because of a video-encoder "
            "error. The footage saved up to that point has been kept.")

    def _stop_recording_encoder(self) -> None:
        """Normal stop: tell the writer to drain + finalise, then join it (so
        ffmpeg has exited and no orphan remains before we return)."""
        self._recording = False
        if self._rec_stop_event is not None:
            self._rec_stop_event.set()
        wt = self._writer_thread
        if wt is not None:
            wt.join(timeout=12.0)
            if wt.is_alive():
                log.error("cam idx=%s: encoder writer did not stop in time — "
                          "video may be missing its final fragment", self._dev.index)
        self._writer_thread = None
        self._rec_queue = None
        self._rec_stop_event = None
        self._rec_path = None
        self._encoder = None


__all__ = ["CameraDevice", "USBCameraThread", "FrameEncoder",
           "preflight_recording", "list_cameras"]
