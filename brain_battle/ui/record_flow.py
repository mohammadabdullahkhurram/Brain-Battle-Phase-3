"""
record_flow.py
──────────────
The guided recording flow (DESIGN §2–§4): full-screen, no sidebar, its own top
bar (Cancel + where you are) and three screens —

    1. Get ready   everything needed before pressing Start on ONE screen: the
                   four camera pictures and both headbands on top (what you
                   check), the session plan and the Start button underneath
                   (what you do). Devices connect by themselves while you type
                   the names.
    2. Recording   built to be read from across a gym: one status strip that is
                   green or says what is wrong, a very large countdown on a
                   card tinted by the phase, a very large "Mark this moment"
                   button, and the cameras and headbands down the side.
    3. Saved       what was saved, anything worth checking, and what to do next.

(The flow used to have separate "Connect devices" and "Set up session" steps;
they were merged — ADR-0018.) The real capture machinery is reused unchanged
(SessionController for phase timing, session_storage for the on-disk layout,
USBCameraThread / _MuseDirectReader via CameraSlot / HeadbandSlot for
recording). Under BRAIN_BATTLE_SIMULATE the devices are simulated through those
same real paths; with the flag unset there is no trace of simulation.

Public surface used by the app shell:
  • RecordFlow(parent)
  • .start()                 — (re)enter at step 1, scan + connect devices
  • .session_saved (signal)  — "Label this recording" on the Saved screen (root path)
  • .exit_flow (signal)      — Cancel / "Back to sessions"
  • .is_recording()          — for the app's quit-confirmation
  • .shutdown()              — stop devices/threads before teardown
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import threading
import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QRectF, QSize
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QIcon
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
    QSizePolicy, QLineEdit, QCheckBox, QScrollArea, QMessageBox, QStackedWidget,
    QDialog, QListWidget, QListWidgetItem, QDialogButtonBox,
)

import logging

import brain_battle.ui.design_tokens as dt
import brain_battle.ui.widgets as ui
import brain_battle.data.app_settings as app_settings
import brain_battle.data.safe_io as safe_io
import brain_battle.capture.camera_source as camera_source
import brain_battle.capture.clock as clock
import brain_battle.capture.contact_log as contact_log
import brain_battle.data.session_history as session_history
import brain_battle.data.session_xdf as session_xdf
from brain_battle.capture.lsl_outlets import LslPublisher
from brain_battle.capture.headband_source import HeadbandSlot
from brain_battle.data.signals import EEG_CHANNELS
from brain_battle.ui.eeg_viewer import EEGViewer
from brain_battle.capture.camera_source import CameraSlot
from brain_battle.capture.session_controller import SessionController, SessionConfig, Phase, _build_timeline
from brain_battle.data.session_storage import (
    create_session, SessionLayout, PhaseRecord, estimate_session_bytes,
    human_bytes, session_total_seconds, disk_space_verdict,
    video_bytes_per_sec_for_mbps, folder_size_bytes, read_phase_markers, short_phase,
)
from brain_battle.capture.round_bell import RoundBell
from brain_battle.capture.keep_awake import KeepAwake
from brain_battle.support.log_setup import attach_session_log
from brain_battle.support import platform_utils
from brain_battle.capture.usb_camera import list_cameras, preflight_recording, CameraDevice

log = logging.getLogger(__name__)

_ILLEGAL = re.compile(r'[<>:"/\\|?*]')


# ── small helpers ────────────────────────────────────────────────────────────

def _mmss(sec) -> str:
    sec = max(0, int(round(sec))); m, s = divmod(sec, 60)
    if m >= 60:
        h, m = divmod(m, 60); return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ── where you are in the flow ────────────────────────────────────────────────

class _StepIndicator(QWidget):
    _STEPS = ["Get ready", "Record", "Saved"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = 0
        self._labels = []
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(10)
        for i, name in enumerate(self._STEPS):
            chip = QLabel(); chip.setMinimumHeight(28)
            self._labels.append(chip)
            h.addWidget(chip)
            if i < len(self._STEPS) - 1:
                line = QFrame(); line.setFixedSize(28, 2)
                line.setObjectName("stepLine")
                line.setStyleSheet(f"#stepLine {{ background: {dt.border_strong()}; }}")
                h.addWidget(line)
        self.set_active(0)

    def set_active(self, idx: int):
        self._active = idx
        for i, chip in enumerate(self._labels):
            name = self._STEPS[i]
            if i == idx:
                chip.setText(f"{i + 1}  {name}")
                chip.setStyleSheet(
                    f"background: {dt.text_primary()}; color: #FFFFFF; border-radius: 14px; "
                    f"padding: 4px 14px; font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; "
                    f"font-weight: 600;")
            elif i < idx:
                chip.setText(f"✓  {name}")
                chip.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt.success_color(),
                                               "padding: 4px 6px;"))
            else:
                chip.setText(f"{i + 1}  {name}")
                chip.setStyleSheet(dt.css_text(dt.SIZE_SM, 500, dt.text_tertiary(),
                                               "padding: 4px 6px;"))


# ── camera tile ───────────────────────────────────────────────────────────────

class CameraTile(QFrame):
    """Live preview + status for one CameraSlot. ``compact`` is the small
    recording-screen variant (no Change button, terse status)."""
    change_requested = pyqtSignal(int)

    def __init__(self, slot: CameraSlot, compact: bool = False, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.compact = compact
        self.setMinimumSize(150, 84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._change = None
        if not compact:
            self._change = QPushButton("Change", self)
            self._change.setCursor(Qt.CursorShape.PointingHandCursor)
            self._change.setToolTip("Use a different camera for this view")
            self._change.setStyleSheet(
                f"QPushButton {{ background: rgba(255,255,255,0.94); color: {dt.text_primary()}; "
                f"border: none; border-radius: 7px; font-family: {dt.FONT_UI}; "
                f"font-size: {dt.SIZE_XS}px; font-weight: 600; padding: 5px 10px; }}"
                f"QPushButton:hover {{ background: #FFFFFF; }}")
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
            r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            missing = self.slot.device is None or self.slot.state == "error"
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(dt.stage()))
            p.drawRoundedRect(r, dt.RADIUS_MD, dt.RADIUS_MD)
            img = self.slot.last_image
            small = QFont("Inter"); small.setPixelSize(11 if self.compact else 12)
            if img is not None and not img.isNull() and not missing:
                iw, ih = img.width(), img.height()
                sc = min(r.width() / iw, r.height() / ih)
                dw, dh = iw * sc, ih * sc
                x = r.x() + (r.width() - dw) / 2; y = r.y() + (r.height() - dh) / 2
                p.drawImage(QRectF(x, y, dw, dh), img)
            else:
                p.setPen(QColor("#98A2B3")); p.setFont(small)
                msg = ("Not found. Check the USB cable and the video receiver, "
                       "then press Search again." if missing else "Connecting…")
                if self.compact and missing:
                    msg = "Not connected"
                p.drawText(r.adjusted(12, 30, -12, -26),
                           Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, msg)
            # red border when missing
            if missing:
                p.setPen(QPen(QColor(dt.record_red()), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r, dt.RADIUS_MD, dt.RADIUS_MD)
            # name chip + status line on the picture
            bold = QFont("Inter"); bold.setPixelSize(11 if self.compact else 12)
            bold.setWeight(QFont.Weight.DemiBold)
            p.setFont(bold)
            name = self.slot.title
            tw = p.fontMetrics().horizontalAdvance(name) + 16
            p.setBrush(QColor(0, 0, 0, 160)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(r.x() + 7, r.y() + 7, tw, 22), 6, 6)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRectF(r.x() + 7, r.y() + 7, tw, 22), Qt.AlignmentFlag.AlignCenter, name)
            icon, txt, col = self._status()
            p.setFont(small)
            sw = p.fontMetrics().horizontalAdvance(icon + " " + txt) + 16
            p.setBrush(QColor(0, 0, 0, 160)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(r.x() + 7, r.bottom() - 28, sw, 21), 6, 6)
            p.setPen(QColor(col))
            p.drawText(QRectF(r.x() + 7, r.bottom() - 28, sw, 21),
                       Qt.AlignmentFlag.AlignCenter, icon + " " + txt)
        finally:
            p.end()

    def _status(self):
        s = self.slot
        if s.device is None:
            return "✕", "Not connected", "#FDA29B"
        if s.state == "error":
            return "✕", "Not found", "#FDA29B"
        if s.rec_failed:
            return "✕", "Recording stopped", "#FDA29B"
        if s.is_recording:
            d = f" · {s.dropped} frames lost" if s.dropped else ""
            return "●", f"Recording{d}", "#FDA29B" if s.dropped else "#75E0A7"
        if s.state == "live":
            return "✓", ("Connected" if self.compact else
                         f"Connected · {s.fps:.0f} frames per second"), "#75E0A7"
        return "…", "Connecting", "#FEDF89"


# ── headband: contact strip + card ─────────────────────────────────────────────

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


class ContactStrip(QWidget):
    """The four skin sensors of one headband as four equal cells — "✓ Left ear",
    "! Right ear" — always in the same place, so a weak sensor is found at a
    glance. Plain sensor names only (never channel codes); state is shown by
    the glyph and the wording of the tooltip, not by colour alone."""

    def __init__(self, compact: bool = False, parent=None):
        super().__init__(parent)
        self._states = {code: None for code, _ in _SENSOR_CHIPS}
        self._compact = compact
        self.setFixedHeight(22 if compact else 26)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_states(self, states: dict, connected: bool = True) -> None:
        new = {code: (states.get(code) if connected else None) for code, _ in _SENSOR_CHIPS}
        if new != self._states:
            self._states = new
            self.update()
        self.setToolTip(" · ".join(self.texts()))

    def texts(self) -> list[str]:
        """What each cell says, e.g. ['✓ Left ear', '! Right ear', …]."""
        out = []
        for code, plain in _SENSOR_CHIPS:
            st = self._states.get(code)
            out.append(plain if st is None else (f"✓ {plain}" if st == "good" else f"! {plain}"))
        return out

    def weak_names(self) -> list[str]:
        return [plain for code, plain in _SENSOR_CHIPS
                if self._states.get(code) not in (None, "good")]

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            n = len(_SENSOR_CHIPS); gap = 4
            w = (self.width() - gap * (n - 1)) / n
            f = QFont("Inter"); f.setPixelSize(10 if self._compact else 11)
            f.setWeight(QFont.Weight.Medium)
            p.setFont(f)
            pal = dt._p()
            for i, (text, (code, _plain)) in enumerate(zip(self.texts(), _SENSOR_CHIPS)):
                st = self._states.get(code)
                if st is None:
                    bg, fg = pal.card_bg_hover, pal.text_tertiary
                elif st == "good":
                    bg, fg = pal.success_bg, pal.success_text
                else:
                    bg, fg = pal.warning_bg, pal.warning_text
                cell = QRectF(i * (w + gap), 0, w, self.height())
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(bg))
                p.drawRoundedRect(cell, 6, 6)
                if st not in (None, "good"):
                    p.setPen(QPen(QColor(pal.warning), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(cell.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
                p.setPen(QColor(fg))
                shown = p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(w - 6))
                p.drawText(cell, Qt.AlignmentFlag.AlignCenter, shown)
        finally:
            p.end()


class HeadbandView(QFrame):
    """One boxer's headband (DESIGN §2/§3): badge + name + connection and
    battery, the four-sensor contact strip, a plain tip when a sensor is weak,
    and the live EEG (a standard viewer: one labelled trace per channel, µV
    scale bar, time axis, event markers). ``compact`` is the recording-screen
    variant (four skin electrodes, 5 s across, no tip)."""
    reconnect_requested = pyqtSignal(str)

    def __init__(self, slot: HeadbandSlot, flow, compact=False, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.compact = compact
        self._flow = flow
        self._name_getter = flow.boxer_name
        self.setObjectName("hbCard")
        self.setStyleSheet(dt.qss_card("hbCard"))
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 9 if compact else 11, 12, 9 if compact else 11)
        v.setSpacing(5 if compact else 7)
        head = QHBoxLayout(); head.setSpacing(8)
        head.addWidget(ui.boxer_badge(slot.which, 22))
        self._title = QLabel("")
        self._title.setStyleSheet(dt.css_text(dt.SIZE_SM, 600))
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        head.addWidget(self._title, 1)
        self._status = QLabel("")
        self._status.setStyleSheet(dt.css_text(dt.SIZE_XS, 500, dt.text_tertiary()))
        head.addWidget(self._status)
        self._reconnect = ui.secondary_button("Reconnect", compact=True)
        self._reconnect.setVisible(False)
        self._reconnect.clicked.connect(lambda: self.reconnect_requested.emit(slot.which))
        head.addWidget(self._reconnect)
        v.addLayout(head)

        self._contact = ContactStrip(compact=compact)
        v.addWidget(self._contact)
        self._tip = QLabel(""); self._tip.setWordWrap(True)
        self._tip.setStyleSheet(dt.css_text(dt.SIZE_XS, 500, dt.warning_color()))
        self._tip.setVisible(False)
        v.addWidget(self._tip)
        # Recording screen: the four skin electrodes, 5 s across. Get ready:
        # all five inputs, 10 s across. The scale fits itself to the signal
        # (the scale bar always says what it currently is).
        self._trace = EEGViewer(channels=EEG_CHANNELS[:4] if compact else EEG_CHANNELS,
                                live=True, color=dt.boxer_colors(slot.which)[2],
                                min_row_px=11 if compact else 12)
        self._trace.set_timebase(5 if compact else 10)
        self._trace.set_scale(None)
        v.addWidget(self._trace, 1)
        self.refresh()

    def contact_texts(self) -> list[str]:
        return self._contact.texts()

    def _set_status(self, text, colour, weight=500):
        self._status.setText(text)
        self._status.setStyleSheet(dt.css_text(dt.SIZE_XS, weight, colour))

    def refresh(self):
        s = self.slot
        name = self._name_getter(s.which)
        dev = (s.device or {}).get("name", "") if s.device else ""
        head_txt = f"Boxer {s.which}" + (f" · {name}" if name else "")
        if dev and not (self.compact and name):   # recording screen: keep it short
            head_txt += f" · {dev}"
        self._title.setText(head_txt)
        batt = f" · battery {s.battery}%" if s.battery is not None else ""
        if s.lost:
            self._set_status("✕ Lost connection", dt.error_color(), 600)
            self._reconnect.setVisible(True)
        elif s.connected:
            self._set_status(f"✓ Connected{batt}", dt.success_color())
            self._reconnect.setVisible(False)
        elif s.device is not None:
            self._set_status("Connecting…", dt.text_tertiary())
            self._reconnect.setVisible(False)
        else:
            self._set_status("Not switched on", dt.text_tertiary())
            self._reconnect.setVisible(False)
        # the four sensors, from the live contact heuristic
        _summary, statuses = s.contact() if s.connected else ("", {})
        self._contact.set_states(statuses, connected=bool(s.connected))
        weak_names = self._contact.weak_names()
        if weak_names and not self.compact:
            tip = _WEAK_TIPS.get(weak_names[0], "Re-seat the sensor so it sits flat "
                                 "against the skin.")
            self._tip.setText(f"Weak contact: {weak_names[0].lower()}. {tip}")
            self._tip.setVisible(True)
        else:
            self._tip.setVisible(False)
        if s.connected and not s.lost and weak_names:
            self._set_status("! Weak contact" + batt, dt.warning_color(), 600)
        # live EEG: seconds-ago before recording, the session clock during it
        ts, chans = s.trace() if s.connected else (None, None)
        self._trace.empty_text = "Waiting for signal…" if s.device else "No headband"
        self._trace.set_time_origin(self._flow.session_origin_unix())
        self._trace.set_markers(self._flow.live_markers())
        self._trace.set_data(ts, chans or {})


# ── phase bar + stepper ────────────────────────────────────────────────────────

class PhaseBar(QWidget):
    """Proportional phase strip (rest/round/break) with an optional now-marker.
    ``labelled`` writes each phase's short name inside its segment when it fits."""

    def __init__(self, labelled: bool = False, height: int = 26, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._segments = []    # (duration, phase_id[, text])
        self._total = 1.0
        self._now = None       # fraction 0..1 or None
        self._labelled = labelled

    def set_segments(self, segments):
        self._segments = [tuple(s) for s in segments]
        self._total = max(0.001, sum(s[0] for s in self._segments))
        self.update()

    def set_now(self, frac):
        self._now = None if frac is None else max(0.0, min(1.0, frac))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            W, H = self.width(), self.height()
            f = QFont("Inter"); f.setPixelSize(11); f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            x = 0.0
            for seg in self._segments:
                dur, pid = seg[0], seg[1]
                bg, fg = dt.phase_fill(pid)
                w = W * (dur / self._total)
                rect = QRectF(x + 1, 2, max(1, w - 2), H - 4)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(bg))
                p.drawRoundedRect(rect, 5, 5)
                if self._labelled and len(seg) > 2 and \
                        p.fontMetrics().horizontalAdvance(seg[2]) + 10 <= rect.width():
                    p.setPen(QColor(fg))
                    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, seg[2])
                x += w
            if self._now is not None:
                nx = max(1.0, min(W - 1.0, W * self._now))
                p.setPen(QPen(QColor(dt.record_red()), 2.5))
                p.drawLine(int(nx), 0, int(nx), H)
        finally:
            p.end()


