"""
record_screens.py
─────────────────
The views of the New-recording flow (docs/DESIGN.md "New recording", design
F). ``record_flow.RecordFlow`` owns the devices and the recording; these
widgets only show its state and call it back.

  FlowHeader       Cancel · "New recording" · (REC timer, who is boxing) ·
                   1 Get ready  2 Record  3 Saved · (Stop recording)
  GetReadyScreen   the checklist + Test timing + Start on the left; cameras,
                   both boxers (code, colour, head sensors, live EEG) and the
                   plan on the right
  RecordingScreen  four cameras with their timecodes; the countdown (small,
                   under the cameras) and the phase bar; Mark this moment (M);
                   both boxers' live EEG with grey automatic markers; the
                   timing line
  SavedScreen      what was saved, the timing result or "Timing not measured",
                   anything worth checking, what to do next
  MomentDialog     "⚑ Moment marked at 3:27" — note, Save note / Add note
                   later (Enter / Esc). Recording never pauses.
  TimingDialog     live rate / delay / jitter / dropped per device; Run buzzer
                   test (Get ready only). Never pauses recording.
"""
from __future__ import annotations

import os
import random

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QFont, QPen
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
    QSizePolicy, QLineEdit, QCheckBox, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QScrollArea,
)

import brain_battle.ui.design_tokens as dt
import brain_battle.ui.widgets as ui
import brain_battle.data.fighters as fighters
import brain_battle.capture.timing as timing
from brain_battle.capture.session_controller import LEAD_IN_DEFAULT
from brain_battle.data.signals import EEG_CHANNELS
from brain_battle.ui.eeg_viewer import EEGViewer


def mmss(sec) -> str:
    sec = max(0, int(round(sec or 0)))
    return f"{sec // 60}:{sec % 60:02d}"


def mmss_cc(sec) -> str:
    """'03:27.40' — the camera timecode."""
    sec = max(0.0, float(sec or 0))
    m = int(sec // 60)
    return f"{m:02d}:{sec - 60 * m:05.2f}"


# ── header ────────────────────────────────────────────────────────────────────

class FlowHeader(QFrame):
    cancel = pyqtSignal()
    stop = pyqtSignal()
    _STEPS = ["Get ready", "Record", "Saved"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("flowHeader"); self.setFixedHeight(66)
        self.setStyleSheet(f"#flowHeader {{ background: {dt.surface()}; "
                           f"border-bottom: 1px solid {dt.border()}; }}")
        h = QHBoxLayout(self); h.setContentsMargins(20, 10, 24, 10); h.setSpacing(18)
        self.cancel_btn = QPushButton("✕  Cancel")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet(f"QPushButton {{ border: 0; background: transparent; "
                                      f"color: #475467; font-family: {dt.FONT_UI}; "
                                      f"font-size: {dt.SIZE_MD}px; font-weight: 500; padding: 8px; }}")
        self.cancel_btn.clicked.connect(self.cancel.emit)
        h.addWidget(self.cancel_btn)
        self.rec_pill = QLabel("")
        self.rec_pill.setObjectName("recPill")
        self.rec_pill.setStyleSheet(f"#recPill {{ background: {dt.error_bg()}; color: {dt.error_color()}; "
                                    f"border-radius: 13px; padding: 5px 12px; font-family: {dt.FONT_MONO}; "
                                    f"font-size: {dt.SIZE_SM}px; font-weight: 600; }}")
        self.rec_pill.setVisible(False)
        h.addWidget(self.rec_pill)
        t = QLabel("New recording"); t.setStyleSheet(dt.css_text(17, 700))
        h.addWidget(t)
        self.mid = QLabel(""); self.mid.setTextFormat(Qt.TextFormat.RichText)
        h.addWidget(self.mid)
        h.addStretch()
        self._steps = []
        for i, name in enumerate(self._STEPS):
            l = QLabel(); self._steps.append(l); h.addWidget(l)
        self.stop_btn = QPushButton("■  Stop recording")
        self.stop_btn.setMinimumHeight(42)
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setStyleSheet(f"QPushButton {{ border: 1px solid {dt.record_red()}; "
                                    f"color: {dt.error_color()}; background: {dt.surface()}; "
                                    f"border-radius: 10px; padding: 0 16px; font-family: {dt.FONT_UI}; "
                                    f"font-size: {dt.SIZE_MD}px; font-weight: 600; }}")
        self.stop_btn.clicked.connect(self.stop.emit)
        self.stop_btn.setVisible(False)
        h.addWidget(self.stop_btn)
        self.set_step(1)

    def set_step(self, n: int):
        for i, l in enumerate(self._steps):
            k = i + 1
            done = k < n or n == 3
            if done:
                l.setText(f"✓ {self._STEPS[i]}")
                l.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt.success_color()))
            elif k == n:
                l.setText(f"{k} {self._STEPS[i]}")
                l.setStyleSheet(dt.css_text(dt.SIZE_SM, 700, dt.accent_hover()))
            else:
                l.setText(f"{k} {self._STEPS[i]}")
                l.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.text_tertiary()))
        self.rec_pill.setVisible(n == 2)
        self.stop_btn.setVisible(n == 2)
        self.cancel_btn.setVisible(n != 3)
        if n != 2:
            self.mid.setText("")


# ── a camera picture ─────────────────────────────────────────────────────────

class CameraTile(QFrame):
    """One camera: live picture, its name, its state ("60 fps" / "Recording"
    / "Not found" / "Reconnecting…") and, while recording, the timecode."""

    def __init__(self, slot, title: str = "", aspect: float = 16 / 10, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.title = title or slot.title
        self.timecode = ""
        self._aspect = aspect
        self.setMinimumSize(150, 84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def hasHeightForWidth(self):          # noqa: N802
        return True

    def heightForWidth(self, w):          # noqa: N802
        return int(w / self._aspect)

    def status(self):
        s = self.slot
        if s.device is None or s.state == "error":
            return "Not found", dt.warning_color()
        if s.stalled:
            return "Reconnecting…", "#FEDF89"
        if s.rec_failed:
            return "Recording stopped", "#FDA29B"
        if s.is_recording:
            return "Recording" + (f" · {s.dropped} frames lost" if s.dropped else ""), "#FFFFFF"
        if s.state == "live":
            fps = s.actual_fps or s.fps
            return f"{fps:.0f} fps", "#FFFFFF"
        return "Connecting…", "#FEDF89"

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            missing = self.slot.device is None or self.slot.state == "error"
            if missing:
                p.setBrush(QColor(dt.canvas_bg()))
                p.setPen(QPen(QColor("#E8A867"), 2, Qt.PenStyle.DashLine))
                p.drawRoundedRect(r, 12, 12)
            else:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(dt.stage()))
                p.drawRoundedRect(r, 12, 12)
                img = self.slot.last_image
                if img is not None and not img.isNull():
                    iw, ih = img.width(), img.height()
                    sc = min(r.width() / iw, r.height() / ih)
                    dw, dh = iw * sc, ih * sc
                    p.drawImage(QRectF(r.x() + (r.width() - dw) / 2, r.y() + (r.height() - dh) / 2,
                                       dw, dh), img)
            f = QFont("Inter"); f.setPixelSize(12); f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(self.title) + 18
            if missing:
                p.setPen(QColor(dt.warning_color()))
                p.drawText(QRectF(r.x() + 10, r.y() + 10, tw, 22), Qt.AlignmentFlag.AlignVCenter,
                           self.title)
            else:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(255, 255, 255, 240))
                p.drawRoundedRect(QRectF(r.x() + 10, r.y() + 10, tw, 22), 7, 7)
                p.setPen(QColor(dt.text_primary()))
                p.drawText(QRectF(r.x() + 10, r.y() + 10, tw, 22), Qt.AlignmentFlag.AlignCenter,
                           self.title)
            text, col = self.status()
            f2 = QFont("Inter"); f2.setPixelSize(12)
            if missing:
                f2.setWeight(QFont.Weight.DemiBold)
            p.setFont(f2)
            sw = p.fontMetrics().horizontalAdvance(text) + (18 if missing else 30)
            sr = QRectF(r.x() + 10, r.bottom() - 32, sw, 22)
            if not missing:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(0, 0, 0, 140))
                p.drawRoundedRect(sr, 7, 7)
                dot = "#4ADE80" if text.endswith("fps") or text.startswith("Recording") else col
                p.setBrush(QColor(dot)); p.drawEllipse(QPointF(sr.x() + 11, sr.center().y()), 3, 3)
                p.setPen(QColor(col))
                p.drawText(sr.adjusted(18, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, text)
            else:
                p.setPen(QColor(col)); p.drawText(sr, Qt.AlignmentFlag.AlignVCenter, text)
            if self.timecode and not missing:
                fm = QFont("JetBrains Mono"); fm.setPixelSize(11); p.setFont(fm)
                cw = p.fontMetrics().horizontalAdvance(self.timecode) + 16
                cr = QRectF(r.right() - cw - 10, r.bottom() - 30, cw, 20)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(0, 0, 0, 140))
                p.drawRoundedRect(cr, 6, 6)
                p.setPen(QColor("#B9F5C9")); p.drawText(cr, Qt.AlignmentFlag.AlignCenter, self.timecode)
        finally:
            p.end()


