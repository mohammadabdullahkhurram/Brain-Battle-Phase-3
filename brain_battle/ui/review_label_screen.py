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

from PyQt6.QtCore import Qt, pyqtSignal, QRect, QRectF, QPointF, QEvent, QThread, QTimer, QSize
from PyQt6.QtGui import QImage, QPainter, QColor, QPen, QFont, QIcon
from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QSizePolicy, QToolTip, QApplication,
    QDialog, QStackedWidget, QLineEdit, QTextEdit,
)

import brain_battle.ui.design_tokens as dt
import brain_battle.ui.widgets as ui
import brain_battle.ui.eeg_viewer as eeg_viewer
from brain_battle.data.signals import EEG_CHANNELS
import brain_battle.data.app_settings as app_settings
import brain_battle.data.label_agreement as label_agreement
import brain_battle.data.label_vocab as label_vocab
import brain_battle.data.labels_store as labels_store
import brain_battle.data.fighters as fighters
import brain_battle.capture.contact_quality as cq
import brain_battle.data.session_history as session_history
import brain_battle.data.session_storage as session_storage
from brain_battle.data.session_storage import read_phase_markers, friendly_phase, short_phase
from brain_battle.playback.pyav_player import open_session_cameras, PlayerController
from brain_battle.support import platform_utils
from brain_battle.ui.fonts import register_fonts


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
    c = ui.Dropdown(items, compact=True)
    c.setCurrentIndex(max(0, c.findData(current)))
    c.setToolTip(tip)
    c.setFixedHeight(28)
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
        h = max(self.HEIGHT_MIN, min(self.HEIGHT_NORMAL, int(available * 0.40)))
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

def _tint(hex_colour: str, amount: float) -> str:
    """The colour mixed with white (amount = how much colour), like CSS
    color-mix(in srgb, c 10%, white)."""
    c = QColor(hex_colour)
    mix = lambda v: int(round(255 - (255 - v) * amount))
    return QColor(mix(c.red()), mix(c.green()), mix(c.blue())).name()


