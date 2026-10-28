"""
Strict layout-fit guard (Phase 3): at 1440×900 and 1280×800, no visible widget
on a page may be clipped — its rect (mapped to the window) must lie inside the
window, and inside its nearest scroll viewport (so nothing is cut off at the
right edge or hidden under a fixed bar).

Legitimate vertical scrolling is allowed: a widget inside a vertical scroll
area may extend below the viewport (you scroll to it). What's NOT allowed is a
widget running off the window, or off the right of its scroll viewport
(horizontal cut-off), or below the window when it is NOT in a scroll area.

Known failures (to be fixed in the Review & Label merge step) are the Review
screen and the old Labelling screen — EEG panes under the play bar, BAND POWER
cut off at 1280. Those are asserted to STILL overflow (so this test reminds us
to flip them to strict once they're fixed); every other page must be clean.

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

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtWidgets import QApplication, QWidget, QAbstractScrollArea

_app = QApplication.instance() or QApplication(sys.argv)

import brain_battle_main as bbm

_failures = []
SIZES = [(1440, 900), (1280, 800)]
TOL = 2   # px tolerance for rounding / sub-pixel borders

# Known-to-overflow pages, fixed in the merge step (DESIGN §6).
KNOWN_OVERFLOW = {"Review & Label", "Old labelling screen"}


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _nearest_scrollarea(w):
    p = w.parent()
    while p is not None:
        if isinstance(p, QAbstractScrollArea):
            return p
        p = p.parent()
    return None


def _overflows(win, page):
    """List visible widgets on `page` that are clipped by the window or by
    their nearest scroll viewport (horizontal), or run below the window while
    not inside a scroll area."""
    W, H = win.width(), win.height()
    out = []
    for w in page.findChildren(QWidget):
        if not w.isVisible() or w.width() <= 0 or w.height() <= 0:
            continue
        reg = w.visibleRegion()
        if reg.isEmpty():
            continue  # fully scrolled out / not shown — fine
        tl = w.mapTo(win, QPoint(0, 0))
        R = QRect(tl, w.size())
        if not R.intersects(QRect(0, 0, W, H)):
            continue  # entirely off-window (scrolled away) — not shown
        sa = _nearest_scrollarea(w)
        in_scroll = sa is not None
        bad = []
        # Off the top/left of the window is always wrong.
        if R.left() < -TOL:
            bad.append("off-left")
        if R.top() < -TOL and not in_scroll:
            bad.append("off-top")
        # Horizontal run-off the window (e.g. BAND POWER cut off at 1280).
        if R.right() > W + TOL:
            bad.append("off-right")
        # Below the window when NOT scrollable there (e.g. under a fixed bar).
        if R.bottom() > H + TOL and not in_scroll:
            bad.append("below-window")
        # Horizontal clip by the nearest scroll viewport.
        if sa is not None:
            vp = sa.viewport()
            vtl = vp.mapTo(win, QPoint(0, 0))
            VR = QRect(vtl, vp.size())
            if R.left() < VR.left() - TOL or R.right() > VR.right() + TOL:
                bad.append("clipped-by-viewport")
        if bad:
            txt = w.text()[:20] if hasattr(w, "text") and callable(w.text) else ""
            out.append((w.__class__.__name__, txt, bad))
    return out


def test_pages_fit():
    win = bbm.BrainBattleApp()
    pages = [(win.IDX_SESSIONS, "Sessions"), (win.IDX_RECORD, "New recording"),
             (win.IDX_REVIEW, "Review & Label"), (win.IDX_SETTINGS, "Settings")]
    for W, H in SIZES:
        win.resize(W, H)
        win.show()
        for _ in range(4):
            _app.processEvents()
        for idx, label in pages:
            win._shell.set_current_index(idx)
            for _ in range(4):
                _app.processEvents()
            page = win._shell.page_widget(idx)
            ov = _overflows(win, page)
            if label in KNOWN_OVERFLOW:
                check(f"[{W}x{H}] '{label}' is a KNOWN overflow (merge-step fix)",
                      len(ov) >= 1, f"{len(ov)} overflowing widgets (expected > 0)")
            else:
                check(f"[{W}x{H}] '{label}' has nothing clipped",
                      len(ov) == 0, f"overflows: {ov[:4]}")

        # The old Labelling screen (reachable via the temporary link) is also a
        # known overflow — check it explicitly.
        win._shell.set_current_index(win.IDX_REVIEW)
        win._review_page._stack.setCurrentIndex(1)  # old Labelling
        for _ in range(4):
            _app.processEvents()
        ov = _overflows(win, win._review_page)
        check(f"[{W}x{H}] 'Old labelling screen' is a KNOWN overflow",
              len(ov) >= 1, f"{len(ov)} overflowing widgets (expected > 0)")
        win._review_page._stack.setCurrentIndex(0)

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
