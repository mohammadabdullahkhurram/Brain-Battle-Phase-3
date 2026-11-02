"""
review_label_tab.py
───────────────────
The one merged Review & Label screen (DESIGN §6). Everything runs off a single
playhead on the t_session_seconds axis via pyav_player: the video tiles, both
brain-activity traces, the timeline cursor, and label placement are the same
number by construction.

Open is labels-first: labels.json + the label list + the timeline render
immediately; the video decoder and the brain recordings load on a background
thread (each section shows its own plain loading state) so placed labels never
wait behind decoding or EEG parsing.

Public surface used by the app shell:
  • ReviewLabelTab(parent) — one widget for the "Review & Label" page.
  • .load_session_folder(root) — open a v3 recording (returns fast).
  • .wait_loaded(timeout) — block until the background media load finishes
    (tests only; the live UI just lets the loading states resolve).
  • .back_to_sessions (signal) — the ← Sessions button.
  • .shutdown() — stop the decode worker/timer before teardown.
"""
from __future__ import annotations

import csv
import datetime as _dt
import getpass
import os
import time

import numpy as np

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRect, QRectF, QEvent, QThread
from PyQt6.QtGui import QImage, QPainter, QColor, QPen, QFont, QPolygonF
from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QSizePolicy, QLineEdit, QButtonGroup, QToolTip, QApplication,
)

import design_tokens as dt
import label_vocab
import labels_store
import contact_quality as cq
import session_history
from pyav_player import open_session_cameras, PlayerController, FRAME_PERIOD

# EEG channel → plain-language sensor (DESIGN vocabulary). Channel CODES never
# appear in the UI — only these names, and only in the legend/tooltip.
CH_CODES = ["TP9", "AF7", "AF8", "TP10"]
CH_NAMES = ["Left ear", "Left forehead", "Right forehead", "Right ear"]
CH_COLORS = ["#0E7490", "#3D4FD6", "#7C3AED", "#B45309"]
EEG_SR = 256
BANDS = [("Delta", 1, 4), ("Theta", 4, 8), ("Alpha", 8, 12),
         ("Beta", 12, 30), ("Gamma", 30, 45)]


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
    specs = [(found.get(name, ""), name) for name in order]
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
    """Marker token → full DESIGN vocabulary name."""
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


def short_phase(label: str) -> str:
    """Shortest sensible form for a narrow timeline band."""
    u = (label or "").upper()
    if u.startswith("BASELINE"):
        return "Rest"
    if u.startswith("ROUND"):
        return friendly_phase(label)       # "Round N" is already short
    if u.startswith("BREAK"):
        return "Break"
    if u.startswith("SESSION_END") or u == "DONE":
        return "End"
    return friendly_phase(label)


# ── video ──────────────────────────────────────────────────────────────────────

