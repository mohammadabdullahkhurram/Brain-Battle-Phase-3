"""
sessions_tab.py
───────────────
The Sessions home screen (docs/DESIGN.md §1): a library of every recording in
the recordings folder. Scanning + folder sizing run on background threads so
the UI never blocks, even with 200+ recordings.

Emits:
  open_session(str)   — the user wants to open a recording (its folder path)
  new_recording()     — the user pressed "New recording"
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
import sys

from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QLineEdit, QScrollArea, QSizePolicy, QComboBox,
)

import design_tokens as dt
import session_storage as ss

STATUS_META = {
    ss.STATUS_INTERRUPTED:     ("Interrupted",     "warning"),
    ss.STATUS_EXPORTED:        ("Exported",        "connected"),
    ss.STATUS_LABELLED:        ("Labelled",        "connected"),
    ss.STATUS_NEEDS_LABELLING: ("Needs labelling", "baseline"),
    ss.STATUS_UNREADABLE:      ("Can't be opened", "recording"),
}
# Filter chips (key, label, matching statuses)
CHIPS = [
    ("all",             "All",             None),
    ("needs_labelling", "Needs labelling", {ss.STATUS_NEEDS_LABELLING}),
    ("labelled",        "Labelled",        {ss.STATUS_LABELLED}),
    ("exported",        "Exported",        {ss.STATUS_EXPORTED}),
    ("interrupted",     "Interrupted",     {ss.STATUS_INTERRUPTED}),
]


# ── pure helpers (unit-tested) ────────────────────────────────────────────────

def _fmt_mmss(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 60}:{sec % 60:02d}"


def _human_date(info) -> str:
    if not info.started_at:
        return ""
    try:
        d = _dt.datetime.fromisoformat(info.started_at)
        return d.strftime("%a %-d %b %Y")
    except (ValueError, TypeError):
        return info.started_at[:10]


def _human_time(info) -> str:
    if not info.started_at:
        return ""
    try:
        return _dt.datetime.fromisoformat(info.started_at).strftime("%H:%M")
    except (ValueError, TypeError):
        return ""


def length_text(info) -> str:
    if not info.readable:
        return "—"
    if info.status == ss.STATUS_INTERRUPTED and info.stopped_sec is not None:
        return f"Stopped at {_fmt_mmss(info.stopped_sec)} of {_fmt_mmss(info.planned_sec)}"
    mins = max(1, round(info.planned_sec / 60)) if info.planned_sec else 0
    rounds = info.rounds
    return f"{mins} min · {rounds} round{'s' if rounds != 1 else ''}"


def _searchable(info) -> str:
    bits = [info.name, info.boxer_a or "", info.boxer_b or "",
            info.started_at[:10] if info.started_at else "", _human_date(info)]
    return " ".join(bits).lower()


def filter_sort_sessions(sessions, query="", status_key="all", newest_first=True):
    """Pure filter + sort used by the UI (and the tests)."""
    q = (query or "").strip().lower()
    chip = next((c for c in CHIPS if c[0] == status_key), CHIPS[0])
    statuses = chip[2]
    out = []
    for s in sessions:
        if statuses is not None and s.status not in statuses:
            continue
        if q and q not in _searchable(s):
            continue
        out.append(s)
    out.sort(key=lambda s: (s.started_unix, s.name), reverse=newest_first)
    return out


def status_counts(sessions) -> dict:
    counts = {"all": len(sessions)}
    for key, _lbl, statuses in CHIPS[1:]:
        counts[key] = sum(1 for s in sessions if s.status in statuses)
    return counts


def reveal_in_finder(path: str) -> None:
    """Reveal a folder (or file) in the OS file manager, selecting it."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path) or path])
    except Exception:
        pass


# ── background workers ────────────────────────────────────────────────────────

class _ScanWorker(QThread):
    done = pyqtSignal(object)   # list[SessionInfo]

    def __init__(self, base_dir, parent=None):
        super().__init__(parent); self._base = base_dir

    def run(self):
        try:
            ss.flag_new_interrupted(self._base)   # flag fresh crashes first
            rows = ss.list_sessions(self._base)
        except Exception:
            rows = []
        self.done.emit(rows)


class _SizeWorker(QThread):
    one = pyqtSignal(str, int)   # (root, bytes)
    done = pyqtSignal(int)       # total bytes

    def __init__(self, roots, parent=None):
        super().__init__(parent); self._roots = list(roots)

    def run(self):
        total = 0
        for r in self._roots:
            if self.isInterruptionRequested():
                return
            b = ss.folder_size_bytes(r)
            total += b
            self.one.emit(r, b)
        self.done.emit(total)


