"""
Strict layout-fit guard: at 1440×900, 1280×800 AND the minimum window size,
no visible widget on a page may be clipped — its rect (mapped to the window)
must lie inside the window, and inside its nearest scroll viewport (so nothing
is cut off at the right edge or hidden under a fixed bar).

Legitimate vertical scrolling is allowed: a widget inside a vertical scroll
area may extend below the viewport (you scroll to it), and a visible scrollbar
is scroll chrome, not an overflow. What's NOT allowed is a widget running off
the window, or off the right of its scroll viewport (horizontal cut-off), or
below the window when it is NOT in a scroll area. Every page must be clean at
all three sizes — there are no exemptions.

Run:  python3 tests/test_layout_fit.py   (nonzero exit on any failure)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Simulated devices so the New recording flow populates (step 1) fast and
# deterministically instead of probing real hardware during a layout check.
os.environ.setdefault("BRAIN_BATTLE_SIMULATE", "1")
import tempfile
os.environ.setdefault("BRAIN_BATTLE_CAPTURE_DIR", tempfile.mkdtemp(prefix="bb_layout_cap_"))
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
# 1440×900 and 1280×800 (DESIGN targets) plus the app's actual minimum window
# size — at the minimum, pages may scroll vertically but must never clip.
SIZES = [(1440, 900), (1280, 800), bbm.MIN_WINDOW]
TOL = 2   # px tolerance for rounding / sub-pixel borders

# Every page must now fit — the merged Review & Label screen (DESIGN §6) has no
# exemption; the old Review/Labelling overflows went away with those screens.
KNOWN_OVERFLOW = set()


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
        # Scrollbars are scroll-area chrome, not content: a visible vertical
        # scrollbar legitimately sits at the viewport edge (the page scrolls
        # rather than clips). Don't treat it as an overflow. Qt's internal
        # gutter CONTAINER for the bar (object name qt_scrollarea_vcontainer/
        # hcontainer) is a plain QWidget, not something named "*ScrollBar*",
        # so it needs its own exclusion — only shows up once a page actually
        # needs to scroll (Settings is the first: DESIGN §7 is a long page).
        if "ScrollBar" in w.__class__.__name__ or \
                w.objectName() in ("qt_scrollarea_vcontainer", "qt_scrollarea_hcontainer"):
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
            check(f"[{W}x{H}] '{label}' has nothing clipped",
                  len(ov) == 0, f"overflows: {ov[:4]}")

    try:
        win.record_flow.shutdown()
        win._review_page.shutdown()
        win._sessions_page.shutdown()   # stop the background scan thread too
        for _ in range(4):
            _app.processEvents()
    except Exception:
        pass


if __name__ == "__main__":
    test_pages_fit()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
