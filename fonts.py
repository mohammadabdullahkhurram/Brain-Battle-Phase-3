"""
fonts.py
────────
Registers the bundled Inter font (SIL OFL 1.1 — see assets/fonts/Inter-OFL.txt)
with Qt so the UI uses it regardless of what's installed on the machine. If the
bundled files can't be loaded for any reason, the app simply falls back to the
system UI font — font loading must never block startup.

Call ``register_fonts()`` once, after the QApplication exists.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
_INTER_FILES = (
    "Inter-Regular.ttf",
    "Inter-Medium.ttf",
    "Inter-SemiBold.ttf",
    "Inter-Bold.ttf",
)

_registered = False


def register_fonts() -> bool:
    """Load the bundled Inter weights into Qt's font database. Returns True if
    at least one weight loaded. Safe to call repeatedly (loads once)."""
    global _registered
    if _registered:
        return True
    try:
        from PyQt6.QtGui import QFontDatabase
    except Exception as e:
        log.warning("Qt font database unavailable (%s) — using system font", e)
        return False
    loaded = 0
    for name in _INTER_FILES:
        path = os.path.join(_FONT_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            fid = QFontDatabase.addApplicationFont(path)
            if fid != -1:
                loaded += 1
            else:
                log.warning("Inter font %s failed to register", name)
        except Exception as e:
            log.warning("could not register Inter font %s: %s", name, e)
    if loaded:
        _registered = True
        log.info("registered %d Inter weight(s) from %s", loaded, _FONT_DIR)
    else:
        log.warning("no Inter fonts loaded from %s — falling back to system font",
                    _FONT_DIR)
    return _registered


__all__ = ["register_fonts"]
