"""
design_tokens.py
────────────────
Design system v4 — "Mission Control × Liquid Glass" — Theme-switchable.

Fresh pass, re-thinking what the previous versions got wrong:

WHAT CHANGED FROM v3
  · Real four-level surface hierarchy (canvas, sidebar, card, elevated)
    with visually distinct treatments, not four nearly-identical shades.
  · Warmer, desaturated neutrals (Linear 2026 refresh school) rather
    than cool blue-tinted grays. The room feels less clinical.
  · Proper Light mode, not a color inversion. Different elevation
    strategy, different accent saturation, different glass opacity.
  · Theme module attribute (`_CURRENT = "dark"`) switchable at runtime
    via `set_theme("light" | "dark")`. All functions read from this at
    call time, not at import.
  · Accent reduced to near-neutral with one brand-blue CTA colour —
    Linear went accent-minimal in 2026 and it reads as more mature.
  · Ambient gradient includes a subtle purple-blue "wallpaper orb" so
    glass panels have something to refract visually.

FOUR SURFACE LEVELS (Linear / Arc / Raycast school)
  canvas        darkest — app body
  sidebar       one step lighter, recessed feel
  card          elevated — workspace panels (where user works)
  raised        brightest — hover, active, dropdown

DARK THEME PALETTE
  Inspired by Warp, Linear dark, Raycast. Warm near-black canvas
  (slight brown-blue undertone), warm surface grays, minimal color.

LIGHT THEME PALETTE
  Not a flip — a separate design. Near-white warm canvas, proper
  elevated cards with subtle shadow (faked via gradient), higher
  contrast text, accents slightly more saturated for visibility.
"""

from __future__ import annotations

from typing import Literal
from PyQt6.QtCore import QEasingCurve
from PyQt6.QtGui  import QFont


# ═══════════════════════════════════════════════════════════════ THEME STATE

Theme = Literal["dark", "light"]
_CURRENT: Theme = "dark"

# Observers — widgets can register a callback to re-style when theme changes.
_observers = []

def set_theme(theme: Theme) -> None:
    """Switch the active theme. Notifies all registered observers."""
    global _CURRENT
    if theme not in ("dark", "light"):
        raise ValueError(f"unknown theme: {theme}")
    if theme == _CURRENT:
        return
    _CURRENT = theme
    for cb in list(_observers):
        try: cb()
        except Exception:
            # Cosmetic only: a failing observer means one widget misses a
            # re-theme; never let it break the switch for the others.
            import logging
            logging.getLogger(__name__).debug("theme observer raised", exc_info=True)

def current_theme() -> Theme:
    return _CURRENT

def on_theme_change(callback) -> None:
    """Register a callback to re-style when theme switches."""
    _observers.append(callback)


# ═══════════════════════════════════════════════════════════════ PALETTES

class _DarkPalette:
    # Surface hierarchy — four visibly distinct levels
    canvas          = "#0E0E12"   # deepest — app body, warm near-black
    sidebar_bg      = "#151519"   # recessed feel
    card_bg         = "#1C1C21"   # elevated workspace panel
    card_bg_hover   = "#24242A"   # brighter on hover
    raised          = "#2B2B33"   # dropdown / popover

    # Borders
    border          = "rgba(255, 255, 255, 0.06)"
    border_strong   = "rgba(255, 255, 255, 0.12)"
    border_active   = "rgba(99, 125, 255, 0.45)"

    # Text
    text_primary    = "#ECEDEE"
    text_secondary  = "#A1A1AA"
    text_tertiary   = "#71717A"
    text_inverse    = "#18181B"

    # Accent — minimal, used only for primary CTA / focus / active nav
    accent          = "#637DFF"   # softer blue than pure azure, Linear-ish
    accent_hover    = "#7A90FF"
    accent_pressed  = "#4E66E5"
    accent_ghost    = "rgba(99, 125, 255, 0.12)"
    accent_border   = "rgba(99, 125, 255, 0.35)"

    # Semantic
    success         = "#10B981"
    warning         = "#F5A524"
    error           = "#F43F5E"   # rose instead of pure red — warmer
    info            = "#06B6D4"   # cyan

    # Ambient orb (glass has something to refract)
    orb_a           = "rgba(99, 125, 255, 0.12)"    # top-right blue
    orb_b           = "rgba(168, 85, 247, 0.07)"    # bottom-left purple


