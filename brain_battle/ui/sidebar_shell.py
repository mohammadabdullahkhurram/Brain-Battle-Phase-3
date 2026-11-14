"""
sidebar_shell.py
────────────────
The app's frame (docs/DESIGN.md "App structure"). Light-only; no theme toggle.

A 244 px sidebar, top to bottom:

  • the brand,
  • **New recording** — the one primary button, because starting a recording
    is the main thing people come here to do,
  • Sessions and Review & Label,
  • (space)
  • who is labelling on this computer ("Your name"; click to change it),
  • a quick **Hide names** switch (for presenting or sharing the screen),
  • the recordings-folder card: name, free space, whether it is a shared
    drive, and a clear warning when it cannot be reached,
  • Help and Settings.

A page can ask for the sidebar to collapse to a 72 px icon rail while it is
active (Review & Label does, to give the videos room) or to disappear (the
recording flow is full-screen). The app opens on the first page (Sessions).
"""
from __future__ import annotations

import os
import shutil

from PyQt6.QtCore    import Qt, pyqtSignal, QSize
from PyQt6.QtGui     import QIcon
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QStackedWidget,
    QSizePolicy, QFrame, QProgressBar,
)

import brain_battle.ui.design_tokens as dt

SIDEBAR_WIDTH   = 244
SIDEBAR_RAIL    = 72


def _human_gb(n_bytes: float) -> str:
    gb = n_bytes / (1024 ** 3)
    if gb >= 100:
        return f"{gb:.0f} GB"
    return f"{gb:.1f} GB"


