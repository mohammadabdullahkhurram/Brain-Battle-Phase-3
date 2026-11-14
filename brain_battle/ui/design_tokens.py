"""
design_tokens.py
────────────────
The light-only design system (docs/DESIGN.md "Design tokens" — the UI source of
truth, rewritten for the November 2026 redesign, ADR-0018). There is NO dark
theme and no theme switching. Colours, type sizes, radii and the stylesheet
factories every screen uses live here; if you need a colour, add it here from
DESIGN.md — don't hardcode hex in widgets.

The accessor functions (``accent()``, ``border()`` …) all return the one light
palette; they are functions so call sites read the same everywhere.
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve
from PyQt6.QtGui  import QFont


# ═══════════════════════════════════════════════════════════════ PALETTE
# (docs/DESIGN.md → "Design tokens")

class _Palette:
    # Surfaces
    canvas          = "#F3F4F6"   # Ground
    surface         = "#FFFFFF"   # Surface
    surface_subtle  = "#F9FAFB"   # quiet fill inside a card (table header, wells)
    sidebar_bg      = "#FFFFFF"
    card_bg         = "#FFFFFF"
    card_bg_hover   = "#F2F4F7"
    raised          = "#FFFFFF"
    stage           = "#0B0F17"   # behind video (the only dark surface: it is picture, not UI)

    # Borders
    border          = "#E4E7EC"
    border_strong   = "#CDD2DA"
    border_active   = "rgba(79, 70, 229, 0.45)"

    # Text
    text_primary    = "#101828"   # Ink
    text_secondary  = "#344054"   # Secondary ink
    text_tertiary   = "#5D6B82"   # Muted text (4.5:1 on Ground)
    text_inverse    = "#FFFFFF"

    # Accent
    accent          = "#4F46E5"
    accent_hover    = "#4338CA"
    accent_pressed  = "#3730A3"
    accent_ghost    = "#EEF2FF"   # Accent soft
    accent_border   = "rgba(79, 70, 229, 0.35)"

    # Semantic (foreground / soft-bg / on-soft text)
    success         = "#067647"
    success_bg      = "#ECFDF3"
    success_text    = "#085D3A"
    warning         = "#B54708"
    warning_bg      = "#FFFAEB"
    warning_text    = "#7A2E0E"
    error           = "#B42318"   # Danger
    error_bg        = "#FEF3F2"
    error_text      = "#912018"
    record_red      = "#DC2626"
    info            = "#4F46E5"

    # Boxers (always paired with the A/B letter + name in the UI)
    boxer_a_line    = "#0E7490"
    boxer_a_soft    = "#ECFEFF"
    boxer_a_text    = "#0B5566"
    boxer_b_line    = "#C2410C"
    boxer_b_soft    = "#FFF4ED"
    boxer_b_text    = "#8F300A"

    # Another labeller's labels in Compare (never a boxer or label colour)
    other_labeller  = "#667085"

    # Phases: (soft-bg, text)
    phase_rest      = ("#EAECF0", "#344054")
    phase_round     = ("#E0E7FF", "#3730A3")
    phase_break     = ("#FEF0C7", "#93370D")


def _p():
    return _Palette


# ─── Accessors (all return the single light palette) ──────────────────────
def canvas_bg():      return _p().canvas
def surface():        return _p().surface
def surface_subtle(): return _p().surface_subtle
def sidebar_bg():     return _p().sidebar_bg
def card_bg():        return _p().card_bg
def card_bg_hover():  return _p().card_bg_hover
def raised():         return _p().raised
def stage():          return _p().stage
def border():         return _p().border
def border_strong():  return _p().border_strong
def text_primary():   return _p().text_primary
def text_secondary(): return _p().text_secondary
def text_tertiary():  return _p().text_tertiary
def text_inverse():   return _p().text_inverse
def accent():         return _p().accent
def accent_hover():   return _p().accent_hover
def accent_pressed(): return _p().accent_pressed
def accent_ghost():   return _p().accent_ghost
def success_color():  return _p().success
def warning_color():  return _p().warning
def error_color():    return _p().error
def record_red():     return _p().record_red
def other_labeller(): return _p().other_labeller
def boxer_colors(which: str):
    """('A'|'B') → (line, soft_bg, text)."""
    p = _p()
    if str(which).upper().endswith("B"):
        return (p.boxer_b_line, p.boxer_b_soft, p.boxer_b_text)
    return (p.boxer_a_line, p.boxer_a_soft, p.boxer_a_text)

def phase_colors():
    p = _p()
    return {
        "idle":     p.text_tertiary,
        "baseline": p.phase_rest[1],
        "round":    p.phase_round[1],
        "break":    p.phase_break[1],
        "done":     p.success,
    }

def phase_fill(phase_id: str):
    """(soft background, text) for a phase id."""
    p = _p()
    return {"baseline": p.phase_rest, "round": p.phase_round, "break": p.phase_break,
            "done": (p.success_bg, p.success_text)}.get(phase_id, p.phase_rest)


def semantic(kind: str):
    """(foreground, soft background, text-on-soft) for ok / warn / bad / info / idle."""
    p = _p()
    return {
        "ok":   (p.success, p.success_bg, p.success_text),
        "warn": (p.warning, p.warning_bg, p.warning_text),
        "bad":  (p.error, p.error_bg, p.error_text),
        "info": (p.accent, p.accent_ghost, p.accent_pressed),
    }.get(kind, (p.text_tertiary, p.card_bg_hover, p.text_secondary))


# ═══════════════════════════════════════════════════════════════ TYPE

FONT_UI   = "'Inter', -apple-system, 'Segoe UI', Roboto, sans-serif"
FONT_MONO = "'JetBrains Mono', 'SF Mono', 'Menlo', 'Consolas', 'Courier New', monospace"
FONT_SANS = FONT_UI

SIZE_HERO    = 48
SIZE_DISPLAY = 30     # screen titles
SIZE_2XL     = 24
SIZE_XL      = 20     # card / dialog titles
SIZE_LG      = 16
SIZE_MD      = 14     # body
SIZE_SM      = 13
SIZE_XS      = 12
SIZE_CAPTION = 11

LS_TIGHT  = "-0.2px"
LS_NORMAL = "0"
LS_WIDE   = "0.3px"
LS_WIDER  = "0.8px"
LS_MEDIUM = "0.3px"

# ═══════════════════════════════════════════════════════════════ SPACE

SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 20
SPACE_6 = 24
SPACE_8 = 32
SPACE_10 = 40
SPACE_12 = 48

# DESIGN: 8px small controls, 10px controls, 14px cards, 18px hero cards.
RADIUS_SM   = 8
RADIUS_MD   = 10
RADIUS_LG   = 14
RADIUS_XL   = 18
RADIUS_FULL = 9999

MOTION_FAST   = 160
MOTION_NORMAL = 240
MOTION_SLOW   = 360
EASE_STANDARD = QEasingCurve.Type.OutCubic
EASE_EMPHASIS = QEasingCurve.Type.OutQuint


# ═══════════════════════════════════════════════════════════════ FONTS

def ui_font(pt: int = 11, weight: int = QFont.Weight.Normal) -> QFont:
    f = QFont("Inter"); f.setStyleHint(QFont.StyleHint.SansSerif)
    f.setPointSize(pt); f.setWeight(weight)
    return f


def number_font(px: int, weight: int = QFont.Weight.DemiBold) -> QFont:
    """Inter with tabular (equal-width) figures, for timers and counts that are
    painted by hand — the digits must not jiggle as they change."""
    f = QFont("Inter"); f.setStyleHint(QFont.StyleHint.SansSerif)
    f.setPixelSize(int(px)); f.setWeight(weight)
    try:
        f.setFeature(QFont.Tag("tnum"), 1)
    except Exception:
        pass                         # older Qt: proportional figures, still fine
    return f


def css_text(size: int = SIZE_MD, weight: int = 400, color: str | None = None,
             extra: str = "") -> str:
    """A widget stylesheet for a piece of text."""
    return (f"color: {color or text_primary()}; font-family: {FONT_UI}; "
            f"font-size: {size}px; font-weight: {weight}; background: transparent; {extra}")


# ═══════════════════════════════════════════════════════ STYLESHEETS

def qss_app() -> str:
    p = _p()
    return f"""
        QMainWindow, #AppRoot {{
            background-color: {p.canvas};
            color: {p.text_primary};
            font-family: {FONT_UI};
            font-size: {SIZE_MD}px;
        }}
        QWidget {{
            color: {p.text_primary};
            font-family: {FONT_UI};
            font-size: {SIZE_MD}px;
        }}
        /* Labels are transparent by default so text never paints its own
           (grey, palette-Window) box on a card. A label that wants a
           background sets one in its own stylesheet, which overrides this. */
        QLabel {{ background: transparent; }}
        QDialog {{ background: {p.surface}; }}
        QToolTip {{
            background-color: {p.text_primary};
            color: #FFFFFF;
            border: none;
            border-radius: 6px;
            padding: 6px 10px;
            font-family: {FONT_UI};
            font-size: {SIZE_XS}px;
        }}
        QSplitter::handle {{ background: {p.border}; }}
        QScrollArea {{ background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
        QScrollBar::handle:vertical {{ background: {p.border_strong}; border-radius: 3px; min-height: 30px; }}
        QScrollBar::handle:vertical:hover {{ background: {p.text_tertiary}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px; }}
        QScrollBar::handle:horizontal {{ background: {p.border_strong}; border-radius: 3px; min-width: 30px; }}
        QScrollBar::handle:horizontal:hover {{ background: {p.text_tertiary}; }}
        QMenu {{
            background-color: {p.surface};
            border: 1px solid {p.border_strong};
            border-radius: {RADIUS_MD}px;
            color: {p.text_primary};
            padding: 6px;
        }}
        QMenu::item {{ padding: 9px 18px; border-radius: {RADIUS_SM}px; font-size: {SIZE_MD}px; }}
        QMenu::item:selected {{ background: {p.accent_ghost}; color: {p.accent_pressed}; }}
        QMenu::separator {{ height: 1px; background: {p.border}; margin: 6px 8px; }}
    """


def qss_canvas_ambient() -> str:
    return f"QWidget#canvas {{ background: {_p().canvas}; }}"


def qss_card(name: str = "card", radius: int = RADIUS_LG, bg: str | None = None,
             border_color: str | None = None) -> str:
    p = _p()
    return (f"QFrame#{name} {{ background: {bg or p.card_bg}; "
            f"border: 1px solid {border_color or p.border}; border-radius: {radius}px; }}")


def qss_sidebar() -> str:
    p = _p()
    return f"""
        QWidget#sidebar {{
            background: {p.sidebar_bg};
            border-right: 1px solid {p.border};
        }}
        QPushButton#sidebarItem {{
            background: transparent;
            border: none;
            color: {p.text_secondary};
            padding: 11px 14px;
            text-align: left;
            font-family: {FONT_UI};
            font-size: {SIZE_MD}px;
            font-weight: 500;
            border-radius: {RADIUS_MD}px;
            margin: 1px 12px;
        }}
        QPushButton#sidebarItem:hover {{ background: {p.card_bg_hover}; color: {p.text_primary}; }}
        QPushButton#sidebarItem:checked {{ background: {p.accent_ghost}; color: {p.accent_pressed}; font-weight: 600; }}
        QPushButton#sidebarPrimary {{
            background: {p.text_primary};
            border: none;
            color: #FFFFFF;
            padding: 12px 14px;
            text-align: left;
            font-family: {FONT_UI};
            font-size: {SIZE_MD}px;
            font-weight: 600;
            border-radius: {RADIUS_MD}px;
            margin: 0 12px 8px 12px;
        }}
        QPushButton#sidebarPrimary:hover {{ background: #1D2939; }}
    """


def _button(bg, fg, bd, hover_bg, hover_fg=None, hover_bd=None, compact=False, weight=600,
            pressed_bg=None) -> str:
    p = _p()
    pad = "6px 14px" if compact else "10px 20px"
    size = SIZE_SM if compact else SIZE_MD
    return f"""
        QPushButton {{
            background: {bg}; color: {fg}; border: 1px solid {bd};
            border-radius: {RADIUS_SM if compact else RADIUS_MD}px; padding: {pad};
            font-family: {FONT_UI}; font-size: {size}px; font-weight: {weight};
        }}
        QPushButton:hover {{ background: {hover_bg}; color: {hover_fg or fg}; border-color: {hover_bd or hover_bg}; }}
        QPushButton:pressed {{ background: {pressed_bg or hover_bg}; }}
        QPushButton:disabled {{ background: {p.card_bg_hover}; border-color: {p.border}; color: #98A2B3; }}
    """


def qss_primary_button(compact: bool = False) -> str:
    p = _p()
    return _button(p.accent, "#FFFFFF", p.accent, p.accent_hover, compact=compact,
                   pressed_bg=p.accent_pressed)


def qss_secondary_button(compact: bool = False) -> str:
    p = _p()
    return _button(p.surface, p.text_secondary, p.border_strong, p.card_bg_hover,
                   p.text_primary, p.border_strong, compact=compact, weight=500)


def qss_ghost_button(compact: bool = False) -> str:
    """No border until hovered — for quiet actions (Back, Cancel, icon buttons)."""
    p = _p()
    return _button("transparent", p.text_secondary, "transparent", p.card_bg_hover,
                   p.text_primary, p.card_bg_hover, compact=compact, weight=500)


def qss_danger_button(compact: bool = False) -> str:
    p = _p()
    return _button(p.record_red, "#FFFFFF", p.record_red, p.error, compact=compact,
                   pressed_bg=p.error_text)


def qss_chip(active: bool = False) -> str:
    p = _p()
    if active:
        return f"""
            QPushButton {{
                background: {p.accent_ghost}; color: {p.accent_pressed};
                border: 1px solid {p.accent_border};
                border-radius: {RADIUS_SM}px; padding: 5px 12px;
                font-family: {FONT_UI}; font-size: {SIZE_XS}px; font-weight: 600;
            }}
        """
    return f"""
        QPushButton {{
            background: {p.surface}; color: {p.text_secondary};
            border: 1px solid {p.border_strong};
            border-radius: {RADIUS_SM}px; padding: 5px 12px;
            font-family: {FONT_UI}; font-size: {SIZE_XS}px; font-weight: 500;
        }}
        QPushButton:hover {{ color: {p.text_primary}; background: {p.card_bg_hover}; }}
    """


def qss_input() -> str:
    p = _p()
    return f"""
        QLineEdit, QSpinBox, QDoubleSpinBox {{
            background: {p.surface};
            color: {p.text_primary};
            border: 1px solid {p.border_strong};
            border-radius: {RADIUS_MD}px;
            padding: 9px 12px;
            font-family: {FONT_UI};
            font-size: {SIZE_MD}px;
            selection-background-color: {p.accent_ghost};
            selection-color: {p.text_primary};
        }}
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
            border: 1px solid {p.accent};
        }}
        QLineEdit:disabled {{ background: {p.card_bg_hover}; color: {p.text_tertiary}; }}
    """


def qss_combo(compact: bool = False) -> str:
    p = _p()
    return f"""
        QComboBox {{
            background: {p.surface}; color: {p.text_primary};
            border: 1px solid {p.border_strong};
            border-radius: {RADIUS_SM if compact else RADIUS_MD}px;
            padding: {'3px 10px' if compact else '8px 12px'};
            font-family: {FONT_UI}; font-size: {SIZE_XS if compact else SIZE_MD}px;
        }}
        QComboBox:hover {{ border-color: {p.text_tertiary}; }}
        QComboBox:focus {{ border-color: {p.accent}; }}
        QComboBox::drop-down {{ border: none; width: 22px; }}
        QComboBox QAbstractItemView {{
            background: {p.surface}; color: {p.text_primary};
            border: 1px solid {p.border_strong}; outline: none;
            selection-background-color: {p.accent_ghost}; selection-color: {p.accent_pressed};
        }}
    """


def qss_checkbox() -> str:
    p = _p()
    return (f"QCheckBox {{ color: {p.text_primary}; font-family: {FONT_UI}; "
            f"font-size: {SIZE_MD}px; spacing: 10px; background: transparent; }}"
            f"QCheckBox:disabled {{ color: {p.text_tertiary}; }}")


def qss_status_pill(kind: str = "idle") -> str:
    p = _p()
    palette = {
        "live":       (p.success_text, p.success_bg, p.success_bg),
        "recording":  (p.record_red,  p.error_bg,   p.error_bg),
        "idle":       (p.text_secondary, p.card_bg_hover,  p.card_bg_hover),
        "connected":  (p.success_text, p.success_bg, p.success_bg),
        "warning":    (p.warning_text, p.warning_bg, p.warning_bg),
        "danger":     (p.error_text, p.error_bg, p.error_bg),
        "accent":     (p.accent_pressed, p.accent_ghost, p.accent_ghost),
        "baseline":   (p.phase_rest[1],  p.phase_rest[0],  p.phase_rest[0]),
        "round":      (p.phase_round[1], p.phase_round[0], p.phase_round[0]),
        "break":      (p.phase_break[1], p.phase_break[0], p.phase_break[0]),
        "done":       (p.success_text, p.success_bg, p.success_bg),
    }
    fg, bg, bd = palette.get(kind, palette["idle"])
    return (f"background: {bg}; color: {fg}; border: 1px solid {bd}; "
            f"border-radius: 11px; padding: 3px 10px; "
            f"font-family: {FONT_UI}; font-size: {SIZE_XS}px; font-weight: 600;")


# ═══════════════════════════════════════════════════════════════ ICONS

ICON_SVGS = {
    "eye":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>',
    "eye-off":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>'
        '<path d="M4 4l16 16"/></svg>',
    "user":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
    "users":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/>'
        '<path d="M16 4.6a3.5 3.5 0 0 1 0 6.8"/><path d="M18 14.2a6.5 6.5 0 0 1 3.5 5.8"/></svg>',
    "download":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 4v11"/><path d="m7 11 5 5 5-5"/><path d="M4 20h16"/></svg>',
    "more":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{c}" stroke="none">'
        '<circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>',
    "check-circle":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 3 3 5-6"/></svg>',
    "alert":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 10v4"/><circle cx="12" cy="17" r="0.7" fill="{c}"/></svg>',
    "x-circle":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6"/><path d="m15 9-6 6"/></svg>',
    "x":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m6 6 12 12"/><path d="m18 6-12 12"/></svg>',
    "play":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{c}" stroke="none">'
        '<path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5Z"/></svg>',
    "pause":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{c}" stroke="none">'
        '<rect x="5.5" y="4" width="4.5" height="16" rx="1.2"/><rect x="14" y="4" width="4.5" height="16" rx="1.2"/></svg>',
    "step-back":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{c}" stroke="{c}" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M18 5.5v13L8.5 12 18 5.5Z"/><path d="M5.5 5v14" fill="none"/></svg>',
    "step-forward":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{c}" stroke="{c}" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M6 5.5v13l9.5-6.5L6 5.5Z"/><path d="M18.5 5v14" fill="none"/></svg>',
    "flag":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M5 21V4"/><path d="M5 4h12l-2.5 4.5L17 13H5"/></svg>',
    "arrow-right":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 12h16"/><path d="m14 6 6 6-6 6"/></svg>',
    "arrow-left":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M20 12H4"/><path d="m10 6-6 6 6 6"/></svg>',
    "record":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.6"><circle cx="12" cy="12" r="9"/>'
        '<circle cx="12" cy="12" r="4.5" fill="#F04438" stroke="none"/></svg>',
    "stop":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{c}" stroke="none">'
        '<rect x="5" y="5" width="14" height="14" rx="2.5"/></svg>',
    "bell":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4l2-2Z"/><path d="M10 21h4"/></svg>',
    "expand":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M14 4h6v6"/><path d="M10 20H4v-6"/><path d="m20 4-7 7"/><path d="m4 20 7-7"/></svg>',
    "undo":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M8 5 3.5 9.5 8 14"/><path d="M3.5 9.5H14a6 6 0 0 1 0 12h-3"/></svg>',
    "drive":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="13" width="18" height="7" rx="2"/><path d="M5.5 13 8 5h8l2.5 8"/>'
        '<circle cx="17" cy="16.5" r="0.8" fill="{c}"/></svg>',
    "activity":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.5.5 0 0 1-.96 0L9.68 3.18a.5.5 0 0 0-.96 0l-2.35 8.36A2 2 0 0 1 4.44 13H2"/></svg>',
    "video":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5"/>'
        '<rect x="2" y="6" width="14" height="12" rx="2"/></svg>',
    "layers":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/>'
        '<path d="M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12"/>'
        '<path d="M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17"/></svg>',
    "tag":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/>'
        '<circle cx="7.5" cy="7.5" r="1.5" fill="{c}"/></svg>',
    "settings":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    "brain":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 18V5a3 3 0 1 0-5.997-.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 0 0 12 22Z"/>'
        '<path d="M12 18V5a3 3 0 1 1 5.997-.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 0 1 12 22Z"/></svg>',
    "home":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 9.5 12 3l9 6.5"/><path d="M5 10v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V10"/>'
        '<path d="M9 21v-6h6v6"/></svg>',
    "plus-circle":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="9"/><path d="M12 8v8"/><path d="M8 12h8"/></svg>',
    "chevron-left":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m15 18-6-6 6-6"/></svg>',
    "circle":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{c}" '
        'stroke="none"><circle cx="12" cy="12" r="5"/></svg>',
    "book":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>'
        '<path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/></svg>',
    "help-circle":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="9"/>'
        '<path d="M9.5 9a2.5 2.5 0 0 1 4.8 1c0 1.5-2.3 1.9-2.3 3.5"/>'
        '<circle cx="12" cy="17" r="0.6" fill="{c}" stroke="none"/></svg>',
    "search":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>',
    "folder":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 20a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h5l2 2.5h7a2 2 0 0 1 2 2V18'
        'a2 2 0 0 1-2 2Z"/></svg>',
}


def icon_pixmap(name: str, color: str = None, size: int = 20):
    from PyQt6.QtGui import QPixmap, QPainter
    from PyQt6.QtCore import Qt, QByteArray
    from PyQt6.QtSvg import QSvgRenderer
    if color is None:
        color = text_primary()
    svg = ICON_SVGS.get(name)
    if svg is None:
        pm = QPixmap(size, size); pm.fill(Qt.GlobalColor.transparent)
        return pm
    svg_bytes = QByteArray(svg.format(c=color).encode("utf-8"))
    renderer = QSvgRenderer(svg_bytes)
    pm = QPixmap(size, size); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); renderer.render(p); p.end()
    return pm


PHASE_COLORS = phase_colors()