class _LightPalette:
    # Warm-white canvas hierarchy — not a flip of dark
    canvas          = "#F7F7F5"   # near-white, very warm
    sidebar_bg      = "#EEEEEA"
    card_bg         = "#FFFFFF"   # cards POP against canvas
    card_bg_hover   = "#F7F7F5"
    raised          = "#FFFFFF"

    border          = "rgba(0, 0, 0, 0.06)"
    border_strong   = "rgba(0, 0, 0, 0.12)"
    border_active   = "rgba(79, 70, 229, 0.45)"

    text_primary    = "#18181B"
    text_secondary  = "#52525B"
    text_tertiary   = "#A1A1AA"
    text_inverse    = "#FFFFFF"

    # Accent slightly more saturated for light-mode visibility
    accent          = "#4F46E5"   # indigo, holds up on white
    accent_hover    = "#6366F1"
    accent_pressed  = "#3730A3"
    accent_ghost    = "rgba(79, 70, 229, 0.08)"
    accent_border   = "rgba(79, 70, 229, 0.35)"

    success         = "#059669"
    warning         = "#D97706"
    error           = "#DC2626"
    info            = "#0891B2"

    orb_a           = "rgba(79, 70, 229, 0.08)"
    orb_b           = "rgba(219, 39, 119, 0.05)"


def _p():
    """Return the active palette."""
    return _DarkPalette if _CURRENT == "dark" else _LightPalette


# ─── Convenience accessors that re-resolve at call time ───────────────────
def canvas_bg():      return _p().canvas
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
def info_color():     return _p().info

def phase_colors():
    p = _p()
    return {
        "idle":     p.text_tertiary,
        "baseline": p.info,
        "round":    p.accent,
        "break":    p.warning,
        "done":     p.success,
    }


# ═══════════════════════════════════════════════════════════════ TYPE

FONT_UI   = "'Inter', 'SF Pro Text', '-apple-system', 'Segoe UI', Roboto, sans-serif"
FONT_MONO = "'JetBrains Mono', 'SF Mono', 'Menlo', 'Consolas', 'Courier New', monospace"

# Sizes — more dramatic scale than v3
SIZE_HERO    = 48   # The Timer. Main draw.
SIZE_DISPLAY = 28
SIZE_LG      = 16
SIZE_MD      = 13
SIZE_SM      = 12
SIZE_XS      = 11
SIZE_CAPTION = 10

LS_TIGHT  = "-0.5px"
LS_NORMAL = "0"
LS_WIDE   = "0.4px"
LS_WIDER  = "1.8px"

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

