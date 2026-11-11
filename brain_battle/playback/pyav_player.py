"""
pyav_player.py
──────────────
App-driven multi-camera playback (DESIGN §6 / docs/archive/PLAYBACK_PROPOSAL.md Option
C). ONE playhead `t` on the `t_session_seconds` axis drives every camera: each
tile shows the frame whose sidecar `t_session_seconds` is the latest ≤ t. There
is no per-player clock and no A/V sync — our videos have no audio — so video,
EEG, and the timeline are the same number by construction, correct even on the
drift camera (whose true per-frame times live in its sidecar).

Three layers:
  • PyAVCamera       — one file: sidecar times + frame-accurate seek/decode.
  • MultiCamDecoder  — the 4 cameras; decode_all(t) → {i: rgb ndarray}. Pure,
                       synchronous, no Qt (what the correctness/fps tests use).
  • DecodeWorker     — a QThread that coalesces seek requests (only the latest
                       pending t is decoded) and emits ready frames as QImages.
  • PlayerController — a QObject whose playhead is timed against the REAL
                       monotonic clock (not tick counts), so 1× stays true 1×;
                       transport (play/pause, step one frame, speed).
"""
from __future__ import annotations

import os
import threading
import time

import numpy as np

FRAME_PERIOD = 1.0 / 30.0   # one "frame" of the session for ← / → stepping


# ── one camera file ───────────────────────────────────────────────────────────