# ── the head with its four sensors ───────────────────────────────────────────

class HeadIcon(QWidget):
    """A head seen from above, the four skin sensors as dots: green = signal
    looks good, orange = signal looks poor (our heuristic — the band does not
    report how well it fits)."""
    ORDER = ("AF7", "AF8", "TP9", "TP10")   # left forehead, right forehead, left ear, right ear

    def __init__(self, size: int = 56, parent=None):
        super().__init__(parent)
        self._states = {}
        self.setFixedSize(size, int(size * 1.15))

    def set_states(self, states: dict):
        if states != self._states:
            self._states = dict(states)
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            s = self.width() / 56.0
            p.scale(s, s)
            line = QPen(QColor("#556070"), 1.8)
            p.setPen(line); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolyline([QPointF(24, 9), QPointF(28, 2), QPointF(32, 9)])     # nose
            p.drawArc(QRectF(2, 31, 10, 9), 90 * 16, 180 * 16)                    # ears
            p.drawArc(QRectF(44, 31, 10, 9), -90 * 16, 180 * 16)
            p.setBrush(QColor("#FFFFFF")); p.drawEllipse(QRectF(7, 11, 42, 50))
            pos = {"AF7": (17, 19), "AF8": (39, 19), "TP9": (10, 44), "TP10": (46, 44)}
            p.setPen(Qt.PenStyle.NoPen)
            for ch, (x, y) in pos.items():
                st = self._states.get(ch)
                col = "#C7CDD8" if st is None else ("#2E9E5B" if st == "good" else "#E8890C")
                p.setBrush(QColor(col)); p.drawEllipse(QPointF(x, y), 5, 5)
        finally:
            p.end()


_PLACE = {"TP9": "left-ear", "AF7": "left-forehead", "AF8": "right-forehead", "TP10": "right-ear"}


def poor_sensors(statuses: dict) -> list:
    """The sensors whose signal does not look good, e.g. ['right ear (TP10)']."""
    return [f"{_PLACE[ch].replace('-', ' ')} ({ch})" for ch in ("TP9", "AF7", "AF8", "TP10")
            if statuses.get(ch) not in (None, "good")]


def ask_start_anyway(parent, text: str) -> bool:
    """'Signal looks poor on K7Q2 right ear (TP10). Start anyway?' —
    Start anyway (True) / Go back (False). Tests replace this function."""
    return ui.ask(parent, "Signal looks poor", text, "Start anyway", "Go back",
                  tone="warn", safe_first=True)


def poor_text(statuses: dict) -> str:
    """'The right-ear signal (TP10) looks poor' — or '' when all look good."""
    weak = [ch for ch in ("TP9", "AF7", "AF8", "TP10") if statuses.get(ch) not in (None, "good")]
    if not weak:
        return ""
    names = ", ".join(f"{_PLACE[ch]} ({ch})" for ch in weak)
    return f"The {names} signal{'s' if len(weak) > 1 else ''} look{'' if len(weak) > 1 else 's'} poor"


class Stepper(QFrame):
    """'Rounds  [−] 3 [+]'."""
    changed = pyqtSignal(int)

    def __init__(self, caption, value, lo, hi, step, fmt, parent=None):
        super().__init__(parent)
        self._v, self._lo, self._hi, self._step, self._fmt = value, lo, hi, step, fmt
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
        cap = QLabel(caption); cap.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt.text_secondary()))
        h.addWidget(cap)
        self._minus = self._btn("−", f"Less {caption.lower()}"); self._minus.clicked.connect(lambda: self._bump(-1))
        self._val = QLabel(); self._val.setMinimumWidth(44)
        self._val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val.setStyleSheet(dt.css_text(dt.SIZE_MD, 600, None, f"font-family: {dt.FONT_MONO};"))
        self._plus = self._btn("+", f"More {caption.lower()}"); self._plus.clicked.connect(lambda: self._bump(1))
        h.addWidget(self._minus); h.addWidget(self._val); h.addWidget(self._plus)
        self._render()

    def _btn(self, txt, name):
        b = QPushButton(txt); b.setFixedSize(34, 36); b.setAccessibleName(name)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(dt.qss_secondary_button(True) + " QPushButton { padding: 0; font-size: 16px; }")
        return b

    def _bump(self, d):
        self.set_value(self._v + d * self._step); self.changed.emit(self._v)

    def _render(self):
        self._val.setText(self._fmt(self._v))
        self._minus.setEnabled(self._v > self._lo); self._plus.setEnabled(self._v < self._hi)

    def value(self):
        return self._v

    def set_value(self, v, force: bool = False):
        """Clamped to the stepper's range — except ``force`` (a plan set by
        code: tools and tests use short rounds the buttons do not offer)."""
        self._v = int(v) if force else max(self._lo, min(self._hi, int(v)))
        self._render()


class PhaseBar(QWidget):
    """The plan as blocks (lead-in, baseline, rounds, breaks) with a playhead;
    done parts blue, the current one light blue."""

    def __init__(self, height: int = 34, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._segments = []      # (duration, kind, text)
        self._now = None

    def set_segments(self, segments):
        self._segments = list(segments); self.update()

    def set_now(self, t):
        self._now = t; self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            total = max(0.001, sum(s[0] for s in self._segments))
            W, H = self.width(), self.height()
            f = QFont("Inter"); f.setPixelSize(12); f.setWeight(QFont.Weight.DemiBold); p.setFont(f)
            x = 0.0; t0 = 0.0
            now = self._now
            for dur, kind, text in self._segments:
                w = W * dur / total
                rect = QRectF(x, 0, max(1.0, w - 3), H)
                done = now is not None and now >= t0 + dur
                on = now is not None and t0 <= now < t0 + dur
                bg = dt.accent() if done else ("#8FAAEC" if on else "#E3E8F0")
                fg = "#FFFFFF" if done else (dt.text_primary() if on else "#475467")
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(bg))
                p.drawRoundedRect(rect, 7, 7)
                label = (text + (" ✓" if done and kind == "round" else "")) if kind != "lead_in" else ""
                if label and p.fontMetrics().horizontalAdvance(label) + 6 < rect.width():
                    p.setPen(QColor(fg)); p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
                x += w; t0 += dur
            if now is not None:
                px = W * min(1.0, now / total)
                p.setPen(QPen(QColor(dt.record_red()), 2))
                p.drawLine(QPointF(px, -2), QPointF(px, H + 2))
        finally:
            p.end()


def plan_segments(timeline) -> list:
    """(duration, kind, text) blocks for a PhaseBar from the controller's timeline."""
    out = []
    for p in timeline:
        k = p.phase.value
        text = {"lead_in": "", "baseline": "Baseline", "break": "Break"}.get(k, f"R{p.round_num}")
        out.append((p.duration, k, text))
    return out


