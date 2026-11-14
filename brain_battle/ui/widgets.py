"""
widgets.py — the small building blocks every screen shares
──────────────────────────────────────────────────────────
One place for the components of the design system (docs/DESIGN.md
"Components"), so a button, a card or a status line looks and behaves the same
on every screen:

  text      ``title`` · ``heading`` · ``body`` · ``muted`` · ``caption``
  surfaces  ``Card`` · ``Banner`` (a full-width message with actions)
  actions   ``primary_button`` · ``secondary_button`` · ``ghost_button`` ·
            ``danger_button`` · ``icon_button`` · ``help_button`` · ``Segmented``
  status    ``StatusLine`` (icon + text, never colour alone) · ``pill`` ·
            ``boxer_badge`` · ``boxer_chip`` · ``Dot``
  numbers   ``TimerLabel`` (hand-painted, equal-width digits that never jiggle)

Everything is light-mode only and styled from ``design_tokens``.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QRectF
from PyQt6.QtGui import QIcon, QPainter, QColor, QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QFrame, QHBoxLayout, QVBoxLayout, QSizePolicy,
    QButtonGroup,
)

import brain_battle.ui.design_tokens as dt

MIN_TARGET = 44          # DESIGN: minimum click target for a main action


# ── text ─────────────────────────────────────────────────────────────────────

def _label(text, size, weight, color, wrap=False) -> QLabel:
    l = QLabel(text)
    l.setWordWrap(wrap)
    l.setStyleSheet(dt.css_text(size, weight, color))
    return l


def title(text: str) -> QLabel:
    """A screen's title."""
    return _label(text, dt.SIZE_DISPLAY, 700, dt.text_primary())


def heading(text: str, size: int = dt.SIZE_LG) -> QLabel:
    """A card or section heading."""
    return _label(text, size, 700, dt.text_primary())


def body(text: str = "", color: str | None = None, size: int = dt.SIZE_MD,
         weight: int = 400, wrap: bool = True) -> QLabel:
    return _label(text, size, weight, color or dt.text_secondary(), wrap)


def muted(text: str = "", size: int = dt.SIZE_SM, wrap: bool = True) -> QLabel:
    return _label(text, size, 400, dt.text_tertiary(), wrap)


def caption(text: str) -> QLabel:
    """Small upper-case label above a group of controls."""
    l = QLabel(text.upper())
    l.setStyleSheet(dt.css_text(dt.SIZE_CAPTION, 700, dt.text_tertiary(),
                                "letter-spacing: 0.6px;"))
    return l


def set_text_style(label: QLabel, size=dt.SIZE_MD, weight=400, color=None) -> None:
    label.setStyleSheet(dt.css_text(size, weight, color or dt.text_secondary()))


# ── surfaces ─────────────────────────────────────────────────────────────────

class Card(QFrame):
    """A white rounded surface. ``tone`` tints it: None | 'ok' | 'warn' | 'bad' |
    'info' | 'subtle'."""
    _n = 0

    def __init__(self, tone: str | None = None, radius: int = dt.RADIUS_LG, parent=None):
        super().__init__(parent)
        Card._n += 1
        self.setObjectName(f"bbCard{Card._n}")      # scoped: never leaks to children
        self._radius = radius
        self.set_tone(tone)

    def set_tone(self, tone: str | None) -> None:
        if tone == "subtle":
            bg, bd = dt.surface_subtle(), dt.border()
        elif tone in ("ok", "warn", "bad", "info"):
            fg, bg, _text = dt.semantic(tone)
            bd = fg
        else:
            bg, bd = dt.card_bg(), dt.border()
        self.setStyleSheet(dt.qss_card(self.objectName(), self._radius, bg, bd))


def hline() -> QFrame:
    f = QFrame(); f.setFixedHeight(1)
    f.setObjectName("bbRule")
    f.setStyleSheet(f"#bbRule {{ background: {dt.border()}; }}")
    return f


# ── buttons ──────────────────────────────────────────────────────────────────

def _btn(text, qss, icon=None, icon_color="#FFFFFF", height=MIN_TARGET, tip=None) -> QPushButton:
    b = QPushButton(text)
    b.setMinimumHeight(height)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(qss)
    if icon:
        b.setIcon(QIcon(dt.icon_pixmap(icon, color=icon_color, size=18)))
        b.setIconSize(QSize(18, 18))
    if tip:
        b.setToolTip(tip)
    return b


def primary_button(text, icon=None, compact=False, tip=None) -> QPushButton:
    return _btn(text, dt.qss_primary_button(compact), icon, "#FFFFFF",
                34 if compact else MIN_TARGET, tip)


def secondary_button(text, icon=None, compact=False, tip=None) -> QPushButton:
    return _btn(text, dt.qss_secondary_button(compact), icon, dt.text_secondary(),
                34 if compact else MIN_TARGET, tip)