class SidebarItem(QPushButton):
    def __init__(self, icon_name: str, label: str, primary: bool = False, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.label_text = label
        self.primary = primary
        self.setCheckable(True)
        self.setObjectName("sidebarPrimary" if primary else "sidebarItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(44)   # DESIGN: ≥44 px targets
        self._expanded = True
        self._apply_text()
        self._refresh_icon()

    def setExpanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._apply_text()
        self._refresh_icon()

    def _apply_text(self) -> None:
        # Escape '&' so Qt doesn't treat it as a mnemonic ("Review & Label").
        shown = self.label_text.replace("&", "&&")
        self.setText(f"  {shown}" if self._expanded else "")
        self.setToolTip("" if self._expanded else self.label_text)

    def _refresh_icon(self) -> None:
        if self.primary:
            color = "#FFFFFF"
        else:
            color = dt.accent() if self.isChecked() else dt.text_secondary()
        self.setIcon(QIcon(dt.icon_pixmap(self.icon_name, color=color, size=20)))
        self.setIconSize(QSize(20, 20))

    def nextCheckState(self):
        super().nextCheckState()
        self._refresh_icon()


class SidebarShell(QWidget):
    page_changed         = pyqtSignal(int)
    identity_clicked     = pyqtSignal()        # the "Your name" row → Settings
    names_hidden_changed = pyqtSignal(bool)    # the quick Hide names switch

    def __init__(self, capture_dir: str | None = None, parent=None):
        super().__init__(parent)
        self._capture_dir = capture_dir
        self._items: list[SidebarItem] = []
        self._collapse_flags: list[bool] = []
        self._hide_flags: list[bool] = []
        self._expanded = True
        self._labeller = ""
        self._names_hidden = False
        self._folder_ok = True
        self._build()

    # ── construction ──
    def _build(self) -> None:
        root = QHBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        self._sidebar = QWidget(); self._sidebar.setObjectName("sidebar")
        self._sidebar.setFixedWidth(SIDEBAR_WIDTH)
        self._sidebar.setStyleSheet(dt.qss_sidebar())
        sl = QVBoxLayout(self._sidebar); sl.setContentsMargins(0, 18, 0, 12); sl.setSpacing(2)

        # Brand
        brand = QWidget()
        bl = QHBoxLayout(brand); bl.setContentsMargins(24, 0, 16, 16); bl.setSpacing(10)
        self._brand_icon = QLabel()
        self._brand_icon.setPixmap(dt.icon_pixmap("brain", color=dt.accent(), size=24))
        self._brand_icon.setFixedSize(24, 24)
        bl.addWidget(self._brand_icon)
        self._brand_label = QLabel("Brain Battle")
        self._brand_label.setStyleSheet(dt.css_text(dt.SIZE_LG, 700))
        bl.addWidget(self._brand_label); bl.addStretch()
        sl.addWidget(brand)

        # The primary action, then the pages
        self._primary_nav = QVBoxLayout(); self._primary_nav.setContentsMargins(0, 0, 0, 0)
        sl.addLayout(self._primary_nav)
        self._nav = QVBoxLayout(); self._nav.setContentsMargins(0, 0, 0, 0); self._nav.setSpacing(2)
        sl.addLayout(self._nav)
        sl.addStretch()

        # Who is labelling + the quick privacy switch
        self._identity = QPushButton(); self._identity.setObjectName("sidebarItem")
        self._identity.setCursor(Qt.CursorShape.PointingHandCursor)
        self._identity.setMinimumHeight(44)
        self._identity.clicked.connect(self.identity_clicked.emit)
        sl.addWidget(self._identity)
        self._hide_btn = QPushButton(); self._hide_btn.setObjectName("sidebarItem")
        self._hide_btn.setCheckable(True)
        self._hide_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hide_btn.setMinimumHeight(44)
        self._hide_btn.clicked.connect(lambda checked: self.set_names_hidden(checked, emit=True))
        sl.addWidget(self._hide_btn)
        sl.addSpacing(6)

        # Recordings-folder card (name + free-space bar + status)
        self._folder_card = self._build_folder_card()
        sl.addWidget(self._folder_card)
        sl.addSpacing(6)

        # Help + Settings
        self._bottom_nav = QVBoxLayout(); self._bottom_nav.setContentsMargins(0, 0, 0, 0); self._bottom_nav.setSpacing(2)
        sl.addLayout(self._bottom_nav)

        root.addWidget(self._sidebar)
        self._stack = QStackedWidget()
        self._stack.setObjectName("pageStack")
        self._stack.setStyleSheet(f"#pageStack {{ background: {dt.canvas_bg()}; }}")
        root.addWidget(self._stack, 1)
        self._refresh_identity(); self._refresh_hide_button()

    # ── who is labelling / hide names ──
    def set_labeller(self, name: str) -> None:
        self._labeller = name or ""
        self._refresh_identity()

    def _refresh_identity(self) -> None:
        name = self._labeller
        self._identity.setIcon(QIcon(dt.icon_pixmap(
            "user", color=dt.text_secondary() if name else dt.warning_color(), size=20)))
        self._identity.setIconSize(QSize(20, 20))
        if self._expanded:
            self._identity.setText(f"  {name}" if name else "  Set your name")
            self._identity.setToolTip("Labels you place are saved under this name. Click to change it."
                                      if name else "Tell the app who is labelling on this computer.")
        else:
            self._identity.setText("")
            self._identity.setToolTip(f"Labelling as {name}" if name else "Set your name")

    def names_hidden(self) -> bool:
        return self._names_hidden

    def set_names_hidden(self, hidden: bool, emit: bool = False) -> None:
        self._names_hidden = bool(hidden)
        self._hide_btn.setChecked(self._names_hidden)
        self._refresh_hide_button()
        if emit:
            self.names_hidden_changed.emit(self._names_hidden)

    def _refresh_hide_button(self) -> None:
        hidden = self._names_hidden
        self._hide_btn.setIcon(QIcon(dt.icon_pixmap(
            "eye-off" if hidden else "eye",
            color=dt.accent() if hidden else dt.text_secondary(), size=20)))
        self._hide_btn.setIconSize(QSize(20, 20))
        text = "Names hidden" if hidden else "Hide names"
        self._hide_btn.setText(f"  {text}" if self._expanded else "")
        self._hide_btn.setToolTip(
            "Boxer names are shown as Boxer A / Boxer B on screen. Click to show them again."
            if hidden else
            "Show Boxer A / Boxer B instead of names on screen — for presenting or sharing your screen.")

    def _build_folder_card(self) -> QWidget:
        card = QFrame(); card.setObjectName("folderCard")
        self._folder_card = card
        v = QVBoxLayout(card); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(5)
        cap = QLabel("RECORDINGS FOLDER")
        cap.setStyleSheet(dt.css_text(dt.SIZE_CAPTION, 700, dt.text_tertiary(),
                                      "letter-spacing: 0.6px; border: none;"))
        v.addWidget(cap)
        self._folder_name = QLabel("—")
        self._folder_name.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, extra="border: none;"))
        v.addWidget(self._folder_name)
        # Free-space bar (colour = status)
        self._free_bar = QProgressBar(); self._free_bar.setFixedHeight(5)
        self._free_bar.setTextVisible(False)
        v.addWidget(self._free_bar)
        # "<n> GB free · <status text>"  (status in text, not colour only)
        self._free_label = QLabel("")
        self._free_label.setStyleSheet(dt.css_text(dt.SIZE_XS, 400, dt.text_tertiary(),
                                                   "border: none;"))
        self._free_label.setWordWrap(True)
        v.addWidget(self._free_label)
        self._style_folder_card(True)
        self.refresh_folder_card()
        return card

    def _style_folder_card(self, ok: bool) -> None:
        self._folder_ok = ok
        bg = dt.surface_subtle() if ok else dt._p().error_bg
        bd = dt.border() if ok else dt.error_color()
        self._folder_card.setStyleSheet(
            f"QFrame#folderCard {{ background: {bg}; border: 1px solid {bd}; "
            f"border-radius: {dt.RADIUS_MD}px; margin: 0 12px; }}")

    def _disk_status(self, free_bytes: int):
        """Classify free space against a default session's estimated size
        (at the currently-chosen video quality). Returns (key, status_text,
        bar_colour). green normally; amber when free < 3× a default session;
        red when free < 1×."""
        from brain_battle.data.session_storage import estimate_session_bytes, video_bytes_per_sec_for_mbps
        from brain_battle.capture.session_controller import SessionConfig
        import brain_battle.data.app_settings as app_settings
        vbps = video_bytes_per_sec_for_mbps(app_settings.load().video_mbps())
        est = estimate_session_bytes(SessionConfig(), n_cameras=4, n_eeg=2, video_bytes_per_sec=vbps)
        if free_bytes < est:
            return ("full", "almost full", dt.record_red())
        if free_bytes < 3 * est:
            return ("low", "getting low", dt.warning_color())
        return ("ok", "plenty of space", dt.success_color())

    def _set_bar_colour(self, colour: str) -> None:
        self._free_bar.setStyleSheet(
            f"QProgressBar {{ background: {dt.border()}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {colour}; border-radius: 2px; }}")

    def set_capture_dir(self, capture_dir: str) -> None:
        """Settings' [Change folder…] changed the active recordings folder —
        update the folder card without rebuilding the sidebar."""
        self._capture_dir = capture_dir
        self.refresh_folder_card()

    def folder_reachable(self) -> bool:
        return self._folder_ok

    def refresh_folder_card(self) -> None:
        """Show the capture folder's name + REAL free space + a plain-language
        status (colour AND text). A folder that cannot be reached (a shared
        drive that is not connected) says so in red."""
        d = self._capture_dir
        if d and os.path.isdir(d):
            self._folder_name.setText(os.path.basename(os.path.abspath(d)) or d)
            self._folder_card.setToolTip(d)
            try:
                u = shutil.disk_usage(d)
                used_frac = int(round((u.used / u.total) * 100)) if u.total else 0
                self._free_bar.setValue(max(0, min(100, used_frac)))
                _key, status_text, colour = self._disk_status(u.free)
                self._set_bar_colour(colour)
                self._free_label.setText(f"{_human_gb(u.free)} free · {status_text}")
            except OSError:
                self._set_bar_colour(dt.text_tertiary())
                self._free_bar.setValue(0)
                self._free_label.setText("free space unknown")
            self._style_folder_card(True)
        elif d:
            self._folder_name.setText(os.path.basename(os.path.abspath(d)) or d)
            self._folder_card.setToolTip(d)
            self._set_bar_colour(dt.error_color())
            self._free_bar.setValue(0)
            self._free_label.setText("✕ Can't be reached — is the drive connected?")
            self._style_folder_card(False)
        else:
            self._folder_name.setText("not set")
            self._set_bar_colour(dt.text_tertiary())
            self._free_bar.setValue(0)
            self._free_label.setText("no folder yet")
            self._style_folder_card(True)

    # ── pages ──
    def add_page(self, key: str, label: str, icon_name: str, widget: QWidget,
                 collapse_on_active: bool = False, bottom: bool = False,
                 hide_sidebar: bool = False, primary: bool = False) -> int:
        """Add a page and its sidebar entry. ``primary`` makes the entry the
        one filled button at the top (New recording)."""
        item = SidebarItem(icon_name, label, primary=primary)
        idx = len(self._items)
        item.clicked.connect(lambda _=False, i=idx: self.set_current_index(i))
        (self._primary_nav if primary else self._bottom_nav if bottom else self._nav).addWidget(item)
        self._items.append(item)
        self._collapse_flags.append(collapse_on_active)
        self._hide_flags.append(hide_sidebar)
        self._stack.addWidget(widget)
        if idx == 0:
            item.setChecked(True); item._refresh_icon()
        return idx

    def set_current_index(self, idx: int) -> None:
        if not (0 <= idx < len(self._items)):
            return
        for i, it in enumerate(self._items):
            it.setChecked(i == idx); it._refresh_icon()
        self._stack.setCurrentIndex(idx)
        # A full-screen page (the recording flow) hides the sidebar entirely;
        # otherwise collapse-to-rail or full sidebar per the page's flag.
        if self._hide_flags[idx]:
            self._sidebar.setVisible(False)
        else:
            self._sidebar.setVisible(True)
            self._set_collapsed(self._collapse_flags[idx])
        self.page_changed.emit(idx)

    def current_index(self) -> int:
        return self._stack.currentIndex()

    def page_widget(self, idx: int) -> QWidget:
        return self._stack.widget(idx)

    # ── collapse to the 72 px icon rail ──
    def _set_collapsed(self, collapsed: bool) -> None:
        expanded = not collapsed
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._sidebar.setFixedWidth(SIDEBAR_WIDTH if expanded else SIDEBAR_RAIL)
        self._brand_label.setVisible(expanded)
        self._folder_card.setVisible(expanded)
        for it in self._items:
            it.setExpanded(expanded)
        self._refresh_identity(); self._refresh_hide_button()


__all__ = ["SidebarShell", "SidebarItem", "SIDEBAR_WIDTH", "SIDEBAR_RAIL"]