# ── Get ready: one boxer ─────────────────────────────────────────────────────

class BoxerCard(QFrame):
    """Get ready: one boxer — code (+ New code), colour, the headband's four
    sensors, its battery, the live EEG."""
    changed = pyqtSignal()

    def __init__(self, flow, which: str, parent=None):
        super().__init__(parent)
        self.flow = flow
        self.which = which
        self.slot = flow._bands[which]
        self.colour = fighters.DEFAULT_COLOUR[which]
        self.setObjectName(f"boxerCard{which}")
        v = QVBoxLayout(self); v.setContentsMargins(16, 14, 16, 14); v.setSpacing(10)
        top = QHBoxLayout(); top.setSpacing(12)
        self._badge_box = QHBoxLayout()
        top.addLayout(self._badge_box)
        cv = QVBoxLayout(); cv.setSpacing(2)
        cv.addWidget(ui.muted(f"Boxer {which} code", size=dt.SIZE_SM))
        self.code = QLineEdit(); self.code.setMaxLength(4); self.code.setPlaceholderText("····")
        self.code.setAccessibleName(f"Boxer {which} code")
        self.code.setFixedWidth(100); self.code.setMinimumHeight(40)
        self.code.textEdited.connect(self._on_code_edited)
        cv.addWidget(self.code)
        top.addLayout(cv)
        self.new_code = ui.secondary_button("New code", compact=True)
        self.new_code.clicked.connect(self._new_code)
        top.addWidget(self.new_code, 0, Qt.AlignmentFlag.AlignBottom)
        colv = QVBoxLayout(); colv.setSpacing(4)
        colv.addWidget(ui.muted("Colour", size=dt.SIZE_SM))
        self._swatches = QHBoxLayout(); self._swatches.setSpacing(6)
        colv.addLayout(self._swatches)
        top.addLayout(colv)
        top.addStretch()
        v.addLayout(top)
        self.code_problem = ui.body("", color=dt.error_color(), size=dt.SIZE_SM)
        self.code_problem.setVisible(False)
        v.addWidget(self.code_problem)
        mid = QHBoxLayout(); mid.setSpacing(14)
        self.head = HeadIcon(56)
        mid.addWidget(self.head, 0, Qt.AlignmentFlag.AlignTop)
        mv = QVBoxLayout(); mv.setSpacing(6)
        hb = QHBoxLayout(); hb.setSpacing(8)
        hb.addWidget(ui.muted("Headband", size=dt.SIZE_SM))
        self.band_choice = ui.Dropdown(compact=True)
        self.band_choice.setAccessibleName(f"Headband for Boxer {which}")
        self.band_choice.setToolTip(f"Which headband Boxer {which} wears")
        self.band_choice.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.band_choice.activated.connect(self._band_chosen)
        hb.addWidget(self.band_choice, 1)
        mv.addLayout(hb)
        self.device_line = QLabel("")        # "No headband found yet" when there is none
        self.device_line.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.text_tertiary()))
        self.device_line.setVisible(False)
        mv.addWidget(self.device_line)
        self.signal = ui.WrapLabel("")
        mv.addWidget(self.signal)
        self.adjusted = ui.secondary_button("I’ve adjusted it", compact=True)
        self.adjusted.clicked.connect(self._recheck)
        self.adjusted.setVisible(False)
        mv.addWidget(self.adjusted, 0, Qt.AlignmentFlag.AlignLeft)
        self.reconnect = ui.secondary_button("Reconnect", compact=True)
        self.reconnect.clicked.connect(lambda: flow.reconnect_band(which))
        self.reconnect.setVisible(False)
        mv.addWidget(self.reconnect, 0, Qt.AlignmentFlag.AlignLeft)
        mid.addLayout(mv, 1)
        v.addLayout(mid)
        self.eeg = EEGViewer(channels=EEG_CHANNELS, live=True, min_row_px=17)
        self.eeg.set_timebase(5); self.eeg.set_scale(None)
        self.eeg.setMinimumHeight(17 * 5 + 30)
        v.addWidget(self.eeg)
        self.set_colours(fighters.PRESET_COLOURS)

    # code + colour
    def _on_code_edited(self, text):
        clean = fighters.normalise_code(text)
        if clean != text:
            pos = self.code.cursorPosition()
            self.code.setText(clean); self.code.setCursorPosition(min(pos, len(clean)))
        self._show_code_problem(); self.changed.emit()

    def _show_code_problem(self):
        t = self.code.text()
        bad = bool(t) and not fighters.code_ok(t)
        self.code_problem.setText("Use exactly 4 capital letters or digits, like K7Q2.")
        self.code_problem.setVisible(bad)
        self.code.setStyleSheet(dt.qss_input() + (
            f" QLineEdit {{ border-color: {dt.record_red()}; }}" if bad else "")
            + f" QLineEdit {{ font-family: {dt.FONT_MONO}; font-size: 18px; font-weight: 700; "
              f"letter-spacing: 3px; }}")

    def _new_code(self):
        other = self.flow._get_ready.card("B" if self.which == "A" else "A").code.text()
        self.code.setText(fighters.new_code(avoid=[other]))
        self._show_code_problem(); self.changed.emit()

    def set_code(self, code: str):
        self.code.setText(fighters.normalise_code(code)); self._show_code_problem()

    def set_colours(self, colours):
        self._colours = list(colours)
        ui.clear_layout(self._swatches)
        for c in self._colours:
            b = QPushButton(); b.setFixedSize(28, 28)
            b.setToolTip(c["name"]); b.setAccessibleName(c["name"])
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            on = c["name"] == self.colour
            b.setStyleSheet(f"QPushButton {{ background: {c['color']}; border-radius: 14px; "
                            f"border: {'3px solid ' + dt.accent() if on else '2px solid #FFFFFF'}; }}")
            b.clicked.connect(lambda _c=False, n=c["name"]: self.pick_colour(n))
            self._swatches.addWidget(b)
        self._render_badge()

    def pick_colour(self, name: str):
        self.colour = name
        self.set_colours(self._colours); self.changed.emit()

    def colour_hex(self) -> str:
        return fighters.colour_hex(self.colour, self._colours)

    def _render_badge(self):
        ui.clear_layout(self._badge_box)
        self._badge_box.addWidget(ui.fighter_badge(self.which, self.colour_hex(), 30))

    def _recheck(self):
        self.adjusted.setText("Checking the signal…"); self.adjusted.setEnabled(False)
        QTimer.singleShot(1100, self._recheck_done)

    def _recheck_done(self):
        self.adjusted.setText("I’ve adjusted it"); self.adjusted.setEnabled(True)
        self.refresh()

    # live state
    def statuses(self) -> dict:
        s = self.slot
        return s.contact()[1] if s.connected else {}

    def _band_chosen(self, i):
        name = self.band_choice.itemData(i)
        if name:
            self.flow.assign_headband(self.which, name)

    def refresh_choices(self):
        """The headband dropdown: every headband found (name + battery)."""
        bands = self.flow.connected_headbands()
        mine = self.slot.device_name
        dd = self.band_choice
        dd.blockSignals(True)
        dd.clear()
        for name, batt in bands:
            dd.addItem(f"{name} · battery {batt}%" if batt is not None else name, name)
        if mine and dd.findData(mine) < 0:
            dd.addItem(mine, mine)
        dd.setCurrentIndex(max(0, dd.findData(mine)) if mine else -1)
        dd.setEnabled(dd.count() > 0 and not self.flow.is_recording())
        dd.blockSignals(False)
        self.device_line.setText("No headband found yet")
        self.device_line.setVisible(dd.count() == 0)

    def refresh(self):
        s = self.slot
        dd = self.band_choice
        if s.device is not None and s.battery is not None:
            i = dd.findData(s.device_name)
            want = f"{s.device_name} · battery {s.battery}%"
            if i >= 0 and dd.itemText(i) != want:
                dd.setItemText(i, want)
        st = self.statuses()
        self.head.set_states(st)
        poor = poor_text(st)
        if s.lost:
            self.signal.setText("Lost connection — switch the headband on and press Reconnect.")
            self.signal.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt.error_color()))
        elif not s.connected:
            self.signal.setText("Connecting…" if s.device else "Switch the headband on.")
            self.signal.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.text_tertiary()))
        elif poor:
            self.signal.setText(f"{poor}. Often the band isn’t flat behind the ear or hair is in "
                                f"the way. Adjust it, then press the button.")
            self.signal.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.warning_color()))
        else:
            self.signal.setText("✓ Signal looks good on all 4 sensors")
            self.signal.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt.success_color()))
        self.adjusted.setVisible(bool(poor) and s.connected and not s.lost)
        self.reconnect.setVisible(bool(s.lost))
        self.setStyleSheet(f"#boxerCard{self.which} {{ background: {dt.surface()}; border-radius: 16px; "
                           f"border: {'1.5px solid ' + dt.warning_border() if poor else '1px solid ' + dt.border()}; }}")
        ts, chans = s.trace() if s.connected else (None, None)
        self.eeg.set_data(ts, chans or {})


