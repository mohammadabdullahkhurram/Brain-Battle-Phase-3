"""
sidebar_shell.py
────────────────
Left-sidebar navigation shell, theme-aware.

What's new in v4:
  · Theme toggle (sun/moon) at the bottom of the sidebar
  · 28 px icons with more breathing room between items
  · Re-styles on theme change via dt.on_theme_change()
  · Quieter brand block — smaller, less visually dominant
"""

from __future__ import annotations

from PyQt6.QtCore    import Qt, QPropertyAnimation, pyqtSignal, QSize
from PyQt6.QtGui     import QIcon
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QStackedWidget,
    QSizePolicy, QFrame,
)

import design_tokens as dt


class SidebarItem(QPushButton):
    def __init__(self, icon_name: str, label: str, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.label_text = label
        self.setCheckable(True)
        self.setObjectName("sidebarItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(46)
        self._expanded = True
        self._apply_text()
        self._refresh_icon()

    def setExpanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._apply_text()
        self._refresh_icon()

    def _apply_text(self) -> None:
        self.setText(f"    {self.label_text}" if self._expanded else "")

    def _refresh_icon(self) -> None:
        color = dt.accent() if self.isChecked() else dt.text_secondary()
        pm = dt.icon_pixmap(self.icon_name, color=color, size=22)
        self.setIcon(QIcon(pm))
        self.setIconSize(QSize(22, 22))

    def nextCheckState(self):
        super().nextCheckState()
        self._refresh_icon()


class SidebarShell(QWidget):
    page_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[SidebarItem] = []
        self._expanded = True
        self._build()
        dt.on_theme_change(self._restyle)

    def _build(self) -> None:
        root = QHBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        self._sidebar = QWidget(); self._sidebar.setObjectName("sidebar")
        self._sidebar.setFixedWidth(220)
        sl = QVBoxLayout(self._sidebar); sl.setContentsMargins(0, 20, 0, 12); sl.setSpacing(4)

        # Brand row
        brand = QWidget()
        bl = QHBoxLayout(brand); bl.setContentsMargins(20, 0, 20, 16); bl.setSpacing(10)
        self._brand_icon = QLabel(); self._brand_icon.setFixedSize(20, 20)
        bl.addWidget(self._brand_icon)
        self._brand_label = QLabel("Brain Battle")
        bl.addWidget(self._brand_label); bl.addStretch()
        sl.addWidget(brand)

        self._sep_top = QFrame(); self._sep_top.setFixedHeight(1)
        sl.addWidget(self._sep_top); sl.addSpacing(10)

        # Nav items container
        self._nav_container = QVBoxLayout()
        self._nav_container.setContentsMargins(0, 0, 0, 0); self._nav_container.setSpacing(2)
        sl.addLayout(self._nav_container)
        sl.addStretch()

        # Footer: theme toggle
        self._theme_btn = QPushButton()
        self._theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_btn.setFixedHeight(40)
        self._theme_btn.clicked.connect(self._toggle_theme)
        sl.addWidget(self._theme_btn)

        # Collapse toggle below it
        self._collapse_btn = QPushButton()
        self._collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse_btn.setFixedHeight(36)
        self._collapse_btn.clicked.connect(self.toggle)
        sl.addWidget(self._collapse_btn)

        root.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._restyle()

    def _restyle(self) -> None:
        def _repolish(w):
            s = w.style(); s.unpolish(w); s.polish(w); w.update()

        # Sidebar background — use inline style on the widget itself
        # (direct selector) to avoid objectName cascade issues on theme switch.
        if dt.current_theme() == "dark":
            self._sidebar.setStyleSheet(
                f"QWidget {{ background: qlineargradient("
                f"x1:0, y1:0, x2:0, y2:1, "
                f"stop:0 rgba(21, 21, 25, 0.98), "
                f"stop:1 rgba(17, 17, 20, 0.98)); "
                f"border-right: 1px solid {dt.border()}; }}"
                + self._sidebar_item_qss()
            )
        else:
            self._sidebar.setStyleSheet(
                f"QWidget {{ background: {dt.sidebar_bg()}; "
                f"border-right: 1px solid {dt.border()}; }}"
                + self._sidebar_item_qss()
            )
        _repolish(self._sidebar)
        self._stack.setStyleSheet(f"QStackedWidget {{ background: {dt.canvas_bg()}; }}")
        _repolish(self._stack)
        self._brand_icon.setPixmap(dt.icon_pixmap("brain", color=dt.accent(), size=20))
        self._brand_label.setStyleSheet(
            f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_MD}px; font-weight: 700; letter-spacing: {dt.LS_TIGHT};"
        )
        self._sep_top.setStyleSheet(f"background: {dt.border()}; margin: 0 16px;")
        for item in self._items:
            _repolish(item)
            item._refresh_icon()
        self._refresh_theme_btn()
        self._refresh_collapse_btn()

    def _sidebar_item_qss(self) -> str:
        return f"""
            QPushButton#sidebarItem {{
                background: transparent; border: none;
                color: {dt.text_secondary()};
                padding: 13px 18px; text-align: left;
                font-family: {dt.FONT_UI}; font-size: {dt.SIZE_MD}px;
                font-weight: 500; border-radius: {dt.RADIUS_MD}px;
                margin: 2px 10px;
            }}
            QPushButton#sidebarItem:hover {{
                background: {dt.card_bg_hover()};
                color: {dt.text_primary()};
            }}
            QPushButton#sidebarItem:checked {{
                background: {dt.accent_ghost()};
                color: {dt.accent()};
                font-weight: 600;
            }}
        """

    def _refresh_theme_btn(self) -> None:
        is_dark = dt.current_theme() == "dark"
        icon_name = "sun" if is_dark else "moon"
        label = "Light mode" if is_dark else "Dark mode"
        pm = dt.icon_pixmap(icon_name, color=dt.text_secondary(), size=18)
        self._theme_btn.setIcon(QIcon(pm))
        self._theme_btn.setIconSize(QSize(18, 18))
        self._theme_btn.setText(f"  {label}" if self._expanded else "")
        self._theme_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; "
            f"color: {dt.text_secondary()}; "
            f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; "
            f"padding: 8px 22px; text-align: left; margin: 0 8px; "
            f"border-radius: {dt.RADIUS_MD}px; }}"
            f"QPushButton:hover {{ background: {dt.card_bg_hover()}; "
            f"color: {dt.text_primary()}; }}"
        )

    def _refresh_collapse_btn(self) -> None:
        self._collapse_btn.setText(
            "    ◀  Collapse" if self._expanded else "    ▶"
        )
        self._collapse_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; "
            f"color: {dt.text_tertiary()}; "
            f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_XS}px; "
            f"padding: 8px 22px; text-align: left; margin: 0 8px; "
            f"border-radius: {dt.RADIUS_MD}px; }}"
            f"QPushButton:hover {{ background: {dt.card_bg_hover()}; "
            f"color: {dt.text_secondary()}; }}"
        )

    def _toggle_theme(self) -> None:
        """Persist the new theme choice and ask for a restart.

        Qt's stylesheet engine doesn't reliably re-evaluate every
        objectName-based selector on live theme switch, so we take the
        same approach as VS Code / Slack / Discord: save the preference
        and ask the user to relaunch. The next launch reads the saved
        theme and applies it cleanly from the start.
        """
        from PyQt6.QtWidgets import QMessageBox
        import json, os
        new_theme = "light" if dt.current_theme() == "dark" else "dark"

        # Persist
        pref_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".brain_battle_prefs.json"
        )
        try:
            with open(pref_path, "w") as f:
                json.dump({"theme": new_theme}, f)
        except Exception:
            pass

        msg = QMessageBox(self)
        msg.setWindowTitle("Theme saved")
        msg.setText(
            f"Switched to {new_theme.title()} Mode.\n\n"
            "Please restart Brain Battle to apply the new theme."
        )
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    def add_page(self, icon_name: str, label: str, widget: QWidget) -> int:
        item = SidebarItem(icon_name, label)
        idx = len(self._items)
        item.clicked.connect(lambda: self.set_current_index(idx))
        self._nav_container.addWidget(item)
        self._items.append(item)
        self._stack.addWidget(widget)
        if idx == 0:
            item.setChecked(True)
            item._refresh_icon()
        return idx

    def set_current_index(self, idx: int) -> None:
        for i, it in enumerate(self._items):
            it.setChecked(i == idx)
            it._refresh_icon()
        self._stack.setCurrentIndex(idx)
        self.page_changed.emit(idx)

    def current_index(self) -> int:
        return self._stack.currentIndex()

    def toggle(self) -> None:
        self._expanded = not self._expanded
        target_w = 220 if self._expanded else 68
        anim = QPropertyAnimation(self._sidebar, b"minimumWidth", self)
        anim.setStartValue(self._sidebar.width()); anim.setEndValue(target_w)
        anim.setDuration(dt.MOTION_NORMAL); anim.setEasingCurve(dt.EASE_EMPHASIS)
        anim2 = QPropertyAnimation(self._sidebar, b"maximumWidth", self)
        anim2.setStartValue(self._sidebar.width()); anim2.setEndValue(target_w)
        anim2.setDuration(dt.MOTION_NORMAL); anim2.setEasingCurve(dt.EASE_EMPHASIS)
        anim.start(); anim2.start()
        self._sidebar.setFixedWidth(target_w)
        self._brand_label.setVisible(self._expanded)
        for item in self._items: item.setExpanded(self._expanded)
        self._refresh_theme_btn(); self._refresh_collapse_btn()


__all__ = ["SidebarShell", "SidebarItem"]
