"""
review_label_screen.py
───────────────────
The one merged Review & Label screen (DESIGN §6) — where labellers spend hours.
Everything runs off a single playhead on the t_session_seconds axis via
pyav_player: the video tiles, both brain-activity displays, the timeline cursor
and label placement are the same number by construction.

Layout
  top bar   ← Sessions · what recording this is · saved status · who is
            labelling · Export for analysis
  left      the four videos, with the brain-activity card under them (it can be
            folded away to give the videos the room); pinned underneath, always
            visible: the transport and the timeline
  right     the Label panel: who did it → what happened, then "My labels" or
            "Compare" (another labeller's labels beside yours, with how far you
            agree)

More than one person can label the same recording. You always label as yourself
("Your name", asked once); your labels are saved in your own file in the
recording folder (labels_store), so nobody overwrites anybody. Other labellers'
labels are read-only here and drawn in grey, never in a label colour.

Open is labels-first: your labels + the label list + the timeline render
immediately; the video decoder and the brain recordings load on a background
thread (each section shows its own plain loading state).

Public surface used by the app shell:
  • ReviewLabelScreen(parent) — one widget for the "Review & Label" page.
  • .load_session_folder(root) — open a recording (returns fast).
  • .wait_loaded(timeout) — block until the background media load finishes
    (tests only; the live UI just lets the loading states resolve).
  • .set_labeller(name) / .set_names_hidden(bool)
  • .back_to_sessions / .labeller_changed (signals)
  • .shutdown() — stop the decode worker/timer before teardown.
"""
from __future__ import annotations

import os
import time

from PyQt6.QtCore import Qt, pyqtSignal, QRect, QRectF, QEvent, QThread, QTimer, QSize
from PyQt6.QtGui import QImage, QPainter, QColor, QPen, QFont, QIcon
from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QSizePolicy, QToolTip, QApplication,
    QDialog, QComboBox, QStackedWidget, QLineEdit,
)

import brain_battle.ui.design_tokens as dt
import brain_battle.ui.widgets as ui
import brain_battle.ui.eeg_viewer as eeg_viewer
from brain_battle.data.signals import EEG_CHANNELS
import brain_battle.data.app_settings as app_settings
import brain_battle.data.label_agreement as label_agreement
import brain_battle.data.label_vocab as label_vocab
import brain_battle.data.labels_store as labels_store
import brain_battle.data.privacy as privacy
import brain_battle.capture.contact_quality as cq
import brain_battle.data.session_history as session_history
import brain_battle.data.session_storage as session_storage
from brain_battle.data.session_storage import read_phase_markers, friendly_phase, short_phase
from brain_battle.playback.pyav_player import open_session_cameras, PlayerController
from brain_battle.support import platform_utils


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
        self._enlarge = QPushButton(self)
        self._enlarge.setIcon(QIcon(dt.icon_pixmap("expand", color="#FFFFFF", size=14)))
        self._enlarge.setIconSize(QSize(14, 14))
        self._enlarge.setFixedSize(28, 28)
        self._enlarge.setToolTip("Make this camera bigger / show all four")
        self._enlarge.setAccessibleName("Enlarge this camera")
        self._enlarge.setCursor(Qt.CursorShape.PointingHandCursor)
        self._enlarge.setStyleSheet(
            "QPushButton { background: rgba(0,0,0,0.55); border: none; border-radius: 7px; }"
            "QPushButton:hover { background: rgba(0,0,0,0.8); }")
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
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor("#161B26"))
            p.drawRoundedRect(r, dt.RADIUS_SM, dt.RADIUS_SM)
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
        W, H, gap = self.width(), self.height(), 6
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


# ── brain activity: a standard EEG viewer per boxer ─────────────────────────────

def _small_combo(items, current, tip):
    c = QComboBox()
    for text, value in items:
        c.addItem(text, value)
    c.setCurrentIndex(max(0, c.findData(current)))
    c.setToolTip(tip)
    c.setFixedHeight(28)
    c.setCursor(Qt.CursorShape.PointingHandCursor)
    c.setStyleSheet(dt.qss_combo(compact=True))
    return c


