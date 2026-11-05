"""
camera_source.py
────────────────
One camera "slot" for the guided recording flow: it owns a real
``USBCameraThread`` (or its simulated stand-in when BRAIN_BATTLE_SIMULATE is
on — same thread, synthetic frames), tracks live state (preview image, fps,
recording / dropped-frame / encoder-error status), and exposes a tiny API the
Connect and Recording step widgets render as views.

This is deliberately a QObject, not a widget: the flow owns the four slots
persistently across steps (you connect cameras in step 1 and they keep running
into step 3), and each step paints the current `last_image` / status itself.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage

import app_settings
from usb_camera import USBCameraThread, CameraDevice

# Slot specs mirror usb_camera_panel._SLOT_SPECS so the recorded filenames and
# the Review screen's camera names line up exactly.
SLOT_SPECS = [
    (1, "Side camera 1", "side"),
    (2, "Side camera 2", "side"),
    (3, "Boxer A view",  "pov"),
    (4, "Boxer B view",  "pov"),
]
# File stem per slot (matches the old USBCameraPanel: "SIDE 01" etc.).
_SLOT_STEM = {1: "SIDE_01", 2: "SIDE_02", 3: "POV_01", 4: "POV_02"}
_SLOT_SUB = {"side": "Side Cams", "pov": "POV Cams"}

PREVIEW_WIDTH = 480   # preview frames downscaled to this before crossing threads


class CameraSlot(QObject):
    changed = pyqtSignal()           # preview frame or status changed

    def __init__(self, index: int, title: str, kind: str, parent=None):
        super().__init__(parent)
        self.index = index
        self.title = title
        self.kind = kind
        self.device: Optional[CameraDevice] = None
        self._thread: Optional[USBCameraThread] = None
        self.state = "empty"          # empty|opening|live|error
        self.state_detail = ""
        self.fps = 0.0
        self.dropped = 0
        self.rec_failed = False
        self.rec_error_msg = ""
        self.last_image: Optional[QImage] = None

    # ── lifecycle ──
    def assign(self, device: Optional[CameraDevice]) -> None:
        self.device = device
        if device is None:
            self.state = "empty"
        self.changed.emit()

    def start(self) -> None:
        if self.device is None or self._thread is not None:
            return
        settings = app_settings.load()
        w, h, fps = settings.video_dims()
        self.rec_failed = False
        self.state = "opening"; self.state_detail = ""
        t = USBCameraThread(self.device, width=w, height=h, fps=fps,
                           bitrate_mbps=settings.video_mbps(), parent=self)
        t.set_preview_width(PREVIEW_WIDTH)
        t.frame_ready.connect(self._on_frame)
        t.state_changed.connect(self._on_state)
        t.stats.connect(self._on_stats)
        t.record_error.connect(self._on_rec_error)
        t.finished.connect(self._on_finished)
        self._thread = t
        t.start()
        self.changed.emit()

    def stop(self) -> None:
        if self._thread is not None:
            self._thread.stop()

    def shutdown(self) -> None:
        if self._thread is not None:
            self._thread.stop()
            self._thread.wait(2500)
            self._thread = None

    # ── recording ──
    def recording_path(self, continuous_dir) -> str:
        sub = _SLOT_SUB.get(self.kind, "Side Cams")
        return str(continuous_dir / sub / f"session_{_SLOT_STEM[self.index]}.mp4")

    def start_recording(self, path, clock) -> None:
        if self._thread is None:
            return
        self.rec_failed = False; self.dropped = 0
        self._thread.start_recording(path, clock=clock)

    def stop_recording(self) -> None:
        if self._thread is not None:
            self._thread.stop_recording()

    # ── state ──
    @property
    def is_live(self) -> bool:
        return self._thread is not None and self.state == "live"

    @property
    def is_recording(self) -> bool:
        return self._thread is not None and self._thread.is_recording

    # ── signal handlers ──
    def _on_frame(self, frame: np.ndarray) -> None:
        try:
            rgb = np.ascontiguousarray(frame[:, :, ::-1])
            h, w = rgb.shape[0], rgb.shape[1]
            img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
            self.last_image = img
        except Exception:
            pass
        self.changed.emit()

    def _on_state(self, state: str) -> None:
        if state == "live":
            self.state = "live"; self.state_detail = ""
        elif state == "opening":
            self.state = "opening"
        elif state.startswith("error"):
            self.state = "error"; self.state_detail = state[7:].strip() or state
        self.changed.emit()

    def _on_stats(self, fps: float, total: int) -> None:
        self.fps = fps
        if self._thread is not None and self._thread.is_recording and not self.rec_failed:
            self.dropped = self._thread.dropped_frames
        self.changed.emit()

    def _on_rec_error(self, msg: str) -> None:
        self.rec_failed = True
        self.rec_error_msg = msg
        self.changed.emit()

    def _on_finished(self) -> None:
        # A thread's finished signal can be delivered LATE — after we've already
        # stopped it and started a replacement (e.g. rapid re-scans). Only clear
        # the handle if the thread that finished is the one we currently hold;
        # otherwise we'd null a reference to a live thread and orphan it (it then
        # runs un-tracked and aborts the process when destroyed).
        t = self.sender()
        if t is not None and t is not self._thread:
            return
        self._thread = None
        self.fps = 0.0
        if self.state != "error":
            self.state = "empty" if self.device is None else "opening"
        self.changed.emit()


def make_slots(parent=None) -> list[CameraSlot]:
    return [CameraSlot(i, title, kind, parent) for (i, title, kind) in SLOT_SPECS]


__all__ = ["CameraSlot", "make_slots", "SLOT_SPECS", "PREVIEW_WIDTH"]
