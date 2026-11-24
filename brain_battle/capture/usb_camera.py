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

import brain_battle.capture.clock as _clock

log = logging.getLogger(__name__)

# Four capture threads each resize every frame (preview, buzzer-test energy).
# OpenCV would split each small resize over every core and the threads would
# then fight over them; one OpenCV worker per calling thread is much steadier.
try:
    cv2.setNumThreads(1)
except Exception:
    pass


# ─── Recording encoder config ──────────────────────────────────────────────────
#
# Default/fallback target bitrate for the H.264 recording, used when no
# explicit bitrate is given (e.g. direct FrameEncoder/USBCameraThread use in
# tests). 540p30 boxing footage is high-motion (fast exchanges, moving
# bodies); 4 Mbps shows visible blocking on the hardest frames, 6 Mbps is
# clean but ~50% larger. 5 Mbps is the balance — visibly clean at 960x540
# while staying ~0.6 MB/s per camera. The Settings video-quality level
# (app_settings.VIDEO_QUALITY) picks the ACTUAL bitrate per recording;
# -maxrate/-bufsize (1.2x / 2x the chosen bitrate) cap the peaks so a burst of
# motion can't blow past the plan.
REC_BITRATE_MBPS  = 5.0
REC_MAXRATE_FACTOR = 1.2
REC_BUFSIZE_FACTOR = 2.0
# Bounded hand-off queue (capture thread → encoder writer thread). ~2 s at
# 30 fps: long enough to ride out a brief encoder hiccup, short enough that a
# genuinely stalled encoder drops frames instead of ballooning memory.
REC_QUEUE_MAXSIZE = 60      # at least this many frames; really 2 s at the camera's rate
# Stall watchdog: a camera that has delivered no frame for this long is
# reopened (the recording continues in the same file).
STALL_SEC = 2.0
# Frame-difference energy for the buzzer test: every frame, sampled on a grid
# of about this size, is compared with the previous one (cheap: ~2 k pixels).
ENERGY_SIZE = (64, 36)          # (kept for reference: the grid of the first version)
ENERGY_STRIDE = 4
ENERGY_KEEP_SEC = 40.0
# Reusable frame buffers per camera (the encoder queue is usually a few frames
# deep; beyond this many in flight, frames are simply allocated). The pool is
# last-in-first-out, so the same two or three buffers are used again and again
# and stay in memory; see camera_worker.CaptureLoop._take_buffer.
POOL_MAX = 12


def _ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


# ─── Constants ────────────────────────────────────────────────────────────────