class BrainActivityCard(QFrame):
    """Both boxers' EEG, drawn as a conventional EEG viewer (ui/eeg_viewer.py):
    one stacked, labelled trace per channel in µV, a shared time axis, timebase
    and scale controls, and the recording's event markers. The playhead stays
    in the centre and the signal moves under it. ``Hide`` folds the card down
    to its title row so the videos get the room."""
    HEIGHT_NORMAL, HEIGHT_TALL, HEIGHT_FOLDED = 300, 480, 46
    HEIGHT_MIN = 258          # the normal height shrinks to this on a small window
                              # (two viewers of five rows each still fit)
    folded_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("brainCard")
        self.setStyleSheet(dt.qss_card("brainCard"))
        v = QVBoxLayout(self); v.setContentsMargins(14, 8, 14, 8); v.setSpacing(4)
        head = QHBoxLayout(); head.setSpacing(6)
        title = QLabel("Brain activity (EEG)")
        title.setStyleSheet(dt.css_text(dt.SIZE_MD, 700))
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title.setMinimumWidth(96)       # may shorten on a narrow window, never push
        head.addWidget(title, 1)
        self._waves = QPushButton("Waves"); self._band = QPushButton("Band power")
        for b in (self._waves, self._band):
            b.setCheckable(True); b.setFixedHeight(28)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._waves.clicked.connect(lambda: self._set_mode("waveform"))
        self._band.clicked.connect(lambda: self._set_mode("bandpower"))
        head.addWidget(self._waves); head.addWidget(self._band)
        self._timebase = _small_combo(
            [(f"{s} s", s) for s in eeg_viewer.TIMEBASES], eeg_viewer.DEFAULT_TIMEBASE,
            "Timebase: how many seconds fit across the display")
        self._timebase.currentIndexChanged.connect(self._apply_view)
        self._scale = _small_combo(
            [("Auto scale", None)] + [(f"{s} µV", s) for s in eeg_viewer.SCALES],
            eeg_viewer.DEFAULT_SCALE,
            "Scale: how many microvolts the scale bar stands for")
        self._scale.currentIndexChanged.connect(self._apply_view)
        self._filter = QPushButton("Filter 1–45 Hz"); self._filter.setCheckable(True)
        self._filter.setChecked(True); self._filter.setFixedHeight(28)
        self._filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._filter.setToolTip("Display filter only — the recorded data is never changed. "
                                "Switch it off to see the raw signal.")
        self._filter.clicked.connect(self._apply_view)
        self._tall = QPushButton(); self._tall.setCheckable(True)
        self._tall.setIcon(QIcon(dt.icon_pixmap("expand", color=dt.text_secondary(), size=14)))
        self._tall.setFixedSize(32, 28); self._tall.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tall.setToolTip("Make the EEG display taller / shorter")
        self._tall.setAccessibleName("Make the EEG display taller")
        self._tall.clicked.connect(self._apply_height)
        self._fold = QPushButton("Hide"); self._fold.setCheckable(True)
        self._fold.setFixedHeight(28); self._fold.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fold.setToolTip("Fold the brain activity away to give the videos more room")
        self._fold.clicked.connect(self._apply_height)
        self._view_controls = [self._waves, self._band, self._timebase, self._scale,
                               self._filter, self._tall]
        for w in (self._timebase, self._scale, self._filter, self._tall, self._fold):
            head.addWidget(w)
        v.addLayout(head)

        self.viewers, self._bars, self._stacks = [], [], []
        self._names, self._contacts, self._rows = [], [], []
        self._dfs = [None, None]
        for i, which in enumerate(("A", "B")):
            row = QHBoxLayout(); row.setSpacing(8); row.setContentsMargins(0, 0, 0, 0)
            line, soft, text = dt.boxer_colors(which)
            name = QLabel(f"Boxer {which}")
            name.setStyleSheet(dt.css_text(dt.SIZE_SM, 600))
            name.setWordWrap(True)
            contact = QLabel(""); contact.setWordWrap(True)
            contact.setStyleSheet(dt.css_text(dt.SIZE_XS, 400, dt.text_tertiary()))
            left = QVBoxLayout(); left.setSpacing(2); left.setContentsMargins(0, 2, 0, 0)
            hdr = QHBoxLayout(); hdr.setSpacing(6)
            hdr.addWidget(ui.boxer_badge(which, 22), 0, Qt.AlignmentFlag.AlignTop)
            hdr.addWidget(name, 1)
            left.addLayout(hdr); left.addWidget(contact); left.addStretch()
            lw = QWidget(); lw.setLayout(left); lw.setFixedWidth(132)
            viewer = eeg_viewer.EEGViewer(color=text)
            viewer.show_marker_labels = (i == 0)     # event names along the top …
            viewer.show_axis = (i == 1)              # … the shared time axis at the bottom
            bars = eeg_viewer.BandPowerBars(color=line)
            stack = QStackedWidget(); stack.addWidget(viewer); stack.addWidget(bars)
            row.addWidget(lw); row.addWidget(stack, 1)
            rw = QWidget(); rw.setLayout(row)
            v.addWidget(rw, 1)
            self.viewers.append(viewer); self._bars.append(bars); self._stacks.append(stack)
            self._names.append(name); self._contacts.append(contact); self._rows.append(rw)
        self._normal_height = self.HEIGHT_NORMAL
        self._mode = "waveform"; self._set_mode("waveform")
        self._apply_view(); self._apply_height()
        for vw in self.viewers:
            vw.empty_text = "Open a recording from Sessions to see its brain activity"

    def _set_mode(self, mode):
        self._mode = mode
        self._waves.setChecked(mode == "waveform"); self._band.setChecked(mode == "bandpower")
        tight = " QPushButton { padding: 4px 9px; }"
        self._waves.setStyleSheet(dt.qss_chip(mode == "waveform") + tight)
        self._band.setStyleSheet(dt.qss_chip(mode == "bandpower") + tight)
        for stack in self._stacks:
            stack.setCurrentIndex(0 if mode == "waveform" else 1)
        self._refresh_bars()

    def _apply_view(self, *_):
        self._filter.setStyleSheet(dt.qss_chip(self._filter.isChecked())
                                   + " QPushButton { padding: 4px 9px; }")
        for vw in self.viewers:
            vw.set_timebase(self._timebase.currentData())
            vw.set_scale(self._scale.currentData())
            vw.set_filter(self._filter.isChecked())
        self._refresh_bars()

    def is_folded(self) -> bool:
        return self._fold.isChecked()

    def _apply_height(self, *_):
        folded = self._fold.isChecked()
        self._fold.setText("Show" if folded else "Hide")
        self._fold.setStyleSheet(dt.qss_chip(folded) + " QPushButton { padding: 4px 9px; }")
        self._tall.setStyleSheet(dt.qss_chip(self._tall.isChecked())
                                 + " QPushButton { padding: 0; }")
        for w in self._view_controls:
            w.setVisible(not folded)
        for rw in self._rows:
            rw.setVisible(not folded)
        self.setFixedHeight(self.HEIGHT_FOLDED if folded else
                            self.HEIGHT_TALL if self._tall.isChecked() else self._normal_height)
        self.folded_changed.emit(folded)

    def fit_to(self, available: int) -> None:
        """Share the space above the timeline with the videos: on a small
        window the normal height gives way (down to HEIGHT_MIN) so the videos —
        what a labeller watches — keep the larger part."""
        h = max(self.HEIGHT_MIN, min(self.HEIGHT_NORMAL, int(available * 0.46)))
        if h != self._normal_height:
            self._normal_height = h
            if not self._fold.isChecked() and not self._tall.isChecked():
                self.setFixedHeight(h)

    def _refresh_bars(self):
        if self._mode == "bandpower":
            for vw, bars in zip(self.viewers, self._bars):
                bars.set_window(*vw.visible_samples())

    def set_loading(self):
        for vw in self.viewers:
            vw.empty_text = "Loading brain activity…"
            vw.set_data(None, {})

    def set_names(self, names):
        for lbl, name in zip(self._names, names):
            lbl.setText(name)

    def load(self, dfs, names):
        self._dfs = dfs
        for i, (df, name) in enumerate(zip(dfs, names)):
            self.viewers[i].empty_text = "No brain recording for this boxer"
            self._names[i].setText(name)
            if df is not None and "t_session_seconds" in getattr(df, "columns", []):
                self.viewers[i].set_data(
                    df["t_session_seconds"].to_numpy(dtype=float),
                    {c: df[c].to_numpy(dtype=float) for c in EEG_CHANNELS if c in df.columns})
            else:
                self._dfs[i] = None
                self.viewers[i].set_data(None, {})

    def set_markers(self, markers):
        """``[(t_session_seconds, text, colour), …]`` drawn on both viewers."""
        for vw in self.viewers:
            vw.set_markers(markers)

    def set_playhead(self, t):
        for vw in self.viewers:
            vw.set_view(t)
        self._refresh_bars()

    def update_contact(self, t):
        if self.is_folded():
            return
        for i, line in enumerate(self._contacts):
            df = self._dfs[i]
            if df is None:
                line.setText("No headband"); continue
            try:
                summary, _ = cq.assess_at(df, t)
            except Exception:
                summary = "Signal good"
            weak = summary != "Signal good"
            line.setText(("! " if weak else "✓ ") + summary)
            line.setStyleSheet(dt.css_text(
                dt.SIZE_XS, 500, dt.warning_color() if weak else dt.success_color()))


# ── timeline ────────────────────────────────────────────────────────────────────

