"""
review_label_tab.py
───────────────────
The one merged Review & Label screen (DESIGN §6), replacing the old separate
Review (python-vlc) and Labelling (Qt Multimedia) tabs. Everything runs off a
single playhead on the t_session_seconds axis via pyav_player: video tiles, both
brain-activity traces, the timeline cursor, and label placement are the same
number by construction.

Public surface used by the app shell:
  • ReviewLabelTab(parent) — one widget for the "Review & Label" page.
  • .load_session_folder(root) — open a v3 recording.
  • .back_to_sessions (signal) — the ← Sessions button.
  • .shutdown() — stop the decode worker/timer before teardown.
"""
from __future__ import annotations

import csv
import datetime as _dt
import getpass
import os

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRectF, QEvent
from PyQt6.QtGui import QImage, QPainter, QColor, QPen, QBrush, QFont
from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QSizePolicy, QLineEdit, QButtonGroup,
)

import design_tokens as dt
import label_vocab
import labels_store
import contact_quality as cq
import session_history
from pyav_player import open_session_cameras, PlayerController, FRAME_PERIOD


# ── small helpers ──────────────────────────────────────────────────────────────

def _fmt_time(t: float) -> str:
    """mm:ss.xx (hundredths), DESIGN transport format."""
    t = max(0.0, float(t))
    m = int(t // 60); s = int(t % 60); cs = int(round((t - int(t)) * 100))
    if cs == 100:
        cs = 0; s += 1
        if s == 60:
            s = 0; m += 1
    return f"{m:02d}:{s:02d}.{cs:02d}"


def _fmt_mmss(t: float) -> str:
    t = max(0.0, float(t)); return f"{int(t // 60)}:{int(t % 60):02d}"


# camera filename token → DESIGN display name
_CAM_NAMES = {
    "SIDE_01": "Side camera 1", "SIDE_02": "Side camera 2",
    "POV_01":  "Boxer A view",  "POV_02":  "Boxer B view",
}


def _camera_name(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename))[0].replace("session_", "")
    return _CAM_NAMES.get(stem, stem.replace("_", " ").title())


def _discover_cameras(root: str) -> list[tuple[str, str]]:
    """(mp4_path, display_name) for all four slots, in DESIGN order."""
    cont = os.path.join(root, "continuous")
    found = {}
    for sub in ("Side Cams", "POV Cams"):
        d = os.path.join(cont, sub)
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.endswith(".mp4"):
                    found[_camera_name(fn)] = os.path.join(d, fn)
    order = ["Side camera 1", "Side camera 2", "Boxer A view", "Boxer B view"]
    specs = []
    for name in order:
        specs.append((found.get(name, ""), name))
    # include any unexpected extras
    for name, path in found.items():
        if name not in order:
            specs.append((path, name))
    return specs[:4] if len(specs) >= 4 else specs