RADIUS_SM   = 6
RADIUS_MD   = 10
RADIUS_LG   = 16
RADIUS_XL   = 20
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
            background-color: {p.raised};
            color: {p.text_primary};
            border: 1px solid {p.border_strong};
            border-radius: {RADIUS_SM}px;
            padding: 6px 10px;
            font-family: {FONT_UI};
            font-size: {SIZE_XS}px;
        }}
        QSplitter::handle {{ background: transparent; }}
        QSplitter::handle:hover {{ background: {p.accent_ghost}; }}
        QScrollBar:vertical {{
            background: transparent; width: 10px; margin: 4px 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {p.border_strong};
            border-radius: 5px; min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {p.text_tertiary}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
        QScrollBar:horizontal {{
            background: transparent; height: 10px; margin: 2px 4px;
        }}
        QScrollBar::handle:horizontal {{
            background: {p.border_strong};
            border-radius: 5px; min-width: 30px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {p.text_tertiary}; }}
        QMenu {{
            background-color: {p.raised};
            border: 1px solid {p.border_strong};
            border-radius: {RADIUS_MD}px;
            color: {p.text_primary};
            padding: 6px;
        }}
        QMenu::item {{ padding: 8px 16px; border-radius: {RADIUS_SM}px; }}
        QMenu::item:selected {{ background: {p.accent_ghost}; color: {p.accent}; }}
    """


def qss_canvas_ambient() -> str:
    """
    Full-width canvas with a single soft accent orb glowing in the upper-right.
    Gives glass panels something to "catch" visually — without going full
    glassmorphism-neon territory.
    """
    p = _p()
    if _CURRENT == "dark":
        return f"""
            QWidget#canvas {{
                background: qradialgradient(
                    cx:0.85, cy:0.2, radius:0.9, fx:0.85, fy:0.2,
                    stop:0 rgba(99, 125, 255, 0.12),
                    stop:0.5 rgba(99, 125, 255, 0.03),
                    stop:1 {p.canvas}
                );
            }}
        """
    return f"""
        QWidget#canvas {{
            background: qradialgradient(
                cx:0.85, cy:0.15, radius:0.9, fx:0.85, fy:0.15,
                stop:0 rgba(79, 70, 229, 0.06),
                stop:0.5 rgba(79, 70, 229, 0.02),
                stop:1 {p.canvas}
            );
        }}
    """


def qss_card() -> str:
    """
    Elevated card — noticeably brighter than canvas, with a subtle
    specular highlight on top. The hero glass surface.
    """
    p = _p()
    if _CURRENT == "dark":
        return f"""
            QFrame#card {{
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(38, 38, 44, 0.85),
                    stop:0.03 rgba(28, 28, 33, 0.95),
                    stop:1 rgba(20, 20, 24, 0.92)
                );
                border: 1px solid {p.border};
                border-top: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: {RADIUS_LG}px;
            }}
        """
    return f"""
        QFrame#card {{
            background: {p.card_bg};
            border: 1px solid {p.border};
            border-radius: {RADIUS_LG}px;
        }}
    """


def qss_sidebar() -> str:
    p = _p()
    if _CURRENT == "dark":
        sb_bg = f"""
            background: qlineargradient(
                x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(21, 21, 25, 0.98),
                stop:1 rgba(17, 17, 20, 0.98)
            );
        """
    else:
        sb_bg = f"background: {p.sidebar_bg};"

    return f"""
        QWidget#sidebar {{
            {sb_bg}
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
        QPushButton#sidebarItem:hover {{
            background: {p.card_bg_hover};
            color: {p.text_primary};
        }}
        QPushButton#sidebarItem:checked {{
            background: {p.accent_ghost};
            color: {p.accent};
            font-weight: 600;
        }}
    """


def qss_primary_button() -> str:
    p = _p()
    return f"""
        QPushButton {{
            background: {p.accent};
            color: {p.text_inverse if _CURRENT == 'light' else '#FFFFFF'};
            border: 1px solid {p.accent};
            border-radius: {RADIUS_MD}px;
            padding: 10px 22px;
            font-family: {FONT_UI};
            font-size: {SIZE_MD}px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: {p.accent_hover};
            border-color: {p.accent_hover};
        }}
        QPushButton:pressed {{
            background: {p.accent_pressed};
            border-color: {p.accent_pressed};
        }}
        QPushButton:disabled {{
            background: {p.card_bg};
            border-color: {p.border};
            color: {p.text_tertiary};
        }}
    """


def qss_secondary_button() -> str:
    p = _p()
    return f"""
        QPushButton {{
            background: transparent;
            color: {p.text_primary};
            border: 1px solid {p.border_strong};
            border-radius: {RADIUS_MD}px;
            padding: 9px 20px;
            font-family: {FONT_UI};
            font-size: {SIZE_MD}px;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background: {p.accent_ghost};
            border-color: {p.accent};
            color: {p.accent};
        }}
        QPushButton:disabled {{ color: {p.text_tertiary}; border-color: {p.border}; }}
    """


def qss_danger_button() -> str:
    p = _p()
    return f"""
        QPushButton {{
            background: transparent;
            color: {p.error};
            border: 1px solid {p.error};
            border-radius: {RADIUS_MD}px;
            padding: 9px 20px;
            font-family: {FONT_UI};
            font-size: {SIZE_MD}px;
            font-weight: 600;
        }}
        QPushButton:hover {{ background: {p.error}; color: #FFFFFF; }}
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
        QPushButton:hover {{
            background: {p.card_bg_hover};
            color: {p.text_primary};
        }}
        QPushButton:checked {{
            background: {p.accent_ghost};
            color: {p.accent};
        }}
    """


def qss_chip(active: bool = False) -> str:
    p = _p()
    if active:
        return f"""
            QPushButton {{
                background: {p.accent_ghost};
                color: {p.accent};
                border: 1px solid {p.accent_border};
                border-radius: {RADIUS_SM}px;
                padding: 5px 14px;
                font-family: {FONT_UI};
                font-size: {SIZE_XS}px;
                font-weight: 600;
                letter-spacing: {LS_WIDE};
            }}
        """
    return f"""
        QPushButton {{
            background: transparent;
            color: {p.text_secondary};
            border: 1px solid {p.border_strong};
            border-radius: {RADIUS_SM}px;
            padding: 5px 14px;
            font-family: {FONT_UI};
            font-size: {SIZE_XS}px;
            letter-spacing: {LS_WIDE};
        }}
        QPushButton:hover {{ color: {p.text_primary}; border-color: {p.text_secondary}; }}
    """


def qss_input() -> str:
    p = _p()
    return f"""
        QLineEdit, QSpinBox, QDoubleSpinBox {{
            background: {p.card_bg};
            color: {p.text_primary};
            border: 1px solid {p.border_strong};
            border-radius: {RADIUS_MD}px;
            padding: 10px 14px;
            font-family: {FONT_MONO};
            font-size: {SIZE_MD}px;
            selection-background-color: {p.accent_ghost};
            selection-color: {p.text_primary};
        }}
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
            border: 1px solid {p.accent};
            background: {p.accent_ghost};
        }}
    """


def qss_section_label() -> str:
    p = _p()
    return (
        f"color: {p.text_tertiary}; font-family: {FONT_UI}; "
        f"font-size: {SIZE_CAPTION}px; letter-spacing: {LS_WIDER}; "
        f"font-weight: 700;"
    )


def qss_brand_label() -> str:
    p = _p()
    return (
        f"color: {p.accent}; font-family: {FONT_UI}; "
        f"font-size: {SIZE_CAPTION}px; letter-spacing: {LS_WIDER}; font-weight: 700;"
    )


def qss_data_readout(size: int = SIZE_SM) -> str:
    p = _p()
    return (
        f"color: {p.text_primary}; font-family: {FONT_MONO}; "
        f"font-size: {size}px; letter-spacing: {LS_NORMAL};"
    )


def qss_display_number() -> str:
    p = _p()
    return (
        f"color: {p.text_primary}; font-family: {FONT_MONO}; "
        f"font-size: {SIZE_HERO}px; font-weight: 300; letter-spacing: {LS_TIGHT};"
    )


def qss_status_pill(kind: str = "idle") -> str:
    p = _p()
    palette = {
        "live":       (p.accent, p.accent_ghost),
        "recording":  (p.error, f"rgba(244, 63, 94, 0.12)" if _CURRENT == "dark" else "rgba(220, 38, 38, 0.10)"),
        "idle":       (p.text_tertiary, f"rgba(255, 255, 255, 0.04)" if _CURRENT == "dark" else "rgba(0, 0, 0, 0.04)"),
        "connected":  (p.success, f"rgba(16, 185, 129, 0.12)"),
        "warning":    (p.warning, f"rgba(245, 165, 36, 0.12)"),
        "baseline":   (p.info, f"rgba(6, 182, 212, 0.12)"),
        "round":      (p.accent, p.accent_ghost),
        "break":      (p.warning, f"rgba(245, 165, 36, 0.12)"),
        "done":       (p.success, f"rgba(16, 185, 129, 0.12)"),
    }
    fg, bg = palette.get(kind, palette["idle"])
    return (
        f"background: {bg}; color: {fg}; border: 1px solid {fg}; "
        f"border-radius: {RADIUS_FULL}px; padding: 4px 12px; "
        f"font-family: {FONT_UI}; font-size: {SIZE_XS}px; "
        f"font-weight: 600; letter-spacing: {LS_WIDE};"
    )


def status_dot_css(kind: str = "idle") -> str:
    p = _p()
    color_map = {
        "idle":      p.text_tertiary, "connected": p.success, "live": p.success,
        "recording": p.error, "error": p.error, "pending": p.warning,
        "baseline":  p.info, "round": p.accent,
    }
    c = color_map.get(kind, p.text_tertiary)
    return f"color: {c}; font-size: 12px;"


def qss_panel() -> str:
    return qss_card()


def qss_glass_panel() -> str:
    return qss_card()


def qss_canvas_gradient() -> str:
    return qss_canvas_ambient()


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
    "sun":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/>'
        '<path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/>'
        '<path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/></svg>',
    "moon":
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>',
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


# ═══════════════════════════════════════════════════════════════ LEGACY

# Module-level palette constants for legacy modules that imported
# individual names. These resolve once at import time but are fine for
# colors that don't change between themes (phase semantic colors, etc.)
# For theme-aware code, use the functions above.

class _LegacyAccessors:
    """Back-compat attribute access that resolves lazily."""
    def __getattr__(self, name):
        mapping = {
            "SANGRIA": error_color, "SANGRIA_HOVER": error_color,
            "SANGRIA_DEEP": error_color, "SANGRIA_GHOST": lambda: "rgba(244,63,94,0.14)",
            "AZURE": accent, "AZURE_BRIGHT": accent_hover,
            "AZURE_DEEP": accent_pressed, "AZURE_GHOST": accent_ghost,
            "PRUSSIAN": lambda: "#00204D",
            "OFF_WHITE": text_primary, "DIM": text_secondary, "DIMMER": text_tertiary,
            "MUTED": border_strong, "HAIRLINE": border, "HAIRLINE_HOVER": border_strong,
            "TEXT_PRIMARY": text_primary, "TEXT_SECONDARY": text_secondary,
            "TEXT_TERTIARY": text_tertiary, "TEXT_INVERSE": text_inverse,
            "SUCCESS": success_color, "WARNING": warning_color,
            "ERROR": error_color, "INFO": info_color,
            "BG_0": canvas_bg, "BG_1": sidebar_bg, "BG_2": card_bg, "BG_3": card_bg_hover,
            "CANVAS_FLAT": canvas_bg, "CANVAS_TOP": canvas_bg, "CANVAS_BOTTOM": canvas_bg,
            "SURFACE_1": sidebar_bg, "SURFACE_2": card_bg, "SURFACE_3": card_bg_hover,
            "GLASS_LOW": card_bg, "GLASS_MID": card_bg, "GLASS_HIGH": card_bg_hover,
        }
        if name in mapping:
            return mapping[name]()
        raise AttributeError(name)

import sys as _sys
_m = _sys.modules[__name__]
_orig_getattr = getattr(_m, "__getattr__", None)
_legacy = _LegacyAccessors()

def __getattr__(name):
    """Module-level __getattr__ for legacy compatibility."""
    if _orig_getattr is not None:
        try: return _orig_getattr(name)
        except AttributeError: pass
    try: return getattr(_legacy, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Constants that are truly constant (don't depend on theme)
FONT_SANS  = FONT_UI
LS_MEDIUM  = "0.4px"
PHASE_COLORS = phase_colors()   # snapshot — older modules read this once
