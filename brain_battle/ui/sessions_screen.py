"""
sessions_screen.py
───────────────
The Sessions home screen (docs/DESIGN.md §1): every recording in the recordings
folder, with the two things people come here for one click away — open a
recording to label it, or export it.

Layout: a title row (New recording), one row of controls (status tabs with
counts · search · sort), then the list. Each recording is one row — click
anywhere on it to open it. The row says when it was recorded and how long it
is, who boxed, how many labels it has and from whom, and its status; the
button on the right is "Label" until *you* have labelled it. The ⋯ menu holds
"Export for analysis…" and "Show in Finder / Explorer".

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

from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QLineEdit, QScrollArea, QComboBox, QMenu,
)

import brain_battle.ui.design_tokens as dt
import brain_battle.ui.widgets as ui
import brain_battle.data.app_settings as app_settings
import brain_battle.data.privacy as privacy
import brain_battle.data.safe_io as safe_io
import brain_battle.data.session_storage as ss
from brain_battle.support import platform_utils

STATUS_META = {
    ss.STATUS_INTERRUPTED:     ("! Interrupted",     "warning"),
    ss.STATUS_EXPORTED:        ("✓ Exported",        "connected"),
    ss.STATUS_LABELLED:        ("✓ Labelled",        "accent"),
    ss.STATUS_NEEDS_LABELLING: ("Needs labelling",   "idle"),
    ss.STATUS_UNREADABLE:      ("✕ Can't be opened", "danger"),
}
# Status tabs (key, label, matching statuses)
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
        return platform_utils.format_date(_dt.datetime.fromisoformat(info.started_at))
    except (ValueError, TypeError):
        return info.started_at[:10]


def _human_time(info) -> str:
    if not info.started_at:
        return ""
    try:
        return platform_utils.format_time(_dt.datetime.fromisoformat(info.started_at))
    except (ValueError, TypeError):
        return ""


def length_text(info) -> str:
    if not info.readable:
        return "—"
    # Interrupted (crash) OR cleanly stopped early both show how far they got.
    if info.stopped_sec is not None:
        return f"Stopped at {_fmt_mmss(info.stopped_sec)} of {_fmt_mmss(info.planned_sec)}"
    mins = max(1, round(info.planned_sec / 60)) if info.planned_sec else 0
    rounds = info.rounds
    return f"{mins} min · {rounds} round{'s' if rounds != 1 else ''}"


def labels_text(info) -> tuple[str, str]:
    """(main line, who line) for the Labels column: '24 labels', 'Maya K, Sam'."""
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


# ── background workers ────────────────────────────────────────────────────────

class _ScanWorker(QThread):
    done = pyqtSignal(object)   # list[SessionInfo]
    folder_state = pyqtSignal(str)   # safe_io.FOLDER_OK / _MISSING / _READ_ONLY

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
            if state == safe_io.FOLDER_OK:
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


# ── the screen ────────────────────────────────────────────────────────────────

class _Row(QFrame):
    """One recording. Clicking anywhere on it (not on a button) opens it."""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sessionRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QFrame#sessionRow {{ background: {dt.card_bg()}; border: none; "
            f"border-bottom: 1px solid {dt.border()}; }}"
            f"QFrame#sessionRow:hover {{ background: {dt.surface_subtle()}; }}")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class SessionsScreen(QWidget):
    open_session    = pyqtSignal(str)
    export_session  = pyqtSignal(str)
    new_recording   = pyqtSignal()
    help_requested  = pyqtSignal()

    # column widths (px); the Boxers column stretches
    _COL = dict(recorded=206, labels=150, status=138, actions=132)

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
        self._scanned_once = False
        self._folder_state = safe_io.FOLDER_OK
        self._labeller = app_settings.labeller_name()
        self._hide_names = app_settings.hide_names_on_screen()
        self.setObjectName("sessionsScreen")
        self.setStyleSheet(f"#sessionsScreen {{ background: {dt.canvas_bg()}; }}")
        self._build()

    # ---- construction ----
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(36, 28, 36, 20); root.setSpacing(0)

        # Title row
        header = QHBoxLayout(); header.setSpacing(10)
        htext = QVBoxLayout(); htext.setSpacing(2)
        htext.addWidget(ui.title("Sessions"))
        htext.addWidget(ui.muted("Every recording in your recordings folder. Click one to "
                                 "watch it back and label it.", size=dt.SIZE_MD, wrap=False))
        header.addLayout(htext); header.addStretch()
        help_btn = ui.help_button()
        help_btn.clicked.connect(self.help_requested.emit)
        header.addWidget(help_btn)
        self._show_btn = ui.secondary_button(
            f"Open folder in {platform_utils.file_manager_name()}", icon="folder")
        self._show_btn.clicked.connect(lambda: platform_utils.open_folder(self._capture_dir))
        new_btn = ui.primary_button("New recording", icon="circle")
        new_btn.clicked.connect(self.new_recording.emit)
        header.addWidget(self._show_btn); header.addWidget(new_btn)
        root.addLayout(header)
        root.addSpacing(18)

        # Folder problem (a shared drive that is not connected / not writable)
        self._folder_banner = ui.Banner("bad")
        self._folder_banner.add_action("Try again", self.refresh, primary=True)
        self._folder_banner.setVisible(False)
        root.addWidget(self._folder_banner)
        self._folder_spacer = QWidget(); self._folder_spacer.setFixedHeight(12)
        self._folder_spacer.setVisible(False)
        root.addWidget(self._folder_spacer)

        # Interrupted banner (hidden unless there's an undismissed interruption)
        self._banner = ui.Banner("warn")
        self._banner_text = self._banner._text
        self._banner.add_action("Open it", self._on_banner_open, primary=True)
        self._banner.add_action("Dismiss", self._on_banner_dismiss)
        self._banner.setVisible(False)
        root.addWidget(self._banner)
        self._banner_spacer = QWidget(); self._banner_spacer.setFixedHeight(12)
        self._banner_spacer.setVisible(False)
        root.addWidget(self._banner_spacer)

        # One row of controls: status tabs (with counts) · search · sort
        controls = QHBoxLayout(); controls.setSpacing(8)
        self._chips = {}
        for key, label, _statuses in CHIPS:
            b = ui.secondary_button(label, compact=True)
            b.setCheckable(True); b.setMinimumHeight(36)
            b.clicked.connect(lambda _c=False, k=key: self._on_chip(k))
            self._chips[key] = b
            controls.addWidget(b)
        controls.addStretch()
        self._search = QLineEdit(); self._search.setPlaceholderText("Search by name or date")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(36); self._search.setMinimumWidth(180)
        self._search.setMaximumWidth(280)
        self._search.setStyleSheet(dt.qss_input() + " QLineEdit { padding: 5px 12px; }")
        self._search.textChanged.connect(self._on_search)
        controls.addWidget(self._search, 1)
        self._sort = QComboBox(); self._sort.addItems(["Newest first", "Oldest first"])
        self._sort.setFixedHeight(36)
        self._sort.setStyleSheet(dt.qss_combo() + " QComboBox { padding: 5px 12px; }")
        self._sort.currentIndexChanged.connect(self._on_sort)
        controls.addWidget(self._sort)
        root.addLayout(controls)
        root.addSpacing(12)
        self._style_chips()

        # The list: one card — a quiet header row, then the recordings
        self._list_card = ui.Card()
        lc = QVBoxLayout(self._list_card); lc.setContentsMargins(1, 1, 1, 1); lc.setSpacing(0)
        self._table_header = self._build_table_header()
        lc.addWidget(self._table_header)
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rows_host = QWidget()
        self._rows_host.setObjectName("rowsHost")
        self._rows_host.setStyleSheet(f"#rowsHost {{ background: {dt.card_bg()}; }}")
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0); self._rows_layout.setSpacing(0)
        self._rows_layout.addStretch()
        self._scroll.setWidget(self._rows_host)
        lc.addWidget(self._scroll, 1)
        root.addWidget(self._list_card, 1)

        # Empty state (takes the list's place when there is nothing to show)
        self._empty = self._build_empty()
        root.addWidget(self._empty, 1)
        self._empty.setVisible(False)

        # Footer
        self._footer = ui.muted("", size=dt.SIZE_SM, wrap=False)
        root.addSpacing(10); root.addWidget(self._footer)

    def _build_table_header(self):
        w = QFrame(); w.setObjectName("tableHead"); w.setFixedHeight(36)
        w.setStyleSheet(f"#tableHead {{ background: {dt.surface_subtle()}; border: none; "
                        f"border-bottom: 1px solid {dt.border()}; "
                        f"border-top-left-radius: {dt.RADIUS_LG - 1}px; "
                        f"border-top-right-radius: {dt.RADIUS_LG - 1}px; }}")
        h = QHBoxLayout(w); h.setContentsMargins(20, 0, 16, 0); h.setSpacing(14)
        def hd(text, width=None, stretch=0):
            l = ui.caption(text)
            if width: l.setFixedWidth(width)
            h.addWidget(l, stretch)
        hd("Recorded", self._COL["recorded"])
        hd("Boxers", stretch=1)
        hd("Labels", self._COL["labels"])
        hd("Status", self._COL["status"])
        hd("", self._COL["actions"])
        return w

    def _build_empty(self):
        f = ui.Card()
        v = QVBoxLayout(f); v.setAlignment(Qt.AlignmentFlag.AlignCenter); v.setSpacing(10)
        icon = QLabel(); icon.setPixmap(dt.icon_pixmap("video", color=dt.border_strong(), size=48))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_title = ui.heading("No recordings yet", size=dt.SIZE_XL)
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_sub = ui.muted("Press New recording to make your first one.", size=dt.SIZE_MD)
        self._empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_btn = ui.primary_button("New recording", icon="circle")
        self._empty_btn.clicked.connect(self.new_recording.emit)
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
        """"Your name" changed: the Label / Open buttons depend on it."""
        self._labeller = name or ""
        self._rebuild_rows()

    def set_names_hidden(self, hidden: bool) -> None:
        if bool(hidden) != self._hide_names:
            self._hide_names = bool(hidden)
            self._update_banner(); self._rebuild_rows()

    def refresh(self):
        """Rescan the folder (off the UI thread) and rebuild the list."""
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return
        if not self._scanned_once:
            self._footer.setText("Looking for recordings…")
        self._scan_worker = _ScanWorker(self._capture_dir, self)
        self._scan_worker.folder_state.connect(self._on_folder_state)
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

    def _on_folder_state(self, state):
        self._folder_state = state
        problem = safe_io.folder_problem_text(self._capture_dir, state)
        self._folder_banner.set_tone_and_text("bad", problem)
        self._folder_banner.setVisible(bool(problem))
        self._folder_spacer.setVisible(bool(problem))

    def _on_scanned(self, sessions):
        self._scanned_once = True
        self._sessions = sessions
        self._update_banner()
        self._rebuild_rows()
        # Folder sizes on a second background thread; footer fills in as they land.
        if self._size_worker is not None and self._size_worker.isRunning():
            self._size_worker.requestInterruption()
        known = {s.root for s in sessions}
        self._sizes = {r: b for r, b in self._sizes.items() if r in known}
        roots = [s.root for s in sessions if s.root not in self._sizes]   # only the new ones
        self._size_worker = _SizeWorker(roots, self)
        self._size_worker.one.connect(self._on_one_size)
        # a bound method, not a lambda: Qt drops the connection by itself if this
        # screen is destroyed while the worker is still finishing
        self._size_worker.done.connect(self._on_sizes_done)
        self._size_worker.start()
        self._update_footer()

    def _on_sizes_done(self, _total):
        self._update_footer()

    def _on_one_size(self, root, nbytes):
        self._sizes[root] = nbytes
        self._update_footer()

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
        self._list_card.setVisible(has_any and showing > 0)
        if not has_any:
            if self._folder_state != safe_io.FOLDER_OK:
                self._empty_title.setText("The recordings folder can't be reached")
                self._empty_sub.setText("Connect the drive it is on and press Try again above, "
                                        "or choose a different folder in Settings.")
                self._empty_btn.setVisible(False)
            else:
                self._empty_title.setText("No recordings yet")
                self._empty_sub.setText("Press New recording to make your first one.")
                self._empty_btn.setVisible(True)
        elif showing == 0:
            self._empty_title.setText("No sessions match")
            self._empty_sub.setText("Try a different search or filter.")
            self._empty_btn.setVisible(False)
        self._style_chips()

    def _make_row(self, info):
        row = _Row()
        h = QHBoxLayout(row); h.setContentsMargins(20, 12, 16, 12); h.setSpacing(14)
        # Recorded: date bold / time · length below
        rec = QVBoxLayout(); rec.setSpacing(2); rec.setContentsMargins(0, 0, 0, 0)
        d = QLabel(_human_date(info) or info.name)
        d.setStyleSheet(dt.css_text(dt.SIZE_MD, 600))
        sub_bits = [x for x in (_human_time(info), length_text(info) if info.readable else "") if x]
        t = QLabel(" · ".join(sub_bits))
        t.setStyleSheet(dt.css_text(dt.SIZE_XS, 400, dt.text_tertiary()))
        rec.addWidget(d); rec.addWidget(t)
        recw = QWidget(); recw.setLayout(rec); recw.setFixedWidth(self._COL["recorded"])
        h.addWidget(recw)
        # Boxers
        bx = QHBoxLayout(); bx.setSpacing(6); bx.setContentsMargins(0, 0, 0, 0)
        if not info.readable:
            bx.addWidget(ui.muted("This folder's details could not be read.", wrap=False))
        elif not info.boxer_a and not info.boxer_b and not self._hide_names:
            none = ui.muted("No names", wrap=False)
            bx.addWidget(none)
        else:
            for which, name in (("A", info.boxer_a), ("B", info.boxer_b)):
                if name or self._hide_names:
                    bx.addWidget(ui.boxer_chip(
                        which, privacy.shown_name(which, name, self._hide_names)
                        if not self._hide_names else "Boxer " + which))
        if info.demo:
            bx.addWidget(ui.pill("● Demo", "warning"))
        bx.addStretch()
        bxw = QWidget(); bxw.setLayout(bx); h.addWidget(bxw, 1)
        # Labels: count / who
        main, who = labels_text(info)
        lv = QVBoxLayout(); lv.setSpacing(2); lv.setContentsMargins(0, 0, 0, 0)
        lm = QLabel(main)
        lm.setStyleSheet(dt.css_text(dt.SIZE_SM, 500, dt.text_secondary()
                                     if info.labels_count else dt.text_tertiary()))
        lv.addWidget(lm)
        if who:
            lw_ = QLabel(who); lw_.setStyleSheet(dt.css_text(dt.SIZE_XS, 400, dt.text_tertiary()))
            lw_.setToolTip("Labelled by " + ", ".join(
                f"{n} ({c})" for n, c in info.labellers.items() if c))
            lv.addWidget(lw_)
        lvw = QWidget(); lvw.setLayout(lv); lvw.setFixedWidth(self._COL["labels"])
        h.addWidget(lvw)
        # Status (glyph + text, never colour alone)
        label, kind = STATUS_META.get(info.status, ("—", "idle"))
        sw = QHBoxLayout(); sw.setContentsMargins(0, 0, 0, 0)
        sw.addWidget(ui.pill(label, kind)); sw.addStretch()
        sww = QWidget(); sww.setLayout(sw); sww.setFixedWidth(self._COL["status"])
        h.addWidget(sww)
        # Actions: Label / Open + the ⋯ menu
        act = QHBoxLayout(); act.setSpacing(6); act.setContentsMargins(0, 0, 0, 0)
        act.addStretch()
        if info.readable:
            mine = info.labels_by(self._labeller) if self._labeller else info.labels_count
            needs_me = mine == 0
            btn = (ui.primary_button if needs_me else ui.secondary_button)(
                "Label" if needs_me else "Open", compact=True)
            btn.setMinimumWidth(68)
            btn.setToolTip("You have not labelled this recording yet." if needs_me
                           else f"You have placed {mine} label{'s' if mine != 1 else ''} here.")
            btn.clicked.connect(lambda _c=False, r=info.root: self.open_session.emit(r))
            act.addWidget(btn)
            row.clicked.connect(lambda r=info.root: self.open_session.emit(r))
        more = ui.icon_button("more", "More actions", size=34, bordered=False)
        more.clicked.connect(lambda _c=False, i=info, b=more: self._row_menu(i, b))
        act.addWidget(more)
        actw = QWidget(); actw.setLayout(act); actw.setFixedWidth(self._COL["actions"])
        h.addWidget(actw)
        return row

    def _row_menu(self, info, anchor):
        menu = QMenu(self)
        if info.readable:
            menu.addAction("Open and label", lambda: self.open_session.emit(info.root))
            menu.addAction("Export for analysis…", lambda: self.export_session.emit(info.root))
            menu.addSeparator()
        menu.addAction(platform_utils.show_in_file_manager_label(),
                       lambda: reveal_in_finder(info.root))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

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
        vs = (privacy.versus(s.boxer_a, s.boxer_b, self._hide_names)
              if (s.boxer_a or s.boxer_b or self._hide_names) else "no names")
        where = _fmt_mmss(s.stopped_sec) if s.stopped_sec is not None else "an early point"
        self._banner.set_text(
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
            import brain_battle.data.session_history as session_history
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
            b.setStyleSheet(dt.qss_chip(active) + " QPushButton { font-size: 13px; padding: 6px 12px; }")


__all__ = ["SessionsScreen", "filter_sort_sessions", "status_counts",
           "length_text", "labels_text", "reveal_in_finder"]
