"""
sidebar_shell.py
────────────────
Left-sidebar navigation shell (docs/DESIGN.md "App structure"). Light-only; no
theme toggle.

Layout: a 232 px sidebar — logo, the nav items (Sessions, New recording,
Review & Label), then at the bottom a recordings-folder card (name + free-space
bar + "N GB free") and Settings. A page can request that the sidebar collapse
to a 72 px icon rail while it's active (Review & Label does, to give the
player room). The app opens on the first page (Sessions).
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

import design_tokens as dt

SIDEBAR_WIDTH   = 232
SIDEBAR_RAIL    = 72


def _human_gb(n_bytes: float) -> str:
    gb = n_bytes / (1024 ** 3)
    if gb >= 100:
        return f"{gb:.0f} GB"
    return f"{gb:.1f} GB"


class SidebarItem(QPushButton):
    def __init__(self, icon_name: str, label: str, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.label_text = label
        self.setCheckable(True)
        self.setObjectName("sidebarItem")
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
        self.setText(f"   {shown}" if self._expanded else "")
        self.setToolTip("" if self._expanded else self.label_text)

    def _refresh_icon(self) -> None:
        color = dt.accent() if self.isChecked() else dt.text_secondary()
        self.setIcon(QIcon(dt.icon_pixmap(self.icon_name, color=color, size=22)))
        self.setIconSize(QSize(22, 22))

    def nextCheckState(self):
        super().nextCheckState()
        self._refresh_icon()


class SidebarShell(QWidget):
    page_changed = pyqtSignal(int)

    def __init__(self, capture_dir: str | None = None, parent=None):
        super().__init__(parent)
        self._capture_dir = capture_dir
        self._items: list[SidebarItem] = []
        self._collapse_flags: list[bool] = []
        self._expanded = True
        self._build()

    # ── construction ──
    def _build(self) -> None:
        root = QHBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        self._sidebar = QWidget(); self._sidebar.setObjectName("sidebar")
        self._sidebar.setFixedWidth(SIDEBAR_WIDTH)
        self._sidebar.setStyleSheet(dt.qss_sidebar())
        sl = QVBoxLayout(self._sidebar); sl.setContentsMargins(0, 18, 0, 12); sl.setSpacing(4)

        # Brand
        brand = QWidget()
        bl = QHBoxLayout(brand); bl.setContentsMargins(20, 0, 20, 14); bl.setSpacing(10)
        self._brand_icon = QLabel()
        self._brand_icon.setPixmap(dt.icon_pixmap("brain", color=dt.accent(), size=22))
        self._brand_icon.setFixedSize(22, 22)
        bl.addWidget(self._brand_icon)
        self._brand_label = QLabel("Brain Battle")
        self._brand_label.setStyleSheet(
            f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_LG}px; font-weight: 700;")
        bl.addWidget(self._brand_label); bl.addStretch()
        sl.addWidget(brand)

        sep = QFrame(); sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {dt.border()}; margin: 0 16px;")
        sl.addWidget(sep); sl.addSpacing(8)

        # Primary nav items container
        self._nav = QVBoxLayout(); self._nav.setContentsMargins(0, 0, 0, 0); self._nav.setSpacing(2)
        sl.addLayout(self._nav)
        sl.addStretch()

        # Recordings-folder card (name + free-space bar + "N GB free")
        self._folder_card = self._build_folder_card()
        sl.addWidget(self._folder_card)

        # Settings + extra bottom items container
        self._bottom_nav = QVBoxLayout(); self._bottom_nav.setContentsMargins(0, 0, 0, 0); self._bottom_nav.setSpacing(2)
        sl.addLayout(self._bottom_nav)

        root.addWidget(self._sidebar)
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"QStackedWidget {{ background: {dt.canvas_bg()}; }}")
        root.addWidget(self._stack, 1)

    def _build_folder_card(self) -> QWidget:
        card = QFrame(); card.setObjectName("folderCard")
        card.setStyleSheet(
            f"QFrame#folderCard {{ background: {dt.card_bg_hover()}; "
            f"border: 1px solid {dt.border()}; border-radius: {dt.RADIUS_MD}px; "
            f"margin: 0 10px; }}")
        v = QVBoxLayout(card); v.setContentsMargins(12, 10, 12, 10); v.setSpacing(5)
        # Caption
        cap = QLabel("Recordings folder")
        cap.setStyleSheet(
            f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_CAPTION}px; font-weight: 600; border: none;")
        v.addWidget(cap)
        # Folder name
        self._folder_name = QLabel("—")
        self._folder_name.setStyleSheet(
            f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_SM}px; font-weight: 600; border: none;")
        v.addWidget(self._folder_name)
        # Free-space bar (colour = status)
        self._free_bar = QProgressBar(); self._free_bar.setFixedHeight(5)
        self._free_bar.setTextVisible(False)
        v.addWidget(self._free_bar)
        # "<n> GB free · <status text>"  (status in text, not colour only)
        self._free_label = QLabel("")
        self._free_label.setStyleSheet(
            f"color: {dt.text_tertiary()}; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_CAPTION}px; border: none;")
        self._free_label.setWordWrap(True)
        v.addWidget(self._free_label)
        self.refresh_folder_card()
        return card

    def _disk_status(self, free_bytes: int):
        """Classify free space against a default session's estimated size.
        Returns (key, status_text, bar_colour). green normally; amber when
        free < 3× a default session; red when free < 1×."""
        from session_storage import estimate_session_bytes
        from session_controller import SessionConfig
        est = estimate_session_bytes(SessionConfig(), n_cameras=4, n_eeg=2)
        if free_bytes < est:
            return ("full", "almost full", dt.record_red())
        if free_bytes < 3 * est:
            return ("low", "getting low", dt.warning_color())
        return ("ok", "plenty of space", dt.success_color())

    def _set_bar_colour(self, colour: str) -> None:
        self._free_bar.setStyleSheet(
            f"QProgressBar {{ background: {dt.border()}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {colour}; border-radius: 2px; }}")

    def refresh_folder_card(self) -> None:
        """Show the capture folder's name + REAL free space + a plain-language
        status (colour AND text)."""
        d = self._capture_dir
        if d and os.path.isdir(d):
            self._folder_name.setText(os.path.basename(os.path.abspath(d)) or d)
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
        else:
            self._folder_name.setText("not set")
            self._set_bar_colour(dt.text_tertiary())
            self._free_bar.setValue(0)
            self._free_label.setText("no folder yet")

    # ── pages ──
    def add_page(self, key: str, label: str, icon_name: str, widget: QWidget,
                 collapse_on_active: bool = False, bottom: bool = False) -> int:
        item = SidebarItem(icon_name, label)
        idx = len(self._items)
        item.clicked.connect(lambda _=False, i=idx: self.set_current_index(i))
        (self._bottom_nav if bottom else self._nav).addWidget(item)
        self._items.append(item)
        self._collapse_flags.append(collapse_on_active)
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


__all__ = ["SidebarShell", "SidebarItem", "SIDEBAR_WIDTH", "SIDEBAR_RAIL"]
