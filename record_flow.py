"""
record_flow.py
──────────────
The guided recording flow (DESIGN §2–§5): a full-screen, sidebar-free flow with
its own top bar (Cancel + a 3-step indicator) and four steps —

    1. Connect devices   (cameras + headbands, live previews + status)
    2. Set up session    (names, steppers, "what will happen", disk check)
    3. Recording         (big countdown, health line, mark moments, stop)
    4. Saved             (a real-data checklist + "worth checking" + actions)

It replaces the old dense Live Monitor. The real capture machinery is reused
unchanged (SessionController for phase timing, session_storage for the on-disk
layout, USBCameraThread / _MuseDirectReader via CameraSlot / HeadbandSlot for
recording). Under BRAIN_BATTLE_SIMULATE the devices are simulated through those
same real paths; with the flag unset there is no trace of simulation.

Public surface used by the app shell:
  • RecordFlow(parent)
  • .start()                 — (re)enter at step 1, scan + connect devices
  • .session_saved (signal)  — "Start labelling" on the Saved screen (root path)
  • .exit_flow (signal)      — Cancel / "Back to sessions"
  • .is_recording()          — for the app's quit-confirmation
  • .shutdown()              — stop devices/threads before teardown
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QFont, QImage, QPolygonF, QIcon
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
    QSizePolicy, QLineEdit, QCheckBox, QScrollArea, QMessageBox, QStackedWidget,
    QDialog, QListWidget, QListWidgetItem, QDialogButtonBox,
)

import logging

import design_tokens as dt
import app_settings
import camera_source
import contact_log
import session_history
from headband_source import HeadbandSlot
from camera_source import CameraSlot
from session_controller import SessionController, SessionConfig, Phase, _build_timeline
from session_storage import (
    create_session, SessionLayout, PhaseRecord, estimate_session_bytes,
    human_bytes, session_total_seconds, disk_space_verdict,
    video_bytes_per_sec_for_mbps,
)
from sync_beep import SyncBeep
from keep_awake import KeepAwake
from log_setup import attach_session_log
from usb_camera import list_cameras, preflight_recording, CameraDevice

log = logging.getLogger(__name__)

_ILLEGAL = re.compile(r'[<>:"/\\|?*]')


# ── small style helpers ──────────────────────────────────────────────────────

def _title(text, size=24):
    l = QLabel(text)
    l.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
                    f"font-size: {size}px; font-weight: 700;")
    return l


def _sub(text):
    l = QLabel(text); l.setWordWrap(True)
    l.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                    f"font-size: {dt.SIZE_MD}px;")
    return l


def _body(text, color=None, size=None, bold=False):
    l = QLabel(text); l.setWordWrap(True)
    l.setStyleSheet(f"color: {color or dt.text_secondary()}; font-family: {dt.FONT_UI}; "
                    f"font-size: {size or dt.SIZE_SM}px; "
                    f"font-weight: {'700' if bold else '400'};")
    return l


def _card() -> QFrame:
    c = QFrame(); c.setObjectName("card")
    c.setStyleSheet(f"QFrame#card {{ background: {dt.card_bg()}; "
                    f"border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_LG}px; }}")
    return c


def _mmss(sec) -> str:
    sec = max(0, int(round(sec))); m, s = divmod(sec, 60)
    if m >= 60:
        h, m = divmod(m, 60); return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _boxer_badge(which: str, size=22) -> QLabel:
    line, soft, text = dt.boxer_colors(which)
    b = QLabel(which); b.setFixedSize(size, size)
    b.setAlignment(Qt.AlignmentFlag.AlignCenter)
    b.setStyleSheet(f"background: {soft}; color: {text}; border: 1px solid {line}; "
                    f"border-radius: {size // 2}px; font-weight: 700; font-size: 11px;")
    return b


# ── step indicator ───────────────────────────────────────────────────────────

class _StepIndicator(QWidget):
    _STEPS = ["Connect devices", "Set up session", "Record"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = 0
        self._labels = []
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
        for i, name in enumerate(self._STEPS):
            chip = QLabel(f"{i + 1}  {name}")
            chip.setMinimumHeight(30)
            self._labels.append(chip)
            h.addWidget(chip)
            if i < len(self._STEPS) - 1:
                arr = QLabel("›")
                arr.setStyleSheet(f"color: {dt.text_tertiary()}; font-size: 16px;")
                h.addWidget(arr)
        self.set_active(0)

    def set_active(self, idx: int):
        self._active = idx
        for i, chip in enumerate(self._labels):
            if i == idx:
                chip.setStyleSheet(
                    f"background: {dt.accent_ghost()}; color: {dt.accent()}; "
                    f"border: 1px solid {dt.accent()}; border-radius: {dt.RADIUS_FULL}px; "
                    f"padding: 5px 14px; font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; "
                    f"font-weight: 700;")
            else:
                done = i < idx
                col = dt.success_color() if done else dt.text_tertiary()
                chip.setStyleSheet(
                    f"background: transparent; color: {col}; "
                    f"border: 1px solid {dt.border_strong()}; border-radius: {dt.RADIUS_FULL}px; "
                    f"padding: 5px 14px; font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; "
                    f"font-weight: {'600' if done else '400'};")


# ── camera tile ───────────────────────────────────────────────────────────────

class CameraTile(QFrame):
    """Live preview + status for one CameraSlot. ``compact`` is the small
    recording-screen variant (no Change button, terse status)."""
    change_requested = pyqtSignal(int)

    def __init__(self, slot: CameraSlot, compact: bool = False, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.compact = compact
        self.setMinimumSize(200, 150 if not compact else 110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._change = None
        if not compact:
            self._change = QPushButton("Change", self)
            self._change.setCursor(Qt.CursorShape.PointingHandCursor)
            self._change.setStyleSheet(
                "QPushButton { background: rgba(255,255,255,0.92); color: #15171C; "
                "border: 1px solid #C9CED7; border-radius: 8px; font-size: 11px; "
                "padding: 4px 10px; } QPushButton:hover { border-color: #3D4FD6; }")
            self._change.clicked.connect(lambda: self.change_requested.emit(self.slot.index))

    def resizeEvent(self, e):
        if self._change is not None:
            self._change.adjustSize()
            self._change.move(self.width() - self._change.width() - 8, 8)
        super().resizeEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = self.rect().adjusted(1, 1, -1, -1)
            missing = self.slot.device is None or self.slot.state == "error"
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor("#10131A"))
            p.drawRoundedRect(QRectF(r), dt.RADIUS_MD, dt.RADIUS_MD)
            img = self.slot.last_image
            if img is not None and not img.isNull() and not missing:
                iw, ih = img.width(), img.height()
                sc = min(r.width() / iw, r.height() / ih)
                dw, dh = iw * sc, ih * sc
                x = r.x() + (r.width() - dw) / 2; y = r.y() + (r.height() - dh) / 2
                p.drawImage(QRectF(x, y, dw, dh), img)
            else:
                p.setPen(QColor("#9AA3AF")); p.setFont(QFont("Inter", 10))
                msg = ("Not found. Check the USB cable and the video receiver, "
                       "then press Search again." if missing else "Connecting…")
                p.drawText(QRectF(r).adjusted(12, 12, -12, -12),
                           Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, msg)
            # red border when missing
            if missing:
                p.setPen(QColor(dt.record_red())); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(r), dt.RADIUS_MD, dt.RADIUS_MD)
            # name chip + status line on the picture
            p.setFont(QFont("Inter", 10, QFont.Weight.DemiBold))
            name = self.slot.title
            tw = p.fontMetrics().horizontalAdvance(name) + 16
            p.setBrush(QColor(0, 0, 0, 150)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(r.x() + 8, r.y() + 8, tw, 22), 6, 6)
            p.setPen(QColor("#FFFFFF")); p.drawText(int(r.x() + 16), int(r.y() + 23), name)
            # status at the bottom
            icon, txt, col = self._status()
            p.setFont(QFont("Inter", 9))
            sw = p.fontMetrics().horizontalAdvance(icon + " " + txt) + 16
            p.setBrush(QColor(0, 0, 0, 150)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(r.x() + 8, r.bottom() - 28, sw, 20), 6, 6)
            p.setPen(QColor(col)); p.drawText(int(r.x() + 16), int(r.bottom() - 14), icon + " " + txt)
        finally:
            p.end()

    def _status(self):
        s = self.slot
        if s.device is None:
            return "✕", "Not connected", "#FCA5A5"
        if s.state == "error":
            return "✕", "Not found", "#FCA5A5"
        if s.rec_failed:
            return "✕", "Recording stopped", "#FCA5A5"
        if s.is_recording:
            d = f" · {s.dropped} frames lost" if s.dropped else ""
            return "●", f"Recording{d}", "#FCA5A5" if s.dropped else "#86EFAC"
        if s.state == "live":
            return "✓", f"Connected · {s.fps:.0f} frames per second", "#86EFAC"
        return "…", "Connecting", "#FDE68A"


# ── live mini EEG trace ────────────────────────────────────────────────────────

class MiniTrace(QWidget):
    """Four thin stacked channel lines for one HeadbandSlot's rolling window."""

    def __init__(self, slot: HeadbandSlot, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.setMinimumHeight(54)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            W, H = self.width(), self.height()
            p.fillRect(self.rect(), QColor(dt.canvas_bg()))
            ts, chans = self.slot.trace()
            if not self.slot.connected or ts is None or not chans:
                p.setPen(QColor(dt.text_tertiary())); p.setFont(QFont("Inter", 9))
                msg = "Waiting for signal…" if self.slot.device else "No headband"
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, msg)
                return
            cols = ["#0E7490", "#3D4FD6", "#7C3AED", "#B45309"]
            keys = list(chans.keys())
            band_h = H / max(1, len(keys))
            import numpy as np
            for ci, ch in enumerate(keys):
                seg = chans[ch]
                if len(seg) < 2:
                    continue
                seg = seg[-min(len(seg), 6 * 256):]
                y_mid = band_h * ci + band_h / 2
                scale = max(1.0, 3.0 * float(np.std(seg)))
                n = len(seg)
                xs = (np.arange(n) / max(1, n - 1)) * W
                ys = y_mid - np.clip(seg / scale, -1, 1) * (band_h * 0.42)
                poly = QPolygonF()
                step = max(1, n // (W + 1))
                for k in range(0, n, step):
                    poly.append(QPointF(float(xs[k]), float(ys[k])))
                p.setPen(QColor(cols[ci % 4])); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPolyline(poly)
        finally:
            p.end()


# ── headband view (badge + battery + 4 sensor chips + trace) ───────────────────

_SENSOR_CHIPS = [("TP9", "Left ear"), ("AF7", "Left forehead"),
                 ("AF8", "Right forehead"), ("TP10", "Right ear")]
_WEAK_TIPS = {
    "Left ear": "Move the band so the sensor sits flat behind the left ear. "
                "Brush hair out of the way.",
    "Right ear": "Move the band so the sensor sits flat behind the right ear. "
                 "Brush hair out of the way.",
    "Left forehead": "Press the left side of the band gently against the forehead; "
                     "wipe away sweat or makeup.",
    "Right forehead": "Press the right side of the band gently against the forehead; "
                      "wipe away sweat or makeup.",
}


class HeadbandView(QFrame):
    """One boxer's headband block (DESIGN §2): badge + name + battery, four
    sensor chips with a plain tip when weak, and a live trace. ``compact`` drops
    the trace height for the recording screen."""
    reconnect_requested = pyqtSignal(str)

    def __init__(self, slot: HeadbandSlot, name_getter, compact=False, parent=None):
        super().__init__(parent)
        self.slot = slot
        self._name_getter = name_getter
        self.setObjectName("card")
        self.setStyleSheet(f"QFrame#card {{ background: {dt.card_bg()}; "
                           f"border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_LG}px; }}")
        v = QVBoxLayout(self); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(8)
        head = QHBoxLayout(); head.setSpacing(8)
        head.addWidget(_boxer_badge(slot.which))
        self._title = QLabel(""); self._title.setStyleSheet(
            f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_MD}px; font-weight: 600;")
        head.addWidget(self._title); head.addStretch()
        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                                   f"font-size: {dt.SIZE_XS}px;")
        head.addWidget(self._status)
        self._reconnect = QPushButton("Reconnect")
        self._reconnect.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reconnect.setStyleSheet(dt.qss_secondary_button())
        self._reconnect.setVisible(False)
        self._reconnect.clicked.connect(lambda: self.reconnect_requested.emit(slot.which))
        head.addWidget(self._reconnect)
        v.addLayout(head)

        chips = QHBoxLayout(); chips.setSpacing(6)
        self._chips = {}
        for code, plain in _SENSOR_CHIPS:
            chip = QLabel(plain); chip.setMinimumHeight(24)
            chip.setToolTip("")
            self._chips[code] = chip
            chips.addWidget(chip)
        chips.addStretch()
        v.addLayout(chips)
        self._tip = QLabel(""); self._tip.setWordWrap(True)
        self._tip.setStyleSheet(f"color: {dt.warning_color()}; font-family: {dt.FONT_UI}; "
                                f"font-size: {dt.SIZE_XS}px;")
        self._tip.setVisible(False)
        v.addWidget(self._tip)
        self._trace = MiniTrace(slot)
        self._trace.setMinimumHeight(44 if compact else 64)
        v.addWidget(self._trace, 1)
        self.refresh()

    def refresh(self):
        s = self.slot
        name = self._name_getter(s.which)
        dev = (s.device or {}).get("name", "") if s.device else ""
        head_txt = f"Boxer {s.which}" + (f" · {name}" if name else "")
        if dev:
            head_txt += f" · {dev}"
        self._title.setText(head_txt)
        if s.lost:
            self._status.setText("✕ Lost connection")
            self._status.setStyleSheet(f"color: {dt.error_color()}; font-family: {dt.FONT_UI}; "
                                       f"font-size: {dt.SIZE_XS}px; font-weight: 600;")
            self._reconnect.setVisible(True)
        elif s.connected:
            batt = f" · battery {s.battery}%" if s.battery is not None else ""
            self._status.setText(f"✓ Connected{batt}")
            self._status.setStyleSheet(f"color: {dt.success_color()}; font-family: {dt.FONT_UI}; "
                                       f"font-size: {dt.SIZE_XS}px;")
            self._reconnect.setVisible(False)
        elif s.device is not None:
            self._status.setText("Connecting…")
            self._reconnect.setVisible(False)
        else:
            self._status.setText("Not switched on")
            self._reconnect.setVisible(False)
        # sensor chips from the live contact heuristic
        summary, statuses = s.contact() if s.connected else ("", {})
        weak_names = []
        for code, plain in _SENSOR_CHIPS:
            chip = self._chips[code]
            st = statuses.get(code, None)
            if not s.connected or st is None:
                chip.setText(plain)
                chip.setStyleSheet(self._chip_css(dt.text_tertiary(), dt.border_strong()))
            elif st == "good":
                chip.setText(f"✓ {plain}")
                chip.setStyleSheet(self._chip_css(dt._p().success_text, dt.success_color(),
                                                  dt._p().success_bg))
            else:
                chip.setText(f"! {plain}")
                chip.setStyleSheet(self._chip_css(dt._p().warning_text, dt.warning_color(),
                                                  dt._p().warning_bg))
                weak_names.append(plain)
        if weak_names:
            tip = _WEAK_TIPS.get(weak_names[0], "Re-seat the sensor so it sits flat "
                                 "against the skin.")
            self._tip.setText(f"Weak contact: {weak_names[0].lower()}. {tip}")
            self._tip.setVisible(True)
        else:
            self._tip.setVisible(False)
        self._trace.update()

    def _chip_css(self, fg, border, bg="transparent"):
        return (f"background: {bg}; color: {fg}; border: 1px solid {border}; "
                f"border-radius: {dt.RADIUS_SM}px; padding: 3px 8px; "
                f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_XS}px;")