# Preview frames per second to emit toward the UI. Capture continues at the
# camera's native rate (which feeds the disk writer at full quality); only
# the preview signal is throttled. 20 Hz is below the threshold where the
# human eye perceives stutter for a small preview tile, well above any
# meaningful sampling rate for situational awareness, and reduces the
# main-thread queued-signal load by roughly 33-66% versus 30/60 fps cameras.
PREVIEW_FPS_CAP      = 15
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
    # Developer-only simulated cameras (BRAIN_BATTLE_SIMULATE) go through the
    # exact same capture path below — only the device list + frame source are
    # synthetic. No trace of this when the flag is unset.
    try:
        import brain_battle.capture.simulation as simulation
        if simulation.enabled():
            return simulation.camera_devices()
    except Exception:
        pass
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
    which the smoke gate relies on). A camera process is told the app's answer
    (``BRAIN_BATTLE_REC_ENCODER``) so it never imports export_module or runs
    the probe itself — that took ~0.5 s in the reader thread at the start of
    every recording."""
    told = os.environ.get("BRAIN_BATTLE_REC_ENCODER")
    if told:
        return told
    try:
        from brain_battle.data.export_module import _HW_ENCODER
        return _HW_ENCODER
    except Exception as e:
        log.warning("could not read probed encoder (%r) — using libx264", e)
        return "libx264"


def _encoder_rate_args(encoder: str, bitrate_mbps: float = REC_BITRATE_MBPS) -> list[str]:
    """Encoder-specific rate-control flags around the given target bitrate.
    (The encoder NAME comes from export_module's probe; only these recorder
    rate flags live here — the detection is not duplicated.)"""
    b = float(bitrate_mbps)
    rate = ["-b:v", f"{b:g}M", "-maxrate", f"{b * REC_MAXRATE_FACTOR:g}M",
            "-bufsize", f"{b * REC_BUFSIZE_FACTOR:g}M"]
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
        from brain_battle.data.export_module import _validate_encoder
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
                 encoder: Optional[str] = None,
                 bitrate_mbps: float = REC_BITRATE_MBPS,
                 on_frame=None):
        self.path = str(path)
        # Optional callback(frame_index, t_monotonic) fired for every frame that
        # reached the encoder — used to publish frame times as a live LSL stream.
        self._on_frame = on_frame
        self._w = int(width)
        self._h = int(height)
        self._fps = float(fps)
        self._clock = clock
        self._bitrate_mbps = float(bitrate_mbps)
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
        if "null-encoder" in capture_debug():
            return self._start_null()
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
            + _encoder_rate_args(self._encoder, self._bitrate_mbps)
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

    def _start_null(self) -> bool:
        """Debug only (soak experiments): no ffmpeg — frames go nowhere, the
        timestamp sidecar is still written."""
        class _Null:
            def __init__(self):
                self.stdin = open(os.devnull, "wb")
                self.stderr = None
            def poll(self):
                return None
            def wait(self, timeout=None):
                return 0
            def kill(self):
                pass
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        open(self.path, "wb").close()
        self._proc = _Null()
        self._open_sidecar()
        return True

    def _drain_stderr(self) -> None:
        from brain_battle.support import platform_utils
        platform_utils.raise_thread_priority()      # a GIL holder: see camera_worker
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

    def write_frame(self, frame: np.ndarray, t_monotonic: float,
                    t_arrival: Optional[float] = None) -> bool:
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
            # straight from the array's memory: no 1.5 MB copy per frame
            proc.stdin.write(memoryview(frame).cast("B"))
        except (BrokenPipeError, OSError, ValueError):
            return False
        self._t_after_pipe = _clock.monotonic()        # (debug trace: pipe vs sidecar time)
        self._write_sidecar_row(t_monotonic, t_arrival)   # only AFTER a successful write
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
            self._ts_file.write(
                "frame_index,t_session_seconds,t_monotonic,t_unix,t_arrival_monotonic\n")
        except OSError as e:
            self._ts_file = None
            log.warning("could not open timestamp sidecar %s: %s", ts_path, e)

    def _write_sidecar_row(self, t_monotonic: float, t_arrival: Optional[float] = None) -> None:
        """One row: the frame's time (``t_monotonic``: its stamp on the
        camera's fitted steady clock, ``timing.FrameClock``; the read time
        when no fit is given) and, last, the moment it was actually read."""
        if t_arrival is None:
            t_arrival = t_monotonic
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
            if self._ts_file is not None:
                self._ts_file.write(
                    f"{self._frame_idx},{t_session:.6f},{t_monotonic:.6f},{t_unix:.6f},"
                    f"{t_arrival:.6f}\n")
        except OSError:
            pass  # a sidecar write failure must not stop capture
        if self._on_frame is not None:
            try:
                self._on_frame(self._frame_idx, t_monotonic)
            except Exception:
                pass  # a listener must never stop capture either
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
    One camera. The capture itself — read, timestamp, hand to ffmpeg, write
    the timestamp sidecar — runs in a **process of its own**
    (``camera_worker``, ADR-0025), so nothing in the app's main process can
    delay it; this QThread starts that process, passes commands to it and
    turns what it reports into the signals and numbers the app reads.
    ``isolated=False`` runs the same loop in this thread instead (tests that
    patch the encoder use that).

    Signals
    ────────
    * ``frame_ready(np.ndarray)`` — a small BGR preview picture (≤ 15 a second).
    * ``state_changed(str)``     — ``"opening"``, ``"live"``, ``"reconnecting"``,
      ``"error: …"``, ``"stopped"``.
    * ``stats(fps, frames_total)`` — once a second.
    * ``record_error(str)`` / ``stalled(s)`` / ``recovered(s)`` / ``fps_note(str)``.

    ``start()`` begins; ``stop()`` asks for an orderly shutdown (the video is
    finalised first). ``start_recording(path, clock)`` / ``stop_recording()``
    are safe to call from any thread.
    """

    frame_ready   = pyqtSignal(np.ndarray)
    state_changed = pyqtSignal(str)
    stats         = pyqtSignal(float, int)  # fps, total_frames
    record_error  = pyqtSignal(str)         # this camera's recording failed mid-session
    stalled       = pyqtSignal(float)       # no frame for this many seconds (watchdog)
    recovered     = pyqtSignal(float)       # reopened after a stall; gap in seconds
    fps_note      = pyqtSignal(str)         # asked for 60 fps, the camera gives 30

    def __init__(self, device: CameraDevice,
                 width: int = 960, height: int = 540, fps: int = 30,
                 bitrate_mbps: float = REC_BITRATE_MBPS,
                 parent: QObject | None = None, isolated: Optional[bool] = None):
        super().__init__(parent)
        self._dev = device
        self._want_w, self._want_h, self._want_fps = width, height, fps
        self._bitrate_mbps = float(bitrate_mbps)
        self._isolated = isolated_capture() if isolated is None else bool(isolated)
        self._preview_w = 0
        self._stop_flag = False
        self._cmds: "queue.SimpleQueue" = queue.SimpleQueue()   # → the loop / process
        self._loop = None
        self._recording = False
        self._rec_pending = False          # asked to start, not started yet
        self._stop_pending = False         # asked to stop, not finalised yet
        self._dropped_frames = 0
        self.frame_hook = None             # callback(frame_index, t_monotonic) per recorded frame
        import brain_battle.capture.timing as timing
        self.monitor = timing.RateMonitor(device.name, 1.0 / max(1, fps), unit="fps")
        self._energy_lock = threading.Lock()
        self._energy: deque = deque(maxlen=int(ENERGY_KEEP_SEC * 120))
        self._last_frame_t: Optional[float] = None
        self.stall_events: list = []       # [(t_monotonic, "stalled"|"recovered", seconds)]
        self.actual_fps: float = float(fps)
        self.fps_fallback = False
        self.queue_peak = 0
        self.queue_capacity = REC_QUEUE_MAXSIZE
        self.process_pid: Optional[int] = None

    # ── commands (any thread) ──
    def set_preview_width(self, w: int) -> None:
        self._preview_w = max(0, int(w))
        self._cmds.put(("preview_width", self._preview_w))

    def stop(self) -> None:
        self._stop_flag = True
        self._cmds.put(("stop", None))

    def start_recording(self, path: str | Path,
                        clock: Optional[tuple[float, float]] = None) -> None:
        """Start recording into ``path`` (picked up on the next frame).
        ``clock`` = the shared capture-time origin (t0_monotonic, t0_unix)."""
        self._rec_pending = True
        self._cmds.put(("start_recording", (str(path), clock)))

    def stop_recording(self) -> None:
        if not (self._recording or self._rec_pending):
            return
        self._stop_pending = True
        self._cmds.put(("stop_recording", None))

    # ── state the app reads ──
    @property
    def device(self) -> CameraDevice:
        return self._dev

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def recording_finalised(self) -> bool:
        """True once no recording is running AND the last one's video +
        sidecar are fully written and closed (safe to read from disk)."""
        return not (self._recording or self._rec_pending or self._stop_pending)

    @property
    def dropped_frames(self) -> int:
        """Frames dropped this recording because the encoder couldn't keep up
        (bounded-queue overflow). 0 is the healthy case."""
        return self._dropped_frames

    @property
    def seconds_since_frame(self) -> Optional[float]:
        if self._last_frame_t is None:
            return None
        return _clock.monotonic() - self._last_frame_t

    def energy_since(self, t0: float) -> tuple[np.ndarray, np.ndarray]:
        """(frame times, frame-difference energy) for frames after ``t0``."""
        with self._energy_lock:
            rows = [r for r in self._energy if r[0] >= t0]
        if not rows:
            return np.zeros(0), np.zeros(0)
        a = np.asarray(rows, dtype=float)
        return a[:, 0], a[:, 1]

    # ── what the loop reports ──
    def _apply(self, kind: str, payload) -> None:  # noqa: C901 — one switch
        if kind == "frames":
            sim = None
            # (read time, stamp on the camera's fitted clock, energy, sim delay):
            # the live rate / jitter use the read times (that is the jitter);
            # the buzzer test uses the stamps, like the sidecar
            with self._energy_lock:
                for _t_read, t_stamp, e, _delay in payload:
                    self._energy.append((t_stamp, e))
            for t_read, t_stamp, _e, delay in payload:
                self.monitor.add(t_read)
                if delay is not None:
                    if sim is None:
                        import brain_battle.capture.simulation as sim
                    # ground truth = the delay the recording carries: the
                    # frame's stamp minus the instant it shows
                    sim.note_delivered(sim.camera_key(self._dev.index), t_read,
                                       delay - (t_read - t_stamp))
            if payload:
                self._last_frame_t = payload[-1][0]
        elif kind == "preview":
            self.frame_ready.emit(payload)
        elif kind == "written":
            hook = self.frame_hook
            if hook is not None:
                for idx, t in payload:
                    try:
                        hook(idx, t)
                    except Exception:
                        pass
        elif kind == "stats":
            self.stats.emit(float(payload[0]), int(payload[1]))
        elif kind == "queue":
            self.queue_peak, self.queue_capacity = int(payload[0]), int(payload[1])
        elif kind == "dropped":
            self._dropped_frames = int(payload[1])
            self.monitor.add_dropped(payload[0])
        elif kind == "rec_started":
            self._recording = True; self._rec_pending = False; self._dropped_frames = 0
        elif kind == "rec_finalised":
            self._recording = self._rec_pending = self._stop_pending = False
        elif kind == "record_error":
            self._recording = self._rec_pending = False
            self.record_error.emit(str(payload))
        elif kind == "state":
            self.state_changed.emit(str(payload))
        elif kind == "fps":
            self.actual_fps = float(payload[0]); self.fps_fallback = bool(payload[1])
            self.monitor.nominal = 1.0 / max(1.0, self.actual_fps)
        elif kind == "fps_note":
            self.fps_note.emit(str(payload))
        elif kind == "stalled":
            self.stalled.emit(float(payload))
        elif kind == "recovered":
            self.recovered.emit(float(payload))
        elif kind == "stall_event":
            self.stall_events.append(tuple(payload))
        elif kind == "log":
            level, name, msg = payload
            logging.getLogger(name).log(level, "[camera process] %s", msg)

    # ── thread body ──
    def run(self) -> None:
        if self._isolated:
            self._run_process()
        else:
            self._run_in_thread()

    def _run_in_thread(self) -> None:
        from brain_battle.capture.camera_worker import CaptureLoop
        loop = CaptureLoop(self._dev, self._want_w, self._want_h, self._want_fps,
                           self._bitrate_mbps, out=self._apply, batch_sec=0.0)
        self._loop = loop
        relay = threading.Thread(target=self._relay_to_loop, args=(loop,),
                                 name="cam-commands", daemon=True)
        relay.start()
        try:
            loop.run()
        finally:
            self._stop_flag = True
            self._cmds.put(("stop", None))
            relay.join(timeout=1.0)

    def _relay_to_loop(self, loop) -> None:
        while True:
            kind, payload = self._cmds.get()
            if kind == "stop":
                loop.stop(); return
            if kind == "start_recording":
                loop.start_recording(*payload)
            elif kind == "stop_recording":
                loop.stop_recording()
            elif kind == "preview_width":
                loop.set_preview_width(payload)

    def _run_process(self) -> None:
        import brain_battle.capture.simulation as simulation
        from brain_battle.capture import camera_worker
        ctx = _mp_context()
        parent_end, child_end = ctx.Pipe(duplex=True)
        env = {k: v for k, v in os.environ.items() if k.startswith("BRAIN_BATTLE_")}
        env.setdefault("BRAIN_BATTLE_REC_ENCODER", _resolve_encoder())   # probed once, here
        proc = ctx.Process(target=camera_worker.main, name=f"camera-{self._dev.index}",
                           args=(child_end, self._dev, self._want_w, self._want_h,
                                 self._want_fps, self._bitrate_mbps, env), daemon=True)
        try:
            with _MAIN_LOCK:
                with _no_main_reimport():
                    proc.start()
        except Exception as e:
            log.error("cam idx=%s: could not start the camera process: %r", self._dev.index, e)
            self.state_changed.emit(f"error: could not start camera {self._dev.index}")
            return
        finally:
            child_end.close()
        self.process_pid = proc.pid

        def taps(times, clear=False):
            self._cmds.put(("clear_taps", None) if clear else ("taps", list(times)))
        sim_on = simulation.enabled()
        if sim_on:
            simulation.add_tap_listener(taps)
            current = simulation.taps_between(-1e18, 1e18)
            if current:
                taps(current)
        if self._preview_w:
            self._cmds.put(("preview_width", self._preview_w))
        stopping_since = None
        try:
            while True:
                # commands → the camera process
                while True:
                    try:
                        cmd = self._cmds.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        parent_end.send(cmd)
                    except (OSError, EOFError, BrokenPipeError):
                        pass
                    if cmd[0] == "stop" and stopping_since is None:
                        stopping_since = time.monotonic()
                # what it reports → signals / numbers
                try:
                    if parent_end.poll(0.02):
                        kind, payload = parent_end.recv()
                        if kind == "exit":
                            break
                        self._apply(kind, payload)
                        continue
                except (OSError, EOFError):
                    break                       # the camera process has gone
                if not proc.is_alive():
                    break
                if stopping_since is not None and time.monotonic() - stopping_since > 20.0:
                    log.error("cam idx=%s: the camera process did not stop — ending it",
                              self._dev.index)
                    break
        finally:
            if sim_on:
                simulation.remove_tap_listener(taps)
            proc.join(timeout=5.0)
            if proc.is_alive():
                proc.terminate(); proc.join(timeout=2.0)
            exitcode = proc.exitcode        # read before close(): it raises after
            try:
                parent_end.close()
            except Exception:
                pass
            if hasattr(proc, "close"):
                try:
                    proc.close()
                except Exception:
                    pass
            if self._recording or self._rec_pending or self._stop_pending:
                self._recording = self._rec_pending = self._stop_pending = False
                if exitcode not in (0, None) and not self._stop_flag:
                    self.record_error.emit("The camera stopped unexpectedly. The footage "
                                           "saved up to that point has been kept.")
            self.state_changed.emit("stopped")


