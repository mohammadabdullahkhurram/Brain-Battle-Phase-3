"""
header_shell.py
───────────────
The app's frame (docs/DESIGN.md "App structure", design F): a white **top
header** — no sidebar — and the screen underneath. Light-only.

    [〜 Brain Battle]  Sessions            ·· extra ··   Documentation   [⚙]

  • the logo (back to Sessions), **Sessions**, then on the right
    **Documentation** and the **Settings** gear;
  • a screen can put something of its own in the header (``set_extra``): the
    documentation search box, "Changes save automatically" on Settings;
  • a full-screen page (New recording, Review & Label) has its own header
    and hides this one (``own_header=True``).

The app opens on the first page (Sessions). There is no "Your name" row and no
folder card any more: the name lives in Settings (and is asked the first time
someone labels); the recordings folder is shown at the foot of Sessions and in
Settings.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QStackedWidget, QFrame,
)

import brain_battle.ui.design_tokens as dt

HEADER_HEIGHT = 66


def _nav_qss(active: bool) -> str:
    if active:
        return (f"QPushButton {{ background: {dt.accent_ghost()}; color: {dt.accent_hover()}; "
                f"border: 0; border-radius: 8px; padding: 8px 14px; font-family: {dt.FONT_UI}; "
                f"font-size: {dt.SIZE_MD}px; font-weight: 600; }}")
    return (f"QPushButton {{ background: transparent; color: #475467; border: 0; "
            f"border-radius: 8px; padding: 8px 14px; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_MD}px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {dt.card_bg_hover()}; }}")


class HeaderShell(QWidget):
    page_changed = pyqtSignal(int)

    def __init__(self, capture_dir: str = "", parent=None):
        super().__init__(parent)
        self._capture_dir = capture_dir
        self._pages = []            # (key, widget, own_header)
        self._nav = {}              # key → nav button
        self.setObjectName("headerShell")
        self.setStyleSheet(f"#headerShell {{ background: {dt.canvas_bg()}; }}")
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)

        self._header = QFrame(); self._header.setObjectName("appHeader")
        self._header.setFixedHeight(HEADER_HEIGHT)
        self._header.setStyleSheet(f"#appHeader {{ background: {dt.surface()}; "
                                   f"border-bottom: 1px solid {dt.border()}; }}")
        h = QHBoxLayout(self._header); h.setContentsMargins(24, 10, 24, 10); h.setSpacing(16)
        self._logo = QPushButton("  Brain Battle")
        self._logo.setObjectName("logo")
        self._logo.setIcon(QIcon(dt.icon_pixmap("wave", color=dt.accent(), size=24)))
        self._logo.setIconSize(QSize(24, 24))
        self._logo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._logo.setStyleSheet(f"#logo {{ background: transparent; border: 0; "
                                 f"font-family: {dt.FONT_UI}; font-size: 17px; font-weight: 700; "
                                 f"color: {dt.text_primary()}; }}")
        self._logo.clicked.connect(lambda: self.set_current_index(0))
        h.addWidget(self._logo)
        self._left = QHBoxLayout(); self._left.setSpacing(4)
        h.addLayout(self._left)
        h.addStretch(1)
        self._extra_box = QHBoxLayout(); self._extra_box.setSpacing(8)
        h.addLayout(self._extra_box)
        self._right = QHBoxLayout(); self._right.setSpacing(4)
        h.addLayout(self._right)
        v.addWidget(self._header)

        self._stack = QStackedWidget()
        v.addWidget(self._stack, 1)
        self._extras = {}           # page index → widget shown in the header

    # ── pages ──
    def add_page(self, key: str, label: str, widget: QWidget, nav: str = "",
                 own_header: bool = False) -> int:
        """Add a screen. ``nav``: "left" (next to the logo), "right" (before
        the gear), "gear" (the Settings gear) or "" (no header button).
        ``own_header``: the page has its own header; hide the app's."""
        idx = self._stack.addWidget(widget)
        self._pages.append((key, widget, own_header))
        if nav:
            if nav == "gear":
                b = QPushButton()
                b.setIcon(QIcon(dt.icon_pixmap("settings", color="#475467", size=21)))
                b.setIconSize(QSize(21, 21)); b.setFixedSize(42, 42)
                b.setToolTip(label); b.setAccessibleName(label)
            else:
                b = QPushButton(label)
                b.setMinimumHeight(38)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setObjectName(f"nav_{key}")
            b.clicked.connect(lambda _c=False, i=idx: self.set_current_index(i))
            (self._left if nav == "left" else self._right).addWidget(b)
            self._nav[key] = b
        self._restyle_nav()
        return idx

    def set_extra(self, idx: int, widget: QWidget) -> None:
        """Something of a page's own in the header while it is shown."""
        self._extras[idx] = widget
        widget.setParent(self._header)
        self._extra_box.addWidget(widget)
        widget.setVisible(self._stack.currentIndex() == idx)

    def set_current_index(self, idx: int) -> None:
        if not 0 <= idx < len(self._pages):
            return
        self._stack.setCurrentIndex(idx)
        self._header.setVisible(not self._pages[idx][2])
        for i, w in self._extras.items():
            w.setVisible(i == idx)
        self._restyle_nav()
        self.page_changed.emit(idx)

    def current_index(self) -> int:
        return self._stack.currentIndex()

    def page_widget(self, idx: int) -> QWidget:
        return self._stack.widget(idx)

    def _restyle_nav(self) -> None:
        cur = self._stack.currentIndex()
        cur_key = self._pages[cur][0] if 0 <= cur < len(self._pages) else ""
        for key, b in self._nav.items():
            active = key == cur_key
            if b.text():
                b.setStyleSheet(_nav_qss(active))
            else:
                bg = dt.accent_ghost() if active else "transparent"
                col = dt.accent_hover() if active else "#475467"
                b.setIcon(QIcon(dt.icon_pixmap("settings", color=col, size=21)))
                b.setStyleSheet(f"QPushButton {{ background: {bg}; border: 0; border-radius: 10px; }}"
                                f"QPushButton:hover {{ background: {dt.card_bg_hover()}; }}")

    # ── recordings folder (shown by Sessions / Settings) ──
    def set_capture_dir(self, capture_dir: str) -> None:
        self._capture_dir = capture_dir

    def capture_dir(self) -> str:
        return self._capture_dir


__all__ = ["HeaderShell", "HEADER_HEIGHT"]
