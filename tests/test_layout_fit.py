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
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401  (temp settings file + a labeller name; see tests/_env.py)
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


def _fixture_sessions(base):
    """A few recordings (manifests only) so Sessions has rows to check — the
    Open button was clipped at 1440×900 and 1280×800 in the owner's test."""
    import json
    rows = [("K7Q2", "Red", "M3XA", "Blue", {}), ("R2DT", "Black", "B9LN", "Blue",
            {"interrupted": True, "stopped_early": True, "stopped_t_session": 387.0}),
            ("Z8PC", "Blue", "J4WE", "Red", {"stopped_early": True, "stopped_t_session": 200.0})]
    for i, (a, ca, b, cb, extra) in enumerate(rows):
        root = os.path.join(base, f"2026-11-2{i} 10-0{i} {a} vs {b}")
        os.makedirs(root, exist_ok=True)
        man = {"app": "Brain Battle", "version": "3.1.0-dev",
               "started_at": f"2026-11-2{i}T10:0{i}:00", "started_unix": 1795600000.0 + i * 86400,
               "boxers": {"A": {"code": a, "colour": ca, "colour_hex": "#D3241B"},
                          "B": {"code": b, "colour": cb, "colour_hex": "#1B45C4"}},
               "config": {"num_rounds": 3, "round_sec": 180, "break_sec": 60, "baseline_sec": 60},
               "lead_in_sec": 15, "moments": [{"t_session_seconds": 40.0, "note": ""}],
               **extra}
        with open(os.path.join(root, "session_manifest.json"), "w", encoding="utf-8") as f:
            json.dump(man, f)


_fixture_sessions(os.environ["BRAIN_BATTLE_CAPTURE_DIR"])

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtWidgets import QApplication, QWidget, QAbstractScrollArea, QLabel

_app = QApplication.instance() or QApplication(sys.argv)

import brain_battle_main as bbm

_failures = []
# 1440×900 and 1280×800 (DESIGN targets) plus the app's actual minimum window
# size — at the minimum, pages may scroll vertically but must never clip.
SIZES = [(1440, 900), (1280, 800), bbm.MIN_WINDOW]
TOL = 2   # px tolerance for rounding / sub-pixel borders

# Every page must now fit — the merged Review & Label screen (DESIGN “Review & Label”) has no
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
        # needs to scroll (Settings is the first: DESIGN “Settings” is a long page).
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
        # Wrapped text squeezed shorter than it needs: the end is cut off
        # (the poor-signal help on Get ready at 1280×800).
        if isinstance(w, QLabel) and w.wordWrap() and w.text() and \
                w.heightForWidth(w.width()) > w.height() + 3:
            bad.append(f"text-cut-off({w.height()}<{w.heightForWidth(w.width())})")
        if bad:
            txt = w.text()[:20] if hasattr(w, "text") and callable(w.text) else ""
            out.append((w.__class__.__name__, txt, bad))
    return out


def test_pages_fit():
    win = bbm.BrainBattleApp()
    pages = [(win.IDX_SESSIONS, "Sessions"), (win.IDX_RECORD, "New recording"),
             (win.IDX_REVIEW, "Review & Label"), (win.IDX_DOCS, "Documentation"),
             (win.IDX_SETTINGS, "Settings")]
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
            if idx == win.IDX_SESSIONS:
                for _ in range(40):                 # the folder scan runs off the UI thread
                    _app.processEvents()
                    if win._sessions_page.rows():
                        break
                    import time; time.sleep(0.05)
                check(f"[{W}x{H}] Sessions shows the fixture rows",
                      len(win._sessions_page.rows()) >= 3, str(len(win._sessions_page.rows())))
            ov = _overflows(win, page)
            check(f"[{W}x{H}] '{label}' has nothing clipped",
                  len(ov) == 0, f"overflows: {ov[:4]}")
            if idx == win.IDX_SESSIONS and win._sessions_page.rows():
                sp = win._sessions_page
                sp.show_history(sp.rows()[0].info)
                for _ in range(4):
                    _app.processEvents()
                rows_ok = all(_overflows(win, r) == [] for r in sp.rows())
                check(f"[{W}x{H}] the history drawer slides over the rows (Open never clipped)",
                      rows_ok and sp._drawer.isVisible()
                      and sp._drawer.geometry().right() <= sp.width() + TOL)
                sp.close_history()

    try:
        win.record_flow.shutdown()
        win._review_page.shutdown()
        win._sessions_page.shutdown()   # stop the background scan thread too
        for _ in range(4):
            _app.processEvents()
    except Exception:
        pass


