"""
sessions_screen.py
──────────────────
The Sessions home screen (docs/DESIGN.md "Sessions", design F): every recording
in the recordings folder, with what to do next one click away.

    [ ● New recording — big blue button ]   [Recordings 5]   [Labelled 2 of 5]
    Sessions   All 5 · Needs labels 2 · Labelled 1 · Exported 1 · Interrupted 1   [search]
    ┌──────────────────────────────────────────────────────────────────────────┐
    │ ● K7Q2 vs ● M3XA                       You: 4 labels  [Needs labels] ⟲ [Continue labelling] │
    │ Mon 16 Nov 2026 · 3 rounds · 12 min                                       │
    └──────────────────────────────────────────────────────────────────────────┘
    Saved in Lab Server › Brain Battle Data · 73.9 GB free · fighters are stored only as codes

Each row's button is the right next step for YOU (``row_action``): Start
labelling, Continue labelling, Add my labels, or Open. ⟲ opens the history
drawer (who did what, from history.jsonl). Right-click a row for "Export for
analysis…" and "Show in Finder / Explorer". A recording whose marked moments
still have no note counts as "Needs labels".

Scanning and folder sizing run on background threads so the screen never
blocks, even with hundreds of recordings on a slow shared drive. If the
recordings folder cannot be reached (a shared drive that is not connected) the
screen says so and offers Try again instead of showing an empty list.

Emits:
  open_session(str)    — open a recording (its folder path)
  export_session(str)  — export a recording without opening it
  new_recording()      — "New recording"
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil

from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer
from PyQt6.QtGui import QPainter, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QLineEdit, QScrollArea, QMenu, QSizePolicy,
)

import brain_battle.ui.design_tokens as dt
import brain_battle.ui.widgets as ui
import brain_battle.data.app_settings as app_settings
import brain_battle.data.safe_io as safe_io
from brain_battle.support.resource_limits import release_qthread
import brain_battle.data.session_storage as ss
import brain_battle.data.session_history as session_history
from brain_battle.support import platform_utils
from brain_battle.ui.fonts import register_fonts

# Status tabs (key, label, matching statuses)
CHIPS = [
    ("all",             "All",          None),
    ("needs_labelling", "Needs labels", {ss.STATUS_NEEDS_LABELLING}),
    ("labelled",        "Labelled",     {ss.STATUS_LABELLED}),
    ("exported",        "Exported",     {ss.STATUS_EXPORTED}),
    ("interrupted",     "Interrupted",  {ss.STATUS_INTERRUPTED}),
]


# ── pure helpers (unit-tested) ────────────────────────────────────────────────

def _fmt_mmss(sec: float) -> str:
    sec = max(0, int(round(sec or 0)))
    return f"{sec // 60}:{sec % 60:02d}"


def _started(info):
    try:
        return _dt.datetime.fromisoformat(info.started_at) if info.started_at else None
    except (ValueError, TypeError):
        return None


def _human_date(info) -> str:
    d = _started(info)
    return platform_utils.format_date(d) if d else (info.started_at or "")[:10]


def _human_time(info) -> str:
    d = _started(info)
    return platform_utils.format_time(d) if d else ""


def length_text(info) -> str:
    """'3 rounds · 12 min', or how far an interrupted / stopped recording got."""
    if not info.readable:
        return "—"
    if info.stopped_sec is not None:
        how = "Interrupted" if info.interrupted else "Stopped early"
        return f"{how} · saved up to {_fmt_mmss(info.stopped_sec)}"
    mins = max(1, round(info.planned_sec / 60)) if info.planned_sec else 0
    rounds = info.rounds
    return f"{rounds} round{'s' if rounds != 1 else ''} · {mins} min"


def labels_text(info) -> tuple[str, str]:
    """(main line, who line): '24 labels', 'Maya K, Jonas R'."""
    if not info.readable:
        return "—", ""
    n = info.labels_count
    if n == 0:
        return "No labels yet", ""
    who = [name for name, count in info.labellers.items() if count]
    main = f"{n} label{'s' if n != 1 else ''}"
    if len(who) > 2:
        return main, f"{len(who)} labellers"
    return main, ", ".join(who)


def row_action(info, me: str) -> tuple[str, str, str]:
    """(button text, 'primary' | 'soft', progress line) — the right next step
    for the person at this computer (DESIGN "Sessions")."""
    names = [n for n, c in info.labellers.items() if c]
    open_m = info.moments_open
    notes = f", {open_m} moment{'s' if open_m != 1 else ''} without a note" if open_m else ""
    if info.status == ss.STATUS_INTERRUPTED:
        return "Open", "soft", (f"Saved up to {_fmt_mmss(info.stopped_sec)}"
                                if info.stopped_sec is not None else "Interrupted")
    if info.status == ss.STATUS_EXPORTED:
        return "Open", "soft", ("By " + ", ".join(names)) if names else "Exported"
    mine = info.labels_by(me) if me else 0
    needs = info.status == ss.STATUS_NEEDS_LABELLING
    if mine and needs:
        return ("Continue labelling", "primary",
                f"You: {mine} label{'s' if mine != 1 else ''}{notes}")
    if not info.labels_count:
        return ("Start labelling", "primary",
                "Nobody has labelled it yet" + (notes.replace(", ", " · ", 1) if notes else ""))
    if me and not mine:
        return "Add my labels", "primary", ", ".join(names) + " labelled · not you yet"
    return (("Continue labelling", "primary") if needs else ("Open", "soft")) + (
        "By " + ", ".join(names) + (f" · {open_m} moments without a note" if open_m else ""),)


def _searchable(info) -> str:
    bits = [info.name, info.boxer_a or "", info.boxer_b or "",
            info.started_at[:10] if info.started_at else "", _human_date(info),
            " ".join(info.labellers)]
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
    """Reveal a folder (or file) in Finder / Explorer, selecting it."""
    platform_utils.reveal_in_file_manager(path)


def folder_display(path: str) -> str:
    """'Lab Server › Brain Battle Data' — the last two parts of a folder."""
    parts = [p for p in os.path.abspath(path or "").replace("\\", "/").split("/") if p]
    return " › ".join(parts[-2:]) if parts else "—"


# ── background workers ────────────────────────────────────────────────────────

class _ScanWorker(QThread):
    done = pyqtSignal(object)        # list[SessionInfo]
    folder_state = pyqtSignal(str)   # safe_io.FOLDER_OK / _MISSING / _READ_ONLY
    free_space = pyqtSignal(object)  # bytes free, or None

    def __init__(self, base_dir, parent=None):
        super().__init__(parent); self._base = base_dir

    def run(self):
        # Is the folder there at all? (Checked here, off the UI thread: asking
        # about a network drive that has gone away can take seconds.)
        state = safe_io.FOLDER_OK
        try:
            if not os.path.isdir(self._base):
                state = safe_io.FOLDER_MISSING
        except OSError:
            state = safe_io.FOLDER_MISSING
        self.folder_state.emit(state)
        try:
            self.free_space.emit(shutil.disk_usage(self._base).free
                                 if state == safe_io.FOLDER_OK else None)
        except OSError:
            self.free_space.emit(None)
        try:
            if state == safe_io.FOLDER_OK:
                ss.flag_new_interrupted(self._base)   # flag fresh crashes first
            rows = ss.list_sessions(self._base)
        except Exception:
            rows = []
        self.done.emit(rows)


# ── the screen ────────────────────────────────────────────────────────────────

class _Row(QFrame):
    """One recording, as a card. Clicking it (not a button) opens it."""
    clicked = pyqtSignal()
    context = pyqtSignal(object)      # global point

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sessionRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QFrame#sessionRow {{ background: {dt.card_bg()}; border: 1px solid {dt.border()}; "
            f"border-radius: 14px; }}"
            f"QFrame#sessionRow:hover {{ border-color: {dt.border_strong()}; }}")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e):
        self.context.emit(e.globalPos())


class _DrawerCover(QWidget):
    """The dimmed cover behind the history drawer; a click on it closes it."""
    clicked = pyqtSignal()

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), QColor(15, 23, 42, 90))
        finally:
            p.end()

    def mousePressEvent(self, e):
        self.clicked.emit()


class HistoryDrawer(QFrame):
    """The ⟲ drawer: who labelled it and everything that happened to it."""
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("historyDrawer")
        self.setStyleSheet(f"#historyDrawer {{ background: {dt.surface()}; "
                           f"border-left: 1px solid {dt.border()}; }}")
        self.setFixedWidth(440)
        v = QVBoxLayout(self); v.setContentsMargins(24, 22, 24, 22); v.setSpacing(12)
        top = QHBoxLayout()
        tv = QVBoxLayout(); tv.setSpacing(2)
        tv.addWidget(ui.caption("History"))
        self.title = QLabel(); self.title.setTextFormat(Qt.TextFormat.RichText)
        self.meta = ui.muted("", size=dt.SIZE_SM)
        tv.addWidget(self.title); tv.addWidget(self.meta)
        top.addLayout(tv, 1)
        close = ui.icon_button("x", "Close")
        close.clicked.connect(self.closed.emit)
        top.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(top)
        self.people = QLabel(); self.people.setWordWrap(True)
        self.people.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.people)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setObjectName("historyScroll")
        sc.setStyleSheet(f"#historyScroll {{ background: {dt.surface()}; border: none; }}")
        sc.viewport().setAutoFillBackground(False)
        host = QWidget(); host.setObjectName("historyList")
        host.setStyleSheet(f"#historyList {{ background: {dt.surface()}; }}")
        self._list = QVBoxLayout(host)
        self._list.setContentsMargins(0, 0, 0, 0); self._list.setSpacing(0); self._list.addStretch()
        sc.setWidget(host)
        v.addWidget(sc, 1)
        v.addWidget(ui.muted("Saved with the recording, so everyone who opens it sees the same "
                             "history."))

    def show_for(self, info):
        self.title.setText(ui.versus_html(info.boxer_a or "Boxer A", info.colour_a,
                                          info.boxer_b or "Boxer B", info.colour_b, 20))
        self.meta.setText(f"{_human_date(info)} · {length_text(info)}")
        ppl = [f"<b>{n}</b> · {c} labels" for n, c in info.labellers.items() if c]
        self.people.setText("&nbsp;&nbsp;&nbsp;".join(ppl))
        self.people.setVisible(bool(ppl))
        ui.clear_layout(self._list, keep_last=1)
        for e in reversed(session_history.read_history(info.root)):
            who, what = session_history.describe(e)
            when = platform_utils.format_iso(e.get("time", ""), fallback=e.get("time", ""))
            dot = (dt.success_color() if who == "Recording"
                   else dt.accent() if e.get("event") == "export_created" else dt.boxer_colors("A")[0])
            row = QLabel(f"<span style='color:{dot}'>&#9679;</span>&nbsp; <b>{who}</b> {what}"
                         f"<br><span style='color:{dt.text_tertiary()};font-family:{dt.FONT_MONO};"
                         f"font-size:12px'>{when}</span>")
            row.setTextFormat(Qt.TextFormat.RichText); row.setWordWrap(True)
            row.setObjectName("histRow")
            row.setStyleSheet(f"#histRow {{ border-bottom: 1px solid #EEF1F6; padding: 10px 0; "
                              f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_MD}px; "
                              f"color: {dt.text_primary()}; }}")
            self._list.insertWidget(self._list.count() - 1, row)
        self.setVisible(True); self.raise_()


class SessionsScreen(QWidget):
    open_session    = pyqtSignal(str)
    export_session  = pyqtSignal(str)
    new_recording   = pyqtSignal()
    help_requested  = pyqtSignal()

    def __init__(self, capture_dir, parent=None):
        super().__init__(parent)
        register_fonts()        # the bundled fonts, however this screen is made
        self._capture_dir = capture_dir
        self._sessions = []          # all SessionInfo
        self._query = ""
        self._status_key = "all"
        self._newest_first = True
        self._scan_worker = None
        self._scanned_once = False
        self._folder_state = safe_io.FOLDER_OK
        self._free = None
        self._labeller = app_settings.labeller_name()
        self.setObjectName("sessionsScreen")
        self.setStyleSheet(f"#sessionsScreen {{ background: {dt.canvas_bg()}; }}")
        self._build()

    # ---- construction ----
    def _build(self):
        outer = QHBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        page = QWidget()
        outer.addWidget(page, 1)
        # The history drawer slides over the page from the right, on a dimmed
        # cover (prototype): it never squeezes the rows.
        self._cover = _DrawerCover(self)
        self._cover.clicked.connect(self.close_history)
        self._cover.setVisible(False)
        self._drawer = HistoryDrawer(self)
        self._drawer.closed.connect(self.close_history)
        self._drawer.setVisible(False)

        root = QVBoxLayout(page); root.setContentsMargins(32, 28, 32, 20); root.setSpacing(18)

        # Hero row: New recording · Recordings · Labelled
        hero = QHBoxLayout(); hero.setSpacing(16)
        self._new_btn = QPushButton()
        self._new_btn.setObjectName("heroNew")
        self._new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_btn.setMinimumHeight(100)
        self._new_btn.setStyleSheet(
            f"#heroNew {{ background: {dt.accent()}; border: 0; border-radius: 18px; }}"
            f"#heroNew:hover {{ background: {dt.accent_hover()}; }}")
        nb = QHBoxLayout(self._new_btn); nb.setContentsMargins(28, 20, 28, 20); nb.setSpacing(20)
        dot = QLabel("●"); dot.setFixedSize(52, 52); dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dot.setObjectName("heroDot")
        dot.setStyleSheet(f"#heroDot {{ background: #FFFFFF; color: {dt.record_red()}; "
                          f"border-radius: 26px; font-size: 26px; }}")
        nb.addWidget(dot)
        txt = QLabel("<span style='font-size:22px;font-weight:700;color:#FFFFFF'>New recording"
                     "</span><br><span style='color:#D6E2FB;font-size:14px'>Connect the headbands "
                     "and cameras, give each fighter a code and colour, then follow the checklist."
                     "</span>")
        txt.setTextFormat(Qt.TextFormat.RichText); txt.setWordWrap(True)
        txt.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        nb.addWidget(txt, 1)
        self._hero_txt = txt
        self._new_btn.clicked.connect(self.new_recording.emit)
        hero.addWidget(self._new_btn, 2)
        self._count_card, self._count_val = self._stat_card("Recordings")
        self._lab_card, self._lab_val = self._stat_card("Labelled")
        hero.addWidget(self._count_card, 1); hero.addWidget(self._lab_card, 1)
        root.addLayout(hero)

        # Folder problem / interrupted banners
        self._folder_banner = ui.Banner("bad")
        self._folder_banner.add_action("Try again", self.refresh, primary=True)
        self._folder_banner.setVisible(False)
        root.addWidget(self._folder_banner)
        self._banner = ui.Banner("warn")
        self._banner.add_action("Open it", self._on_banner_open, primary=True)
        self._banner.add_action("Dismiss", self._on_banner_dismiss)
        self._banner.setVisible(False)
        root.addWidget(self._banner)

        # Title · status pills · search
        controls = QHBoxLayout(); controls.setSpacing(8)
        t = QLabel("Sessions"); t.setStyleSheet(dt.css_text(dt.SIZE_2XL, 700))
        controls.addWidget(t); controls.addSpacing(10)
        self._chips = {}
        for key, label, _statuses in CHIPS:
            b = QPushButton(label); b.setCheckable(True); b.setMinimumHeight(36)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self._on_chip(k))
            self._chips[key] = b
            controls.addWidget(b)
        controls.addStretch()
        self._search = QLineEdit(); self._search.setPlaceholderText("Search a fighter code or date")
        self._search.setAccessibleName("Search sessions")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(38); self._search.setMinimumWidth(220)
        self._search.setMaximumWidth(280)
        self._search.setStyleSheet(dt.qss_input() + " QLineEdit { padding: 5px 12px; }"
                                   " QLineEdit:focus { padding: 4px 11px; }")
        self._search.textChanged.connect(self._on_search)
        controls.addWidget(self._search, 1)
        root.addLayout(controls)
        self._style_chips()

        # The list of rows
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rows_host = QWidget(); self._rows_host.setObjectName("rowsHost")
        self._rows_host.setStyleSheet(f"#rowsHost {{ background: {dt.canvas_bg()}; }}")
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0); self._rows_layout.setSpacing(8)
        self._rows_layout.addStretch()
        self._scroll.setWidget(self._rows_host)
        root.addWidget(self._scroll, 1)

        self._empty = self._build_empty()
        root.addWidget(self._empty, 1)
        self._empty.setVisible(False)

        self._footer = QLabel("")
        self._footer.setTextFormat(Qt.TextFormat.RichText)
        self._footer.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.text_tertiary()))
        root.addWidget(self._footer)

    def _stat_card(self, label):
        c = ui.Card(radius=16)
        v = QVBoxLayout(c); v.setContentsMargins(22, 18, 22, 18); v.setSpacing(0)
        v.addWidget(ui.muted(label, size=dt.SIZE_SM))
        val = QLabel("0"); val.setTextFormat(Qt.TextFormat.RichText)
        val.setStyleSheet(dt.css_text(36, 700))
        v.addWidget(val)
        return c, val

    def _build_empty(self):
        f = ui.Card()
        v = QVBoxLayout(f); v.setAlignment(Qt.AlignmentFlag.AlignCenter); v.setSpacing(10)
        icon = QLabel(); icon.setPixmap(dt.icon_pixmap("video", color=dt.border_strong(), size=48))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_title = ui.heading("No recordings yet", size=dt.SIZE_XL)
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_sub = ui.muted("Press New recording to make your first one.", size=dt.SIZE_MD)
        self._empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_btn = ui.secondary_button("Show all")
        self._empty_btn.clicked.connect(self._clear_filter)
        v.addWidget(icon); v.addWidget(self._empty_title); v.addWidget(self._empty_sub)
        v.addSpacing(6)
        v.addWidget(self._empty_btn, 0, Qt.AlignmentFlag.AlignCenter)
        return f

    # ---- data flow ----
    def set_capture_dir(self, capture_dir: str) -> None:
        """Settings' [Change folder…] changed the active recordings folder —
        point future scans at it and refresh immediately."""
        self._capture_dir = capture_dir
        self._scanned_once = False
        self.refresh()

    def set_labeller(self, name: str) -> None:
        """"Your name" changed: each row's next step depends on it."""
        self._labeller = name or ""
        self._rebuild_rows()

    def refresh(self):
        """Rescan the folder (off the UI thread) and rebuild the list."""
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return
        if not self._scanned_once:
            self._footer.setText("Looking for recordings…")
        release_qthread(self._scan_worker)      # the finished one (it is our child)
        self._scan_worker = _ScanWorker(self._capture_dir, self)
        self._scan_worker.folder_state.connect(self._on_folder_state)
        self._scan_worker.free_space.connect(self._on_free_space)
        self._scan_worker.done.connect(self._on_scanned)
        self._scan_worker.start()

    def shutdown(self):
        """Stop the background worker and wait for it (no QThread may be
        destroyed while still running). Safe to call more than once."""
        w = getattr(self, "_scan_worker", None)
        if w is not None:
            try:
                w.requestInterruption(); w.quit(); w.wait(2000)
            except RuntimeError:
                pass  # already destroyed

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def _on_folder_state(self, state):
        self._folder_state = state
        problem = safe_io.folder_problem_text(self._capture_dir, state)
        self._folder_banner.set_tone_and_text("bad", problem)
        self._folder_banner.setVisible(bool(problem))

    def _on_free_space(self, free):
        self._free = free

    def _on_scanned(self, sessions):
        self._scanned_once = True
        self._sessions = sessions
        self._update_banner()
        self._rebuild_rows()

    # ---- rendering ----
    def _visible(self):
        return filter_sort_sessions(self._sessions, self._query,
                                    self._status_key, self._newest_first)

    def _rebuild_rows(self):
        ui.clear_layout(self._rows_layout, keep_last=1)
        visible = self._visible()
        for info in visible:
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, self._make_row(info))
        has_any = len(self._sessions) > 0
        showing = len(visible)
        self._empty.setVisible(not has_any or showing == 0)
        self._scroll.setVisible(has_any and showing > 0)
        if not has_any:
            if self._folder_state != safe_io.FOLDER_OK:
                self._empty_title.setText("The recordings folder can't be reached")
                self._empty_sub.setText("Connect the drive it is on and press Try again above, "
                                        "or choose a different folder in Settings.")
            else:
                self._empty_title.setText("No recordings yet")
                self._empty_sub.setText("Press New recording to make your first one.")
            self._empty_btn.setVisible(False)
        elif showing == 0:
            self._empty_title.setText("No sessions match")
            self._empty_sub.setText("Try a different search or filter.")
            self._empty_btn.setVisible(True)
        counts = status_counts(self._sessions)
        n = counts["all"]
        self._count_val.setText(str(n))
        lab = counts.get("labelled", 0) + counts.get("exported", 0)
        self._lab_val.setText(f"{lab} <span style='font-size:18px;font-weight:500;"
                              f"color:{dt.text_tertiary()}'>of {n}</span>")
        self._style_chips()
        self._update_footer()

    def _make_row(self, info):
        row = _Row()
        h = QHBoxLayout(row); h.setContentsMargins(20, 12, 14, 12); h.setSpacing(14)
        left = QVBoxLayout(); left.setSpacing(2)
        if not info.readable:
            vs = QLabel(info.name); vs.setStyleSheet(dt.css_text(dt.SIZE_LG, 600))
            meta = ui.muted("This folder's details could not be read.", wrap=False)
        else:
            vs = QLabel(ui.versus_html(info.boxer_a or "Boxer A", info.colour_a,
                                       info.boxer_b or "Boxer B", info.colour_b, 16))
            vs.setTextFormat(Qt.TextFormat.RichText)
            meta = QLabel(f"{_human_date(info)} · {length_text(info)}"
                          + ("  ·  demo" if info.demo else ""))
            meta.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.text_tertiary(),
                                           f"font-family: {dt.FONT_MONO};"))
        meta.setWordWrap(True)                   # wraps instead of pushing Open off the row
        meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        vs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        vs.setMinimumWidth(vs.sizeHint().width())
        left.addWidget(vs); left.addWidget(meta)
        lw = QWidget(); lw.setLayout(left)
        lw.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        h.addWidget(lw, 1)
        if info.readable:
            text, kind, prog = row_action(info, self._labeller)
            pl = ui.muted(prog, size=dt.SIZE_SM)
            pl.setMinimumWidth(110); pl.setMaximumWidth(230)
            pl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            pl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            h.addWidget(pl)
        chip_box = QHBoxLayout(); chip_box.addStretch(); chip_box.addWidget(ui.status_chip(info.status))
        cw = QWidget(); cw.setLayout(chip_box); cw.setFixedWidth(118)
        h.addWidget(cw)
        hist = ui.icon_button("history", "History", size=42)
        hist.clicked.connect(lambda _c=False, i=info: self.show_history(i))
        h.addWidget(hist)
        if info.readable:
            btn = (ui.primary_button(text) if kind == "primary" else QPushButton(text))
            if kind != "primary":
                btn.setMinimumHeight(ui.MIN_TARGET)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setStyleSheet(
                    f"QPushButton {{ background: {dt.accent_ghost()}; color: {dt.accent_hover()}; "
                    f"border: 1px solid {dt.accent_ghost()}; border-radius: 10px; padding: 0 16px; "
                    f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_MD}px; font-weight: 600; }}")
            btn.setMinimumWidth(130)
            btn.clicked.connect(lambda _c=False, r=info.root: self.open_session.emit(r))
            h.addWidget(btn)
            row.clicked.connect(lambda r=info.root: self.open_session.emit(r))
        row.context.connect(lambda pos, i=info: self._row_menu(i, pos))
        row.info = info
        return row

    def rows(self) -> list:
        """The row widgets currently shown (tests)."""
        return [self._rows_layout.itemAt(i).widget() for i in range(self._rows_layout.count() - 1)]

    def show_history(self, info):
        self._place_drawer()
        self._cover.setVisible(True); self._cover.raise_()
        self._drawer.show_for(info)

    def close_history(self):
        self._drawer.setVisible(False)
        self._cover.setVisible(False)

    def _place_drawer(self):
        self._cover.setGeometry(self.rect())
        w = min(self._drawer.width(), self.width())
        self._drawer.setGeometry(self.width() - w, 0, w, self.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_drawer()
        QTimer.singleShot(0, self._fit_hero)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._fit_hero)

    def _fit_hero(self):
        """A button doesn't grow for the wrapped text inside it: size it by hand
        so "…then follow the checklist." is never cut off on a narrow window."""
        txt = self._hero_txt
        if txt.width() > 0:
            self._new_btn.setMinimumHeight(max(100, txt.heightForWidth(txt.width()) + 40))

    def _row_menu(self, info, pos):
        menu = QMenu(self)
        if info.readable:
            menu.addAction("Open", lambda: self.open_session.emit(info.root))
            menu.addAction("Export for analysis…", lambda: self.export_session.emit(info.root))
            menu.addAction("History", lambda: self.show_history(info))
            menu.addSeparator()
        menu.addAction(platform_utils.show_in_file_manager_label(),
                       lambda: reveal_in_finder(info.root))
        menu.exec(pos)

    def _update_footer(self):
        free = f" · {ss.human_bytes(self._free)} free" if self._free is not None else ""
        self._footer.setText(
            f"Saved in <b style='color:{dt.text_primary()}'>{folder_display(self._capture_dir)}</b>"
            f"{free} · fighters are stored only as codes")

    def _update_banner(self):
        pending = [s for s in self._sessions
                   if s.interrupted and not s.dismissed and s.readable]
        if not pending:
            self._banner.setVisible(False)
            self._banner_root = None
            return
        s = pending[0]
        self._banner_root = s.root
        vs = f"{s.boxer_a or 'Boxer A'} vs {s.boxer_b or 'Boxer B'}"
        where = _fmt_mmss(s.stopped_sec) if s.stopped_sec is not None else "an early point"
        self._banner.set_text(
            f"A recording was interrupted — the recording from {_human_date(s)} {_human_time(s)} "
            f"({vs}) stopped unexpectedly at {where}. Everything recorded up to "
            f"that point has been kept.")
        self._banner.setVisible(True)

    # ---- events ----
    def _on_search(self, text):
        self._query = text; self._rebuild_rows()

    def _on_chip(self, key):
        self._status_key = key; self._rebuild_rows()

    def _clear_filter(self):
        self._status_key = "all"
        self._search.setText("")
        self._rebuild_rows()

    def _on_banner_open(self):
        if getattr(self, "_banner_root", None):
            self.open_session.emit(self._banner_root)

    def _on_banner_dismiss(self):
        root = getattr(self, "_banner_root", None)
        if not root:
            return
        ss.dismiss_interrupted(root)
        try:
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
            b.setText(f"{label} {counts.get(key, 0)}")
            active = (key == self._status_key)
            b.setChecked(active)
            if active:
                b.setStyleSheet(f"QPushButton {{ border: 1px solid {dt.accent()}; background: "
                                f"{dt.accent_ghost()}; color: {dt.accent_hover()}; border-radius: 18px; "
                                f"padding: 0 14px; font-family: {dt.FONT_UI}; font-size: 14px; "
                                f"font-weight: 600; }}")
            else:
                b.setStyleSheet(f"QPushButton {{ border: 1px solid {dt.border_strong()}; background: "
                                f"{dt.surface()}; color: {dt.text_primary()}; border-radius: 18px; "
                                f"padding: 0 14px; font-family: {dt.FONT_UI}; font-size: 14px; "
                                f"font-weight: 500; }}")


__all__ = ["SessionsScreen", "HistoryDrawer", "filter_sort_sessions", "status_counts",
           "length_text", "labels_text", "row_action", "reveal_in_finder", "folder_display"]