# ── Get ready ────────────────────────────────────────────────────────────────

class GetReadyScreen(QWidget):
    """Step 1: the checklist and Start on the left; cameras, both boxers and the
    plan on the right."""

    def __init__(self, flow, parent=None):
        super().__init__(parent)
        self.flow = flow
        root = QHBoxLayout(self); root.setContentsMargins(24, 18, 24, 24); root.setSpacing(22)
        # ── left: checklist ──
        aside = ui.Card(radius=16); aside.setMinimumWidth(270); aside.setMaximumWidth(340)
        av = QVBoxLayout(aside); av.setContentsMargins(22, 22, 22, 22); av.setSpacing(12)
        t = QLabel("Get ready"); t.setStyleSheet(dt.css_text(22, 700)); av.addWidget(t)
        av.addWidget(ui.muted("Work down the list. Start unlocks when everything is green."))
        self._checks_box = QVBoxLayout(); self._checks_box.setSpacing(6)
        self._checks_box.setContentsMargins(0, 0, 0, 0)
        checks_host = QWidget(); checks_host.setObjectName("readyChecks")
        checks_host.setStyleSheet("#readyChecks { background: transparent; }")
        ch_v = QVBoxLayout(checks_host); ch_v.setContentsMargins(0, 0, 0, 0)
        ch_v.addLayout(self._checks_box); ch_v.addStretch()
        checks_scroll = QScrollArea(); checks_scroll.setWidgetResizable(True)
        checks_scroll.setFrameShape(QFrame.Shape.NoFrame)
        checks_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        checks_scroll.setObjectName("readyChecksScroll")
        checks_scroll.setStyleSheet("#readyChecksScroll { background: transparent; }")
        checks_scroll.viewport().setAutoFillBackground(False)
        checks_scroll.setWidget(checks_host)
        av.addWidget(checks_scroll, 1)       # the list scrolls; Start stays at the bottom
        self.timing_btn = ui.secondary_button("⏱  Test timing")
        self.timing_btn.clicked.connect(flow.open_timing_dialog)
        av.addWidget(self.timing_btn)
        self._start = ui.primary_button("●  Start recording")
        self._start.setMinimumHeight(52)
        self._start.clicked.connect(self.start_pressed)
        av.addWidget(self._start)
        self.start_note = QLabel(""); self.start_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.start_note.setWordWrap(True)
        av.addWidget(self.start_note)
        root.addWidget(aside, 1)
        # ── right ──
        right = QVBoxLayout(); right.setSpacing(14)
        cams = ui.Card(radius=16)
        cv = QVBoxLayout(cams); cv.setContentsMargins(16, 14, 16, 14); cv.setSpacing(10)
        ch = QHBoxLayout()
        h2 = QLabel("Cameras"); h2.setStyleSheet(dt.css_text(17, 700)); ch.addWidget(h2)
        self.cam_count = QLabel(""); ch.addWidget(self.cam_count)
        ch.addStretch()
        self.search_btn = ui.secondary_button("Search again", compact=True)
        self.search_btn.clicked.connect(flow.search_again)
        ch.addWidget(self.search_btn)
        cv.addLayout(ch)
        grid = QHBoxLayout(); grid.setSpacing(8)
        self.tiles = [CameraTile(s) for s in flow._cams]
        self.cam_choice = {}                 # slot index → its camera dropdown
        for tile in self.tiles:
            col = QVBoxLayout(); col.setSpacing(6)
            dd = ui.Dropdown(compact=True)
            dd.setAccessibleName(f"Camera for {tile.slot.title}")
            dd.setToolTip(f"Which camera is {tile.slot.title}")
            dd.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            dd.activated.connect(lambda _i, idx=tile.slot.index: self._camera_chosen(idx))
            self.cam_choice[tile.slot.index] = dd
            col.addWidget(dd)
            col.addWidget(tile)
            grid.addLayout(col, 1)
        cv.addLayout(grid)
        right.addWidget(cams)
        boxers = QHBoxLayout(); boxers.setSpacing(10)
        self._cards = {w: BoxerCard(flow, w) for w in ("A", "B")}
        for c in self._cards.values():
            c.changed.connect(self._recompute)
            boxers.addWidget(c, 1)
        right.addLayout(boxers)
        self.swap_btn = QPushButton("⇄  Swap the headbands between Boxer A and Boxer B")
        self.swap_btn.setMinimumHeight(42)
        self.swap_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.swap_btn.setStyleSheet(f"QPushButton {{ border: 1px dashed {dt.border_strong()}; "
                                    f"background: {dt.surface()}; color: {dt.accent_hover()}; "
                                    f"border-radius: 10px; font-family: {dt.FONT_UI}; "
                                    f"font-size: {dt.SIZE_MD}px; font-weight: 600; }}")
        self.swap_btn.clicked.connect(flow.swap_headbands)
        right.addWidget(self.swap_btn)
        plan = ui.Card(radius=16)
        pv = QVBoxLayout(plan); pv.setContentsMargins(16, 12, 16, 12); pv.setSpacing(8)
        pl = QHBoxLayout(); pl.setSpacing(22)
        pv.addLayout(pl)
        pl2 = QHBoxLayout(); pl2.setSpacing(22)
        pv.addLayout(pl2)
        self._st_rounds = Stepper("Rounds", 3, 1, 12, 1, str)
        self._st_round = Stepper("Round", 180, 30, 600, 30, mmss)
        self._st_break = Stepper("Break", 60, 0, 300, 15, mmss)
        self._st_rest = Stepper("Baseline", 60, 0, 300, 15, mmss)
        for st in (self._st_rounds, self._st_round, self._st_break, self._st_rest):
            pl.addWidget(st); st.changed.connect(self._recompute)
        self._bell = QCheckBox("Ring the bell at the start and end of each round")
        self._bell.setStyleSheet(dt.qss_checkbox())
        pl.addStretch()
        pl2.addWidget(self._bell)
        self.total_line = ui.muted("", wrap=False)
        pl2.addWidget(self.total_line, 1)
        right.addWidget(plan)
        right.addStretch()
        # The right column scrolls on a small window, so nothing in it (the
        # poor-signal help, the EEG) is ever squeezed or cut off.
        right_host = QWidget(); right_host.setObjectName("readyRight")
        right_host.setStyleSheet("#readyRight { background: transparent; }")
        right.setContentsMargins(0, 0, 0, 0)
        right_host.setLayout(right)
        self._right_scroll = QScrollArea(); self._right_scroll.setWidgetResizable(True)
        self._right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._right_scroll.setObjectName("readyScroll")
        self._right_scroll.setStyleSheet("#readyScroll { background: transparent; }")
        self._right_scroll.viewport().setAutoFillBackground(False)
        self._right_scroll.setWidget(right_host)
        root.addWidget(self._right_scroll, 3)
        self._disk = ui.StatusLine("", "idle")       # the space line's state (tests)
        self._disk.setVisible(False)
        self._checks = []

    # ── devices ──
    def refresh_device_choices(self):
        """Fill every camera and headband dropdown from what is connected."""
        devs = self.flow.connected_cameras()
        recording = self.flow.is_recording()
        for slot in self.flow._cams:
            dd = self.cam_choice[slot.index]
            dd.blockSignals(True)
            dd.clear()
            for d in devs:
                dd.addItem(d.name, d.name)
            cur = slot.device.name if slot.device is not None else None
            dd.setCurrentIndex(dd.findData(cur) if cur else -1)
            if cur is None:
                dd.setPlaceholderText("No camera")
            dd.setEnabled(bool(devs) and not recording)
            dd.blockSignals(False)
        for c in self._cards.values():
            c.refresh_choices()

    def _camera_chosen(self, slot_index):
        dd = self.cam_choice[slot_index]
        name = dd.currentData()
        if name:
            self.flow.assign_camera(slot_index, name)

    # ── values ──
    def card(self, which) -> BoxerCard:
        return self._cards[which]

    @property
    def _name_a(self):            # (kept for older callers: the code fields)
        return self._cards["A"].code

    @property
    def _name_b(self):
        return self._cards["B"].code

    def config(self):
        from brain_battle.capture.session_controller import SessionConfig
        return SessionConfig(num_rounds=self._st_rounds.value(), round_sec=self._st_round.value(),
                             break_sec=self._st_break.value(), baseline_sec=self._st_rest.value())

    def names(self):
        return (fighters.normalise_code(self._cards["A"].code.text()),
                fighters.normalise_code(self._cards["B"].code.text()))

    def colours(self):
        return self._cards["A"].colour, self._cards["B"].colour

    def bell(self):
        return self._bell.isChecked()

    def set_plan(self, cfg, force: bool = True):
        self._st_rounds.set_value(cfg.num_rounds, force); self._st_round.set_value(cfg.round_sec, force)
        self._st_break.set_value(cfg.break_sec, force); self._st_rest.set_value(cfg.baseline_sec, force)
        self._recompute()

    def set_fighters(self, a, b, ca="Red", cb="Blue"):
        self._cards["A"].set_code(a); self._cards["B"].set_code(b)
        self._cards["A"].colour, self._cards["B"].colour = ca, cb
        for c in self._cards.values():
            c.set_colours(c._colours)
        self._recompute()

    def populate_from_settings(self):
        s = self.flow._settings
        self.set_plan(s.session_config(), force=False)
        self._bell.setChecked(bool(s.round_bell))
        colours = fighters.load_colours(s.capture_dir_resolved())
        for w, c in self._cards.items():
            c.colour = fighters.DEFAULT_COLOUR[w]
            c.set_colours(colours)
            c.code.setText("")
            c._show_code_problem()
        self._recompute()

    # ── the checklist ──
    def checks(self) -> list:
        """[(ok, title, note, soft)] — soft items never block Start."""
        f = self.flow
        out = []
        for w in ("A", "B"):
            b = f._bands[w]
            dev = (b.device or {}).get("name", "")
            out.append((b.connected and not b.lost, f"Headband {w} connected",
                        dev if b.connected else ("Lost connection — press Reconnect" if b.lost
                                                 else f"Switch on headband {w} ({f._settings.headband_for(w) or 'not set'})"),
                        False))
        poor = [(w, poor_text(self._cards[w].statuses())) for w in ("A", "B")]
        poor = [(w, t) for w, t in poor if t]
        conn = all(f._bands[w].connected for w in ("A", "B"))
        out.append((conn and not poor, "Good signal on all sensors",
                    "All 8 sensors look good" if conn and not poor else
                    (f"Boxer {poor[0][0]}: {poor[0][1][0].lower()}{poor[0][1][1:]}" if poor else
                     "Waiting for both headbands"), True))
        live = [s for s in f._cams if s.is_live]
        fps = int(round(max([s.actual_fps for s in live] or [f._settings.video_dims()[2]])))
        missing = [s.title for s in f._cams if not s.is_live]
        note = (f"All 4 at {fps} fps" if not missing else
                (f"{missing[0]} not found — check the USB cable" if f._cams and any(
                    s.device is None or s.state == "error" for s in f._cams) else "Connecting…"))
        fps_notes = [s.fps_note for s in f._cams if s.fps_note]
        if not missing and fps_notes:
            note = fps_notes[0]
        out.append((len(live) == len(f._cams) == 4, "4 cameras connected", note, False))
        a, b = self.names()
        ca, cb = self.colours()
        prob = fighters.codes_problem(a, b) or fighters.colours_problem(ca, cb)
        out.append((prob is None, "Fighter codes and colours",
                    prob or f"{a} ({ca.lower()}) vs {b} ({cb.lower()})", False))
        cal = f.calibration
        out.append((bool(cal), "Timing checked",
                    timing.summary_text(cal).replace("Largest difference between devices after "
                                                     "correction:", "Buzzer test done ·")
                    if cal else "Recommended: press Test timing and run the buzzer test", True))
        disk_ok, disk_note = f.disk_check(self.config())
        out.append((disk_ok, "Enough space", disk_note, False))
        return out

    def poor_signal_text(self) -> str:
        """'Signal looks poor on K7Q2 right ear (TP10). Start anyway?' — or ''
        when the signal check is green (or a headband is not connected yet)."""
        a, b = self.names()
        parts = []
        for w, code in (("A", a), ("B", b)):
            if not self.flow._bands[w].connected:
                continue
            for sensor in poor_sensors(self._cards[w].statuses()):
                parts.append(f"{code or 'Boxer ' + w} {sensor}")
        if not parts:
            return ""
        return f"Signal looks poor on {', '.join(parts)}. Start anyway?"

    def start_pressed(self) -> None:
        """Start recording — after asking, when the signal check is not green
        (it is our heuristic, so it never blocks)."""
        text = self.poor_signal_text()
        if text and not ask_start_anyway(self, text):
            return
        self.flow.begin_recording()

    def problems(self) -> int:
        return sum(1 for ok, _t, _n, soft in self._checks if not ok and not soft)

    def _recompute(self, *_):
        self._checks = self.checks()
        ui.clear_layout(self._checks_box)
        for ok, title, note, soft in self._checks:
            row = QFrame(); row.setObjectName("checkRow")
            row.setStyleSheet(f"#checkRow {{ border-radius: 10px; background: "
                              f"{'transparent' if ok else dt.warning_bg()}; }}")
            h = QHBoxLayout(row); h.setContentsMargins(8, 6, 8, 6); h.setSpacing(10)
            ic = QLabel("✓" if ok else "!"); ic.setFixedSize(22, 22)
            ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ic.setObjectName("ci")
            ic.setStyleSheet(f"#ci {{ border-radius: 11px; color: #FFFFFF; font-weight: 700; "
                             f"background: {dt.success_color() if ok else dt.warning_color()}; }}")
            h.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
            txt = QLabel(f"<b style='font-weight:600'>{title}</b><br><span style='color:"
                         f"{dt.text_tertiary() if ok else dt.warning_color()};font-size:13px'>{note}</span>")
            txt.setTextFormat(Qt.TextFormat.RichText); txt.setWordWrap(True)
            h.addWidget(txt, 1)
            self._checks_box.addWidget(row)
        bad = self.problems()
        self._start.setEnabled(bad == 0)
        self.start_note.setText(f"Fix {bad} thing{'s' if bad != 1 else ''} above to start" if bad
                                else f"Recording starts {LEAD_IN_DEFAULT} s before the baseline")
        self.start_note.setStyleSheet(dt.css_text(dt.SIZE_SM, 400,
                                                  dt.warning_color() if bad else dt.success_color()))
        self._disk.set("", "ok" if self._checks[-1][0] else "bad")
        live = sum(1 for s in self.flow._cams if s.is_live)
        self.cam_count.setText(f"{live} of 4 connected")
        self.cam_count.setStyleSheet(dt.css_text(dt.SIZE_MD, 500,
                                                 dt.success_color() if live == 4 else dt.warning_color()))
        cfg = self.config()
        from brain_battle.capture.session_controller import total_seconds
        tot = total_seconds(cfg, lead_in=False)
        self.total_line.setText(f"0:{LEAD_IN_DEFAULT:02d} lead-in + {mmss(tot)} = "
                                f"{mmss(tot + LEAD_IN_DEFAULT)} in total · remembered for next time")

    def on_tick(self):
        for t in self.tiles:
            t.update()
        for c in self._cards.values():
            c.refresh()
        self._tick_n = getattr(self, "_tick_n", 0) + 1
        if self._tick_n % 8 == 0:              # the checklist ~twice a second
            self._recompute()