# ─── the camera processes ─────────────────────────────────────────────────────

_MAIN_LOCK = threading.Lock()


def isolated_capture() -> bool:
    """Run each camera in a process of its own (the default; ADR-0025).
    ``BRAIN_BATTLE_CAMERA_PROCESS=0`` runs them as threads of the app."""
    return os.environ.get("BRAIN_BATTLE_CAMERA_PROCESS", "1").strip().lower() not in (
        "0", "false", "no", "off")


def capture_debug() -> set:
    """Debug-only switches for experiments (tools/soak_test.py):
    ``BRAIN_BATTLE_CAPTURE_DEBUG=no-preview,null-encoder``."""
    return {w.strip() for w in os.environ.get("BRAIN_BATTLE_CAPTURE_DEBUG", "").split(",")
            if w.strip()}


def _mp_context():
    import multiprocessing as mp
    return mp.get_context("spawn")       # never fork a process that runs Qt


class _no_main_reimport:
    """A spawned process normally re-runs the starting script (``__main__``)
    before calling its target. The camera process needs nothing from it, and a
    test script or the app would do real work on import — so, while the
    process is being started, ``__main__`` is made to look like an interactive
    session (no file, no module name) and multiprocessing skips it."""

    def __enter__(self):
        import sys
        m = sys.modules.get("__main__")
        self._m = m
        self._saved = {}
        if m is not None:
            for k in ("__file__", "__spec__"):
                if k in m.__dict__:
                    self._saved[k] = m.__dict__[k]
            m.__dict__.pop("__file__", None)
            m.__dict__["__spec__"] = None
        return self

    def __exit__(self, *exc):
        m = self._m
        if m is not None:
            m.__dict__.pop("__spec__", None)
            m.__dict__.update(self._saved)
        return False


__all__ = ["CameraDevice", "USBCameraThread", "FrameEncoder",
           "preflight_recording", "list_cameras", "isolated_capture", "capture_debug"]