class LaneTimeline(QWidget):
    """The whole recording as lanes (design F): **Rounds** (lead-in, baseline,
    rounds, breaks), **System** (a tick per automatic marker), **A · code**,
    **B · code**, **Both** (label chips; a label that lasts a while is as wide
    as it lasts; another labeller's are faded while comparing), **Notes**
    (custom notes) and **Moments** (⚑ marked moments, with their note or "add
    a note"). One red playhead. Click empty space to seek; click a chip or a
    tick to select it."""
    seek_requested = pyqtSignal(float)
    selected = pyqtSignal(str, object)        # kind ("label"|"note"|"moment"|"system"), key
    LABEL_W = 76
    LANES = (("rounds", 20), ("system", 16), ("A", 23), ("B", 23), ("Both", 23),
             ("notes", 23), ("moments", 23))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 0.001
        self.spans = []                # (t0, t1, label)
        self.system = []               # (t, label)
        self.labels = []               # my labels (incl. notes)
        self.others = []               # [(name, labels)] while comparing
        self.moments = []              # [{"t_session_seconds", "note"}]
        self.names = {"A": "A", "B": "B"}
        self.colours = {"A": dt.boxer_colors("A")[0], "B": dt.boxer_colors("B")[0]}
        self.selection = None          # (kind, key)
        self._t = 0.0
        self._hits = []                # (QRectF, kind, key)
        self.setMinimumHeight(self._height())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _height(self):
        return sum(h + 5 for _k, h in self.LANES) + 20

    # data
    def set_session(self, duration, phases, labels=None):
        self.duration = max(0.001, float(duration or 0))
        self.spans = session_storage.phase_spans(phases, duration)
        self.system = [(float(t), lab) for t, _pid, lab in phases]
        if labels is not None:
            self.labels = list(labels)
        self.update()

    def set_labels(self, labels):
        self.labels = list(labels); self.update()

    def set_others(self, others):
        self.others = list(others); self.update()

    def set_moments(self, moments):
        self.moments = list(moments); self.update()

    def set_fighters(self, names, colours):
        self.names = dict(names); self.colours = dict(colours); self.update()

    def set_playhead(self, t):
        self._t = float(t); self.update()

    def set_selection(self, sel):
        self.selection = sel; self.update()

    # geometry
    def _plot_x0(self):
        return float(self.LABEL_W)

    def _x(self, t):
        w = self.width() - self.LABEL_W - 4
        return self.LABEL_W + max(0.0, min(1.0, t / self.duration)) * w

    def _t_at(self, x):
        w = max(1.0, self.width() - self.LABEL_W - 4)
        return max(0.0, min(self.duration, (x - self.LABEL_W) / w * self.duration))

    def lane_rect(self, key):
        y = 0
        for k, h in self.LANES:
            if k == key:
                return QRectF(self.LABEL_W, y, self.width() - self.LABEL_W - 4, h)
            y += h + 5
        return QRectF()

    def mousePressEvent(self, e):
        pos = e.position()
        for rect, kind, key in reversed(self._hits):
            if rect.contains(pos):
                self.selection = (kind, key)
                self.selected.emit(kind, key)
                self.update()
                return
        if pos.x() >= self.LABEL_W:
            self.selection = None
            self.seek_requested.emit(self._t_at(pos.x()))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and e.position().x() >= self.LABEL_W:
            self.seek_requested.emit(self._t_at(e.position().x()))

    # painting
    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._hits = []
            small = QFont("Inter"); small.setPixelSize(12)
            chipf = QFont("Inter"); chipf.setPixelSize(11); chipf.setWeight(QFont.Weight.DemiBold)
            titles = {"rounds": ("Rounds", dt.text_tertiary(), 400),
                      "system": ("System", dt.text_tertiary(), 400),
                      "A": (f"A · {self.names.get('A', 'A')}", self.colours["A"], 700),
                      "B": (f"B · {self.names.get('B', 'B')}", self.colours["B"], 700),
                      "Both": ("Both", dt.text_secondary(), 700),
                      "notes": ("Notes", dt.text_secondary(), 600),
                      "moments": ("Moments", dt.warning_color(), 600)}
            bgs = {"rounds": None, "system": "#FAFBFC", "A": None, "B": None, "Both": "#F4F5F8",
                   "notes": "#F6F7FA", "moments": "#FFFAF3"}
            for key, h in self.LANES:
                r = self.lane_rect(key)
                text, col, wt = titles[key]
                f = QFont("JetBrains Mono" if key in ("A", "B") else "Inter")
                f.setPixelSize(12); f.setWeight(QFont.Weight(wt))
                p.setFont(f); p.setPen(QColor(col))
                p.drawText(QRectF(0, r.y(), self.LABEL_W - 6, r.height()),
                           Qt.AlignmentFlag.AlignVCenter, text)
                bg = bgs[key] or (_tint(self.colours[key], 0.10) if key in ("A", "B") else None)
                if bg:
                    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(bg))
                    p.drawRoundedRect(r, 6, 6)
            # rounds
            r = self.lane_rect("rounds")
            p.setFont(chipf)
            for t0, t1, lab in self.spans:
                u = (lab or "").upper()
                x0, x1 = self._x(t0), self._x(min(t1, self.duration))
                rr = QRectF(x0, r.y(), max(1.0, x1 - x0 - 2), r.height())
                if u.startswith("ROUND"):
                    bg, fg = "#DCE6FB", dt.accent_hover()
                elif u.startswith("RECORDING_START"):
                    bg, fg = "#F6F7FA", dt.text_tertiary()
                else:
                    bg, fg = "#EEF1F6", dt.text_tertiary()
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(bg)); p.drawRoundedRect(rr, 4, 4)
                name = "" if u.startswith("RECORDING_START") else friendly_phase(lab)
                if name and p.fontMetrics().horizontalAdvance(name) + 6 < rr.width():
                    p.setPen(QColor(fg)); p.drawText(rr, Qt.AlignmentFlag.AlignCenter, name)
            # system ticks
            r = self.lane_rect("system")
            for i, (t, lab) in enumerate(self.system):
                x = self._x(t)
                sel = self.selection == ("system", i)
                p.setPen(QPen(QColor(dt.text_primary() if sel else dt.system_marker()), 3 if sel else 2))
                p.drawLine(QPointF(x, r.y() + 2), QPointF(x, r.bottom() - 2))
                self._hits.append((QRectF(x - 4, r.y(), 8, r.height()), "system", i))
            # labels (mine + others faded)
            def chip(lab, faded=False):
                if lab.get("type") == "note":
                    lane, text = "notes", "▢ " + str(lab.get("text", ""))[:26]
                    col = dt.text_secondary()
                else:
                    lane, text = (lab.get("who") or "Both"), str(lab.get("label", ""))
                    col = lab.get("color", dt.accent())
                r = self.lane_rect(lane if lane in ("A", "B", "Both", "notes") else "Both")
                x0 = self._x(float(lab["t_start"]))
                fm = p.fontMetrics()
                w = fm.horizontalAdvance(text) + 14
                if lab.get("t_end") is not None:
                    w = max(w, self._x(float(lab["t_end"])) - x0)
                rr = QRectF(x0, r.y() + 2, w, r.height() - 4)
                sel = self.selection == ("note" if lane == "notes" else "label", lab.get("id"))
                c = QColor(col)
                if faded:
                    c.setAlpha(80)
                if lane == "notes":
                    p.setBrush(QColor("#FFFFFF")); p.setPen(QPen(c, 1))
                else:
                    p.setBrush(c); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(rr, 5, 5)
                if sel:
                    p.setPen(QPen(QColor(dt.text_primary()), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(rr.adjusted(-1, -1, 1, 1), 6, 6)
                p.setPen(QColor(dt.text_secondary() if lane == "notes" else "#FFFFFF"))
                p.drawText(rr.adjusted(6, 0, -3, 0), Qt.AlignmentFlag.AlignVCenter,
                           fm.elidedText(text, Qt.TextElideMode.ElideRight, int(rr.width() - 8)))
                if not faded:
                    self._hits.append((rr, "note" if lane == "notes" else "label", lab.get("id")))
            p.setFont(chipf)
            for _name, labs in self.others:
                for lab in labs:
                    chip(lab, faded=True)
            for lab in self.labels:
                chip(lab)
            # moments
            r = self.lane_rect("moments")
            for i, m in enumerate(self.moments):
                t = float(m["t_session_seconds"])
                note = m.get("note") or ""
                text = f"⚑ {_fmt_mmss(t)} · " + (note[:22] + ("…" if len(note) > 22 else "")
                                                 if note else "add a note")
                x0 = self._x(t)
                w = min(p.fontMetrics().horizontalAdvance(text) + 14, r.width() * 0.3)
                rr = QRectF(x0, r.y() + 2, w, r.height() - 4)
                p.setPen(QPen(QColor(dt.warning_color()), 1.2))
                p.setBrush(QColor(dt.warning_bg() if not note else "#FFFFFF"))
                p.drawRoundedRect(rr, 5, 5)
                if self.selection == ("moment", i):
                    p.setPen(QPen(QColor(dt.text_primary()), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(rr.adjusted(-1, -1, 1, 1), 6, 6)
                p.setPen(QColor(dt.warning_color()))
                p.drawText(rr.adjusted(6, 0, -3, 0), Qt.AlignmentFlag.AlignVCenter,
                           p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(w - 8)))
                self._hits.append((rr, "moment", i))
            # axis + playhead
            last = self.lane_rect(self.LANES[-1][0])
            p.setFont(small); p.setPen(QColor(dt.text_tertiary()))
            for k in range(5):
                t = self.duration * k / 4
                x = self._x(t)
                p.drawText(QRectF(x - 30, last.bottom() + 3, 60, 14),
                           Qt.AlignmentFlag.AlignCenter, _fmt_mmss(t))
            x = self._x(self._t)
            p.setPen(QPen(QColor(dt.record_red()), 2))
            p.drawLine(QPointF(x, 0), QPointF(x, last.bottom()))
        finally:
            p.end()


TimelineStrip = LaneTimeline      # the older name


class _MediaLoadWorker(QThread):
    """Off the UI thread: open the camera decoders (reads every sidecar) and
    parse the EEG CSVs — the two slow parts of opening a recording."""
    done = pyqtSignal(object, object)   # (MultiCamDecoder, {idx: df})

    def __init__(self, root, specs, eeg_plan, parent=None):
        super().__init__(parent)
        self._root = root; self._specs = specs; self._eeg_plan = eeg_plan

    def run(self):
        # the buzzer test's per-device corrections (none if it was not run)
        import brain_battle.capture.timing as timing
        corr = timing.corrections(session_storage._read_manifest(self._root) or {})
        decoder = open_session_cameras(self._specs, corr)
        dfs = {}
        for idx, path in self._eeg_plan.items():
            try:
                # tolerant: an interrupted recording can end in a half-written row
                df = session_storage.read_csv_tolerant(path).dropna()
                shift = corr.get(timing.band_device("AB"[idx]), 0.0)
                if shift and "t_session_seconds" in df.columns:
                    df["t_session_seconds"] = df["t_session_seconds"] - shift
                dfs[idx] = df
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

    PANEL_WIDTH = 360
    OTHERS_POLL_MS = 5000                   # how often other labellers' files are re-read

    def __init__(self, parent=None):
        super().__init__(parent)
        register_fonts()        # the bundled fonts, however this screen is made
        self._root = None
        self._labeller = app_settings.labeller_name()
        self._hide_names = False
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
        page = QVBoxLayout(); page.setContentsMargins(24, 14, 24, 14); page.setSpacing(12)
        page.addWidget(self._build_compare_banner())
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); content.setObjectName("reviewContent")
        content.setStyleSheet(f"#reviewContent {{ background: {dt.canvas_bg()}; }}")
        cv = QVBoxLayout(content); cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(12)
        top = QHBoxLayout(); top.setSpacing(18)
        self._grid = VideoGrid([("", "Side camera 1"), ("", "Side camera 2"),
                                ("", "Boxer A view"), ("", "Boxer B view")])
        grid_wrap = QFrame(); grid_wrap.setObjectName("videoWrap")
        grid_wrap.setStyleSheet(f"#videoWrap {{ background: {dt.stage()}; border-radius: 12px; }}")
        gw = QVBoxLayout(grid_wrap); gw.setContentsMargins(6, 6, 6, 6); gw.addWidget(self._grid)
        grid_wrap.setMinimumHeight(240)
        top.addWidget(grid_wrap, 3)
        self._labels_panel = self._build_labels_panel()
        top.addWidget(self._labels_panel, 0)
        cv.addLayout(top, 1)
        self._brain = BrainActivityCard()
        cv.addWidget(self._brain)
        self._scroll.setWidget(content)
        page.addWidget(self._scroll, 1)
        page.addWidget(self._build_dock())          # pinned: transport + lanes
        root.addLayout(page, 1)

    def _build_topbar(self):
        bar = QFrame(); bar.setFixedHeight(66); bar.setObjectName("topBar")
        bar.setStyleSheet(f"#topBar {{ background: {dt.surface()}; "
                          f"border-bottom: 1px solid {dt.border()}; }}")
        h = QHBoxLayout(bar); h.setContentsMargins(16, 8, 24, 8); h.setSpacing(16)
        back = QPushButton("‹  Sessions"); back.setMinimumHeight(40)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setStyleSheet(f"QPushButton {{ border: 0; background: transparent; color: #475467; "
                           f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_MD}px; font-weight: 500; "
                           f"padding: 8px 10px; }}")
        back.clicked.connect(self._leave)
        h.addWidget(back)
        tv = QVBoxLayout(); tv.setSpacing(0); tv.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel("Review & Label"); self._title.setTextFormat(Qt.TextFormat.RichText)
        self._title.setStyleSheet(dt.css_text(dt.SIZE_LG, 700))
        self._subtitle = QLabel("Open a recording from Sessions")
        self._subtitle.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.text_tertiary(),
                                                 f"font-family: {dt.FONT_MONO};"))
        tv.addWidget(self._title); tv.addWidget(self._subtitle)
        h.addLayout(tv, 1)
        self._tabs = ui.Segmented([("My labels", "mine"), ("Compare", "compare")],
                                  current="mine", height=36)
        self._tabs.changed.connect(self._on_tab)
        h.addWidget(self._tabs)
        self._autosave = ui.StatusLine("All labels saved", "ok", size=dt.SIZE_SM, wrap=False)
        h.addWidget(self._autosave)
        help_btn = ui.help_button()
        help_btn.clicked.connect(self.help_requested.emit)
        h.addWidget(help_btn)
        self._export_btn = ui.primary_button("Export for analysis")
        self._export_btn.clicked.connect(self._open_export)
        h.addWidget(self._export_btn)
        return bar

    def _build_compare_banner(self):
        self._compare_banner = ui.Card(tone="info", radius=14)
        h = QHBoxLayout(self._compare_banner); h.setContentsMargins(16, 10, 16, 10); h.setSpacing(10)
        tv = QVBoxLayout(); tv.setSpacing(2)
        self._agree_text = ui.body("", color=dt.accent_hover(), size=dt.SIZE_SM, weight=600)
        self._agree_sub = ui.muted("", size=dt.SIZE_XS)
        tv.addWidget(self._agree_text); tv.addWidget(self._agree_sub)
        h.addLayout(tv, 1)
        self._compare_combo = ui.Dropdown(compact=True); self._compare_combo.setFixedHeight(32)
        self._compare_combo.setToolTip("Whose labels to show beside yours")
        self._compare_combo.currentIndexChanged.connect(self._on_compare_changed)
        h.addWidget(self._compare_combo)
        self._tol_combo = _small_combo(
            [(f"within {t:g} s", t) for t in label_agreement.TOLERANCES], self._tolerance,
            "Two labels count as the same event when they are the same label for the same "
            "boxer and start within this long of each other.")
        self._tol_combo.setFixedHeight(32)
        self._tol_combo.currentIndexChanged.connect(self._on_tolerance_changed)
        h.addWidget(self._tol_combo)
        self._compare_banner.setVisible(False)
        return self._compare_banner

    def _build_dock(self):
        """Transport + lanes, always visible under the videos and the EEG."""
        dock = QFrame(); dock.setObjectName("dock")
        dock.setStyleSheet(dt.qss_card("dock"))
        v = QVBoxLayout(dock); v.setContentsMargins(16, 8, 16, 10); v.setSpacing(6)
        h = QHBoxLayout(); h.setSpacing(10)
        self._play_btn = QPushButton(); self._play_btn.setFixedSize(42, 42)
        self._play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._play_btn.setToolTip("Play / pause (Space)"); self._play_btn.setAccessibleName("Play")
        self._play_btn.setStyleSheet(dt.qss_primary_button() + " QPushButton { padding: 0; "
                                     "border-radius: 21px; }")
        self._play_btn.clicked.connect(self._toggle_play)
        h.addWidget(self._play_btn)
        self._time_lbl = ui.TimerLabel(px=16)
        self._time_lbl.set_template("00:00.00")
        self._time_lbl.setText("00:00.00")
        h.addWidget(self._time_lbl)
        self._phase_chip = ui.muted("", size=dt.SIZE_MD, wrap=False)
        h.addWidget(self._phase_chip, 1)
        self._sys_chip = QLabel(""); self._sys_chip.setObjectName("sysChip")
        self._sys_chip.setStyleSheet(f"#sysChip {{ background: {dt.card_bg_hover()}; border-radius: 6px; "
                                     f"padding: 3px 8px; font-family: {dt.FONT_MONO}; "
                                     f"font-size: {dt.SIZE_SM}px; }}")
        self._sys_chip.setVisible(False)
        h.addWidget(self._sys_chip)
        self._speed = ui.Segmented([(lbl, s) for s, lbl in _SPEEDS], current=1.0, height=32)
        self._speed.setToolTip("Playback speed")
        self._speed.changed.connect(self._set_speed)
        self._speed_group = self._speed
        h.addWidget(self._speed)
        v.addLayout(h)
        self._strip = LaneTimeline()
        self._strip.seek_requested.connect(self._on_timeline_seek)
        self._strip.selected.connect(self._on_timeline_select)
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
                                                    color="#FFFFFF", size=16)))
        self._play_btn.setIconSize(QSize(16, 16))

    def _label_grid_widget(self, for_moment=False):
        grid = QGridLayout(); grid.setSpacing(6)
        return grid

    def _build_labels_panel(self):
        panel = QFrame(); panel.setObjectName("labelPanel"); panel.setFixedWidth(self.PANEL_WIDTH)
        panel.setStyleSheet(dt.qss_card("labelPanel"))
        v = QVBoxLayout(panel); v.setContentsMargins(16, 12, 16, 12); v.setSpacing(8)
        self._panel_tabs = ui.Segmented([("Label", "label"), ("Activity", "activity")],
                                        current="label", height=34)
        self._panel_tabs.changed.connect(self._on_panel_tab)
        v.addWidget(self._panel_tabs)
        self._panel_stack = QStackedWidget()
        v.addWidget(self._panel_stack, 1)

        # ── Label tab: normal / custom note / moment ──
        self._mode_stack = QStackedWidget()
        self._panel_stack.addWidget(self._mode_stack)
        normal = QWidget(); nv = QVBoxLayout(normal); nv.setContentsMargins(0, 0, 0, 0); nv.setSpacing(8)
        idrow = QHBoxLayout(); idrow.setSpacing(6)
        self._add_head = QLabel("Add a label"); self._add_head.setStyleSheet(dt.css_text(dt.SIZE_MD, 700))
        idrow.addWidget(self._add_head, 1)
        self._labeller_chip = QPushButton("")
        self._labeller_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self._labeller_chip.setToolTip("Labels you place are saved under this name. Not you?")
        self._labeller_chip.setStyleSheet(f"QPushButton {{ border: 0; background: transparent; "
                                          f"color: {dt.accent()}; font-family: {dt.FONT_UI}; "
                                          f"font-size: {dt.SIZE_SM}px; font-weight: 600; }}")
        self._labeller_chip.clicked.connect(self._show_name_ask)
        idrow.addWidget(self._labeller_chip)
        nv.addLayout(idrow)
        self._name_ask = ui.Card(tone="info", radius=dt.RADIUS_MD)
        na = QVBoxLayout(self._name_ask); na.setContentsMargins(12, 10, 12, 12); na.setSpacing(8)
        na.addWidget(ui.body("Who is labelling?", color=dt.text_primary(), weight=700))
        na.addWidget(ui.muted("Your labels are saved under your name, so several people can label "
                              "the same session. We only ask once on this computer.",
                              size=dt.SIZE_XS))
        self._name_edit = QLineEdit(); self._name_edit.setPlaceholderText("e.g. Maya K")
        self._name_edit.setMinimumHeight(40); self._name_edit.setStyleSheet(dt.qss_input())
        self._name_edit.returnPressed.connect(self._confirm_name)
        na.addWidget(self._name_edit)
        self._name_ok = ui.primary_button("Save")
        self._name_ok.clicked.connect(self._confirm_name)
        na.addWidget(self._name_ok)
        nv.addWidget(self._name_ask)
        nv.addWidget(ui.body("1 · Who?", weight=600, size=dt.SIZE_SM))
        who_row = QHBoxLayout(); who_row.setSpacing(6)
        self._who_btns = {}
        for which in ("A", "B", "Both"):
            b = QPushButton(which); b.setCheckable(True); b.setMinimumHeight(40)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, w=which: self._set_who(w))
            self._who_btns[which] = b; who_row.addWidget(b, 1)
        nv.addLayout(who_row)
        nv.addWidget(ui.body("2 · What happened?", weight=600, size=dt.SIZE_SM))
        self._label_grid = QGridLayout(); self._label_grid.setSpacing(6)
        nv.addLayout(self._label_grid)
        self._pending_note = ui.Card(tone="warn", radius=dt.RADIUS_SM)
        pn = QHBoxLayout(self._pending_note); pn.setContentsMargins(10, 8, 10, 8)
        self._pending_text = ui.body("", color=dt._p().warning_text, size=dt.SIZE_XS, weight=500)
        pn.addWidget(self._pending_text, 1)
        self._pending_note.setVisible(False)
        nv.addWidget(self._pending_note)
        self._clinch_note = ui.muted("", size=dt.SIZE_XS)
        nv.addWidget(self._clinch_note)
        self._note_btn = QPushButton("▢  Custom note…")
        self._note_btn.setMinimumHeight(40); self._note_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._note_btn.setStyleSheet(f"QPushButton {{ border: 1px dashed {dt.border_strong()}; "
                                     f"background: {dt.surface()}; color: {dt.accent_hover()}; "
                                     f"border-radius: 10px; font-family: {dt.FONT_UI}; "
                                     f"font-size: {dt.SIZE_MD}px; font-weight: 600; }}")
        self._note_btn.clicked.connect(self.start_note)
        nv.addWidget(self._note_btn)
        self._sel_strip = QFrame(); self._sel_strip.setObjectName("selStrip")
        self._sel_strip.setStyleSheet(f"#selStrip {{ background: {dt.card_bg_hover()}; border-radius: 10px; }}")
        ss_ = QHBoxLayout(self._sel_strip); ss_.setContentsMargins(10, 6, 6, 6)
        self._sel_text = ui.body("", size=dt.SIZE_SM); ss_.addWidget(self._sel_text, 1)
        self._sel_delete = ui.secondary_button("Delete", compact=True)
        self._sel_delete.clicked.connect(self._delete_selected)
        ss_.addWidget(self._sel_delete)
        self._sel_strip.setVisible(False)
        nv.addWidget(self._sel_strip)
        self._moments_warn = QLabel(""); self._moments_warn.setWordWrap(True)
        self._moments_warn.setObjectName("momWarn")
        self._moments_warn.setStyleSheet(f"#momWarn {{ background: {dt.warning_bg()}; color: "
                                         f"{dt.warning_color()}; border-radius: 10px; padding: 8px 10px; "
                                         f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; "
                                         f"font-weight: 500; }}")
        self._moments_warn.setVisible(False)
        nv.addWidget(self._moments_warn)
        self._legacy_note = ui.Card(tone="info", radius=dt.RADIUS_SM)
        ln = QVBoxLayout(self._legacy_note); ln.setContentsMargins(10, 8, 10, 8); ln.setSpacing(6)
        self._legacy_text = ui.body("", color=dt.text_secondary(), size=dt.SIZE_XS)
        ln.addWidget(self._legacy_text)
        self._legacy_btn = ui.secondary_button("These are mine", compact=True)
        self._legacy_btn.clicked.connect(self._claim_legacy)
        ln.addWidget(self._legacy_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._legacy_note.setVisible(False)
        nv.addWidget(self._legacy_note)
        nv.addStretch()
        foot = QHBoxLayout()
        foot.addWidget(ui.muted("Space play · 1–8 label · N note", size=dt.SIZE_SM), 1)
        self._undo_btn = ui.secondary_button("Undo", compact=True)
        self._undo_btn.clicked.connect(self.undo)
        foot.addWidget(self._undo_btn)
        nv.addLayout(foot)
        self._mode_stack.addWidget(normal)

        # custom note
        note = QWidget(); tv = QVBoxLayout(note); tv.setContentsMargins(0, 0, 0, 0); tv.setSpacing(8)
        self._note_head = QLabel("Custom note"); self._note_head.setStyleSheet(dt.css_text(dt.SIZE_MD, 700))
        tv.addWidget(self._note_head)
        tv.addWidget(ui.muted("Describe what is happening. Custom notes stay with this session only; "
                              "they are not added to the label list.", size=dt.SIZE_SM))
        nrow = QHBoxLayout(); nrow.setSpacing(6)
        self._note_who_btns = {}
        for which in ("A", "B", "Both"):
            b = QPushButton(which); b.setCheckable(True); b.setMinimumHeight(38)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, w=which: self._set_who(w))
            self._note_who_btns[which] = b; nrow.addWidget(b, 1)
        tv.addLayout(nrow)
        self._note_text = QTextEdit(); self._note_text.setAcceptRichText(False)
        self._note_text.setPlaceholderText("e.g. A keeps pressing forward, B backs onto the ropes "
                                           "and covers up")
        self._note_text.setFixedHeight(110)
        self._note_text.setStyleSheet(dt.qss_input())
        self._note_text.textChanged.connect(
            lambda: self._note_save.setEnabled(bool(self._note_text.toPlainText().strip())))
        tv.addWidget(self._note_text)
        nb = QHBoxLayout(); nb.addStretch()
        cancel = ui.secondary_button("Cancel", compact=True); cancel.clicked.connect(self.cancel_note)
        self._note_save = ui.primary_button("Add note", compact=True)
        self._note_save.clicked.connect(self.add_note); self._note_save.setEnabled(False)
        nb.addWidget(cancel); nb.addWidget(self._note_save)
        tv.addLayout(nb); tv.addStretch()
        self._mode_stack.addWidget(note)

        # a marked moment
        mom = QWidget(); mv = QVBoxLayout(mom); mv.setContentsMargins(0, 0, 0, 0); mv.setSpacing(8)
        mh = QHBoxLayout()
        self._moment_head = QLabel("Moment"); self._moment_head.setStyleSheet(dt.css_text(dt.SIZE_MD, 700))
        mh.addWidget(self._moment_head, 1)
        done = ui.secondary_button("Done", compact=True); done.clicked.connect(self._deselect)
        mh.addWidget(done)
        mv.addLayout(mh)
        mv.addWidget(ui.body("Observer’s note", weight=600, size=dt.SIZE_SM))
        self._moment_note = QTextEdit(); self._moment_note.setAcceptRichText(False)
        self._moment_note.setPlaceholderText("What did you see at this moment?")
        self._moment_note.setFixedHeight(90)
        self._moment_note.setStyleSheet(dt.qss_input())
        self._moment_note.textChanged.connect(self._on_moment_note_edited)
        mv.addWidget(self._moment_note)
        mv.addWidget(ui.muted("You can also pick a label below to put a label at this exact moment.",
                              size=dt.SIZE_SM))
        self._moment_grid = QGridLayout(); self._moment_grid.setSpacing(6)
        mv.addLayout(self._moment_grid); mv.addStretch()
        self._mode_stack.addWidget(mom)
        self._moment_save_timer = QTimer(self); self._moment_save_timer.setSingleShot(True)
        self._moment_save_timer.setInterval(700)
        self._moment_save_timer.timeout.connect(self._save_moment_note)

        # ── Activity tab ──
        self._act_scroll, self._act_host, self._act_layout = self._scroll_list("activityHost")
        self._panel_stack.addWidget(self._act_scroll)
        self._selected = None          # ("label"|"note", id) or ("moment", index)
        self._set_who("A")
        self._refresh_identity()
        return panel

    def _scroll_list(self, name):
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget(); host.setObjectName(name)
        host.setStyleSheet(f"#{name} {{ background: {dt.card_bg()}; }}")
        lay = QVBoxLayout(host); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addStretch()
        scroll.setWidget(host)
        return scroll, host, lay

    # ---- the panel: tabs, modes, selection ----
    def _on_panel_tab(self, key):
        self._panel_stack.setCurrentIndex(0 if key == "label" else 1)
        if key == "activity":
            self._refresh_activity()

    def _refresh_activity(self):
        ui.clear_layout(self._act_layout, keep_last=1)
        if not self._root:
            return
        for e in reversed(session_history.read_history(self._root)):
            who, what = session_history.describe(e)
            when = platform_utils.format_iso(e.get("time", ""), fallback=e.get("time", ""))
            row = QLabel(f"<span style='color:{dt.text_tertiary()};font-family:{dt.FONT_MONO};"
                         f"font-size:11px'>{when}</span><br><b>{who}</b> {what}")
            row.setTextFormat(Qt.TextFormat.RichText); row.setWordWrap(True)
            row.setObjectName("actRow")
            row.setStyleSheet(f"#actRow {{ border-bottom: 1px solid #EEF1F6; padding: 7px 0; "
                              f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; }}")
            self._act_layout.insertWidget(self._act_layout.count() - 1, row)

    def activity_lines(self) -> list:
        return [self._act_layout.itemAt(i).widget().text()
                for i in range(self._act_layout.count() - 1)]

    def _current_t(self):
        return self._player.t if self._player else getattr(self, "_t_shown", 0.0)

    def start_note(self):
        """Custom note… (N): describe something in your own words."""
        if not self._labeller:
            self._show_name_ask(); return
        self._selected = None; self._strip.set_selection(None)
        self._note_head.setText(f"Custom note at {_fmt_mmss(self._current_t())}")
        self._note_text.clear()
        self._panel_tabs.set_value("label", emit=True)
        self._mode_stack.setCurrentIndex(1)
        self._note_text.setFocus()

    def cancel_note(self):
        self._note_text.clear()
        self._mode_stack.setCurrentIndex(0)

    def add_note(self, text: str | None = None):
        """Add note: a label of type "note" — who, when, the text, you."""
        text = (self._note_text.toPlainText() if text is None else text).strip()
        if not text:
            return None
        if not self._labeller:
            self._show_name_ask(); return None
        lab = labels_store.new_note(text, self._who, self._current_t(), created_by=self._labeller)
        self._store.add(lab)
        self._note_text.clear()
        self._mode_stack.setCurrentIndex(0)
        self._after_change(added=lab)
        self._select(("note", lab["id"]))
        return lab

    def _on_timeline_seek(self, t):
        self._deselect()
        self._seek(t)

    def _on_timeline_select(self, kind, key):
        if kind == "system":
            if 0 <= key < len(self._phases):
                t, _pid, lab = self._phases[key]
                self._sys_chip.setText(f"{lab} · {_fmt_time(t)}")
                self._sys_chip.setVisible(True)
                self._seek(t)
            return
        self._select((kind, key))

    def _select(self, sel):
        self._selected = sel
        self._strip.set_selection(sel)
        kind, key = sel
        if kind == "moment":
            if not (0 <= key < len(self._all_moments)):
                return
            m = self._all_moments[key]
            self._seek(float(m["t_session_seconds"]))
            self._moment_head.setText(f"Moment at {_fmt_mmss(m['t_session_seconds'])}")
            self._moment_note.blockSignals(True)
            self._moment_note.setPlainText(m.get("note") or "")
            self._moment_note.blockSignals(False)
            self._panel_tabs.set_value("label", emit=True)
            self._mode_stack.setCurrentIndex(2)
            return
        lab = self._store.by_id(key)
        if lab is None:
            for _n, labs in self._others.items():
                lab = next((l for l in labs if l.get("id") == key), None) or lab
        if lab is None:
            return
        self._seek(float(lab["t_start"]))
        mine = self._store.by_id(key) is not None
        what = (f"“{lab.get('text', '')}”" if lab.get("type") == "note" else lab.get("label", ""))
        self._sel_text.setText(f"Selected: <b>{what}</b> ({lab.get('who', '')}) at "
                               f"{_fmt_mmss(lab['t_start'])} · {lab.get('created_by') or lab.get('labeller', '')}")
        self._sel_text.setTextFormat(Qt.TextFormat.RichText)
        self._sel_delete.setVisible(mine)
        self._sel_strip.setVisible(True)
        self._mode_stack.setCurrentIndex(0)

    def _deselect(self):
        self._selected = None
        self._strip.set_selection(None)
        self._sel_strip.setVisible(False)
        self._sys_chip.setVisible(False)
        if self._mode_stack.currentIndex() == 2:
            self._save_moment_note()
            self._mode_stack.setCurrentIndex(0)

    def _delete_selected(self):
        if self._selected and self._selected[0] in ("label", "note"):
            self._delete_label(self._selected[1])
            self._deselect()

    def _on_moment_note_edited(self):
        self._moment_save_timer.start()

    def _save_moment_note(self):
        """The observer's note of the selected moment: saved into the recording
        (manifest + moments.csv), shared with everyone who opens it."""
        self._moment_save_timer.stop()
        if not (self._selected and self._selected[0] == "moment" and self._root):
            return
        i = self._selected[1]
        if not (0 <= i < len(self._all_moments)):
            return
        m = self._all_moments[i]
        text = session_storage.clean_note(self._moment_note.toPlainText())
        if text == (m.get("note") or ""):
            return
        had = bool(m.get("note"))
        if session_storage.set_moment_note(self._root, m["t_session_seconds"], text):
            m["note"] = text
            if text and not had:
                try:
                    session_history.log_event(self._root, session_history.EVENT_MOMENT_NOTED,
                                              {"t_session_seconds": m["t_session_seconds"],
                                               "note": text}, user=self._labeller or None)
                except Exception:
                    pass
            self._mark_saved()
        else:
            self._autosave.set("Not saved — the recordings folder can't be reached.", "bad")
        self._strip.set_moments(self._all_moments)
        self._refresh_moments_warning()

    def set_moment_note(self, index: int, text: str) -> bool:
        """Write a moment's note (tests and keyboard users)."""
        self._select(("moment", index))
        self._moment_note.setPlainText(text)
        self._save_moment_note()
        return self._all_moments[index].get("note") == session_storage.clean_note(text)

    def _refresh_moments_warning(self):
        n = sum(1 for m in self._all_moments if not m.get("note"))
        self._moments_warn.setText(f"{n} marked moment{'s' if n != 1 else ''} "
                                   f"{'have' if n != 1 else 'has'} no note yet — click "
                                   f"{'them' if n != 1 else 'it'} on the timeline")
        self._moments_warn.setVisible(bool(n) and not self._sel_strip.isVisible())

    def _leave(self):
        """‹ Sessions: everything is already saved; say so if labels were added
        (prototype: "✓ Your labels are saved" · Keep labelling / Close)."""
        if self._unsaved and self._root:
            self._save_now()
        self._save_moment_note()
        n = getattr(self, "_added_this_visit", 0)
        if n and self._root:
            no_note = sum(1 for m in self._all_moments if not m.get("note"))
            text = (f"Every label is saved the moment you add it — {n} new this time."
                    + (f" {no_note} marked moment{'s' if no_note != 1 else ''} still "
                       f"{'have' if no_note != 1 else 'has'} no note; you can add "
                       f"{'them' if no_note != 1 else 'it'} next time." if no_note else ""))
            if not ui.ask(self, "✓ Your labels are saved", text, "Close", "Keep labelling"):
                return
            self._added_this_visit = 0
        self.back_to_sessions.emit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._brain.fit_to(self._scroll.viewport().height() - 240)

    def showEvent_fit(self):
        self._brain.fit_to(self._scroll.viewport().height() - 240)

    # ---- who is labelling ----
    def labeller(self) -> str:
        return self._labeller

    def _refresh_identity(self):
        named = bool(self._labeller)
        self._labeller_chip.setText(f"as {self._labeller} · Not you?" if named
                                    else "you’ll be asked your name")
        self._name_ask.setVisible(not named)

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

    def _shown(self, which):
        return self._names.get(which) or f"Boxer {which}"

    def _has_name(self, which):
        return bool(self._names.get(which)) and not self._hide_names

    def _brain_names(self):
        return [f"Boxer {w} · {self._names[w]}" if self._names.get(w) else f"Boxer {w}"
                for w in ("A", "B")]

    # ---- loading (labels-first; media in the background) ----
    def load_session_folder(self, root: str):
        self.shutdown()
        self._root = root
        self._added_this_visit = 0
        self._loaded = False
        self._labeller = app_settings.labeller_name() or self._labeller
        self._refresh_identity()
        self._deselect()
        self._mode_stack.setCurrentIndex(0)
        self._panel_tabs.set_value("label", emit=True)
        recordings_dir = os.path.dirname(os.path.abspath(root))
        self._vocab = label_vocab.load(recordings_dir)
        self._rebuild_label_buttons()

        man = session_storage._read_manifest(root) or {}
        self._names = {w: fighters.display(man, w) for w in ("A", "B")}
        self._fcolours = {w: fighters.info(man, w)["colour_hex"] or dt.boxer_colors(w)[0]
                          for w in ("A", "B")}
        self._stopped_early = bool(man.get("stopped_early"))
        self._interrupted = bool(man.get("interrupted"))
        self._strip.set_fighters(self._names, self._fcolours)
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
        fc = getattr(self, "_fcolours", {})
        when = platform_utils.format_iso(self._started_at,
                                         fallback=os.path.basename(self._root or ""))
        self._title.setText(ui.versus_html(a or "Boxer A", fc.get("A"), b or "Boxer B",
                                           fc.get("B"), 16))
        mins = max(1, round(self._duration / 60)) if self._duration else 0
        self._subtitle.setText(f"{when} · " + self._subtitle_text(mins, self._rounds))

    def _subtitle_text(self, mins, rounds, a=None, b=None):
        bits = [f"{rounds} round{'s' if rounds != 1 else ''}", f"{mins} min"]
        if getattr(self, "_interrupted", False):
            bits = [f"Interrupted · saved up to {_fmt_mmss(self._duration)}"]
        elif getattr(self, "_stopped_early", False):
            bits = [f"Stopped early · {_fmt_mmss(self._duration)}"]
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
        """The label list as buttons (keys 1–8/9), twice: in "What happened?"
        and in the moment editor (puts the label at the moment's time)."""
        self._label_buttons = {}
        self._label_btn_widgets = []
        long_ones = []
        for grid, at_moment in ((self._label_grid, False), (self._moment_grid, True)):
            ui.clear_layout(grid)
            for i, entry in enumerate(self._vocab):
                key = entry.get("key", i + 1)
                b = QPushButton(f"{key if key < 10 else ''}   {entry['name']}")
                b.setMinimumHeight(38); b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setStyleSheet(_label_button_qss(entry.get("color", dt.accent())))
                lasts = entry.get("kind") == label_vocab.DURATION
                b.setToolTip(f"Press {key}" + (" when it starts and again when it ends"
                                              if lasts and not at_moment else ""))
                if at_moment:
                    b.clicked.connect(lambda _c=False, e=entry: self._label_at_moment(e))
                else:
                    b.clicked.connect(lambda _c=False, e=entry: self._place_from_vocab(e))
                    self._label_buttons[key] = entry
                    self._label_btn_widgets.append(b)
                    if lasts:
                        long_ones.append(entry["name"])
                grid.addWidget(b, i // 2, i % 2)
        if long_ones:
            names = " and ".join(long_ones) if len(long_ones) <= 2 else \
                ", ".join(long_ones[:-1]) + " and " + long_ones[-1]
            verb = "lasts" if len(long_ones) == 1 else "last"
            self._clinch_note.setText(f"{names} {verb} a while: press the number when it "
                                      f"starts and again when it ends (Esc cancels).")
        self._clinch_note.setVisible(bool(long_ones))

    def _label_at_moment(self, entry):
        """A label exactly at the selected moment's time."""
        if not self._labeller:
            self._show_name_ask(); return
        if not (self._selected and self._selected[0] == "moment"):
            return
        t = float(self._all_moments[self._selected[1]]["t_session_seconds"])
        self._commit_label(entry, t, None)

    def _update_who_button_labels(self):
        self._set_who(self._who)

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
        self._t_shown = t
        self._brain.set_playhead(t)
        self._brain.update_contact(t)
        self._strip.set_playhead(t)
        self._time_lbl.setText(_fmt_time(t))
        text, _pid = self._phase_text(t)
        self._phase_chip.setText(f"of {_fmt_mmss(self._duration)}" + (f" · {text}" if text else ""))
        self._add_head.setText(f"Add a label at {_fmt_mmss(t)}")

    def _on_state(self, playing):
        self._set_play_icon(playing)

    def _phase_text(self, t):
        active = None
        for (mt, pid, label) in [m for m in self._phases if session_storage.is_phase_start(m[2])]:
            if mt <= t + 1e-6:
                active = (mt, pid, label)
            else:
                break
        if active is None or active[1] == "done":
            return "", None
        return friendly_phase(active[2]), active[1]

    # ---- labels ----
    def _set_who(self, which):
        self._who = which
        fc = getattr(self, "_fcolours", {"A": dt.boxer_colors("A")[0], "B": dt.boxer_colors("B")[0]})
        for btns in (self._who_btns, getattr(self, "_note_who_btns", {})):
            for w, btn in btns.items():
                on = w == which
                if w == "Both":
                    btn.setText("Both")
                    bd, bg = (dt.text_secondary(), "#F1F3F7") if on else (dt.border_strong(), dt.surface())
                else:
                    btn.setText(f"{w} · {self._names.get(w) or ('Boxer ' + w)}")
                    col = QColor(fc.get(w))
                    bd = fc.get(w) if on else dt.border_strong()
                    bg = _tint(fc.get(w), 0.12) if on else dt.surface()
                btn.setStyleSheet(f"QPushButton {{ border: {'2px' if on else '1px'} solid {bd}; "
                                  f"background: {bg}; color: {dt.text_primary()}; border-radius: 10px; "
                                  f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; "
                                  f"font-weight: {700 if on else 500}; }}")
                btn.setChecked(on)

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
        self._moments = list(self._all_moments)
        self._strip.set_labels(self._timeline_labels())
        self._strip.set_moments(self._all_moments)
        self._undo_btn.setEnabled(self._store.can_undo())
        self._refresh_compare()
        self._refresh_legacy_note()
        self._refresh_moments_warning()
        self._brain.set_markers(self._eeg_markers())

    def _eeg_markers(self):
        """Every event of the recording as (time, text, colour) for the EEG
        display: the automatic system markers in grey, marked moments in red,
        your labels and notes in their colours."""
        out = [(t, label, dt.system_marker()) for t, _pid, label in self._phases]
        out += [(float(m["t_session_seconds"]), "MOMENT", dt.record_red()) for m in self._all_moments]
        for lab in self._store.labels:
            col = lab.get("color", dt.accent())
            name = "Note" if labels_store.is_note(lab) else lab.get("label", "")
            out.append((float(lab["t_start"]), f"{name} · {lab.get('who', '')}", col))
            if lab.get("t_end") is not None:
                out.append((float(lab["t_end"]), f"{name} ends", col))
        return out

    def _timeline_labels(self):
        return list(self._store.labels)

    # ---- saving ----
    def _autosave_and_history(self, added=None, removed=None):
        if not self._root or not self._labeller:
            return
        if added is not None:
            self._added_this_visit = getattr(self, "_added_this_visit", 0) + 1
        saved = self._save_now()
        try:
            for event, lab in ((session_history.EVENT_LABELS_ADDED, added),
                               (session_history.EVENT_LABELS_REMOVED, removed)):
                if lab is not None:
                    session_history.log_event(
                        self._root, event,
                        {"label": lab["label"], "who": lab.get("who"),
                         "t_start": lab.get("t_start"), "labeller": self._labeller,
                         "type": lab.get("type") or "label"},
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
        self._compare_combo.setVisible(len(names) > 1)
        self._tabs.setVisible(bool(names))
        if not names and self._tabs.value() == "compare":
            self._tabs.set_value("mine")

    def _on_compare_changed(self, _i):
        self._compare_with = self._compare_combo.currentData()
        self._refresh_compare()

    def _on_tolerance_changed(self, _i):
        self._tolerance = float(self._tol_combo.currentData())
        self._refresh_compare()

    def _compare_labels(self):
        return list(self._others.get(self._compare_with, [])) if self.comparing() else []

    def agreement(self):
        """How far my labels and the compared labeller's agree (custom notes
        left out), or None."""
        if not self._compare_with:
            return None
        mine = [l for l in self._store.labels if not labels_store.is_note(l)]
        theirs = [l for l in self._others.get(self._compare_with, []) if not labels_store.is_note(l)]
        return label_agreement.compare(mine, theirs, self._labeller or "You",
                                       self._compare_with, self._tolerance)

    def _refresh_compare(self):
        on = self.comparing()
        self._strip.set_others([(self._compare_with, self._others[self._compare_with])] if on else [])
        self._compare_banner.setVisible(on)
        if not on:
            return
        ag = self.agreement()
        self._agree_text.setText(ag.sentence(a_is_you=True)
                                 + f" Faded chips are {self._compare_with}’s.")
        self._agree_sub.setText(
            f"“The same event” means the same label for the same boxer, starting within "
            f"{self._tolerance:g} s of each other."
            + (f" On those, you differ by {ag.mean_time_difference:.2f} s on average."
               if ag.matched else ""))

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
            self._cancel_pending(); self._deselect(); return True
        if key == Qt.Key.Key_N:
            self.start_note(); return True
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
        self._save_moment_note()
        if self._player:
            self._player.pause() if hasattr(self._player, "pause") else None
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


__all__ = ["ReviewLabelScreen", "read_phase_markers", "friendly_phase", "short_phase",
           "_discover_cameras", "_fmt_time"]