# ── Recording ────────────────────────────────────────────────────────────────

class LiveBoxerCard(QFrame):
    """Recording: one boxer — head sensors, badge, code, signal, battery and the
    live EEG (5 s, grey automatic markers, red moments)."""

    def __init__(self, flow, which, parent=None):
        super().__init__(parent)
        self.flow = flow; self.which = which; self.slot = flow._bands[which]
        self.setObjectName(f"liveBoxer{which}")
        v = QVBoxLayout(self); v.setContentsMargins(16, 14, 16, 10); v.setSpacing(8)
        h = QHBoxLayout(); h.setSpacing(12)
        self.head = HeadIcon(38); h.addWidget(self.head)
        self._badge = QHBoxLayout(); h.addLayout(self._badge)
        tv = QVBoxLayout(); tv.setSpacing(0)
        self.name = QLabel(""); self.name.setTextFormat(Qt.TextFormat.RichText)
        self.signal = ui.WrapLabel("")
        tv.addWidget(self.name); tv.addWidget(self.signal)
        h.addLayout(tv, 1)
        self.battery = ui.muted("", size=dt.SIZE_SM); h.addWidget(self.battery)
        self.reconnect = ui.secondary_button("Reconnect", compact=True)
        self.reconnect.clicked.connect(lambda: flow.reconnect_band(which))
        self.reconnect.setVisible(False)
        h.addWidget(self.reconnect)
        v.addLayout(h)
        self.eeg = EEGViewer(channels=EEG_CHANNELS, live=True, min_row_px=24)
        self.eeg.set_timebase(5); self.eeg.set_scale(100)
        v.addWidget(self.eeg, 1)

    def setup(self):
        code = self.flow.boxer_name(self.which)
        hx = self.flow.fighter_colour_hex(self.which)
        ui.clear_layout(self._badge)
        self._badge.addWidget(ui.fighter_badge(self.which, hx, 24))
        self.name.setText(ui.fighter_html(code or f"Boxer {self.which}", hx, 15))

    def refresh(self):
        s = self.slot
        st = s.contact()[1] if s.connected else {}
        self.head.set_states(st)
        poor = poor_text(st)
        if s.lost:
            self.signal.setText("Lost connection — recording continues; press Reconnect")
            col, w = dt.error_color(), 600
        elif poor:
            self.signal.setText(f"{poor.replace('The ', '', 1)[0].upper()}{poor.replace('The ', '', 1)[1:]}"
                                f" — check the band in the next break")
            col, w = dt.warning_color(), 500
        else:
            self.signal.setText("Signal looks good on all 4 sensors")
            col, w = dt.success_color(), 400
        self.signal.setStyleSheet(dt.css_text(dt.SIZE_SM, w, col))
        self.reconnect.setVisible(bool(s.lost))
        self.battery.setText(f"Battery {s.battery}%" if s.battery is not None else "")
        self.setStyleSheet(f"#liveBoxer{self.which} {{ background: {dt.surface()}; border-radius: 16px; "
                           f"border: {'1.5px solid ' + dt.warning_border() if poor or s.lost else '1px solid ' + dt.border()}; }}")
        ts, chans = s.trace() if s.connected else (None, None)
        self.eeg.set_time_origin(self.flow.session_origin_unix())
        self.eeg.set_markers(self.flow.live_markers())
        self.eeg.set_data(ts, chans or {})