class TimelineStrip(QWidget):
    """The whole recording at a glance: a Phases row (rest / rounds / breaks),
    one lane per boxer for YOUR labels (Boxer A, Boxer B, Both), and — while
    comparing — one grey lane per other labeller. Click or drag to move the
    playhead; clicking near a label jumps exactly onto it."""
    seek_requested = pyqtSignal(float)

    _PHASE_H = 24
    _LANE_H = 20
    _GUTTER = 62          # lane names live here, never on top of the labels
    _LANES = ["A", "B", "Both"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 1.0
        self.phases = []
        self.labels = []
        self.others = []           # [(labeller name, [labels])] — read-only, grey
        self.playhead = 0.0
        self._phase_rects = []     # (QRect, full_name) for tooltips
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._resize()

    def _resize(self):
        self.setFixedHeight(self._PHASE_H + 4
                            + (len(self._LANES) + len(self.others)) * self._LANE_H + 4)

    def set_session(self, duration, phases, labels):
        self.duration = max(0.001, float(duration)); self.phases = list(phases)
        self.labels = list(labels); self.update()

    def set_labels(self, labels):
        self.labels = list(labels); self.update()

    def set_others(self, others):
        self.others = [(n, list(l)) for n, l in (others or [])]
        self._resize(); self.update()

    def set_playhead(self, t):
        self.playhead = float(t); self.update()

    def _x(self, t):
        return self._GUTTER + (self.width() - self._GUTTER - 4) * (t / self.duration)

    def _t_at(self, x):
        return max(0.0, min(self.duration,
                            (x - self._GUTTER) / max(1, self.width() - self._GUTTER - 4) * self.duration))

    def _lane_y(self, who):
        i = self._LANES.index(who) if who in self._LANES else 2
        return self._PHASE_H + 4 + i * self._LANE_H

    def _other_y(self, i):
        return self._PHASE_H + 4 + (len(self._LANES) + i) * self._LANE_H

    def mousePressEvent(self, e):
        x = e.position().x()
        best, bd = None, 8
        everything = list(self.labels) + [l for _n, labs in self.others for l in labs]
        for lab in everything:
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

    def _draw_label(self, p, lab, y, col, hollow=False):
        x0 = self._x(lab["t_start"])
        h = self._LANE_H - 7
        if lab.get("t_end") is not None:
            x1 = self._x(lab["t_end"])
            rect = QRectF(x0, y + 3, max(3, x1 - x0), h)
            if hollow:
                p.setBrush(QColor(col.red(), col.green(), col.blue(), 40))
                p.setPen(QPen(col, 1.2))
            else:
                p.setBrush(QColor(col.red(), col.green(), col.blue(), 170))
                p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect, 3, 3)
        else:
            p.setPen(QPen(col, 1.4 if hollow else 2.4, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(int(x0), y + 3, int(x0), y + 3 + h)

    def _paint(self, p):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        W = self.width(); pal = dt._p()
        colmap = {"baseline": pal.phase_rest, "round": pal.phase_round,
                  "break": pal.phase_break, "done": pal.phase_rest}
        gutter_font = QFont("Inter"); gutter_font.setPixelSize(11)
        # gutter: lane names
        p.setFont(gutter_font); p.setPen(QColor(dt.text_tertiary()))
        p.drawText(QRectF(0, 2, self._GUTTER - 6, self._PHASE_H - 4),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "Phases")
        self._phase_rects = []
        if self.phases:
            bounds = list(self.phases) + [(self.duration, "", "")]
            phase_font = QFont("Inter"); phase_font.setPixelSize(11)
            phase_font.setWeight(QFont.Weight.DemiBold)
            for i in range(len(self.phases)):
                t0, pid, lbl = self.phases[i]
                if pid == "done":
                    continue
                t1 = bounds[i + 1][0]
                bg, fg = colmap.get(pid, pal.phase_rest)
                x0, x1 = self._x(t0), self._x(t1)
                bw = max(1, x1 - x0 - 1)
                p.setBrush(QColor(bg)); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRectF(x0, 2, bw, self._PHASE_H - 4), 5, 5)
                full = friendly_phase(lbl)
                self._phase_rects.append((QRect(int(x0), 2, int(bw), self._PHASE_H - 4), full))
                # longest label that fits, never mid-word
                p.setFont(phase_font)
                fm = p.fontMetrics()
                text = full if fm.horizontalAdvance(full) + 12 <= bw else short_phase(lbl)
                if fm.horizontalAdvance(text) + 10 <= bw:
                    p.setPen(QColor(fg))
                    p.drawText(QRectF(x0 + 6, 2, bw - 8, self._PHASE_H - 4),
                               Qt.AlignmentFlag.AlignVCenter, text)
        p.setFont(gutter_font)
        for who in self._LANES:
            y = self._lane_y(who)
            p.setPen(QColor(dt.border()))
            p.drawLine(self._GUTTER, y + self._LANE_H - 1, W - 4, y + self._LANE_H - 1)
            p.setPen(QColor(dt.boxer_colors(who)[2] if who in ("A", "B") else dt.text_tertiary()))
            p.drawText(QRectF(0, y, self._GUTTER - 6, self._LANE_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       who if who == "Both" else f"Boxer {who}")
        for lab in self.labels:
            self._draw_label(p, lab, self._lane_y(lab.get("who", "Both")),
                             QColor(lab.get("color", dt.accent())))
        grey = QColor(dt.other_labeller())
        for i, (name, labs) in enumerate(self.others):
            y = self._other_y(i)
            p.setPen(QColor(dt.border()))
            p.drawLine(self._GUTTER, y + self._LANE_H - 1, W - 4, y + self._LANE_H - 1)
            p.setPen(grey)
            short = p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, self._GUTTER - 8)
            p.drawText(QRectF(0, y, self._GUTTER - 6, self._LANE_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, short)
            for lab in labs:
                self._draw_label(p, lab, y, grey, hollow=True)
        px = self._x(self.playhead)
        p.setPen(QPen(QColor(dt.text_primary()), 1.6))
        p.drawLine(int(px), 0, int(px), self.height())
        p.setBrush(QColor(dt.text_primary())); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(px - 4, 0, 8, 5), 2, 2)


# ── background media loader ─────────────────────────────────────────────────────

class _MediaLoadWorker(QThread):
    """Off the UI thread: open the camera decoders (reads every sidecar) and
    parse the EEG CSVs — the two slow parts of opening a recording."""
    done = pyqtSignal(object, object)   # (MultiCamDecoder, {idx: df})

    def __init__(self, root, specs, eeg_plan, parent=None):
        super().__init__(parent)
        self._root = root; self._specs = specs; self._eeg_plan = eeg_plan

    def run(self):
        decoder = open_session_cameras(self._specs)
        dfs = {}
        for idx, path in self._eeg_plan.items():
            try:
                # tolerant: an interrupted recording can end in a half-written row
                dfs[idx] = session_storage.read_csv_tolerant(path).dropna()
            except Exception:
                dfs[idx] = None
        self.done.emit(decoder, dfs)


# ── the screen ───────────────────────────────────────────────────────────────────

_SPEEDS = ((0.25, "0.25×"), (0.5, "0.5×"), (1.0, "1×"), (2.0, "2×"))


def _label_button_qss(colour: str) -> str:
    return (f"QPushButton {{ text-align: left; padding: 0 10px; background: {dt.surface()}; "
            f"color: {dt.text_primary()}; border: 1px solid {dt.border_strong()}; "
            f"border-left: 5px solid {colour}; border-radius: {dt.RADIUS_SM}px; "
            f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {dt.card_bg_hover()}; }}"
            f"QPushButton:pressed {{ background: {dt.accent_ghost()}; }}"
            f"QPushButton:disabled {{ color: {dt.text_tertiary()}; background: {dt.surface_subtle()}; }}")


def _who_qss(which: str, active: bool) -> str:
    if not active:
        return dt.qss_secondary_button(compact=True)
    line, soft, text = (dt.boxer_colors(which) if which in ("A", "B")
                        else (dt.accent(), dt.accent_ghost(), dt.accent_pressed()))
    return (f"QPushButton {{ background: {soft}; color: {text}; border: 1.5px solid {line}; "
            f"border-radius: {dt.RADIUS_SM}px; padding: 6px 8px; font-weight: 700; "
            f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; }}")


