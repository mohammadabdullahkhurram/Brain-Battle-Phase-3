"""
circular_timer.py
─────────────────
Apple-timer-style circular dial used during an active capture session.

Visual layout (dark themed, Brain-Battle yellow accent):

                    ╭─── phase label ───╮
                    │     ROUND 1       │
                    │                   │
                    │      01:23        │  ← mm:ss countdown
                    │                   │
                    │   round 1 of 3    │
                    ╰─── arc ring ──────╯

The remaining-time arc sweeps counter-clockwise from 12 o'clock as the phase
ticks down. Three color states communicate phase at a glance:
  • BASELINE   → neutral cyan
  • ROUND N    → yellow-green (brand color #637DFF)
  • BREAK N    → muted orange
  • IDLE/DONE  → grey

Public API:
    set_phase(phase_id, label, round_num, total_rounds)
    set_time(remaining_sec, total_sec)
    reset()
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QRectF, QPointF, QSize
from PyQt6.QtGui  import QPainter, QPen, QColor, QFont, QBrush
from PyQt6.QtWidgets import QWidget, QSizePolicy


# Phase → (ring color, subtitle template)
_PHASE_COLORS = {
    "idle":     QColor("#2A2A30"),
    "baseline": QColor("#38BDF8"),   # sky — BASELINE
    "round":    QColor("#637DFF"),   # azure — ROUND (primary brand accent)
    "break":    QColor("#F59E0B"),   # amber — BREAK
    "done":     QColor("#10B981"),   # emerald — DONE
}

_BG_TRACK = QColor("#24242A")
_TEXT_PRIMARY  = QColor("#F5F7FA")
_TEXT_SECONDARY = QColor("#B4BCCC")
_TEXT_TERTIARY = QColor("#6B7489")


class CircularTimer(QWidget):
    """Apple-style countdown dial driven by the SessionController."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(220, 220)

        self._phase_id:     str = "idle"
        self._label:        str = "READY"
        self._round_num:    int = 0
        self._total_rounds: int = 0
        self._remaining:    int = 0
        self._total:        int = 0

    # ----------------------------------------------------------------- API
    def set_phase(self, phase_id: str, label: str,
                  round_num: int = 0, total_rounds: int = 0) -> None:
        self._phase_id     = phase_id
        self._label        = label.upper()
        self._round_num    = round_num
        self._total_rounds = total_rounds
        self.update()

    def set_time(self, remaining_sec: int, total_sec: int) -> None:
        self._remaining = max(0, int(remaining_sec))
        self._total     = max(1, int(total_sec))
        self.update()

    def reset(self) -> None:
        self._phase_id, self._label = "idle", "READY"
        self._round_num = self._total_rounds = 0
        self._remaining = self._total = 0
        self.update()

    # ------------------------------------------------------------ sizing
    def sizeHint(self) -> QSize:
        return QSize(320, 320)

    def heightForWidth(self, w: int) -> int:
        return w  # keep it square-ish

    # ----------------------------------------------------------- painting
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Square inscribed inside the widget, centred.
        side = min(self.width(), self.height())
        margin = max(10, int(side * 0.06))
        rect = QRectF(
            (self.width()  - side) / 2 + margin,
            (self.height() - side) / 2 + margin,
            side - 2 * margin,
            side - 2 * margin,
        )

        # Track (full background ring)
        ring_w = max(6, int(side * 0.04))
        track_pen = QPen(_BG_TRACK, ring_w, Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.FlatCap)
        p.setPen(track_pen)
        p.drawArc(rect, 0, 360 * 16)

        # Active arc — sweep counter-clockwise from 12 o'clock as time ticks down.
        color = _PHASE_COLORS.get(self._phase_id, _PHASE_COLORS["idle"])
        if self._total > 0 and self._phase_id not in ("idle", "done"):
            frac = max(0.0, min(1.0, self._remaining / self._total))
            span_deg = 360.0 * frac
            arc_pen = QPen(color, ring_w, Qt.PenStyle.SolidLine,
                           Qt.PenCapStyle.RoundCap)
            p.setPen(arc_pen)
            # Qt: angle units = 1/16 of a degree, 0° at 3 o'clock, CCW positive.
            # We want start at 12 (=90°) and sweep CCW as remaining shrinks.
            start_angle = int(90 * 16)
            span_angle  = int(span_deg * 16)
            p.drawArc(rect, start_angle, span_angle)
        elif self._phase_id == "done":
            # Solid green ring when finished.
            done_pen = QPen(color, ring_w, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap)
            p.setPen(done_pen)
            p.drawArc(rect, 0, 360 * 16)

        # Central text stack
        cx = rect.center().x(); cy = rect.center().y()

        # Phase label (top)
        label_color = color if self._phase_id not in ("idle",) else _TEXT_TERTIARY
        p.setPen(QPen(label_color))
        label_font = QFont("JetBrains Mono"); label_font.setPointSize(max(9, int(side * 0.05)))
        label_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3)
        label_font.setBold(True)
        p.setFont(label_font)
        p.drawText(QRectF(rect.left(), cy - side * 0.22, rect.width(), side * 0.08),
                   Qt.AlignmentFlag.AlignCenter, self._label)

        # Big time (centre)
        time_font = QFont("JetBrains Mono"); time_font.setPointSize(max(22, int(side * 0.16)))
        time_font.setBold(True)
        p.setFont(time_font)
        p.setPen(QPen(_TEXT_PRIMARY))
        txt = self._format_time(self._remaining if self._total > 0 else 0)
        p.drawText(QRectF(rect.left(), cy - side * 0.10, rect.width(), side * 0.24),
                   Qt.AlignmentFlag.AlignCenter, txt)

        # Subtitle (below time) — round X of Y, or total time on idle/done.
        sub_font = QFont("JetBrains Mono"); sub_font.setPointSize(max(8, int(side * 0.035)))
        sub_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
        p.setFont(sub_font)
        p.setPen(QPen(_TEXT_SECONDARY))
        sub_txt = self._subtitle()
        p.drawText(QRectF(rect.left(), cy + side * 0.14, rect.width(), side * 0.08),
                   Qt.AlignmentFlag.AlignCenter, sub_txt)

        p.end()

    # -------------------------------------------------------- helpers
    @staticmethod
    def _format_time(sec: int) -> str:
        sec = max(0, int(sec))
        m, s = divmod(sec, 60)
        if m >= 60:
            h, m = divmod(m, 60)
            return f"{h:d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _subtitle(self) -> str:
        if self._phase_id == "idle":
            return "AWAITING SESSION"
        if self._phase_id == "done":
            return "SESSION COMPLETE"
        if self._phase_id == "baseline":
            return "CALIBRATION WINDOW"
        if self._phase_id == "round" and self._total_rounds:
            return f"ROUND {self._round_num} OF {self._total_rounds}"
        if self._phase_id == "break":
            return "REST & RESET"
        return ""


__all__ = ["CircularTimer"]
