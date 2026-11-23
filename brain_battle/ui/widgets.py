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
  dialogs   ``AppDialog`` · ``ask`` · ``tell`` — the ONE in-app dialog (white
            rounded card on a dimmed cover, buttons right; ``warn`` / ``danger``
            variants). No native alert or message box is used anywhere.
  inputs    ``Dropdown`` — the ONE app-styled dropdown (light, rounded, Inter,
            blue focus); never a bare ``QComboBox``.

Everything is light-mode only and styled from ``design_tokens``.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QRectF, QRect, QPoint, QPointF
from PyQt6.QtGui import QIcon, QPainter, QColor, QFont, QFontMetrics, QPen
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QFrame, QHBoxLayout, QVBoxLayout, QSizePolicy,
    QButtonGroup, QLayout, QDialog, QComboBox, QListView,
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


class WrapLabel(QLabel):
    """A word-wrapped label that is never squeezed shorter than its text needs
    (Qt layouts do not always honour a wrapped label's height for its width,
    which cut off the end of the poor-signal help on Get ready)."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)

    def _fit(self):
        if self.width() > 0:
            need = self.heightForWidth(self.width())
            if need > 0 and need != self.minimumHeight():
                self.setMinimumHeight(need)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def setText(self, text):                          # noqa: N802
        super().setText(text)
        self._fit()


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


def fighter_badge(which: str, colour_hex: str | None, size: int = 22) -> QLabel:
    """Design F: the square A / B badge filled with the colour the fighter
    wears (white letter). Falls back to the A/B accent when the colour is
    unknown (an old recording)."""
    bg = colour_hex or dt.boxer_colors(which)[0]
    b = QLabel(which); b.setFixedSize(size, size)
    b.setAlignment(Qt.AlignmentFlag.AlignCenter)
    b.setObjectName(f"fBadge{which}")
    b.setStyleSheet(f"#fBadge{which} {{ background: {bg}; color: #FFFFFF; "
                    f"border-radius: {max(4, size // 4)}px; font-family: {dt.FONT_UI}; "
                    f"font-weight: 700; font-size: {max(10, size // 2)}px; }}")
    return b


def neutral_badge(which: str, size: int = 22) -> QLabel:
    """The square A / B badge in neutral grey (where no fighter colour applies,
    e.g. Settings › headbands)."""
    b = QLabel(which); b.setFixedSize(size, size)
    b.setAlignment(Qt.AlignmentFlag.AlignCenter)
    b.setObjectName(f"nBadge{which}")
    b.setStyleSheet(f"#nBadge{which} {{ background: {dt.card_bg_hover()}; "
                    f"color: {dt.text_secondary()}; border: 1px solid {dt.border_strong()}; "
                    f"border-radius: {max(4, size // 4)}px; font-family: {dt.FONT_UI}; "
                    f"font-weight: 700; font-size: {max(10, size // 2)}px; }}")
    return b


def fighter_html(code: str, colour_hex: str | None, size: int = dt.SIZE_LG) -> str:
    """Rich text for a fighter: a colour dot and the code in bold mono
    ('● K7Q2'). An old recording's name is shown in the UI font instead."""
    dot = colour_hex or dt.text_tertiary()
    import brain_battle.data.fighters as fighters
    mono = fighters.code_ok(code)
    family = dt.FONT_MONO if mono else dt.FONT_UI
    spacing = "letter-spacing:1px;" if mono else ""
    return (f"<span style='color:{dot}'>&#9679;</span>&nbsp;"
            f"<span style=\"font-family:{family};font-weight:700;{spacing}"
            f"color:{dt.text_primary()};font-size:{size}px\">{code}</span>")


def versus_html(a: str, ca, b: str, cb, size: int = dt.SIZE_LG) -> str:
    """'● K7Q2 vs ● M3XA'."""
    return (fighter_html(a, ca, size) + f"&nbsp;&nbsp;<span style='color:{dt.text_tertiary()};"
            f"font-size:{dt.SIZE_MD}px'>vs</span>&nbsp;&nbsp;" + fighter_html(b, cb, size))