def read_phase_markers(root: str) -> list[tuple]:
    """(t_session_seconds, phase_id, label) rows, time-sorted."""
    p = os.path.join(root, "phase_markers.csv")
    out = []
    if not os.path.isfile(p):
        return out
    try:
        with open(p, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    out.append((float(row["t_session_seconds"]),
                                row.get("phase_id", ""), row.get("label", "")))
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        pass
    out.sort(key=lambda r: r[0])
    return out


def friendly_phase(label: str) -> str:
    """Turn a marker token into DESIGN vocabulary."""
    import re
    u = (label or "").upper()
    if u.startswith("BASELINE"):
        return "Rest before round 1"
    if u.startswith("ROUND"):
        m = re.search(r"(\d+)", u)
        return f"Round {m.group(1)}" if m else "Round"
    if u.startswith("BREAK"):
        return "Break"
    if u.startswith("SESSION_END") or u == "DONE":
        return "Finished"
    return label or ""


# ── video ──────────────────────────────────────────────────────────────────────

class VideoTile(QFrame):
    enlarge_toggled = pyqtSignal(int)

    def __init__(self, index: int, name: str, missing: bool, parent=None):
        super().__init__(parent)
        self.index = index
        self.name = name
        self.missing = missing
        self._img: QImage | None = None
        self.setMinimumSize(160, 90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"background: #0E0F12; border-radius: {dt.RADIUS_MD}px;")
        self._enlarge = QPushButton("⤢", self)
        self._enlarge.setFixedSize(26, 26)
        self._enlarge.setCursor(Qt.CursorShape.PointingHandCursor)
        self._enlarge.setStyleSheet(
            "QPushButton { background: rgba(0,0,0,0.55); color: #FFFFFF; border: none; "
            f"border-radius: 6px; font-size: 14px; }}")
        self._enlarge.clicked.connect(lambda: self.enlarge_toggled.emit(self.index))

    def set_image(self, img: QImage):
        self._img = img
        self.update()

    def resizeEvent(self, e):
        self._enlarge.move(self.width() - self._enlarge.width() - 8, 8)
        super().resizeEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.fillRect(self.rect(), QColor("#0E0F12"))
        if self.missing:
            p.setPen(QColor("#9AA3AF"))
            p.setFont(QFont("Inter", 11))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "No video for this camera")
        elif self._img is not None and not self._img.isNull():
            iw, ih = self._img.width(), self._img.height()
            w, h = self.width(), self.height()
            scale = min(w / iw, h / ih)
            dw, dh = iw * scale, ih * scale
            x, y = (w - dw) / 2, (h - dh) / 2
            p.drawImage(QRectF(x, y, dw, dh), self._img)
        # name chip
        p.setFont(QFont("Inter", 10, QFont.Weight.DemiBold))
        tw = p.fontMetrics().horizontalAdvance(self.name) + 16
        p.setBrush(QColor(0, 0, 0, 150)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(8, 8, tw, 22, 6, 6)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(16, 23, self.name)
        p.end()


class VideoGrid(QWidget):
    def __init__(self, specs: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(8)
        self.tiles: list[VideoTile] = []
        for i, (path, name) in enumerate(specs):
            tile = VideoTile(i, name, missing=not (path and os.path.isfile(path)))
            tile.enlarge_toggled.connect(self._toggle_enlarge)
            self.tiles.append(tile)
        self._enlarged = None
        self._relayout()

    def _relayout(self):
        for t in self.tiles:
            self._grid.removeWidget(t); t.setVisible(False)
        if self._enlarged is None:
            for i, t in enumerate(self.tiles):
                self._grid.addWidget(t, i // 2, i % 2)
                t.setVisible(True)
        else:
            t = self.tiles[self._enlarged]
            self._grid.addWidget(t, 0, 0, 2, 2)
            t.setVisible(True)

    def _toggle_enlarge(self, index: int):
        self._enlarged = None if self._enlarged == index else index
        self._relayout()

    def set_frames(self, frames: dict):
        for i, img in frames.items():
            if 0 <= i < len(self.tiles):
                self.tiles[i].set_image(img)


# ── timeline ────────────────────────────────────────────────────────────────────

class TimelineStrip(QWidget):
    """Phases row (read-only) + three label lanes (Boxer A / Boxer B / Both) with
    point ticks and duration bars, plus a draggable playhead. Clicking a mark or
    anywhere seeks."""
    seek_requested = pyqtSignal(float)

    _PHASE_H = 26
    _LANE_H = 22
    _LANES = ["A", "B", "Both"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 1.0
        self.phases: list[tuple] = []       # (t, phase_id, label)
        self.labels: list[dict] = []
        self.playhead = 0.0
        self.setMinimumHeight(self._PHASE_H + len(self._LANES) * self._LANE_H + 26)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    def set_session(self, duration, phases, labels):
        self.duration = max(0.001, float(duration))
        self.phases = list(phases)
        self.labels = list(labels)
        self.update()

    def set_labels(self, labels):
        self.labels = list(labels); self.update()

    def set_playhead(self, t):
        self.playhead = float(t); self.update()

    def _x(self, t):
        return 2 + (self.width() - 4) * (t / self.duration)

    def _t_at(self, x):
        return max(0.0, min(self.duration, (x - 2) / max(1, self.width() - 4) * self.duration))

    def _lane_y(self, who):
        i = self._LANES.index(who) if who in self._LANES else 2
        return self._PHASE_H + 4 + i * self._LANE_H

    def mousePressEvent(self, e):
        # snap to a nearby mark, else seek to the clicked time
        t = self._t_at(e.position().x())
        best, bd = None, 8
        for lab in self.labels:
            lx = self._x(lab["t_start"])
            if abs(lx - e.position().x()) < bd:
                best, bd = lab["t_start"], abs(lx - e.position().x())
        self.seek_requested.emit(best if best is not None else t)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.seek_requested.emit(self._t_at(e.position().x()))

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        W = self.width()
        pr, pround, pbreak = dt.phase_colors()
        colmap = {"baseline": pr, "round": pround, "break": pbreak, "done": pr}
        # phases row
        if self.phases:
            bounds = list(self.phases) + [(self.duration, "", "")]
            for i in range(len(self.phases)):
                t0 = self.phases[i][0]; t1 = bounds[i + 1][0]
                pid = self.phases[i][1]
                bg, fg = colmap.get(pid, pr)
                x0, x1 = self._x(t0), self._x(t1)
                p.setBrush(QColor(bg)); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRectF(x0, 2, max(1, x1 - x0 - 1), self._PHASE_H - 4), 4, 4)
                name = friendly_phase(self.phases[i][2])
                if x1 - x0 > 46:
                    p.setPen(QColor(fg)); p.setFont(QFont("Inter", 9, QFont.Weight.DemiBold))
                    p.drawText(QRectF(x0 + 6, 2, x1 - x0 - 8, self._PHASE_H - 4),
                               Qt.AlignmentFlag.AlignVCenter, name)
        # lanes
        p.setFont(QFont("Inter", 8))
        for who in self._LANES:
            y = self._lane_y(who)
            p.setPen(QColor(dt.border()))
            p.drawLine(2, y + self._LANE_H - 4, W - 2, y + self._LANE_H - 4)
            p.setPen(QColor(dt.text_tertiary()))
            tag = who if who == "Both" else f"Boxer {who}"
            p.drawText(QPoint(4, y + 12), tag)
        for lab in self.labels:
            who = lab.get("who", "Both")
            y = self._lane_y(who)
            col = QColor(lab.get("color", "#3D4FD6"))
            x0 = self._x(lab["t_start"])
            if lab.get("t_end") is not None:
                x1 = self._x(lab["t_end"])
                p.setBrush(QColor(col.red(), col.green(), col.blue(), 150))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRectF(x0, y + 2, max(2, x1 - x0), self._LANE_H - 8), 3, 3)
            else:
                p.setPen(QPen(col, 2))
                p.drawLine(int(x0), y + 2, int(x0), y + self._LANE_H - 6)
        # playhead
        px = self._x(self.playhead)
        p.setPen(QPen(QColor(dt.accent()), 1.5))
        p.drawLine(int(px), 0, int(px), self.height())
        p.end()


# ── brain activity card ─────────────────────────────────────────────────────────

class BrainActivityCard(QFrame):
    """One [Waves | Band power] toggle over one row per boxer: badge + name,
    contact-quality line, and the playhead-centred trace (reuses the tested
    EEGReviewWidget with its jargon header hidden)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(f"QFrame#card {{ background: {dt.card_bg()}; "
                           f"border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_LG}px; }}")
        from eeg_module import EEGReviewWidget
        v = QVBoxLayout(self); v.setContentsMargins(12, 10, 12, 10); v.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("Brain activity")
        title.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
                            f"font-size: {dt.SIZE_MD}px; font-weight: 700;")
        head.addWidget(title); head.addStretch()
        self._waves_btn = QPushButton("Waves"); self._band_btn = QPushButton("Band power")
        for b in (self._waves_btn, self._band_btn):
            b.setCheckable(True); b.setMinimumHeight(28)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._waves_btn.clicked.connect(lambda: self._set_mode("waveform"))
        self._band_btn.clicked.connect(lambda: self._set_mode("bandpower"))
        head.addWidget(self._waves_btn); head.addWidget(self._band_btn)
        v.addLayout(head)

        self._rows = []
        self._widgets = []
        self._contact_lines = []
        for which, chan in (("A", 1), ("B", 2)):
            row = QVBoxLayout(); row.setSpacing(2)
            hdr = QHBoxLayout(); hdr.setSpacing(8)
            self._name = QLabel()
            badge = QLabel(which)
            line, soft, text = dt.boxer_colors(which)
            badge.setFixedSize(20, 20); badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(f"background: {soft}; color: {text}; border: 1px solid {line}; "
                                f"border-radius: 10px; font-weight: 700; font-size: 11px;")
            name = QLabel(f"Boxer {which}")
            name.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
                               f"font-size: {dt.SIZE_SM}px; font-weight: 600;")
            contact = QLabel("")
            contact.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                                  f"font-size: {dt.SIZE_XS}px;")
            hdr.addWidget(badge); hdr.addWidget(name); hdr.addSpacing(8)
            hdr.addWidget(contact); hdr.addStretch()
            w = EEGReviewWidget(channel_id=chan)
            w.setMinimumHeight(60); w.setMaximumHeight(92)
            # hide the widget's own jargon header bar — the card drives the view
            try:
                w.layout().itemAt(0).widget().setVisible(False)
            except Exception:
                pass
            row.addLayout(hdr); row.addWidget(w)
            v.addLayout(row)
            self._widgets.append(w)
            self._rows.append(name)
            self._contact_lines.append(contact)
        self._mode = "waveform"
        self._set_mode("waveform")

    def _set_mode(self, mode):
        self._mode = mode
        self._waves_btn.setChecked(mode == "waveform")
        self._band_btn.setChecked(mode == "bandpower")
        self._waves_btn.setStyleSheet(dt.qss_chip(mode == "waveform"))
        self._band_btn.setStyleSheet(dt.qss_chip(mode == "bandpower"))
        for w in self._widgets:
            try:
                w._show_view(mode)
            except Exception:
                pass

    def load(self, dfs, names):
        """dfs/names: 2-list (df or None, boxer display name). df carries
        t_session_seconds; loaded at offset 0 so set_playhead(t) == t."""
        self._dfs = dfs
        for i, (df, name) in enumerate(zip(dfs, names)):
            self._rows[i].setText(name)
            if df is not None:
                try:
                    self._widgets[i].load(df, offset_sec=0.0, device_name="")
                except Exception:
                    pass
        self._set_mode(self._mode)

    def set_playhead(self, t):
        for w in self._widgets:
            try:
                w.set_playhead(t)
            except Exception:
                pass

    def update_contact(self, t):
        for i, line in enumerate(self._contact_lines):
            df = getattr(self, "_dfs", [None, None])[i] if hasattr(self, "_dfs") else None
            if df is None:
                line.setText("No headband")
                continue
            try:
                summary, _ = cq.assess_at(df, t)
            except Exception:
                summary = "Signal good"
            line.setText(summary)
            weak = summary != "Signal good"
            line.setStyleSheet(
                f"color: {dt.warning_color() if weak else dt.success_color()}; "
                f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_XS}px;")


# ── the tab ──────────────────────────────────────────────────────────────────────

class ReviewLabelTab(QWidget):
    back_to_sessions = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root = None
        self._store = labels_store.LabelsStore()
        self._vocab = []
        self._player: PlayerController | None = None
        self._phases = []
        self._duration = 0.0
        self._who = "A"
        self._pending_duration = None   # (vocab_entry) mid-duration label
        self._created_by = _getuser()
        self._focus_getter = None       # overridable in tests
        self.setStyleSheet(f"ReviewLabelTab {{ background: {dt.canvas_bg()}; }}")
        self._build()

    # ---- construction ----
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(self._build_topbar())
        body = QHBoxLayout(); body.setContentsMargins(20, 14, 20, 16); body.setSpacing(16)

        # No scroll wrapper on the left: the column is sized to fit 1280×800 so
        # nothing is ever clipped (the strict layout test forbids a scrollbar
        # eating viewport width here).
        left = QWidget(); left.setStyleSheet(f"background: {dt.canvas_bg()};")
        lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(12)

        self._grid = VideoGrid(_discover_cameras(self._root) if self._root else
                               [("", "Side camera 1"), ("", "Side camera 2"),
                                ("", "Boxer A view"), ("", "Boxer B view")])
        grid_wrap = QFrame()
        grid_wrap.setStyleSheet(f"background: #0E0F12; border-radius: {dt.RADIUS_LG}px;")
        gw = QVBoxLayout(grid_wrap); gw.setContentsMargins(8, 8, 8, 8)
        gw.addWidget(self._grid)
        grid_wrap.setMinimumHeight(170)
        lv.addWidget(grid_wrap, 1)

        self._brain = BrainActivityCard()
        self._brain.setMaximumHeight(240)
        lv.addWidget(self._brain)

        self._timeline_card = self._build_timeline_card()
        self._timeline_card.setMaximumHeight(150)
        lv.addWidget(self._timeline_card)

        lv.addWidget(self._build_transport())
        body.addWidget(left, 1)

        self._labels_panel = self._build_labels_panel()
        body.addWidget(self._labels_panel)
        root.addLayout(body, 1)

    def _build_topbar(self):
        bar = QFrame(); bar.setFixedHeight(58)
        bar.setStyleSheet(f"background: {dt.surface()}; border-bottom: 1px solid {dt.border()};")
        h = QHBoxLayout(bar); h.setContentsMargins(16, 8, 20, 8); h.setSpacing(12)
        back = QPushButton("←  Sessions"); back.setMinimumHeight(38)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setStyleSheet(dt.qss_secondary_button())
        back.clicked.connect(self.back_to_sessions.emit)
        h.addWidget(back)
        tv = QVBoxLayout(); tv.setSpacing(1)
        self._title = QLabel("Review & Label")
        self._title.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
                                  f"font-size: {dt.SIZE_LG}px; font-weight: 700;")
        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                                     f"font-size: {dt.SIZE_SM}px;")
        tv.addWidget(self._title); tv.addWidget(self._subtitle)
        h.addLayout(tv); h.addStretch()
        self._autosave = QLabel("✓ All labels saved")
        self._autosave.setStyleSheet(f"color: {dt.success_color()}; font-family: {dt.FONT_UI}; "
                                     f"font-size: {dt.SIZE_SM}px;")
        h.addWidget(self._autosave); h.addSpacing(10)
        self._export_btn = QPushButton("Export for analysis")
        self._export_btn.setMinimumHeight(40); self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.setStyleSheet(dt.qss_primary_button())
        self._export_btn.clicked.connect(self._open_export)
        h.addWidget(self._export_btn)
        return bar

    def _build_timeline_card(self):
        card = QFrame(); card.setObjectName("card")
        card.setStyleSheet(f"QFrame#card {{ background: {dt.card_bg()}; "
                           f"border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_LG}px; }}")
        v = QVBoxLayout(card); v.setContentsMargins(12, 10, 12, 10); v.setSpacing(6)
        self._strip = TimelineStrip()
        self._strip.seek_requested.connect(self._seek)
        v.addWidget(self._strip)
        return card

    def _build_transport(self):
        bar = QFrame(); bar.setObjectName("card")
        bar.setStyleSheet(f"QFrame#card {{ background: {dt.card_bg()}; "
                          f"border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_LG}px; }}")
        h = QHBoxLayout(bar); h.setContentsMargins(12, 8, 12, 8); h.setSpacing(10)
        self._back_frame = self._tbtn("◀ |", lambda: self._step(-1))
        self._play_btn = self._tbtn("▶", self._toggle_play, primary=True)
        self._fwd_frame = self._tbtn("| ▶", lambda: self._step(1))
        h.addWidget(self._back_frame); h.addWidget(self._play_btn); h.addWidget(self._fwd_frame)
        self._time_lbl = QLabel("00:00.00 / 00:00.00")
        self._time_lbl.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_MONO}; "
                                     f"font-size: {dt.SIZE_MD}px;")
        h.addSpacing(8); h.addWidget(self._time_lbl)
        self._phase_chip = QLabel("")
        self._phase_chip.setStyleSheet(dt.qss_status_pill("round"))
        h.addWidget(self._phase_chip); h.addStretch()
        spd = QLabel("Speed")
        spd.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; font-size: {dt.SIZE_XS}px;")
        h.addWidget(spd)
        self._speed_group = QButtonGroup(self)
        for s, lbl in ((0.25, "0.25×"), (0.5, "0.5×"), (1.0, "1×")):
            b = QPushButton(lbl); b.setCheckable(True); b.setMinimumHeight(30)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, sp=s: self._set_speed(sp))
            b.setStyleSheet(dt.qss_chip(s == 1.0))
            b.setChecked(s == 1.0)
            self._speed_group.addButton(b)
            h.addWidget(b)
        return bar

    def _tbtn(self, text, slot, primary=False):
        b = QPushButton(text); b.setMinimumHeight(36); b.setMinimumWidth(44)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(dt.qss_primary_button() if primary else dt.qss_secondary_button())
        b.clicked.connect(slot)
        return b

    def _build_labels_panel(self):
        panel = QFrame(); panel.setObjectName("card"); panel.setFixedWidth(340)
        panel.setStyleSheet(f"QFrame#card {{ background: {dt.card_bg()}; "
                            f"border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_LG}px; }}")
        v = QVBoxLayout(panel); v.setContentsMargins(14, 14, 14, 12); v.setSpacing(10)
        head = QLabel("Labels")
        head.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
                           f"font-size: {dt.SIZE_LG}px; font-weight: 700;")
        v.addWidget(head)
        # 1 · who
        v.addWidget(self._step_label("1 · Who did it?"))
        who_row = QHBoxLayout(); who_row.setSpacing(6)
        self._who_btns = {}
        for which in ("A", "B", "Both"):
            b = QPushButton(which); b.setCheckable(True); b.setMinimumHeight(38)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, w=which: self._set_who(w))
            self._who_btns[which] = b
            who_row.addWidget(b)
        v.addLayout(who_row)
        # 2 · what
        v.addWidget(self._step_label("2 · What happened?"))
        self._label_grid = QGridLayout(); self._label_grid.setSpacing(6)
        v.addLayout(self._label_grid)
        self._clinch_note = QLabel("Clinch & Referee stop last a while: press the "
                                   "number when it starts, again when it ends (Esc cancels).")
        self._clinch_note.setWordWrap(True)
        self._clinch_note.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                                        f"font-size: {dt.SIZE_XS}px;")
        v.addWidget(self._clinch_note)
        # placed labels
        self._placed_head = QLabel("Placed labels (0)")
        self._placed_head.setStyleSheet(f"color: {dt.text_secondary()}; font-family: {dt.FONT_UI}; "
                                        f"font-size: {dt.SIZE_SM}px; font-weight: 700;")
        v.addWidget(self._placed_head)
        self._placed_scroll = QScrollArea(); self._placed_scroll.setWidgetResizable(True)
        self._placed_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._placed_host = QWidget(); self._placed_host.setStyleSheet(f"background: {dt.card_bg()};")
        self._placed_layout = QVBoxLayout(self._placed_host)
        self._placed_layout.setContentsMargins(0, 0, 0, 0); self._placed_layout.setSpacing(4)
        self._placed_layout.addStretch()
        self._placed_scroll.setWidget(self._placed_host)
        v.addWidget(self._placed_scroll, 1)
        undo = QPushButton("Undo"); undo.setMinimumHeight(36)
        undo.setCursor(Qt.CursorShape.PointingHandCursor)
        undo.setStyleSheet(dt.qss_secondary_button()); undo.clicked.connect(self.undo)
        v.addWidget(undo)
        helpl = QLabel("Space play / pause · ← → one frame · 1–8 place a label · "
                       "Tab switch boxer · ⌘Z undo")
        helpl.setWordWrap(True)
        helpl.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                            f"font-size: {dt.SIZE_CAPTION}px;")
        v.addWidget(helpl)
        self._set_who("A")
        return panel

    def _step_label(self, text):
        l = QLabel(text)
        l.setStyleSheet(f"color: {dt.text_secondary()}; font-family: {dt.FONT_UI}; "
                        f"font-size: {dt.SIZE_SM}px; font-weight: 700;")
        return l

    # ---- loading ----
    def load_session_folder(self, root: str):
        self.shutdown()
        self._root = root
        recordings_dir = os.path.dirname(os.path.abspath(root))
        self._vocab = label_vocab.load(recordings_dir)
        self._rebuild_label_buttons()

        # manifest → title/subtitle
        import json
        man = {}
        try:
            man = json.loads(open(os.path.join(root, "session_manifest.json"),
                                  encoding="utf-8").read())
        except Exception:
            pass
        boxers = man.get("boxers") or {}
        a = (boxers.get("A") or {}).get("name"); b = (boxers.get("B") or {}).get("name")
        when = man.get("started_at", "") or os.path.basename(root)
        vs = f"{a} vs {b}" if a and b else (a or b or "No names")
        try:
            dts = _dt.datetime.fromisoformat(when).strftime("%a %-d %b %Y, %H:%M")
        except (ValueError, TypeError):
            dts = when
        self._title.setText(f"{dts} · {vs}")
        cfg = man.get("config") or {}
        rounds = int(cfg.get("num_rounds") or 0)

        # cameras + player
        specs = _discover_cameras(root)
        decoder = open_session_cameras(specs)
        self._phases = read_phase_markers(root)
        eeg_dur = 0.0
        dfs, names = self._load_eeg(root, a, b)
        for df in dfs:
            if df is not None and "t_session_seconds" in getattr(df, "columns", []):
                eeg_dur = max(eeg_dur, float(df["t_session_seconds"].iloc[-1]))
        self._duration = max(decoder.duration, eeg_dur,
                             self._phases[-1][0] if self._phases else 0.0)
        # rebuild the grid for the actual cameras
        self._swap_grid(specs)
        self._brain.load(dfs, names)

        mins = max(1, round(self._duration / 60)) if self._duration else 0
        self._subtitle.setText(f"{mins} min · {rounds} round{'s' if rounds != 1 else ''}"
                               + (f" · Boxer A {a}" if a else "")
                               + (f" · Boxer B {b}" if b else ""))

        self._player = PlayerController(decoder, self._duration)
        self._player.frames_ready.connect(self._on_frames)
        self._player.playhead_changed.connect(self._on_playhead)
        self._player.state_changed.connect(self._on_state)
        self._strip.set_session(self._duration, self._phases, [])

        # labels (migrate v1 → v2 + history)
        labels_path = os.path.join(root, labels_store.JSON_NAME)
        self._store, migrated = labels_store.load(labels_path)
        if migrated:
            try:
                session_history.log_event(root, session_history.EVENT_LABELS_MIGRATED)
            except Exception:
                pass
        self._refresh_placed()
        try:
            session_history.log_event(root, session_history.EVENT_OPENED_FOR_REVIEW)
        except Exception:
            pass
        self._seek(0.0)

    def _swap_grid(self, specs):
        parent_layout = self._grid.parentWidget().layout()
        old = self._grid
        new = VideoGrid(specs)
        parent_layout.replaceWidget(old, new)
        old.setParent(None); old.deleteLater()
        self._grid = new

    def _load_eeg(self, root, name_a, name_b):
        import pandas as pd
        muse = os.path.join(root, "continuous", "Muse data")
        dfs = [None, None]
        try:
            from eeg_module import BOXER_LABELS
            from live_session_tab import _muse_friendly
        except Exception:
            BOXER_LABELS, _muse_friendly = {}, (lambda x: x)
        files = []
        if os.path.isdir(muse):
            files = sorted(f for f in os.listdir(muse) if f.endswith(".csv"))
        # map each device (A, B) to its csv by friendly filename
        for device, boxer in BOXER_LABELS.items():
            letter = boxer.replace("BOXER ", "").strip()
            idx = 0 if letter == "A" else 1
            fr = _muse_friendly(device)
            match = next((f for f in files if fr in f), None)
            if match:
                try:
                    dfs[idx] = pd.read_csv(os.path.join(muse, match))
                except Exception:
                    dfs[idx] = None
        # fallback: assign leftover files in order
        if all(d is None for d in dfs) and files:
            for i, f in enumerate(files[:2]):
                try:
                    dfs[i] = pd.read_csv(os.path.join(muse, f))
                except Exception:
                    pass
        names = [f"Boxer A{(' · ' + name_a) if name_a else ''}",
                 f"Boxer B{(' · ' + name_b) if name_b else ''}"]
        return dfs, names

    def _rebuild_label_buttons(self):
        while self._label_grid.count():
            it = self._label_grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._label_buttons = {}
        for i, entry in enumerate(self._vocab):
            key = entry.get("key", i + 1)
            b = QPushButton(f"{key}  {entry['name']}")
            b.setMinimumHeight(40); b.setCursor(Qt.CursorShape.PointingHandCursor)
            col = entry.get("color", "#3D4FD6")
            b.setStyleSheet(
                f"QPushButton {{ text-align: left; padding-left: 10px; background: {dt.surface()}; "
                f"color: {dt.text_primary()}; border: 1px solid {dt.border_strong()}; "
                f"border-left: 4px solid {col}; border-radius: {dt.RADIUS_SM}px; "
                f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; }}"
                f"QPushButton:hover {{ border-color: {dt.accent()}; }}")
            b.clicked.connect(lambda _c=False, e=entry: self._place_from_vocab(e))
            self._label_grid.addWidget(b, i // 2, i % 2)
            self._label_buttons[key] = entry

    # ---- transport / playhead ----
    def _toggle_play(self):
        if self._player:
            self._player.toggle()

    def _step(self, n):
        if self._player:
            self._player.step(n)

    def _seek(self, t):
        if self._player:
            self._player.seek(t)
        else:
            self._on_playhead(t)

    def _set_speed(self, s):
        if self._player:
            self._player.set_speed(s)
        for b in self._speed_group.buttons():
            b.setStyleSheet(dt.qss_chip(b.text() == f"{s:g}×"))

    def _on_frames(self, t, frames):
        self._grid.set_frames(frames)

    def _on_playhead(self, t):
        self._brain.set_playhead(t)
        self._brain.update_contact(t)
        self._strip.set_playhead(t)
        self._time_lbl.setText(f"{_fmt_time(t)} / {_fmt_time(self._duration)}")
        self._phase_chip.setText(self._phase_text(t))

    def _on_state(self, playing):
        self._play_btn.setText("❚❚" if playing else "▶")

    def _phase_text(self, t):
        active = None
        for (mt, pid, label) in self._phases:
            if mt <= t + 1e-6:
                active = (mt, label)
            else:
                break
        if active is None:
            return ""
        name = friendly_phase(active[1])
        return f"{name} · {_fmt_mmss(t - active[0])} in"

    # ---- labels ----
    def _set_who(self, which):
        self._who = which
        for w, b in self._who_btns.items():
            active = (w == which)
            if active:
                line, soft, text = dt.boxer_colors(w) if w in ("A", "B") else (dt.accent(), dt.accent_ghost(), dt.accent())
                b.setStyleSheet(f"QPushButton {{ background: {soft}; color: {text}; "
                                f"border: 1px solid {line}; border-radius: {dt.RADIUS_SM}px; "
                                f"font-weight: 700; font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; }}")
            else:
                b.setStyleSheet(dt.qss_secondary_button())
            b.setChecked(active)

    def _cycle_who(self):
        order = ["A", "B", "Both"]
        self._set_who(order[(order.index(self._who) + 1) % 3])

    def _place_from_vocab(self, entry):
        t = self._player.t if self._player else 0.0
        kind = entry.get("kind", label_vocab.POINT)
        if kind == label_vocab.DURATION:
            self._handle_duration(entry, t)
            return
        self._commit_label(entry, t, None)

    def _handle_duration(self, entry, t):
        if self._pending_duration is None:
            self._pending_duration = (entry, t)
            self._autosave.setText(
                f"{entry['name']} started at {_fmt_time(t)} — press {entry['key']} "
                f"again to end, Esc to cancel")
            self._autosave.setStyleSheet(
                f"color: {dt.warning_color()}; font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px;")
        else:
            start_entry, t0 = self._pending_duration
            self._pending_duration = None
            t_start, t_end = sorted((t0, t))
            self._commit_label(start_entry, t_start, t_end)

    def _cancel_pending(self):
        if self._pending_duration is not None:
            self._pending_duration = None
            self._mark_saved()

    def _commit_label(self, entry, t_start, t_end):
        lab = labels_store.new_label(
            label=entry["name"], who=self._who,
            kind=label_vocab.DURATION if t_end is not None else label_vocab.POINT,
            t_start=t_start, t_end=t_end, color=entry.get("color", "#3D4FD6"),
            created_by=self._created_by)
        self._store.add(lab)
        self._after_change(added=lab)

    def undo(self):
        op = self._store.undo()
        if op:
            self._after_change()

    def redo(self):
        op = self._store.redo()
        if op:
            self._after_change()

    def _delete_label(self, label_id):
        lab = self._store.delete(label_id)
        if lab:
            self._after_change(removed=lab)

    def _after_change(self, added=None, removed=None):
        self._autosave_and_history(added, removed)
        self._refresh_placed()
        self._strip.set_labels(self._store.labels)

    def _autosave_and_history(self, added=None, removed=None):
        if not self._root:
            return
        try:
            self._store.save(os.path.join(self._root, labels_store.JSON_NAME))
        except Exception:
            pass
        try:
            if added is not None:
                session_history.log_event(self._root, session_history.EVENT_LABELS_ADDED,
                                          {"label": added["label"]})
            if removed is not None:
                session_history.log_event(self._root, session_history.EVENT_LABELS_REMOVED,
                                          {"label": removed["label"]})
        except Exception:
            pass
        self._mark_saved()

    def _mark_saved(self):
        self._autosave.setText("✓ All labels saved")
        self._autosave.setStyleSheet(f"color: {dt.success_color()}; font-family: {dt.FONT_UI}; "
                                     f"font-size: {dt.SIZE_SM}px;")

    def _refresh_placed(self):
        while self._placed_layout.count() > 1:
            it = self._placed_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        labels = sorted(self._store.labels, key=lambda x: x.get("created_at", ""), reverse=True)
        self._placed_head.setText(f"Placed labels ({len(labels)})")
        for lab in labels:
            self._placed_layout.insertWidget(self._placed_layout.count() - 1,
                                             self._placed_row(lab))

    def _placed_row(self, lab):
        row = QFrame()
        row.setStyleSheet(f"QFrame {{ background: {dt.surface()}; border: 1px solid {dt.border()}; "
                          f"border-radius: {dt.RADIUS_SM}px; }}")
        h = QHBoxLayout(row); h.setContentsMargins(8, 5, 6, 5); h.setSpacing(6)
        tstr = _fmt_time(lab["t_start"]) + (f"–{_fmt_time(lab['t_end'])}" if lab.get("t_end") is not None else "")
        t = QLabel(tstr)
        t.setStyleSheet(f"color: {dt.text_secondary()}; font-family: {dt.FONT_MONO}; font-size: {dt.SIZE_XS}px;")
        who = QLabel(lab.get("who", ""))
        who.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; font-size: {dt.SIZE_XS}px; font-weight: 700;")
        sw = QLabel("●"); sw.setStyleSheet(f"color: {lab.get('color', '#3D4FD6')};")
        name = QLabel(lab.get("label", ""))
        name.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px;")
        dele = QPushButton("✕"); dele.setFixedSize(22, 22); dele.setCursor(Qt.CursorShape.PointingHandCursor)
        dele.setStyleSheet("QPushButton { border: none; color: #8A8F99; } QPushButton:hover { color: #B91C1C; }")
        dele.clicked.connect(lambda _c=False, i=lab["id"]: self._delete_label(i))
        h.addWidget(t); h.addWidget(who); h.addWidget(sw); h.addWidget(name, 1); h.addWidget(dele)
        # click row (not delete) → seek
        def _seek_to(_e, ts=lab["t_start"]):
            self._seek(ts)
        row.mousePressEvent = _seek_to
        return row

    # ---- keyboard ----
    def _typing_now(self) -> bool:
        from PyQt6.QtWidgets import QApplication, QLineEdit, QTextEdit, QPlainTextEdit
        w = (self._focus_getter() if self._focus_getter else QApplication.focusWidget())
        return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit))

    def handle_key(self, key, modifiers) -> bool:
        """Return True if consumed. Never acts while typing in a text field."""
        if self._typing_now():
            return False
        ctrl = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if ctrl and key == Qt.Key.Key_Z:
            self.redo() if shift else self.undo()
            return True
        if key == Qt.Key.Key_Space:
            self._toggle_play(); return True
        if key == Qt.Key.Key_Left:
            self._step(-1); return True
        if key == Qt.Key.Key_Right:
            self._step(1); return True
        if key == Qt.Key.Key_Tab:
            self._cycle_who(); return True
        if key == Qt.Key.Key_Escape:
            self._cancel_pending(); return True
        if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            n = key - Qt.Key.Key_0
            entry = self._label_buttons.get(n) if hasattr(self, "_label_buttons") else None
            if entry:
                self._place_from_vocab(entry); return True
        return False

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and self.isVisible():
            if self.handle_key(event.key(), event.modifiers()):
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, e):
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self)
        super().showEvent(e)

    def hideEvent(self, e):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
        super().hideEvent(e)

    # ---- export ----
    def _open_export(self):
        if not self._root:
            return
        from export_module import ExportOptionsDialog
        dlg = ExportOptionsDialog(self._root, self._store.labels, self._phases, self)
        dlg.exec()

    # ---- teardown ----
    def shutdown(self):
        if self._player is not None:
            try:
                self._player.shutdown()
            except Exception:
                pass
            self._player = None


def _getuser() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "user"


__all__ = ["ReviewLabelTab", "read_phase_markers", "friendly_phase",
           "_discover_cameras", "_fmt_time"]