class RecordingScreen(QWidget):
    """Step 2. Built to be read from across a gym."""

    def __init__(self, flow, parent=None):
        super().__init__(parent)
        self.flow = flow
        root = QHBoxLayout(self); root.setContentsMargins(24, 18, 24, 24); root.setSpacing(24)
        left = QVBoxLayout(); left.setSpacing(12)
        self._write_banner = ui.Banner("bad")
        self._write_banner.setVisible(False)
        left.addWidget(self._write_banner)
        self._health = ui.Banner("warn")
        self._health.setVisible(False)
        left.addWidget(self._health)
        grid = QGridLayout(); grid.setSpacing(10)
        self.tiles = []
        for i, s in enumerate(flow._cams):
            tile = CameraTile(s, aspect=16 / 9)
            self.tiles.append(tile)
            grid.addWidget(tile, i // 2, i % 2)
        left.addLayout(grid, 3)
        big = ui.Card(radius=16)
        bv = QHBoxLayout(big); bv.setContentsMargins(26, 20, 26, 20); bv.setSpacing(32)
        cd = QHBoxLayout(); cd.setSpacing(10)
        self._count = QLabel("0:00")
        self._count.setStyleSheet(dt.css_text(56, 600, None, f"font-family: {dt.FONT_MONO};"))
        self._left_lbl = QLabel(""); self._left_lbl.setStyleSheet(dt.css_text(17, 400, dt.text_tertiary()))
        cd.addWidget(self._count); cd.addWidget(self._left_lbl, 0, Qt.AlignmentFlag.AlignBottom)
        bv.addLayout(cd)
        pv = QVBoxLayout(); pv.setSpacing(6)
        ph = QHBoxLayout()
        self._round_lbl = QLabel(""); self._round_lbl.setStyleSheet(dt.css_text(15, 700, dt.accent_hover()))
        self._next_lbl = ui.muted("", size=15, wrap=False)
        ph.addWidget(self._round_lbl); ph.addStretch(); ph.addWidget(self._next_lbl)
        pv.addLayout(ph)
        self._bar = PhaseBar(34)
        pv.addWidget(self._bar)
        bv.addLayout(pv, 1)
        left.addWidget(big)
        mrow = QHBoxLayout(); mrow.setSpacing(14)
        self.mark_btn = ui.primary_button("⚑  Mark this moment   M")
        self.mark_btn.setMinimumHeight(52)
        self.mark_btn.clicked.connect(flow.mark_moment)
        mrow.addWidget(self.mark_btn)
        self._moment_lbl = ui.muted("Press M when something happens. You can add a note straight "
                                    "away or later.", size=dt.SIZE_MD)
        mrow.addWidget(self._moment_lbl, 1)
        left.addLayout(mrow)
        root.addLayout(left, 3)
        right = QVBoxLayout(); right.setSpacing(14)
        self.boxers = {w: LiveBoxerCard(flow, w) for w in ("A", "B")}
        for c in self.boxers.values():
            right.addWidget(c, 1)
        self.timing_btn = QPushButton("")
        self.timing_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.timing_btn.setMinimumHeight(42)
        self.timing_btn.clicked.connect(flow.open_timing_dialog)
        right.addWidget(self.timing_btn)
        cap = QLabel("Scale 100 µV · 256 Hz · display filter 1–45 Hz · grey labels are automatic markers")
        cap.setWordWrap(True)
        cap.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.text_tertiary(), f"font-family: {dt.FONT_MONO};"))
        right.addWidget(cap)
        root.addLayout(right, 2)
        self._confirm = None

    def show_write_problem(self, text: str):
        self._write_banner.set_text(text)
        self._write_banner.setVisible(bool(text))

    def reset_for_recording(self):
        self.show_write_problem("")
        for c in self.boxers.values():
            c.setup()
        from brain_battle.capture.session_controller import _build_timeline
        self._bar.set_segments(plan_segments(_build_timeline(self.flow._cfg)))
        self._bar.set_now(0.0)
        self._moment_lbl.setText("Press M when something happens. You can add a note straight away "
                                 "or later.")

    def set_phase(self, phase_id, label, round_num, total_rounds, duration):
        if phase_id == "round":
            self._round_lbl.setText(f"Round {round_num} of {total_rounds}")
            self._left_lbl.setText(f"left in round {round_num}")
        elif phase_id == "lead_in":
            self._round_lbl.setText("Lead-in (recording already running)")
            self._left_lbl.setText("until the baseline starts")
        elif phase_id == "baseline":
            self._round_lbl.setText("Baseline")
            self._left_lbl.setText("left in the baseline")
        else:
            self._round_lbl.setText("Break")
            self._left_lbl.setText("left in the break")
        self._next_lbl.setText(self.flow.next_phase_text())

    def set_time(self, remaining, total, elapsed_session):
        self._count.setText(mmss(remaining))
        self._bar.set_now(elapsed_session)

    def show_moment(self, t_session, count, noted):
        self._moment_lbl.setText(f"{count} moment{'s' if count != 1 else ''} marked · "
                                 f"{noted} with notes.")

    def on_tick(self):
        el = self.flow._elapsed_session()
        for t in self.tiles:
            t.timecode = mmss_cc(el) if t.slot.is_recording else ""
            t.update()
        for c in self.boxers.values():
            c.refresh()
        self.flow.header.rec_pill.setText(f"●  REC {mmss(el)}")
        ok = self.flow.health_ok()
        txt = self.flow.health_text()
        self._health.set_text(txt.lstrip("! "))
        self._health.setVisible(not ok)
        a, b = self.flow.boxer_name("A"), self.flow.boxer_name("B")
        self.flow.header.mid.setText(
            ui.versus_html(a or "Boxer A", self.flow.fighter_colour_hex("A"),
                           b or "Boxer B", self.flow.fighter_colour_hex("B"), 14)
            + (f" <span style='color:{dt.success_color()}'>· everything is recording</span>" if ok else
               f" <span style='color:{dt.warning_color()}'>· check the warning</span>"))
        line = self.flow.timing_line()
        good = "✓" in line
        self.timing_btn.setText("⏱  " + line)
        self.timing_btn.setStyleSheet(
            dt.qss_secondary_button() + f" QPushButton {{ text-align: left; padding-left: 14px; "
            f"color: {dt.success_color() if good else dt.warning_color()}; }}")


