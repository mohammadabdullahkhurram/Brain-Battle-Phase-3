import os
os.environ["QT_MEDIA_BACKEND"] = "ffmpeg"
os.environ["QT_FFMPEG_DISABLE_HW_DECODING"] = "1"

# Configure logging BEFORE the heavy imports so import-time messages
# (e.g. export_module's encoder detection) reach the console handler.
import logging
from log_setup import init_logging
init_logging()
log = logging.getLogger(__name__)

from pathlib import Path
import sys
import datetime as _dt

import vlc

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QFrame, QSizePolicy, QFileDialog,
    QListWidget, QListWidgetItem, QScrollArea,
    QSplitter, QSlider, QComboBox, QLineEdit,
    QGroupBox, QProgressBar, QStatusBar, QToolBar,
    QSpacerItem, QMessageBox, QStackedWidget
)
from PyQt6.QtCore import Qt, QTimer, QSize, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon, QPixmap, QPainter

from eeg_module import EEGLiveWidget, EEGReviewWidget
from live_session_tab import LiveSessionTab
from vlc_module import VLCVideoSlot, SyncPlaybackController
from export_module import FullSessionExportWorker, LabelsExporter, ExportProgressDialog
from labels_config import LABELS as PREDEFINED_LABELS, next_colour, save as save_labels
from version import __version__
import session_history

_INT32_MAX = 2_147_483_647

# Autosaved labels file written into each session folder. schema_version lets
# Phase 4 migrate the on-disk shape (e.g. moving times onto the capture-clock
# t_session axis, filling boxer/end fields) without silently misreading v1.
LABELS_JSON_NAME = "labels.json"
LABELS_SCHEMA_VERSION = 1
# Which time axis the autosaved label times (`t_seconds`) are on. The Labelling
# tab still plays video from local 0 (NOT the capture-clock t_session axis the
# Review tab aligns on — see docs/ROADMAP.md Phase 3), so label times are
# seconds from the start of playback. Recorded in labels.json so the Phase 3
# Review & Label merge can convert them onto the capture axis (add each source's
# measured start). When that lands, this becomes "capture_clock_t_session".
LABELS_TIME_AXIS = "labelling_playhead_offset0"