STATUS_CHIP = {  # status key → (text, background, foreground)
    "needs_labelling": ("Needs labels", dt._Palette.warning_bg, dt._Palette.warning),
    "labelled":        ("Labelled", dt._Palette.success_bg, dt._Palette.success_text),
    "exported":        ("Exported", dt._Palette.accent_ghost, dt._Palette.accent_hover),
    "interrupted":     ("Interrupted", dt._Palette.error_bg, dt._Palette.error_text),
    "unreadable":      ("Can't be opened", dt._Palette.card_bg_hover, dt._Palette.text_secondary),
}


def status_chip(status: str) -> QLabel:
    text, bg, fg = STATUS_CHIP.get(status, (status, dt.card_bg_hover(), dt.text_secondary()))
    l = QLabel(text)
    l.setObjectName("statusChip")
    l.setAlignment(Qt.AlignmentFlag.AlignCenter)
    l.setStyleSheet(f"#statusChip {{ background: {bg}; color: {fg}; border-radius: 11px; "
                    f"padding: 3px 10px; font-family: {dt.FONT_UI}; font-size: {dt.SIZE_XS}px; "
                    f"font-weight: 600; }}")
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


class FlowLayout(QLayout):
    """Lays its widgets out left to right and wraps onto the next line —
    for rows of chips (the label list, fighter colours)."""

    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self._items = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):          # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):              # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):              # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):    # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):      # noqa: N802
        return True

    def heightForWidth(self, w):      # noqa: N802
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):      # noqa: N802
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):               # noqa: N802
        return self.minimumSize()

    def minimumSize(self):            # noqa: N802
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _do(self, rect, test):
        x, y, line = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            nx = x + hint.width() + self._sp
            if nx - self._sp > rect.right() and line > 0:
                x, y = rect.x(), y + line + self._sp
                nx = x + hint.width() + self._sp
                line = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x = nx
            line = max(line, hint.height())
        return y + line - rect.y()


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


# ── dialogs: the one in-app dialog ───────────────────────────────────────────

#: Tests answer dialogs headlessly: ``widgets.dialog_responder = fn`` where
#: ``fn(dialog)`` returns the result ``exec()`` should give (for ``ask``: True /
#: False). While it is set no dialog is shown. Every dialog it answered is also
#: kept in ``answered_dialogs`` (title, text, button texts) so a test can check
#: what was asked.
dialog_responder = None
answered_dialogs: list = []


def _outline_danger_qss() -> str:
    """Prototype ``.btn.danger``: white, red border, red text."""
    return (f"QPushButton {{ background: {dt.surface()}; color: {dt.error_color()}; "
            f"border: 1px solid {dt.record_red()}; border-radius: {dt.RADIUS_MD}px; "
            f"padding: 10px 20px; font-family: {dt.FONT_UI}; font-size: {dt.SIZE_MD}px; "
            f"font-weight: 600; }}"
            f"QPushButton:hover {{ background: {dt.error_bg()}; }}")