class ReviewLabelScreen(QWidget):
    back_to_sessions = pyqtSignal()
    help_requested   = pyqtSignal()
    labeller_changed = pyqtSignal(str)      # "Your name" was set from this screen

    PANEL_WIDTH = 348
    OTHERS_POLL_MS = 5000                   # how often other labellers' files are re-read

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root = None
        self._labeller = app_settings.labeller_name()
        self._hide_names = app_settings.hide_names_on_screen()
        self._store = labels_store.LabelsStore(labeller=self._labeller)
        self._others = {}            # {labeller name: [labels]} — everyone but me
        self._compare_with = None
        self._tolerance = label_agreement.DEFAULT_TOLERANCE
        self._vocab = []
        self._player: PlayerController | None = None
        self._phases = []
        self._duration = 0.0
        self._rounds = 0
        self._started_at = ""
        self._who = "A"
        self._names = {"A": None, "B": None}
        self._pending_duration = None
        self._all_moments = []       # every "Mark this moment" in the recording
        self._moments = []           # … those this labeller has not named yet
        self._focus_getter = None
        self._loader = None
        self._loaded = False
        self._unsaved = False
        self.setObjectName("reviewScreen")
        self.setStyleSheet(f"#reviewScreen {{ background: {dt.canvas_bg()}; }}")
        self._build()
        self._retry_timer = QTimer(self); self._retry_timer.setInterval(5000)
        self._retry_timer.timeout.connect(self._retry_save)
        self._others_timer = QTimer(self); self._others_timer.setInterval(self.OTHERS_POLL_MS)
        self._others_timer.timeout.connect(self._reload_others)

    # ---- construction ----
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(self._build_topbar())
        body = QHBoxLayout(); body.setContentsMargins(16, 12, 16, 12); body.setSpacing(12)

        # Left: the videos + brain activity scroll; the transport and timeline
        # are PINNED below, so the tools you navigate with never scroll away.
        left = QVBoxLayout(); left.setSpacing(10)
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); content.setObjectName("reviewContent")
        content.setStyleSheet(f"#reviewContent {{ background: {dt.canvas_bg()}; }}")
        cv = QVBoxLayout(content); cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(10)

        self._grid = VideoGrid([("", "Side camera 1"), ("", "Side camera 2"),
                                ("", "Boxer A view"), ("", "Boxer B view")])
        grid_wrap = QFrame()
        grid_wrap.setObjectName("videoWrap")
        grid_wrap.setStyleSheet(f"#videoWrap {{ background: {dt.stage()}; "
                                f"border-radius: {dt.RADIUS_LG}px; }}")
        gw = QVBoxLayout(grid_wrap); gw.setContentsMargins(6, 6, 6, 6); gw.addWidget(self._grid)
        grid_wrap.setMinimumHeight(220)
        cv.addWidget(grid_wrap, 1)

        self._brain = BrainActivityCard()
        cv.addWidget(self._brain)
        self._scroll.setWidget(content)
        left.addWidget(self._scroll, 1)
        left.addWidget(self._build_dock())          # pinned: transport + timeline
        body.addLayout(left, 1)

        self._labels_panel = self._build_labels_panel()
        body.addWidget(self._labels_panel)
        root.addLayout(body, 1)

    def _build_topbar(self):
        bar = QFrame(); bar.setFixedHeight(60); bar.setObjectName("topBar")
        bar.setStyleSheet(f"#topBar {{ background: {dt.surface()}; "
                          f"border-bottom: 1px solid {dt.border()}; }}")
        h = QHBoxLayout(bar); h.setContentsMargins(12, 8, 16, 8); h.setSpacing(10)
        back = ui.ghost_button("Sessions", icon="arrow-left")
        back.setMinimumHeight(40)
        back.clicked.connect(self.back_to_sessions.emit)
        h.addWidget(back)
        tv = QVBoxLayout(); tv.setSpacing(0); tv.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel("Review & Label")
        self._title.setStyleSheet(dt.css_text(dt.SIZE_LG, 700))
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._subtitle = QLabel("Open a recording from Sessions")
        self._subtitle.setStyleSheet(dt.css_text(dt.SIZE_XS, 400, dt.text_tertiary()))
        self._subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tv.addWidget(self._title); tv.addWidget(self._subtitle)
        h.addLayout(tv, 1)
        self._autosave = ui.StatusLine("All labels saved", "ok", size=dt.SIZE_SM, wrap=False)
        self._autosave.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        h.addWidget(self._autosave)
        help_btn = ui.help_button()
        help_btn.clicked.connect(self.help_requested.emit)
        h.addWidget(help_btn)
        self._export_btn = ui.primary_button("Export for analysis", icon="download")
        self._export_btn.setMinimumHeight(40)
        self._export_btn.clicked.connect(self._open_export)
        h.addWidget(self._export_btn)
        return bar

    def _build_dock(self):
        """Transport + timeline, always visible under the videos."""
        dock = QFrame(); dock.setObjectName("dock")
        dock.setStyleSheet(dt.qss_card("dock"))
        v = QVBoxLayout(dock); v.setContentsMargins(14, 10, 14, 10); v.setSpacing(8)
        h = QHBoxLayout(); h.setSpacing(6)
        self._back_btn = self._tbtn("step-back", "Back one frame (←)", lambda: self._step(-1))
        self._play_btn = QPushButton()
        self._play_btn.setFixedSize(48, 40)
        self._play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._play_btn.setToolTip("Play / pause (Space)")
        self._play_btn.setAccessibleName("Play or pause")
        self._play_btn.setStyleSheet(dt.qss_primary_button() + " QPushButton { padding: 0; }")
        self._play_btn.clicked.connect(self._toggle_play)
        self._fwd_btn = self._tbtn("step-forward", "Forward one frame (→)", lambda: self._step(1))
        h.addWidget(self._back_btn); h.addWidget(self._play_btn); h.addWidget(self._fwd_btn)
        h.addSpacing(8)
        self._time_lbl = ui.TimerLabel(px=17)
        self._time_lbl.set_template("00:00.00 / 00:00.00")
        self._time_lbl.setText("00:00.00 / 00:00.00")
        h.addWidget(self._time_lbl)
        self._phase_chip = QLabel("")
        self._phase_chip.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt.text_secondary()))
        self._phase_chip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        h.addWidget(self._phase_chip, 1)
        for text, tip, step in (("‹", "Jump to the previous label (↑)", -1),
                                ("›", "Jump to the next label (↓)", 1)):
            b = ui.secondary_button(text, compact=True, tip=tip)
            b.setFixedSize(36, 34); b.setAccessibleName(tip)
            b.setStyleSheet(dt.qss_secondary_button(True) +
                            " QPushButton { padding: 0 0 3px 0; font-size: 18px; }")
            b.clicked.connect(lambda _c=False, d=step: self._jump_label(d))
            h.addWidget(b)
        h.addSpacing(4)
        self._speed = ui.Segmented([(lbl, s) for s, lbl in _SPEEDS], current=1.0, height=34)
        self._speed.setToolTip("Playback speed")
        self._speed.changed.connect(self._set_speed)
        self._speed_group = self._speed       # (kept name: the buttons of the speed control)
        h.addWidget(self._speed)
        v.addLayout(h)
        self._strip = TimelineStrip()
        self._strip.seek_requested.connect(self._seek)
        v.addWidget(self._strip)
        self._timeline_card = dock
        self._set_play_icon(False)
        return dock

    def _tbtn(self, icon, tip, slot):
        b = ui.icon_button(icon, tip, size=40)
        b.clicked.connect(slot)
        return b

    def _set_play_icon(self, playing):
        self._play_btn.setIcon(QIcon(dt.icon_pixmap("pause" if playing else "play",
                                                    color="#FFFFFF", size=18)))
        self._play_btn.setIconSize(QSize(18, 18))

    def _build_labels_panel(self):
        panel = QFrame(); panel.setObjectName("labelPanel"); panel.setFixedWidth(self.PANEL_WIDTH)
        panel.setStyleSheet(dt.qss_card("labelPanel"))
        v = QVBoxLayout(panel); v.setContentsMargins(16, 14, 16, 12); v.setSpacing(8)

        # who is labelling
        idrow = QHBoxLayout(); idrow.setSpacing(8)
        idrow.addWidget(ui.heading("Label"))
        idrow.addStretch()
        self._labeller_chip = ui.ghost_button("", icon="user", compact=True,
                                              tip="Labels you place are saved under this name.")
        self._labeller_chip.clicked.connect(self._show_name_ask)
        idrow.addWidget(self._labeller_chip)
        v.addLayout(idrow)

        # "Your name", asked once
        self._name_ask = ui.Card(tone="info", radius=dt.RADIUS_MD)
        na = QVBoxLayout(self._name_ask); na.setContentsMargins(12, 10, 12, 12); na.setSpacing(8)
        na.addWidget(ui.body("Who is labelling?", color=dt.text_primary(), weight=700))
        na.addWidget(ui.muted("Your labels are saved under your name, separately from anyone "
                              "else's, so two people can label the same recording.",
                              size=dt.SIZE_XS))
        self._name_edit = QLineEdit(); self._name_edit.setPlaceholderText("Your name")
        self._name_edit.setMinimumHeight(40); self._name_edit.setStyleSheet(dt.qss_input())
        self._name_edit.returnPressed.connect(self._confirm_name)
        na.addWidget(self._name_edit)
        self._name_ok = ui.primary_button("Start labelling")
        self._name_ok.clicked.connect(self._confirm_name)
        na.addWidget(self._name_ok)
        v.addWidget(self._name_ask)

        # 1 · who did it
        v.addWidget(ui.caption("Who did it?"))
        who_row = QHBoxLayout(); who_row.setSpacing(6)
        self._who_btns = {}
        for which in ("A", "B", "Both"):
            b = QPushButton(which); b.setCheckable(True); b.setMinimumHeight(40)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, w=which: self._set_who(w))
            self._who_btns[which] = b; who_row.addWidget(b, 1)
        v.addLayout(who_row)

        # 2 · what happened
        v.addWidget(ui.caption("What happened?  ·  click or press the number"))
        self._label_grid = QGridLayout(); self._label_grid.setSpacing(6)
        v.addLayout(self._label_grid)
        self._pending_note = ui.Card(tone="warn", radius=dt.RADIUS_SM)
        pn = QHBoxLayout(self._pending_note); pn.setContentsMargins(10, 8, 10, 8)
        self._pending_text = ui.body("", color=dt._p().warning_text, size=dt.SIZE_XS, weight=500)
        pn.addWidget(self._pending_text, 1)
        self._pending_note.setVisible(False)
        v.addWidget(self._pending_note)
        self._clinch_note = ui.muted("", size=dt.SIZE_XS)
        v.addWidget(self._clinch_note)
        v.addWidget(ui.hline())

        # My labels | Compare
        tabs = QHBoxLayout(); tabs.setSpacing(6)
        self._tabs = ui.Segmented([("My labels", "mine"), ("Compare", "compare")],
                                  current="mine", height=32)
        self._tabs.changed.connect(self._on_tab)
        tabs.addWidget(self._tabs); tabs.addStretch()
        self._undo_btn = ui.icon_button("undo", f"Undo ({platform_utils.shortcut('Z')})", size=32)
        self._undo_btn.clicked.connect(self.undo)
        tabs.addWidget(self._undo_btn)
        v.addLayout(tabs)

        self._tab_stack = QStackedWidget()
        self._tab_stack.addWidget(self._build_mine_tab())
        self._tab_stack.addWidget(self._build_compare_tab())
        v.addWidget(self._tab_stack, 1)

        z = platform_utils.shortcut("Z")
        helpl = ui.muted(f"Space play/pause · ← → one frame (Shift: 1 second) · ↑ ↓ previous/next "
                         f"label · 1–9 place a label · Tab switch boxer · {z} undo",
                         size=dt.SIZE_CAPTION)
        v.addWidget(helpl)
        self._set_who("A")
        self._refresh_identity()
        return panel

    def _scroll_list(self, name):
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget(); host.setObjectName(name)
        host.setStyleSheet(f"#{name} {{ background: {dt.card_bg()}; }}")
        lay = QVBoxLayout(host); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
        lay.addStretch()
        scroll.setWidget(host)
        return scroll, host, lay

    def _build_mine_tab(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
        self._placed_head = ui.muted("No labels yet", size=dt.SIZE_XS, wrap=False)
        v.addWidget(self._placed_head)
        self._legacy_note = ui.Card(tone="info", radius=dt.RADIUS_SM)
        ln = QVBoxLayout(self._legacy_note); ln.setContentsMargins(10, 8, 10, 8); ln.setSpacing(6)
        self._legacy_text = ui.body("", color=dt.text_secondary(), size=dt.SIZE_XS)
        ln.addWidget(self._legacy_text)
        self._legacy_btn = ui.secondary_button("These are mine", compact=True)
        self._legacy_btn.clicked.connect(self._claim_legacy)
        ln.addWidget(self._legacy_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._legacy_note.setVisible(False)
        v.addWidget(self._legacy_note)
        self._placed_scroll, self._placed_host, self._placed_layout = self._scroll_list("placedHost")
        v.addWidget(self._placed_scroll, 1)
        return w

    def _build_compare_tab(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
        row = QHBoxLayout(); row.setSpacing(6)
        self._compare_combo = QComboBox(); self._compare_combo.setFixedHeight(30)
        self._compare_combo.setStyleSheet(dt.qss_combo(compact=True))
        self._compare_combo.setToolTip("Whose labels to show beside yours")
        self._compare_combo.currentIndexChanged.connect(self._on_compare_changed)
        row.addWidget(self._compare_combo, 1)
        self._tol_combo = _small_combo(
            [(f"within {t:g} s", t) for t in label_agreement.TOLERANCES], self._tolerance,
            "Two labels count as the same event when they are the same label for the same "
            "boxer and start within this long of each other.")
        self._tol_combo.setFixedHeight(30)
        self._tol_combo.currentIndexChanged.connect(self._on_tolerance_changed)
        row.addWidget(self._tol_combo)
        v.addLayout(row)
        self._agree_card = ui.Card(tone="subtle", radius=dt.RADIUS_SM)
        ac = QVBoxLayout(self._agree_card); ac.setContentsMargins(10, 8, 10, 8); ac.setSpacing(2)
        self._agree_text = ui.body("", color=dt.text_primary(), size=dt.SIZE_SM, weight=500)
        self._agree_sub = ui.muted("", size=dt.SIZE_XS)
        ac.addWidget(self._agree_text); ac.addWidget(self._agree_sub)
        v.addWidget(self._agree_card)
        self._diff_scroll, self._diff_host, self._diff_layout = self._scroll_list("diffHost")
        v.addWidget(self._diff_scroll, 1)
        return w

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._brain.fit_to(self._scroll.viewport().height())

    def showEvent_fit(self):
        self._brain.fit_to(self._scroll.viewport().height())

    # ---- who is labelling ----
    def labeller(self) -> str:
        return self._labeller

    def _refresh_identity(self):
        named = bool(self._labeller)
        self._labeller_chip.setText(self._labeller if named else "Set your name")
        self._name_ask.setVisible(not named)
        for b in getattr(self, "_label_btn_widgets", []):
            b.setEnabled(named)

    def _show_name_ask(self):
        self._name_edit.setText(self._labeller)
        self._name_ask.setVisible(True)
        self._name_edit.setFocus(); self._name_edit.selectAll()

    def _confirm_name(self):
        name = " ".join(self._name_edit.text().split())
        if not name:
            self._name_edit.setFocus(); return
        name = app_settings.set_labeller_name(name)
        self.set_labeller(name)
        self.labeller_changed.emit(name)

    def set_labeller(self, name: str) -> None:
        """Label as this person from now on. An open recording switches to
        their file (the previous labeller's labels were already saved)."""
        name = name or ""
        changed = not labels_store.same_labeller(name, self._labeller) or name != self._labeller
        self._labeller = name
        self._refresh_identity()
        if changed and self._root:
            self._open_labels()
            self._refresh_all_labels()

    def set_names_hidden(self, hidden: bool) -> None:
        if bool(hidden) == self._hide_names:
            return
        self._hide_names = bool(hidden)
        self._update_who_button_labels()
        if self._root:
            self._refresh_titles()
            self._brain.set_names(self._brain_names())

    def _shown(self, which):
        return privacy.shown_name(which, self._names.get(which), self._hide_names)

    def _has_name(self, which):
        return bool(self._names.get(which)) and not self._hide_names

    def _brain_names(self):
        return [f"Boxer {w}" + (f" · {self._names[w]}" if self._has_name(w) else "")
                for w in ("A", "B")]

    # ---- loading (labels-first; media in the background) ----
    def load_session_folder(self, root: str):
        self.shutdown()
        self._root = root
        self._loaded = False
        self._labeller = app_settings.labeller_name() or self._labeller
        self._hide_names = app_settings.hide_names_on_screen() if self._root else self._hide_names
        self._refresh_identity()
        recordings_dir = os.path.dirname(os.path.abspath(root))
        self._vocab = label_vocab.load(recordings_dir)
        self._rebuild_label_buttons()

        man = session_storage._read_manifest(root) or {}
        self._names = privacy.boxer_names(man)
        self._started_at = man.get("started_at", "") or ""
        cfg = man.get("config") or {}
        self._rounds = int(cfg.get("num_rounds") or 0)
        self._update_who_button_labels()

        # FAST: phases + labels + timeline render immediately
        self._phases = read_phase_markers(root)
        self._duration = self._phases[-1][0] if self._phases else 0.0
        self._all_moments = session_storage.read_moments(root)
        self._open_labels()
        self._strip.set_session(self._duration, self._phases, self._timeline_labels())
        self._refresh_all_labels()
        self._refresh_titles()
        self._brain.set_loading()
        try:
            session_history.log_event(root, session_history.EVENT_OPENED_FOR_REVIEW)
        except Exception:
            pass
        self._others_timer.start()

        # SLOW: cameras (sidecars) + EEG CSVs on a background thread
        specs = _discover_cameras(root)
        self._swap_grid(specs)
        eeg_plan = self._eeg_plan(root)
        self._loader = _MediaLoadWorker(root, specs, eeg_plan, self)
        self._loader.done.connect(self._on_media_loaded)
        self._loader.start()

    def _open_labels(self):
        """Open MY labels file for this recording (migrating an old single
        labels.json first) and read everybody else's."""
        root = self._root
        had_legacy = os.path.isfile(os.path.join(root, labels_store.JSON_NAME))
        owner = labels_store.migrate_legacy(root) if had_legacy else None
        if owner:
            try:
                session_history.log_event(root, session_history.EVENT_LABELS_MIGRATED,
                                          {"to_labeller": owner})
            except Exception:
                pass
        if self._labeller:
            self._store = labels_store.open_for_labeller(root, self._labeller)
        else:
            self._store = labels_store.LabelsStore(labeller="")
        self._pending_duration = None
        self._unsaved = False
        self._mark_saved()
        self._reload_others(refresh=False)

    def _reload_others(self, refresh=True):
        """Re-read the other labellers' files (they may be labelling right now
        on another computer)."""
        if not self._root:
            return
        try:
            everyone = labels_store.read_all(self._root)
        except Exception:
            return
        others = {n: labs for n, labs in everyone.items()
                  if not (self._labeller and labels_store.same_labeller(n, self._labeller))}
        if others == self._others and refresh:
            return
        self._others = others
        self._fill_compare_combo()
        if refresh:
            self._refresh_compare()
            self._refresh_legacy_note()

    def _refresh_titles(self):
        a, b = self._names.get("A"), self._names.get("B")
        when = platform_utils.format_iso(self._started_at,
                                         fallback=os.path.basename(self._root or ""))
        if self._hide_names:
            vs = privacy.versus(a, b, True)
        else:
            vs = f"{a} vs {b}" if a and b else (a or b or "No names")
        self._title.setText(f"{when} · {vs}")
        mins = max(1, round(self._duration / 60)) if self._duration else 0
        self._subtitle.setText(self._subtitle_text(mins, self._rounds))

    def _subtitle_text(self, mins, rounds, a=None, b=None):
        bits = [f"{mins} min", f"{rounds} round{'s' if rounds != 1 else ''}"]
        n = len(self._others)
        if n:
            bits.append(f"also labelled by {', '.join(self._others)}" if n <= 2
                        else f"also labelled by {n} others")
        return " · ".join(bits)

    def _eeg_plan(self, root) -> dict:
        """{0: Boxer A's EEG file, 1: Boxer B's} from the recording's manifest."""
        files = session_storage.eeg_files(root)
        return {i: files[who] for i, who in enumerate(("A", "B")) if who in files}

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
        self._player.set_speed(self._speed.value())
        self._strip.set_session(self._duration, self._phases, self._timeline_labels())
        self._brain.load(ordered, self._brain_names())
        self._refresh_titles()
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
        ui.clear_layout(self._label_grid)
        self._label_buttons = {}
        self._label_btn_widgets = []
        long_ones = []
        for i, entry in enumerate(self._vocab):
            key = entry.get("key", i + 1)
            b = QPushButton(f"{key}   {entry['name']}")
            b.setMinimumHeight(40); b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_label_button_qss(entry.get("color", dt.accent())))
            lasts = entry.get("kind") == label_vocab.DURATION
            b.setToolTip(f"Press {key}" + (" when it starts and again when it ends"
                                          if lasts else " to place it at the current moment"))
            b.clicked.connect(lambda _c=False, e=entry: self._place_from_vocab(e))
            b.setEnabled(bool(self._labeller))
            self._label_grid.addWidget(b, i // 2, i % 2)
            self._label_buttons[key] = entry
            self._label_btn_widgets.append(b)
            if lasts:
                long_ones.append(entry["name"])
        if long_ones:
            names = " and ".join(long_ones) if len(long_ones) <= 2 else \
                ", ".join(long_ones[:-1]) + " and " + long_ones[-1]
            verb = "lasts" if len(long_ones) == 1 else "last"
            self._clinch_note.setText(f"{names} {verb} a while: press the number when it "
                                      f"starts and again when it ends (Esc cancels).")
        self._clinch_note.setVisible(bool(long_ones))

    def _update_who_button_labels(self):
        for which in ("A", "B"):
            self._who_btns[which].setText(
                f"{which} · {self._names[which]}" if self._has_name(which) else f"Boxer {which}")
        self._who_btns["Both"].setText("Both")

    # ---- transport / playhead ----
    def _toggle_play(self):
        if self._player:
            self._player.toggle()

    def _step(self, n):
        if self._player:
            self._player.step(n)

    def _skip(self, seconds):
        t = (self._player.t if self._player else 0.0) + seconds
        self._seek(max(0.0, min(self._duration, t)))

    def _seek(self, t):
        if self._player:
            self._player.seek(t)
        else:
            self._on_playhead(t)

    def _set_speed(self, s):
        if self._player:
            self._player.set_speed(s)
        self._speed.set_value(s)

    def _jump_label(self, direction):
        """Move the playhead to the previous / next label (yours, and the other
        labeller's while comparing)."""
        t = self._player.t if self._player else 0.0
        times = sorted({float(l["t_start"]) for l in self._timeline_labels()}
                       | {float(l["t_start"]) for l in self._compare_labels()})
        if direction > 0:
            nxt = next((x for x in times if x > t + 0.02), None)
        else:
            nxt = next((x for x in reversed(times) if x < t - 0.02), None)
        if nxt is not None:
            self._seek(nxt)

    def _on_frames(self, t, frames):
        self._grid.set_frames(frames)

    def _on_playhead(self, t):
        self._brain.set_playhead(t)
        self._brain.update_contact(t)
        self._strip.set_playhead(t)
        self._time_lbl.setText(f"{_fmt_time(t)} / {_fmt_time(self._duration)}")
        text, pid = self._phase_text(t)
        self._phase_chip.setText(text)
        if pid != getattr(self, "_chip_pid", None):
            self._chip_pid = pid
            self._phase_chip.setStyleSheet(dt.css_text(
                dt.SIZE_SM, 600, dt.phase_fill(pid)[1] if pid else dt.text_secondary()))

    def _on_state(self, playing):
        self._set_play_icon(playing)

    def _phase_text(self, t):
        active = None
        for (mt, pid, label) in self._phases:
            if mt <= t + 1e-6:
                active = (mt, pid, label)
            else:
                break
        if active is None or active[1] == "done":
            return "", None
        return f"{friendly_phase(active[2])} · {_fmt_mmss(t - active[0])} in", active[1]

    # ---- labels ----
    def _set_who(self, which):
        self._who = which
        for w, btn in self._who_btns.items():
            btn.setStyleSheet(_who_qss(w, w == which))
            btn.setChecked(w == which)

    def _cycle_who(self):
        order = ["A", "B", "Both"]
        self._set_who(order[(order.index(self._who) + 1) % 3])

    def _place_from_vocab(self, entry):
        if not self._labeller:              # nobody to save the label under yet
            self._show_name_ask(); return
        t = self._player.t if self._player else 0.0
        if entry.get("kind", label_vocab.POINT) == label_vocab.DURATION:
            self._handle_duration(entry, t)
        else:
            self._commit_label(entry, t, None)

    def _handle_duration(self, entry, t):
        if self._pending_duration is None:
            self._pending_duration = (entry, t)
            self._pending_text.setText(
                f"{entry['name']} started at {_fmt_time(t)} — press {entry['key']} again "
                f"when it ends. Esc cancels.")
            self._pending_note.setVisible(True)
        else:
            start_entry, t0 = self._pending_duration
            self._pending_duration = None
            self._pending_note.setVisible(False)
            t_start, t_end = sorted((t0, t))
            self._commit_label(start_entry, t_start, t_end)

    def _cancel_pending(self):
        if self._pending_duration is not None:
            self._pending_duration = None
            self._pending_note.setVisible(False)

    def _commit_label(self, entry, t_start, t_end):
        lab = labels_store.new_label(
            label=entry["name"], who=self._who,
            kind=label_vocab.DURATION if t_end is not None else label_vocab.POINT,
            t_start=t_start, t_end=t_end, color=entry.get("color", dt.accent()),
            created_by=self._labeller)
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
        self._refresh_all_labels()

    def _refresh_all_labels(self):
        self._moments = [m for m in self._all_moments
                         if not self._store.moment_named(m["t_session_seconds"])]
        self._refresh_placed()
        self._strip.set_labels(self._timeline_labels())
        self._refresh_compare()
        self._refresh_legacy_note()
        self._brain.set_markers(self._eeg_markers())

    def _eeg_markers(self):
        """Every event of the recording as (time, text, colour) for the EEG
        display: phase starts, marked moments, and your placed labels."""
        pal = dt._p()
        fg = {"baseline": pal.phase_rest[1], "round": pal.phase_round[1],
              "break": pal.phase_break[1], "done": pal.phase_rest[1]}
        out = [(t, short_phase(label), fg.get(pid, dt.text_secondary()))
               for t, pid, label in self._phases]
        out += [(float(m["t_session_seconds"]), "Moment", dt.record_red()) for m in self._moments]
        for lab in self._store.labels:
            col = lab.get("color", dt.accent())
            text = f"{lab.get('label', '')} · {lab.get('who', '')}"
            out.append((float(lab["t_start"]), text, col))
            if lab.get("t_end") is not None:
                out.append((float(lab["t_end"]), f"{lab.get('label', '')} ends", col))
        return out

    # ---- marked moments (unnamed until named here) ----
    def _moment_pseudo_labels(self):
        """Moments rendered as unnamed point labels to be named here."""
        return [{"id": f"moment:{i}", "label": "Marked moment — click to name",
                 "who": "Both", "kind": "point",
                 "t_start": float(mo["t_session_seconds"]), "t_end": None,
                 "color": dt.record_red(), "moment": True}
                for i, mo in enumerate(self._moments)]

    def _timeline_labels(self):
        return list(self._store.labels) + self._moment_pseudo_labels()

    def _name_moment(self, moment_pseudo):
        if not self._labeller:
            self._show_name_ask(); return
        t = float(moment_pseudo["t_start"])
        dlg = _MomentNameDialog(self._vocab, {w: self._shown(w) if self._has_name(w) else None
                                              for w in ("A", "B")}, self)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.result_entry is None:
            return
        self._apply_moment_name(t, dlg.result_entry, dlg.result_who)

    def _apply_moment_name(self, t, entry, who):
        """Turn the marked moment at ``t`` into a normal label of mine. The mark
        itself stays in the recording, so every labeller can name it."""
        lab = labels_store.new_label(
            label=entry["name"], who=who, kind=label_vocab.POINT,
            t_start=t, t_end=None, color=entry.get("color", dt.accent()),
            created_by=self._labeller)
        self._store.add(lab)
        self._store.mark_moment_named(t)
        self._after_change(added=lab)

    # ---- saving ----
    def _autosave_and_history(self, added=None, removed=None):
        if not self._root or not self._labeller:
            return
        saved = self._save_now()
        try:
            for event, lab in ((session_history.EVENT_LABELS_ADDED, added),
                               (session_history.EVENT_LABELS_REMOVED, removed)):
                if lab is not None:
                    session_history.log_event(
                        self._root, event,
                        {"label": lab["label"], "who": lab.get("who"),
                         "t_start": lab.get("t_start"), "labeller": self._labeller},
                        user=self._labeller)
        except Exception:
            pass
        if saved:
            self._mark_saved()

    def _save_now(self) -> bool:
        try:
            self._store.save()
        except Exception:
            # Typically a shared drive that has gone away. Nothing is lost: the
            # labels stay in memory and saving is retried until it works.
            self._unsaved = True
            self._autosave.set("Not saved — the recordings folder can't be reached. "
                               "Keep this window open; saving is retried automatically.", "bad")
            self._retry_timer.start()
            return False
        self._unsaved = False
        self._retry_timer.stop()
        return True

    def _retry_save(self):
        if self._unsaved and self._save_now():
            self._mark_saved()

    def has_unsaved_labels(self) -> bool:
        return self._unsaved

    def _mark_saved(self):
        self._autosave.set("All labels saved", "ok")

    # ---- "My labels" ----
    def _refresh_placed(self):
        ui.clear_layout(self._placed_layout, keep_last=1)
        labels = sorted(self._store.labels, key=lambda x: x.get("created_at", ""), reverse=True)
        moments = self._moment_pseudo_labels()
        n = len(labels)
        head = f"{n} label{'s' if n != 1 else ''} placed, newest first" if n else \
            ("No labels yet — play the video and press a number when something happens."
             if self._labeller else "No labels yet")
        if moments:
            head = f"{len(moments)} marked moment{'s' if len(moments) != 1 else ''} to name · " + \
                   (f"{n} label{'s' if n != 1 else ''}" if n else "no labels yet")
        self._placed_head.setText(head)
        self._placed_head.setWordWrap(not n)
        self._tabs.button("mine").setText(f"My labels  {n}")
        self._undo_btn.setEnabled(self._store.can_undo())
        # Moments to name first (they need attention), then placed labels.
        for mo in moments:
            self._placed_layout.insertWidget(self._placed_layout.count() - 1, self._moment_row(mo))
        for lab in labels:
            self._placed_layout.insertWidget(self._placed_layout.count() - 1,
                                             self._label_row(lab, deletable=True))

    def _placed_row(self, lab):
        return self._moment_row(lab) if lab.get("moment") else self._label_row(lab, True)

    def _label_row(self, lab, deletable=False, tag=None):
        """time · who · colour · name [· tag] [· delete]. Click to jump there."""
        row = QFrame(); row.setObjectName("row")
        row.setStyleSheet(f"QFrame#row {{ background: {dt.surface()}; border: 1px solid "
                          f"{dt.border()}; border-radius: {dt.RADIUS_SM}px; }}"
                          f"QFrame#row:hover {{ background: {dt.surface_subtle()}; }}")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(row); h.setContentsMargins(8, 5, 4, 5); h.setSpacing(7)
        tstr = _fmt_time(lab["t_start"]) + (f"–{_fmt_time(lab['t_end'])}"
                                            if lab.get("t_end") is not None else "")
        t = QLabel(tstr)
        t.setStyleSheet(dt.css_text(dt.SIZE_XS, 500, dt.text_secondary()))
        who = lab.get("who", "")
        wl = QLabel(who)
        wcol = dt.boxer_colors(who)[2] if who in ("A", "B") else dt.text_tertiary()
        wl.setStyleSheet(dt.css_text(dt.SIZE_XS, 700, wcol))
        name = QLabel(lab.get("label", ""))
        name.setStyleSheet(dt.css_text(dt.SIZE_SM, 500))
        h.addWidget(t); h.addWidget(wl)
        h.addWidget(ui.Dot(lab.get("color", dt.accent()) if deletable or tag is None
                           else dt.other_labeller(), 9))
        h.addWidget(name, 1)
        if tag:
            tl = QLabel(tag); tl.setStyleSheet(dt.css_text(dt.SIZE_CAPTION, 600, dt.text_tertiary()))
            h.addWidget(tl)
        if deletable:
            dele = ui.icon_button("x", "Remove this label", size=26, color=dt.text_tertiary(),
                                  bordered=False)
            dele.clicked.connect(lambda _c=False, i=lab["id"]: self._delete_label(i))
            h.addWidget(dele)
        else:
            h.addSpacing(4)
        def _seek_to(_e, ts=lab["t_start"]):
            self._seek(ts)
        row.mousePressEvent = _seek_to
        return row

    def _moment_row(self, mo):
        """A 'Marked moment — click to name' row (DESIGN §6)."""
        row = QFrame(); row.setObjectName("row")
        row.setStyleSheet(
            f"QFrame#row {{ background: {dt._p().warning_bg}; border: 1px solid "
            f"{dt.warning_color()}; border-radius: {dt.RADIUS_SM}px; }}")
        h = QHBoxLayout(row); h.setContentsMargins(8, 7, 8, 7); h.setSpacing(7)
        t = QLabel(_fmt_time(mo["t_start"]))
        t.setStyleSheet(dt.css_text(dt.SIZE_XS, 500, dt._p().warning_text))
        name = QLabel("Marked moment — click to name")
        name.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt._p().warning_text))
        h.addWidget(t); h.addWidget(name, 1)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        def _click(_e, m=mo):
            self._seek(m["t_start"]); self._name_moment(m)
        row.mousePressEvent = _click
        return row

    # ---- labels from before labeller names ----
    def _refresh_legacy_note(self):
        legacy = [n for n in labels_store.legacy_sets(self._root)] if self._root else []
        self._legacy_name = legacy[0] if legacy else None
        show = bool(legacy) and bool(self._labeller)
        if show:
            n = len(self._others.get(self._legacy_name, [])) or len(self._store) \
                if labels_store.same_labeller(self._legacy_name, self._labeller) \
                else len(self._others.get(self._legacy_name, []))
            self._legacy_text.setText(
                f"This recording has {n} label{'s' if n != 1 else ''} from before the app "
                f"asked who was labelling (saved as “{self._legacy_name}”). If you placed "
                f"them, take them over.")
        self._legacy_note.setVisible(show)

    def _claim_legacy(self):
        name = getattr(self, "_legacy_name", None)
        if not (name and self._root and self._labeller):
            return
        try:
            moved = labels_store.claim_legacy(self._root, name, self._store)
            session_history.log_event(self._root, session_history.EVENT_LABELS_CLAIMED,
                                      {"from": name, "labels": moved, "labeller": self._labeller},
                                      user=self._labeller)
        except Exception:
            return
        self._reload_others(refresh=False)
        self._refresh_all_labels()

    # ---- Compare ----
    def _on_tab(self, key):
        self._tab_stack.setCurrentIndex(0 if key == "mine" else 1)
        self._undo_btn.setVisible(key == "mine")
        self._refresh_compare()

    def comparing(self) -> bool:
        return self._tabs.value() == "compare" and bool(self._compare_with)

    def _fill_compare_combo(self):
        names = list(self._others)
        self._compare_combo.blockSignals(True)
        self._compare_combo.clear()
        for n in names:
            self._compare_combo.addItem(f"{n}  ({len(self._others[n])})", n)
        if self._compare_with not in names:
            self._compare_with = names[0] if names else None
        if self._compare_with:
            self._compare_combo.setCurrentIndex(self._compare_combo.findData(self._compare_with))
        self._compare_combo.blockSignals(False)
        self._compare_combo.setEnabled(bool(names))
        self._tabs.button("compare").setText(f"Compare  {len(names)}" if names else "Compare")

    def _on_compare_changed(self, _i):
        self._compare_with = self._compare_combo.currentData()
        self._refresh_compare()

    def _on_tolerance_changed(self, _i):
        self._tolerance = float(self._tol_combo.currentData())
        self._refresh_compare()

    def _compare_labels(self):
        return list(self._others.get(self._compare_with, [])) if self.comparing() else []

    def agreement(self):
        """How far my labels and the compared labeller's agree (or None)."""
        if not self._compare_with:
            return None
        return label_agreement.compare(self._store.labels, self._others.get(self._compare_with, []),
                                       self._labeller or "You", self._compare_with, self._tolerance)

    def _refresh_compare(self):
        on = self.comparing()
        self._strip.set_others([(self._compare_with, self._others[self._compare_with])] if on else [])
        if self._tabs.value() != "compare":
            return
        ui.clear_layout(self._diff_layout, keep_last=1)
        self._tol_combo.setEnabled(bool(self._compare_with))
        if not self._compare_with:
            self._agree_text.setText("Nobody else has labelled this recording yet.")
            self._agree_sub.setText("When another person labels it — on this computer or on "
                                    "another one that uses the same recordings folder — their "
                                    "labels appear here beside yours.")
            return
        ag = self.agreement()
        self._agree_text.setText(ag.sentence(a_is_you=True))
        self._agree_sub.setText(
            f"“The same event” means the same label for the same boxer, starting within "
            f"{self._tolerance:g} s of each other."
            + (f" On those, you differ by {ag.mean_time_difference:.2f} s on average."
               if ag.matched else ""))
        rows = [(lab, "only you") for lab in ag.only_a] + \
               [(lab, f"only {self._compare_with}") for lab in ag.only_b]
        rows.sort(key=lambda r: float(r[0]["t_start"]))
        for lab, tag in rows:
            mine = tag == "only you"
            self._diff_layout.insertWidget(self._diff_layout.count() - 1,
                                           self._label_row(lab, deletable=False,
                                                           tag=tag if not mine else "only you"))
        if not rows and ag.total:
            self._diff_layout.insertWidget(0, ui.muted("No differences — every event was marked "
                                                       "by both of you.", size=dt.SIZE_XS))

    # ---- keyboard ----
    def _typing_now(self) -> bool:
        from PyQt6.QtWidgets import QTextEdit, QPlainTextEdit
        w = (self._focus_getter() if self._focus_getter else QApplication.focusWidget())
        return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit))

    def handle_key(self, key, modifiers) -> bool:
        if self._typing_now():
            return False
        ctrl = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if ctrl and key == Qt.Key.Key_Z:
            self.redo() if shift else self.undo(); return True
        if ctrl:
            return False                       # leave other shortcuts to the app
        if key == Qt.Key.Key_Space:
            self._toggle_play(); return True
        if key == Qt.Key.Key_Left:
            self._skip(-1.0) if shift else self._step(-1); return True
        if key == Qt.Key.Key_Right:
            self._skip(1.0) if shift else self._step(1); return True
        if key == Qt.Key.Key_Up:
            self._jump_label(-1); return True
        if key == Qt.Key.Key_Down:
            self._jump_label(1); return True
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
        if event.type() == QEvent.Type.KeyPress and self.isVisible() \
                and QApplication.activeModalWidget() is None:
            if self.handle_key(event.key(), event.modifiers()):
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, e):
        QApplication.instance().installEventFilter(self); super().showEvent(e)
        QTimer.singleShot(0, self.showEvent_fit)

    def hideEvent(self, e):
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
        super().hideEvent(e)

    # ---- export ----
    def _open_export(self):
        if not self._root:
            return
        if self._unsaved:
            self._save_now()
        from brain_battle.data.export_module import ExportOptionsDialog
        ExportOptionsDialog(self._root, None, self._phases, self).exec()

    # ---- teardown ----
    def shutdown(self):
        self._others_timer.stop()
        self._retry_timer.stop()
        if self._unsaved and self._root:
            self._save_now()
        if self._loader is not None and self._loader.isRunning():
            self._loader.wait(3000)
        self._loader = None
        if self._player is not None:
            try:
                self._player.shutdown()
            except Exception:
                pass
            self._player = None