# ── phase bar + stepper ────────────────────────────────────────────────────────

class PhaseBar(QWidget):
    """Proportional phase strip (rest/round/break) with an optional now-marker."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._segments = []    # (duration, phase_id)
        self._total = 1.0
        self._now = None       # fraction 0..1 or None

    def set_segments(self, segments):
        self._segments = list(segments)
        self._total = max(0.001, sum(d for d, _ in self._segments))
        self.update()

    def set_now(self, frac):
        self._now = None if frac is None else max(0.0, min(1.0, frac))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            W, H = self.width(), self.height()
            pal = dt._p()
            colmap = {"baseline": pal.phase_rest[0], "round": pal.phase_round[0],
                      "break": pal.phase_break[0]}
            x = 0.0
            for dur, pid in self._segments:
                w = W * (dur / self._total)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(colmap.get(pid, pal.phase_rest[0])))
                p.drawRoundedRect(QRectF(x + 1, 2, max(1, w - 2), H - 4), 4, 4)
                x += w
            if self._now is not None:
                nx = W * self._now
                p.setPen(QColor(dt.record_red()))
                p.drawLine(int(nx), 0, int(nx), H)
        finally:
            p.end()


class Stepper(QFrame):
    """− value +  stepper. Value is an int (seconds or a count); `fmt` renders it."""
    changed = pyqtSignal()

    def __init__(self, caption, value, lo, hi, step, fmt, parent=None):
        super().__init__(parent)
        self._value = value; self._lo = lo; self._hi = hi; self._step = step
        self._fmt = fmt
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(4)
        cap = QLabel(caption)
        cap.setStyleSheet(f"color: {dt.text_secondary()}; font-family: {dt.FONT_UI}; "
                          f"font-size: {dt.SIZE_XS}px; font-weight: 600;")
        v.addWidget(cap)
        row = QHBoxLayout(); row.setSpacing(6)
        self._minus = self._btn("−"); self._plus = self._btn("+")
        self._minus.clicked.connect(lambda: self._bump(-self._step))
        self._plus.clicked.connect(lambda: self._bump(self._step))
        self._val = QLabel(""); self._val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val.setMinimumWidth(78); self._val.setMinimumHeight(38)
        self._val.setStyleSheet(
            f"background: {dt.surface()}; color: {dt.text_primary()}; "
            f"border: 1px solid {dt.border_strong()}; border-radius: {dt.RADIUS_MD}px; "
            f"font-family: {dt.FONT_MONO}; font-size: {dt.SIZE_LG}px; font-weight: 600;")
        row.addWidget(self._minus); row.addWidget(self._val); row.addWidget(self._plus)
        row.addStretch()
        v.addLayout(row)
        self._render()

    def _btn(self, txt):
        b = QPushButton(txt); b.setFixedSize(38, 38); b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(dt.qss_secondary_button() +
                        f" QPushButton {{ font-size: 18px; padding: 0; }}")
        return b

    def _bump(self, d):
        self._value = max(self._lo, min(self._hi, self._value + d))
        self._render(); self.changed.emit()

    def _render(self):
        self._val.setText(self._fmt(self._value))
        self._minus.setEnabled(self._value > self._lo)
        self._plus.setEnabled(self._value < self._hi)

    def value(self):
        return self._value

    def set_value(self, v):
        self._value = max(self._lo, min(self._hi, v)); self._render()


# ── camera picker ──────────────────────────────────────────────────────────────

class _CameraPicker(QDialog):
    def __init__(self, claimed: set, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose a camera")
        self.setMinimumWidth(420)
        self.chosen: Optional[CameraDevice] = None
        v = QVBoxLayout(self); v.setContentsMargins(18, 16, 18, 16); v.setSpacing(12)
        v.addWidget(_body("Pick the camera for this view.", bold=True))
        self._list = QListWidget(); v.addWidget(self._list, 1)
        for d in list_cameras():
            it = QListWidgetItem(f"  {d.name}")
            it.setData(Qt.ItemDataRole.UserRole, d)
            if d.index in claimed:
                it.setText(it.text() + "   (in use)")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(it)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Use this camera")
        bb.accepted.connect(self._accept); bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _accept(self):
        it = self._list.currentItem()
        if it is not None:
            d = it.data(Qt.ItemDataRole.UserRole)
            if isinstance(d, CameraDevice):
                self.chosen = d; self.accept()


# ── Step 1 — Connect devices ────────────────────────────────────────────────────

class ConnectStep(QWidget):
    def __init__(self, flow, parent=None):
        super().__init__(parent)
        self.flow = flow
        root = QVBoxLayout(self); root.setContentsMargins(40, 28, 40, 20); root.setSpacing(14)
        root.addWidget(_title("Connect the cameras and headbands"))
        root.addWidget(_sub("Plug in the cameras and switch both headbands on. When "
                            "everything shows a green tick, you are ready."))
        body = QHBoxLayout(); body.setSpacing(16)

        cam_card = _card()
        cv = QVBoxLayout(cam_card); cv.setContentsMargins(14, 12, 14, 14); cv.setSpacing(8)
        cv.addWidget(_body("Cameras", bold=True, size=dt.SIZE_MD))
        grid = QGridLayout(); grid.setSpacing(10)
        self._tiles = []
        for i, slot in enumerate(flow._cams):
            tile = CameraTile(slot, compact=False)
            tile.change_requested.connect(flow.open_camera_picker)
            grid.addWidget(tile, i // 2, i % 2)
            self._tiles.append(tile)
        cv.addLayout(grid, 1)
        body.addWidget(cam_card, 3)

        hb_card = _card()
        hv = QVBoxLayout(hb_card); hv.setContentsMargins(14, 12, 14, 14); hv.setSpacing(10)
        hv.addWidget(_body("Headbands", bold=True, size=dt.SIZE_MD))
        self._hbviews = []
        for which in ("A", "B"):
            view = HeadbandView(flow._bands[which], flow.boxer_name)
            view.reconnect_requested.connect(flow.reconnect_band)
            hv.addWidget(view)
            self._hbviews.append(view)
        hv.addStretch()
        body.addWidget(hb_card, 2)
        root.addLayout(body, 1)

        bar = QHBoxLayout(); bar.setSpacing(12)
        info = QVBoxLayout(); info.setSpacing(1)
        self._summary = _body("", bold=True, size=dt.SIZE_MD)
        self._explain = _body("", size=dt.SIZE_XS, color=dt.text_tertiary())
        info.addWidget(self._summary); info.addWidget(self._explain)
        bar.addLayout(info); bar.addStretch()
        again = QPushButton("Search again"); again.setMinimumHeight(44)
        again.setCursor(Qt.CursorShape.PointingHandCursor)
        again.setStyleSheet(dt.qss_secondary_button())
        again.clicked.connect(flow.search_again)
        bar.addWidget(again)
        cont = QPushButton("Continue  →"); cont.setMinimumHeight(44)
        cont.setCursor(Qt.CursorShape.PointingHandCursor)
        cont.setStyleSheet(dt.qss_primary_button())
        cont.clicked.connect(flow.goto_setup)
        bar.addWidget(cont)
        root.addLayout(bar)

    def on_tick(self):
        for t in self._tiles:
            t.update()
        for v in self._hbviews:
            v.refresh()
        self._refresh_summary()

    def _refresh_summary(self):
        cams = sum(1 for s in self.flow._cams if s.is_live)
        bands = sum(1 for b in self.flow._bands.values() if b.connected)
        ready = cams + bands
        self._summary.setText(f"{ready} of 6 devices ready")
        missing = []
        if cams < len(self.flow._cams):
            missing.append(f"{len(self.flow._cams) - cams} camera(s)")
        if bands < 2:
            missing.append(f"{2 - bands} headband(s)")
        if missing:
            self._explain.setText("You can continue, but " + " and ".join(missing)
                                  + " will not be recorded.")
        else:
            self._explain.setText("Everything is connected. You're ready to set up the session.")


# ── Step 2 — Set up session ──────────────────────────────────────────────────────

class SetupStep(QWidget):
    def __init__(self, flow, parent=None):
        super().__init__(parent)
        self.flow = flow
        root = QVBoxLayout(self); root.setContentsMargins(40, 28, 40, 16); root.setSpacing(12)
        root.addWidget(_title("Set up the session"))
        self._datetime = _sub("")
        root.addWidget(self._datetime)

        body = QHBoxLayout(); body.setSpacing(16)

        # left: names + steppers + bell. Both columns ignore their width hint so
        # the 3:2 stretch split is honoured and neither pushes the other
        # off-screen (the phase list / notes would otherwise force extra width).
        left = _card()
        lv = QVBoxLayout(left); lv.setContentsMargins(18, 16, 18, 16); lv.setSpacing(12)
        names = QGridLayout(); names.setHorizontalSpacing(10); names.setVerticalSpacing(10)
        names.addWidget(_boxer_badge("A"), 0, 0)
        names.addWidget(_body("Boxer A name"), 0, 1)
        self._name_a = QLineEdit(); self._name_a.setMinimumHeight(38)
        self._name_a.setStyleSheet(dt.qss_input()); self._name_a.setPlaceholderText("optional")
        names.addWidget(self._name_a, 0, 2)
        names.addWidget(_boxer_badge("B"), 1, 0)
        names.addWidget(_body("Boxer B name"), 1, 1)
        self._name_b = QLineEdit(); self._name_b.setMinimumHeight(38)
        self._name_b.setStyleSheet(dt.qss_input()); self._name_b.setPlaceholderText("optional")
        names.addWidget(self._name_b, 1, 2)
        names.setColumnStretch(2, 1)
        lv.addLayout(names)
        self._hb_note = _body("", size=dt.SIZE_XS, color=dt.text_tertiary())
        lv.addWidget(self._hb_note)

        steppers = QGridLayout(); steppers.setHorizontalSpacing(18); steppers.setVerticalSpacing(12)
        self._st_rounds = Stepper("Rounds", 3, 1, 20, 1, lambda v: str(v))
        self._st_round = Stepper("Round length", 180, 10, 1800, 30, _mmss)
        self._st_break = Stepper("Break between rounds", 60, 0, 600, 15, _mmss)
        self._st_rest = Stepper("Rest before round 1", 60, 0, 600, 15, _mmss)
        for i, st in enumerate((self._st_rounds, self._st_round, self._st_break, self._st_rest)):
            steppers.addWidget(st, i // 2, i % 2)
            st.changed.connect(self._recompute)
        lv.addLayout(steppers)
        self._bell = QCheckBox("Ring a bell when each round starts")
        self._bell.setStyleSheet(f"color: {dt.text_secondary()}; font-family: {dt.FONT_UI}; "
                                 f"font-size: {dt.SIZE_SM}px;")
        lv.addWidget(self._bell)
        lv.addStretch()
        body.addWidget(left, 1)

        # right: "What will happen" — a fixed-width side panel (like the Review
        # labels panel) so its wrapped text is bounded deterministically.
        right = _card()
        right.setFixedWidth(360)
        rv = QVBoxLayout(right); rv.setContentsMargins(18, 16, 18, 16); rv.setSpacing(10)
        rv.addWidget(_body("What will happen", bold=True, size=dt.SIZE_MD))
        self._bar = PhaseBar()
        rv.addWidget(self._bar)
        # One multi-line label added directly to the card (a single wrapped
        # label behaves; a nested sub-layout of labels mis-sizes on offscreen).
        self._phase_lines = QLabel(""); self._phase_lines.setWordWrap(True)
        self._phase_lines.setStyleSheet(
            f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_XS}px; line-height: 150%;")
        rv.addWidget(self._phase_lines)
        self._autostop = _body("", size=dt.SIZE_XS, color=dt.text_tertiary())
        rv.addWidget(self._autostop)
        self._disk = _body("", size=dt.SIZE_SM)
        self._disk.setWordWrap(True)
        rv.addWidget(self._disk)
        self._saves_to = _body("", size=dt.SIZE_XS, color=dt.text_tertiary())
        rv.addWidget(self._saves_to)
        rv.addStretch()
        # Wrapped labels in a nested column must take the card width, never a
        # wider hint (otherwise they push the card past the window edge).
        for _lab in (self._autostop, self._disk, self._saves_to, self._hb_note):
            _lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body.addWidget(right, 0)
        root.addLayout(body, 1)

        bar = QHBoxLayout(); bar.setSpacing(12)
        back = QPushButton("←  Back"); back.setMinimumHeight(44)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setStyleSheet(dt.qss_secondary_button())
        back.clicked.connect(flow.goto_connect)
        bar.addWidget(back)
        bar.addWidget(_body("Recording starts with the rest period.",
                            size=dt.SIZE_XS, color=dt.text_tertiary()))
        bar.addStretch()
        self._start = QPushButton("●  Start recording"); self._start.setMinimumHeight(44)
        self._start.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start.setStyleSheet(dt.qss_danger_button())
        self._start.clicked.connect(flow.begin_recording)
        bar.addWidget(self._start)
        root.addLayout(bar)

    def populate_from_settings(self):
        import datetime as _dt
        s = self.flow._settings
        cfg = s.session_config()
        self._st_rounds.set_value(cfg.num_rounds)
        self._st_round.set_value(cfg.round_sec)
        self._st_break.set_value(cfg.break_sec)
        self._st_rest.set_value(cfg.baseline_sec)
        self._bell.setChecked(bool(s.round_bell))
        self._name_a.setText(self.flow._names.get("A") or "")
        self._name_b.setText(self.flow._names.get("B") or "")
        self._datetime.setText(_dt.datetime.now().strftime("%a %-d %b %Y, %H:%M")
                               + " · the recording is named by its date and time")
        hb_a = s.headband_for("A") or "a headband"
        hb_b = s.headband_for("B") or "a headband"
        self._hb_note.setText(f"Boxer A wears headband {hb_a} · Boxer B wears {hb_b}.")
        self._recompute()

    def config(self) -> SessionConfig:
        return SessionConfig(num_rounds=self._st_rounds.value(),
                             round_sec=self._st_round.value(),
                             break_sec=self._st_break.value(),
                             baseline_sec=self._st_rest.value())

    def names(self):
        return self._name_a.text().strip(), self._name_b.text().strip()

    def bell(self):
        return self._bell.isChecked()

    def _recompute(self):
        cfg = self.config()
        timeline = _build_timeline(cfg)
        segs = [(p.duration, p.phase.value) for p in timeline]
        self._bar.set_segments(segs)
        # phase list with start times (one multi-line label)
        elapsed = 0
        lines = []
        for p in timeline:
            name = {"baseline": "Rest before round 1", "round": f"Round {p.round_num}",
                    "break": "Break"}.get(p.phase.value, p.label)
            lines.append(f"{_mmss(elapsed)}   {name} · {_mmss(p.duration)}")
            elapsed += p.duration
        self._phase_lines.setText("\n".join(lines))
        total = session_total_seconds(cfg)
        self._autostop.setText(f"Recording stops by itself at {_mmss(total)}. "
                               f"Total length {_mmss(total)}.")
        self._update_disk(cfg)
        self._update_saves_to(cfg)

    def _update_disk(self, cfg):
        import shutil
        n_cam = sum(1 for s in self.flow._cams if s.is_live) or len(self.flow._cams)
        n_eeg = sum(1 for b in self.flow._bands.values() if b.connected)
        base = self.flow._settings.capture_dir_resolved()
        try:
            os.makedirs(base, exist_ok=True)
            free = shutil.disk_usage(base).free
        except OSError:
            self._disk.setText(""); self._start.setEnabled(True); return
        vbps = video_bytes_per_sec_for_mbps(self.flow._settings.video_mbps())
        est = estimate_session_bytes(cfg, n_cam, n_eeg, vbps)
        est_h, free_h = human_bytes(est), human_bytes(free)
        verdict = disk_space_verdict(cfg, n_cam, n_eeg, free, vbps)
        if verdict == "blocked":
            self._disk.setText(f"✕ Not enough space — this session needs about {est_h}, "
                               f"but only {free_h} is free. Free up space or shorten the session.")
            self._disk.setStyleSheet(f"color: {dt.error_color()}; font-family: {dt.FONT_UI}; "
                                     f"font-size: {dt.SIZE_SM}px;")
            self._start.setEnabled(False)
        elif verdict == "low":
            self._disk.setText(f"! Low space — this session needs about {est_h}. "
                               f"Only {free_h} is free.")
            self._disk.setStyleSheet(f"color: {dt.warning_color()}; font-family: {dt.FONT_UI}; "
                                     f"font-size: {dt.SIZE_SM}px;")
            self._start.setEnabled(True)
        else:
            self._disk.setText(f"✓ Enough space — this session needs about {est_h}. "
                               f"You have {free_h} free.")
            self._disk.setStyleSheet(f"color: {dt.success_color()}; font-family: {dt.FONT_UI}; "
                                     f"font-size: {dt.SIZE_SM}px;")
            self._start.setEnabled(True)

    def _update_saves_to(self, cfg):
        import datetime as _dt
        from session_storage import _session_folder_name
        a, b = self.names()
        base = self.flow._settings.capture_dir_resolved()
        folder = _session_folder_name(_dt.datetime.now(), a or None, b or None)
        self._saves_to.setText(f"Saves to {os.path.basename(base)} › {folder}")


# ── Step 3 — Recording ────────────────────────────────────────────────────────────

class RecordingStep(QWidget):
    def __init__(self, flow, parent=None):
        super().__init__(parent)
        self.flow = flow
        root = QVBoxLayout(self); root.setContentsMargins(28, 18, 28, 18); root.setSpacing(12)

        # header
        hdr = QHBoxLayout(); hdr.setSpacing(12)
        self._rec_pill = QLabel("●  Recording 00:00")
        self._rec_pill.setStyleSheet(dt.qss_status_pill("recording"))
        hdr.addWidget(self._rec_pill)
        self._who = _title("", size=dt.SIZE_LG); hdr.addWidget(self._who)
        self._date = _body("", size=dt.SIZE_XS, color=dt.text_tertiary()); hdr.addWidget(self._date)
        hdr.addStretch()
        self._mark = QPushButton("Mark this moment  (M)"); self._mark.setMinimumHeight(44)
        self._mark.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mark.setStyleSheet(dt.qss_secondary_button())
        self._mark.clicked.connect(flow.mark_moment)
        hdr.addWidget(self._mark)
        self._stop = QPushButton("■  Stop recording"); self._stop.setMinimumHeight(44)
        self._stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop.setStyleSheet(dt.qss_danger_button())
        self._stop.clicked.connect(self._ask_stop)
        hdr.addWidget(self._stop)
        root.addLayout(hdr)

        # inline stop confirmation (hidden until asked)
        self._confirm = _card()
        self._confirm.setStyleSheet(f"QFrame#card {{ background: {dt._p().warning_bg}; "
                                    f"border: 1px solid {dt.warning_color()}; "
                                    f"border-radius: {dt.RADIUS_LG}px; }}")
        cc = QHBoxLayout(self._confirm); cc.setContentsMargins(16, 10, 16, 10); cc.setSpacing(12)
        self._confirm_text = _body("", size=dt.SIZE_SM, color=dt._p().warning_text)
        cc.addWidget(self._confirm_text, 1)
        keep = QPushButton("Keep recording"); keep.setMinimumHeight(40)
        keep.setStyleSheet(dt.qss_secondary_button()); keep.setCursor(Qt.CursorShape.PointingHandCursor)
        keep.clicked.connect(lambda: self._confirm.setVisible(False))
        cc.addWidget(keep)
        stopsave = QPushButton("Stop and save"); stopsave.setMinimumHeight(40)
        stopsave.setStyleSheet(dt.qss_danger_button()); stopsave.setCursor(Qt.CursorShape.PointingHandCursor)
        stopsave.clicked.connect(flow.stop_and_save)
        cc.addWidget(stopsave)
        self._confirm.setVisible(False)
        root.addWidget(self._confirm)

        body = QHBoxLayout(); body.setSpacing(16)

        # big card
        big = _card()
        bv = QVBoxLayout(big); bv.setContentsMargins(24, 20, 24, 22); bv.setSpacing(8)
        self._phase_chip = QLabel(""); self._phase_chip.setStyleSheet(dt.qss_status_pill("round"))
        self._phase_chip.setAlignment(Qt.AlignmentFlag.AlignLeft)
        chip_row = QHBoxLayout(); chip_row.addWidget(self._phase_chip); chip_row.addStretch()
        bv.addLayout(chip_row)
        self._round_lbl = _title("", size=28); bv.addWidget(self._round_lbl)
        self._count = QLabel("0:00")
        self._count.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_MONO}; "
                                  f"font-size: 110px; font-weight: 300;")
        self._count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bv.addWidget(self._count)
        self._left_lbl = _body("left in this round", size=dt.SIZE_SM, color=dt.text_tertiary())
        self._left_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bv.addWidget(self._left_lbl)
        self._progress = PhaseBar(); self._progress.setFixedHeight(8)
        bv.addWidget(self._progress)
        self._next_lbl = _body("", size=dt.SIZE_SM); bv.addWidget(self._next_lbl)
        bv.addStretch()
        self._session_bar = PhaseBar()
        bv.addWidget(_body("Whole session", size=dt.SIZE_XS, color=dt.text_tertiary()))
        bv.addWidget(self._session_bar)
        self._moment_note = _body("", size=dt.SIZE_XS, color=dt.accent())
        bv.addWidget(self._moment_note)
        body.addWidget(big, 3)

        # side
        side = QVBoxLayout(); side.setSpacing(10)
        self._health = _card()
        ov = QVBoxLayout(self._health); ov.setContentsMargins(14, 10, 14, 10)
        self._health_lbl = _body("", size=dt.SIZE_SM, bold=True)
        self._health_lbl.setWordWrap(True)
        ov.addWidget(self._health_lbl)
        side.addWidget(self._health)
        prev_card = _card()
        pv = QVBoxLayout(prev_card); pv.setContentsMargins(10, 10, 10, 10); pv.setSpacing(8)
        grid = QGridLayout(); grid.setSpacing(8)
        self._tiles = []
        for i, slot in enumerate(flow._cams):
            tile = CameraTile(slot, compact=True)
            grid.addWidget(tile, i // 2, i % 2)
            self._tiles.append(tile)
        pv.addLayout(grid)
        side.addWidget(prev_card, 1)
        self._hbviews = []
        for which in ("A", "B"):
            view = HeadbandView(flow._bands[which], flow.boxer_name, compact=True)
            view.reconnect_requested.connect(flow.reconnect_band)
            side.addWidget(view)
            self._hbviews.append(view)
        sw = QWidget(); sw.setLayout(side); sw.setMinimumWidth(320); sw.setMaximumWidth(420)
        body.addWidget(sw, 2)
        root.addLayout(body, 1)

    def _ask_stop(self):
        self._confirm_text.setText(self.flow.stop_prompt_text())
        self._confirm.setVisible(True)

    def reset_for_recording(self):
        self._confirm.setVisible(False)
        self._moment_note.setText("")
        a, b = self.flow._names.get("A"), self.flow._names.get("B")
        vs = f"{a or 'Boxer A'} vs {b or 'Boxer B'}"
        self._who.setText(vs)
        import datetime as _dt
        self._date.setText(_dt.datetime.now().strftime("%a %-d %b %Y"))
        total = session_total_seconds(self.flow._cfg)
        segs = [(p.duration, p.phase.value) for p in _build_timeline(self.flow._cfg)]
        self._session_bar.set_segments(segs); self._session_bar.set_now(0.0)

    def set_phase(self, phase_id, label, round_num, total_rounds, duration):
        self._phase_chip.setText(self._phase_name(phase_id, round_num))
        self._phase_chip.setStyleSheet(dt.qss_status_pill(phase_id))
        if phase_id == "round":
            self._round_lbl.setText(f"Round {round_num} of {total_rounds}")
            self._left_lbl.setText("left in this round")
        elif phase_id == "baseline":
            self._round_lbl.setText("Rest before round 1")
            self._left_lbl.setText("until round 1")
        else:
            self._round_lbl.setText("Break")
            self._left_lbl.setText("until the next round")
        self._next_lbl.setText(self.flow.next_phase_text())

    def _phase_name(self, pid, rn):
        return {"baseline": "Rest", "round": f"Round {rn}", "break": "Break",
                "done": "Finished"}.get(pid, pid.title())

    def set_time(self, remaining, total, elapsed_session):
        self._count.setText(_mmss(remaining))
        frac = (total - remaining) / max(1, total)
        self._progress.set_segments([(max(0.001, frac), "round"), (max(0.001, 1 - frac), "baseline")])
        tot = session_total_seconds(self.flow._cfg)
        self._session_bar.set_now(min(1.0, elapsed_session / max(1, tot)))
        self._rec_pill.setText(f"●  Recording {_mmss(elapsed_session)}")

    def on_tick(self):
        for t in self._tiles:
            t.update()
        for v in self._hbviews:
            v.refresh()
        self._health_lbl.setText(self.flow.health_text())
        ok = self.flow.health_ok()
        self._health_lbl.setStyleSheet(
            f"color: {dt.success_color() if ok else dt.warning_color()}; "
            f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; font-weight: 700;")

    def show_moment(self, t_session, count):
        self._moment_note.setText(f"Moment marked at {_mmss(t_session)} · {count} marked so far. "
                                  f"You can name them later in Review.")


# ── Step 4 — Saved ──────────────────────────────────────────────────────────────

class SavedStep(QWidget):
    def __init__(self, flow, parent=None):
        super().__init__(parent)
        self.flow = flow
        root = QVBoxLayout(self); root.setContentsMargins(40, 30, 40, 24); root.setSpacing(12)
        self._head = _title("✓  Recording saved")
        root.addWidget(self._head)
        self._meta = _sub(""); root.addWidget(self._meta)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget(); host.setStyleSheet(f"background: {dt.canvas_bg()};")
        self._list = QVBoxLayout(host); self._list.setContentsMargins(0, 0, 0, 0); self._list.setSpacing(8)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        self._folder = _body("", size=dt.SIZE_XS, color=dt.text_tertiary())
        root.addWidget(self._folder)

        bar = QHBoxLayout(); bar.setSpacing(12)
        self._label_btn = QPushButton("Start labelling  →"); self._label_btn.setMinimumHeight(44)
        self._label_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._label_btn.setStyleSheet(dt.qss_primary_button())
        self._label_btn.clicked.connect(flow.start_labelling)
        back = QPushButton("Back to sessions"); back.setMinimumHeight(44)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setStyleSheet(dt.qss_secondary_button())
        back.clicked.connect(flow._leave_to_sessions)
        another = QPushButton("Record another"); another.setMinimumHeight(44)
        another.setCursor(Qt.CursorShape.PointingHandCursor)
        another.setStyleSheet(dt.qss_secondary_button())
        another.clicked.connect(flow.start)
        finder = QPushButton("Show in Finder"); finder.setMinimumHeight(44)
        finder.setCursor(Qt.CursorShape.PointingHandCursor)
        finder.setStyleSheet(dt.qss_secondary_button())
        finder.clicked.connect(flow.reveal_saved)
        bar.addWidget(self._label_btn); bar.addWidget(another); bar.addWidget(back)
        bar.addStretch(); bar.addWidget(finder)
        root.addLayout(bar)

    def _row(self, icon, text, color=None):
        row = QFrame()
        row.setStyleSheet(f"QFrame {{ background: {dt.surface()}; border: 1px solid {dt.border()}; "
                          f"border-radius: {dt.RADIUS_MD}px; }}")
        h = QHBoxLayout(row); h.setContentsMargins(14, 10, 14, 10); h.setSpacing(10)
        ic = QLabel(icon); ic.setStyleSheet(f"color: {color or dt.success_color()}; font-size: 15px;")
        h.addWidget(ic)
        lb = _body(text, size=dt.SIZE_SM); lb.setWordWrap(True)
        h.addWidget(lb, 1)
        return row

    def populate(self, info: dict):
        while self._list.count():
            it = self._list.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._meta.setText(info["meta"])
        for icon, text, color in info["checklist"]:
            self._list.addWidget(self._row(icon, text, color))
        worth = info["worth_checking"]
        if worth:
            self._list.addWidget(_body("Worth checking", bold=True, size=dt.SIZE_MD))
            for text in worth:
                self._list.addWidget(self._row("!", text, dt.warning_color()))
        self._list.addStretch()
        self._folder.setText(info["folder_line"])


# ── the flow ───────────────────────────────────────────────────────────────────

class RecordFlow(QWidget):
    session_saved  = pyqtSignal(str)     # "Start labelling" (session root)
    exit_flow      = pyqtSignal()        # Cancel / "Back to sessions"
    help_requested = pyqtSignal(str)     # the CURRENT step's doc anchor

    STEP_CONNECT, STEP_SETUP, STEP_RECORD, STEP_SAVED = 0, 1, 2, 3
    _STEP_ANCHORS = {
        STEP_CONNECT: "step-1-connect-devices",
        STEP_SETUP: "step-2-set-up-session",
        STEP_RECORD: "step-3-recording",
        STEP_SAVED: "step-4-saved",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = app_settings.load()
        self._cams = camera_source.make_slots(self)
        self._bands = {"A": HeadbandSlot("A", self), "B": HeadbandSlot("B", self)}
        self._controller = SessionController(self)
        self._beep = SyncBeep(self)
        self._keep_awake = KeepAwake()
        self._layout: Optional[SessionLayout] = None
        self._cfg: Optional[SessionConfig] = None
        self._names = {"A": "", "B": ""}
        self._bell = False
        self._session_t0 = None
        self._session_t0_unix = None
        self._moments = []
        self._contact_writer = None
        self._active_phase = None
        self._stopping_early = False
        self._cam_rec_paths = {}
        self._band_rec_paths = {}
        self._saved_root = None
        self._scan_thread = None
        self.setStyleSheet(f"RecordFlow {{ background: {dt.canvas_bg()}; }}")
        self._build()
        self._wire_controller()

        self._tick = QTimer(self); self._tick.setInterval(60)
        self._tick.timeout.connect(self._on_ui_tick)
        self._contact_timer = QTimer(self); self._contact_timer.setInterval(1000)
        self._contact_timer.timeout.connect(self._log_contact)

    # ── construction ──
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        bar = QFrame(); bar.setFixedHeight(60)
        bar.setStyleSheet(f"background: {dt.surface()}; border-bottom: 1px solid {dt.border()};")
        h = QHBoxLayout(bar); h.setContentsMargins(20, 10, 20, 10); h.setSpacing(16)
        self._cancel = QPushButton("Cancel"); self._cancel.setMinimumHeight(40)
        self._cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel.setStyleSheet(dt.qss_secondary_button())
        self._cancel.clicked.connect(self.cancel)
        h.addWidget(self._cancel)
        h.addStretch()
        self._indicator = _StepIndicator()
        h.addWidget(self._indicator)
        h.addStretch()
        help_btn = QPushButton()
        help_btn.setIcon(QIcon(dt.icon_pixmap("help-circle", color=dt.text_tertiary(), size=18)))
        help_btn.setFixedSize(36, 36)
        help_btn.setToolTip("Help")
        help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        help_btn.setStyleSheet(dt.qss_secondary_button() + " QPushButton { padding: 0; }")
        help_btn.clicked.connect(
            lambda: self.help_requested.emit(self._STEP_ANCHORS.get(self._stack.currentIndex(),
                                                                    "step-1-connect-devices")))
        h.addWidget(help_btn)
        root.addWidget(bar)

        self._stack = QStackedWidget()
        self._connect_step = ConnectStep(self)
        self._setup_step = SetupStep(self)
        self._recording_step = RecordingStep(self)
        self._saved_step = SavedStep(self)
        for w in (self._connect_step, self._setup_step, self._recording_step, self._saved_step):
            self._stack.addWidget(w)
        root.addWidget(self._stack, 1)

    # ── public: entry / navigation ──
    def start(self):
        """(Re)enter the flow at step 1 and (re)scan + connect devices."""
        if self._controller.is_running:
            return
        self._settings = app_settings.load()
        self._reset_session_state()
        self._stop_devices()
        self._set_step(self.STEP_CONNECT)
        self._scan_and_assign()
        self._tick.start()

    def goto_connect(self):
        self._set_step(self.STEP_CONNECT)

    def goto_setup(self):
        # carry any names typed already
        a, b = self._setup_step.names()
        if a or b:
            self._names = {"A": a, "B": b}
        self._setup_step.populate_from_settings()
        self._set_step(self.STEP_SETUP)

    def _set_step(self, idx):
        self._stack.setCurrentIndex(idx)
        if idx <= self.STEP_RECORD:
            self._indicator.setVisible(True)
            self._indicator.set_active(min(idx, 2))
        else:
            self._indicator.setVisible(False)
        self._cancel.setVisible(idx != self.STEP_SAVED)

    def _reset_session_state(self):
        self._layout = None; self._cfg = None
        self._moments = []; self._active_phase = None
        self._stopping_early = False
        self._cam_rec_paths = {}; self._band_rec_paths = {}
        self._saved_root = None

    # ── device scanning / assignment ──
    def _scan_and_assign(self):
        from PyQt6.QtCore import QSettings
        devs = list_cameras()
        remembered = QSettings("Constructor University", "Brain Battle")
        used = set()
        # honour remembered slot→device-name assignments across launches
        for slot in self._cams:
            want = remembered.value(f"cameras/slot{slot.index}")
            match = next((d for d in devs if d.name == want and d.index not in used), None)
            if match:
                slot.assign(match); used.add(match.index)
            else:
                slot.assign(None)
        rest = [d for d in devs if d.index not in used]
        for slot in self._cams:
            if slot.device is None and rest:
                d = rest.pop(0); slot.assign(d); used.add(d.index)
        for slot in self._cams:
            if slot.device is not None:
                slot.start()
                remembered.setValue(f"cameras/slot{slot.index}", slot.device.name)
        self._scan_headbands()

    def _scan_headbands(self):
        try:
            import simulation
            if simulation.enabled():
                for d in simulation.muse_devices():
                    who = d.get("boxer", "")
                    if who in self._bands:
                        self._bands[who].assign({"name": d["name"], "address": d["address"]})
                        self._bands[who].start()
                return
        except Exception:
            pass
        # Real BLE scan, mapped to A/B via the settings headband mapping.
        from eeg_module import _MuseScanThread
        th = _MuseScanThread(self)
        th.found.connect(self._on_headbands_found)
        th.error.connect(lambda m: log.warning("headband scan error: %s", m))
        self._scan_thread = th
        th.start()

    def _on_headbands_found(self, devices):
        want = {self._settings.headband_for("A"): "A", self._settings.headband_for("B"): "B"}
        for d in devices:
            who = want.get(d.get("name"))
            if who and not self._bands[who].connected:
                self._bands[who].assign({"name": d["name"], "address": d["address"]})
                self._bands[who].start()

    def search_again(self):
        self._stop_devices()
        self._scan_and_assign()

    def open_camera_picker(self, slot_index):
        claimed = {s.device.index for s in self._cams if s.device is not None
                   and s.index != slot_index}
        dlg = _CameraPicker(claimed, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.chosen is not None:
            slot = next(s for s in self._cams if s.index == slot_index)
            slot.shutdown()
            slot.assign(dlg.chosen)
            slot.start()
            from PyQt6.QtCore import QSettings
            QSettings("Constructor University", "Brain Battle").setValue(
                f"cameras/slot{slot_index}", dlg.chosen.name)

    def reconnect_band(self, which):
        b = self._bands.get(which)
        if b is not None:
            b.reconnect()

    def boxer_name(self, which):
        return self._names.get(which) or ""

    # ── recording ──
    def _wire_controller(self):
        c = self._controller
        c.session_started.connect(self._on_session_started)
        c.phase_changed.connect(self._on_phase_changed)
        c.tick.connect(self._on_phase_tick)
        c.session_finished.connect(self._on_session_finished)
        if self._bell:
            pass  # bell wired per-session in begin_recording

    def begin_recording(self):
        if self._controller.is_running:
            return
        self._cfg = self._setup_step.config()
        a, b = self._setup_step.names()
        self._names = {"A": a, "B": b}
        self._bell = self._setup_step.bell()
        err = self._cfg.validate()
        if err:
            QMessageBox.warning(self, "Invalid session", err); return

        rec_ok, _enc, rec_msg = preflight_recording()
        if not rec_ok:
            QMessageBox.critical(self, "Can't record video", rec_msg); return

        base = self._settings.capture_dir_resolved()
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Cannot write recordings", str(e)); return
        if not self._disk_guard(base):
            return

        boxer_a = {"name": a or None, "headband": self._settings.headband_for("A")}
        boxer_b = {"name": b or None, "headband": self._settings.headband_for("B")}
        try:
            self._layout = create_session(base, self._cfg, boxer_a=boxer_a, boxer_b=boxer_b)
            attach_session_log(self._layout.root)
        except Exception as e:
            QMessageBox.critical(self, "Cannot create session folder", str(e)); return

        # round bell: BASELINE + each ROUND start (SessionController emits this)
        try:
            self._controller.beep_requested.disconnect(self._beep.play)
        except TypeError:
            pass
        if self._bell:
            self._controller.beep_requested.connect(self._beep.play)

        self._controller.configure(self._cfg)
        self._recording_step.reset_for_recording()
        self._set_step(self.STEP_RECORD)
        self._keep_awake.start()
        self._install_key_filter()
        self._controller.start()

    def _disk_guard(self, base) -> bool:
        import shutil
        n_cam = sum(1 for s in self._cams if s.is_live)
        n_eeg = sum(1 for b in self._bands.values() if b.connected)
        try:
            free = shutil.disk_usage(base).free
        except OSError:
            return True
        cams = n_cam or len(self._cams)
        vbps = video_bytes_per_sec_for_mbps(self._settings.video_mbps())
        est = estimate_session_bytes(self._cfg, cams, n_eeg, vbps)
        verdict = disk_space_verdict(self._cfg, cams, n_eeg, free, vbps)
        if verdict == "blocked":
            QMessageBox.critical(self, "Not enough disk space",
                                 f"This recording needs about {human_bytes(est)}, but only "
                                 f"{human_bytes(free)} is free. Free up space or shorten the "
                                 f"session, then try again.")
            return False
        if verdict == "low":
            r = QMessageBox.question(self, "Low disk space",
                                     f"This recording needs about {human_bytes(est)}. Only "
                                     f"{human_bytes(free)} is free. Start anyway?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return False
        return True

    def _on_session_started(self):
        if self._layout is None:
            return
        self._session_t0 = time.monotonic()
        self._session_t0_unix = time.time()
        clock = (self._session_t0, self._session_t0_unix)
        try:
            self._layout.set_capture_origin(self._session_t0, self._session_t0_unix)
        except Exception as e:
            log.error("capture origin write failed: %s", e)
        session_history.log_event(self._layout.root, session_history.EVENT_RECORDING_STARTED,
                                  {"rounds": self._cfg.num_rounds, "round_sec": self._cfg.round_sec,
                                   "break_sec": self._cfg.break_sec, "baseline_sec": self._cfg.baseline_sec})
        cont = self._layout.continuous_dir()
        # cameras
        self._cam_rec_paths = {}
        for slot in self._cams:
            if slot.is_live:
                path = slot.recording_path(cont)
                slot.start_recording(path, clock)
                self._cam_rec_paths[slot.index] = path
        # headbands
        from eeg_module import muse_friendly
        muse_dir = cont / "Muse data"; muse_dir.mkdir(parents=True, exist_ok=True)
        self._band_rec_paths = {}
        for which, band in self._bands.items():
            if band.connected and band.device:
                fn = f"session_{muse_friendly(band.device.get('name',''))}.csv"
                path = str(muse_dir / _ILLEGAL.sub("-", fn))
                band.start_recording(path, self._session_t0_unix)
                self._band_rec_paths[which] = path
        # contact-quality log
        self._contact_writer = contact_log.ContactLogWriter(contact_log.path_for(self._layout.root))
        self._contact_timer.start()

    def _on_phase_changed(self, phase_id, label, duration, round_num):
        total_rounds = self._cfg.num_rounds
        self._recording_step.set_phase(phase_id, label, round_num, total_rounds, duration)
        self._recording_step.set_time(duration, duration, self._elapsed_session())
        if self._layout is not None and self._session_t0 is not None:
            try:
                t = time.monotonic() - self._session_t0
                norm = label.upper().replace(" ", "_")
                marker = f"{norm}_START" if phase_id != "done" else norm
                self._layout.append_marker(t, phase_id, marker, round_num)
            except Exception as e:
                log.warning("marker write failed: %s", e)
        if self._layout is None:
            return
        phase = Phase(phase_id)
        if phase in (Phase.BASELINE, Phase.ROUND, Phase.BREAK):
            self._layout.add_phase(PhaseRecord(
                phase=phase.value, label=label, round_num=round_num, folder="continuous",
                planned_duration=duration, start_unix=time.time(),
                start_monotonic=time.monotonic()))
            if self._active_phase is not None:
                pp, pr = self._active_phase
                try: self._layout.close_phase(pp, pr)
                except Exception as e: log.warning("close_phase: %s", e)
            self._active_phase = (phase, round_num)

    def _on_phase_tick(self, remaining, elapsed, total):
        self._recording_step.set_time(remaining, total, self._elapsed_session())

    def _elapsed_session(self):
        if self._session_t0 is None:
            return 0.0
        return time.monotonic() - self._session_t0

    def _on_session_finished(self):
        self._finish(stopped_early=self._stopping_early)

    def stop_and_save(self):
        if not self._controller.is_running:
            return
        self._stopping_early = True
        self._controller.stop()   # → _on_session_finished

    def _finish(self, stopped_early=False, goto_saved=True):
        if self._layout is not None and self._session_t0 is not None:
            try:
                t = time.monotonic() - self._session_t0
                self._layout.append_marker(t, "done", "SESSION_END", 0)
                if stopped_early:
                    self._layout.mark_stopped_early(t)
            except Exception as e:
                log.warning("SESSION_END write failed: %s", e)
        if self._active_phase is not None and self._layout is not None:
            ph, rn = self._active_phase
            try: self._layout.close_phase(ph, rn)
            except Exception as e: log.warning("final close_phase: %s", e)
            self._active_phase = None
        self._contact_timer.stop()
        if self._contact_writer is not None:
            self._contact_writer.close(); self._contact_writer = None
        # stop recordings (cameras + bands), record each band's first-sample time
        for slot in self._cams:
            slot.stop_recording()
        for idx, which in enumerate(("A", "B"), start=1):
            band = self._bands[which]
            band.stop_recording()
            if self._layout is not None:
                first = band.last_recorded_first_unix()
                if first is not None:
                    try: self._layout.record_source_start(f"EEG_{idx:02d}", first)
                    except Exception as e: log.warning("record_source_start: %s", e)
        self._keep_awake.stop()
        self._remove_key_filter()
        if self._layout is not None:
            session_history.log_event(self._layout.root, session_history.EVENT_RECORDING_FINISHED)
            self._saved_root = str(self._layout.root)
        if goto_saved and self._saved_root:
            self._saved_step.populate(self._build_saved_info())
            self._set_step(self.STEP_SAVED)

    # ── mark this moment ──
    def mark_moment(self):
        if not self._controller.is_running or self._layout is None:
            return
        t = time.monotonic() - self._session_t0
        entry = self._layout.add_moment(t)
        self._moments.append(entry)
        try:
            session_history.log_event(self._layout.root, session_history.EVENT_MOMENT_MARKED,
                                      {"t_session_seconds": entry["t_session_seconds"]})
        except Exception:
            pass
        self._recording_step.show_moment(t, len(self._moments))

    # ── recording-screen text helpers ──
    def next_phase_text(self):
        plan = self._controller.phase_plan()
        cur = self._controller.current_phase_info
        if cur is None or not plan:
            return ""
        try:
            i = plan.index(cur)
        except ValueError:
            return ""
        if i + 1 >= len(plan):
            return "Next: nothing — this is the final round."
        nxt = plan[i + 1]
        nm = {"baseline": "Rest", "round": f"Round {nxt.round_num}",
              "break": "Break"}.get(nxt.phase.value, nxt.label)
        txt = f"Next: {nm}, {_mmss(nxt.duration)}"
        if self._bell and i + 2 < len(plan) and plan[i + 2].phase == Phase.ROUND:
            txt += f" · the bell rings when round {plan[i + 2].round_num} starts"
        elif self._bell and nxt.phase == Phase.ROUND:
            txt += f" · the bell rings when round {nxt.round_num} starts"
        return txt

    def stop_prompt_text(self):
        cur = self._controller.current_phase_info
        where = ""
        if cur is not None:
            nm = {"baseline": "The rest period", "round": f"Round {cur.round_num}",
                  "break": "The break"}.get(cur.phase.value, cur.label)
            where = f"{nm} is still running. "
        return (f"Stop recording now? {where}Everything recorded so far is kept, but the "
                f"rest of the session will not happen.")

    def health_text(self):
        problems = []
        ncam = 0
        for s in self._cams:
            if s.device is None:
                continue
            if s.rec_failed:
                problems.append(f"{s.title} stopped recording")
            elif s.state == "error":
                problems.append(f"{s.title} is not connected")
            elif s.dropped:
                problems.append(f"{s.title} is losing frames")
            else:
                ncam += 1
        nb = 0
        for which, b in self._bands.items():
            if b.device is None:
                continue
            if b.lost:
                problems.append(f"Boxer {which}'s headband lost connection")
            elif not b.connected:
                problems.append(f"Boxer {which}'s headband is connecting")
            else:
                nb += 1
        if not problems:
            return f"✓ All {ncam} camera{'s' if ncam != 1 else ''} and {nb} headband" \
                   f"{'s' if nb != 1 else ''} are recording"
        return "! " + "; ".join(problems) + "."

    def health_ok(self):
        return self.health_text().startswith("✓")

    def _log_contact(self):
        if self._contact_writer is None or self._session_t0 is None:
            return
        t = time.monotonic() - self._session_t0
        for which, b in self._bands.items():
            if b.connected:
                summary, statuses = b.contact()
                self._contact_writer.append(t, which, summary, statuses)

    # ── Saved-screen data (from REAL files) ──
    def _build_saved_info(self):
        import datetime as _dt
        from session_storage import folder_size_bytes, session_total_seconds
        from review_label_tab import read_phase_markers
        root = self._saved_root
        a, b = self._names.get("A"), self._names.get("B")
        vs = f"{a or 'Boxer A'} vs {b or 'Boxer B'}"
        markers = read_phase_markers(root)
        rounds = self._cfg.num_rounds
        mins = max(1, round(session_total_seconds(self._cfg) / 60))
        when = _dt.datetime.now().strftime("%a %-d %b %Y, %H:%M")
        meta = f"{when} · {vs} · {mins} min · {rounds} round{'s' if rounds != 1 else ''}"

        checklist = []
        # cameras
        for slot in self._cams:
            path = self._cam_rec_paths.get(slot.index)
            if not path:
                continue
            exists = os.path.isfile(path)
            lost = f" · {slot.dropped} frames lost" if slot.dropped else ""
            if exists and not slot.rec_failed:
                checklist.append(("✓", f"{slot.title} video saved{lost}", dt.success_color()))
            else:
                checklist.append(("!", f"{slot.title} video incomplete{lost}", dt.warning_color()))
        # brain recordings
        for which in ("A", "B"):
            path = self._band_rec_paths.get(which)
            if path and os.path.isfile(path):
                checklist.append(("✓", f"Boxer {which} brain recording saved", dt.success_color()))
        # phases + moments
        n_rounds = sum(1 for m in markers if m[2].upper().startswith("ROUND"))
        n_breaks = sum(1 for m in markers if m[2].upper().startswith("BREAK"))
        checklist.append(("✓", f"{n_rounds} round(s) and {n_breaks} break(s) marked automatically",
                          dt.success_color()))
        if self._moments:
            checklist.append(("✓", f"{len(self._moments)} moment(s) you marked", dt.success_color()))

        # worth checking
        worth = []
        for p in contact_log.weak_periods(root, markers, min_sec=30):
            worth.append(contact_log.describe_weak(p))
        for slot in self._cams:
            if slot.dropped:
                worth.append(f"{slot.title} lost {slot.dropped} frames — the recording may stutter.")
            if slot.rec_failed:
                worth.append(f"{slot.title} stopped recording partway through.")
        for which, b in self._bands.items():
            if b.lost:
                worth.append(f"Boxer {which}'s headband disconnected during the recording.")

        size = human_bytes(folder_size_bytes(root))
        folder_line = f"Saved in {os.path.basename(os.path.dirname(root))} › " \
                      f"{os.path.basename(root)} · {size}"
        return {"meta": meta, "checklist": checklist, "worth_checking": worth,
                "folder_line": folder_line}

    def _leave_to_sessions(self):
        self._stop_devices()
        self._tick.stop()
        self.exit_flow.emit()

    def start_labelling(self):
        self._stop_devices()
        self._tick.stop()
        if self._saved_root:
            self.session_saved.emit(self._saved_root)

    def reveal_saved(self):
        if self._saved_root:
            try:
                from sessions_tab import reveal_in_finder
                reveal_in_finder(self._saved_root)
            except Exception as e:
                log.warning("reveal in finder failed: %s", e)

    # ── cancel / quit protection ──
    def is_recording(self):
        return self._controller.is_running

    def _anything_connected(self):
        return (any(s.device is not None for s in self._cams)
                or any(b.device is not None for b in self._bands.values()))

    def cancel(self):
        if self.is_recording():
            r = QMessageBox.question(
                self, "Stop the recording?",
                "A recording is in progress. Stop it and leave? Everything recorded "
                "so far is kept.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
            self._stopping_early = True
            self._controller.stop()         # finalises + saves what was recorded
            self._leave_to_sessions()
            return
        if self._anything_connected():
            r = QMessageBox.question(
                self, "Leave setup?",
                "Leave the recording setup? The cameras and headbands will disconnect.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        self._leave_to_sessions()

    # ── key filter for M = mark moment ──
    def _install_key_filter(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self)

    def _remove_key_filter(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtWidgets import QLineEdit, QTextEdit
        if event.type() == QEvent.Type.KeyPress and self._controller.is_running:
            from PyQt6.QtWidgets import QApplication
            w = QApplication.focusWidget()
            if not isinstance(w, (QLineEdit, QTextEdit)) and event.key() == Qt.Key.Key_M:
                self.mark_moment(); return True
        return super().eventFilter(obj, event)

    # ── ui tick (live previews / traces) ──
    def _on_ui_tick(self):
        cur = self._stack.currentWidget()
        if hasattr(cur, "on_tick"):
            try:
                cur.on_tick()
            except Exception:
                pass

    # ── teardown ──
    def _stop_devices(self):
        for slot in self._cams:
            slot.shutdown()
        for b in self._bands.values():
            b.shutdown()

    def shutdown(self):
        try:
            if self._controller.is_running:
                self._stopping_early = True
                self._controller.stop()
        except Exception:
            pass
        self._contact_timer.stop()
        if self._contact_writer is not None:
            try: self._contact_writer.close()
            except Exception: pass
            self._contact_writer = None
        try: self._keep_awake.stop()
        except Exception: pass
        self._remove_key_filter()
        self._tick.stop()
        self._stop_devices()


__all__ = ["RecordFlow"]