# ── Saved ────────────────────────────────────────────────────────────────────

class SavedScreen(QWidget):
    HEAD_OK = "Recording saved"
    HEAD_EARLY = "Recording saved (stopped early)"
    HEAD_PROBLEM = "Recording saved, but with problems"
    _XDF_OK = "Analysis file made (recording.xdf): everything on one clock."
    _XDF_FAIL = "The analysis file (recording.xdf) could not be made — it is rebuilt when you export."

    def __init__(self, flow, parent=None):
        super().__init__(parent)
        self.flow = flow
        outer = QHBoxLayout(self); outer.setContentsMargins(24, 48, 24, 24)
        col = QWidget(); col.setMaximumWidth(800)
        outer.addStretch(1); outer.addWidget(col, 100); outer.addStretch(1)
        v = QVBoxLayout(col); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(22)
        top = QHBoxLayout(); top.setSpacing(18)
        self._tick = QLabel("✓"); self._tick.setFixedSize(64, 64)
        self._tick.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tick.setObjectName("savedTick")
        top.addWidget(self._tick)
        tv = QVBoxLayout(); tv.setSpacing(4)
        self._head = QLabel(self.HEAD_OK); self._head.setStyleSheet(dt.css_text(28, 700))
        self._meta = QLabel(""); self._meta.setTextFormat(Qt.TextFormat.RichText)
        tv.addWidget(self._head); tv.addWidget(self._meta)
        top.addLayout(tv, 1)
        v.addLayout(top)
        self._card = ui.Card(radius=16)
        self._items = QVBoxLayout(self._card); self._items.setContentsMargins(0, 0, 0, 0)
        self._items.setSpacing(0)
        v.addWidget(self._card)
        self._xdf_row = None
        btns = QHBoxLayout(); btns.setSpacing(10)
        self.label_btn = ui.primary_button("Start labelling →"); self.label_btn.setMinimumHeight(52)
        self.label_btn.clicked.connect(flow.start_labelling)
        self.back_btn = ui.secondary_button("Back to sessions"); self.back_btn.setMinimumHeight(52)
        self.back_btn.clicked.connect(flow._leave_to_sessions)
        self.again_btn = ui.secondary_button("Record another"); self.again_btn.setMinimumHeight(52)
        self.again_btn.clicked.connect(flow.record_another)
        btns.addWidget(self.label_btn); btns.addWidget(self.back_btn); btns.addWidget(self.again_btn)
        btns.addStretch()
        v.addLayout(btns)
        v.addStretch()

    def _row(self, ok: bool, title: str, detail: str):
        r = QFrame(); r.setObjectName("savedRow")
        r.setStyleSheet("#savedRow { border-bottom: 1px solid #EEF1F6; }")
        h = QHBoxLayout(r); h.setContentsMargins(22, 14, 22, 14); h.setSpacing(12)
        ic = QLabel("✓" if ok else "!"); ic.setFixedSize(28, 28)
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter); ic.setObjectName("ci")
        ic.setStyleSheet(f"#ci {{ border-radius: 14px; color: #FFFFFF; font-weight: 700; "
                         f"background: {dt.success_color() if ok else dt.warning_color()}; }}")
        h.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        t = QLabel(f"<b style='font-weight:600'>{title}</b><br><span style='color:"
                   f"{dt.text_tertiary()};font-size:13px'>{detail}</span>")
        t.setTextFormat(Qt.TextFormat.RichText); t.setWordWrap(True)
        h.addWidget(t, 1)
        r.ok, r.title, r.detail = ok, title, detail
        self._items.addWidget(r)
        return r

    def populate(self, info: dict):
        clean = not info.get("problems")
        head = self.HEAD_OK if clean else self.HEAD_PROBLEM
        if clean and info.get("stopped_early"):
            head = self.HEAD_EARLY
        self._head.setText(head)
        good = clean
        self._tick.setText("✓" if good else "!")
        self._tick.setStyleSheet(f"#savedTick {{ border-radius: 32px; font-size: 30px; font-weight: 700; "
                                 f"background: {dt.success_bg() if good else dt.warning_bg()}; "
                                 f"color: {dt.success_color() if good else dt.warning_color()}; }}")
        self._meta.setText(info["meta"])
        ui.clear_layout(self._items)
        for ok, title, detail in info["items"]:
            self._row(ok, title, detail)
        self._xdf_row = self._row(True, "Analysis file", "Making recording.xdf…")
        folder = QFrame(); folder.setObjectName("folderRow")
        folder.setStyleSheet(f"#folderRow {{ background: #F9FAFC; }}")
        fh = QHBoxLayout(folder); fh.setContentsMargins(22, 14, 22, 14)
        fl = QLabel(info["folder_line"]); fl.setTextFormat(Qt.TextFormat.RichText); fl.setWordWrap(True)
        fh.addWidget(fl, 1)
        ob = ui.secondary_button("Open folder"); ob.clicked.connect(self.flow.reveal_saved)
        fh.addWidget(ob)
        self._items.addWidget(folder)

    def item_titles(self) -> list:
        out = []
        for i in range(self._items.count()):
            w = self._items.itemAt(i).widget()
            if w is not None and hasattr(w, "title"):
                out.append(w.title)
        return out

    def item_details(self) -> list:
        out = []
        for i in range(self._items.count()):
            w = self._items.itemAt(i).widget()
            if w is not None and hasattr(w, "detail"):
                out.append((w.title, w.detail, w.ok))
        return out

    def set_xdf_state(self, ok: bool):
        if self._xdf_row is None:
            return
        idx = self._items.indexOf(self._xdf_row)
        self._xdf_row.setParent(None); self._xdf_row.deleteLater()
        self._xdf_row = self._row(ok, "Analysis file", self._XDF_OK if ok else self._XDF_FAIL)
        self._items.removeWidget(self._xdf_row)
        self._items.insertWidget(idx, self._xdf_row)

    def add_worth_checking(self, text: str):
        idx = self._items.count() - 1                 # above the folder row
        r = self._row(False, "Worth checking", text)
        self._items.removeWidget(r); self._items.insertWidget(idx, r)