class _ProgressBar(QWidget):
    """A plain rounded progress bar in one colour."""

    def __init__(self, height=10, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self._frac = 0.0
        self._colour = dt.accent()

    def set(self, frac, colour=None):
        self._frac = max(0.0, min(1.0, frac))
        if colour:
            self._colour = colour
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = QRectF(self.rect()); rad = r.height() / 2
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(255, 255, 255, 170))
            p.drawRoundedRect(r, rad, rad)
            if self._frac > 0:
                p.setBrush(QColor(self._colour))
                p.drawRoundedRect(QRectF(0, 0, max(r.height(), r.width() * self._frac),
                                         r.height()), rad, rad)
        finally:
            p.end()


class Stepper(QFrame):
    """− value +  stepper. Value is an int (seconds or a count); `fmt` renders it."""
    changed = pyqtSignal()

    def __init__(self, caption, value, lo, hi, step, fmt, parent=None):
        super().__init__(parent)
        self._value = value; self._lo = lo; self._hi = hi; self._step = step
        self._fmt = fmt
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(4)
        cap = QLabel(caption)
        cap.setStyleSheet(dt.css_text(dt.SIZE_XS, 600, dt.text_secondary()))
        v.addWidget(cap)
        row = QHBoxLayout(); row.setSpacing(4)
        self._minus = self._btn("−", f"Less: {caption.lower()}")
        self._plus = self._btn("+", f"More: {caption.lower()}")
        self._minus.clicked.connect(lambda: self._bump(-self._step))
        self._plus.clicked.connect(lambda: self._bump(self._step))
        self._val = ui.TimerLabel(px=17, align=Qt.AlignmentFlag.AlignHCenter)
        self._val.setFixedWidth(58)
        row.addWidget(self._minus); row.addWidget(self._val); row.addWidget(self._plus)
        row.addStretch()
        v.addLayout(row)
        self._render()

    def _btn(self, txt, name):
        b = QPushButton(txt); b.setFixedSize(40, 40); b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setAccessibleName(name); b.setToolTip(name)
        b.setAutoRepeat(True)
        b.setStyleSheet(dt.qss_secondary_button() +
                        " QPushButton { font-size: 20px; padding: 0 0 2px 0; font-weight: 500; }")
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
        v = QVBoxLayout(self); v.setContentsMargins(22, 20, 22, 18); v.setSpacing(12)
        v.addWidget(ui.heading("Pick the camera for this view", size=dt.SIZE_XL))
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