class VideoTile(QFrame):
    """One 16:9 tile; the frame fills it (letterbox only if the aspect differs),
    with the camera-name chip drawn on top of the picture."""
    enlarge_toggled = pyqtSignal(int)

    def __init__(self, index: int, name: str, missing: bool, parent=None):
        super().__init__(parent)
        self.index = index
        self.name = name
        self.missing = missing
        self.loading = True
        self._img: QImage | None = None
        self._enlarge = QPushButton("⤢", self)
        self._enlarge.setFixedSize(26, 26)
        self._enlarge.setCursor(Qt.CursorShape.PointingHandCursor)
        self._enlarge.setStyleSheet(
            "QPushButton { background: rgba(0,0,0,0.55); color: #FFFFFF; border: none; "
            "border-radius: 6px; font-size: 14px; }")
        self._enlarge.clicked.connect(lambda: self.enlarge_toggled.emit(self.index))

    def set_image(self, img: QImage):
        self._img = img
        self.loading = False
        self.update()

    def resizeEvent(self, e):
        self._enlarge.move(self.width() - self._enlarge.width() - 8, 8)
        super().resizeEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = self.rect()
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor("#0E0F12"))
            p.drawRoundedRect(r, dt.RADIUS_MD, dt.RADIUS_MD)
            if self.missing:
                p.setPen(QColor("#9AA3AF")); p.setFont(QFont("Inter", 11))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, "No video for this camera")
            elif self._img is not None and not self._img.isNull():
                iw, ih = self._img.width(), self._img.height()
                w, h = self.width(), self.height()
                scale = min(w / iw, h / ih)
                dw, dh = iw * scale, ih * scale
                x, y = (w - dw) / 2, (h - dh) / 2
                p.drawImage(QRectF(x, y, dw, dh), self._img)
            elif self.loading:
                p.setPen(QColor("#9AA3AF")); p.setFont(QFont("Inter", 11))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Loading video…")
            # name chip on the picture
            p.setFont(QFont("Inter", 10, QFont.Weight.DemiBold))
            tw = p.fontMetrics().horizontalAdvance(self.name) + 16
            p.setBrush(QColor(0, 0, 0, 150)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(8, 8, tw, 22, 6, 6)
            p.setPen(QColor("#FFFFFF")); p.drawText(16, 23, self.name)
        finally:
            p.end()


class VideoGrid(QWidget):
    """2×2 grid of 16:9 tiles laid out manually so each tile keeps 16:9 and the
    frame fills it. One tile can be enlarged to fill the whole area."""

    def __init__(self, specs: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(180)
        self.tiles: list[VideoTile] = []
        for i, (path, name) in enumerate(specs):
            tile = VideoTile(i, name, missing=not (path and os.path.isfile(path)), parent=self)
            tile.enlarge_toggled.connect(self._toggle_enlarge)
            self.tiles.append(tile)
        self._enlarged = None
        self._relayout()

    def _toggle_enlarge(self, index):
        self._enlarged = None if self._enlarged == index else index
        self._relayout()

    def resizeEvent(self, e):
        self._relayout(); super().resizeEvent(e)

    def _relayout(self):
        W, H, gap = self.width(), self.height(), 8
        if self._enlarged is not None:
            for i, t in enumerate(self.tiles):
                t.setVisible(i == self._enlarged)
            t = self.tiles[self._enlarged]
            cw, ch = self._fit_169(W, H)
            t.setGeometry(int((W - cw) / 2), int((H - ch) / 2), int(cw), int(ch))
            return
        for t in self.tiles:
            t.setVisible(True)
        cw, ch = self._fit_169((W - gap) / 2, (H - gap) / 2)
        bw, bh = 2 * cw + gap, 2 * ch + gap
        x0, y0 = (W - bw) / 2, (H - bh) / 2
        for i, t in enumerate(self.tiles):
            r, c = i // 2, i % 2
            t.setGeometry(int(x0 + c * (cw + gap)), int(y0 + r * (ch + gap)),
                          int(cw), int(ch))

    @staticmethod
    def _fit_169(w, h):
        w = max(1, w); h = max(1, h)
        if w / h > 16 / 9:
            return h * 16 / 9, h
        return w, w * 9 / 16

    def set_frames(self, frames: dict):
        for i, img in frames.items():
            if 0 <= i < len(self.tiles):
                self.tiles[i].set_image(img)


# ── brain activity (jargon-free) ─────────────────────────────────────────────────

class BrainTrace(QWidget):
    """One boxer's compact brain view: four thin stacked channel lines in a ±5 s
    window with the playhead fixed in the centre, or five band-power bars. No
    channel codes, no per-channel axis numbers (the card carries the legend)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._t = None
        self._chans = None
        self._has = False
        self._play = 0.0
        self._mode = "waveform"
        self._bp = None
        self._last_bp_t = None
        self.setToolTip("Top to bottom: " + " · ".join(CH_NAMES))

    def set_mode(self, mode):
        self._mode = mode; self._last_bp_t = None; self.update()

    def load(self, df):
        if df is None or "t_session_seconds" not in getattr(df, "columns", []):
            self._has = False; self.update(); return
        self._t = df["t_session_seconds"].to_numpy(dtype=float)
        self._chans = [df[c].to_numpy(dtype=float) if c in df.columns
                       else np.zeros(len(self._t)) for c in CH_CODES]
        self._has = True
        self.update()

    def set_no_data(self):
        self._has = False; self.update()

    def set_playhead(self, t):
        self._play = float(t)
        if self._mode == "bandpower":
            if self._last_bp_t is None or abs(t - self._last_bp_t) >= 0.2:
                self._compute_bp(); self._last_bp_t = t
        self.update()

    def _window(self, half=5.0):
        i0 = int(np.searchsorted(self._t, self._play - half))
        i1 = int(np.searchsorted(self._t, self._play + half))
        return i0, i1

    def _compute_bp(self):
        if not self._has:
            self._bp = None; return
        from scipy.signal import welch
        i0, i1 = self._window()
        if i1 - i0 < 32:
            self._bp = None; return
        powers = np.zeros(len(BANDS))
        for ch in self._chans:
            seg = ch[i0:i1]
            f, pxx = welch(seg, fs=EEG_SR, nperseg=min(256, len(seg)))
            for bi, (_n, lo, hi) in enumerate(BANDS):
                powers[bi] += np.trapz(pxx[(f >= lo) & (f < hi)],
                                       f[(f >= lo) & (f < hi)]) if np.any((f >= lo) & (f < hi)) else 0.0
        self._bp = powers / max(1e-9, powers.max())

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            W, H = self.width(), self.height()
            p.fillRect(self.rect(), QColor(dt.card_bg()))
            if not self._has:
                p.setPen(QColor(dt.text_tertiary())); p.setFont(QFont("Inter", 10))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                           "No brain recording for this boxer")
                return
            if self._mode == "bandpower":
                self._paint_bands(p, W, H)
            else:
                self._paint_waves(p, W, H)
        finally:
            p.end()

    def _paint_waves(self, p, W, H):
        i0, i1 = self._window()
        n = i1 - i0
        band_h = H / 4.0
        for ci in range(4):
            y_mid = band_h * ci + band_h / 2
            col = QColor(CH_COLORS[ci])
            if n >= 2:
                seg = self._chans[ci][i0:i1]
                t = self._t[i0:i1]
                scale = max(1.0, 3.0 * float(np.std(seg)))
                xs = (t - (self._play - 5.0)) / 10.0 * W
                ys = y_mid - np.clip(seg / scale, -1, 1) * (band_h * 0.42)
                poly = QPolygonF([QPoint(0, 0)] * 0)
                poly = QPolygonF()
                step = max(1, n // (W + 1))
                for k in range(0, n, step):
                    poly.append(QPoint(int(xs[k]), int(ys[k])))
                p.setPen(QPen(col, 1.0)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPolyline(poly)
            else:
                p.setPen(QPen(col, 1.0))
                p.drawLine(0, int(y_mid), W, int(y_mid))
        # centre playhead
        p.setPen(QPen(QColor(dt.accent()), 1.2))
        p.drawLine(W // 2, 0, W // 2, H)

    def _paint_bands(self, p, W, H):
        if self._bp is None:
            self._compute_bp()
        vals = self._bp if self._bp is not None else np.zeros(len(BANDS))
        nb = len(BANDS); gap = 10
        bw = (W - (nb + 1) * gap) / nb
        for bi, (name, _lo, _hi) in enumerate(BANDS):
            x = gap + bi * (bw + gap)
            bh = max(2.0, vals[bi] * (H - 24))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(dt.accent()))
            p.drawRoundedRect(QRectF(x, H - 18 - bh, bw, bh), 3, 3)
            p.setPen(QColor(dt.text_tertiary())); p.setFont(QFont("Inter", 8))
            p.drawText(QRectF(x - 4, H - 16, bw + 8, 14),
                       Qt.AlignmentFlag.AlignHCenter, name)


class BrainActivityCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(f"QFrame#card {{ background: {dt.card_bg()}; "
                           f"border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_LG}px; }}")
        v = QVBoxLayout(self); v.setContentsMargins(12, 8, 12, 8); v.setSpacing(3)
        head = QHBoxLayout()
        title = QLabel("Brain activity")
        title.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
                            f"font-size: {dt.SIZE_MD}px; font-weight: 700;")
        head.addWidget(title); head.addStretch()
        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                                   f"font-size: {dt.SIZE_XS}px;")
        head.addWidget(self._status)
        self._waves = QPushButton("Waves"); self._band = QPushButton("Band power")
        for b in (self._waves, self._band):
            b.setCheckable(True); b.setMinimumHeight(26)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._waves.clicked.connect(lambda: self._set_mode("waveform"))
        self._band.clicked.connect(lambda: self._set_mode("bandpower"))
        head.addWidget(self._waves); head.addWidget(self._band)
        v.addLayout(head)
        # legend (colour dot + plain sensor name) on its own row; labels may
        # shrink (Ignored policy) so the card fits narrow windows.
        leg = QHBoxLayout(); leg.setSpacing(4)
        for name, col in zip(CH_NAMES, CH_COLORS):
            dot = QLabel("●"); dot.setStyleSheet(f"color: {col}; font-size: 10px;")
            lab = QLabel(name)
            lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            lab.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                              f"font-size: {dt.SIZE_XS}px;")
            leg.addWidget(dot); leg.addWidget(lab); leg.addSpacing(4)
        leg.addStretch()
        v.addLayout(leg)

        self._traces = []
        self._names = []
        self._contacts = []
        self._dfs = [None, None]
        for which in ("A", "B"):
            row = QHBoxLayout(); row.setSpacing(8)
            badge = QLabel(which); badge.setFixedSize(20, 20)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            line, soft, text = dt.boxer_colors(which)
            badge.setStyleSheet(f"background: {soft}; color: {text}; border: 1px solid {line}; "
                                f"border-radius: 10px; font-weight: 700; font-size: 11px;")
            name = QLabel(f"Boxer {which}")
            name.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
                               f"font-size: {dt.SIZE_SM}px; font-weight: 600;")
            contact = QLabel("")
            contact.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                                  f"font-size: {dt.SIZE_XS}px;")
            left = QVBoxLayout(); left.setSpacing(2)
            hdr = QHBoxLayout(); hdr.setSpacing(6)
            hdr.addWidget(badge); hdr.addWidget(name); hdr.addStretch()
            left.addLayout(hdr); left.addWidget(contact)
            lw = QWidget(); lw.setLayout(left); lw.setFixedWidth(150)
            trace = BrainTrace()
            row.addWidget(lw); row.addWidget(trace, 1)
            rw = QWidget(); rw.setLayout(row)
            v.addWidget(rw, 1)
            self._traces.append(trace); self._names.append(name); self._contacts.append(contact)
        self._mode = "waveform"; self._set_mode("waveform")
        self.set_loading()

    def _set_mode(self, mode):
        self._mode = mode
        self._waves.setChecked(mode == "waveform"); self._band.setChecked(mode == "bandpower")
        self._waves.setStyleSheet(dt.qss_chip(mode == "waveform"))
        self._band.setStyleSheet(dt.qss_chip(mode == "bandpower"))
        for t in self._traces:
            t.set_mode(mode)

    def set_loading(self):
        self._status.setText("Loading brain activity…")

    def load(self, dfs, names):
        self._dfs = dfs
        self._status.setText("")
        for i, (df, name) in enumerate(zip(dfs, names)):
            self._names[i].setText(name)
            if df is not None:
                self._traces[i].load(df)
            else:
                self._traces[i].set_no_data()
        self._set_mode(self._mode)

    def set_playhead(self, t):
        for tr in self._traces:
            tr.set_playhead(t)

    def update_contact(self, t):
        for i, line in enumerate(self._contacts):
            df = self._dfs[i]
            if df is None:
                line.setText("No headband"); continue
            try:
                summary, _ = cq.assess_at(df, t)
            except Exception:
                summary = "Signal good"
            weak = summary != "Signal good"
            line.setText(summary)
            line.setStyleSheet(
                f"color: {dt.warning_color() if weak else dt.success_color()}; "
                f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_XS}px;")


# ── timeline ────────────────────────────────────────────────────────────────────

class TimelineStrip(QWidget):
    seek_requested = pyqtSignal(float)

    _PHASE_H = 22
    _LANE_H = 22
    _LANES = ["A", "B", "Both"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 1.0
        self.phases = []
        self.labels = []
        self.playhead = 0.0
        self._phase_rects = []     # (QRect, full_name) for tooltips
        self.setMinimumHeight(self._PHASE_H + len(self._LANES) * self._LANE_H + 10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    def set_session(self, duration, phases, labels):
        self.duration = max(0.001, float(duration)); self.phases = list(phases)
        self.labels = list(labels); self.update()

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
        return self._PHASE_H + 2 + i * self._LANE_H

    def mousePressEvent(self, e):
        x = e.position().x()
        best, bd = None, 8
        for lab in self.labels:
            if abs(self._x(lab["t_start"]) - x) < bd:
                best, bd = lab["t_start"], abs(self._x(lab["t_start"]) - x)
        self.seek_requested.emit(best if best is not None else self._t_at(x))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.seek_requested.emit(self._t_at(e.position().x())); return
        pt = e.position().toPoint()
        for rect, full in self._phase_rects:
            if rect.contains(pt):
                QToolTip.showText(e.globalPosition().toPoint(), full, self); return
        QToolTip.hideText()

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        W = self.width(); pal = dt._p()
        colmap = {"baseline": pal.phase_rest, "round": pal.phase_round,
                  "break": pal.phase_break, "done": pal.phase_rest}
        self._phase_rects = []
        if self.phases:
            bounds = list(self.phases) + [(self.duration, "", "")]
            for i in range(len(self.phases)):
                t0, pid, lbl = self.phases[i]
                t1 = bounds[i + 1][0]
                bg, fg = colmap.get(pid, pal.phase_rest)
                x0, x1 = self._x(t0), self._x(t1)
                bw = max(1, x1 - x0 - 1)
                p.setBrush(QColor(bg)); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRectF(x0, 2, bw, self._PHASE_H - 4), 4, 4)
                full = friendly_phase(lbl)
                self._phase_rects.append((QRect(int(x0), 2, int(bw), self._PHASE_H - 4), full))
                # longest label that fits, never mid-word
                p.setFont(QFont("Inter", 9, QFont.Weight.DemiBold))
                fm = p.fontMetrics()
                text = full if fm.horizontalAdvance(full) + 10 <= bw else short_phase(lbl)
                if fm.horizontalAdvance(text) + 8 <= bw:
                    p.setPen(QColor(fg))
                    p.drawText(QRectF(x0 + 5, 2, bw - 6, self._PHASE_H - 4),
                               Qt.AlignmentFlag.AlignVCenter, text)
        p.setFont(QFont("Inter", 8))
        for who in self._LANES:
            y = self._lane_y(who)
            p.setPen(QColor(dt.border()))
            p.drawLine(2, y + self._LANE_H - 3, W - 2, y + self._LANE_H - 3)
            p.setPen(QColor(dt.text_tertiary()))
            p.drawText(QPoint(4, y + 11), who if who == "Both" else f"Boxer {who}")
        for lab in self.labels:
            y = self._lane_y(lab.get("who", "Both"))
            col = QColor(lab.get("color", "#3D4FD6"))
            x0 = self._x(lab["t_start"])
            if lab.get("t_end") is not None:
                x1 = self._x(lab["t_end"])
                p.setBrush(QColor(col.red(), col.green(), col.blue(), 150))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRectF(x0, y + 2, max(2, x1 - x0), self._LANE_H - 7), 3, 3)
            else:
                p.setPen(QPen(col, 2))
                p.drawLine(int(x0), y + 2, int(x0), y + self._LANE_H - 5)
        px = self._x(self.playhead)
        p.setPen(QPen(QColor(dt.accent()), 1.5))
        p.drawLine(int(px), 0, int(px), self.height())


# ── background media loader ─────────────────────────────────────────────────────

class _MediaLoadWorker(QThread):
    """Off the UI thread: open the camera decoders (reads every sidecar) and
    parse the EEG CSVs — the two slow parts of opening a recording."""
    done = pyqtSignal(object, object)   # (MultiCamDecoder, {idx: df})

    def __init__(self, root, specs, eeg_plan, parent=None):
        super().__init__(parent)
        self._root = root; self._specs = specs; self._eeg_plan = eeg_plan

    def run(self):
        import pandas as pd
        decoder = open_session_cameras(self._specs)
        dfs = {}
        for idx, path in self._eeg_plan.items():
            try:
                dfs[idx] = pd.read_csv(path)
            except Exception:
                dfs[idx] = None
        self.done.emit(decoder, dfs)


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
        self._names = {"A": None, "B": None}
        self._pending_duration = None
        self._created_by = _getuser()
        self._focus_getter = None
        self._loader = None
        self._loaded = False
        self.setStyleSheet(f"ReviewLabelTab {{ background: {dt.canvas_bg()}; }}")
        self._build()

    # ---- construction ----
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(self._build_topbar())
        body = QHBoxLayout(); body.setContentsMargins(20, 12, 20, 12); body.setSpacing(16)

        # Left: scrollable content (video + brain + timeline) with the transport
        # bar PINNED below the scroll so it is always visible.
        left = QVBoxLayout(); left.setSpacing(10)
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); content.setStyleSheet(f"background: {dt.canvas_bg()};")
        cv = QVBoxLayout(content); cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(10)

        self._grid = VideoGrid([("", "Side camera 1"), ("", "Side camera 2"),
                                ("", "Boxer A view"), ("", "Boxer B view")])
        grid_wrap = QFrame()
        grid_wrap.setStyleSheet(f"background: #0E0F12; border-radius: {dt.RADIUS_LG}px;")
        gw = QVBoxLayout(grid_wrap); gw.setContentsMargins(8, 8, 8, 8); gw.addWidget(self._grid)
        grid_wrap.setMinimumHeight(200)
        cv.addWidget(grid_wrap, 1)

        self._brain = BrainActivityCard()
        self._brain.setMinimumHeight(140); self._brain.setMaximumHeight(190)
        cv.addWidget(self._brain)

        self._timeline_card = self._build_timeline_card()
        cv.addWidget(self._timeline_card)
        self._scroll.setWidget(content)
        left.addWidget(self._scroll, 1)
        left.addWidget(self._build_transport())    # pinned
        body.addLayout(left, 1)

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
        v = QVBoxLayout(card); v.setContentsMargins(12, 8, 12, 8); v.setSpacing(4)
        self._strip = TimelineStrip()
        self._strip.seek_requested.connect(self._seek)
        v.addWidget(self._strip)
        return card

    def _build_transport(self):
        bar = QFrame(); bar.setObjectName("card")
        bar.setStyleSheet(f"QFrame#card {{ background: {dt.card_bg()}; "
                          f"border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_LG}px; }}")
        h = QHBoxLayout(bar); h.setContentsMargins(12, 8, 12, 8); h.setSpacing(10)
        h.addWidget(self._tbtn("◀ |", lambda: self._step(-1)))
        self._play_btn = self._tbtn("▶", self._toggle_play, primary=True)
        h.addWidget(self._play_btn)
        h.addWidget(self._tbtn("| ▶", lambda: self._step(1)))
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
            b.setStyleSheet(dt.qss_chip(s == 1.0)); b.setChecked(s == 1.0)
            self._speed_group.addButton(b); h.addWidget(b)
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
        v.addWidget(self._step_label("1 · Who did it?"))
        who_row = QHBoxLayout(); who_row.setSpacing(6)
        self._who_btns = {}
        for which in ("A", "B", "Both"):
            b = QPushButton(which); b.setCheckable(True); b.setMinimumHeight(38)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, w=which: self._set_who(w))
            self._who_btns[which] = b; who_row.addWidget(b)
        v.addLayout(who_row)
        v.addWidget(self._step_label("2 · What happened?"))
        self._label_grid = QGridLayout(); self._label_grid.setSpacing(6)
        v.addLayout(self._label_grid)
        self._clinch_note = QLabel("Clinch & Referee stop last a while: press the "
                                   "number when it starts, again when it ends (Esc cancels).")
        self._clinch_note.setWordWrap(True)
        self._clinch_note.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                                        f"font-size: {dt.SIZE_XS}px;")
        v.addWidget(self._clinch_note)
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

    # ---- loading (labels-first; media in the background) ----
    def load_session_folder(self, root: str):
        self.shutdown()
        self._root = root
        self._loaded = False
        recordings_dir = os.path.dirname(os.path.abspath(root))
        self._vocab = label_vocab.load(recordings_dir)
        self._rebuild_label_buttons()

        import json
        man = {}
        try:
            man = json.loads(open(os.path.join(root, "session_manifest.json"),
                                  encoding="utf-8").read())
        except Exception:
            pass
        boxers = man.get("boxers") or {}
        a = (boxers.get("A") or {}).get("name"); b = (boxers.get("B") or {}).get("name")
        self._names = {"A": a, "B": b}
        when = man.get("started_at", "") or os.path.basename(root)
        vs = f"{a} vs {b}" if a and b else (a or b or "No names")
        try:
            dts = _dt.datetime.fromisoformat(when).strftime("%a %-d %b %Y, %H:%M")
        except (ValueError, TypeError):
            dts = when
        self._title.setText(f"{dts} · {vs}")
        cfg = man.get("config") or {}
        rounds = int(cfg.get("num_rounds") or 0)
        self._rounds = rounds
        self._update_who_button_labels()

        # FAST: phases + labels + timeline render immediately
        self._phases = read_phase_markers(root)
        self._duration = self._phases[-1][0] if self._phases else 0.0
        labels_path = os.path.join(root, labels_store.JSON_NAME)
        self._store, migrated = labels_store.load(labels_path)
        if migrated:
            try:
                session_history.log_event(root, session_history.EVENT_LABELS_MIGRATED)
            except Exception:
                pass
        self._refresh_placed()
        self._strip.set_session(self._duration, self._phases, self._store.labels)
        mins = max(1, round(self._duration / 60)) if self._duration else 0
        self._subtitle.setText(self._subtitle_text(mins, rounds, a, b))
        self._brain.set_loading()
        try:
            session_history.log_event(root, session_history.EVENT_OPENED_FOR_REVIEW)
        except Exception:
            pass

        # SLOW: cameras (sidecars) + EEG CSVs on a background thread
        specs = _discover_cameras(root)
        self._swap_grid(specs)
        eeg_plan = self._eeg_plan(root)
        self._loader = _MediaLoadWorker(root, specs, eeg_plan, self)
        self._loader.done.connect(self._on_media_loaded)
        self._loader.start()

    def _subtitle_text(self, mins, rounds, a, b):
        return (f"{mins} min · {rounds} round{'s' if rounds != 1 else ''}"
                + (f" · Boxer A {a}" if a else "") + (f" · Boxer B {b}" if b else ""))

    def _eeg_plan(self, root) -> dict:
        """{0: pathA, 1: pathB} — which CSV is each boxer's band."""
        muse = os.path.join(root, "continuous", "Muse data")
        plan = {}
        try:
            from eeg_module import BOXER_LABELS
            from live_session_tab import _muse_friendly
        except Exception:
            BOXER_LABELS, _muse_friendly = {}, (lambda x: x)
        files = sorted(f for f in os.listdir(muse)) if os.path.isdir(muse) else []
        files = [f for f in files if f.endswith(".csv")]
        for device, boxer in BOXER_LABELS.items():
            idx = 0 if boxer.replace("BOXER ", "").strip() == "A" else 1
            fr = _muse_friendly(device)
            match = next((f for f in files if fr in f), None)
            if match:
                plan[idx] = os.path.join(muse, match)
        if not plan and files:
            for i, f in enumerate(files[:2]):
                plan[i] = os.path.join(muse, f)
        return plan

    def _on_media_loaded(self, decoder, dfs):
        eeg_dur = 0.0
        ordered = [dfs.get(0), dfs.get(1)]
        for df in ordered:
            if df is not None and "t_session_seconds" in getattr(df, "columns", []):
                eeg_dur = max(eeg_dur, float(df["t_session_seconds"].iloc[-1]))
        self._duration = max(decoder.duration, eeg_dur,
                             self._phases[-1][0] if self._phases else 0.0)
        self._player = PlayerController(decoder, self._duration)
        self._player.frames_ready.connect(self._on_frames)
        self._player.playhead_changed.connect(self._on_playhead)
        self._player.state_changed.connect(self._on_state)
        self._strip.set_session(self._duration, self._phases, self._store.labels)
        names = [f"Boxer A{(' · ' + self._names['A']) if self._names['A'] else ''}",
                 f"Boxer B{(' · ' + self._names['B']) if self._names['B'] else ''}"]
        self._brain.load(ordered, names)
        mins = max(1, round(self._duration / 60)) if self._duration else 0
        self._subtitle.setText(self._subtitle_text(mins, getattr(self, "_rounds", 0),
                                                   self._names["A"], self._names["B"]))
        self._loaded = True
        self._seek(0.0)

    def wait_loaded(self, timeout: float = 15.0) -> bool:
        deadline = time.time() + timeout
        while not self._loaded and time.time() < deadline:
            QApplication.processEvents(); time.sleep(0.01)
        return self._loaded

    def _swap_grid(self, specs):
        parent_layout = self._grid.parentWidget().layout()
        old = self._grid
        new = VideoGrid(specs)
        parent_layout.replaceWidget(old, new)
        old.setParent(None); old.deleteLater()
        self._grid = new

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

    def _update_who_button_labels(self):
        a, b = self._names.get("A"), self._names.get("B")
        self._who_btns["A"].setText(f"A · {a}" if a else "Boxer A")
        self._who_btns["B"].setText(f"B · {b}" if b else "Boxer B")
        self._who_btns["Both"].setText("Both")

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
        return f"{friendly_phase(active[1])} · {_fmt_mmss(t - active[0])} in"

    # ---- labels ----
    def _set_who(self, which):
        self._who = which
        for w, btn in self._who_btns.items():
            active = (w == which)
            if active:
                line, soft, text = (dt.boxer_colors(w) if w in ("A", "B")
                                    else (dt.accent(), dt.accent_ghost(), dt.accent()))
                btn.setStyleSheet(f"QPushButton {{ background: {soft}; color: {text}; "
                                  f"border: 1px solid {line}; border-radius: {dt.RADIUS_SM}px; "
                                  f"font-weight: 700; font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; }}")
            else:
                btn.setStyleSheet(dt.qss_secondary_button())
            btn.setChecked(active)

    def _cycle_who(self):
        order = ["A", "B", "Both"]
        self._set_who(order[(order.index(self._who) + 1) % 3])

    def _place_from_vocab(self, entry):
        t = self._player.t if self._player else 0.0
        if entry.get("kind", label_vocab.POINT) == label_vocab.DURATION:
            self._handle_duration(entry, t)
        else:
            self._commit_label(entry, t, None)

    def _handle_duration(self, entry, t):
        if self._pending_duration is None:
            self._pending_duration = (entry, t)
            self._autosave.setText(
                f"{entry['name']} started at {_fmt_time(t)} — press {entry['key']} again "
                f"to end, Esc to cancel")
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
        if self._store.undo():
            self._after_change()

    def redo(self):
        if self._store.redo():
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
        def _seek_to(_e, ts=lab["t_start"]):
            self._seek(ts)
        row.mousePressEvent = _seek_to
        return row

    # ---- keyboard ----
    def _typing_now(self) -> bool:
        from PyQt6.QtWidgets import QLineEdit, QTextEdit, QPlainTextEdit
        w = (self._focus_getter() if self._focus_getter else QApplication.focusWidget())
        return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit))

    def handle_key(self, key, modifiers) -> bool:
        if self._typing_now():
            return False
        ctrl = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if ctrl and key == Qt.Key.Key_Z:
            self.redo() if shift else self.undo(); return True
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
            entry = (getattr(self, "_label_buttons", {}) or {}).get(key - Qt.Key.Key_0)
            if entry:
                self._place_from_vocab(entry); return True
        return False

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and self.isVisible():
            if self.handle_key(event.key(), event.modifiers()):
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, e):
        QApplication.instance().installEventFilter(self); super().showEvent(e)

    def hideEvent(self, e):
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
        super().hideEvent(e)

    # ---- export ----
    def _open_export(self):
        if not self._root:
            return
        from export_module import ExportOptionsDialog
        ExportOptionsDialog(self._root, self._store.labels, self._phases, self).exec()

    # ---- teardown ----
    def shutdown(self):
        if self._loader is not None and self._loader.isRunning():
            self._loader.wait(3000)
        self._loader = None
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


__all__ = ["ReviewLabelTab", "read_phase_markers", "friendly_phase", "short_phase",
           "_discover_cameras", "_fmt_time"]