def test_owner_screenshot_details():
    """The small things from the owner's screenshots (2026-11-23)."""
    import numpy as np
    from brain_battle.capture.simulation import SimCapture
    cap = SimCapture(200, w=960, h=540, fps=60, stem="SIDE_01")
    ok, frame = cap.read()
    h, w = frame.shape[:2]
    text_col = np.array([170, 180, 200])
    def text_px(region):
        return int((np.abs(region.astype(int) - text_col).sum(axis=2) < 40).sum())
    check("simulated camera: no text where the app draws the camera's name (top left)",
          ok and text_px(frame[: int(h * 0.25), : int(w * 0.5)]) == 0)
    check("simulated camera: no text where the app draws the state and timecode (bottom)",
          ok and text_px(frame[int(h * 0.75):, :]) == 0)
    check("simulated camera: its small text sits in the top-right corner",
          ok and text_px(frame[: int(h * 0.2), int(w * 0.5):]) > 0)
    # Settings › headbands: neutral A / B badges, not the old teal / orange
    from brain_battle.ui.settings_screen import SettingsScreen
    import brain_battle.ui.design_tokens as dt
    st = SettingsScreen()
    badges = [l for l in st.findChildren(QLabel) if l.objectName() in ("nBadgeA", "nBadgeB")]
    old = {dt.boxer_colors("A")[0].lower(), dt.boxer_colors("B")[0].lower()}
    check("Settings › headbands: neutral Boxer A / B badges",
          len(badges) == 2 and not any(c in b.styleSheet().lower() for b in badges for c in old))


def _ink_rows(img, rect):
    """Top and bottom row (in ``img``) of anything drawn inside ``rect`` —
    pixels that differ clearly from the rect's corner (the background)."""
    bg = img.pixelColor(rect.left(), rect.top())
    rows = []
    for y in range(max(0, rect.top()), min(img.height(), rect.bottom() + 1)):
        for x in range(max(0, rect.left()), min(img.width(), rect.right() + 1)):
            c = img.pixelColor(x, y)
            if abs(c.red() - bg.red()) + abs(c.green() - bg.green()) + abs(c.blue() - bg.blue()) > 120:
                rows.append(y); break
    return (rows[0], rows[-1]) if rows else None


def test_icons_on_the_text_line():
    """An icon and its text sit on one line ("✓ All labels saved" had its tick
    above the words): the icon's middle within 3 px of the middle of the first
    line of text — in the Review header (Saved / Saving…), and in a status
    line or banner whose text wraps."""
    from PyQt6.QtWidgets import QWidget, QHBoxLayout
    import brain_battle.ui.widgets as ui
    import brain_battle.ui.design_tokens as dt

    def first_line_mid(w):
        """(icon middle, first text line middle) in ``w``'s own pixels."""
        img = w.grab().toImage()
        ic, tx = w._icon, w._text
        ir = QRect(ic.mapTo(w, QPoint(0, 0)), ic.size())
        tr = QRect(tx.mapTo(w, QPoint(0, 0)), tx.size())
        i, t = _ink_rows(img, ir), _ink_rows(img, tr)
        if i is None or t is None:
            return None
        line = tx.fontMetrics().height()
        t_bottom = min(t[1], t[0] + int(line * 0.8))        # first line only
        return (i[0] + i[1]) / 2, (t[0] + t_bottom) / 2

    win = bbm.BrainBattleApp(); win.resize(1280, 800); win.show()
    win._shell.set_current_index(win.IDX_REVIEW)
    for _ in range(4):
        _app.processEvents()
    try:
        sl = win._review_page._autosave
        for text, kind in (("All labels saved", "ok"), ("Saving…", "info")):
            sl.set(text, kind); _app.processEvents()
            m = first_line_mid(sl)
            check(f"Review header: \"{text}\" icon and text on one line",
                  m is not None and abs(m[0] - m[1]) <= 3, str(m))
        host = QWidget(); host.resize(420, 120)
        hv = QHBoxLayout(host)
        long = ("The recordings folder can't be reached — check the network drive is "
                "connected, then try again.")
        widgets = [ui.StatusLine(long, "bad", size=dt.SIZE_SM),
                   ui.StatusLine("Short", "ok"), ui.Banner("warn")]
        widgets[2].set_text(long)
        for w in widgets:
            hv.addWidget(w)
        host.show(); _app.processEvents()
        for w in widgets:
            m = first_line_mid(w)
            check(f"{type(w).__name__} \"{w.text()[:20]}…\": the icon is level with the first line",
                  m is not None and abs(m[0] - m[1]) <= 3, str(m))
        host.close()
    finally:
        win.record_flow.shutdown(); win._review_page.shutdown(); win._sessions_page.shutdown()
        win.close()


if __name__ == "__main__":
    test_pages_fit()
    test_icons_on_the_text_line()
    test_owner_screenshot_details()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