def ghost_button(text, icon=None, compact=False, tip=None) -> QPushButton:
    return _btn(text, dt.qss_ghost_button(compact), icon, dt.text_secondary(),
                34 if compact else MIN_TARGET, tip)


def danger_button(text, icon=None, compact=False, tip=None) -> QPushButton:
    return _btn(text, dt.qss_danger_button(compact), icon, "#FFFFFF",
                34 if compact else MIN_TARGET, tip)


def icon_button(icon: str, tip: str, size: int = 36, color: str | None = None,
                bordered: bool = True) -> QPushButton:
    """A square icon-only button. ``tip`` is also its accessible name."""
    b = QPushButton()
    b.setIcon(QIcon(dt.icon_pixmap(icon, color=color or dt.text_secondary(), size=18)))
    b.setIconSize(QSize(18, 18))
    b.setFixedSize(size, size)
    b.setToolTip(tip); b.setAccessibleName(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet((dt.qss_secondary_button(True) if bordered else dt.qss_ghost_button(True))
                    + " QPushButton { padding: 0; }")
    return b


def help_button() -> QPushButton:
    return icon_button("help-circle", "Help", color=dt.text_tertiary())


class Segmented(QWidget):
    """A row of mutually exclusive choices: ``Segmented([("0.5×", 0.5), …])``.
    Emits ``changed(value)``."""
    changed = pyqtSignal(object)

    def __init__(self, options, current=None, height: int = 32, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(4)
        self._group = QButtonGroup(self); self._group.setExclusive(True)
        self._buttons = {}
        for text, value in options:
            b = QPushButton(text); b.setCheckable(True); b.setMinimumHeight(height)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, v=value: self.set_value(v, emit=True))
            self._group.addButton(b); h.addWidget(b)
            self._buttons[value] = b
        self._value = None
        self.set_value(current if current in self._buttons else options[0][1])
        for b in self._buttons.values():        # never squeeze a choice's text
            b.setMinimumWidth(b.sizeHint().width())

    def value(self):
        return self._value

    def buttons(self):
        return list(self._buttons.values())

    def button(self, value) -> QPushButton:
        return self._buttons[value]

    def set_value(self, value, emit: bool = False) -> None:
        if value not in self._buttons:
            return
        self._value = value
        for v, b in self._buttons.items():
            b.setChecked(v == value)
            b.setStyleSheet(dt.qss_chip(v == value))
        if emit:
            self.changed.emit(value)


# ── status ───────────────────────────────────────────────────────────────────

_STATUS_ICON = {"ok": "check-circle", "warn": "alert", "bad": "x-circle",
                "info": "help-circle", "idle": "circle"}


class StatusLine(QWidget):
    """Icon + text for a state (ok / warn / bad / info / idle). Each state has
    its own icon SHAPE (tick in a circle, warning triangle, cross in a circle),
    so the state never rests on colour alone."""

    def __init__(self, text: str = "", kind: str = "idle", size: int = dt.SIZE_SM,
                 weight: int = 500, wrap: bool = True, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
        self._icon = QLabel(); self._icon.setFixedSize(18, 18)
        self._text = QLabel(); self._text.setWordWrap(wrap)
        self._size, self._weight = size, weight
        h.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        h.addWidget(self._text, 1)
        self._kind = None
        self.set(text, kind)

    def set(self, text: str, kind: str = "idle") -> None:
        fg, _bg, on_soft = dt.semantic(kind)
        if kind != self._kind:
            self._kind = kind
            self._icon.setPixmap(dt.icon_pixmap(_STATUS_ICON.get(kind, "circle"), color=fg, size=18))
            self._text.setStyleSheet(dt.css_text(
                self._size, self._weight, on_soft if kind != "idle" else dt.text_secondary()))
        self._icon.setVisible(bool(text))
        self._text.setText(text)

    def text(self) -> str:
        return self._text.text()

    def kind(self) -> str:
        return self._kind or "idle"


def pill(text: str, kind: str = "idle") -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(dt.qss_status_pill(kind))
    l.setAlignment(Qt.AlignmentFlag.AlignCenter)
    l.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return l


def boxer_badge(which: str, size: int = 24) -> QLabel:
    """The round A / B letter in the boxer's colour."""
    line, soft, text = dt.boxer_colors(which)
    b = QLabel(which); b.setFixedSize(size, size)
    b.setAlignment(Qt.AlignmentFlag.AlignCenter)
    b.setStyleSheet(f"background: {soft}; color: {text}; border: 1.5px solid {line}; "
                    f"border-radius: {size // 2}px; font-family: {dt.FONT_UI}; "
                    f"font-weight: 700; font-size: {max(10, size // 2)}px;")
    return b


def boxer_chip(which: str, name: str) -> QLabel:
    """'A · Sam' in the boxer's colour (letter + name, never colour alone)."""
    line, soft, text = dt.boxer_colors(which)
    l = QLabel(f"{which} · {name}")
    l.setStyleSheet(f"background: {soft}; color: {text}; border: 1px solid {line}; "
                    f"border-radius: 11px; padding: 3px 10px; font-family: {dt.FONT_UI}; "
                    f"font-size: {dt.SIZE_XS}px; font-weight: 600;")
    l.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return l


class Banner(Card):
    """A full-width message: what happened and what to do, with its actions on
    the right. ``tone``: 'warn' | 'bad' | 'info' | 'ok'."""

    def __init__(self, tone: str = "warn", parent=None):
        super().__init__(tone=tone, radius=dt.RADIUS_MD, parent=parent)
        self._tone = tone
        h = QHBoxLayout(self); h.setContentsMargins(16, 12, 12, 12); h.setSpacing(12)
        self._icon = QLabel(); self._icon.setFixedSize(20, 20)
        h.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        self._text = QLabel(""); self._text.setWordWrap(True)
        h.addWidget(self._text, 1)
        self._actions = QHBoxLayout(); self._actions.setSpacing(8)
        h.addLayout(self._actions)
        self.set_tone_and_text(tone, "")

    def set_tone_and_text(self, tone: str, text: str) -> None:
        self._tone = tone
        self.set_tone(tone)
        fg, _bg, on_soft = dt.semantic(tone)
        self._icon.setPixmap(dt.icon_pixmap(_STATUS_ICON.get(tone, "alert"), color=fg, size=20))
        self._text.setStyleSheet(dt.css_text(dt.SIZE_SM, 500, on_soft))
        self._text.setText(text)

    def set_text(self, text: str) -> None:
        self._text.setText(text)

    def text(self) -> str:
        return self._text.text()

    def add_action(self, text: str, slot, primary: bool = False) -> QPushButton:
        b = (primary_button if primary else secondary_button)(text, compact=True)
        b.clicked.connect(slot)
        self._actions.addWidget(b)
        return b


# ── numbers ──────────────────────────────────────────────────────────────────

class TimerLabel(QWidget):
    """Text painted with equal-width digits (Inter 'tnum'), scaled down to fit
    its width. For the countdown and the playback clock, where a proportional
    font would make the digits shift as they change."""

    def __init__(self, px: int = 16, weight=QFont.Weight.DemiBold, color: str | None = None,
                 align=Qt.AlignmentFlag.AlignLeft, parent=None):
        super().__init__(parent)
        self._text = ""
        self._px = px
        self._weight = weight
        self._color = color or dt.text_primary()
        self._align = align
        self._template = None
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._apply_size()

    def _font(self, px=None) -> QFont:
        return dt.number_font(px or self._px, self._weight)

    def _apply_size(self):
        fm = QFontMetrics(self._font())
        self.setFixedHeight(int(fm.height() * 0.98))
        self.setMinimumWidth(min(160, fm.horizontalAdvance(self._template or self._text or "0:00") + 2))

    def set_template(self, text: str) -> None:
        """Reserve the width of the widest text this label will show."""
        self._template = text
        fm = QFontMetrics(self._font())
        self.setMinimumWidth(fm.horizontalAdvance(text) + 2)

    def setText(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.update()

    def text(self) -> str:
        return self._text

    def set_color(self, color: str) -> None:
        if color != self._color:
            self._color = color; self.update()

    def set_pixel_size(self, px: int) -> None:
        if px != self._px:
            self._px = px; self._apply_size(); self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            px = self._px
            font = self._font(px)
            w = QFontMetrics(font).horizontalAdvance(self._text)
            if w > self.width() > 0:                 # shrink to fit, never clip
                px = max(10, int(px * self.width() / w))
                font = self._font(px)
            p.setFont(font); p.setPen(QColor(self._color))
            p.drawText(QRectF(self.rect()), int(self._align | Qt.AlignmentFlag.AlignVCenter),
                       self._text)
        finally:
            p.end()


class Dot(QWidget):
    """A small filled circle (a colour swatch or a state dot)."""

    def __init__(self, color: str, size: int = 10, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(size, size)

    def set_color(self, color: str) -> None:
        self._color = color; self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(self._color))
            p.drawEllipse(self.rect())
        finally:
            p.end()


def clear_layout(layout, keep_last: int = 0) -> None:
    """Remove (and delete) every widget in a layout, optionally keeping the last
    ``keep_last`` items (e.g. a trailing stretch)."""
    while layout.count() > keep_last:
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None); w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


__all__ = ["title", "heading", "body", "muted", "caption", "set_text_style", "Card", "hline",
           "primary_button", "secondary_button", "ghost_button", "danger_button",
           "icon_button", "help_button", "Segmented", "StatusLine", "pill", "boxer_badge",
           "boxer_chip", "Banner", "TimerLabel", "Dot", "clear_layout", "MIN_TARGET"]
