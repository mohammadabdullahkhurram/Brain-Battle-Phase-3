"""
design_tokens.py
────────────────
Light-only design system, built from the approved tokens in docs/DESIGN.md
(the UI source of truth). There is NO dark theme and no theme switching — the
app is light mode only (DESIGN "Principles"). All accessor functions return the
one light palette; they are kept as functions so existing call sites don't
change.

Colours come straight from DESIGN.md "Design tokens". If you need a colour,
add it here from DESIGN.md — don't hardcode hex in widgets.
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve
from PyQt6.QtGui  import QFont


# ═══════════════════════════════════════════════════════════════ PALETTE
# (docs/DESIGN.md → "Design tokens")

class _Palette:
    # Surfaces
    canvas          = "#F6F7F9"   # Ground
    surface         = "#FFFFFF"   # Surface
    sidebar_bg      = "#FFFFFF"
    card_bg         = "#FFFFFF"
    card_bg_hover   = "#F1F2F5"
    raised          = "#FFFFFF"

    # Borders
    border          = "#DFE2E8"
    border_strong   = "#C9CED7"
    border_active   = "rgba(61, 79, 214, 0.45)"

    # Text
    text_primary    = "#15171C"   # Ink
    text_secondary  = "#3A404B"   # Secondary ink
    text_tertiary   = "#555D6B"   # Muted text
    text_inverse    = "#FFFFFF"

    # Accent
    accent          = "#3D4FD6"
    accent_hover    = "#2B3AA8"
    accent_pressed  = "#2B3AA8"
    accent_ghost    = "#EEF0FD"   # Accent soft
    accent_border   = "rgba(61, 79, 214, 0.35)"

    # Semantic (foreground / soft-bg / on-soft text)
    success         = "#15803D"
    success_bg      = "#E8F5EC"
    success_text    = "#11632F"
    warning         = "#B45309"
    warning_bg      = "#FDF3E7"
    warning_text    = "#6B3205"
    error           = "#B91C1C"   # Danger
    error_bg        = "#FDECEC"
    error_text      = "#991B1B"
    record_red      = "#DC2626"
    info            = "#3D4FD6"    # no distinct "info" in DESIGN → use accent

    # Boxers (always paired with the A/B letter + name in the UI)
    boxer_a_line    = "#0E7490"
    boxer_a_soft    = "#E6F4F7"
    boxer_a_text    = "#0B5566"
    boxer_b_line    = "#C2410C"
    boxer_b_soft    = "#FDEFE7"
    boxer_b_text    = "#8F300A"

    # Phases: (soft-bg, text)
    phase_rest      = ("#E3E7ED", "#3A404B")
    phase_round     = ("#DDE1FB", "#2B3AA8")
    phase_break     = ("#F6E3C8", "#6B3205")


def _p():
    return _Palette


# ─── Accessors (all return the single light palette) ──────────────────────
def canvas_bg():      return _p().canvas
def surface():        return _p().surface
def sidebar_bg():     return _p().sidebar_bg
def card_bg():        return _p().card_bg
def card_bg_hover():  return _p().card_bg_hover
def raised():         return _p().raised
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
def info_color():     return _p().info

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


# ═══════════════════════════════════════════════════════════════ TYPE

FONT_UI   = "'Inter', -apple-system, 'Segoe UI', Roboto, sans-serif"
FONT_MONO = "'JetBrains Mono', 'SF Mono', 'Menlo', 'Consolas', 'Courier New', monospace"
FONT_SANS = FONT_UI

SIZE_HERO    = 48
SIZE_DISPLAY = 28
SIZE_LG      = 16
SIZE_MD      = 13
SIZE_SM      = 12
SIZE_XS      = 11
SIZE_CAPTION = 10

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

# DESIGN: 10px controls, 12–14px cards.
RADIUS_SM   = 8
RADIUS_MD   = 10
RADIUS_LG   = 14
RADIUS_XL   = 16
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

def mono_font(pt: int = 11, weight: int = QFont.Weight.Normal) -> QFont:
    f = QFont("JetBrains Mono"); f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSize(pt); f.setWeight(weight)
    return f

def sans_font(pt: int = 11, weight: int = QFont.Weight.Normal) -> QFont:
    return ui_font(pt, weight)


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
        QToolTip {{
            background-color: {p.surface};
            color: {p.text_primary};
            border: 1px solid {p.border_strong};
            border-radius: {RADIUS_SM}px;
            padding: 6px 10px;
            font-family: {FONT_UI};
            font-size: {SIZE_XS}px;
        }}
        QSplitter::handle {{ background: {p.border}; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
        QScrollBar::handle:vertical {{ background: {p.border_strong}; border-radius: 5px; min-height: 30px; }}
        QScrollBar::handle:vertical:hover {{ background: {p.text_tertiary}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
        QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px; }}
        QScrollBar::handle:horizontal {{ background: {p.border_strong}; border-radius: 5px; min-width: 30px; }}
        QScrollBar::handle:horizontal:hover {{ background: {p.text_tertiary}; }}
        QMenu {{
            background-color: {p.surface};
            border: 1px solid {p.border_strong};
            border-radius: {RADIUS_MD}px;
            color: {p.text_primary};
            padding: 6px;
        }}
        QMenu::item {{ padding: 8px 16px; border-radius: {RADIUS_SM}px; }}
        QMenu::item:selected {{ background: {p.accent_ghost}; color: {p.accent}; }}
    """


