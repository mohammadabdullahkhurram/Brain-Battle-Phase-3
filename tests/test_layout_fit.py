"""
Layout-fit guard (Phase 3, Part C): at 1440×900 and 1280×800, every page's
minimum size must fit the content area — nothing clipped or pushed off-screen.

Run:  python3 tests/test_layout_fit.py   (nonzero exit on any failure)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    import vlc  # noqa: F401
except Exception:
    import types
    sys.modules["vlc"] = types.ModuleType("vlc")

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

import brain_battle_main as bbm

_failures = []
SIZES = [(1440, 900), (1280, 800)]


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def test_pages_fit():
    win = bbm.BrainBattleApp()
    pages = [(win.IDX_SESSIONS, "Sessions"), (win.IDX_RECORD, "New recording"),
             (win.IDX_REVIEW, "Review & Label"), (win.IDX_SETTINGS, "Settings")]
    for W, H in SIZES:
        win.resize(W, H)
        win.show()
        for _ in range(3):
            _app.processEvents()
        # The window must not demand more than the target size.
        wm = win.minimumSizeHint()
        check(f"window minimum fits {W}x{H}",
              wm.width() <= W and wm.height() <= H,
              f"min={wm.width()}x{wm.height()}")
        stack = win._shell._stack
        for idx, label in pages:
            win._shell.set_current_index(idx)
            for _ in range(3):
                _app.processEvents()
            page = win._shell.page_widget(idx)
            mh = page.minimumSizeHint()
            cw, ch = stack.width(), stack.height()
            fits_w = mh.width() <= 0 or mh.width() <= cw + 1
            fits_h = mh.height() <= 0 or mh.height() <= ch + 1
            check(f"[{W}x{H}] '{label}' fits the content area",
                  fits_w and fits_h,
                  f"page min={mh.width()}x{mh.height()} content={cw}x{ch}")
    try:
        win.live_tab.shutdown(); win.sync_tab.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    test_pages_fit()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