# ── Step 1 — Get ready (devices on top, the plan + Start underneath) ────────────

class ConnectStep(QWidget):
    """The devices half of "Get ready": four camera pictures and both
    headbands, with one line that says whether everything is ready."""

    def __init__(self, flow, parent=None):
        super().__init__(parent)
        self.flow = flow
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(10)

        top = QHBoxLayout(); top.setSpacing(12)
        tv = QVBoxLayout(); tv.setSpacing(2)
        tv.addWidget(ui.title("Get ready"))
        # (two labels kept for clarity: the count, then what it means)
        line = QHBoxLayout(); line.setSpacing(8)
        self._summary = QLabel("")
        self._explain = ui.muted("", size=dt.SIZE_MD, wrap=False)
        self._explain.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        line.addWidget(self._summary); line.addWidget(self._explain, 1)
        tv.addLayout(line)
        top.addLayout(tv, 1)
        again = ui.secondary_button("Search again", icon="search",
                                    tip="Look for the cameras and headbands again")
        again.clicked.connect(flow.search_again)
        top.addWidget(again, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(top)

        body = QHBoxLayout(); body.setSpacing(12)
        grid = QGridLayout(); grid.setSpacing(8); grid.setContentsMargins(0, 0, 0, 0)
        self._tiles = []
        for i, slot in enumerate(flow._cams):
            tile = CameraTile(slot, compact=False)
            tile.change_requested.connect(flow.open_camera_picker)
            grid.addWidget(tile, i // 2, i % 2)
            self._tiles.append(tile)
        gw = QWidget(); gw.setLayout(grid)
        body.addWidget(gw, 3)

        hv = QVBoxLayout(); hv.setSpacing(8); hv.setContentsMargins(0, 0, 0, 0)
        self._hbviews = []
        for which in ("A", "B"):
            view = HeadbandView(flow._bands[which], flow)
            view.reconnect_requested.connect(flow.reconnect_band)
            hv.addWidget(view, 1)
            self._hbviews.append(view)
        hw = QWidget(); hw.setLayout(hv); hw.setMinimumWidth(330)
        body.addWidget(hw, 2)
        root.addLayout(body, 1)

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
        total = len(self.flow._cams) + 2
        missing = []
        if cams < len(self.flow._cams):
            missing.append(_plural(len(self.flow._cams) - cams, "camera"))
        if bands < 2:
            missing.append(_plural(2 - bands, "headband"))
        ok = not missing
        self._summary.setText(("✓ " if ok else "! ") + f"{ready} of {total} devices ready")
        self._summary.setStyleSheet(dt.css_text(
            dt.SIZE_MD, 700, dt.success_color() if ok else dt.warning_color()))
        if missing:
            self._explain.setText("You can still record, but " + " and ".join(missing)
                                  + " will not be recorded.")
        else:
            self._explain.setText("Everything is connected.")


class SetupStep(ui.Card):
    """The plan half of "Get ready": names, rounds, what will happen, the disk
    check, and the Start button. One compact card under the devices."""

    def __init__(self, flow, parent=None):
        super().__init__(parent=parent)
        self.flow = flow
        self._net_cache = {}
        root = QVBoxLayout(self); root.setContentsMargins(18, 14, 18, 14); root.setSpacing(12)

        # Row 1: who is boxing, the bell, where it will be saved
        r1 = QHBoxLayout(); r1.setSpacing(10)
        self._name_a = self._name_field("A"); self._name_b = self._name_field("B")
        r1.addWidget(ui.boxer_badge("A", 26)); r1.addWidget(self._name_a, 2)
        r1.addSpacing(6)
        r1.addWidget(ui.boxer_badge("B", 26)); r1.addWidget(self._name_b, 2)
        r1.addSpacing(10)
        self._bell = QCheckBox("Ring a bell when each round starts")
        self._bell.setStyleSheet(dt.qss_checkbox())
        r1.addWidget(self._bell)
        r1.addStretch(1)
        root.addLayout(r1)
        self._hb_note = ui.muted("", size=dt.SIZE_XS, wrap=False)
        self._hb_note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root.addWidget(self._hb_note)

        # Row 2: the plan (four steppers) · what will happen · Start
        r2 = QHBoxLayout(); r2.setSpacing(16)
        self._st_rounds = Stepper("Rounds", 3, 1, 20, 1, lambda v: str(v))
        self._st_round = Stepper("Round length", 180, 10, 1800, 30, _mmss)
        self._st_break = Stepper("Break between", 60, 0, 600, 15, _mmss)
        self._st_rest = Stepper("Rest before round 1", 60, 0, 600, 15, _mmss)
        for st in (self._st_rounds, self._st_round, self._st_break, self._st_rest):
            r2.addWidget(st, 0, Qt.AlignmentFlag.AlignBottom)
            st.changed.connect(self._recompute)
        plan = QVBoxLayout(); plan.setSpacing(4)
        self._bar = PhaseBar(labelled=True, height=24)
        plan.addWidget(self._bar)
        self._autostop = ui.body("", size=dt.SIZE_XS, color=dt.text_secondary())
        plan.addWidget(self._autostop)
        self._disk = ui.StatusLine("", "idle", size=dt.SIZE_XS)
        plan.addWidget(self._disk)
        self._saves_to = ui.muted("", size=dt.SIZE_CAPTION)
        plan.addWidget(self._saves_to)
        for lab in (self._autostop, self._saves_to):
            lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._disk.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        pw = QWidget(); pw.setLayout(plan); pw.setMinimumWidth(200)
        r2.addWidget(pw, 1)
        self._start = ui.danger_button("Start recording", icon="circle")
        self._start.setMinimumHeight(56); self._start.setMinimumWidth(190)
        self._start.setStyleSheet(dt.qss_danger_button() +
                                  f" QPushButton {{ font-size: {dt.SIZE_LG}px; font-weight: 700; }}")
        self._start.setToolTip("Recording starts with the rest period.")
        self._start.clicked.connect(flow.begin_recording)
        r2.addWidget(self._start, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(r2)
        self._name_a.textChanged.connect(self._on_names_changed)
        self._name_b.textChanged.connect(self._on_names_changed)

    def _name_field(self, which):
        e = QLineEdit(); e.setMinimumHeight(40); e.setMinimumWidth(110)
        e.setStyleSheet(dt.qss_input())
        e.setPlaceholderText(f"Boxer {which}'s name (optional)")
        e.setAccessibleName(f"Boxer {which} name")
        return e

    def _on_names_changed(self, _text):
        a, b = self.names()
        self.flow._names = {"A": a, "B": b}
        self._update_saves_to(self.config())

    def populate_from_settings(self):
        s = self.flow._settings
        cfg = s.session_config()
        self._st_rounds.set_value(cfg.num_rounds)
        self._st_round.set_value(cfg.round_sec)
        self._st_break.set_value(cfg.break_sec)
        self._st_rest.set_value(cfg.baseline_sec)
        self._bell.setChecked(bool(s.round_bell))
        for edit, which in ((self._name_a, "A"), (self._name_b, "B")):
            edit.blockSignals(True)
            edit.setText(self.flow._names.get(which) or "")
            edit.blockSignals(False)
        hb_a = s.headband_for("A") or "a headband"
        hb_b = s.headband_for("B") or "a headband"
        self._hb_note.setText(f"Boxer A wears headband {hb_a} · Boxer B wears {hb_b}. "
                              f"Names are optional and can be hidden later.")
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
        short = {"baseline": "Rest", "break": "Break"}
        self._bar.set_segments([(p.duration, p.phase.value,
                                 short.get(p.phase.value, f"R{p.round_num}"))
                                for p in timeline])
        total = session_total_seconds(cfg)
        rest = f"{_mmss(cfg.baseline_sec)} rest, then " if cfg.baseline_sec else ""
        rounds = f"{_plural(cfg.num_rounds, 'round')} of {_mmss(cfg.round_sec)}"
        breaks = (f" with {_mmss(cfg.break_sec)} breaks"
                  if cfg.num_rounds > 1 and cfg.break_sec else "")
        self._autostop.setText(f"{rest}{rounds}{breaks}. Stops by itself at {_mmss(total)}.")
        self._update_disk(cfg)
        self._update_saves_to(cfg)

    def _is_network(self, base) -> bool:
        if base not in self._net_cache:
            self._net_cache[base] = safe_io.is_network_path(base)
        return self._net_cache[base]

    def _update_disk(self, cfg):
        import shutil
        n_cam = sum(1 for s in self.flow._cams if s.is_live) or len(self.flow._cams)
        n_eeg = sum(1 for b in self.flow._bands.values() if b.connected)
        base = self.flow._settings.capture_dir_resolved()
        state = safe_io.folder_status(base, create=True)
        if state != safe_io.FOLDER_OK:
            self._disk.set("The recordings folder can't be reached. Connect its drive, or "
                           "choose another folder in Settings." if state == safe_io.FOLDER_MISSING
                           else "This computer is not allowed to save into the recordings "
                                "folder. Choose another folder in Settings.", "bad")
            self._start.setEnabled(False); return
        try:
            free = shutil.disk_usage(base).free
        except OSError:
            self._disk.set("", "idle"); self._start.setEnabled(True); return
        vbps = video_bytes_per_sec_for_mbps(self.flow._settings.video_mbps())
        est = estimate_session_bytes(cfg, n_cam, n_eeg, vbps)
        est_h, free_h = human_bytes(est), human_bytes(free)
        verdict = disk_space_verdict(cfg, n_cam, n_eeg, free, vbps)
        if verdict == "blocked":
            self._disk.set(f"Not enough space — this session needs about {est_h}, "
                           f"but only {free_h} is free. Free up space or shorten the session.",
                           "bad")
            self._start.setEnabled(False)
        elif verdict == "low":
            self._disk.set(f"Low space — this session needs about {est_h}. "
                           f"Only {free_h} is free.", "warn")
            self._start.setEnabled(True)
        elif self._is_network(base):
            self._disk.set(f"Enough space ({est_h} needed, {free_h} free) — but this folder is "
                           f"on a network drive. If the connection drops while recording, video "
                           f"can be lost; recording to this computer and copying afterwards is "
                           f"safer.", "warn")
            self._start.setEnabled(True)
        else:
            self._disk.set(f"Enough space — this session needs about {est_h}. "
                           f"You have {free_h} free.", "ok")
            self._start.setEnabled(True)

    def _update_saves_to(self, cfg):
        from brain_battle.data.session_storage import _session_folder_name
        a, b = self.names()
        base = self.flow._settings.capture_dir_resolved()
        folder = _session_folder_name(_dt.datetime.now(), a or None, b or None)
        self._saves_to.setText(f"Saves to {os.path.basename(base)} › {folder}")


class _ReadyPage(QWidget):
    """Step 1: the devices (ConnectStep) above the plan (SetupStep)."""

    def __init__(self, connect_step, setup_step, parent=None):
        super().__init__(parent)
        self._connect, self._setup = connect_step, setup_step
        root = QVBoxLayout(self); root.setContentsMargins(28, 16, 28, 16); root.setSpacing(12)
        root.addWidget(connect_step, 1)
        root.addWidget(setup_step)

    def on_tick(self):
        self._connect.on_tick()
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self._ticks % 25 == 0:          # the disk line follows devices connecting
            self._setup._update_disk(self._setup.config())


# ── Step 2 — Recording ────────────────────────────────────────────────────────────

class RecordingStep(QWidget):
    def __init__(self, flow, parent=None):
        super().__init__(parent)
        self.flow = flow
        root = QVBoxLayout(self); root.setContentsMargins(20, 14, 20, 16); root.setSpacing(10)

        # status strip: recording clock · who · health · stop
        self._strip = ui.Card(tone="ok", radius=dt.RADIUS_MD)
        hdr = QHBoxLayout(self._strip); hdr.setContentsMargins(14, 8, 8, 8); hdr.setSpacing(12)
        self._rec_pill = QLabel("●  REC 0:00")
        self._rec_pill.setStyleSheet(
            f"background: {dt.record_red()}; color: #FFFFFF; border-radius: 13px; "
            f"padding: 4px 12px; font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; "
            f"font-weight: 700;")
        self._rec_pill.setMinimumWidth(112)
        self._rec_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.addWidget(self._rec_pill)
        self._who = QLabel(""); self._who.setStyleSheet(dt.css_text(dt.SIZE_MD, 700))
        hdr.addWidget(self._who)
        self._health_lbl = QLabel(""); self._health_lbl.setWordWrap(True)
        hdr.addWidget(self._health_lbl, 1)
        self._stop = ui.danger_button("Stop recording", icon="stop")
        self._stop.clicked.connect(self._ask_stop)
        hdr.addWidget(self._stop)
        root.addWidget(self._strip)
        self._date = QLabel("")            # (kept: the date is part of the header text)

        # inline stop confirmation (hidden until asked)
        self._confirm = ui.Card(tone="warn", radius=dt.RADIUS_MD)
        cc = QHBoxLayout(self._confirm); cc.setContentsMargins(16, 10, 10, 10); cc.setSpacing(10)
        self._confirm_text = ui.body("", size=dt.SIZE_MD, color=dt._p().warning_text, weight=500)
        cc.addWidget(self._confirm_text, 1)
        keep = ui.secondary_button("Keep recording")
        keep.clicked.connect(lambda: self._confirm.setVisible(False))
        cc.addWidget(keep)
        stopsave = ui.danger_button("Stop and save")
        stopsave.clicked.connect(flow.stop_and_save)
        cc.addWidget(stopsave)
        self._confirm.setVisible(False)
        root.addWidget(self._confirm)

        body = QHBoxLayout(); body.setSpacing(12)

        # the hero: phase, countdown, mark-a-moment — tinted by the phase
        self._big = QFrame(); self._big.setObjectName("hero")
        bv = QVBoxLayout(self._big); bv.setContentsMargins(28, 20, 28, 20); bv.setSpacing(4)
        self._phase_chip = QLabel("")       # (kept for the phase id; the title says it)
        self._phase_chip.setVisible(False)
        self._round_lbl = QLabel("")
        self._round_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bv.addWidget(self._round_lbl)
        bv.addStretch(1)
        self._count = ui.TimerLabel(px=150, weight=QFont.Weight.Bold,
                                    align=Qt.AlignmentFlag.AlignHCenter)
        self._count.setText("0:00")
        bv.addWidget(self._count)
        self._left_lbl = QLabel("left in this round")
        self._left_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bv.addWidget(self._left_lbl)
        bv.addSpacing(8)
        self._progress = _ProgressBar(height=10)
        bv.addWidget(self._progress)
        self._next_lbl = QLabel(""); self._next_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._next_lbl.setWordWrap(True)
        bv.addWidget(self._next_lbl)
        bv.addStretch(1)
        self._mark = QPushButton("  Mark this moment   (M)")
        self._mark.setIcon(QIcon(dt.icon_pixmap("flag", color=dt.text_primary(), size=22)))
        self._mark.setIconSize(QSize(22, 22))
        self._mark.setMinimumHeight(64)
        self._mark.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mark.setToolTip("Something worth finding again just happened — press M. "
                              "You name it later, in Review.")
        self._mark.setStyleSheet(
            f"QPushButton {{ background: #FFFFFF; color: {dt.text_primary()}; "
            f"border: 2px solid {dt.text_primary()}; border-radius: {dt.RADIUS_LG}px; "
            f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_XL}px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: {dt.surface_subtle()}; }}"
            f"QPushButton:pressed {{ background: {dt.text_primary()}; color: #FFFFFF; }}")
        self._mark.clicked.connect(flow.mark_moment)
        bv.addWidget(self._mark)
        self._moment_note = QLabel("No moments marked yet")
        self._moment_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bv.addWidget(self._moment_note)
        bv.addSpacing(8)
        self._session_bar = PhaseBar(labelled=True, height=26)
        bv.addWidget(self._session_bar)
        body.addWidget(self._big, 3)

        # side: cameras + headbands
        side = QVBoxLayout(); side.setSpacing(8); side.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout(); grid.setSpacing(6); grid.setContentsMargins(0, 0, 0, 0)
        self._tiles = []
        for i, slot in enumerate(flow._cams):
            tile = CameraTile(slot, compact=True)
            grid.addWidget(tile, i // 2, i % 2)
            self._tiles.append(tile)
        gw = QWidget(); gw.setLayout(grid)
        side.addWidget(gw, 2)
        self._hbviews = []
        for which in ("A", "B"):
            view = HeadbandView(flow._bands[which], flow, compact=True)
            view.reconnect_requested.connect(flow.reconnect_band)
            side.addWidget(view, 2)
            self._hbviews.append(view)
        sw = QWidget(); sw.setLayout(side); sw.setMinimumWidth(340); sw.setMaximumWidth(460)
        body.addWidget(sw, 2)
        root.addLayout(body, 1)
        self._tint("baseline")

    def _tint(self, phase_id):
        """Colour the hero card by the phase, so the phase reads from a distance
        (the title always says it in words as well)."""
        bg, fg = dt.phase_fill(phase_id)
        self._phase_id = phase_id
        self._big.setStyleSheet(f"QFrame#hero {{ background: {bg}; border: none; "
                                f"border-radius: {dt.RADIUS_XL}px; }}")
        self._round_lbl.setStyleSheet(dt.css_text(34, 700, fg))
        self._left_lbl.setStyleSheet(dt.css_text(dt.SIZE_LG, 500, fg))
        self._next_lbl.setStyleSheet(dt.css_text(dt.SIZE_MD, 500, fg))
        self._moment_note.setStyleSheet(dt.css_text(dt.SIZE_SM, 500, fg))
        self._count.set_color(dt.text_primary())
        self._progress.set(self._progress._frac, fg)

    def _ask_stop(self):
        self._confirm_text.setText(self.flow.stop_prompt_text())
        self._confirm.setVisible(True)

    def reset_for_recording(self):
        self._confirm.setVisible(False)
        self._moment_note.setText("No moments marked yet")
        a, b = self.flow._names.get("A"), self.flow._names.get("B")
        self._who.setText(f"{a or 'Boxer A'} vs {b or 'Boxer B'}")
        self._date.setText(platform_utils.format_date(_dt.datetime.now()))
        short = {"baseline": "Rest", "break": "Break"}
        self._session_bar.set_segments(
            [(p.duration, p.phase.value, short.get(p.phase.value, f"Round {p.round_num}"))
             for p in _build_timeline(self.flow._cfg)])
        self._session_bar.set_now(0.0)

    def set_phase(self, phase_id, label, round_num, total_rounds, duration):
        self._phase_chip.setText(self._phase_name(phase_id, round_num))
        if phase_id == "round":
            self._round_lbl.setText(f"Round {round_num} of {total_rounds}")
            self._left_lbl.setText("left in this round")
        elif phase_id == "baseline":
            self._round_lbl.setText("Rest before round 1")
            self._left_lbl.setText("until round 1")
        else:
            self._round_lbl.setText("Break")
            self._left_lbl.setText("until the next round")
        self._tint(phase_id if phase_id in ("baseline", "round", "break") else "baseline")
        self._next_lbl.setText(self.flow.next_phase_text())

    def _phase_name(self, pid, rn):
        return {"baseline": "Rest", "round": f"Round {rn}", "break": "Break",
                "done": "Finished"}.get(pid, pid.title())

    def set_time(self, remaining, total, elapsed_session):
        self._count.setText(_mmss(remaining))
        self._progress.set((total - remaining) / max(1, total))
        tot = session_total_seconds(self.flow._cfg)
        self._session_bar.set_now(min(1.0, elapsed_session / max(1, tot)))
        self._rec_pill.setText(f"●  REC {_mmss(elapsed_session)}")

    def resizeEvent(self, e):
        # the countdown is as large as the hero card allows
        h = self._big.height() if self._big.height() > 0 else self.height()
        self._count.set_pixel_size(int(max(84, min(190, h * 0.30))))
        super().resizeEvent(e)

    def on_tick(self):
        for t in self._tiles:
            t.update()
        for v in self._hbviews:
            v.refresh()
        ok = self.flow.health_ok()
        text = self.flow.health_text()
        if text != self._health_lbl.text() or ok != getattr(self, "_health_ok", None):
            self._health_ok = ok
            self._health_lbl.setText(text)
            self._strip.set_tone("ok" if ok else "warn")
            self._health_lbl.setStyleSheet(dt.css_text(
                dt.SIZE_MD, 600, dt._p().success_text if ok else dt._p().warning_text))

    def show_moment(self, t_session, count):
        self._moment_note.setText(f"✓ Moment marked at {_mmss(t_session)} · {count} marked so "
                                  f"far. You name them later, in Review.")


# ── Step 3 — Saved ──────────────────────────────────────────────────────────────

class SavedStep(QWidget):
    def __init__(self, flow, parent=None):
        super().__init__(parent)
        self.flow = flow
        outer = QHBoxLayout(self); outer.setContentsMargins(28, 24, 28, 24)
        outer.addStretch(1)
        col = QVBoxLayout(); col.setSpacing(12)
        head = QHBoxLayout(); head.setSpacing(14)
        tick = QLabel(); tick.setPixmap(dt.icon_pixmap("check-circle", color=dt.success_color(), size=44))
        tick.setFixedSize(44, 44)
        head.addWidget(tick)
        ht = QVBoxLayout(); ht.setSpacing(0)
        self._head = ui.title("Recording saved")
        ht.addWidget(self._head)
        self._meta = ui.muted("", size=dt.SIZE_MD)
        ht.addWidget(self._meta)
        head.addLayout(ht, 1)
        col.addLayout(head)

        card = ui.Card()
        cv = QVBoxLayout(card); cv.setContentsMargins(6, 6, 6, 6); cv.setSpacing(0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget(); host.setObjectName("savedHost")
        host.setStyleSheet(f"#savedHost {{ background: {dt.card_bg()}; }}")
        self._list = QVBoxLayout(host); self._list.setContentsMargins(8, 6, 8, 6); self._list.setSpacing(0)
        self._xdf_row = None
        self._has_worth = False
        scroll.setWidget(host)
        cv.addWidget(scroll)
        col.addWidget(card, 1)

        self._folder = ui.muted("", size=dt.SIZE_SM)
        col.addWidget(self._folder)

        bar = QHBoxLayout(); bar.setSpacing(10)
        self._label_btn = ui.primary_button("Label this recording", icon="arrow-right")
        self._label_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)   # arrow after text
        self._label_btn.clicked.connect(flow.start_labelling)
        another = ui.secondary_button("Record another")
        another.clicked.connect(flow.start)
        back = ui.secondary_button("Back to sessions")
        back.clicked.connect(flow._leave_to_sessions)
        finder = ui.ghost_button(platform_utils.show_in_file_manager_label(), icon="folder")
        finder.clicked.connect(flow.reveal_saved)
        bar.addWidget(self._label_btn); bar.addWidget(another); bar.addWidget(back)
        bar.addStretch(); bar.addWidget(finder)
        col.addLayout(bar)
        cw = QWidget(); cw.setLayout(col); cw.setMaximumWidth(860)
        outer.addWidget(cw, 8)
        outer.addStretch(1)

    _XDF_BUSY = "Putting everything on one timeline for analysis (recording.xdf)…"
    _XDF_OK = "Everything on one timeline for analysis (recording.xdf) saved"
    _XDF_FAIL = ("The single analysis file (recording.xdf) could not be made. Nothing is "
                 "lost — it is rebuilt when you export.")

    def set_xdf_state(self, ok: bool):
        """The background build of recording.xdf finished."""
        if self._xdf_row is None:
            return
        try:
            icon, text = self._xdf_row.findChildren(QLabel)[:2]
            color = dt.success_color() if ok else dt.warning_color()
            icon.setText("✓" if ok else "!")
            icon.setStyleSheet(dt.css_text(dt.SIZE_LG, 700, color))
            text.setText(self._XDF_OK if ok else self._XDF_FAIL)
        except RuntimeError:            # the row was already replaced
            self._xdf_row = None

    def _worth_heading(self):
        l = ui.heading("Worth checking", size=dt.SIZE_MD)
        l.setContentsMargins(8, 14, 0, 4)
        return l

    def add_worth_checking(self, text: str):
        """Append a "Worth checking" line after the screen was first filled in."""
        at = self._list.count() - 1                     # before the trailing stretch
        if not self._has_worth:
            self._list.insertWidget(at, self._worth_heading())
            self._has_worth = True
            at += 1
        self._list.insertWidget(at, self._row("!", text, dt.warning_color()))

    def _row(self, icon, text, color=None):
        row = QFrame()
        row.setObjectName("row")
        row.setStyleSheet("QFrame#row { background: transparent; border: none; }")
        h = QHBoxLayout(row); h.setContentsMargins(8, 7, 8, 7); h.setSpacing(12)
        ic = QLabel(icon); ic.setFixedWidth(18)
        ic.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        ic.setStyleSheet(dt.css_text(dt.SIZE_LG, 700, color or dt.success_color()))
        h.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        lb = ui.body(text, size=dt.SIZE_MD, color=dt.text_primary())
        h.addWidget(lb, 1)
        return row

    def populate(self, info: dict):
        ui.clear_layout(self._list)
        self._meta.setText(info["meta"])
        for icon, text, color in info["checklist"]:
            self._list.addWidget(self._row(icon, text, color))
        self._xdf_row = self._row("…", self._XDF_BUSY, dt.text_tertiary())
        self._list.addWidget(self._xdf_row)
        worth = info["worth_checking"]
        self._has_worth = bool(worth)
        if worth:
            self._list.addWidget(self._worth_heading())
            for text in worth:
                self._list.addWidget(self._row("!", text, dt.warning_color()))
        self._list.addStretch()
        self._folder.setText(info["folder_line"])


# ── the flow ───────────────────────────────────────────────────────────────────

class RecordFlow(QWidget):
    session_saved  = pyqtSignal(str)     # "Label this recording" (session root)
    exit_flow      = pyqtSignal()        # Cancel / "Back to sessions"
    help_requested = pyqtSignal(str)     # the CURRENT step's doc anchor
    _xdf_done      = pyqtSignal(str, bool)   # (session root, built OK) — from the build thread

    # Step 1 "Get ready" holds both the devices (ConnectStep) and the plan
    # (SetupStep), so STEP_CONNECT and STEP_SETUP are the same screen.
    STEP_READY, STEP_RECORD, STEP_SAVED = 0, 1, 2
    STEP_CONNECT = STEP_SETUP = STEP_READY
    _STEP_ANCHORS = {
        STEP_READY: "step-1-get-ready",
        STEP_RECORD: "step-2-recording",
        STEP_SAVED: "step-3-saved",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = app_settings.load()
        self._cams = camera_source.make_slots(self)
        self._bands = {"A": HeadbandSlot("A", self), "B": HeadbandSlot("B", self)}
        self._controller = SessionController(self)
        self._beep = RoundBell(self)
        self._keep_awake = KeepAwake()
        self._layout: Optional[SessionLayout] = None
        self._cfg: Optional[SessionConfig] = None
        self._names = {"A": "", "B": ""}
        self._bell = False
        self._session_t0 = None
        self._session_t0_unix = None
        self._moments = []
        self._live_markers = []
        self._contact_writer = None
        self._active_phase = None
        self._stopping_early = False
        self._cam_rec_paths = {}
        self._band_rec_paths = {}
        self._saved_root = None
        self._scan_thread = None
        self._lsl: Optional[LslPublisher] = None
        self._xdf_thread: Optional[threading.Thread] = None
        self._xdf_root: Optional[str] = None     # recording waiting for its XDF
        self._xdf_wait_started = 0.0
        self._xdf_done.connect(self._on_xdf_done)
        self.setObjectName("recordFlow")
        self.setStyleSheet(f"#recordFlow {{ background: {dt.canvas_bg()}; }}")
        self._build()
        self._wire_controller()

        self._tick = QTimer(self); self._tick.setInterval(60)
        self._tick.timeout.connect(self._on_ui_tick)
        self._contact_timer = QTimer(self); self._contact_timer.setInterval(1000)
        self._contact_timer.timeout.connect(self._log_contact)
        # After a recording stops, wait for every camera file to be closed
        # before building recording.xdf from the finished files.
        self._xdf_timer = QTimer(self); self._xdf_timer.setInterval(150)
        self._xdf_timer.timeout.connect(self._maybe_build_xdf)

    # ── construction ──
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        bar = QFrame(); bar.setFixedHeight(56); bar.setObjectName("topBar")
        bar.setStyleSheet(f"#topBar {{ background: {dt.surface()}; "
                          f"border-bottom: 1px solid {dt.border()}; }}")
        h = QHBoxLayout(bar); h.setContentsMargins(12, 8, 16, 8); h.setSpacing(16)
        self._cancel = ui.ghost_button("Cancel", icon="x")
        self._cancel.setMinimumHeight(40)
        self._cancel.clicked.connect(self.cancel)
        h.addWidget(self._cancel)
        h.addStretch()
        self._indicator = _StepIndicator()
        h.addWidget(self._indicator)
        h.addStretch()
        help_btn = ui.help_button()
        help_btn.clicked.connect(
            lambda: self.help_requested.emit(self._STEP_ANCHORS.get(self._stack.currentIndex(),
                                                                    "step-1-get-ready")))
        h.addWidget(help_btn)
        root.addWidget(bar)

        self._stack = QStackedWidget()
        self._connect_step = ConnectStep(self)
        self._setup_step = SetupStep(self)
        self._ready_page = _ReadyPage(self._connect_step, self._setup_step)
        self._recording_step = RecordingStep(self)
        self._saved_step = SavedStep(self)
        for w in (self._ready_page, self._recording_step, self._saved_step):
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
        # Live LSL streams (LabRecorder / LSL viewers can see them from now on).
        self._lsl = LslPublisher(enabled=self._settings.lsl_streams)
        self._lsl.open_markers()
        for slot in self._cams:
            self._lsl.open_video(slot.stem)
        for band in self._bands.values():
            band.publisher = self._lsl
        self._names = {"A": "", "B": ""}
        self._setup_step.populate_from_settings()
        self._set_step(self.STEP_READY)
        self._scan_and_assign()
        self._tick.start()

    def goto_connect(self):
        self._set_step(self.STEP_READY)

    def goto_setup(self):
        """Show "Get ready" (devices + plan are one screen now; kept so callers
        that asked for the old second step still land in the right place)."""
        self._set_step(self.STEP_READY)

    def _set_step(self, idx):
        self._stack.setCurrentIndex(idx)
        self._indicator.set_active(min(idx, 2))
        self._cancel.setVisible(idx != self.STEP_SAVED)

    def _reset_session_state(self):
        self._layout = None; self._cfg = None
        self._moments = []; self._active_phase = None
        self._live_markers = []
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
            import brain_battle.capture.simulation as simulation
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
        from brain_battle.capture.eeg_module import _MuseScanThread
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
        state = safe_io.folder_status(base, create=True)
        if state != safe_io.FOLDER_OK:
            QMessageBox.critical(self, "The recordings folder can't be used",
                                 safe_io.folder_problem_text(base, state)); return
        if not self._disk_guard(base):
            return

        boxer_a = {"name": a or None, "headband": self._settings.headband_for("A")}
        boxer_b = {"name": b or None, "headband": self._settings.headband_for("B")}
        try:
            self._layout = create_session(base, self._cfg, boxer_a=boxer_a, boxer_b=boxer_b)
            attach_session_log(self._layout.root)
        except Exception as e:
            QMessageBox.critical(
                self, "The recording could not be started",
                f"The folder for this recording could not be made in {base} ({e}). Check that "
                f"the drive is connected and that you are allowed to save there."); return

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
        # ONE origin for every source, on the one capture clock.
        origin = clock.session_origin()
        self._session_t0 = origin["t0_monotonic"]
        self._session_t0_unix = origin["t0_unix"]
        cam_clock = (self._session_t0, self._session_t0_unix)
        try:
            self._layout.set_capture_origin(**origin)
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
                slot.start_recording(path, cam_clock, self._lsl)
                self._cam_rec_paths[slot.index] = path
        # headbands
        from brain_battle.capture.eeg_module import muse_friendly
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
                self._publish_marker(marker, t)
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
                self._publish_marker("SESSION_END", t)
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
            self._saved_root = self._xdf_root = str(self._layout.root)
            self._xdf_wait_started = time.monotonic()
            self._xdf_timer.start()
        if goto_saved and self._saved_root:
            self._saved_step.populate(self._build_saved_info())
            self._set_step(self.STEP_SAVED)

    def _publish_marker(self, text, t_session):
        """A phase start / marked moment happened: show it on the live EEG and
        publish it on the LSL marker stream."""
        if self._session_t0 is None:
            return
        pretty = "Moment" if text == session_xdf.MOMENT_MARKER else short_phase(text)
        colour = dt.record_red() if text == session_xdf.MOMENT_MARKER else dt.text_secondary()
        self._live_markers.append((self._session_t0_unix + t_session, pretty, colour))
        if self._lsl is not None:
            self._lsl.push_marker(text, self._session_t0 + t_session)

    def live_markers(self):
        """[(unix time, text, colour)] for the live EEG display."""
        return self._live_markers

    def session_origin_unix(self):
        """The running recording's start (Unix seconds), or None when idle."""
        return self._session_t0_unix if self._controller.is_running else None

    # ── recording.xdf (built from the finished files, off the UI thread) ──
    def _maybe_build_xdf(self):
        waited = time.monotonic() - self._xdf_wait_started
        if not all(s.recording_finalised for s in self._cams) and waited < 20.0:
            return
        if self._xdf_thread is not None:
            return                      # a build is running; try again next tick
        self._xdf_timer.stop()
        root, self._xdf_root = self._xdf_root, None
        if not root:
            return

        def work():
            try:
                ok = session_xdf.build_session_xdf(root) is not None
            except Exception as e:
                log.error("building recording.xdf failed: %r", e)
                ok = False
            self._xdf_done.emit(root, ok)

        self._xdf_thread = threading.Thread(target=work, name="xdf-build", daemon=True)
        self._xdf_thread.start()

    def _on_xdf_done(self, root, ok):
        self._xdf_thread = None
        if root == self._saved_root:
            self._saved_step.set_xdf_state(ok)
            for text in self._frame_rate_notes(root):
                self._saved_step.add_worth_checking(text)

    def _frame_rate_notes(self, root) -> list:
        """A line for each camera whose real frame rate was more than 1 % off
        the rate it was asked for (only knowable once its file is closed)."""
        from brain_battle.data.session_storage import camera_frame_rates
        want = float(self._settings.video_dims()[2])
        titles = {s.stem: s.title for s in self._cams}
        notes = []
        try:
            for stem, _n, fps in camera_frame_rates(root):
                if abs(fps - want) / want > 0.01:
                    notes.append(
                        f"{titles.get(stem, stem)} recorded {fps:.1f} frames per second instead "
                        f"of {want:.0f}. Playback and the analysis files use each frame's real "
                        f"time, so nothing is out of step — but the camera or its cable may "
                        f"need a look.")
        except Exception as e:
            log.warning("frame-rate check failed: %r", e)
        return notes

    def wait_for_xdf(self, timeout: float = 30.0) -> bool:
        """Block (processing events) until the pending recording.xdf build has
        finished. Used on shutdown and by tests."""
        from PyQt6.QtWidgets import QApplication
        deadline = time.monotonic() + timeout
        while (self._xdf_timer.isActive() or self._xdf_thread is not None) \
                and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        return not self._xdf_timer.isActive() and self._xdf_thread is None

    # ── mark this moment ──
    def mark_moment(self):
        if not self._controller.is_running or self._layout is None:
            return
        t = time.monotonic() - self._session_t0
        entry = self._layout.add_moment(t)
        self._moments.append(entry)
        self._publish_marker(session_xdf.MOMENT_MARKER, t)
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
            return "Last round — the recording stops by itself when it ends."
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
        root = self._saved_root
        a, b = self._names.get("A"), self._names.get("B")
        vs = f"{a or 'Boxer A'} vs {b or 'Boxer B'}"
        markers = read_phase_markers(root)
        rounds = self._cfg.num_rounds
        mins = max(1, round(session_total_seconds(self._cfg) / 60))
        when = platform_utils.format_date_time(_dt.datetime.now())
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
        breaks = f" and {_plural(n_breaks, 'break')}" if n_breaks else ""
        checklist.append(("✓", f"{_plural(n_rounds, 'round')}{breaks} marked automatically",
                          dt.success_color()))
        if self._moments:
            checklist.append(("✓", f"{_plural(len(self._moments), 'moment')} you marked — name "
                                   f"them when you label", dt.success_color()))

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
                platform_utils.reveal_in_file_manager(self._saved_root)
            except Exception as e:
                log.warning("showing the recording in the file manager failed: %s", e)

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
            b.publisher = None
        if self._lsl is not None:
            self._lsl.close()
            self._lsl = None

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
        self._stop_devices()          # closes every camera file …
        self.wait_for_xdf()           # … so the pending recording.xdf can finish


__all__ = ["RecordFlow"]