def _atomic_write_json(path, data) -> None:
    """Write JSON to `path` via a temp file + os.replace so a crash during
    the write never leaves a half-written (corrupt) labels file — either the
    old file or the complete new one survives."""
    import json as _json
    import os as _os
    import tempfile as _tempfile
    d = _os.path.dirname(_os.path.abspath(path))
    _os.makedirs(d, exist_ok=True)
    fd, tmp = _tempfile.mkstemp(dir=d, prefix=".labels-", suffix=".tmp")
    try:
        with _os.fdopen(fd, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            _os.fsync(f.fileno())
        _os.replace(tmp, path)   # atomic on POSIX and Windows (same filesystem)
    except Exception:
        try:
            _os.unlink(tmp)
        except OSError:
            pass
        raise


def _friendly_when(iso: str) -> str:
    """Turn a manifest 'started_at' ISO string into a plain, readable
    date+time for the crash-recovery prompt. Falls back to the raw string."""
    if not iso:
        return "an earlier session"
    try:
        dt = _dt.datetime.fromisoformat(iso)
        return dt.strftime("%A %d %B %Y at %H:%M")
    except (ValueError, TypeError):
        return iso


def _clamp_ms(ms) -> int:
    try:
        ms = float(ms)
    except (TypeError, ValueError):
        return 0
    if ms != ms:  # NaN — int(nan) raises; a NaN offset means "no offset"
        return 0
    return int(max(0.0, min(ms, float(_INT32_MAX))))


from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtGui import QImage


class VideoSurface(QWidget):
    """QPainter-based video surface — no native NSView created."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #F6F7F9;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._st     = None
        self._bufs   = None
        self._vw     = 1
        self._vh     = 1
        self._vpitch = 4
        self._timer  = QTimer(self)
        self._timer.setInterval(33)  # ~30 fps during playback
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def attach_vmem(self, bufs, st, w, h, pitch, real_w=None):
        self._bufs   = bufs; self._st = st
        self._vw     = w        # buffer width (aligned, used as QImage bytesPerLine / 4)
        self._vh     = h
        self._vpitch = pitch
        self._real_w = real_w if real_w is not None else w  # actual video pixel width

    def _tick(self):
        st = self._st
        if st is None or not st['fresh']:
            return
        st['fresh'] = False
        st['r'], st['d'] = st['d'], st['r']
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtCore import QRectF
        painter = QPainter(self)
        st, bufs = self._st, self._bufs
        if st is not None and bufs is not None:
            # Build QImage using the aligned pitch as bytesPerLine.
            # Crop display to the real video width so the padding columns
            # (which may contain garbage) are never drawn — AND crop the
            # bottom 1 row because VLC's vmem unlock callback can race the
            # last-row write, producing a single-line green/garbled artefact
            # on the bottom edge of the first frame after seeks.
            img = QImage(bufs[st['d']], self._vw, self._vh,
                         self._vpitch, QImage.Format.Format_ARGB32)
            # Crop right padding and bottom row.
            crop_w = min(self._real_w, self._vw)
            crop_h = max(1, self._vh - 1)
            if crop_w < self._vw or crop_h < self._vh:
                img = img.copy(0, 0, crop_w, crop_h)
            sw, sh = self.width(), self.height()
            iw, ih = img.width(), img.height()
            scale  = min(sw / iw, sh / ih)
            dw, dh = iw * scale, ih * scale
            painter.drawImage(QRectF((sw - dw) / 2, (sh - dh) / 2, dw, dh), img)
        else:
            painter.fillRect(self.rect(), Qt.GlobalColor.black)
        painter.end()



def _supplementary_qss():
    """Theme-aware QSS supplementing dt.qss_app() — covers widgets that
    aren't specifically styled by the main stylesheet factories (sliders,
    list widgets, progress bars, combo boxes, group boxes, etc.)."""
    import design_tokens as dt
    return f"""
        QFrame#panel {{ background-color: {dt.card_bg()}; border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_MD}px; }}
        QFrame#video_slot {{ background-color: {dt.canvas_bg()}; border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_MD}px; }}
        QFrame#video_slot:hover {{ border-color: {dt.border_strong()}; }}
        QLabel#panel_title {{ color: {dt.accent()}; font-size: 10px; letter-spacing: 1.8px; font-family: {dt.FONT_UI}; font-weight: 700; padding: 0px; }}
        QLabel#timecode {{ color: {dt.accent()}; font-size: 13px; font-family: {dt.FONT_MONO}; letter-spacing: 0.8px; }}
        QSlider::groove:horizontal {{ height: 3px; background: {dt.border_strong()}; border-radius: 1px; }}
        QSlider::sub-page:horizontal {{ background: {dt.accent()}; height: 3px; border-radius: 1px; }}
        QSlider::handle:horizontal {{ background: {dt.accent()}; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; }}
        QListWidget {{ background-color: {dt.card_bg()}; border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_MD}px; color: {dt.text_primary()}; font-family: {dt.FONT_UI}; font-size: 12px; outline: none; padding: 4px; }}
        QListWidget::item {{ padding: 8px 12px; border-radius: {dt.RADIUS_SM}px; }}
        QListWidget::item:selected {{ background-color: {dt.accent_ghost()}; color: {dt.accent()}; }}
        QProgressBar {{ background-color: {dt.card_bg()}; border: 1px solid {dt.border()}; height: 4px; text-align: center; color: transparent; border-radius: 2px; }}
        QProgressBar::chunk {{ background-color: {dt.accent()}; border-radius: 2px; }}
        QStatusBar {{ background-color: {dt.canvas_bg()}; border-top: 1px solid {dt.border()}; color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; font-size: 10px; }}
        QGroupBox {{ border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_MD}px; margin-top: 18px; padding: 12px; font-family: {dt.FONT_UI}; font-size: 10px; letter-spacing: 1.4px; font-weight: 700; color: {dt.text_tertiary()}; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px; color: {dt.text_tertiary()}; letter-spacing: 1.4px; }}
        QComboBox {{ background-color: {dt.card_bg()}; border: 1px solid {dt.border_strong()}; border-radius: {dt.RADIUS_MD}px; color: {dt.text_primary()}; padding: 8px 12px; font-family: {dt.FONT_UI}; font-size: 12px; }}
        QComboBox:hover {{ border-color: {dt.accent()}; }}
    """

APP_STYLE = ""  # Legacy placeholder — kept for compatibility with tests

def make_divider():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("background-color: #C9CED7; max-height: 1px; border: none;")
    return line

def make_panel_title(text):
    lbl = QLabel(text); lbl.setObjectName("panel_title"); return lbl

def make_source_label(text):
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #3A404B; font-size: 11px; font-family: 'Inter'; letter-spacing: 2px; padding-left: 12px;")
    return lbl


def make_opaque_toolbar(height: int = 44) -> QFrame:
    """
    Build a toolbar widget guaranteed to render opaquely above its
    siblings. The default QWidget with just a stylesheet background
    can be painted-over by sibling widgets that use native surfaces
    (notably QVideoWidget on macOS, which bypasses Qt's compositor).
    The combination of WA_StyledBackground + a QPalette fill + a
    QFrame subclass (which respects palette-driven backgrounds) makes
    the toolbar opaque, and the caller is expected to `.raise_()` it
    after the parent layout is assembled.
    """
    tb = QFrame()
    tb.setObjectName("tabToolbar")
    tb.setFixedHeight(height)
    tb.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    tb.setAutoFillBackground(True)
    # Set a QPalette fill so even when the stylesheet selector misses
    # (which can happen on first paint after a video widget warms up)
    # the toolbar still draws over whatever's underneath.
    from PyQt6.QtGui import QPalette, QColor
    pal = tb.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#F6F7F9"))
    tb.setPalette(pal)
    tb.setStyleSheet(
        "QFrame#tabToolbar { background-color: #F6F7F9; "
        "border-bottom: 1px solid #DFE2E8; }"
    )
    return tb


class VideoSlot(QFrame):
    upload_requested = pyqtSignal(int)
    def __init__(self, slot_index, label="CAM", parent=None):
        super().__init__(parent)
        self.slot_index = slot_index; self.label = label
        self.setObjectName("video_slot")
        self.setMinimumSize(200, 140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._build()
    def _build(self):
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        top_bar = QWidget(); top_bar.setFixedHeight(26)
        top_bar.setStyleSheet("background-color: #F6F7F9; border-bottom: 1px solid #DFE2E8;")
        tl = QHBoxLayout(top_bar); tl.setContentsMargins(10,0,10,0)
        self.status_dot = QLabel("●"); self.status_dot.setObjectName("status_dot_idle")
        self.slot_label = QLabel(f"{self.label} {self.slot_index:02d}")
        self.slot_label.setStyleSheet("color: #555D6B; font-size: 10px; font-family: 'Inter'; letter-spacing: 2px;")
        self.filename_label = QLabel("NO SOURCE")
        self.filename_label.setStyleSheet("color: #C9CED7; font-size: 9px; font-family: 'Inter'; letter-spacing: 1px;")
        tl.addWidget(self.status_dot); tl.addSpacing(6); tl.addWidget(self.slot_label)
        tl.addSpacing(12); tl.addWidget(self.filename_label); tl.addStretch()
        layout.addWidget(top_bar)
        self.main_area = QWidget()
        self.main_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.main_area.setStyleSheet("background-color: #F6F7F9;")
        ml = QVBoxLayout(self.main_area); ml.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder = QLabel(f"[ {self.label} {self.slot_index:02d} ]")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet("color: #C9CED7; font-size: 16px; font-family: 'Inter'; letter-spacing: 4px;")
        ml.addWidget(self.placeholder); layout.addWidget(self.main_area)
    def set_active(self, filename=""):
        self.status_dot.setStyleSheet("color: #15803D; font-size: 10px;")
        if filename:
            self.filename_label.setText(filename.split("/")[-1][:28])
            self.filename_label.setStyleSheet("color: #3A404B; font-size: 9px; font-family: 'Inter'; letter-spacing: 1px;")
        self.placeholder.setText("")
    def set_idle(self):
        self.status_dot.setStyleSheet("color: #C9CED7; font-size: 10px;")
        self.filename_label.setText("NO SOURCE"); self.placeholder.setText(f"[ {self.label} {self.slot_index:02d} ]")


class EEGPanel(QFrame):
    def __init__(self, channel_id=1, parent=None):
        super().__init__(parent); self.channel_id = channel_id
        self.setObjectName("panel"); self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); self._build()
    def _build(self):
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        top_bar = QWidget(); top_bar.setFixedHeight(26)
        top_bar.setStyleSheet("background-color: #F6F7F9; border-bottom: 1px solid #DFE2E8;")
        tb = QHBoxLayout(top_bar); tb.setContentsMargins(10,0,10,0)
        dot = QLabel("●"); dot.setStyleSheet("color: #C9CED7; font-size: 10px;")
        lbl = QLabel(f"EEG CHANNEL {self.channel_id}  —  MUSE-S")
        lbl.setStyleSheet("color: #555D6B; font-size: 10px; font-family: 'Inter'; letter-spacing: 2px;")
        self.hz_label = QLabel("256 HZ")
        self.hz_label.setStyleSheet("color: #C9CED7; font-size: 9px; font-family: 'Inter'; letter-spacing: 1px;")
        tb.addWidget(dot); tb.addSpacing(6); tb.addWidget(lbl); tb.addStretch(); tb.addWidget(self.hz_label)
        layout.addWidget(top_bar)
        ph = QLabel(f"EEG plot will render here — channel {self.channel_id}")
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph.setStyleSheet("color: #C9CED7; font-size: 11px; font-family: 'Inter'; letter-spacing: 2px; padding: 20px;")
        layout.addWidget(ph)


class SourceRow(QWidget):
    file_loaded = pyqtSignal(int, str)
    def __init__(self, source_id, label, accept_filter, parent=None):
        super().__init__(parent); self.source_id = source_id; self.file_path = None
        self.accept_filter = accept_filter; self._build(label)
    def _build(self, label):
        layout = QHBoxLayout(self); layout.setContentsMargins(0,3,0,3); layout.setSpacing(6)
        self.status = QLabel("○"); self.status.setFixedWidth(14)
        self.status.setStyleSheet("color: #3A404B; font-size: 12px; font-family: 'Inter';")
        self.type_lbl = QLabel(label); self.type_lbl.setFixedWidth(100)
        self.type_lbl.setStyleSheet("color: #3A404B; font-size: 10px; font-family: 'Inter'; letter-spacing: 1px;")
        self.file_lbl = QLabel("—")
        self.file_lbl.setStyleSheet("color: #3A404B; font-size: 10px; font-family: 'Inter';")
        self.file_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.file_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.offset_lbl = QLabel(""); self.offset_lbl.setVisible(False)
        btn = QPushButton("UPLOAD"); btn.setFixedWidth(72); btn.setFixedHeight(26)
        btn.setStyleSheet("background-color: #FFFFFF; border: 1px solid #C9CED7; color: #3A404B; font-family: 'Inter'; font-size: 10px; padding: 0 6px;")
        btn.clicked.connect(self._pick_file)
        layout.addWidget(self.status); layout.addWidget(self.type_lbl); layout.addWidget(self.file_lbl); layout.addWidget(btn)
    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select file", "", self.accept_filter)
        if path:
            self.set_path(path)
    def set_path(self, path: str):
        """Programmatic equivalent of picking a file via the UPLOAD button.
        Used by LiveSessionTab → SyncReviewTab auto-population after a capture
        session finishes."""
        if not path:
            return
        self.file_path = path
        name = path.split("/")[-1]; short = name[:12] + "…" if len(name) > 12 else name
        self.file_lbl.setText(short); self.file_lbl.setStyleSheet("color: #15171C; font-size: 10px; font-family: 'Inter';")
        self.status.setText("●"); self.status.setStyleSheet("color: #15803D; font-size: 12px; font-family: 'Inter';")
        self.file_loaded.emit(self.source_id, path)
    def set_offset(self, seconds):
        if seconds == 0.0:
            self.offset_lbl.setText("BASE"); self.offset_lbl.setStyleSheet("color: #3D4FD6; font-size: 10px; font-family: 'Inter'; text-align: right;")
        else:
            sign = "+" if seconds > 0 else ""
            self.offset_lbl.setText(f"{sign}{seconds:.3f}s"); self.offset_lbl.setStyleSheet("color: #3D4FD6; font-size: 10px; font-family: 'Inter'; text-align: right;")

import pandas as pd


# Phase-marker colour palette — shared by the Sync + Review timeline strip and
# the Labelling tab. All ROUND_*_START events share blue (same kind of event —
# scanning the timeline should read "5 blue chips = 5 rounds"); same for breaks.
PHASE_MARKER_COLORS = {
    "baseline": "#15803D",   # emerald — calm, "before the work"
    "round":    "#3D4FD6",   # electric blue — active phase
    "break":    "#B45309",   # amber — rest/pause
    "done":     "#DC2626",   # crimson — terminal, stop
}


def _read_phase_markers(csv_path: str) -> list[tuple[float, str, str]]:
    """Parse a v2.x phase_markers.csv into (t_sec, color, label) tuples.
    Rows with an unparsable t_session_seconds are skipped; a missing or
    unreadable file yields an empty list."""
    import csv as _csv
    rows: list[tuple[float, str, str]] = []
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                try:
                    t = float(row.get("t_session_seconds", ""))
                except (TypeError, ValueError):
                    continue
                label = (row.get("label") or "PHASE").strip()
                phase_id = (row.get("phase_id") or "").strip().lower()
                rows.append((t, PHASE_MARKER_COLORS.get(phase_id, "#3D4FD6"), label))
    except Exception as e:
        log.warning("failed to read phase markers %s: %s", csv_path, e)
    return rows


def _read_capture_t0_unix(session_root) -> "float | None":
    """Read capture_clock.t0_unix from a session's manifest — the shared
    origin the v3 timestamp alignment reconciles every source against.
    Returns None if the manifest or the field is absent (a pre-3.0 session,
    which v3 does not support)."""
    import json as _json
    from pathlib import Path as _P
    try:
        data = _json.loads((_P(session_root) / "session_manifest.json")
                           .read_text(encoding="utf-8"))
        return data.get("capture_clock", {}).get("t0_unix")
    except Exception as e:
        log.warning("could not read capture_clock from manifest in %s: %s",
                    session_root, e)
        return None


def _read_frame_sidecar_start(mp4_path: str) -> "float | None":
    """First frame's t_session_seconds from a camera's timestamp sidecar
    (<mp4-stem>.timestamps.csv) — the camera's measured start on the shared
    axis. None if the sidecar is missing/empty."""
    from pathlib import Path as _P
    import csv as _csv
    p = _P(mp4_path)
    sidecar = p.parent / (p.stem + ".timestamps.csv")
    if not sidecar.exists():
        return None
    try:
        with open(sidecar, "r", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                return float(row["t_session_seconds"])  # first data row
    except Exception as e:
        log.warning("could not read frame sidecar %s: %s", sidecar, e)
    return None


class SyncReviewTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.media_players      = [None, None, None, None]
        self._video_widgets_qt  = [None, None, None, None]
        self._vmem_states       = [None, None, None, None]
        self.video_paths        = [None, None, None, None]
        self.video_widgets      = []
        self.eeg_file_name_1 = None; self.eeg_file_name_2 = None
        self.sync_result = None; self._worker = None
        self._temp_paths = []; self._delays = []; self._residuals_ms = []
        self._phase_markers: list[tuple[float, str, str]] = []
        # v3 capture-time alignment state (set by load_session_folder):
        # the shared origin, each video's measured start on the shared axis,
        # and axis_shift = the latest source start = the playhead-0 point.
        self._session_root = None
        self._capture_t0_unix = None
        self._cam_starts: list[float] = []
        self._axis_shift: float = 0.0
        self._post_sync_offsets_ms = [0, 0, 0, 0]
        self._pos_timer = QTimer(self); self._pos_timer.setInterval(100)
        self._pos_timer.timeout.connect(self._poll_position)
        self._app_ref = None
        self._build()
        import design_tokens as _dt
        self.setStyleSheet(f"SyncReviewTab {{ background: {_dt.canvas_bg()}; }}")

    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        toolbar = make_opaque_toolbar(44)
        tb = QHBoxLayout(toolbar); tb.setContentsMargins(16,0,16,0); tb.setSpacing(8)
        tb.addWidget(make_panel_title("REVIEW")); tb.addStretch()
        self.open_session_btn = QPushButton("OPEN SESSION FOLDER"); self.open_session_btn.setFixedHeight(28)
        self.open_session_btn.setStyleSheet("background-color: #EEF0FD; border: 1px solid #3D4FD6; color: #3D4FD6; font-family: 'Inter'; font-size: 10px; letter-spacing: 2px; padding: 0 14px;")
        self.open_session_btn.clicked.connect(self._open_session_dialog)
        tb.addWidget(self.open_session_btn)
        # "ALIGN" (not "AUTO-SYNC"): alignment is READ from the capture-time
        # stamps, not discovered — there is nothing to sync.
        self.sync_btn = QPushButton("ALIGN"); self.sync_btn.setFixedHeight(28)
        self.sync_btn.setStyleSheet("background-color: #EEF0FD; border: 1px solid #3D4FD6; color: #3D4FD6; font-family: 'Inter'; font-size: 10px; letter-spacing: 2px; padding: 0 16px;")
        self.export_btn = QPushButton("EXPORT SYNCED DATA"); self.export_btn.setFixedHeight(28)
        self.export_btn.setStyleSheet("background-color: transparent; border: 1px solid #3D4FD6; color: #3D4FD6; font-family: 'Inter'; font-size: 10px; letter-spacing: 1px; padding: 0 14px;")
        self.export_btn.clicked.connect(self._export_synced_data)
        tb.addWidget(self.sync_btn); tb.addWidget(self.export_btn); root.addWidget(toolbar)
        # Defer until the layout commits so the toolbar ends up above
        # native QVideoWidget surfaces that would otherwise paint over it.
        QTimer.singleShot(0, toolbar.raise_)
        self._toolbar = toolbar

        splitter = QSplitter(Qt.Orientation.Horizontal); splitter.setHandleWidth(1)
        upload_widget = QWidget(); upload_widget.setFixedWidth(400)
        upload_widget.setStyleSheet("background-color: #F6F7F9;")
        upload_layout = QVBoxLayout(upload_widget); upload_layout.setContentsMargins(10,16,10,16); upload_layout.setSpacing(6)
        upload_layout.addWidget(make_source_label("DATA SOURCES")); upload_layout.addSpacing(6)
        header = QHBoxLayout(); header.setSpacing(6)
        for txt, w in [("", 14), ("TYPE", 100), ("FILE", 0), ("", 80)]:
            h = QLabel(txt); h.setStyleSheet("color: #C9CED7; font-size: 9px; font-family: 'Inter'; letter-spacing: 2px;")
            if w: h.setFixedWidth(w)
            else: h.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            header.addWidget(h)
        upload_layout.addLayout(header); upload_layout.addWidget(make_divider())
        self.sources = []
        source_defs = [
            ("DSLR  01","Video Files (*.mp4 *.mov *.avi *.MP4 *.MOV)"),("DSLR  02","Video Files (*.mp4 *.mov *.avi *.MP4 *.MOV)"),
            ("POV     01","Video Files (*.mp4 *.mov *.avi *.MP4 *.MOV)"),("POV     02","Video Files (*.mp4 *.mov *.avi *.MP4 *.MOV)"),
            ("MUSE-S  01","CSV Files (*.csv)"),("MUSE-S  02","CSV Files (*.csv)"),
        ]
        for i, (lbl, filt) in enumerate(source_defs):
            row = SourceRow(i, lbl, filt); upload_layout.addWidget(row)
            if i == 3: upload_layout.addWidget(make_divider())
            self.sources.append(row); row.file_loaded.connect(self._on_file_loaded)
        upload_layout.addWidget(make_divider())
        self.sync_status = QLabel("AWAITING SOURCES")
        self.sync_status.setStyleSheet("color: #C9CED7; font-size: 10px; font-family: 'Inter'; letter-spacing: 2px; padding: 8px 0;")
        upload_layout.addWidget(self.sync_status)
        self.sync_progress = QProgressBar(); self.sync_progress.setValue(0)
        self.sync_progress.setFixedHeight(3); self.sync_progress.setVisible(False)
        upload_layout.addWidget(self.sync_progress); upload_layout.addStretch()
        self.goto_label_btn = QPushButton("PROCEED TO LABELLING  →"); self.goto_label_btn.setEnabled(False)
        self.goto_label_btn.setStyleSheet("background-color: #EEF0FD; border: 1px solid #3D4FD6; color: #3D4FD6; font-family: 'Inter'; font-size: 10px; letter-spacing: 2px; padding: 10px;")
        upload_layout.addWidget(self.goto_label_btn); splitter.addWidget(upload_widget)

        preview_widget = QWidget(); preview_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        preview_layout = QVBoxLayout(preview_widget); preview_layout.setContentsMargins(0,0,0,0); preview_layout.setSpacing(0)
        right_scroll = QScrollArea(); right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        right_scroll.setStyleSheet("QScrollArea { background: #F6F7F9; border: none; } QScrollBar:vertical { background: #F6F7F9; width: 8px; border: none; } QScrollBar::handle:vertical { background: #DFE2E8; border-radius: 4px; min-height: 24px; } QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: none; }")
        right_content = QWidget(); right_content.setStyleSheet("background-color: #F6F7F9;")
        right_vbox = QVBoxLayout(right_content); right_vbox.setContentsMargins(0,0,0,0); right_vbox.setSpacing(0)

        class _AspectVideoArea(QWidget):
            HEADER_H = 26; SPACING = 2
            def resizeEvent(self, event):
                super().resizeEvent(event)
                cell_w = max(100, (self.width() - self.SPACING) // 2); cell_h = int(cell_w * 9 / 16)
                self.setFixedHeight((cell_h + self.HEADER_H) * 2 + self.SPACING)

        video_area = _AspectVideoArea(); video_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        video_area.setStyleSheet("background-color: #F6F7F9;")
        va_layout = QVBoxLayout(video_area); va_layout.setContentsMargins(0,0,0,0); va_layout.setSpacing(0)
        vgrid = QGridLayout(); vgrid.setSpacing(2); vgrid.setContentsMargins(0,0,0,0)
        vgrid.setRowStretch(0,1); vgrid.setRowStretch(1,1); vgrid.setColumnStretch(0,1); vgrid.setColumnStretch(1,1)

        def _make_video_slot(cam_label, placeholder_text):
            slot = QWidget(); slot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            slot_layout = QVBoxLayout(slot); slot_layout.setContentsMargins(0,0,0,0); slot_layout.setSpacing(0)
            hdr = QWidget(); hdr.setFixedHeight(26)
            hdr.setStyleSheet("background-color: #F6F7F9; border-bottom: 1px solid #DFE2E8;")
            hdr_layout = QHBoxLayout(hdr); hdr_layout.setContentsMargins(8,0,8,0); hdr_layout.setSpacing(6)
            dot = QLabel("●"); dot.setStyleSheet("color: #C9CED7; font-size: 10px;"); dot.setFixedWidth(12)
            name_lbl = QLabel(cam_label); name_lbl.setStyleSheet("color: #3A404B; font-size: 9px; font-family: 'Inter'; letter-spacing: 2px;")
            status_lbl = QLabel("— NO FILE"); status_lbl.setStyleSheet("color: #C9CED7; font-size: 9px; font-family: 'Inter';")
            hdr_layout.addWidget(dot); hdr_layout.addWidget(name_lbl); hdr_layout.addWidget(status_lbl); hdr_layout.addStretch()
            slot_layout.addWidget(hdr)
            vlc_container = QWidget(); vlc_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            vlc_container.setStyleSheet("background-color: #F6F7F9;")
            slot_layout.addWidget(vlc_container, 1)
            ph = QLabel(placeholder_text, vlc_container); ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setStyleSheet("color: #C9CED7; font-size: 10px; font-family: 'Inter'; letter-spacing: 1px; background-color: transparent;")
            ph.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True); ph.show()
            def _resize_container(event, _ph=ph, _c=vlc_container): _ph.setGeometry(0, 0, _c.width(), _c.height())
            vlc_container.resizeEvent = _resize_container
            def _resize_slot(event, _hdr=hdr): _hdr.raise_()
            slot.resizeEvent = _resize_slot
            slot._dot = dot; slot._status = status_lbl; slot._ph = ph; slot._vlc_container = vlc_container
            return slot, vlc_container

        slot1, self.video_display_1 = _make_video_slot("DSLR  01", "[ DSLR 01  —  UPLOAD VIDEO TO VIEW ]")
        slot2, self.video_display_2 = _make_video_slot("DSLR  02", "[ DSLR 02  —  UPLOAD VIDEO TO VIEW ]")
        slot3, self.video_display_3 = _make_video_slot("POV     01", "[ POV 01  —  UPLOAD VIDEO TO VIEW ]")
        slot4, self.video_display_4 = _make_video_slot("POV     02", "[ POV 02  —  UPLOAD VIDEO TO VIEW ]")
        self._video_slots = [slot1, slot2, slot3, slot4]
        self.video_widgets = [self.video_display_1, self.video_display_2, self.video_display_3, self.video_display_4]
        vgrid.addWidget(slot1,0,0); vgrid.addWidget(slot2,0,1); vgrid.addWidget(slot3,1,0); vgrid.addWidget(slot4,1,1)
        va_layout.addLayout(vgrid); right_vbox.addWidget(video_area)

        eeg_row_widget = QWidget(); eeg_row_widget.setFixedHeight(320)
        eeg_row_widget.setStyleSheet("background-color: #F6F7F9;")
        eeg_row_layout = QVBoxLayout(eeg_row_widget); eeg_row_layout.setContentsMargins(0,0,0,0); eeg_row_layout.setSpacing(0)
        eeg_row_layout.addWidget(make_source_label("EEG OVERLAY"))
        eeg_cols_widget = QWidget(); eeg_cols_widget.setStyleSheet("background-color: #F6F7F9;")
        eeg_cols = QHBoxLayout(eeg_cols_widget); eeg_cols.setContentsMargins(12,4,12,8); eeg_cols.setSpacing(6)
        self.eeg_review_1 = EEGReviewWidget(channel_id=1); self.eeg_review_2 = EEGReviewWidget(channel_id=2)
        eeg_cols.addWidget(self.eeg_review_1); eeg_cols.addWidget(self.eeg_review_2)
        eeg_row_layout.addWidget(eeg_cols_widget, 1); right_vbox.addWidget(eeg_row_widget)
        right_scroll.setWidget(right_content); preview_layout.addWidget(right_scroll, stretch=1)

        # 84px (not 52) — TimelineWithMarkers adds a 32px marker strip
        # below the slider for phase_markers.csv chips.
        controls = QWidget(); controls.setFixedHeight(84)
        controls.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        controls.setStyleSheet("background-color: #F6F7F9; border-top: 2px solid #DFE2E8;")
        ctrl_layout = QHBoxLayout(controls); ctrl_layout.setContentsMargins(16,0,16,0); ctrl_layout.setSpacing(10)
        self.play_btn = QPushButton("▶  PLAY"); self.play_btn.setFixedWidth(110); self.play_btn.setFixedHeight(34)
        self.play_btn.setEnabled(False)
        self.play_btn.setStyleSheet("background-color: #FFFFFF; border: 1px solid #C9CED7; color: #15171C; font-family: 'Inter'; font-size: 11px; letter-spacing: 1px; padding: 6px;")
        self.play_btn.clicked.connect(self._toggle_play)
        self.tc_label = QLabel("00:00:00.000"); self.tc_label.setObjectName("timecode"); self.tc_label.setFixedWidth(110)
        self.time_slider = TimelineWithMarkers(); self.time_slider.setRange(0,0); self.time_slider.setValue(0)
        self.time_slider.sliderMoved.connect(self._set_position)
        self.time_slider.markerSeek.connect(self._on_marker_seek)
        self.duration_label = QLabel("--:--:--.---")
        self.duration_label.setStyleSheet("color: #3A404B; font-size: 11px; font-family: 'Inter'; letter-spacing: 1px;")
        self.duration_label.setFixedWidth(90)
        ctrl_layout.addWidget(self.play_btn); ctrl_layout.addWidget(self.tc_label)
        ctrl_layout.addWidget(self.time_slider); ctrl_layout.addWidget(self.duration_label)
        splitter.addWidget(preview_widget); splitter.setSizes([400, 1200])
        splitter.setStretchFactor(0,0); splitter.setStretchFactor(1,1)
        root.addWidget(splitter, stretch=1); root.addWidget(controls)
        self.sync_btn.clicked.connect(self._align); self.goto_label_btn.clicked.connect(self._request_label_tab)

    # ── Video loading — VLC vmem → QPainter (no native NSView / Metal) ──────────
    #
    # VLC decodes the video and writes BGRA pixels into a ctypes buffer.
    # A display callback fires for each frame, wraps the buffer in a QImage,
    # and schedules an update() on the VideoSurface widget.  VideoSurface draws
    # with QPainter inside paintEvent — it is a plain QWidget with no native
    # NSView, so it is fully clipped by its parent and can never overflow the
    # header or tab bar regardless of macOS compositor behaviour.

    def _load_video_vmem(self, video_num, path):
        import ctypes
        i = video_num - 1; self.video_paths[i] = path
        container = self.video_widgets[i]

        old = self.media_players[i]
        if old is not None: old.stop()
        old_vw = self._video_widgets_qt[i]
        if old_vw is not None:
            if container.layout(): container.layout().removeWidget(old_vw)
            old_vw.setParent(None); old_vw.deleteLater()
            self._video_widgets_qt[i] = None

        surface = VideoSurface(container)
        if container.layout() is None:
            from PyQt6.QtWidgets import QVBoxLayout as _VBL
            _lay = _VBL(container); _lay.setContentsMargins(0,0,0,0); _lay.setSpacing(0)
        container.layout().addWidget(surface); surface.show()
        self._video_widgets_qt[i] = surface

        src_w, src_h = 1920, 1080
        try:
            import ffmpeg as _ff
            probe = _ff.probe(path)
            vs = next((s for s in probe["streams"] if s["codec_type"] == "video"), None)
            if vs: src_w = int(vs.get("width", 1920)); src_h = int(vs.get("height", 1080))
        except Exception as e:
            log.warning("dimension probe failed for %s: %r — assuming 1920x1080", path, e)
        width = src_w // 2; height = src_h // 2
        # Align pitch to a 32-byte boundary.
        # VLC's swscaler pads rows internally to 16 bytes; QImage expects the
        # same pitch we tell VLC.  A mismatch on the last scanline causes the
        # green artefact seen on the bottom-right camera.
        raw_pitch = width * 4
        pitch = (raw_pitch + 31) & ~31   # round up to next multiple of 32
        # Re-derive the buffer width from the aligned pitch so QImage rows match
        buf_width = pitch // 4           # pixels per row as seen by QImage

        bufs = [(ctypes.c_uint8 * (pitch * height))() for _ in range(3)]
        # Initialise every byte so that if VLC's lock callback fires before
        # the first full frame lands, the stale buffer shows opaque black
        # rather than whatever colour the uninitialised bytes happen to
        # decode to. In BGRA, zeroed memory is transparent black; random
        # memory often reads as green (G byte dominates). We initialise to
        # opaque black — zero BGR + alpha 0xFF.
        #
        # The bottom-line green artefact seen on the bottom-left camera slot
        # comes from the same mechanism: VLC's last-row write sometimes races
        # the lock callback, so the bottom scanline is read while partially
        # uninitialised. Zeroing the buffer up-front eliminates the artefact
        # everywhere.
        import struct
        _black_px = struct.pack("BBBB", 0x00, 0x00, 0x00, 0xFF)  # BGRA opaque black
        _row = _black_px * buf_width
        for b in bufs:
            ctypes.memmove(b, _row * height, pitch * height)
        st   = {'w': 0, 'r': 1, 'd': 2, 'fresh': False}
        # Pass buf_width (aligned) so QImage reads the correct row stride,
        # and real_w (actual pixels) so paintEvent crops the padding columns.
        surface.attach_vmem(bufs, st, buf_width, height, pitch, real_w=width)

        @vlc.CallbackDecorators.VideoLockCb
        def _lock(opaque, planes):
            planes[0] = ctypes.cast(bufs[st['w']], ctypes.c_void_p); return None

        @vlc.CallbackDecorators.VideoUnlockCb
        def _unlock(opaque, picture, planes):
            st['w'], st['r'] = st['r'], st['w']; st['fresh'] = True

        @vlc.CallbackDecorators.VideoDisplayCb
        def _display(opaque, picture): pass

        _inst = vlc.Instance()
        player = _inst.media_player_new()
        player._vlc_instance_ref = _inst
        player.video_set_callbacks(_lock, _unlock, _display, None)
        # Tell VLC: decode to `width` pixels per row, but write rows with
        # the aligned `pitch` bytes — this matches what the buffer was allocated for.
        player.video_set_format("BGRA", width, height, pitch)
        media = _inst.media_new(Path(path).as_uri()); player.set_media(media)
        player._vmem_refs = (_lock, _unlock, _display, bufs, st)
        self.media_players[i] = player

        em = player.event_manager()
        em.event_attach(vlc.EventType.MediaPlayerTimeChanged,   lambda e, _i=i: self._vlc_time_changed(_i, e))
        em.event_attach(vlc.EventType.MediaPlayerLengthChanged, lambda e, _i=i: self._vlc_length_changed(_i, e))

        def _prewarm(_p=player):
            _p.play()
            def _pause_when_ready(_count=[0], _pp=_p):
                if _pp.get_state() == vlc.State.Playing:
                    _pp.pause(); _pp.set_time(0)
                elif _count[0] < 100:
                    _count[0] += 1; QTimer.singleShot(100, _pause_when_ready)
            QTimer.singleShot(100, _pause_when_ready)
        QTimer.singleShot(200, _prewarm)

    # ── Platform dispatch — Windows uses HWND, macOS uses vmem ──────────────

    def _load_video(self, video_num: int, path: str):
        """
        Platform-aware video loader.
        Windows: give VLC the HWND directly for GPU-accelerated rendering.
        macOS/Linux: use vmem triple-buffer to avoid NSView compositor issues.
        """
        import sys as _sys
        if _sys.platform == "win32":
            self._load_video_hwnd(video_num, path)
        else:
            self._load_video_vmem(video_num, path)

    def _load_video_hwnd(self, video_num: int, path: str):
        """
        Windows renderer — give VLC the native HWND for hardware-accelerated
        compositing. No ctypes buffer copies, no QPainter blits.
        """
        i = video_num - 1
        self.video_paths[i] = path
        container = self.video_widgets[i]

        old = self.media_players[i]
        if old is not None:
            old.stop()
        old_vw = self._video_widgets_qt[i]
        if old_vw is not None:
            if container.layout():
                container.layout().removeWidget(old_vw)
            old_vw.setParent(None)
            old_vw.deleteLater()
            self._video_widgets_qt[i] = None

        from PyQt6.QtWidgets import QVBoxLayout as _VBL
        win_widget = QWidget(container)
        win_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        win_widget.setStyleSheet("background-color: #F6F7F9;")
        if container.layout() is None:
            _lay = _VBL(container)
            _lay.setContentsMargins(0, 0, 0, 0)
        container.layout().addWidget(win_widget)
        win_widget.show()
        self._video_widgets_qt[i] = win_widget

        _inst = vlc.Instance()
        player = _inst.media_player_new()
        player._vlc_instance_ref = _inst

        hwnd = int(win_widget.winId())
        player.set_hwnd(hwnd)

        media = _inst.media_new(Path(path).as_uri())
        player.set_media(media)
        self.media_players[i] = player

        em = player.event_manager()
        em.event_attach(vlc.EventType.MediaPlayerTimeChanged,
                        lambda e, _i=i: self._vlc_time_changed(_i, e))
        em.event_attach(vlc.EventType.MediaPlayerLengthChanged,
                        lambda e, _i=i: self._vlc_length_changed(_i, e))

        def _prewarm(_p=player):
            _p.play()
            def _pause_when_ready(_count=[0], _pp=_p):
                if _pp.get_state() == vlc.State.Playing:
                    _pp.pause(); _pp.set_time(0)
                elif _count[0] < 100:
                    _count[0] += 1; QTimer.singleShot(80, _pause_when_ready)
            QTimer.singleShot(100, _pause_when_ready)
        QTimer.singleShot(200, _prewarm)


    def _raise_ui_above_vlc(self) -> None:
        pass  # vmem uses no native view

    def _vlc_time_changed(self, player_idx, event):
        if self.sync_result is not None: return
        p = self.media_players[player_idx]
        if p:
            ms = p.get_time()
            if ms >= 0: QTimer.singleShot(0, lambda: self._position_changed(ms))

    def _vlc_length_changed(self, player_idx, event):
        p = self.media_players[player_idx]
        if p:
            ms = p.get_length()
            if ms > 0: QTimer.singleShot(0, lambda: self._duration_changed(ms))

    def _on_file_loaded(self, source_id, path):
        if source_id < 4:
            self._load_video(source_id + 1, path); self.play_btn.setEnabled(True)
            slot = self._video_slots[source_id]; slot._ph.hide()
            fname = path.split("/")[-1]; short = fname[:12] + "…" if len(fname) > 12 else fname
            slot._dot.setStyleSheet("color: #15803D; font-size: 10px;")
            slot._status.setText(f"— {short}"); slot._status.setStyleSheet("color: #15171C; font-size: 9px; font-family: 'Inter';")
        elif source_id == 4:
            self.eeg_file_name_1 = path
            self._load_eeg_async(path, self.eeg_review_1)
        elif source_id == 5:
            self.eeg_file_name_2 = path
            self._load_eeg_async(path, self.eeg_review_2)

    def _load_eeg_async(self, path: str, widget) -> None:
        """Load a Muse CSV via a QThread so delivery back to the main thread is safe."""
        # QTimer.singleShot from a plain daemon thread is not safe on macOS —
        # it silently drops the call. Use a QThread with a signal instead.
        class _EEGLoader(QThread):
            done = pyqtSignal(object)  # emits the DataFrame
            def __init__(self, p, parent=None):
                super().__init__(parent); self._p = p
            def run(self):
                try:
                    df = pd.read_csv(self._p)
                    log.debug("EEG load: columns=%s rows=%s", list(df.columns)[:8], len(df))
                    col_map = {c.lower(): c for c in df.columns}
                    if 'timestamps' not in df.columns and 'timestamps' in col_map:
                        df = df.rename(columns={col_map['timestamps']: 'timestamps'})
                    self.done.emit(df)
                except Exception as e:
                    log.error("EEG CSV load failed: %s", e)

        loader = _EEGLoader(path, parent=self)
        # Keep reference so GC doesn't destroy it before it finishes
        if not hasattr(self, '_eeg_loaders'):
            self._eeg_loaders = []
        self._eeg_loaders.append(loader)

        def _on_done(df, _w=widget, _l=loader):
            try:
                _w.load(df, offset_sec=0.0)
            except Exception:
                log.error("EEG review widget load failed", exc_info=True)
            finally:
                # Wait for the thread's run() to fully exit before letting
                # Python GC reclaim it — prevents "QThread destroyed while
                # still running" on macOS.
                try: _l.wait(2000)
                except Exception: pass
                if _l in self._eeg_loaders:
                    self._eeg_loaders.remove(_l)

        loader.done.connect(_on_done)
        loader.start()

    def shutdown(self) -> None:
        """Cleanly stop any in-flight loader threads before Qt teardown."""
        for loader in list(getattr(self, '_eeg_loaders', [])):
            try:
                loader.quit()
                loader.wait(2000)
            except Exception:
                pass
        self._eeg_loaders = []
        # Stop the sync analysis worker if it's mid-run
        worker = getattr(self, '_worker', None)
        if worker is not None:
            try:
                worker.quit()
                worker.wait(2000)
            except Exception:
                pass

    def _raise_overlays(self): pass

    # NOTE: playback lockstep across the four players rests solely on
    # issuing play() to all of them in the same event-loop turn (plus the
    # per-clip seek offsets). There is NO active drift correction — a
    # _drift_hb/_nudge_drift heartbeat existed as dead code (guarded but
    # never created) and was removed. Revisit once real multi-minute
    # capture data exists to observe whether VLC clocks actually drift.

    def _toggle_play(self):
        active = [p for p in self.media_players if p]
        any_playing = any(p.get_state() == vlc.State.Playing for p in active)
        if any_playing:
            for p in active: p.pause()
            self.play_btn.setText("▶  PLAY"); self._pos_timer.stop()
        else:
            for p in active: p.play()
            self.play_btn.setText("⏸  PAUSE"); self._pos_timer.start()

    def _set_position(self, position_ms):
        # With the corrected sync formula: each clip's local time = global_t + seek_offset.
        # The reference clip (seek=0) plays at exactly global_t.
        seek_targets = getattr(self, "_residuals_ms", [])
        for i, p in enumerate(self.media_players):
            if p:
                my_seek = seek_targets[i] if seek_targets and i < len(seek_targets) else 0
                p.set_time(_clamp_ms(position_ms + my_seek))
        self._update_tc(position_ms)
        self.eeg_review_1.set_playhead(position_ms / 1000.0)
        self.eeg_review_2.set_playhead(position_ms / 1000.0)

    def _position_changed(self, ms):
        if self.sync_result is not None: return
        self.time_slider.blockSignals(True); self.time_slider.setValue(_clamp_ms(ms)); self.time_slider.blockSignals(False)
        self._update_tc(ms)

    def _duration_changed(self, ms):
        if self.sync_result is not None: return
        if ms > 0:
            self.time_slider.setRange(0, _clamp_ms(ms))
            self.time_slider.set_duration_sec(ms / 1000.0)
            secs = ms / 1000.0; h = int(secs//3600); m = int((secs%3600)//60); s = secs%60
            self.duration_label.setText(f"{h:02d}:{m:02d}:{s:06.3f}")

    def _on_marker_seek(self, t_sec: float):
        """User clicked a phase-marker chip on the timeline strip — snap the
        playhead (all players + EEG overlays) to that timestamp."""
        ms = _clamp_ms(t_sec * 1000.0)
        self.time_slider.blockSignals(True)
        self.time_slider.setValue(ms)
        self.time_slider.blockSignals(False)
        self._set_position(ms)

    def _poll_position(self):
        # Read global_t from the reference player (seek=0 or smallest seek).
        # global_t = player_local_time - seek_offset.
        seek_targets = getattr(self, "_residuals_ms", [])
        for i, p in enumerate(self.media_players):
            if p:
                t = p.get_time()
                my_seek = seek_targets[i] if seek_targets and i < len(seek_targets) else 0
                global_t = _clamp_ms(t - my_seek)
                self.time_slider.blockSignals(True)
                self.time_slider.setValue(global_t)
                self.time_slider.blockSignals(False)
                self._update_tc(global_t)
                self.eeg_review_1.set_playhead(global_t / 1000.0)
                self.eeg_review_2.set_playhead(global_t / 1000.0)
                break

    def _update_tc(self, ms):
        secs = ms / 1000.0; h = int(secs//3600); m = int((secs%3600)//60); s = secs%60
        self.tc_label.setText(f"{h:02d}:{m:02d}:{s:06.3f}")

    def _align(self):
        """Align the loaded session by its recorded capture-time stamps —
        the single alignment method (v3, all sources come from the recording
        app). Each camera's measured start (from its sidecar) is its offset;
        the latest start (axis_shift) is the playhead-0 reference;
        seek = axis_shift - start. Synchronous — no audio, no discovery."""
        video_paths = [s.file_path for s in self.sources[:4] if s.file_path]
        if len(video_paths) < 2:
            QMessageBox.warning(self, "Not enough sources", "A session needs at least 2 camera recordings to align."); return
        if self._capture_t0_unix is None:
            QMessageBox.warning(
                self, "No capture clock",
                "This session has no capture_clock. v3 aligns by capture-time "
                "stamps; sessions recorded before v3.0 are not supported."); return
        self.sync_btn.setEnabled(False); self.goto_label_btn.setEnabled(False)
        self.sync_progress.setVisible(True); self.sync_progress.setValue(100)
        self._set_sync_status("ALIGNING BY CAPTURE CLOCK…", "#3D4FD6")
        cam_starts = [self._cam_starts[i] if i < len(self._cam_starts) else 0.0
                      for i in range(len(video_paths))]
        max_start = max(cam_starts) if cam_starts else 0.0
        seek_ms = [_clamp_ms((max_start - d) * 1000) for d in cam_starts]
        # Drive the player/EEG/timeline apply path (_on_sync_finished) with the
        # per-source offsets + seeks in the payload shape it consumes.
        payload = ([float(d) for d in cam_starts] + [float(max_start)]
                   + [float(s) for s in seek_ms])
        self._on_sync_finished(video_paths, payload)

    def _on_sync_finished(self, video_paths, delays_with_sentinel):
        self.sync_progress.setValue(100)
        n_clips = len(video_paths)
        delays = list(delays_with_sentinel[:n_clips])
        max_norm_delay = float(delays_with_sentinel[n_clips]) if len(delays_with_sentinel) > n_clips else 0.0
        seek_ms_list = [int(delays_with_sentinel[n_clips+1+i]) if len(delays_with_sentinel) > n_clips+1+i else 0 for i in range(n_clips)]
        self._temp_paths = video_paths; self._delays = delays; self._residuals_ms = seek_ms_list

        from sync_engine import SourceOffset, SyncResult as _SR
        video_sources = [s for s in self.sources[:4] if s.file_path]
        # Single alignment method: capture_clock. offset_sec = each camera's
        # measured start on the shared axis; confidence is not applicable
        # (there is no correlation to score) — None flows to the CSV as the
        # honest "n/a (capture_clock)" sentinel.
        video_offsets = [SourceOffset(source_id=f"VIDEO_{i+1:02d}", path=src.file_path, offset_sec=delays[i] if i < len(delays) else 0.0, is_reference=(delays[i] == 0.0 if i < len(delays) else True), method="capture_clock", confidence=None) for i, src in enumerate(video_sources)]
        eeg_sources = [s for s in self.sources[4:] if s.file_path]
        # EEG is placed on the shared axis by its t_session_seconds column;
        # its offset is the axis_shift the widget applies — confidence None.
        eeg_offsets = [SourceOffset(source_id=f"EEG_{i+1:02d}", path=s.file_path, offset_sec=0.0, method="capture_clock", confidence=None) for i, s in enumerate(eeg_sources)]
        global_duration = 0.0
        for i, path in enumerate(video_paths):
            if path and os.path.exists(path):
                try:
                    import ffmpeg as _ffmpeg; probe = _ffmpeg.probe(path)
                    d = float(probe["format"].get("duration", 0.0))
                    seek_s = seek_ms_list[i] / 1000.0 if i < len(seek_ms_list) else 0.0
                    content_dur = max(d - seek_s, 0.0)
                    global_duration = content_dur if global_duration == 0.0 else min(global_duration, content_dur)
                except Exception as e:
                    log.warning("duration probe failed for %s: %r — global duration may be wrong", path, e)
        self.sync_result = _SR(global_duration=max(global_duration, 0.0), video_offsets=video_offsets, eeg_offsets=eeg_offsets)

        target_seeks = [_clamp_ms(s) for s in seek_ms_list]
        for i, path in enumerate(video_paths):
            p = self.media_players[i] if i < len(self.media_players) else None
            if p and path:
                _pinst = getattr(p, '_vlc_instance_ref', None)
                media = (_pinst.media_new if _pinst else vlc.Media)(Path(path).as_uri())
                p.set_media(media); p.play()

        _poll_count = [0]
        def _wait_and_seek():
            _poll_count[0] += 1
            active = [p for p in self.media_players if p]
            if not all(p.get_length() > 0 for p in active) and _poll_count[0] < 100:
                QTimer.singleShot(100, _wait_and_seek); return
            for i, p in enumerate(self.media_players):
                if p:
                    seek = target_seeks[i] if i < len(target_seeks) else 0
                    p.pause(); p.set_time(seek); log.debug("player %d: set_time(%sms)", i, seek)
            # Global usable duration = min(clip_length - seek) across all players
            min_usable_ms = 0
            for i, p in enumerate(self.media_players):
                if p and p.get_length() > 0:
                    seek = target_seeks[i] if i < len(target_seeks) else 0
                    usable = p.get_length() - seek
                    if usable > 0:
                        min_usable_ms = usable if min_usable_ms == 0 else min(min_usable_ms, usable)
            if min_usable_ms > 0:
                self.time_slider.setRange(0, _clamp_ms(min_usable_ms))
                self.time_slider.set_duration_sec(min_usable_ms / 1000.0)
                self.time_slider.setValue(0)
                secs = min_usable_ms / 1000.0; h = int(secs//3600); m = int((secs%3600)//60); s = secs%60
                self.duration_label.setText(f"{h:02d}:{m:02d}:{s:05.2f}")
            else:
                self.time_slider.setRange(0, 0)
        QTimer.singleShot(200, _wait_and_seek)

        self.time_slider.setRange(0, 0); self.time_slider.setValue(0)
        self.play_btn.setText("▶  PLAY"); self.play_btn.setEnabled(True); self._post_sync_offsets_ms = [0]*4
        for i, (src_row, delay) in enumerate(zip(self.sources[:4], delays)): src_row.set_offset(delay)
        eeg_sources = [s for s in self.sources[4:] if s.file_path]
        for i, widget in enumerate([self.eeg_review_1, self.eeg_review_2]):
            if i < len(eeg_sources):
                # offset_sec = axis_shift places EEG on the playhead axis:
                # its t_session_seconds column, shifted to playhead 0 = the
                # latest source start. Co-registers EEG with markers + video.
                try: df = pd.read_csv(eeg_sources[i].file_path); widget.load(df, offset_sec=self._axis_shift)
                except Exception as e: log.warning("EEG %d load for review failed: %s", i + 1, e)
        msg = "ALIGNED BY CAPTURE-TIME CLOCK"
        if self._phase_markers:
            msg += f"  ·  {len(self._phase_markers)} MARKERS LOADED"
        self._set_sync_status(msg, "#15803D"); self.sync_btn.setEnabled(True); self.goto_label_btn.setEnabled(True)
        log.info("capture-clock alignment · offsets (seek per player): %s ms", seek_ms_list)

    def _set_sync_status(self, text, color):
        self.sync_status.setText(text)
        self.sync_status.setStyleSheet(f"color: {color}; font-size: 10px; font-family: 'Inter'; letter-spacing: 2px; padding: 8px 0;")

    def _sync_offsets_rows(self) -> list:
        """Rows for sync_offsets.csv. Under capture-clock alignment there is
        no correlation to score, so `confidence` is None and the cell records
        the honest `n/a (capture_clock)` sentinel instead of a fake number.
        `method` and `offset_sec` are always written."""
        if not self.sync_result:
            return []
        return [{
            "source": o.source_id,
            "offset_sec": o.offset_sec,
            "method": o.method,
            "confidence": (round(o.confidence, 3) if o.confidence is not None
                           else f"n/a ({o.method})"),
            "is_reference": o.is_reference,
        } for o in self.sync_result.all_offsets()]

    def _read_markers_for_export(self) -> list:
        """(t_session_seconds, phase_id, label) triples from the loaded
        session's phase_markers.csv — raw session axis (unshifted), matching
        the EEG t_session_seconds. Empty if no session/markers file."""
        import csv as _csv
        from pathlib import Path as _P
        if not self._session_root:
            return []
        path = _P(self._session_root) / "phase_markers.csv"
        if not path.exists():
            return []
        out = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    try:
                        t = float(row.get("t_session_seconds", ""))
                    except (TypeError, ValueError):
                        continue
                    out.append((t, (row.get("phase_id") or "").strip(),
                                (row.get("label") or "").strip()))
        except Exception as e:
            log.warning("could not read markers for export: %s", e)
        return out

    def _export_synced_data(self):
        from PyQt6.QtWidgets import QInputDialog
        if not self.sync_result: QMessageBox.warning(self, "No aligned data", "Click ALIGN before exporting."); return
        zip_name, ok = QInputDialog.getText(self, "Export", "Name this export:", text="Brain_Battle_Session")
        if not ok or not zip_name.strip(): return
        zip_name = zip_name.strip().replace(" ", "_")
        if not zip_name.endswith(".zip"): zip_name += ".zip"
        save_path, _ = QFileDialog.getSaveFileName(self, "Save", zip_name, "ZIP (*.zip)")
        if not save_path: return
        video_paths = [s.file_path or "" for s in self.sources[:4]]
        offset_secs = [self.sync_result.video_offsets[i].offset_sec if i < len(self.sync_result.video_offsets) else 0.0 for i in range(4)]
        eeg_paths = [s.file_path or "" for s in self.sources[4:]]
        sync_rows = self._sync_offsets_rows()
        # Phase markers on the RAW session axis (t_session, phase_id, label) —
        # same axis as the EEG's t_session_seconds, for the co-registered file.
        markers = self._read_markers_for_export()
        worker = FullSessionExportWorker(zip_path=save_path, zip_name=zip_name, video_paths=video_paths, offset_secs=offset_secs, eeg_paths=eeg_paths, labels=[], sync_offsets_rows=sync_rows, global_duration=self.sync_result.global_duration, merge_video=True, phase_markers=markers, parent=self)
        if self._session_root:
            session_history.log_event(
                self._session_root, session_history.EVENT_EXPORT_CREATED,
                {"name": zip_name, "to": save_path})
        ExportProgressDialog(worker, parent=self).exec()

    def _request_label_tab(self):
        parent = self.parent()
        while parent and not isinstance(parent, QTabWidget): parent = parent.parent()
        if not parent: return
        parent.setCurrentIndex(2); label_tab = parent.widget(2)
        if not self.sync_result or not hasattr(label_tab, "load_session_data"): return
        video_data = [(s.file_path, self.sync_result.video_offsets[i].offset_sec if i < len(self.sync_result.video_offsets) else 0.0) for i, s in enumerate(self.sources[:4])]
        eeg_data = []
        for i, s in enumerate(self.sources[4:]):
            if s.file_path:
                try: df = pd.read_csv(s.file_path); off = self.sync_result.eeg_offsets[i].offset_sec if i < len(self.sync_result.eeg_offsets) else 0.0; eeg_data.append((df, off))
                except Exception as e:
                    log.warning("EEG CSV read for labelling handoff failed (%s): %r", s.file_path, e)
                    eeg_data.append((None, 0.0))
            else: eeg_data.append((None, 0.0))
        # Pass the session folder so the label tab autosaves/restores labels
        # into it (same labels.json the OPEN SESSION FOLDER path uses).
        label_tab.load_session_data(video_data, eeg_data, self.sync_result,
                                    session_root=self._session_root)

    def _open_session_dialog(self) -> None:
        """Operator-facing counterpart to the automatic Live→Review handoff:
        pick a session root (or its continuous/ folder) and load it. All
        discovery, fallback, and status-line logic lives in
        load_session_folder."""
        folder = QFileDialog.getExistingDirectory(self, "Open session folder", "")
        if not folder:
            return
        self.load_session_folder(folder)

    def load_session_folder(self, session_root: str, round_num: int = 1) -> None:
        """
        Auto-populate the review tab from a Brain Battle <timestamp>/ folder.

        Called by LiveSessionTab when a capture session finishes, so the
        operator can jump straight from LIVE MONITOR → SYNC + REVIEW without
        re-picking every file. Expects the v2.x continuous-recording layout
        (mirrors LabellingTab._open_session_folder):
            <session_root>/continuous/Side Cams/*.mp4
            <session_root>/continuous/POV Cams/*.mp4
            <session_root>/continuous/Muse data/*.csv
            <session_root>/phase_markers.csv  (optional)

        round_num is a legacy v1.x parameter and is ignored — v2.x sessions
        have no per-round folders; phase boundaries live in phase_markers.csv.

        Mapping (matches SourceRow definitions above):
            0-1 DSLR      ← Side Cams/*.mp4   (first two, alphabetical)
            2-3 POV       ← POV  Cams/*.mp4
            4-5 MUSE-S    ← Muse data/*.csv
        """
        from pathlib import Path as _Path
        root = _Path(session_root)
        cont = root / "continuous"
        if not cont.is_dir():
            # Operator may have handed us the continuous/ folder itself…
            if (root / "Side Cams").is_dir() or (root / "POV Cams").is_dir():
                cont = root; root = root.parent
            else:
                # …or the parent directory of the session folder — look one
                # level deep (mirrors LabellingTab._open_session_folder).
                children = sorted(root.iterdir()) if root.is_dir() else []
                for child in children:
                    if child.is_dir() and (child / "continuous").is_dir():
                        root = child; cont = child / "continuous"; break
                else:
                    self._set_sync_status(
                        f"NO continuous/ FOLDER IN {root.name}", "#B91C1C")
                    return

        def _files(subdir, patterns):
            d = cont / subdir
            out = []
            if d.is_dir():
                for pat in patterns:
                    out += sorted(p for p in d.glob(pat) if p.is_file())
            return list(dict.fromkeys(out))  # dedup (case-insensitive FS)

        side_cams = _files("Side Cams", ("*.mp4", "*.MP4"))
        pov_cams  = _files("POV Cams",  ("*.mp4", "*.MP4"))
        muse_csvs = _files("Muse data", ("*.csv", "*.CSV"))

        # Assign each discovered file into the matching SourceRow. Silently
        # skip slots with no corresponding file — operator can upload manually.
        video_paths = list(side_cams[:2]) + list(pov_cams[:2])
        for i, path in enumerate(side_cams[:2]):
            self.sources[i].set_path(str(path))
        for i, path in enumerate(pov_cams[:2]):
            self.sources[2 + i].set_path(str(path))
        for i, path in enumerate(muse_csvs[:2]):
            self.sources[4 + i].set_path(str(path))

        # ── v3 capture-time alignment (no legacy fallback) ──────────────────
        # Read the shared origin and each camera's measured start on the shared
        # axis. axis_shift = the latest source start = the moment every source
        # has data = the playhead-0 point. Markers and EEG are placed relative
        # to this same shift so all three co-register (fixes §2.4).
        self._session_root = str(root)
        self._capture_t0_unix = _read_capture_t0_unix(str(root))
        self._cam_starts = [(_read_frame_sidecar_start(str(p)) or 0.0)
                            for p in video_paths]
        starts = [s for s in self._cam_starts if s is not None]
        self._axis_shift = max(starts) if starts else 0.0
        if self._capture_t0_unix is None:
            log.warning("session %s has no capture_clock — v3 requires it "
                        "(pre-3.0 sessions are unsupported)", root.name)

        # Phase markers → chips on the timeline strip, shifted onto the
        # playhead axis (t_session - axis_shift) so they line up with video.
        self._phase_markers = []
        self.time_slider.clear_markers()
        markers_csv = root / "phase_markers.csv"
        if markers_csv.exists():
            self._phase_markers = _read_phase_markers(str(markers_csv))
            for t_sec, color, label in self._phase_markers:
                self.time_slider.add_marker(t_sec - self._axis_shift, color, label)

        found = len(side_cams[:2]) + len(pov_cams[:2]) + len(muse_csvs[:2])
        if found:
            session_history.log_event(
                str(root), session_history.EVENT_OPENED_FOR_REVIEW,
                {"files": found})
            msg = f"LOADED  {found} FILES  FROM  {root.name}"
            if self._phase_markers:
                msg += f"  ·  {len(self._phase_markers)} MARKERS"
            self._set_sync_status(msg, "#3D4FD6")
        else:
            self._set_sync_status(f"NO RECORDINGS FOUND IN {root.name}", "#B91C1C")


class LabelChip(QWidget):
    clicked = pyqtSignal(str)
    def __init__(self, label_name, color, parent=None):
        super().__init__(parent); self.label_name = label_name; self._name = label_name; self.color = color
        layout = QHBoxLayout(self); layout.setContentsMargins(0,2,0,2)
        btn = QPushButton(label_name); btn.setFixedHeight(28)
        btn.setStyleSheet(f"background-color: transparent; border: 1px solid {color}; color: {color}; font-family: 'Inter'; font-size: 11px; letter-spacing: 1px; padding: 0 10px; text-align: left;")
        btn.clicked.connect(lambda: self.clicked.emit(self.label_name)); layout.addWidget(btn)


class LabelMarker(QWidget):
    removed = pyqtSignal()
    def __init__(self, label_name, color, timestamp_str, parent=None, is_phase=False,
                 t_seconds=None, boxer=None, end_t_seconds=None):
        super().__init__(parent); self.label_name = label_name; self.timestamp_str = timestamp_str; self._color = color
        self.is_phase = is_phase
        # Numeric placement time in seconds (authoritative — timestamp_str is
        # for display). None on legacy markers built before this field, in
        # which case callers parse it back from timestamp_str. boxer /
        # end_t_seconds are Phase-4 fields carried through now as nullable so
        # the autosave schema is forward-compatible.
        self.t_seconds = t_seconds
        self.boxer = boxer
        self.end_t_seconds = end_t_seconds
        # Phase markers (auto-loaded from session) get a dashed left border
        # so they're visibly distinct from the user's manual labels.
        if is_phase:
            self.setStyleSheet(f"background-color: #F6F7F9; border-left: 2px dashed {color};")
        else:
            self.setStyleSheet("background-color: #F6F7F9;")
        layout = QHBoxLayout(self); layout.setContentsMargins(8,5,8,5); layout.setSpacing(10)
        # Triangle for manual, double-bar for phase markers (visual cue)
        dot_glyph = "║" if is_phase else "▶"
        dot = QLabel(dot_glyph); dot.setFixedWidth(12); dot.setStyleSheet(f"color: {color}; font-size: 9px; border: none;")
        ts = QLabel(timestamp_str); ts.setFixedWidth(88); ts.setStyleSheet("color: #3A404B; font-size: 10px; font-family: 'Inter'; border: none;")
        name = QLabel(label_name); name.setStyleSheet(f"color: {color}; font-size: 10px; font-family: 'Inter'; letter-spacing: 1px; border: none;")
        name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        remove = QPushButton("×"); remove.setFixedSize(20,20)
        remove.setStyleSheet("background: transparent; border: none; color: #3A404B; font-size: 14px; padding: 0;")
        remove.clicked.connect(self._remove)
        layout.addWidget(dot); layout.addWidget(ts); layout.addWidget(name); layout.addWidget(remove)
    def _remove(self):
        """Properly detach from the parent layout BEFORE notifying listeners,
        so any handler that walks the layout sees the post-removal state
        immediately. deleteLater alone is async — it leaves the widget
        parented until Qt's event loop processes it, which means listeners
        like _rebuild_timeline_markers would see this widget still in the
        layout and re-add its tick. Explicit removeWidget + setParent(None)
        unparents synchronously."""
        parent = self.parentWidget()
        if parent is not None:
            layout = parent.layout()
            if layout is not None:
                layout.removeWidget(self)
        self.setParent(None)
        self.removed.emit()
        self.deleteLater()


class TimelineWithMarkers(QWidget):
    """Composite widget: a horizontal QSlider stacked above a thin marker
    strip that paints colored ticks at each label's timestamp position.

    Public surface mirrors QSlider so the LabellingTab can drive it the
    same way it drove the bare slider: setValue, valueChanged signal,
    setRange. Add/remove markers via add_marker(t_sec, color, label) and
    clear_markers(). Clicking a tick seeks to that timestamp.

    Why a custom widget rather than custom-painting on the slider itself:
    painting on QSlider via stylesheet doesn't allow point-positioned
    marks, and overriding paintEvent on a QSlider subclass interferes
    with the handle/groove rendering on macOS. A separate strip below
    is cleaner and looks more like a real video editor timeline.
    """
    valueChanged = pyqtSignal(int)
    sliderMoved  = pyqtSignal(int)  # user-initiated drag, mirrors QSlider
    markerSeek = pyqtSignal(float)  # fires when user clicks a marker; arg = t_sec

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_sec = 0.0
        self._markers: list[tuple[float, str, str]] = []   # (t_sec, color, label)
        v = QVBoxLayout(self); v.setContentsMargins(0,0,0,0); v.setSpacing(3)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 10000); self._slider.setValue(0)
        self._slider.valueChanged.connect(self.valueChanged.emit)
        self._slider.sliderMoved.connect(self.sliderMoved.emit)
        v.addWidget(self._slider)
        self._strip = _MarkerStrip(self)
        v.addWidget(self._strip)

    # ── QSlider-compatible API ───────────────────────────────────────────────
    def setValue(self, v: int): self._slider.setValue(v)
    def value(self) -> int: return self._slider.value()
    def setRange(self, lo: int, hi: int): self._slider.setRange(lo, hi)
    def blockSignals(self, b: bool):
        super().blockSignals(b); return self._slider.blockSignals(b)

    # ── Marker management ────────────────────────────────────────────────────
    def set_duration_sec(self, dur: float):
        """Set the duration the slider/markers map to. Required for
        marker positions to render correctly — the strip needs to know
        how many seconds the slider width represents."""
        self._duration_sec = max(0.0, float(dur))
        self._strip.update()

    def add_marker(self, t_sec: float, color: str, label: str = ""):
        """Add a vertical tick at the given timestamp."""
        self._markers.append((float(t_sec), str(color), str(label)))
        self._strip.update()

    def clear_markers(self):
        self._markers.clear(); self._strip.update()

    def markers(self) -> list[tuple[float, str, str]]:
        return list(self._markers)

    def remove_marker_near(self, t_sec: float, tol_sec: float = 0.05):
        """Remove the marker closest to t_sec within tolerance, if any."""
        if not self._markers: return
        idx = min(range(len(self._markers)),
                  key=lambda i: abs(self._markers[i][0] - t_sec))
        if abs(self._markers[idx][0] - t_sec) <= tol_sec:
            self._markers.pop(idx); self._strip.update()


class _MarkerStrip(QWidget):
    """The marker strip below the slider. Paints chunky colored chips at
    each label's proportional position, with a tiny label badge above
    each one showing an abbreviated identifier (e.g. "BL", "R1", "B2",
    "END"). Click a chip to seek; hover for a full tooltip with the
    label text and timestamp.

    Strip layout (top → bottom):
       6 px  label badge band
       4 px  spacer
      16 px  chip band (colored vertical chips)
       4 px  spacer
       2 px  baseline rule + time tick scale
    Total height: 32 px.
    """

    HEIGHT       = 32
    LABEL_BAND   = 9    # height of the abbreviation badge band
    CHIP_TOP     = 13   # y where colored chips start
    CHIP_BOT     = 28   # y where colored chips end
    BASELINE_Y   = 30   # the thin horizontal rule
    PAD          = 7    # mirror QSlider handle padding so ticks align
    CHIP_HALF_W  = 5    # half-width of a chip (10 px wide total)
    HIT_TOL_PX   = 10   # click within this many px to count as a hit

    def __init__(self, parent: "TimelineWithMarkers"):
        super().__init__(parent)
        self._tl = parent
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(100)
        self.setMouseTracking(True)
        self._hover_idx: int | None = None

    @staticmethod
    def _abbrev(label: str) -> str:
        """Return a 1-3 character abbreviation for the badge above a chip.
        Patterns recognised:
          BASELINE_START   → BL
          ROUND_1_START    → R1
          BREAK_2_START    → B2
          SESSION_END      → END
          custom labels    → first 2 letters uppercase (e.g. PUNCH_LEFT → PL)
        """
        s = label.upper().replace(" ", "_")
        if s.startswith("BASELINE"): return "BL"
        if s.startswith("SESSION_END") or s == "END": return "END"
        if s.startswith("ROUND_"):
            n = s.split("_")[1]
            return f"R{n}" if n.isdigit() else "R"
        if s.startswith("BREAK_"):
            n = s.split("_")[1]
            return f"B{n}" if n.isdigit() else "B"
        # User custom label — try to grab capital letters in order, fall
        # back to first two non-underscore chars.
        caps = [c for c in s if c.isalpha()][:3]
        if "_" in s:
            parts = [p for p in s.split("_") if p]
            return "".join(p[0] for p in parts[:3]) if parts else (s[:2] or "?")
        return "".join(caps[:2]) if caps else "?"

    def _marker_x(self, t_sec: float) -> int:
        dur = self._tl._duration_sec
        if dur <= 0: return -100
        usable_w = max(1, self.width() - 2 * self.PAD)
        return self.PAD + int((t_sec / dur) * usable_w)

    def _hit_test(self, x: int) -> int | None:
        """Return the index of the marker closest to x within HIT_TOL_PX,
        or None if no marker is close enough."""
        if not self._tl._markers: return None
        best_i, best_d = None, self.HIT_TOL_PX + 1
        for i, (t, _c, _l) in enumerate(self._tl._markers):
            d = abs(self._marker_x(t) - x)
            if d < best_d:
                best_d, best_i = d, i
        return best_i

    def mouseMoveEvent(self, event):
        idx = self._hit_test(int(event.position().x()))
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.update()
            if idx is not None:
                t, _c, label = self._tl._markers[idx]
                h = int(t//3600); m = int((t%3600)//60); s = t%60
                ts = f"{h:02d}:{m:02d}:{s:06.3f}"
                self.setToolTip(f"{label}\n{ts}")
            else:
                self.setToolTip("")
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        x = int(event.position().x())
        idx = self._hit_test(x)
        if idx is not None:
            t_sec, _c, _l = self._tl._markers[idx]
            self._tl.markerSeek.emit(t_sec)
        else:
            dur = self._tl._duration_sec
            if dur > 0:
                usable_w = max(1, self.width() - 2 * self.PAD)
                frac = max(0.0, min(1.0, (x - self.PAD) / usable_w))
                self._tl.markerSeek.emit(frac * dur)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QPen, QColor, QFont
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # Strip background — clearly distinct from the canvas so the
        # strip reads as a discrete "lane" you can click into.
        p.fillRect(self.rect(), QColor("#EDEFF2"))
        # Subtle frame top + bottom so the strip is bounded visually
        p.setPen(QPen(QColor("#C9CED7"), 1))
        p.drawLine(0, 0, w, 0); p.drawLine(0, h-1, w, h-1)
        # Baseline rule for time ticks
        p.drawLine(self.PAD, self.BASELINE_Y, w - self.PAD, self.BASELINE_Y)
        dur = self._tl._duration_sec
        if dur <= 0: p.end(); return
        # Time-tick scale every 30s on long sessions, every 10% on short
        tick_interval = 30.0 if dur >= 60 else max(5.0, dur / 10)
        t = 0.0
        p.setPen(QPen(QColor("#C9CED7"), 1))
        while t <= dur:
            x = self._marker_x(t)
            p.drawLine(x, self.BASELINE_Y - 2, x, self.BASELINE_Y + 1)
            t += tick_interval
        # Label-badge font
        badge_font = QFont("Inter", 7); badge_font.setWeight(QFont.Weight.Bold)
        p.setFont(badge_font)
        # Markers
        for i, (t_sec, color, label) in enumerate(self._tl._markers):
            x = self._marker_x(t_sec)
            c = QColor(color)
            is_hover = (i == self._hover_idx)
            # Soft halo glow around the chip
            halo = QColor(c); halo.setAlpha(120 if is_hover else 70)
            p.fillRect(x - self.CHIP_HALF_W - 2, self.CHIP_TOP - 1,
                       2 * self.CHIP_HALF_W + 4, (self.CHIP_BOT - self.CHIP_TOP) + 2, halo)
            # Solid chip body
            p.fillRect(x - self.CHIP_HALF_W, self.CHIP_TOP,
                       2 * self.CHIP_HALF_W, self.CHIP_BOT - self.CHIP_TOP, c)
            # Top accent — lighter line so chips read as 3-D and aren't flat
            cap = QColor(c).lighter(150)
            p.fillRect(x - self.CHIP_HALF_W, self.CHIP_TOP,
                       2 * self.CHIP_HALF_W, 2, cap)
            # Label badge above the chip
            abbrev = self._abbrev(label)
            badge_w = max(18, len(abbrev) * 7 + 4)
            badge_x = x - badge_w // 2
            badge_y = 1
            # Badge background — same color but desaturated/lighter
            badge_bg = QColor(c).lighter(110); badge_bg.setAlpha(220)
            p.fillRect(badge_x, badge_y, badge_w, self.LABEL_BAND, badge_bg)
            # Badge text — high-contrast (white on saturated colors)
            p.setPen(QPen(QColor("#FFFFFF")))
            p.drawText(badge_x, badge_y, badge_w, self.LABEL_BAND,
                       Qt.AlignmentFlag.AlignCenter, abbrev)
            # Thin connector line from badge down to chip top
            p.setPen(QPen(c, 1))
            p.drawLine(x, self.LABEL_BAND, x, self.CHIP_TOP)
        p.end()


class LabellingTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent); self._fake_ts = 0.0
        # Session folder autosave of user labels lands at <root>/labels.json.
        # None until a session is opened; restoring suppresses per-insert saves.
        self._session_root = None
        self._restoring = False
        self._build()
        self._install_shortcuts()
        import design_tokens as _dt
        self.setStyleSheet(f"LabellingTab {{ background: {_dt.canvas_bg()}; }}")

    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        toolbar = make_opaque_toolbar(44)
        tb = QHBoxLayout(toolbar); tb.setContentsMargins(16,0,16,0); tb.setSpacing(8)
        tb.addWidget(make_panel_title("EVENT LABELLING")); tb.addStretch()
        self.open_session_btn = QPushButton("OPEN SESSION FOLDER"); self.open_session_btn.setFixedHeight(28)
        self.open_session_btn.setStyleSheet("background-color: #EEF0FD; border: 1px solid #3D4FD6; color: #3D4FD6; font-family: 'Inter'; font-size: 10px; letter-spacing: 2px; padding: 0 14px;")
        self.open_session_btn.clicked.connect(self._open_session_folder)
        tb.addWidget(self.open_session_btn)
        self.export_csv_btn = QPushButton("EXPORT LABELS CSV"); self.export_csv_btn.setFixedHeight(28)
        self.export_csv_btn.setStyleSheet("background-color: #EEF0FD; border: 1px solid #3D4FD6; color: #3D4FD6; font-family: 'Inter'; font-size: 10px; letter-spacing: 2px; padding: 0 14px;")
        tb.addWidget(self.export_csv_btn); root.addWidget(toolbar)
        QTimer.singleShot(0, toolbar.raise_)
        self._toolbar = toolbar
        body = QHBoxLayout(); body.setContentsMargins(0,0,0,0); body.setSpacing(0)

        # LEFT RAIL — 400px wide, mirrors Sync + Review structure:
        # top: DATA SOURCES (upload rows) + LABEL PALETTE
        # bottom: PLACED LABELS list
        left = QWidget(); left.setFixedWidth(400)
        left.setStyleSheet("background-color: #F6F7F9; border-right: 1px solid #DFE2E8;")
        left_outer = QVBoxLayout(left); left_outer.setContentsMargins(0,0,0,0); left_outer.setSpacing(0)

        # --- DATA SOURCES (uploads) — mirrors SyncReviewTab ---------------------
        sources_panel = QWidget()
        sources_panel.setStyleSheet("background-color: #F6F7F9;")
        sources_layout = QVBoxLayout(sources_panel)
        sources_layout.setContentsMargins(10, 14, 10, 10); sources_layout.setSpacing(6)
        sources_layout.addWidget(make_source_label("DATA SOURCES"))
        sources_layout.addSpacing(2)
        # Column headers
        header = QHBoxLayout(); header.setSpacing(6)
        for txt, w in [("", 14), ("TYPE", 100), ("FILE", 0), ("", 72)]:
            h = QLabel(txt); h.setStyleSheet("color: #C9CED7; font-size: 9px; font-family: 'Inter'; letter-spacing: 2px;")
            if w: h.setFixedWidth(w)
            else: h.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            header.addWidget(h)
        sources_layout.addLayout(header); sources_layout.addWidget(make_divider())
        # Upload rows: 4 video slots + 2 Muse-S CSVs (matches Sync tab)
        self.label_sources = []
        label_source_defs = [
            ("DSLR  01", "Video Files (*.mp4 *.mov *.avi *.MP4 *.MOV)"),
            ("DSLR  02", "Video Files (*.mp4 *.mov *.avi *.MP4 *.MOV)"),
            ("POV     01", "Video Files (*.mp4 *.mov *.avi *.MP4 *.MOV)"),
            ("POV     02", "Video Files (*.mp4 *.mov *.avi *.MP4 *.MOV)"),
            ("MUSE-S  01", "CSV Files (*.csv)"),
            ("MUSE-S  02", "CSV Files (*.csv)"),
        ]
        for i, (lbl, filt) in enumerate(label_source_defs):
            row = SourceRow(i, lbl, filt)
            sources_layout.addWidget(row)
            if i == 3: sources_layout.addWidget(make_divider())
            self.label_sources.append(row)
            row.file_loaded.connect(self._on_label_source_loaded)
        sources_layout.addWidget(make_divider())
        left_outer.addWidget(sources_panel)

        # --- LABEL PALETTE -------------------------------------------------------
        palette_header = QWidget(); palette_header.setFixedHeight(36)
        palette_header.setStyleSheet("background-color: #F6F7F9; border-top: 1px solid #DFE2E8; border-bottom: 1px solid #DFE2E8;")
        ph = QHBoxLayout(palette_header); ph.setContentsMargins(14,0,12,0); ph.setSpacing(6)
        ph.addWidget(make_source_label("LABEL PALETTE")); ph.addStretch()
        left_outer.addWidget(palette_header)
        self.active_label_display = QLabel("NO LABEL SELECTED"); self.active_label_display.setFixedHeight(28)
        self.active_label_display.setStyleSheet("color: #555D6B; font-size: 10px; font-family: 'Inter'; padding: 0 14px; background: #F6F7F9; border-bottom: 1px solid #DFE2E8;")
        left_outer.addWidget(self.active_label_display)

        chip_scroll = QScrollArea(); chip_scroll.setWidgetResizable(True)
        chip_scroll.setStyleSheet("border: none; background: #F6F7F9;")
        chip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        chip_scroll.setMinimumHeight(140)
        self._palette_widget = QWidget(); self._palette_widget.setStyleSheet("background: #F6F7F9;")
        self._palette_layout = QVBoxLayout(self._palette_widget); self._palette_layout.setContentsMargins(12,6,12,6)
        self._palette_layout.setSpacing(3); self._palette_layout.addStretch()
        chip_scroll.setWidget(self._palette_widget); left_outer.addWidget(chip_scroll, 1)

        add_bar = QWidget(); add_bar.setFixedHeight(36)
        add_bar.setStyleSheet("background-color: #F6F7F9; border-top: 1px solid #DFE2E8;")
        ab = QHBoxLayout(add_bar); ab.setContentsMargins(12,4,12,4); ab.setSpacing(6)
        self.custom_input = QLineEdit(); self.custom_input.setPlaceholderText("add label…")
        self.custom_input.setStyleSheet("background: #FFFFFF; border: 1px solid #DFE2E8; color: #15171C; font-family: 'Inter'; font-size: 11px; padding: 0 8px;")
        add_btn = QPushButton("+"); add_btn.setFixedSize(28,26)
        add_btn.setStyleSheet("background:#EEF0FD; border:1px solid #3D4FD6; color:#3D4FD6; font-size:16px; padding:0;")
        ab.addWidget(self.custom_input); ab.addWidget(add_btn)
        left_outer.addWidget(add_bar)
        self.active_label = None; self.active_color = "#3D4FD6"
        for name, color in PREDEFINED_LABELS: self._add_chip_to_palette(name, color)
        add_btn.clicked.connect(self._add_custom_label); self.custom_input.returnPressed.connect(self._add_custom_label)

        # --- PLACED LABELS -------------------------------------------------------
        placed_header = QWidget(); placed_header.setFixedHeight(36)
        placed_header.setStyleSheet("background-color: #F6F7F9; border-top: 1px solid #DFE2E8; border-bottom: 1px solid #DFE2E8;")
        rh = QHBoxLayout(placed_header); rh.setContentsMargins(14,0,14,0)
        rh.addWidget(make_source_label("PLACED LABELS")); rh.addStretch()
        self.label_count = QLabel("0")
        self.label_count.setStyleSheet("color: #3D4FD6; font-size: 10px; font-family: 'Inter';")
        rh.addWidget(self.label_count)
        left_outer.addWidget(placed_header)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background-color: #F6F7F9;")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(140)
        self.label_list_widget = QWidget(); self.label_list_widget.setStyleSheet("background-color: #F6F7F9;")
        self.placed_layout = QVBoxLayout(self.label_list_widget)
        self.placed_layout.setContentsMargins(0,4,0,4); self.placed_layout.setSpacing(0)
        self.placed_layout.addStretch()
        scroll.setWidget(self.label_list_widget); left_outer.addWidget(scroll, 1)

        body.addWidget(left)

        # === RIGHT AREA — scrollable, mirrors Sync tab =============================
        right_scroll = QScrollArea(); right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        right_scroll.setStyleSheet(
            "QScrollArea { background: #F6F7F9; border: none; } "
            "QScrollBar:vertical { background: #F6F7F9; width: 8px; border: none; } "
            "QScrollBar::handle:vertical { background: #DFE2E8; border-radius: 4px; min-height: 24px; } "
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: none; }"
        )
        center = QWidget(); center.setStyleSheet("background-color: #F6F7F9;")
        center.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        center_layout = QVBoxLayout(center); center_layout.setContentsMargins(0,0,0,0); center_layout.setSpacing(0)

        # Video grid (2x2) — uses _AspectVideoArea to size proportionally to width
        class _LabelVideoArea(QWidget):
            HEADER_H = 26; SPACING = 2
            def resizeEvent(self, event):
                super().resizeEvent(event)
                cell_w = max(100, (self.width() - self.SPACING - 24) // 2)
                cell_h = int(cell_w * 9 / 16)
                self.setFixedHeight((cell_h + self.HEADER_H) * 2 + self.SPACING + 40)

        video_area = _LabelVideoArea()
        video_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        video_area.setStyleSheet("background-color: #F6F7F9;")
        va = QVBoxLayout(video_area); va.setContentsMargins(12,12,12,8); va.setSpacing(6)
        va.addWidget(make_source_label("SYNCHRONIZED PLAYBACK"))
        vg = QGridLayout(); vg.setSpacing(6)
        vg.setRowStretch(0,1); vg.setRowStretch(1,1)
        vg.setColumnStretch(0,1); vg.setColumnStretch(1,1)
        self.label_slots = []
        for i, ln in enumerate(["DSLR","DSLR","POV","POV"]):
            sl = VLCVideoSlot(i+1, label=ln)
            vg.addWidget(sl, *divmod(i,2))
            self.label_slots.append(sl)
        self._label_sync_ctrl = SyncPlaybackController()
        va.addLayout(vg, 1)
        center_layout.addWidget(video_area)

        # EEG overlay below — fixed minimum so it's always legible
        eeg_strip = QWidget()
        eeg_strip.setMinimumHeight(220); eeg_strip.setMaximumHeight(280)
        eeg_strip.setStyleSheet("background-color: #F6F7F9;")
        eeg_strip_layout = QVBoxLayout(eeg_strip)
        eeg_strip_layout.setContentsMargins(12,6,12,6); eeg_strip_layout.setSpacing(4)
        eeg_strip_layout.addWidget(make_source_label("EEG OVERLAY"))
        eeg_strip_row = QHBoxLayout(); eeg_strip_row.setSpacing(6)
        self.eeg_label_1 = EEGReviewWidget(channel_id=1); self.eeg_label_2 = EEGReviewWidget(channel_id=2)
        eeg_strip_row.addWidget(self.eeg_label_1); eeg_strip_row.addWidget(self.eeg_label_2)
        eeg_strip_layout.addLayout(eeg_strip_row, 1)
        center_layout.addWidget(eeg_strip)

        # Timeline + transport — fixed at bottom
        tl_container = QWidget(); tl_container.setFixedHeight(100)
        tl_container.setStyleSheet("background-color: #F6F7F9; border-top: 1px solid #DFE2E8;")
        tl_layout = QVBoxLayout(tl_container); tl_layout.setContentsMargins(16,8,16,8); tl_layout.setSpacing(6)
        tl_header = QHBoxLayout(); tl_header.addWidget(make_source_label("TIMELINE")); tl_header.addStretch()
        self.tl_tc = QLabel("00:00:00.000"); self.tl_tc.setObjectName("timecode"); tl_header.addWidget(self.tl_tc)
        tl_layout.addLayout(tl_header)
        self.tl_slider = TimelineWithMarkers()
        self.tl_slider.setRange(0,10000); self.tl_slider.setValue(0)
        self.tl_slider.valueChanged.connect(self._on_timeline_scrub)
        # Clicking a marker tick on the strip seeks to that exact timestamp
        self.tl_slider.markerSeek.connect(self._seek_to_time_sec)
        tl_layout.addWidget(self.tl_slider)
        play_row = QHBoxLayout(); play_row.setSpacing(8)
        rewind_btn = QPushButton("⏮"); rewind_btn.setFixedSize(32,28)
        rewind_btn.setStyleSheet("background-color: #FFFFFF; border: 1px solid #DFE2E8; color: #15171C; font-size: 12px; padding: 0;")
        rewind_btn.clicked.connect(lambda: self._label_sync_ctrl.seek(0.0)); play_row.addWidget(rewind_btn)
        self._play_btn_lbl = QPushButton("▶  PLAY"); self._play_btn_lbl.setFixedWidth(110); self._play_btn_lbl.setFixedHeight(28)
        self._play_btn_lbl.setEnabled(False)
        self._play_btn_lbl.setStyleSheet("background-color: #FFFFFF; border: 1px solid #DFE2E8; color: #15171C; font-size: 12px; padding: 0;")
        self._play_btn_lbl.clicked.connect(self._toggle_play_label); play_row.addWidget(self._play_btn_lbl)
        skip_btn = QPushButton("⏭"); skip_btn.setFixedSize(32,28)
        skip_btn.setStyleSheet("background-color: #FFFFFF; border: 1px solid #DFE2E8; color: #15171C; font-size: 12px; padding: 0;")
        play_row.addWidget(skip_btn)
        self._label_pos_timer = QTimer(self); self._label_pos_timer.setInterval(100)
        self._label_pos_timer.timeout.connect(self._update_label_timeline)
        place_btn = QPushButton("PLACE LABEL AT PLAYHEAD"); place_btn.setFixedHeight(28)
        place_btn.setStyleSheet("background-color: #EEF0FD; border: 1px solid #3D4FD6; color: #3D4FD6; font-family: 'Inter'; font-size: 9px; letter-spacing: 2px; padding: 0 14px;")
        place_btn.clicked.connect(self._place_label); play_row.addWidget(place_btn); play_row.addStretch()
        self._end_lbl = QLabel("--:--:--.---")
        self._end_lbl.setStyleSheet("color: #3A404B; font-size: 11px; font-family: 'Inter'; letter-spacing: 1px;")
        play_row.addWidget(self._end_lbl); tl_layout.addLayout(play_row)
        center_layout.addWidget(tl_container)

        right_scroll.setWidget(center)
        body.addWidget(right_scroll, 1)

        root.addLayout(body)
        self.export_csv_btn.clicked.connect(self._export_labels); QTimer.singleShot(0, self._do_recount)

    def _on_label_source_loaded(self, source_id: int, path: str):
        """Route uploaded file to the correct video slot or EEG widget.
        source_ids 0-3 are DSLR/POV videos, 4-5 are Muse-S CSVs.
        Also tries to find a sibling phase_markers.csv and auto-load it."""
        if source_id < 4:
            slot = self.label_slots[source_id]
            try:
                slot.load(path, offset_sec=0.0)
            except Exception as e:
                log.warning("label tab: video %d load failed: %s", source_id, e)
            # Re-attach every loaded slot to the sync controller. Without
            # this, the PLAY button has no players to control and the
            # video stays black even though it loaded successfully.
            try:
                self._label_sync_ctrl.attach(self.label_slots, offsets_sec=None)
            except Exception as e:
                log.warning("label tab: sync attach failed: %s", e)
            # Tell the timeline strip how long the video is so existing
            # markers re-position correctly.
            try:
                loaded = [sl for sl in self.label_slots if sl.is_loaded()]
                dur = loaded[0].get_duration_sec() if loaded else 0.0
                # Duration is sometimes 0 immediately after load — VLC
                # hasn't read the metadata yet. Re-query after 600ms.
                if dur > 0:
                    self.tl_slider.set_duration_sec(dur)
                else:
                    QTimer.singleShot(600, self._refresh_timeline_duration)
            except Exception as e:
                log.debug("timeline duration not ready yet (retried later): %r", e)
            self._play_btn_lbl.setEnabled(True)
        elif source_id == 4:
            self._load_label_eeg_async(path, self.eeg_label_1)
        elif source_id == 5:
            self._load_label_eeg_async(path, self.eeg_label_2)

        # Phase marker auto-discovery: walk upward from the uploaded file
        # to find a phase_markers.csv. Idempotent — if markers with the
        # same timestamps are already placed, this call is a no-op.
        try:
            self._try_auto_load_markers_for(path)
        except Exception as e:
            log.warning("phase-marker auto-load failed: %s", e)

    def _try_auto_load_markers_for(self, file_path: str) -> None:
        """Walk up to 4 directory levels looking for phase_markers.csv.
        Session folder structure puts it at <session_root>/phase_markers.csv
        and uploaded files live at <session_root>/continuous/{Side|POV}
        Cams/*.mp4 — so 2-3 levels up usually finds it."""
        from pathlib import Path
        p = Path(file_path).resolve().parent
        for _ in range(4):
            candidate = p / "phase_markers.csv"
            if candidate.exists():
                self._load_phase_markers_csv(str(candidate))
                # The folder holding phase_markers.csv is the session root —
                # adopt it for label autosave if we don't already have one,
                # and restore any labels saved there before.
                if not self._session_root:
                    self._session_root = str(p)
                    self._restore_labels_from_json()
                return
            if p.parent == p: break  # filesystem root
            p = p.parent

    def _load_phase_markers_csv(self, csv_path: str) -> None:
        """Read phase_markers.csv and place a phase-style label for each
        row. De-duplicates against already-placed phase markers by
        (timestamp_str, label_name) so calling this multiple times during
        a multi-file upload is safe. Colours come from the module-level
        PHASE_MARKER_COLORS palette (shared with the Sync + Review tab)."""
        # Collect existing phase-marker keys for dedup
        existing = set()
        for i in range(self.placed_layout.count()):
            item = self.placed_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, LabelMarker) and getattr(w, "is_phase", False):
                existing.add((w.timestamp_str, w.label_name))

        added = 0
        for t, color, label in _read_phase_markers(csv_path):
            h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
            ts_str = f"{h:02d}:{m:02d}:{s:06.3f}"
            if (ts_str, label) in existing:
                continue  # already placed, skip
            marker = LabelMarker(label, color, ts_str, is_phase=True)
            marker.removed.connect(self._update_label_count)
            marker.removed.connect(self._rebuild_timeline_markers)
            # Insert phase markers in sorted order by timestamp at
            # the top of the list, before any user-placed markers.
            self.placed_layout.insertWidget(self.placed_layout.count() - 1, marker)
            # Mirror onto the timeline strip (the list stays authoritative)
            try: self.tl_slider.add_marker(t, color, label)
            except Exception as e: log.debug("timeline marker mirror failed: %r", e)
            existing.add((ts_str, label))
            added += 1
        if added > 0:
            self._update_label_count()
            log.info("auto-loaded %d phase markers from %s", added, csv_path)

    def _open_session_folder(self) -> None:
        """Pick a session root folder via a dialog, then load it."""
        folder = QFileDialog.getExistingDirectory(self, "Open session folder", "")
        if not folder:
            return
        self.load_session_folder(folder, announce=True)

    def load_session_folder(self, folder: str, announce: bool = False) -> None:
        """Load every recording in a session folder (4 videos + 2 EEG CSVs +
        phase markers), then restore any user labels autosaved to
        <root>/labels.json. Expects the v2.x continuous-recording layout:
            <session_root>/continuous/Side Cams/*.mp4
            <session_root>/continuous/POV Cams/*.mp4
            <session_root>/continuous/Muse data/*.csv
            <session_root>/phase_markers.csv  (optional)
            <session_root>/labels.json        (optional — autosaved labels)

        announce controls the final summary message box (off for headless
        callers). Split out of _open_session_folder so it's testable without
        a file dialog."""
        from pathlib import Path
        root = Path(folder)
        cont = root / "continuous"
        if not cont.exists():
            # Maybe the user picked the continuous folder itself, or a
            # parent of the session folder. Try both fallbacks.
            if (root / "Side Cams").exists() and (root / "POV Cams").exists():
                cont = root; root = root.parent
            else:
                # Look one level deep in case they picked the parent dir
                for child in root.iterdir():
                    if child.is_dir() and (child / "continuous").exists():
                        root = child; cont = child / "continuous"; break
                else:
                    if announce:
                        QMessageBox.information(
                            self, "No session found",
                            "Couldn't find a 'continuous/' folder under the selected path.\n\n"
                            "Pick the session root (e.g. 'Brain Battle 2026-04-29 18-30-05/').",
                        )
                    return

        # This session's folder is now resolved — it owns the labels.json
        # autosave target. Clear any markers left from a previously-open
        # session so we show THIS session's state fresh, not an accumulation.
        self._session_root = str(root)
        self._clear_all_placed_labels()

        # Find videos in known subfolders + load into slots in this order:
        # Side Cams → slots 0,1 ; POV Cams → slots 2,3
        loaded_video = False
        side_dir = cont / "Side Cams"
        pov_dir  = cont / "POV Cams"
        muse_dir = cont / "Muse data"
        side_videos = sorted(side_dir.glob("*.mp4")) if side_dir.exists() else []
        pov_videos  = sorted(pov_dir.glob("*.mp4")) if pov_dir.exists() else []
        for i, vp in enumerate(side_videos[:2]):
            try:
                self.label_slots[i].load(str(vp), offset_sec=0.0)
                self.label_sources[i].set_path(str(vp))
                loaded_video = True
            except Exception as e:
                log.warning("session open: side cam %d load failed: %s", i, e)
        for i, vp in enumerate(pov_videos[:2]):
            slot_idx = 2 + i
            try:
                self.label_slots[slot_idx].load(str(vp), offset_sec=0.0)
                self.label_sources[slot_idx].set_path(str(vp))
                loaded_video = True
            except Exception as e:
                log.warning("session open: pov cam %d load failed: %s", i, e)

        # EEG CSVs
        muse_csvs = sorted(muse_dir.glob("*.csv")) if muse_dir.exists() else []
        for i, mp in enumerate(muse_csvs[:2]):
            try:
                widget = self.eeg_label_1 if i == 0 else self.eeg_label_2
                self._load_label_eeg_async(str(mp), widget)
                self.label_sources[4 + i].set_path(str(mp))
            except Exception as e:
                log.warning("session open: muse %d load failed: %s", i, e)

        # Phase markers
        markers_csv = root / "phase_markers.csv"
        if markers_csv.exists():
            self._load_phase_markers_csv(str(markers_csv))

        # Restore any user labels autosaved from a previous labelling pass.
        self._restore_labels_from_json()

        if loaded_video:
            # Re-attach all slots to the sync controller so PLAY works.
            try:
                self._label_sync_ctrl.attach(self.label_slots, offsets_sec=None)
            except Exception as e:
                log.warning("label tab: sync attach failed: %s", e)
            QTimer.singleShot(600, self._refresh_timeline_duration)
            self._play_btn_lbl.setEnabled(True)

        # Status feedback
        n_videos = len(side_videos[:2]) + len(pov_videos[:2])
        n_muse   = len(muse_csvs[:2])
        n_marks  = sum(1 for _ in open(markers_csv, encoding="utf-8")) - 1 if markers_csv.exists() else 0
        if announce:
            QMessageBox.information(
                self, "Session loaded",
                f"Loaded {n_videos} video(s), {n_muse} EEG CSV(s), "
                f"{max(0, n_marks)} phase marker(s) from\n{root.name}",
            )

    def _load_label_eeg_async(self, path: str, widget):
        """Load EEG CSV for label tab — mirrors SyncReviewTab._load_eeg_async."""
        class _LabelEEGLoader(QThread):
            done = pyqtSignal(object)
            def __init__(self, p, parent=None):
                super().__init__(parent); self._p = p
            def run(self):
                try:
                    df = pd.read_csv(self._p)
                    col_map = {c.lower(): c for c in df.columns}
                    if 'timestamps' not in df.columns and 'timestamps' in col_map:
                        df = df.rename(columns={col_map['timestamps']: 'timestamps'})
                    self.done.emit(df)
                except Exception as e:
                    log.error("label tab: EEG CSV load failed: %s", e)
        loader = _LabelEEGLoader(path, parent=self)
        if not hasattr(self, '_label_eeg_loaders'):
            self._label_eeg_loaders = []
        self._label_eeg_loaders.append(loader)
        def _on_done(df, _w=widget, _l=loader):
            try: _w.load(df, offset_sec=0.0)
            except Exception:
                log.error("label tab: EEG widget load failed", exc_info=True)
            finally:
                try: _l.wait(2000)
                except Exception: pass
                if _l in self._label_eeg_loaders:
                    self._label_eeg_loaders.remove(_l)
        loader.done.connect(_on_done)
        loader.start()

    def _add_chip_to_palette(self, name, color):
        row = QWidget(); row.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row); rl.setContentsMargins(0,1,0,1); rl.setSpacing(4)
        chip = LabelChip(name, color); chip.clicked.connect(self._select_label); rl.addWidget(chip)
        # Keyboard shortcut hint — first 9 palette chips get 1-9 binding
        slot_idx = self._palette_layout.count() - 1  # before the stretch
        if 0 <= slot_idx < 9:
            import design_tokens as _dt
            key_lbl = QLabel(str(slot_idx + 1))
            key_lbl.setFixedSize(20, 20)
            key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            key_lbl.setStyleSheet(
                f"background: {_dt.card_bg()}; border: 1px solid {_dt.border_strong()}; "
                f"border-radius: 4px; color: {_dt.text_secondary()}; "
                f"font-family: {_dt.FONT_MONO}; font-size: 10px; font-weight: 600;"
            )
            rl.addWidget(key_lbl)
        rm = QPushButton("×"); rm.setFixedSize(22,24)
        rm.setStyleSheet("background: transparent; border: none; color: #3A404B; font-size: 14px; padding: 0;")
        rm.clicked.connect(lambda checked, r=row, n=name: self._remove_chip(r, n)); rl.addWidget(rm)
        self._palette_layout.insertWidget(self._palette_layout.count()-1, row)

    def _install_shortcuts(self):
        """Keyboard shortcuts for real-time labelling workflow.
        Number keys 1-9 select + place labels. Space toggles play/pause."""
        from PyQt6.QtGui import QShortcut, QKeySequence
        for i in range(9):
            sc = QShortcut(QKeySequence(str(i + 1)), self)
            sc.activated.connect(lambda idx=i: self._shortcut_place(idx))
        sc_play = QShortcut(QKeySequence("Space"), self)
        sc_play.activated.connect(self._toggle_play_label)

    def _shortcut_place(self, idx):
        """Select palette chip #idx and place label at current playhead."""
        # Walk the palette to find the idx-th chip
        palette_row = None; n = 0
        for i in range(self._palette_layout.count() - 1):
            item = self._palette_layout.itemAt(i)
            if item and item.widget():
                chips = item.widget().findChildren(LabelChip)
                if chips:
                    if n == idx:
                        palette_row = chips[0]; break
                    n += 1
        if palette_row is None: return
        self._select_label(palette_row._name, palette_row.color)
        self._place_label()

    def _remove_chip(self, row, name):
        row.deleteLater()
        if self.active_label == name:
            self.active_label = None; self.active_color = "#3D4FD6"; self.active_label_display.setText("NO LABEL SELECTED")
            self.active_label_display.setStyleSheet("color: #555D6B; font-size: 10px; font-family: 'Inter'; padding: 0 12px; background: #F6F7F9; border-bottom: 1px solid #DFE2E8;")
        QTimer.singleShot(50, lambda: save_labels(self._current_labels()))

    def _current_labels(self):
        labels = []
        for i in range(self._palette_layout.count()-1):
            item = self._palette_layout.itemAt(i)
            if item and item.widget():
                chips = item.widget().findChildren(LabelChip)
                if chips: labels.append((chips[0]._name, chips[0].color))
        return labels

    def _add_custom_label(self):
        name = self.custom_input.text().strip().upper().replace(" ","_")
        if not name: return
        existing = set()
        for i in range(self._palette_layout.count()-1):
            item = self._palette_layout.itemAt(i)
            if item and item.widget():
                children = item.widget().findChildren(LabelChip)
                if children: existing.add(children[0]._name)
        if name in existing: self._select_label(name); self.custom_input.clear(); return
        color = next_colour(self._current_labels()); self._add_chip_to_palette(name, color)
        self.custom_input.clear(); self._select_label(name); save_labels(self._current_labels())

    def _select_label(self, name):
        self.active_label = name; color = "#3D4FD6"
        for i in range(self._palette_layout.count()-1):
            item = self._palette_layout.itemAt(i)
            if item and item.widget():
                chips = item.widget().findChildren(LabelChip)
                if chips and chips[0]._name == name: color = chips[0].color; break
        self.active_color = color; self.active_label_display.setText(f"● {name}")
        self.active_label_display.setStyleSheet(f"color: {color}; font-size: 11px; font-family: 'Inter'; padding: 0 12px; background: #F6F7F9; border-bottom: 1px solid {color};")

    def _toggle_play_label(self):
        if self._label_sync_ctrl.is_playing():
            self._label_sync_ctrl.pause(); self._play_btn_lbl.setText("▶  PLAY"); self._label_pos_timer.stop()
        else:
            self._label_sync_ctrl.play(); self._play_btn_lbl.setText("⏸  PAUSE"); self._label_pos_timer.start()

    def _update_label_timeline(self):
        global_t = self._label_sync_ctrl.current_global_t(); self._fake_ts = global_t
        h = int(global_t//3600); m = int((global_t%3600)//60); s = global_t%60
        self.tl_tc.setText(f"{h:02d}:{m:02d}:{s:06.3f}")
        loaded = [sl for sl in self.label_slots if sl.is_loaded()]
        dur = (loaded[0].get_duration_sec() if loaded else 1.0) or 1.0
        self.tl_slider.blockSignals(True); self.tl_slider.setValue(int((global_t/dur)*10000)); self.tl_slider.blockSignals(False)
        self.eeg_label_1.set_playhead(global_t); self.eeg_label_2.set_playhead(global_t)

    def _refresh_timeline_duration(self):
        """Re-read video duration and push it to the timeline strip.
        Called once after upload because VLC's get_duration() returns 0
        until metadata is parsed; a small delay fixes it."""
        try:
            loaded = [sl for sl in self.label_slots if sl.is_loaded()]
            if not loaded: return
            dur = loaded[0].get_duration_sec()
            if dur > 0:
                self.tl_slider.set_duration_sec(dur)
                # also update the end-time display
                h = int(dur//3600); m = int((dur%3600)//60); s = dur%60
                self._end_lbl.setText(f"{h:02d}:{m:02d}:{s:06.3f}")
        except Exception as e:
            log.debug("timeline duration refresh failed (VLC not ready): %r", e)

    def _on_timeline_scrub(self, value):
        loaded = [sl for sl in self.label_slots if sl.is_loaded()]
        dur = (loaded[0].get_duration_sec() if loaded else 1.0) or 1.0
        total_secs = (value/10000)*dur; h = int(total_secs//3600); m = int((total_secs%3600)//60); s = total_secs%60
        self.tl_tc.setText(f"{h:02d}:{m:02d}:{s:06.3f}"); self._fake_ts = total_secs
        self._label_sync_ctrl.seek(total_secs); self.eeg_label_1.set_playhead(total_secs); self.eeg_label_2.set_playhead(total_secs)

    def _seek_to_time_sec(self, t_sec: float):
        """Seek the timeline to a specific timestamp in seconds. Used when
        the user clicks a marker chip on the timeline strip — snaps the
        playhead, video, and EEG playheads to that exact moment."""
        loaded = [sl for sl in self.label_slots if sl.is_loaded()]
        dur = (loaded[0].get_duration_sec() if loaded else 1.0) or 1.0
        # Compute slider value matching that timestamp
        frac = max(0.0, min(1.0, t_sec / dur))
        slider_val = int(frac * 10000)
        # setValue triggers _on_timeline_scrub which does the actual seeking
        self.tl_slider.setValue(slider_val)

    def load_session_data(self, video_slots_data, eeg_data, sync_result,
                          session_root=None):
        # Handoff from the Review tab. When it passes the session folder, adopt
        # it for label autosave and restore any labels saved earlier, matching
        # the OPEN SESSION FOLDER path.
        if session_root:
            self._session_root = str(session_root)
            self._clear_all_placed_labels()
        for i, (path, offset) in enumerate(video_slots_data):
            if i < len(self.label_slots) and path: self.label_slots[i].load(path, offset_sec=offset)
        loaded = [s for s in self.label_slots if s.is_loaded()]; offsets = [s._offset_sec for s in loaded]
        dur = sync_result.global_duration if sync_result else 0.0
        self._label_sync_ctrl.attach(loaded, offsets_sec=offsets, global_duration=dur)
        if dur > 0:
            h = int(dur//3600); m = int((dur%3600)//60); s = dur%60; self._end_lbl.setText(f"{h:02d}:{m:02d}:{s:06.3f}")
        for i, (df, offset) in enumerate(eeg_data):
            if i == 0 and df is not None: self.eeg_label_1.load(df, offset_sec=offset)
            elif i == 1 and df is not None: self.eeg_label_2.load(df, offset_sec=offset)
        if session_root:
            self._restore_labels_from_json()
        self._play_btn_lbl.setEnabled(True)

    def load_eeg_data(self, df1, offset1, df2, offset2):
        if df1 is not None: self.eeg_label_1.load(df1, offset_sec=offset1)
        if df2 is not None: self.eeg_label_2.load(df2, offset_sec=offset2)

    def _place_label(self):
        if not self.active_label: return
        ts = float(self._fake_ts); h = int(ts//3600); m = int((ts%3600)//60); s = ts%60
        ts_str = f"{h:02d}:{m:02d}:{s:06.3f}"
        marker = LabelMarker(self.active_label, self.active_color, ts_str, t_seconds=ts)
        marker.removed.connect(self._update_label_count)
        marker.removed.connect(self._rebuild_timeline_markers)
        marker.removed.connect(self._autosave_labels)
        marker.removed.connect(lambda m=marker: self._log_label_removed(m))
        self.placed_layout.insertWidget(self.placed_layout.count()-1, marker); self._update_label_count()
        # Mirror the label onto the timeline strip as a coloured tick
        try:
            self.tl_slider.add_marker(ts, self.active_color, self.active_label)
        except Exception as e:
            log.debug("timeline marker mirror failed: %r", e)
        self._autosave_labels()
        if self._session_root:
            session_history.log_event(
                self._session_root, session_history.EVENT_LABELS_ADDED,
                {"label": marker.label_name, "t_seconds": round(ts, 3),
                 "who": marker.boxer})

    def _log_label_removed(self, marker):
        """Log one labels_removed event per user removal (skip phase markers,
        which are read-only session boundaries, not user labels)."""
        if getattr(marker, "is_phase", False) or not self._session_root:
            return
        if getattr(self, "_restoring", False):
            return   # clearing/restoring is not a user action
        session_history.log_event(
            self._session_root, session_history.EVENT_LABELS_REMOVED,
            {"label": marker.label_name, "t_seconds": marker.t_seconds,
             "who": marker.boxer})

    def _rebuild_timeline_markers(self):
        """Re-derive ticks from the current PLACED LABELS list. Called
        when a marker is removed so its tick disappears too. Safe to
        run synchronously because LabelMarker._remove() now unparents
        itself from the layout BEFORE emitting `removed`, so this walk
        sees the post-removal state."""
        try:
            self.tl_slider.clear_markers()
            for i in range(self.placed_layout.count()):
                item = self.placed_layout.itemAt(i)
                w = item.widget() if item else None
                if not isinstance(w, LabelMarker):
                    continue
                try:
                    parts = w.timestamp_str.split(":")
                    t = float(parts[0])*3600 + float(parts[1])*60 + float(parts[2])
                    self.tl_slider.add_marker(t, w._color, w.label_name)
                except (RuntimeError, AttributeError):
                    continue
                except Exception:
                    continue
        except Exception:
            pass

    def _update_label_count(self): QTimer.singleShot(0, self._do_recount)

    def _do_recount(self):
        if not hasattr(self,'placed_layout') or not hasattr(self,'label_count'): return
        n = sum(1 for i in range(self.placed_layout.count()) if isinstance(self.placed_layout.itemAt(i).widget(), LabelMarker))
        self.label_count.setText(str(n))

    def get_labels(self):
        labels = []
        for i in range(self.placed_layout.count()-1):
            item = self.placed_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), LabelMarker):
                marker = item.widget(); ts_str = marker.timestamp_str; name = marker.label_name
                # Prefer the numeric placement time; fall back to parsing the
                # display string only for any legacy marker without it.
                ts_sec = marker.t_seconds
                if ts_sec is None:
                    try: parts = ts_str.split(":"); ts_sec = float(parts[0])*3600 + float(parts[1])*60 + float(parts[2])
                    except Exception: ts_sec = 0.0
                labels.append({"timestamp_sec": round(float(ts_sec),3), "timestamp_str": ts_str, "label": name, "color": marker._color})
        return labels

    # ── Label autosave / restore (session folder) ───────────────────────────

    def _user_label_markers(self):
        """Placed markers that are user labels (not auto-loaded phase markers)."""
        out = []
        for i in range(self.placed_layout.count()):
            item = self.placed_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, LabelMarker) and not w.is_phase:
                out.append(w)
        return out

    def _autosave_labels(self):
        """Atomically persist the current user labels to <root>/labels.json.
        Called on every add/remove. No-op while restoring or if no session
        folder is set (labels still work in memory + via EXPORT)."""
        if self._restoring or not self._session_root:
            return
        labels = []
        for w in self._user_label_markers():
            t = w.t_seconds
            if t is None:
                try:
                    parts = w.timestamp_str.split(":")
                    t = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                except Exception:
                    t = 0.0
            labels.append({
                "t_seconds": round(float(t), 3),
                "label": w.label_name,
                "color": w._color,
                "boxer": w.boxer,              # Phase-4 field, null for now
                "end_t_seconds": w.end_t_seconds,  # Phase-4 field, null for now
            })
        data = {"schema_version": LABELS_SCHEMA_VERSION,
                "time_axis": LABELS_TIME_AXIS, "labels": labels}
        try:
            _atomic_write_json(os.path.join(self._session_root, LABELS_JSON_NAME), data)
        except Exception as e:
            log.warning("label autosave to %s failed: %s", self._session_root, e)

    def _restore_labels_from_json(self):
        """Re-place user labels saved to <root>/labels.json by a previous
        labelling pass. No-op if the file is absent or its schema_version
        isn't one this build understands."""
        if not self._session_root:
            return
        path = os.path.join(self._session_root, LABELS_JSON_NAME)
        if not os.path.exists(path):
            return
        import json as _json
        try:
            data = _json.loads(open(path, encoding="utf-8").read())
        except Exception as e:
            log.warning("could not read %s: %s", path, e)
            return
        ver = data.get("schema_version")
        if ver != LABELS_SCHEMA_VERSION:
            log.warning("labels.json schema_version %r != %r — not restoring "
                        "(a newer/older build wrote it)", ver, LABELS_SCHEMA_VERSION)
            return
        self._restoring = True
        try:
            for entry in data.get("labels", []):
                try:
                    t = float(entry.get("t_seconds", 0.0))
                except (TypeError, ValueError):
                    continue
                name = entry.get("label", "LABEL")
                color = entry.get("color", "#3D4FD6")
                h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
                ts_str = f"{h:02d}:{m:02d}:{s:06.3f}"
                marker = LabelMarker(name, color, ts_str, t_seconds=t,
                                     boxer=entry.get("boxer"),
                                     end_t_seconds=entry.get("end_t_seconds"))
                marker.removed.connect(self._update_label_count)
                marker.removed.connect(self._rebuild_timeline_markers)
                marker.removed.connect(self._autosave_labels)
                marker.removed.connect(lambda m=marker: self._log_label_removed(m))
                self.placed_layout.insertWidget(self.placed_layout.count() - 1, marker)
                try:
                    self.tl_slider.add_marker(t, color, name)
                except Exception as e:
                    log.debug("timeline marker mirror failed on restore: %r", e)
            self._update_label_count()
        finally:
            self._restoring = False

    def _clear_all_placed_labels(self):
        """Remove every placed marker (user + phase) and its timeline tick.
        Used when opening a session so it shows that session's labels fresh
        rather than an accumulation. Suppresses autosave during the clear."""
        self._restoring = True
        try:
            for i in reversed(range(self.placed_layout.count())):
                item = self.placed_layout.itemAt(i)
                w = item.widget() if item else None
                if isinstance(w, LabelMarker):
                    self.placed_layout.removeWidget(w)
                    w.setParent(None)
                    w.deleteLater()
            try:
                self.tl_slider.clear_markers()
            except Exception:
                pass
            self._update_label_count()
        finally:
            self._restoring = False

    def _export_labels(self):
        labels = self.get_labels()
        if not labels: QMessageBox.information(self, "No labels", "Place some labels before exporting."); return
        path, _ = QFileDialog.getSaveFileName(self, "Export labels", "labels.csv", "CSV (*.csv)")
        if path:
            try:
                LabelsExporter.export(labels, path)
                if self._session_root:
                    session_history.log_event(
                        self._session_root, session_history.EVENT_LABELS_EXPORTED,
                        {"count": len(labels), "to": path})
                QMessageBox.information(self, "Exported", f"Saved {len(labels)} labels to:\n{path}")
            except Exception as e: QMessageBox.critical(self, "Export failed", str(e))


def _h1(text):
    import design_tokens as dt
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
                      f"font-size: 24px; font-weight: 700;")
    return lbl


def _sub(text):
    import design_tokens as dt
    lbl = QLabel(text); lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                      f"font-size: {dt.SIZE_MD}px;")
    return lbl


class SessionsPlaceholder(QWidget):
    """Temporary Sessions home (DESIGN §1). The real session list lands in the
    next step; for now this shows the DESIGN empty state with working
    'New recording' and 'Show folder in Finder' buttons."""

    def __init__(self, capture_dir, on_new_recording, parent=None):
        super().__init__(parent)
        import design_tokens as dt
        self._capture_dir = capture_dir
        self.setStyleSheet(f"background: {dt.canvas_bg()};")
        root = QVBoxLayout(self); root.setContentsMargins(40, 36, 40, 36); root.setSpacing(0)
        header = QHBoxLayout()
        htext = QVBoxLayout(); htext.setSpacing(4)
        htext.addWidget(_h1("Sessions"))
        htext.addWidget(_sub("Every recording in your recordings folder. "
                             "Open one to watch it back and add labels."))
        header.addLayout(htext); header.addStretch()
        show_btn = QPushButton("Show folder in Finder")
        show_btn.setStyleSheet(dt.qss_secondary_button()); show_btn.setMinimumHeight(44)
        show_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        show_btn.clicked.connect(self._show_in_finder)
        new_btn = QPushButton("New recording")
        new_btn.setStyleSheet(dt.qss_primary_button()); new_btn.setMinimumHeight(44)
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.clicked.connect(on_new_recording)
        header.addWidget(show_btn); header.addSpacing(10); header.addWidget(new_btn)
        root.addLayout(header)
        root.addSpacing(28)

        empty = QFrame(); empty.setObjectName("emptyCard")
        empty.setStyleSheet(f"QFrame#emptyCard {{ background: {dt.card_bg()}; "
                            f"border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_LG}px; }}")
        ev = QVBoxLayout(empty); ev.setContentsMargins(40, 60, 40, 60)
        ev.setAlignment(Qt.AlignmentFlag.AlignCenter); ev.setSpacing(10)
        icon = QLabel(); icon.setPixmap(dt.icon_pixmap("home", color=dt.text_tertiary(), size=40))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("border: none; background: transparent;")
        ev.addWidget(icon)
        t = QLabel("Sessions list arrives in the next step")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet(f"color: {dt.text_secondary()}; font-family: {dt.FONT_UI}; "
                        f"font-size: {dt.SIZE_LG}px; font-weight: 600; border: none; background: transparent;")
        ev.addWidget(t)
        t2 = QLabel("No recordings yet. Press New recording to make your first one.")
        t2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t2.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                         f"font-size: {dt.SIZE_MD}px; border: none; background: transparent;")
        ev.addWidget(t2)
        root.addWidget(empty, 1)

    def _show_in_finder(self):
        import subprocess, sys as _sys
        d = self._capture_dir
        try:
            os.makedirs(d, exist_ok=True)
            if _sys.platform == "darwin": subprocess.Popen(["open", d])
            elif _sys.platform == "win32": os.startfile(d)  # noqa
            else: subprocess.Popen(["xdg-open", d])
        except Exception as e:
            log.warning("could not open recordings folder: %s", e)


class SettingsPlaceholder(QWidget):
    """Temporary Settings (DESIGN §7). Lists the planned sections; the real
    controls land in a later step."""

    _SECTIONS = [
        ("Recordings folder", "Where recordings are saved, with free space."),
        ("Headbands", "Assign each headband to Boxer A / Boxer B."),
        ("Video quality", "Standard 960×540, High 1280×720, or Light 640×360."),
        ("Session defaults", "Rounds, round length, break, rest, and the round bell."),
        ("Label list", "The shared set of labels the team places."),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        import design_tokens as dt
        self.setStyleSheet(f"background: {dt.canvas_bg()};")
        root = QVBoxLayout(self); root.setContentsMargins(40, 36, 40, 36); root.setSpacing(0)
        root.addWidget(_h1("Settings"))
        root.addWidget(_sub("These settings apply to your next recording. The "
                            "controls arrive in a later step."))
        root.addSpacing(24)
        for title, desc in self._SECTIONS:
            card = QFrame(); card.setObjectName("settingsCard")
            card.setStyleSheet(f"QFrame#settingsCard {{ background: {dt.card_bg()}; "
                               f"border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_LG}px; }}")
            cv = QVBoxLayout(card); cv.setContentsMargins(20, 16, 20, 16); cv.setSpacing(4)
            tl = QLabel(title)
            tl.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
                             f"font-size: {dt.SIZE_LG}px; font-weight: 600; border: none;")
            dl = QLabel(desc)
            dl.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                             f"font-size: {dt.SIZE_MD}px; border: none;")
            cv.addWidget(tl); cv.addWidget(dl)
            root.addWidget(card); root.addSpacing(12)
        root.addStretch()
        foot = QLabel(f"Brain Battle v{__version__} · Constructor University")
        foot.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                           f"font-size: {dt.SIZE_SM}px;")
        root.addWidget(foot)


class ReviewAndLabelPage(QWidget):
    """Temporary host for the Review & Label section. For now it shows the
    existing Review screen, with a clearly-marked TEMPORARY link to the old
    Labelling screen — both are replaced by one merged screen in a later step."""

    def __init__(self, review_widget, label_widget, parent=None):
        super().__init__(parent)
        import design_tokens as dt
        self._review = review_widget
        self._label = label_widget
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)

        bar = QWidget(); bar.setFixedHeight(34)
        bar.setStyleSheet(f"background: {dt.warning_color()}; ")
        bl = QHBoxLayout(bar); bl.setContentsMargins(14, 0, 14, 0); bl.setSpacing(8)
        note = QLabel("Temporary: Review and Labelling are still two screens — "
                      "they merge into one in a later step.")
        note.setStyleSheet(f"color: #FFFFFF; font-family: {dt.FONT_UI}; "
                           f"font-size: {dt.SIZE_SM}px; font-weight: 600;")
        bl.addWidget(note); bl.addStretch()
        self._toggle = QPushButton("Old labelling screen →")
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.setStyleSheet(
            f"QPushButton {{ background: #FFFFFF; color: {dt.warning_color()}; "
            f"border: none; border-radius: {dt.RADIUS_SM}px; padding: 4px 12px; "
            f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; font-weight: 600; }}")
        self._toggle.clicked.connect(self._toggle_page)
        bl.addWidget(self._toggle)
        v.addWidget(bar)

        self._stack = QStackedWidget()
        self._stack.addWidget(review_widget)
        self._stack.addWidget(label_widget)
        v.addWidget(self._stack, 1)

    def _toggle_page(self):
        showing_review = self._stack.currentIndex() == 0
        self._stack.setCurrentIndex(1 if showing_review else 0)
        self._toggle.setText("← Back to Review" if showing_review
                             else "Old labelling screen →")


class BrainBattleApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Brain Battle  v{__version__}")
        self.setMinimumSize(1280, 800); self.resize(1440, 900); self._build()

    def _build(self):
        from sidebar_shell import SidebarShell
        from live_session_tab import _default_capture_root

        self._restyle_app()   # apply the light global stylesheet once

        central = QWidget(); central.setObjectName("AppRoot")
        self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        capture_dir = _default_capture_root()
        self._shell = SidebarShell(capture_dir=capture_dir)

        # The existing screens, wired behind the new navigation (DESIGN
        # "App structure"). Later steps replace them with the redesigned
        # Sessions / Record / Review & Label screens.
        self.live_tab  = LiveSessionTab()
        self.sync_tab  = SyncReviewTab(); self.sync_tab._app_ref = self
        self.label_tab = LabellingTab()
        self._review_page = ReviewAndLabelPage(self.sync_tab, self.label_tab)
        self._sessions_page = SessionsPlaceholder(
            capture_dir, on_new_recording=self._go_to_new_recording)
        self._settings_page = SettingsPlaceholder()

        self.IDX_SESSIONS = self._shell.add_page("sessions", "Sessions", "home", self._sessions_page)
        self.IDX_RECORD   = self._shell.add_page("record", "New recording", "plus-circle", self.live_tab)
        self.IDX_REVIEW   = self._shell.add_page("review", "Review & Label", "layers",
                                                 self._review_page, collapse_on_active=True)
        self.IDX_SETTINGS = self._shell.add_page("settings", "Settings", "settings",
                                                 self._settings_page, bottom=True)

        # Compat shim for any code that walked up to a QTabWidget.
        class _ShellTabShim:
            def __init__(self, shell): self._shell = shell
            def setCurrentIndex(self, idx: int): self._shell.set_current_index(idx)
            def currentIndex(self): return self._shell.current_index()
        self.tabs = _ShellTabShim(self._shell)

        # After a recording is saved, land on Review & Label with it loaded.
        self.live_tab.session_saved.connect(self._on_session_saved)

        root.addWidget(self._shell, 1)
        self._shell.set_current_index(self.IDX_SESSIONS)   # app opens on Sessions

        # After the window is up, check for a recording that was interrupted
        # (crashed mid-session) last time and offer to open it.
        QTimer.singleShot(0, self._check_for_interrupted_sessions)

    def _go_to_new_recording(self):
        self._shell.set_current_index(self.IDX_RECORD)

    def _on_session_saved(self, session_root):
        self.sync_tab.load_session_folder(session_root)
        self._shell.set_current_index(self.IDX_REVIEW)

    def _check_for_interrupted_sessions(self):
        """Scan the capture folder for sessions that never finished and, if
        any are found, flag them (so we don't ask again) and offer to open the
        most recent one. Never raises — a recovery prompt must not block launch."""
        try:
            from session_storage import scan_interrupted_sessions, mark_session_interrupted
            from live_session_tab import _default_capture_root
            found = scan_interrupted_sessions(_default_capture_root())
        except Exception as e:
            log.warning("interrupted-session scan failed: %s", e)
            return
        if not found:
            return
        # Flag every detected interrupted session so none of them re-prompt,
        # then ask about the most recent.
        for s in found:
            try:
                mark_session_interrupted(s.root)
                session_history.log_event(
                    s.root, session_history.EVENT_RECORDING_INTERRUPTED)
            except Exception as e:
                log.warning("could not flag interrupted session %s: %s", s.root, e)
        recent = found[0]
        when = _friendly_when(recent.started_at)
        box = QMessageBox(self)
        box.setWindowTitle("Recording interrupted")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            f"A recording from {when} was interrupted.\n\n"
            f"Everything recorded up to that point has been kept."
        )
        open_btn = box.addButton("Open it", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            try:
                session_history.log_event(
                    recent.root, session_history.EVENT_RECORDING_RECOVERED)
                self.sync_tab.load_session_folder(recent.root)
                self._shell.set_current_index(self.IDX_REVIEW)
            except Exception as e:
                log.warning("could not open interrupted session %s: %s", recent.root, e)

    def _restyle_app(self):
        """Apply the light global stylesheet once."""
        import design_tokens as dt
        self.setStyleSheet(dt.qss_app() + _supplementary_qss())


if __name__ == "__main__":
    import traceback as _tb

    # Light mode only — no theme preference to load (DESIGN.md).
    app = QApplication(sys.argv); app.setApplicationName("Brain Battle")
    from fonts import register_fonts
    register_fonts()   # bundled Inter; falls back to the system font if absent
    def _excepthook(exc_type, exc_value, exc_tb):
        log.critical("UNHANDLED EXCEPTION", exc_info=(exc_type, exc_value, exc_tb))
    sys.excepthook = _excepthook
    try:
        window = BrainBattleApp(); window.show()
    except Exception:
        _tb.print_exc(); input("\nPress Enter to exit..."); sys.exit(1)
    def _cleanup():
        # Stop EEG streams + camera threads + loader threads before Qt
        # tears down widgets — prevents "QThread destroyed while still
        # running" crash on exit.
        try: window.live_tab.shutdown()
        except Exception: pass
        try: window.sync_tab.shutdown()
        except Exception: pass
    app.aboutToQuit.connect(_cleanup)
    sys.exit(app.exec())