class AppDialog(QDialog):
    """The app's dialog (docs/design/prototype.html ``.dialog``): a white card
    with 20 px corners on a dimmed cover over the whole window; a title, text,
    any extra content in ``body``, and buttons on the right. ``tone`` is None,
    ``"warn"`` (orange border) or ``"danger"`` (red border, red title). It is
    frameless — never a native macOS / Windows alert — and modal.

    ``dim=False`` uses a light cover (the prototype's Recording screen: the
    recording stays visible behind it)."""

    def __init__(self, parent=None, title: str = "", text: str = "", tone: str | None = None,
                 width: int = 520, dim: bool = True):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.tone = tone
        self._dim = dim
        self._title_text = title
        self._text = text
        self._cancel_result = 0
        self.setObjectName("appDialog")
        # The app stylesheet paints every QDialog white; this one is a cover.
        self.setStyleSheet("QDialog#appDialog { background: transparent; }")
        outer = QVBoxLayout(self); outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        row = QHBoxLayout(); row.addStretch(1)
        self.card = QFrame(); self.card.setObjectName("appDialogCard")
        self.card.setMaximumWidth(width); self.card.setMinimumWidth(min(width, 360))
        self.card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        border = {"warn": "2px solid #E8890C", "danger": f"2px solid {dt.record_red()}"}.get(
            tone, f"1px solid {dt.border()}")
        self.card.setStyleSheet(f"#appDialogCard {{ background: {dt.surface()}; border: {border}; "
                                f"border-radius: 20px; }}")
        row.addWidget(self.card, 100); row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        v = QVBoxLayout(self.card); v.setContentsMargins(28, 26, 28, 24); v.setSpacing(14)
        self.title_label = QLabel(title); self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(dt.css_text(20, 700, dt.error_color() if tone == "danger"
                                                   else dt.text_primary()))
        self.title_label.setVisible(bool(title))
        v.addWidget(self.title_label)
        self.text_label = QLabel(text); self.text_label.setWordWrap(True)
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)
        self.text_label.setStyleSheet(dt.css_text(dt.SIZE_MD, 400, dt.text_secondary()))
        self.text_label.setVisible(bool(text))
        v.addWidget(self.text_label)
        self.body = QVBoxLayout(); self.body.setSpacing(10)
        v.addLayout(self.body)
        self.button_row = QHBoxLayout(); self.button_row.setSpacing(10)
        self.button_row.addStretch(1)
        v.addLayout(self.button_row)
        self.buttons: list[QPushButton] = []
        if parent is not None:
            self.setWindowTitle(title)

    # content
    def title(self) -> str:
        return self._title_text

    def text(self) -> str:
        return self._text

    def button_texts(self) -> list:
        return [b.text() for b in self.buttons]

    def add_button(self, text: str, result=1, kind: str = "secondary",
                   default: bool = False) -> QPushButton:
        """A button on the right; clicking it closes the dialog with ``result``.
        ``kind``: ``primary`` · ``secondary`` · ``danger`` (outlined red)."""
        if kind == "primary":
            b = primary_button(text)
        elif kind == "danger":
            b = _btn(text, _outline_danger_qss())
        else:
            b = secondary_button(text)
        b.clicked.connect(lambda _c=False, r=result: self.done(r))
        b.setDefault(default); b.setAutoDefault(default)
        self.button_row.addWidget(b)
        self.buttons.append(b)
        return b

    def set_cancel_result(self, result) -> None:
        """What Esc (or closing the window) returns."""
        self._cancel_result = result

    # behaviour
    def exec(self):                                    # noqa: A003
        if dialog_responder is not None:
            answered_dialogs.append({"title": self._title_text, "text": self._text,
                                     "buttons": self.button_texts(), "tone": self.tone})
            return dialog_responder(self)
        self._cover_window()
        return super().exec()

    def show(self):
        self._cover_window()
        super().show()

    def _cover_window(self):
        top = self.parentWidget().window() if self.parentWidget() is not None else None
        if top is not None and top.isVisible():
            self.setGeometry(top.geometry())
        else:
            self.adjustSize()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.done(self._cancel_result); return
        super().keyPressEvent(e)

    def reject(self):
        self.done(self._cancel_result)

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), QColor(15, 23, 42, 115 if self._dim else 46))
        finally:
            p.end()


def ask(parent, title: str, text: str, yes: str, no: str = "Cancel", tone: str | None = None,
        safe_first: bool = False, danger: bool = False) -> bool:
    """A yes/no question in the app's dialog. True when ``yes`` was chosen.
    ``safe_first``: the safe answer (``no``, e.g. "Keep recording") is the blue
    primary button, as the prototype does for anything that stops a recording.
    ``danger``: ``yes`` is outlined red."""
    d = AppDialog(parent, title, text, tone=tone)
    d.set_cancel_result(0)
    yes_kind = "danger" if danger else ("secondary" if safe_first else "primary")
    if safe_first:
        d.add_button(no, 0, "primary", default=True)
        d.add_button(yes, 1, yes_kind)
    else:
        d.add_button(no, 0, "secondary")
        d.add_button(yes, 1, yes_kind, default=True)
    return bool(d.exec())