class PyAVCamera:
    """Open a camera MP4 + its sidecar; decode the frame for a session time."""

    def __init__(self, mp4_path: str, name: str = ""):
        self.path = mp4_path
        self.name = name
        self.missing = not (mp4_path and os.path.isfile(mp4_path))
        self.times = np.zeros(0, dtype=float)   # t_session_seconds per frame
        self._container = None
        self._stream = None
        self._fps = 30.0
        self._last_index = None
        self._last_frame = None
        if not self.missing:
            try:
                self._open()
                self._load_sidecar()
            except Exception:
                self.missing = True

    def _open(self):
        import av
        self._container = av.open(self.path)
        self._stream = self._container.streams.video[0]
        self._stream.thread_type = "AUTO"
        rate = self._stream.average_rate
        self._fps = float(rate) if rate else 30.0

    def _load_sidecar(self):
        side = os.path.splitext(self.path)[0] + ".timestamps.csv"
        if not os.path.isfile(side):
            self.times = np.zeros(0, dtype=float)
            return
        import csv
        ts = []
        with open(side, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    ts.append(float(row["t_session_seconds"]))
                except (KeyError, TypeError, ValueError):
                    pass
        self.times = np.asarray(ts, dtype=float)

    @property
    def n_frames(self) -> int:
        return int(self.times.shape[0])

    @property
    def last_t(self) -> float:
        return float(self.times[-1]) if self.n_frames else 0.0

    @property
    def first_t(self) -> float:
        return float(self.times[0]) if self.n_frames else 0.0

    def index_for_t(self, t: float):
        """Latest frame index whose sidecar time ≤ t (None before the first
        frame; clamped to the last frame for an interrupted recording)."""
        if self.n_frames == 0:
            return None
        i = int(np.searchsorted(self.times, t, side="right") - 1)
        if i < 0:
            return None
        return min(i, self.n_frames - 1)

    def t_for_index(self, i: int) -> float:
        return float(self.times[i])

    def decode_index(self, idx):
        """Return the rgb24 ndarray for frame `idx` (None if missing/not yet
        started). Caches the last decode so re-requesting the same frame is
        free."""
        if idx is None or self.missing:
            return None
        if idx == self._last_index and self._last_frame is not None:
            return self._last_frame
        frame = self._decode(idx)
        if frame is not None:
            self._last_index = idx
            self._last_frame = frame
        return frame

    def _decode(self, target: int):
        import av
        stream = self._stream
        tb = stream.time_base
        target_sec = target / self._fps
        try:
            offset = int(target_sec / tb)
            self._container.seek(offset, stream=stream, backward=True,
                                 any_frame=False)
        except av.AVError:
            try:
                self._container.seek(0, stream=stream, backward=True)
            except av.AVError:
                return None
        best = None
        try:
            for frame in self._container.decode(stream):
                if frame.pts is None:
                    best = frame
                    continue
                idx = int(round(float(frame.pts * tb) * self._fps))
                best = frame
                if idx >= target:
                    break
        except av.AVError:
            if best is None:
                return None
        if best is None:
            return None
        return best.to_ndarray(format="rgb24")

    def close(self):
        try:
            if self._container is not None:
                self._container.close()
        except Exception:
            pass
        self._container = None


# ── the four cameras ───────────────────────────────────────────────────────────

class MultiCamDecoder:
    def __init__(self, cameras: list[PyAVCamera]):
        self.cameras = cameras

    @property
    def duration(self) -> float:
        return max((c.last_t for c in self.cameras if c.n_frames), default=0.0)

    def indices_for_t(self, t: float) -> list:
        return [c.index_for_t(t) for c in self.cameras]

    def decode_all(self, t: float) -> dict:
        """{camera_index: rgb ndarray} for the frame-at-t of each camera."""
        out = {}
        for i, cam in enumerate(self.cameras):
            frame = cam.decode_index(cam.index_for_t(t))
            if frame is not None:
                out[i] = frame
        return out

    def close(self):
        for c in self.cameras:
            c.close()


# ── Qt layers (imported lazily so the pure layers above need no Qt) ─────────────

def _np_to_qimage(arr):
    from PyQt6.QtGui import QImage
    h, w, _ = arr.shape
    arr = np.ascontiguousarray(arr)
    return QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


try:
    from PyQt6.QtCore import QObject, QThread, pyqtSignal, QTimer

    class DecodeWorker(QThread):
        """Decodes off the UI thread. Only the LATEST requested playhead is
        decoded: requests arriving while a decode runs overwrite the pending
        one (seek coalescing), so a flurry of scrubs costs a couple of decodes,
        not one per scrub."""
        frames_ready = pyqtSignal(float, dict)   # (t, {i: QImage})

        def __init__(self, decoder: MultiCamDecoder, parent=None):
            super().__init__(parent)
            self._decoder = decoder
            self._cond = threading.Condition()
            self._pending_t = None
            self._have_req = False
            self._stop = False
            self.decode_count = 0   # batches actually decoded (for the scrub test)
            self.last_t = None      # last playhead actually decoded

        def request(self, t: float):
            with self._cond:
                self._pending_t = float(t)
                self._have_req = True
                self._cond.notify()

        def run(self):
            while True:
                with self._cond:
                    while not self._have_req and not self._stop:
                        self._cond.wait()
                    if self._stop:
                        return
                    t = self._pending_t
                    self._have_req = False
                frames = self._decoder.decode_all(t)
                self.decode_count += 1
                self.last_t = t
                qimgs = {i: _np_to_qimage(a) for i, a in frames.items()}
                self.frames_ready.emit(t, qimgs)

        def stop(self):
            with self._cond:
                self._stop = True
                self._cond.notify()
            self.wait(2000)

    class PlayerController(QObject):
        """Single-clock transport. The playhead is derived from the real
        monotonic clock each tick, so playback speed is honest regardless of
        how fast frames actually decode (we skip frames, never time)."""
        playhead_changed = pyqtSignal(float)        # t (seconds)
        frames_ready     = pyqtSignal(float, dict)  # (t, {i: QImage})
        state_changed    = pyqtSignal(bool)         # playing?

        def __init__(self, decoder: MultiCamDecoder, duration: float, parent=None):
            super().__init__(parent)
            self._decoder = decoder
            self.duration = float(duration)
            self._t = 0.0
            self._speed = 1.0
            self._playing = False
            self._wall0 = 0.0
            self._t0 = 0.0
            self._worker = DecodeWorker(decoder)
            self._worker.frames_ready.connect(self.frames_ready)
            self._worker.start()
            self._timer = QTimer(self)
            self._timer.setInterval(16)   # ~60 Hz request rate; pacing is clock-based
            self._timer.timeout.connect(self._tick)

        # ---- state ----
        @property
        def t(self) -> float:
            return self._t

        @property
        def playing(self) -> bool:
            return self._playing

        @property
        def speed(self) -> float:
            return self._speed

        # ---- transport ----
        def play(self):
            if self._playing or self.duration <= 0:
                return
            if self._t >= self.duration:
                self._t = 0.0
            self._wall0 = time.monotonic()
            self._t0 = self._t
            self._playing = True
            self._timer.start()
            self.state_changed.emit(True)

        def pause(self):
            if not self._playing:
                return
            self._playing = False
            self._timer.stop()
            self.state_changed.emit(False)

        def toggle(self):
            self.pause() if self._playing else self.play()

        def set_speed(self, speed: float):
            self._speed = float(speed)
            if self._playing:           # re-anchor so the change is seamless
                self._wall0 = time.monotonic()
                self._t0 = self._t

        def seek(self, t: float):
            self._set_t(max(0.0, min(float(t), self.duration)))
            if self._playing:
                self._wall0 = time.monotonic()
                self._t0 = self._t

        def step(self, n_frames: int):
            self.pause()
            self.seek(self._t + n_frames * FRAME_PERIOD)

        # ---- internals ----
        def _tick(self):
            if not self._playing:
                return
            elapsed = time.monotonic() - self._wall0
            t = self._t0 + self._speed * elapsed
            if t >= self.duration:
                t = self.duration
                self._set_t(t)
                self.pause()
                return
            self._set_t(t)

        def _set_t(self, t: float):
            self._t = t
            self._worker.request(t)
            self.playhead_changed.emit(t)

        def shutdown(self):
            self._timer.stop()
            self._worker.stop()
            self._decoder.close()

except ImportError:   # pragma: no cover - PyQt6 always present in this app
    pass


def open_session_cameras(camera_specs: list[tuple[str, str]]) -> MultiCamDecoder:
    """camera_specs: list of (mp4_path, display_name). Missing files become
    tiles that report 'No video for this camera'."""
    return MultiCamDecoder([PyAVCamera(p, n) for p, n in camera_specs])


__all__ = ["PyAVCamera", "MultiCamDecoder", "DecodeWorker", "PlayerController",
           "open_session_cameras", "FRAME_PERIOD"]
