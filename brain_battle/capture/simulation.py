"""
simulation.py  —  DEVELOPER-ONLY simulated cameras + headbands
──────────────────────────────────────────────────────────────
Hardware is currently broken (see AGENTS.md "Project situation"), so this
module lets the guided recording flow be built and tested end to end without
real cameras or Muse-S bands. It is gated entirely behind the
``BRAIN_BATTLE_SIMULATE`` environment variable: with it unset, nothing here is
reachable and the UI shows no trace of simulation.

**This module is NOT shipped** (see docs/ROADMAP.md Phase 6 — excluded from the
packaged app alongside tools/ and tests/).

Everything goes through the REAL capture paths, so the on-disk result is
byte-for-byte what real hardware would produce:
  • Cameras → a cv2.VideoCapture-compatible ``SimCapture`` fed to the real
    ``USBCameraThread`` (→ ffmpeg fragmented H.264 + per-frame sidecars),
    rate-limited to real time.
  • Headbands → ``SimMuseReader`` (a subclass of the real
    ``eeg_module._MuseDirectReader``) that synthesises 12-sample chunks at
    256 Hz and feeds them through the reader's real ``_on_eeg`` /
    ``start_recording`` / ``stop_recording`` path, plus a simulated battery
    telemetry signal and a deliberately weak sensor on Boxer B so the
    contact-quality warnings can be seen.

Failure injection (also env-gated, for the failure-path screens/tests):
  • BRAIN_BATTLE_SIMULATE_MISSING_CAMERA=1  → only 3 cameras enumerate.
  • BRAIN_BATTLE_SIMULATE_DROP_HEADBAND=<s> → Boxer B's band disconnects <s>
    seconds into recording (default 8 when set to "1").
"""
from __future__ import annotations

import os
import threading
import time

import numpy as np

# Weak sensor shown on Boxer B's band (DESIGN: TP10 = right ear).
_WEAK_SENSOR = "TP10"
_WEAK_BOXER = "B"


# ─── gating ───────────────────────────────────────────────────────────────────

def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _truthy(os.environ.get("BRAIN_BATTLE_SIMULATE"))


def missing_camera() -> bool:
    return enabled() and _truthy(os.environ.get("BRAIN_BATTLE_SIMULATE_MISSING_CAMERA"))


def drop_headband_after() -> float | None:
    """Seconds into recording that Boxer B's band should drop, or None."""
    if not enabled():
        return None
    v = os.environ.get("BRAIN_BATTLE_SIMULATE_DROP_HEADBAND")
    if not v:
        return None
    if _truthy(v):
        return 8.0
    try:
        return float(v)
    except ValueError:
        return None


# ─── simulated cameras ────────────────────────────────────────────────────────

def camera_devices():
    """Enumerate simulated capture devices as real CameraDevice records so the
    picker / auto-assign treats them like hardware. One fewer when the
    missing-camera failure is on."""
    from brain_battle.capture.usb_camera import CameraDevice
    names = ["Simulated Side camera 1", "Simulated Side camera 2",
             "Simulated Boxer A view", "Simulated Boxer B view"]
    if missing_camera():
        names = names[:3]
    return [CameraDevice(index=200 + i, name=n, backend=0)
            for i, n in enumerate(names)]


class SimCapture:
    """cv2.VideoCapture-compatible synthetic source. ``read()`` blocks until the
    next frame is due, pacing capture to real time exactly as a physical camera
    would, so the real USBCameraThread timing/sidecar logic is exercised."""

    def __init__(self, index: int, w: int = 960, h: int = 540, fps: float = 30.0):
        self._index = int(index)
        self._w, self._h = int(w), int(h)
        self._fps = float(fps) or 30.0
        self._open = True
        self._t0 = time.monotonic()
        self._n = 0
        self._label = f"SIM CAM {self._index}"
        # Pre-render a static background so each read() is cheap.
        self._bg = np.full((self._h, self._w, 3), 22, dtype=np.uint8)

    # cv2.VideoCapture surface used by USBCameraThread.run() ───────────────────
    def isOpened(self):  # noqa: N802
        return self._open

    def set(self, prop, val):  # noqa: N802
        import cv2
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            self._w = int(val) or self._w
        elif prop == cv2.CAP_PROP_FRAME_HEIGHT:
            self._h = int(val) or self._h
        elif prop == cv2.CAP_PROP_FPS:
            self._fps = float(val) or self._fps
        return True

    def get(self, prop):  # noqa: N802
        import cv2
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._w)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._h)
        if prop == cv2.CAP_PROP_FPS:
            return float(self._fps)
        return 0.0

    def read(self):  # noqa: N802
        if not self._open:
            return False, None
        # Pace to real time: wait until this frame's nominal instant.
        due = self._t0 + self._n / self._fps
        delay = due - time.monotonic()
        if delay > 0:
            time.sleep(min(delay, 0.1))
        frame = self._render(self._n)
        self._n += 1
        return True, frame

    def release(self):  # noqa: N802
        self._open = False

    # ── frame rendering (cheap; a moving bar + burned-in clock) ──
    def _render(self, n):
        import cv2
        img = self._bg.copy()
        t = n / self._fps
        bar_x = int((n * 6) % self._w)
        cv2.rectangle(img, (bar_x, 0), (bar_x + 40, self._h), (60, 70, 160), -1)
        cv2.putText(img, self._label, (28, 70), cv2.FONT_HERSHEY_SIMPLEX,
                    1.1, (235, 235, 235), 2, cv2.LINE_AA)
        cv2.putText(img, f"{t:6.2f}s  f{n}", (28, 130), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9, (180, 190, 210), 2, cv2.LINE_AA)
        return img