# ── dialogs ──────────────────────────────────────────────────────────────────

class MomentDialog(ui.AppDialog):
    """⚑ Moment marked at m:ss — the note can wait; recording never pauses.
    The app's dialog on a light cover, so the recording stays visible."""
    saved = pyqtSignal(int, str)        # moment index, note
    later = pyqtSignal(int)

    def __init__(self, index: int, t_session: float, parent=None):
        super().__init__(parent, width=540, dim=False)
        self.index = index
        self.setWindowTitle("Moment marked")
        self.setModal(False)
        v = self.body
        top = QHBoxLayout()
        t = QLabel(f"⚑ Moment marked at {mmss(t_session)}"); t.setStyleSheet(dt.css_text(20, 700))
        top.addWidget(t); top.addStretch()
        rec = QLabel("● still recording"); rec.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt.error_color()))
        top.addWidget(rec)
        v.addLayout(top)
        lab = QLabel("What did you see? (optional)")
        lab.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt.text_secondary()))
        v.addWidget(lab)
        self.note = QTextEdit(); self.note.setPlaceholderText("e.g. B dropped the guard after a combination")
        self.note.setAcceptRichText(False); self.note.setFixedHeight(90)
        self.note.setStyleSheet(dt.qss_input())
        self.note.textChanged.connect(self._update)
        self.note.installEventFilter(self)
        v.addWidget(self.note)
        b = QHBoxLayout(); b.addStretch()
        self.later_btn = ui.secondary_button("Add note later")
        self.later_btn.clicked.connect(self.add_later)
        self.save_btn = ui.primary_button("Save note")
        self.save_btn.clicked.connect(self.save_note)
        b.addWidget(self.later_btn); b.addWidget(self.save_btn)
        v.addLayout(b)
        v.addWidget(ui.muted("Enter saves · Esc means later. The recording keeps going while this "
                             "is open."))
        self._done = False
        self._update()

    def _update(self):
        self.save_btn.setEnabled(bool(self.note.toPlainText().strip()))

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj is self.note and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                    not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                if self.note.toPlainText().strip():
                    self.save_note()
                else:
                    self.add_later()
                return True
            if event.key() == Qt.Key.Key_Escape:
                self.add_later(); return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.add_later(); return
        super().keyPressEvent(e)

    def save_note(self):
        text = self.note.toPlainText().strip()
        if not text or self._done:
            return
        self._done = True
        self.saved.emit(self.index, text)
        self.accept()

    def add_later(self):
        if self._done:
            return
        self._done = True
        self.later.emit(self.index)
        self.reject()

    def closeEvent(self, e):
        self.add_later()
        super().closeEvent(e)


class TimingDialog(ui.AppDialog):
    """⏱ Timing check: live numbers for every device; the buzzer test."""
    COLUMNS = ["Device", "Rate", "Delay (raw)", "Jitter", "Dropped", "After correction"]

    def __init__(self, flow, recording: bool, parent=None):
        super().__init__(parent, width=820, dim=not recording)
        self.flow = flow
        self.recording = recording
        self.setWindowTitle("Timing check")
        self.setModal(not recording)
        self.card.setMinimumWidth(680)
        self.set_cancel_result(1)
        v = self.body
        top = QHBoxLayout()
        t = QLabel("⏱ Timing check"); t.setStyleSheet(dt.css_text(20, 700)); top.addWidget(t)
        top.addStretch()
        live = QLabel("● still recording" if recording else "live")
        live.setStyleSheet(dt.css_text(dt.SIZE_SM, 600 if recording else 400,
                                       dt.error_color() if recording else dt.text_tertiary()))
        top.addWidget(live)
        v.addLayout(top)
        v.addWidget(ui.muted("Live numbers for every device. Delay is how late a device delivers "
                             "what happened. The buzzer test measures it so the app can subtract "
                             "it. Goal: under 10 ms between all devices after correction."))
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setMinimumHeight(240)
        v.addWidget(self.table)
        self.summary = QLabel(""); self.summary.setWordWrap(True)
        self.summary.setObjectName("timingSummary")
        v.addWidget(self.summary)
        b = QHBoxLayout(); b.addStretch()
        self.buzzer_btn = ui.secondary_button("Run buzzer test")
        self.buzzer_btn.clicked.connect(self.run_buzzer)
        self.buzzer_btn.setVisible(not recording)
        done = ui.primary_button("Done"); done.clicked.connect(self.accept)
        b.addWidget(self.buzzer_btn); b.addWidget(done)
        v.addLayout(b)
        v.addWidget(ui.muted("Logged every second during recording and included in the export as "
                             "timing_quality.csv, so we can see where to improve."))
        self._running = False
        self._beeps = 0
        self._timer = QTimer(self); self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def run_buzzer(self):
        if self._running:
            return
        self._running = True
        self._beeps = 0
        self.buzzer_btn.setEnabled(False)
        self.buzzer_btn.setText("Listening for the taps…")
        self.flow.run_buzzer_test(on_done=self._on_done, on_beep=self._on_beep)
        self.refresh()

    def _on_beep(self, n):
        self._beeps = n
        self.refresh()

    def _on_done(self, cal):
        self._running = False
        self.buzzer_btn.setEnabled(True)
        self.buzzer_btn.setText("Run buzzer test again")
        self.refresh()

    def refresh(self):
        rows = self.flow.timing_rows()
        self.table.setRowCount(len(rows))

        def ms(x):
            return "—" if x is None else f"{x:.0f} ms"
        for i, r in enumerate(rows):
            after = r.get("after_ms")
            vals = [r["name"], r["rate"], ms(r.get("delay_ms")),
                    "—" if r.get("jitter_ms") is None else f"±{r['jitter_ms']:.0f} ms",
                    r["dropped"], "not measured" if after is None else f"{after:.1f} ms"]
            for j, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if j == 5 and after is not None:
                    it.setForeground(QColor(dt.success_color() if after < timing.GOAL_MS else dt.warning_color()))
                self.table.setItem(i, j, it)
        cal = self.flow.calibration
        if self._running:
            text = (f"Beep {self._beeps} of {timing.N_BEEPS}… both boxers tap their headband at "
                    f"every beep, in view of the cameras…")
            bg, fg = dt.canvas_bg(), dt.text_secondary()
        elif cal:
            text = timing.summary_text(cal)
            worst = cal.get("largest_difference_ms")
            good = worst is not None and worst < timing.GOAL_MS and cal.get("complete")
            text = ("✓ " if good else "") + text
            bg = dt.success_bg() if good else dt.warning_bg()
            fg = dt.success_color() if good else dt.warning_color()
        else:
            text = ("Not measured yet. Press Run buzzer test: at each of the 5 beeps, both boxers tap "
                    "their headband once, in view of the cameras. Rate, jitter and dropped are live "
                    "already." if not self.recording else
                    "Not measured this session — the buzzer test runs on Get ready, before recording.")
            bg, fg = dt.canvas_bg(), dt.text_secondary()
        self.summary.setText(text)
        self.summary.setStyleSheet(f"#timingSummary {{ background: {bg}; color: {fg}; border-radius: 12px; "
                                   f"padding: 10px 12px; font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; }}")

    def done(self, r):
        self._timer.stop()
        super().done(r)


__all__ = ["FlowHeader", "CameraTile", "HeadIcon", "Stepper", "PhaseBar", "BoxerCard",
           "GetReadyScreen", "LiveBoxerCard", "RecordingScreen", "SavedScreen",
           "MomentDialog", "TimingDialog", "poor_text", "plan_segments", "mmss", "mmss_cc"]