def tell(parent, title: str, text: str, tone: str | None = "warn", button: str = "OK") -> None:
    """A message with one button (an error, a folder problem) in the app's dialog."""
    d = AppDialog(parent, title, text, tone=tone)
    d.set_cancel_result(1)
    d.add_button(button, 1, "primary", default=True)
    d.exec()


# ── the one dropdown ─────────────────────────────────────────────────────────

def qss_dropdown(name: str, compact: bool = False) -> str:
    p = dt
    return f"""
        QComboBox#{name} {{
            background: {p.surface()}; color: {p.text_primary()};
            border: 1px solid {p.border_strong()};
            border-radius: {dt.RADIUS_SM if compact else dt.RADIUS_MD}px;
            padding: {'3px 28px 3px 10px' if compact else '7px 34px 7px 12px'};
            font-family: {dt.FONT_UI}; font-size: {dt.SIZE_XS if compact else dt.SIZE_MD}px;
            combobox-popup: 0;
        }}
        QComboBox#{name}:hover {{ border-color: {p.text_tertiary()}; }}
        QComboBox#{name}:focus, QComboBox#{name}:on {{ border: 1px solid {p.accent()}; }}
        QComboBox#{name}:disabled {{ background: {p.card_bg_hover()}; color: {p.text_tertiary()}; }}
        QComboBox#{name}::drop-down {{ border: none; width: 0px; }}
        QComboBox#{name}::down-arrow {{ image: none; width: 0px; }}
        QComboBox#{name} QAbstractItemView {{
            background: {p.surface()}; color: {p.text_primary()};
            border: 1px solid {p.border_strong()}; border-radius: {dt.RADIUS_SM}px;
            padding: 4px; outline: none;
            font-family: {dt.FONT_UI}; font-size: {dt.SIZE_XS if compact else dt.SIZE_MD}px;
        }}
        QComboBox#{name} QAbstractItemView::item {{
            min-height: {24 if compact else 32}px; padding: 0 8px; border-radius: 6px;
            color: {p.text_primary()};
        }}
        QComboBox#{name} QAbstractItemView::item:selected,
        QComboBox#{name} QAbstractItemView::item:hover {{
            background: {p.accent_ghost()}; color: {p.accent_pressed()};
        }}
    """


class Dropdown(QComboBox):
    """The app's dropdown: light, rounded, Inter, a blue border when focused or
    open, its own chevron and a light list (no system-styled popup).
    ``Dropdown([("Side camera 1", 0), …])`` or plain strings."""
    _n = 0

    def __init__(self, items=(), compact: bool = False, parent=None):
        super().__init__(parent)
        Dropdown._n += 1
        self.setObjectName(f"bbDropdown{Dropdown._n}")
        self._compact = compact
        self.setView(QListView())
        self.view().setStyleSheet(f"background: {dt.surface()}; color: {dt.text_primary()};")
        self.setStyleSheet(qss_dropdown(self.objectName(), compact))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(28 if compact else 40)
        for it in items:
            if isinstance(it, (tuple, list)):
                self.addItem(str(it[0]), it[1])
            else:
                self.addItem(str(it))

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            col = QColor(dt.accent() if (self.hasFocus() and self.isEnabled())
                         else dt.text_tertiary())
            p.setPen(QPen(col, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            s = 3.5 if self._compact else 4.5
            cx = self.width() - (14 if self._compact else 18)
            cy = self.height() / 2 + 1
            p.drawPolyline([QPointF(cx - s, cy - s / 2), QPointF(cx, cy + s / 2),
                            QPointF(cx + s, cy - s / 2)])
        finally:
            p.end()


__all__ = ["title", "heading", "body", "muted", "caption", "Card", "hline",
           "primary_button", "secondary_button", "ghost_button", "danger_button",
           "icon_button", "help_button", "Segmented", "StatusLine", "pill", "boxer_badge",
           "boxer_chip", "FlowLayout", "fighter_badge", "fighter_html", "versus_html", "status_chip",
           "STATUS_CHIP", "Banner", "TimerLabel", "Dot", "clear_layout", "MIN_TARGET",
           "AppDialog", "ask", "tell", "Dropdown", "qss_dropdown", "neutral_badge", "WrapLabel"]