class _MomentNameDialog(QDialog):
    """Name a marked moment (DESIGN §6): pick who did it, then the label. The
    chosen label turns the moment into a normal label."""

    def __init__(self, vocab, names, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Name this moment")
        self.setMinimumWidth(400)
        self.result_entry = None
        self.result_who = "Both"
        v = QVBoxLayout(self); v.setContentsMargins(22, 20, 22, 20); v.setSpacing(10)
        v.addWidget(ui.heading("Name this moment", size=dt.SIZE_XL))
        v.addWidget(ui.caption("Who did it?"))
        who_row = QHBoxLayout(); who_row.setSpacing(6)
        self._who_btns = {}
        for which in ("A", "B", "Both"):
            text = (f"{which} · {names.get(which)}" if which in ("A", "B") and names.get(which)
                    else (which if which == "Both" else f"Boxer {which}"))
            b = QPushButton(text); b.setCheckable(True); b.setMinimumHeight(40)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, w=which: self._set_who(w))
            self._who_btns[which] = b; who_row.addWidget(b)
        v.addLayout(who_row)
        v.addWidget(ui.caption("What happened?"))
        grid = QGridLayout(); grid.setSpacing(6)
        for i, entry in enumerate(vocab):
            btn = QPushButton(f"{entry.get('key', i + 1)}   {entry['name']}")
            btn.setMinimumHeight(40); btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(_label_button_qss(entry.get("color", dt.accent())))
            btn.clicked.connect(lambda _c=False, e=entry: self._pick(e))
            grid.addWidget(btn, i // 2, i % 2)
        v.addLayout(grid)
        self._set_who("Both")

    def _set_who(self, which):
        self.result_who = which
        for w, b in self._who_btns.items():
            b.setChecked(w == which)
            b.setStyleSheet(_who_qss(w, w == which))

    def _pick(self, entry):
        self.result_entry = entry
        self.accept()


__all__ = ["ReviewLabelScreen", "read_phase_markers", "friendly_phase", "short_phase",
           "_discover_cameras", "_fmt_time"]
