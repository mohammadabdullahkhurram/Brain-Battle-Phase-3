"""
Light-only design-system guard (Phase 3, Part C).

Fails if any dark-theme machinery survives: a dark palette or theme-switch API
in design_tokens, a theme toggle, or any per-widget `_apply_theme` hex map.
Also sanity-checks that the core surfaces are actually light.

Run:  python3 tests/test_light_theme.py   (nonzero exit on any failure)
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

import design_tokens as dt

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _lum(hexv):
    hexv = hexv.lstrip("#")
    r, g, b = int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_no_theme_api():
    for attr in ("set_theme", "current_theme", "on_theme_change"):
        check(f"design_tokens has no {attr}()", not hasattr(dt, attr))
    for attr in ("_DarkPalette", "_CURRENT"):
        check(f"design_tokens has no {attr}", not hasattr(dt, attr))


def test_app_source_has_no_theme_machinery():
    # No first-party module may reference the removed theme machinery.
    forbidden = ["on_theme_change", "_apply_theme", "_DarkPalette",
                 "set_theme(", "current_theme(", "_toggle_theme",
                 "brain_battle_prefs"]
    py_files = [f for f in glob.glob(os.path.join(ROOT, "*.py"))]
    py_files += glob.glob(os.path.join(ROOT, "sync_utils", "*.py"))
    py_files += glob.glob(os.path.join(ROOT, "tools", "*.py"))
    offenders = {}
    for fn in py_files:
        src = open(fn, encoding="utf-8").read()
        hits = [tok for tok in forbidden if tok in src]
        if hits:
            offenders[os.path.relpath(fn, ROOT)] = hits
    check("no first-party module references theme machinery",
          not offenders, str(offenders))


def test_design_tokens_source_has_no_dark_palette():
    src = open(os.path.join(ROOT, "design_tokens.py"), encoding="utf-8").read()
    # The old dark canvas / card near-blacks must be gone from the token file.
    for dark in ("#0E0E12", "#1C1C21", "#151519", '"dark"'):
        check(f"design_tokens.py has no {dark}", dark not in src)


def test_core_surfaces_are_light():
    check("canvas is light", _lum(dt.canvas_bg()) > 200, dt.canvas_bg())
    check("card is light", _lum(dt.card_bg()) > 200, dt.card_bg())
    check("primary text is dark (readable on light)",
          _lum(dt.text_primary()) < 80, dt.text_primary())
    check("secondary text meets ~AA on canvas",
          _lum(dt.text_secondary()) < 120, dt.text_secondary())


if __name__ == "__main__":
    test_no_theme_api()
    test_app_source_has_no_theme_machinery()
    test_design_tokens_source_has_no_dark_palette()
    test_core_surfaces_are_light()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