# ── widgets ───────────────────────────────────────────────────────────────────

def _badge(text, kind, parent=None):
    lbl = QLabel(text, parent)
    lbl.setStyleSheet(dt.qss_status_pill(kind))
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl


def _boxer_badge(letter, name):
    line, soft, text = dt.boxer_colors(letter)
    lbl = QLabel(f"{letter} · {name}" if name else "—")
    if name:
        lbl.setStyleSheet(
            f"background: {soft}; color: {text}; border: 1px solid {line}; "
            f"border-radius: {dt.RADIUS_SM}px; padding: 3px 8px; "
            f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_XS}px; font-weight: 600;")
    else:
        lbl.setStyleSheet(
            f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_SM}px; background: transparent;")
    return lbl


class SessionsTab(QWidget):
    open_session  = pyqtSignal(str)
    new_recording = pyqtSignal()

    # column widths (px); the Boxers column stretches
    _COL = dict(recorded=140, length=180, labels=70, status=150, actions=190)

    def __init__(self, capture_dir, parent=None):
        super().__init__(parent)
        self._capture_dir = capture_dir
        self._sessions = []          # all SessionInfo
        self._sizes = {}             # root -> bytes
        self._query = ""
        self._status_key = "all"
        self._newest_first = True
        self._scan_worker = None
        self._size_worker = None
        self.setStyleSheet(f"SessionsTab {{ background: {dt.canvas_bg()}; }}")
        self._build()

    # ---- construction ----
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(40, 32, 40, 24); root.setSpacing(0)

        # Header
        header = QHBoxLayout()
        htext = QVBoxLayout(); htext.setSpacing(4)
        title = QLabel("Sessions")
        title.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
                            f"font-size: 24px; font-weight: 700;")
        sub = QLabel("Every recording in your recordings folder. Open one to "
                     "watch it back and add labels.")
        sub.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                          f"font-size: {dt.SIZE_MD}px;")
        htext.addWidget(title); htext.addWidget(sub)
        header.addLayout(htext); header.addStretch()
        show_btn = QPushButton("Show folder in Finder")
        show_btn.setStyleSheet(dt.qss_secondary_button()); show_btn.setMinimumHeight(44)
        show_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        show_btn.clicked.connect(lambda: reveal_in_finder(self._capture_dir))
        new_btn = QPushButton("New recording")
        new_btn.setStyleSheet(dt.qss_primary_button()); new_btn.setMinimumHeight(44)
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.clicked.connect(self.new_recording.emit)
        header.addWidget(show_btn); header.addSpacing(10); header.addWidget(new_btn)
        root.addLayout(header)
        root.addSpacing(16)

        # Interrupted banner (hidden unless there's an undismissed interruption)
        self._banner = self._build_banner()
        self._banner.setVisible(False)
        root.addWidget(self._banner)
        self._banner_spacer = QWidget(); self._banner_spacer.setFixedHeight(12)
        self._banner_spacer.setVisible(False)
        root.addWidget(self._banner_spacer)

        # Controls: search + sort
        controls = QHBoxLayout(); controls.setSpacing(10)
        self._search = QLineEdit(); self._search.setPlaceholderText("Search by boxer name or date")
        self._search.setMinimumHeight(40); self._search.setStyleSheet(
            f"QLineEdit {{ background: {dt.surface()}; border: 1px solid {dt.border_strong()}; "
            f"border-radius: {dt.RADIUS_MD}px; padding: 8px 12px; color: {dt.text_primary()}; "
            f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_MD}px; }}"
            f"QLineEdit:focus {{ border-color: {dt.accent()}; }}")
        self._search.textChanged.connect(self._on_search)
        controls.addWidget(self._search, 1)
        self._sort = QComboBox(); self._sort.addItems(["Newest first", "Oldest first"])
        self._sort.setMinimumHeight(40)
        self._sort.currentIndexChanged.connect(self._on_sort)
        controls.addWidget(self._sort)
        root.addLayout(controls)
        root.addSpacing(10)

        # Status chips
        self._chip_row = QHBoxLayout(); self._chip_row.setSpacing(8)
        self._chips = {}
        for key, label, _statuses in CHIPS:
            b = QPushButton(label); b.setCheckable(True); b.setMinimumHeight(32)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self._on_chip(k))
            self._chips[key] = b
            self._chip_row.addWidget(b)
        self._chip_row.addStretch()
        root.addLayout(self._chip_row)
        root.addSpacing(10)
        self._style_chips()

        # Table header
        self._table_header = self._build_table_header()
        root.addWidget(self._table_header)

        # Scrollable rows
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rows_host = QWidget()
        self._rows_host.setStyleSheet(f"background: {dt.canvas_bg()};")
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0); self._rows_layout.setSpacing(0)
        self._rows_layout.addStretch()
        self._scroll.setWidget(self._rows_host)
        root.addWidget(self._scroll, 1)

        # Empty state (overlaps the scroll area region when shown)
        self._empty = self._build_empty()
        root.addWidget(self._empty, 1)
        self._empty.setVisible(False)

        # Footer
        self._footer = QLabel("")
        self._footer.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                                   f"font-size: {dt.SIZE_SM}px;")
        root.addSpacing(8); root.addWidget(self._footer)

    def _build_banner(self):
        f = QFrame(); f.setObjectName("banner")
        f.setStyleSheet(
            f"QFrame#banner {{ background: {dt._Palette.warning_bg}; "
            f"border: 1px solid {dt.warning_color()}; border-radius: {dt.RADIUS_MD}px; }}")
        h = QHBoxLayout(f); h.setContentsMargins(16, 10, 12, 10); h.setSpacing(10)
        self._banner_text = QLabel("")
        self._banner_text.setWordWrap(True)
        self._banner_text.setStyleSheet(
            f"color: {dt._Palette.warning_text}; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_SM}px; background: transparent; border: none;")
        h.addWidget(self._banner_text, 1)
        open_btn = QPushButton("Open it"); open_btn.setMinimumHeight(32)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setStyleSheet(
            f"QPushButton {{ background: {dt.warning_color()}; color: #FFFFFF; border: none; "
            f"border-radius: {dt.RADIUS_SM}px; padding: 6px 14px; font-weight: 600; "
            f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; }}")
        open_btn.clicked.connect(self._on_banner_open)
        dismiss_btn = QPushButton("Dismiss"); dismiss_btn.setMinimumHeight(32)
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.setStyleSheet(
            f"QPushButton {{ background: #FFFFFF; color: {dt._Palette.warning_text}; "
            f"border: 1px solid {dt.warning_color()}; border-radius: {dt.RADIUS_SM}px; "
            f"padding: 6px 14px; font-weight: 600; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_SM}px; }}")
        dismiss_btn.clicked.connect(self._on_banner_dismiss)
        h.addWidget(open_btn); h.addWidget(dismiss_btn)
        return f

    def _build_table_header(self):
        w = QWidget(); w.setFixedHeight(28)
        h = QHBoxLayout(w); h.setContentsMargins(12, 0, 12, 0); h.setSpacing(12)
        def hd(text, width=None, stretch=0):
            l = QLabel(text); l.setStyleSheet(
                f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                f"font-size: {dt.SIZE_CAPTION}px; font-weight: 700; letter-spacing: 0.6px;")
            if width: l.setFixedWidth(width)
            h.addWidget(l, stretch)
            return l
        hd("RECORDED", self._COL["recorded"])
        hd("BOXERS", stretch=1)
        hd("LENGTH", self._COL["length"])
        hd("LABELS", self._COL["labels"])
        hd("STATUS", self._COL["status"])
        hd("", self._COL["actions"])
        return w

    def _build_empty(self):
        f = QFrame(); f.setObjectName("emptyCard")
        f.setStyleSheet(f"QFrame#emptyCard {{ background: {dt.card_bg()}; "
                        f"border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_LG}px; }}")
        v = QVBoxLayout(f); v.setAlignment(Qt.AlignmentFlag.AlignCenter); v.setSpacing(10)
        icon = QLabel(); icon.setPixmap(dt.icon_pixmap("home", color=dt.text_tertiary(), size=40))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("background: transparent; border: none;")
        self._empty_title = QLabel("No recordings yet")
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_title.setStyleSheet(
            f"color: {dt.text_secondary()}; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_LG}px; font-weight: 600; background: transparent; border: none;")
        self._empty_sub = QLabel("Press New recording to make your first one.")
        self._empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_sub.setStyleSheet(
            f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_MD}px; background: transparent; border: none;")
        v.addWidget(icon); v.addWidget(self._empty_title); v.addWidget(self._empty_sub)
        return f

    # ---- data flow ----
    def refresh(self):
        """Rescan the folder (off the UI thread) and rebuild the list."""
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return
        self._scan_worker = _ScanWorker(self._capture_dir, self)
        self._scan_worker.done.connect(self._on_scanned)
        self._scan_worker.start()

    def shutdown(self):
        """Stop the background workers and wait for them (no QThread may be
        destroyed while still running). Safe to call more than once."""
        for attr in ("_scan_worker", "_size_worker"):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.requestInterruption()
                    w.quit()
                    w.wait(2000)
                except RuntimeError:
                    pass  # already destroyed

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def _on_scanned(self, sessions):
        self._sessions = sessions
        self._update_banner()
        self._rebuild_rows()
        # Folder sizes on a second background thread; footer fills in as they land.
        if self._size_worker is not None and self._size_worker.isRunning():
            self._size_worker.requestInterruption()
        self._sizes = {}
        roots = [s.root for s in sessions]
        self._size_worker = _SizeWorker(roots, self)
        self._size_worker.one.connect(self._on_one_size)
        self._size_worker.done.connect(lambda _t: self._update_footer())
        self._size_worker.start()
        self._update_footer()

    def _on_one_size(self, root, nbytes):
        self._sizes[root] = nbytes
        self._update_footer()

    # ---- rendering ----
    def _visible(self):
        return filter_sort_sessions(self._sessions, self._query,
                                    self._status_key, self._newest_first)

    def _rebuild_rows(self):
        # clear existing row widgets (keep the trailing stretch)
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None); w.deleteLater()
        visible = self._visible()
        for info in visible:
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, self._make_row(info))
        has_any = len(self._sessions) > 0
        showing = len(visible)
        self._empty.setVisible(not has_any or showing == 0)
        self._scroll.setVisible(has_any and showing > 0)
        self._table_header.setVisible(has_any and showing > 0)
        if not has_any:
            self._empty_title.setText("No recordings yet")
            self._empty_sub.setText("Press New recording to make your first one.")
        elif showing == 0:
            self._empty_title.setText("No sessions match")
            self._empty_sub.setText("Try a different search or filter.")
        self._style_chips()

    def _make_row(self, info):
        row = QFrame(); row.setObjectName("sessionRow")
        row.setStyleSheet(
            f"QFrame#sessionRow {{ background: {dt.card_bg()}; "
            f"border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_MD}px; }}")
        h = QHBoxLayout(row); h.setContentsMargins(12, 8, 12, 8); h.setSpacing(12)
        # Recorded (date bold / time below)
        rec = QVBoxLayout(); rec.setSpacing(1)
        d = QLabel(_human_date(info) or "—")
        d.setStyleSheet(f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
                        f"font-size: {dt.SIZE_SM}px; font-weight: 600; background: transparent;")
        t = QLabel(_human_time(info))
        t.setStyleSheet(f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
                        f"font-size: {dt.SIZE_XS}px; background: transparent;")
        rec.addWidget(d); rec.addWidget(t)
        recw = QWidget(); recw.setLayout(rec); recw.setFixedWidth(self._COL["recorded"])
        h.addWidget(recw)
        # Boxers
        bx = QHBoxLayout(); bx.setSpacing(6)
        bx.addWidget(_boxer_badge("A", info.boxer_a))
        bx.addWidget(_boxer_badge("B", info.boxer_b))
        bx.addStretch()
        bxw = QWidget(); bxw.setLayout(bx); h.addWidget(bxw, 1)
        # Length
        ln = QLabel(length_text(info)); ln.setFixedWidth(self._COL["length"])
        ln.setStyleSheet(f"color: {dt.text_secondary()}; font-family: {dt.FONT_UI}; "
                         f"font-size: {dt.SIZE_SM}px; background: transparent;")
        h.addWidget(ln)
        # Labels count
        lc = QLabel(str(info.labels_count) if info.readable else "—")
        lc.setFixedWidth(self._COL["labels"])
        lc.setStyleSheet(f"color: {dt.text_secondary()}; font-family: {dt.FONT_MONO}; "
                         f"font-size: {dt.SIZE_SM}px; background: transparent;")
        h.addWidget(lc)
        # Status badge (icon + text)
        label, kind = STATUS_META.get(info.status, ("—", "idle"))
        badge = _badge(label, kind); badge.setFixedWidth(self._COL["status"])
        h.addWidget(badge)
        # Actions
        act = QHBoxLayout(); act.setSpacing(6)
        finder = QPushButton("Show in Finder"); finder.setMinimumHeight(32)
        finder.setCursor(Qt.CursorShape.PointingHandCursor)
        finder.setStyleSheet(_small_secondary())
        finder.clicked.connect(lambda _c=False, r=info.root: reveal_in_finder(r))
        act.addWidget(finder)
        if info.readable:
            is_needs = info.status == ss.STATUS_NEEDS_LABELLING
            btn = QPushButton("Label" if is_needs else "Open")
            btn.setMinimumHeight(32); btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(_small_primary())
            btn.clicked.connect(lambda _c=False, r=info.root: self.open_session.emit(r))
            act.addWidget(btn)
        actw = QWidget(); actw.setLayout(act); actw.setFixedWidth(self._COL["actions"])
        h.addWidget(actw)

        wrap = QFrame()
        wl = QVBoxLayout(wrap); wl.setContentsMargins(0, 0, 0, 6); wl.setSpacing(0)
        wl.addWidget(row)
        return wrap

    def _update_footer(self):
        visible = self._visible()
        total_bytes = sum(self._sizes.get(s.root, 0) for s in self._sessions)
        folder = os.path.basename(os.path.abspath(self._capture_dir)) if self._capture_dir else "—"
        # Once sizes are in (or there's nothing to size), show the total.
        sized = self._sizes or not self._sessions
        size_txt = ss.human_bytes(total_bytes) if sized else "calculating…"
        self._footer.setText(
            f"{len(visible)} of {len(self._sessions)} sessions · {size_txt} used in {folder}")

    def _update_banner(self):
        pending = [s for s in self._sessions
                   if s.interrupted and not s.dismissed and s.readable]
        if not pending:
            self._banner.setVisible(False); self._banner_spacer.setVisible(False)
            self._banner_root = None
            return
        s = pending[0]
        self._banner_root = s.root
        when = _human_date(s)
        tm = _human_time(s)
        vs = (f"{s.boxer_a} vs {s.boxer_b}" if s.boxer_a and s.boxer_b
              else (s.boxer_a or s.boxer_b or "no names"))
        where = _fmt_mmss(s.stopped_sec) if s.stopped_sec is not None else "an early point"
        self._banner_text.setText(
            f"A recording was interrupted — the recording from {when} {tm} "
            f"({vs}) stopped unexpectedly at {where}. Everything recorded up to "
            f"that point has been kept.")
        self._banner.setVisible(True); self._banner_spacer.setVisible(True)

    # ---- events ----
    def _on_search(self, text):
        self._query = text; self._rebuild_rows(); self._update_footer()

    def _on_sort(self, idx):
        self._newest_first = (idx == 0); self._rebuild_rows()

    def _on_chip(self, key):
        self._status_key = key; self._rebuild_rows(); self._update_footer()

    def _on_banner_open(self):
        if getattr(self, "_banner_root", None):
            self.open_session.emit(self._banner_root)

    def _on_banner_dismiss(self):
        root = getattr(self, "_banner_root", None)
        if not root:
            return
        ss.dismiss_interrupted(root)
        try:
            import session_history
            session_history.log_event(root, "interrupted_dismissed")
        except Exception:
            pass
        for s in self._sessions:
            if s.root == root:
                s.dismissed = True
        self._update_banner()

    def _style_chips(self):
        counts = status_counts(self._sessions)
        for key, label, _ in CHIPS:
            b = self._chips[key]
            n = counts.get(key, 0)
            b.setText(f"{label}  {n}")
            active = (key == self._status_key)
            b.setChecked(active)
            b.setStyleSheet(dt.qss_chip(active))


def _small_primary():
    return (f"QPushButton {{ background: {dt.accent()}; color: #FFFFFF; border: none; "
            f"border-radius: {dt.RADIUS_SM}px; padding: 5px 14px; font-weight: 600; "
            f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; }}"
            f"QPushButton:hover {{ background: {dt.accent_hover()}; }}")


def _small_secondary():
    return (f"QPushButton {{ background: {dt.surface()}; color: {dt.text_secondary()}; "
            f"border: 1px solid {dt.border_strong()}; border-radius: {dt.RADIUS_SM}px; "
            f"padding: 5px 10px; font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; }}"
            f"QPushButton:hover {{ border-color: {dt.accent()}; color: {dt.accent()}; }}")


__all__ = ["SessionsTab", "filter_sort_sessions", "status_counts",
           "length_text", "reveal_in_finder"]