def qss_canvas_ambient() -> str:
    return f"QWidget#canvas {{ background: {_p().canvas}; }}"


def qss_card() -> str:
    p = _p()
    return f"""
        QFrame#card {{
            background: {p.card_bg};
            border: 1px solid {p.border};
            border-radius: {RADIUS_LG}px;
        }}
    """


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
            padding: 13px 18px;
            text-align: left;
            font-family: {FONT_UI};
            font-size: {SIZE_MD}px;
            font-weight: 500;
            border-radius: {RADIUS_MD}px;
            margin: 2px 10px;
        }}
        QPushButton#sidebarItem:hover {{ background: {p.card_bg_hover}; color: {p.text_primary}; }}
        QPushButton#sidebarItem:checked {{ background: {p.accent_ghost}; color: {p.accent}; font-weight: 600; }}
    """


def qss_primary_button() -> str:
    p = _p()
    return f"""
        QPushButton {{
            background: {p.accent};
            color: #FFFFFF;
            border: 1px solid {p.accent};
            border-radius: {RADIUS_MD}px;
            padding: 10px 22px;
            font-family: {FONT_UI};
            font-size: {SIZE_MD}px;
            font-weight: 600;
        }}
        QPushButton:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
        QPushButton:pressed {{ background: {p.accent_pressed}; border-color: {p.accent_pressed}; }}
        QPushButton:disabled {{ background: {p.card_bg_hover}; border-color: {p.border}; color: {p.text_tertiary}; }}
    """


def qss_secondary_button() -> str:
    p = _p()
    return f"""
        QPushButton {{
            background: {p.surface};
            color: {p.text_primary};
            border: 1px solid {p.border_strong};
            border-radius: {RADIUS_MD}px;
            padding: 9px 20px;
            font-family: {FONT_UI};
            font-size: {SIZE_MD}px;
            font-weight: 500;
        }}
        QPushButton:hover {{ background: {p.accent_ghost}; border-color: {p.accent}; color: {p.accent}; }}
        QPushButton:disabled {{ color: {p.text_tertiary}; border-color: {p.border}; }}
    """


def qss_danger_button() -> str:
    p = _p()
    return f"""
        QPushButton {{
            background: {p.record_red};
            color: #FFFFFF;
            border: 1px solid {p.record_red};
            border-radius: {RADIUS_MD}px;
            padding: 9px 20px;
            font-family: {FONT_UI};
            font-size: {SIZE_MD}px;
            font-weight: 600;
        }}
        QPushButton:hover {{ background: {p.error}; border-color: {p.error}; }}
    """


def qss_ghost_button() -> str:
    p = _p()
    return f"""
        QPushButton {{
            background: transparent;
            color: {p.text_secondary};
            border: none;
            border-radius: {RADIUS_SM}px;
            padding: 6px 12px;
            font-family: {FONT_UI};
            font-size: {SIZE_SM}px;
        }}
        QPushButton:hover {{ background: {p.card_bg_hover}; color: {p.text_primary}; }}
        QPushButton:checked {{ background: {p.accent_ghost}; color: {p.accent}; }}
    """


def qss_chip(active: bool = False) -> str:
    p = _p()
    if active:
        return f"""
            QPushButton {{
                background: {p.accent_ghost}; color: {p.accent};
                border: 1px solid {p.accent_border};
                border-radius: {RADIUS_SM}px; padding: 5px 14px;
                font-family: {FONT_UI}; font-size: {SIZE_XS}px;
                font-weight: 600; letter-spacing: {LS_WIDE};
            }}
        """
    return f"""
        QPushButton {{
            background: {p.surface}; color: {p.text_secondary};
            border: 1px solid {p.border_strong};
            border-radius: {RADIUS_SM}px; padding: 5px 14px;
            font-family: {FONT_UI}; font-size: {SIZE_XS}px;
            letter-spacing: {LS_WIDE};
        }}
        QPushButton:hover {{ color: {p.text_primary}; border-color: {p.text_secondary}; }}
    """


def qss_input() -> str:
    p = _p()
    return f"""
        QLineEdit, QSpinBox, QDoubleSpinBox {{
            background: {p.surface};
            color: {p.text_primary};
            border: 1px solid {p.border_strong};
            border-radius: {RADIUS_MD}px;
            padding: 10px 14px;
            font-family: {FONT_UI};
            font-size: {SIZE_MD}px;
            selection-background-color: {p.accent_ghost};
            selection-color: {p.text_primary};
        }}
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
            border: 1px solid {p.accent};
        }}
    """


def qss_section_label() -> str:
    p = _p()
    return (f"color: {p.text_tertiary}; font-family: {FONT_UI}; "
            f"font-size: {SIZE_CAPTION}px; letter-spacing: {LS_WIDER}; font-weight: 700;")


def qss_brand_label() -> str:
    p = _p()
    return (f"color: {p.accent}; font-family: {FONT_UI}; "
            f"font-size: {SIZE_CAPTION}px; letter-spacing: {LS_WIDER}; font-weight: 700;")


def qss_data_readout(size: int = SIZE_SM) -> str:
    p = _p()
    return (f"color: {p.text_primary}; font-family: {FONT_MONO}; "
            f"font-size: {size}px; letter-spacing: {LS_NORMAL};")


def qss_display_number() -> str:
    p = _p()
    return (f"color: {p.text_primary}; font-family: {FONT_MONO}; "
            f"font-size: {SIZE_HERO}px; font-weight: 300; letter-spacing: {LS_TIGHT};")


def qss_status_pill(kind: str = "idle") -> str:
    p = _p()
    palette = {
        "live":       (p.success_text, p.success_bg, p.success),
        "recording":  (p.record_red,  p.error_bg,   p.record_red),
        "idle":       (p.text_tertiary, "#EDEFF2",  p.border_strong),
        "connected":  (p.success_text, p.success_bg, p.success),
        "warning":    (p.warning_text, p.warning_bg, p.warning),
        "baseline":   (p.phase_rest[1],  p.phase_rest[0],  p.phase_rest[0]),
        "round":      (p.phase_round[1], p.phase_round[0], p.phase_round[0]),
        "break":      (p.phase_break[1], p.phase_break[0], p.phase_break[0]),
        "done":       (p.success_text, p.success_bg, p.success),
    }
    fg, bg, bd = palette.get(kind, palette["idle"])
    return (f"background: {bg}; color: {fg}; border: 1px solid {bd}; "
            f"border-radius: {RADIUS_FULL}px; padding: 4px 12px; "
            f"font-family: {FONT_UI}; font-size: {SIZE_XS}px; "
            f"font-weight: 600; letter-spacing: {LS_WIDE};")


def status_dot_css(kind: str = "idle") -> str:
    p = _p()
    color_map = {
        "idle": p.text_tertiary, "connected": p.success, "live": p.success,
        "recording": p.record_red, "error": p.error, "pending": p.warning,
        "baseline": p.phase_rest[1], "round": p.accent,
    }
    c = color_map.get(kind, p.text_tertiary)
    return f"color: {c}; font-size: 12px;"


def qss_panel() -> str:       return qss_card()
def qss_glass_panel() -> str: return qss_card()
def qss_canvas_gradient() -> str: return qss_canvas_ambient()


# ═══════════════════════════════════════════════════════════════ ICONS

ICON_SVGS = {
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
