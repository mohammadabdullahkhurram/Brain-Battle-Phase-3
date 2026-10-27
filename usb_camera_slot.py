"""
usb_camera_slot.py
──────────────────
Live-preview widget for one USB / capture-card camera.

Complements usb_camera.USBCameraThread: that module delivers BGR frames via
signal, this module takes them and draws them on a pyqtgraph ImageItem at
60 FPS with negligible CPU. It also owns the start/stop recording controls
and exposes a minimal API the LiveSessionTab can call at phase boundaries.

Why pyqtgraph ImageItem?
    The research notes show ImageItem renders ~75-150× faster than
    matplotlib's AxesImage for video-rate updates (pyqtgraph SciPy 2023
    paper, Figure 4). That matters when you have four live 1080p30 feeds
    plus two live EEG panels competing for the Qt event loop.

Design
    * One header strip: device name, status dot, live FPS, record indicator
    * Main area: pyqtgraph ViewBox hosting a single ImageItem (row-major,
      invertY so (0,0) is top-left as cameras expect)
    * Bottom strip (collapsible): "CHOOSE DEVICE" and "CONNECT/DISCONNECT"
    * Status dot colors mirror the RTMPSlot's visual language so operators
      see the same cues across both camera-ingest paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore    import Qt, pyqtSignal
from PyQt6.QtGui     import QFont
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QWidget, QDialog, QListWidget, QListWidgetItem,
    QDialogButtonBox,
)

from usb_camera import CameraDevice, USBCameraThread, list_cameras


# Row-major axis order is what you want for camera frames: arrays are (H, W, C)
# natively, origin at (0, 0) in the top-left. Applied once, module-level.
pg.setConfigOptions(imageAxisOrder='row-major')


# ─── Shared visual language — sourced live from design_tokens ────────────────

import design_tokens as dt

# These are functions (not constants) so they re-resolve each call after a
# theme switch. Old code that referenced `_BG` etc. as identifiers has been
# updated throughout this module to call `_bg()` / `_border()` / etc.

def _bg():           return dt.card_bg()
def _panel_border(): return dt.border()
def _text_dim():     return dt.text_secondary()
def _text():         return dt.text_primary()
def _accent():       return dt.accent()
def _error():        return dt.error_color()
def _ok():           return dt.success_color()
def _header_bg():    return dt.canvas_bg()
def _footer_bg():    return dt.canvas_bg()

# Legacy aliases (read-once snapshot, kept only for accidental in-module refs)
_BG           = dt.card_bg()
_PANEL_BORDER = dt.border()
_TEXT_DIM     = dt.text_secondary()
_TEXT         = dt.text_primary()
_ACCENT       = dt.accent()
_ERROR        = dt.error_color()
_OK           = dt.success_color()


def _mono(size=11, letter=1):
    f = QFont("JetBrains Mono"); f.setPointSize(size); return f


# ─── Device picker dialog ─────────────────────────────────────────────────────

class CameraPickerDialog(QDialog):
    """Modal dialog that enumerates cameras and lets the operator pick one."""

    def __init__(self, parent: Optional[QWidget] = None,
                 claimed_indices: Optional[set[int]] = None):
        super().__init__(parent)
        self.setWindowTitle("Select USB Camera")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setStyleSheet(
            "QDialog { background-color: #F6F7F9; }"
            "QLabel { color: #15171C; font-family: 'JetBrains Mono'; font-size: 11px; }"
            "QListWidget { background-color: #F6F7F9; border: 1px solid #DFE2E8;"
            "              color: #15171C; font-family: 'JetBrains Mono'; font-size: 12px;"
            "              outline: none; }"
            "QListWidget::item { padding: 8px 12px; border-bottom: 1px solid #DFE2E8; }"
            "QListWidget::item:selected { background-color: #EEF0FD; color: #3D4FD6;"
            "                             border-left: 2px solid #3D4FD6; }"
            "QPushButton { background-color: #FFFFFF; color: #15171C;"
            "              border: 1px solid #DFE2E8; padding: 8px 18px;"
            "              font-family: 'JetBrains Mono'; font-size: 11px; letter-spacing: 1px; }"
            "QPushButton:hover { background-color: #DFE2E8; border-color: #3A404B; }"
        )
        self._claimed = claimed_indices or set()
        self._selected: Optional[CameraDevice] = None
        self._build()
        self._refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self); root.setContentsMargins(18, 14, 18, 14); root.setSpacing(10)
        hdr = QLabel("AVAILABLE CAMERAS")
        hdr.setStyleSheet("color: #3D4FD6; font-size: 10px; letter-spacing: 3px;"
                          " font-family: 'JetBrains Mono';")
        root.addWidget(hdr)
        self._list = QListWidget()
        root.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        self._refresh_btn = QPushButton("RESCAN")
        self._refresh_btn.clicked.connect(self._refresh)
        btn_row.addWidget(self._refresh_btn); btn_row.addStretch()
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("USE CAMERA")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("CANCEL")
        bb.accepted.connect(self._accept); bb.rejected.connect(self.reject)
        btn_row.addWidget(bb)
        root.addLayout(btn_row)

    def _refresh(self) -> None:
        self._list.clear()
        self._list.addItem("Scanning for cameras…")
        # Defer to give UI time to show the message.
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, self._populate)

    def _populate(self) -> None:
        self._list.clear()
        devices = list_cameras(max_probe=8)
        if not devices:
            self._list.addItem("No cameras detected. Check USB connections and rescan.")
            return
        for dev in devices:
            item = QListWidgetItem(f"  [{dev.index:>2}]   {dev.name}")
            item.setData(Qt.ItemDataRole.UserRole, dev)
            if dev.index in self._claimed:
                item.setText(item.text() + "    (in use)")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)

    def _accept(self) -> None:
        item = self._list.currentItem()
        if item is not None:
            dev = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(dev, CameraDevice):
                self._selected = dev
                self.accept()

    def chosen(self) -> Optional[CameraDevice]:
        return self._selected


# ─── USB camera slot ──────────────────────────────────────────────────────────

class USBCameraSlot(QFrame):
    """
    One camera panel in the live-monitor grid. Owns a USBCameraThread and
    a pyqtgraph image view. Matches the RTMPSlot visual language exactly so
    the two ingest paths can sit side by side in the same grid.
    """

    # Emitted when a camera is successfully opened. The int is the device
    # index, used by the parent grid to de-duplicate claims across slots.
    device_claimed   = pyqtSignal(int)
    device_released  = pyqtSignal(int)

    def __init__(self, slot_index: int, label: str = "CAM", parent=None):
        super().__init__(parent)
        self.slot_index = slot_index
        self.label      = label
        self._thread:   Optional[USBCameraThread] = None
        self._device:   Optional[CameraDevice]    = None
        self._last_fps: float = 0.0
        self._rec_failed: bool = False
        self._build()
        self._restyle()

    # ── UI ──
    def _build(self) -> None:
        self.setStyleSheet(
            f"USBCameraSlot {{ background-color: {_bg()};"
            f" border: 1px solid {_panel_border()};"
            f" border-radius: {dt.RADIUS_MD}px; }}"
        )
        self.setMinimumSize(240, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # Header strip.
        self._hdr = QWidget(); self._hdr.setFixedHeight(32)
        self._hdr.setStyleSheet(
            f"background-color: {_header_bg()}; "
            f"border-bottom: 1px solid {_panel_border()};"
        )
        hl = QHBoxLayout(self._hdr); hl.setContentsMargins(12, 0, 12, 0); hl.setSpacing(8)

        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {_text_dim()}; font-size: 11px;")
        self._name_lbl = QLabel(self.label)
        self._name_lbl.setStyleSheet(
            f"color: {_text_dim()}; font-size: 10px; letter-spacing: 2px;"
            f" font-family: {dt.FONT_UI}; font-weight: 600;"
        )
        self._status_lbl = QLabel("NO SOURCE")
        self._status_lbl.setStyleSheet(
            f"color: {dt.text_tertiary()}; font-size: 9px; letter-spacing: 1px;"
            f" font-family: {dt.FONT_UI};"
        )
        hl.addWidget(self._dot); hl.addWidget(self._name_lbl)
        hl.addSpacing(12); hl.addWidget(self._status_lbl); hl.addStretch()
        # FPS + recording pill
        self._fps_lbl = QLabel("")
        self._fps_lbl.setStyleSheet(
            f"color: {_text_dim()}; font-size: 9px; letter-spacing: 0.4px;"
            f" font-family: {dt.FONT_MONO};"
        )
        self._rec_lbl = QLabel("")
        self._rec_lbl.setStyleSheet(
            f"color: {_error()}; font-size: 9px; letter-spacing: 1.4px;"
            f" font-family: {dt.FONT_UI}; font-weight: 600;"
        )
        hl.addWidget(self._fps_lbl); hl.addSpacing(8); hl.addWidget(self._rec_lbl)
        root.addWidget(self._hdr)

        # Image view: direct ImageItem inside a ViewBox is both simpler and
        # considerably faster than pg.ImageView for pure video playback.
        self._gfx_widget = pg.GraphicsLayoutWidget()
        self._gfx_widget.setBackground(_bg())
        self._viewbox = self._gfx_widget.addViewBox(
            lockAspect=True, invertY=True, enableMenu=False,
        )
        self._viewbox.setMouseEnabled(x=False, y=False)
        self._viewbox.setBackgroundColor(_bg())
        self._image_item = pg.ImageItem(axisOrder='row-major')
        self._viewbox.addItem(self._image_item)
        root.addWidget(self._gfx_widget, 1)

        self._placeholder = QLabel(f"[ {self.label} ]\nNO DEVICE")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            f"color: {dt.text_tertiary()}; font-size: 14px; letter-spacing: 2px;"
            f" font-family: {dt.FONT_UI}; background-color: {_bg()};"
        )
        self._placeholder.setParent(self._gfx_widget)
        self._placeholder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # Footer strip with SELECT + CONNECT buttons.
        self._footer = QWidget(); self._footer.setFixedHeight(32)
        self._footer.setStyleSheet(
            f"background-color: {_footer_bg()}; "
            f"border-top: 1px solid {_panel_border()};"
        )
        fl = QHBoxLayout(self._footer); fl.setContentsMargins(12, 0, 12, 0); fl.setSpacing(8)
        self._device_lbl = QLabel("—")
        self._device_lbl.setStyleSheet(
            f"color: {_text_dim()}; font-size: 10px; font-family: {dt.FONT_UI};"
        )
        fl.addWidget(self._device_lbl); fl.addStretch()
        self._pick_btn = QPushButton("SELECT")
        self._pick_btn.setFixedHeight(24)
        self._pick_btn.setStyleSheet(dt.qss_secondary_button() +
            f" QPushButton {{ font-size: 10px; padding: 0 12px; }}")
        self._pick_btn.clicked.connect(self._open_picker)
        self._connect_btn = QPushButton("CONNECT")
        self._connect_btn.setFixedHeight(24)
        self._connect_btn.setEnabled(False)
        self._connect_btn.setStyleSheet(dt.qss_secondary_button() +
            f" QPushButton {{ font-size: 10px; padding: 0 12px; }}")
        self._connect_btn.clicked.connect(self._toggle_connect)
        fl.addWidget(self._pick_btn); fl.addWidget(self._connect_btn)
        root.addWidget(self._footer)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # Keep the placeholder overlaid on the graphics widget.
        self._placeholder.setGeometry(self._gfx_widget.rect())

    # ── Device picking ──
    def _open_picker(self) -> None:
        dlg = CameraPickerDialog(self, claimed_indices=self._claimed_indices_snapshot())
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.chosen() is not None:
            self._device = dlg.chosen()
            self._device_lbl.setText(f"{self._device.name}  (idx {self._device.index})")
            self._device_lbl.setStyleSheet(
                f"color: {_text()}; font-size: 10px; font-family: {dt.FONT_UI};"
            )
            self._connect_btn.setEnabled(True)

    def _restyle(self) -> None:
        """Re-apply all stylesheets from live tokens (theme switch)."""
        self.setStyleSheet(
            f"USBCameraSlot {{ background-color: {_bg()};"
            f" border: 1px solid {_panel_border()};"
            f" border-radius: {dt.RADIUS_MD}px; }}"
        )
        self._hdr.setStyleSheet(
            f"background-color: {_header_bg()}; "
            f"border-bottom: 1px solid {_panel_border()};"
        )
        self._dot.setStyleSheet(f"color: {_text_dim()}; font-size: 11px;")
        self._name_lbl.setStyleSheet(
            f"color: {_text_dim()}; font-size: 10px; letter-spacing: 2px;"
            f" font-family: {dt.FONT_UI}; font-weight: 600;"
        )
        self._status_lbl.setStyleSheet(
            f"color: {dt.text_tertiary()}; font-size: 9px; letter-spacing: 1px;"
            f" font-family: {dt.FONT_UI};"
        )
        self._fps_lbl.setStyleSheet(
            f"color: {_text_dim()}; font-size: 9px; letter-spacing: 0.4px;"
            f" font-family: {dt.FONT_MONO};"
        )
        self._rec_lbl.setStyleSheet(
            f"color: {_error()}; font-size: 9px; letter-spacing: 1.4px;"
            f" font-family: {dt.FONT_UI}; font-weight: 600;"
        )
        self._gfx_widget.setBackground(_bg())
        self._viewbox.setBackgroundColor(_bg())
        self._placeholder.setStyleSheet(
            f"color: {dt.text_tertiary()}; font-size: 14px; letter-spacing: 2px;"
            f" font-family: {dt.FONT_UI}; background-color: {_bg()};"
        )
        self._footer.setStyleSheet(
            f"background-color: {_footer_bg()}; "
            f"border-top: 1px solid {_panel_border()};"
        )
        self._device_lbl.setStyleSheet(
            f"color: {_text_dim() if self._device is None else _text()}; "
            f"font-size: 10px; font-family: {dt.FONT_UI};"
        )
        self._pick_btn.setStyleSheet(dt.qss_secondary_button() +
            f" QPushButton {{ font-size: 10px; padding: 0 12px; }}")
        self._connect_btn.setStyleSheet(dt.qss_secondary_button() +
            f" QPushButton {{ font-size: 10px; padding: 0 12px; }}")

    def _claimed_indices_snapshot(self) -> set[int]:
        """Snapshot of currently-claimed camera indices across sibling slots."""
        claimed: set[int] = set()
        parent = self.parent()
        if parent is not None and hasattr(parent, "findChildren"):
            for sibling in parent.findChildren(USBCameraSlot):
                if sibling is self:
                    continue
                if sibling._device is not None and sibling._thread is not None:
                    claimed.add(sibling._device.index)
        return claimed

    # ── Connect / disconnect ──
    def _toggle_connect(self) -> None:
        if self._thread is None:
            self._start_thread()
        else:
            self._stop_thread()

    def _start_thread(self) -> None:
        if self._device is None:
            return
        self._rec_failed = False
        self._thread = USBCameraThread(self._device, parent=self)
        self._thread.frame_ready.connect(self._on_frame)
        self._thread.state_changed.connect(self._on_state)
        self._thread.stats.connect(self._on_stats)
        self._thread.record_error.connect(self._on_record_error)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()
        self._connect_btn.setText("DISCONNECT")
        self._connect_btn.setStyleSheet(
            f"background-color: transparent; color: {_ERROR}; border: 1px solid {_ERROR};"
            f" font-family: 'JetBrains Mono'; font-size: 10px; padding: 0 12px;"
        )
        self._pick_btn.setEnabled(False)
        self.device_claimed.emit(self._device.index)

    def _stop_thread(self) -> None:
        if self._thread is None:
            return
        self._thread.stop()
        # The finished signal will clean up. Don't block the UI.
        self._connect_btn.setEnabled(False)
        self._connect_btn.setText("STOPPING…")

    def _on_thread_finished(self) -> None:
        idx = self._device.index if self._device else -1
        self._thread = None
        self._last_fps = 0.0
        self._fps_lbl.setText("")
        self._status_lbl.setText("NO SOURCE")
        self._status_lbl.setStyleSheet(
            f"color: #C9CED7; font-size: 9px; letter-spacing: 1px;"
            f" font-family: 'JetBrains Mono';"
        )
        self._dot.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 11px;")
        self._placeholder.setVisible(True)
        self._connect_btn.setText("CONNECT")
        self._connect_btn.setEnabled(self._device is not None)
        self._connect_btn.setStyleSheet(
            "background-color: #EEF0FD; color: #3D4FD6; border: 1px solid #3D4FD6;"
            " font-family: 'JetBrains Mono'; font-size: 10px; padding: 0 12px;"
        )
        self._pick_btn.setEnabled(True)
        if idx >= 0:
            self.device_released.emit(idx)

    # ── Signal handlers ──
    def _on_frame(self, frame: np.ndarray) -> None:
        # OpenCV returns BGR (H, W, 3). pyqtgraph's ImageItem in row-major
        # mode wants (H, W, 3) RGB. Convert channels only; no transpose.
        # autoLevels=False + explicit levels=(0,255) skips per-frame stats
        # (critical for keeping 30 FPS), and keeps 8-bit uint data displayed
        # at full range instead of being normalised to its own min/max.
        rgb = frame[:, :, ::-1]
        self._image_item.setImage(rgb, autoLevels=False, levels=(0, 255))
        if self._placeholder.isVisible():
            self._placeholder.setVisible(False)
            self._viewbox.autoRange(padding=0)

    def _on_state(self, state: str) -> None:
        if state == "live":
            self._dot.setStyleSheet(f"color: {_OK}; font-size: 11px;")
            self._status_lbl.setText("LIVE")
            self._status_lbl.setStyleSheet(
                f"color: {_OK}; font-size: 9px; letter-spacing: 2px;"
                f" font-family: 'JetBrains Mono';"
            )
        elif state == "opening":
            self._dot.setStyleSheet(f"color: {_ACCENT}; font-size: 11px;")
            self._status_lbl.setText("OPENING…")
        elif state.startswith("error"):
            self._dot.setStyleSheet(f"color: {_ERROR}; font-size: 11px;")
            self._status_lbl.setText(state.upper()[:40])
            self._status_lbl.setStyleSheet(
                f"color: {_ERROR}; font-size: 9px; letter-spacing: 1px;"
                f" font-family: 'JetBrains Mono';"
            )

    def _on_stats(self, fps: float, total: int) -> None:
        self._last_fps = fps
        self._fps_lbl.setText(f"{fps:>4.1f} FPS")
        # Surface dropped frames while recording so the operator can see the
        # encoder falling behind (healthy = no count shown).
        if self._thread is not None and self._thread.is_recording \
                and not getattr(self, "_rec_failed", False):
            dropped = self._thread.dropped_frames
            self._rec_lbl.setText(f"● REC  ({dropped} dropped)" if dropped else "● REC")
            self._rec_lbl.setStyleSheet(
                f"color: {_error()}; font-size: 9px; letter-spacing: 1.4px;"
                f" font-family: {dt.FONT_UI}; font-weight: 600;")

    def _on_record_error(self, message: str) -> None:
        """This camera's recording failed mid-session. Mark the slot red with a
        plain message; the other cameras keep recording."""
        self._rec_failed = True
        self._dot.setStyleSheet(f"color: {_error()}; font-size: 11px;")
        self._status_lbl.setText("RECORDING STOPPED")
        self._status_lbl.setStyleSheet(
            f"color: {_error()}; font-size: 9px; letter-spacing: 1px;"
            f" font-family: {dt.FONT_UI};")
        self._rec_lbl.setText("● REC ERROR")
        self._rec_lbl.setToolTip(message)

    # ── Phase-aware recording API (called by LiveSessionTab at phase boundaries) ──
    def start_recording(self, path: str | Path, clock=None) -> None:
        if self._thread is None:
            return
        self._rec_failed = False
        self._thread.start_recording(path, clock=clock)
        self._rec_lbl.setText("● REC")

    def stop_recording(self) -> None:
        if self._thread is None:
            return
        self._thread.stop_recording()
        if not getattr(self, "_rec_failed", False):
            self._rec_lbl.setText("")

    @property
    def current_device(self) -> Optional[CameraDevice]:
        return self._device

    @property
    def is_live(self) -> bool:
        return self._thread is not None

    # Cleanly release on application shutdown.
    def shutdown(self) -> None:
        self._stop_thread()
        if self._thread is not None:
            self._thread.wait(2000)


__all__ = ["USBCameraSlot", "CameraPickerDialog"]