def open_capture(device, w=960, h=540, fps=30.0):
    """cv2.VideoCapture replacement for a simulated CameraDevice."""
    return SimCapture(getattr(device, "index", 0), w=w, h=h, fps=fps)


# ─── simulated headbands ──────────────────────────────────────────────────────

def muse_devices():
    """Two simulated Muse-S bands whose names match the default headband
    mapping, so they auto-assign to Boxer A / Boxer B."""
    import brain_battle.data.app_settings as app_settings
    s = app_settings.load()
    out = []
    for who in ("A", "B"):
        dev = s.headband_for(who) or f"MuseS-000{who}"
        out.append({"name": dev, "address": f"sim-{who}", "boxer": who})
    return out


_SIM_READER_CLS = None


def _sim_reader_cls():
    """Define SimMuseReader lazily (subclassing the real _MuseDirectReader), so
    importing this module doesn't pull in Qt/eeg_module until a simulated reader
    is actually created."""
    global _SIM_READER_CLS
    if _SIM_READER_CLS is not None:
        return _SIM_READER_CLS

    from PyQt6.QtCore import pyqtSignal
    from brain_battle.capture.eeg_module import _MuseDirectReader

    class SimMuseReader(_MuseDirectReader):
        """Generates synthetic 256 Hz EEG and feeds it through the real reader's
        ``_on_eeg`` path (so recording/flush are the real code). Emits
        ``connected``/``samples`` like the real reader plus a ``battery``
        telemetry signal. Boxer B's right-ear sensor is kept weak so the
        contact-quality warning is visible; optionally drops mid-recording."""

        battery = pyqtSignal(int)

        def __init__(self, address, name="", boxer="", parent=None):
            super().__init__(address=address, name=name, parent=parent)
            self._boxer = (boxer or "").upper()
            self._sim_running = False
            self._rec_start_mono = None
            self._battery_level = 82 if self._boxer == "A" else 76

        def start_recording(self, path, origin_unix=None, append=False):
            self._rec_start_mono = time.monotonic()
            return super().start_recording(path, origin_unix=origin_unix, append=append)

        def stop(self):
            self._sim_running = False
            self._running = False

        def run(self):
            self._running = True
            self._sim_running = True
            self._first_chunk_seen = False
            sr, chunk = 256, 12
            rng = np.random.default_rng(abs(hash(self.name)) % (2**32))
            t_start = time.monotonic()
            sample_i = 0
            drop_after = drop_headband_after()
            last_batt = 0.0
            ph = rng.uniform(0, 2 * np.pi)
            self._emit_battery(self._battery_level)
            while self._running and self._sim_running:
                now = time.monotonic()
                due = int((now - t_start) * sr)
                if due - sample_i < chunk:
                    time.sleep(chunk / sr / 2)
                    continue
                data = self._make_chunk(sample_i, chunk, sr, rng, ph)
                ts = time.time() + np.arange(chunk) / sr
                try:
                    self._on_eeg(data, ts.astype(np.float64))
                except Exception:
                    pass
                sample_i += chunk
                if now - last_batt > 3.0:
                    last_batt = now
                    self._battery_level = max(1, self._battery_level - 1)
                    self._emit_battery(self._battery_level)
                if (drop_after is not None and self._boxer == _WEAK_BOXER
                        and self._rec_start_mono is not None
                        and now - self._rec_start_mono > drop_after):
                    self._running = False
                    try:
                        self.error.emit("The headband turned off or moved out of range.")
                    except Exception:
                        pass
                    return

        def _make_chunk(self, start, n, sr, rng, ph):
            t = (start + np.arange(n)) / sr
            data = np.zeros((5, n), dtype=np.float32)
            for ci, ch in enumerate(("TP9", "AF7", "AF8", "TP10")):
                sig = rng.normal(0.0, 8.0, n) + 3.0 * np.sin(2 * np.pi * 10.0 * t + ph + ci)
                if self._boxer == _WEAK_BOXER and ch == _WEAK_SENSOR:
                    sig = rng.normal(0.0, 0.1, n)   # near-flat → "weak contact"
                data[ci] = sig
            data[4] = rng.normal(0.0, 4.0, n)       # AUX
            return data

        def _emit_battery(self, level):
            try:
                self.battery.emit(int(level))
            except Exception:
                pass

    _SIM_READER_CLS = SimMuseReader
    return _SIM_READER_CLS


def make_muse_reader(address: str, name: str, boxer: str = "", parent=None):
    """A simulated reader standing in for _MuseDirectReader over fake BLE."""
    return _sim_reader_cls()(address=address, name=name, boxer=boxer, parent=parent)


__all__ = [
    "enabled", "missing_camera", "drop_headband_after",
    "camera_devices", "SimCapture", "open_capture",
    "muse_devices", "make_muse_reader",
]
