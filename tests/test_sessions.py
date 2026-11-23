"""
Part 2 — Sessions screen (DESIGN §1) acceptance tests.

Covers:
  • discovery on a --library demo folder (statuses classified, a damaged
    manifest surfaces as an "unreadable" row, fighter codes + dates parsed)
  • folder naming (sanitize slashes/colons/emoji/long/reserved, collisions,
    no-name)
  • search / filter / sort logic (pure helpers)
  • interrupted banner shows once and dismissal persists across a "restart"
    (re-read from disk)
  • performance: scanning 200 manifests completes promptly and the real
    background scan worker delivers off the UI thread
  • strict layout: the populated Sessions screen has nothing clipped at
    1440×900 and 1280×800

generate_library() uses a ProcessPoolExecutor (spawn); each spawned child
re-imports this module, so the demo library is built exactly ONCE inside the
__main__ guard (children never run it) and cheap per-test variants are made
with shutil.copytree (no subprocesses). Building it at module top level would
recurse; building it after a Qt QThread has started would deadlock.

Run:  python3 tests/test_sessions.py   (nonzero exit on any failure)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401  (temp settings file + a labeller name; see tests/_env.py)
import os
import shutil
import sys
import tempfile
import time
import datetime as _dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import vlc  # noqa: F401
except Exception:
    import types
    sys.modules["vlc"] = types.ModuleType("vlc")

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtWidgets import (
    QApplication, QWidget, QAbstractScrollArea, QMainWindow,
    QLabel, QPushButton, QFrame,
)

_app = QApplication.instance() or QApplication(sys.argv)

import brain_battle.data.session_storage as ss
import brain_battle.ui.sessions_screen as st
from brain_battle.capture.session_controller import SessionConfig

_failures = []
BASE_LIB = None   # the one generated demo library (set in __main__)


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _write_damaged(base, name="2026-10-20 10-00 broken session"):
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "session_manifest.json"), "w") as f:
        f.write("{ this is not valid json ::: ")
    return d


def _copy_lib(suffix):
    dst = tempfile.mkdtemp(prefix=f"bb_{suffix}_")
    # copy the demo sessions into the fresh dir (copytree needs a non-existing
    # leaf, so copy each child folder)
    for entry in os.listdir(BASE_LIB):
        src = os.path.join(BASE_LIB, entry)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dst, entry))
    return dst


# ── 1. discovery ────────────────────────────────────────────────────────────

def test_discovery():
    base = _copy_lib("disc")
    _write_damaged(base)
    rows = ss.list_sessions(base)

    by_status = {}
    for r in rows:
        by_status.setdefault(r.status, []).append(r)

    check("discovery: 7 sessions found (6 demo + 1 damaged)", len(rows) == 7,
          f"{len(rows)}")
    check("discovery: one interrupted", len(by_status.get(ss.STATUS_INTERRUPTED, [])) == 1)
    check("discovery: one exported", len(by_status.get(ss.STATUS_EXPORTED, [])) == 1)
    check("discovery: two labelled", len(by_status.get(ss.STATUS_LABELLED, [])) == 2)
    check("discovery: two needs-labelling (incl. the stopped-early one)",
          len(by_status.get(ss.STATUS_NEEDS_LABELLING, [])) == 2)
    check("discovery: damaged manifest surfaces as 'unreadable' (never hidden)",
          len(by_status.get(ss.STATUS_UNREADABLE, [])) == 1)

    intr = next((r for r in rows if r.boxer_a == "M3XA"), None)
    check("discovery: fighter codes parsed (M3XA vs Z8PC)",
          intr is not None and intr.boxer_b == "Z8PC",
          f"{intr.boxer_a if intr else None}/{intr.boxer_b if intr else None}")
    import brain_battle.data.fighters as _f
    check("discovery: fighter colours parsed (blue vs black)",
          intr is not None and (intr.colour_a, intr.colour_b) == (_f.colour_hex("Blue"),
                                                                  _f.colour_hex("Black")))
    check("discovery: interrupted session carries a stopped time",
          intr is not None and intr.stopped_sec is not None, f"{intr.stopped_sec if intr else None}")
    exported = by_status.get(ss.STATUS_EXPORTED, [None])[0]
    check("discovery: exported session has >= 1 label", exported and exported.labels_count >= 1,
          f"{exported.labels_count if exported else None}")
    import brain_battle.data.fighters as _fighters
    check("discovery: demo fighters are codes only (no names)",
          all(_fighters.code_ok(r.boxer_a) and _fighters.code_ok(r.boxer_b)
              for r in rows if r.readable))
    stopped = [r for r in rows if r.readable and r.stopped_sec is not None
               and r.status == ss.STATUS_NEEDS_LABELLING]
    check("discovery: the stopped-early recording carries its stop time", len(stopped) == 1)
    check("discovery: demo sessions carry the demo flag",
          all(r.demo for r in rows if r.readable),
          f"{sum(1 for r in rows if r.readable and r.demo)}/{sum(1 for r in rows if r.readable)}")
    check("discovery: newest-first ordering (started_unix descending)",
          all(rows[i].started_unix >= rows[i + 1].started_unix for i in range(len(rows) - 1)
              if rows[i].readable and rows[i + 1].readable))


# ── 2. folder naming ──────────────────────────────────────────────────────────

def test_folder_naming():
    check("naming: slashes/colons dropped",
          "/" not in ss.sanitize_name("Sam/Alex: fight") and ":" not in ss.sanitize_name("a:b"),
          ss.sanitize_name("Sam/Alex: fight"))
    check("naming: emoji kept", "🥊" in ss.sanitize_name("Sam 🥊"))
    check("naming: capped at 40 chars", len(ss.sanitize_name("Z" * 100)) == 40)
    check("naming: Windows-reserved name escaped", ss.sanitize_name("CON") not in ("CON", "con"),
          ss.sanitize_name("CON"))
    check("naming: empty after sanitize → empty string", ss.sanitize_name("///") == "",
          repr(ss.sanitize_name("///")))

    d = _dt.datetime(2026, 10, 29, 16, 20)
    check("naming: both codes", ss._session_folder_name(d, "K7Q2", "M3XA") == "2026-10-29 16-20 K7Q2 vs M3XA")
    check("naming: one code", ss._session_folder_name(d, "K7Q2", "") == "2026-10-29 16-20 K7Q2")
    check("naming: no codes → timestamp only", ss._session_folder_name(d, "", "") == "2026-10-29 16-20")

    base = tempfile.mkdtemp(prefix="bb_name_")
    cfg = SessionConfig()
    l1 = ss.create_session(base, cfg, started_at=d, boxer_a="K7Q2", boxer_b="M3XA")
    l2 = ss.create_session(base, cfg, started_at=d, boxer_a="K7Q2", boxer_b="M3XA")
    l3 = ss.create_session(base, cfg, started_at=d, boxer_a="K7Q2", boxer_b="M3XA")
    names = sorted(os.path.basename(str(x.root)) for x in (l1, l2, l3))
    check("naming: collisions get (2)/(3), originals not renamed",
          names == ["2026-10-29 16-20 K7Q2 vs M3XA",
                    "2026-10-29 16-20 K7Q2 vs M3XA (2)",
                    "2026-10-29 16-20 K7Q2 vs M3XA (3)"], names)


# ── 3. search / filter / sort ─────────────────────────────────────────────────

def test_filter_sort():
    rows = ss.list_sessions(BASE_LIB)

    counts = st.status_counts(rows)
    check("chips: counts (all=6, needs=2, labelled=2, exported=1, interrupted=1)",
          counts == {"all": 6, "needs_labelling": 2, "labelled": 2,
                     "exported": 1, "interrupted": 1}, str(counts))

    check("search: by fighter code 'j4we' → 2 (case-insensitive)",
          len(st.filter_sort_sessions(rows, query="j4we")) == 2)
    check("search: by fighter code 'R2DT' → 2",
          len(st.filter_sort_sessions(rows, query="R2DT")) == 2)
    # dates are relative to now, so derive a real one from the library
    some_date = next(r.started_at[:10] for r in rows if r.started_at)
    want = sum(1 for r in rows if (r.started_at or "")[:10] == some_date)
    check(f"search: by ISO date {some_date} → {want}",
          len(st.filter_sort_sessions(rows, query=some_date)) == want)
    check("search: no match → 0",
          len(st.filter_sort_sessions(rows, query="zzzzz")) == 0)

    check("filter: interrupted chip → 1",
          len(st.filter_sort_sessions(rows, status_key="interrupted")) == 1)
    check("filter: needs_labelling chip → 2",
          len(st.filter_sort_sessions(rows, status_key="needs_labelling")) == 2)

    newest = st.filter_sort_sessions(rows, newest_first=True)
    oldest = st.filter_sort_sessions(rows, newest_first=False)
    check("sort: newest first is descending by start time",
          all(newest[i].started_unix >= newest[i + 1].started_unix
              for i in range(len(newest) - 1)))
    check("sort: oldest first is the reverse", [s.root for s in oldest] == [s.root for s in newest][::-1])


# ── 4. banner shows once + dismissal persists ─────────────────────────────────

def test_banner_and_dismissal():
    base = _copy_lib("ban")
    rows = ss.list_sessions(base)
    interrupted = [r for r in rows if r.interrupted and not r.dismissed]
    check("banner: one pending interruption before dismissal", len(interrupted) == 1)

    tab = st.SessionsScreen(base)
    tab._on_scanned(rows)
    # isHidden() reflects the explicit setVisible() state without needing the
    # tab to be shown inside a top-level window.
    check("banner: visible when a pending interruption exists", not tab._banner.isHidden())

    target = interrupted[0].root
    tab._banner_root = target
    tab._on_banner_dismiss()
    check("banner: hidden immediately after Dismiss", tab._banner.isHidden())
    tab.shutdown()

    reread = ss.session_info(target)
    check("dismissal: manifest flag persisted (interrupted_dismissed)", reread.dismissed is True)
    rows2 = ss.list_sessions(base)
    still_pending = [r for r in rows2 if r.interrupted and not r.dismissed]
    check("dismissal: no pending interruptions after restart", len(still_pending) == 0)

    tab2 = st.SessionsScreen(base)
    tab2._on_scanned(rows2)
    check("banner: does NOT reappear on a fresh screen after dismissal",
          tab2._banner.isHidden())
    tab2.shutdown()


# ── 5. performance: 200 manifests, off the UI thread ──────────────────────────

def test_performance_200():
    base = tempfile.mkdtemp(prefix="bb_perf_")
    cfg = SessionConfig()
    start = _dt.datetime(2026, 1, 1, 9, 0)
    for i in range(200):
        when = start + _dt.timedelta(hours=i)
        ss.create_session(base, cfg, started_at=when,
                          boxer_a=f"A{i}", boxer_b=f"B{i}")

    t0 = time.perf_counter()
    rows = ss.list_sessions(base)
    dt_list = time.perf_counter() - t0
    check("perf: list_sessions(200) returns all rows", len(rows) == 200, f"{len(rows)}")
    check("perf: list_sessions(200) is prompt (< 3s)", dt_list < 3.0, f"{dt_list:.3f}s")

    worker = st._ScanWorker(base)
    got = {}
    worker.done.connect(lambda r: got.setdefault("rows", r))
    worker.start()
    deadline = time.time() + 10.0
    while "rows" not in got and time.time() < deadline:
        _app.processEvents()
        time.sleep(0.01)
    worker.wait(2000)
    check("perf: background _ScanWorker delivered off the UI thread",
          got.get("rows") is not None and len(got["rows"]) == 200,
          f"{len(got.get('rows') or [])}")


# ── 6. strict layout for the populated Sessions screen ────────────────────────

def _nearest_scrollarea(w):
    p = w.parent()
    while p is not None:
        if isinstance(p, QAbstractScrollArea):
            return p
        p = p.parent()
    return None


def _overflows(win, page):
    W, H = win.width(), win.height()
    out = []
    TOL = 2
    for w in page.findChildren(QWidget):
        if not w.isVisible() or w.width() <= 0 or w.height() <= 0:
            continue
        if w.visibleRegion().isEmpty():
            continue
        # A scroll bar (and Qt's container for it) is scroll chrome beside the
        # viewport, not clipped content — as in tests/test_layout_fit.py.
        if "ScrollBar" in w.__class__.__name__ or \
                w.objectName() in ("qt_scrollarea_vcontainer", "qt_scrollarea_hcontainer"):
            continue
        tl = w.mapTo(win, QPoint(0, 0))
        R = QRect(tl, w.size())
        if not R.intersects(QRect(0, 0, W, H)):
            continue
        sa = _nearest_scrollarea(w)
        in_scroll = sa is not None
        bad = []
        if R.left() < -TOL:
            bad.append("off-left")
        if R.top() < -TOL and not in_scroll:
            bad.append("off-top")
        if R.right() > W + TOL:
            bad.append("off-right")
        if R.bottom() > H + TOL and not in_scroll:
            bad.append("below-window")
        if sa is not None:
            vp = sa.viewport()
            VR = QRect(vp.mapTo(win, QPoint(0, 0)), vp.size())
            if R.left() < VR.left() - TOL or R.right() > VR.right() + TOL:
                bad.append("clipped-by-viewport")
        if bad:
            out.append((w.__class__.__name__, bad))
    return out


def test_layout_strict():
    rows = ss.list_sessions(BASE_LIB)
    for W, H in [(1440, 900), (1280, 800)]:
        win = QMainWindow()
        tab = st.SessionsScreen(BASE_LIB)
        win.setCentralWidget(tab)
        win.resize(W, H)
        win.show()
        tab._on_scanned(rows)
        for _ in range(5):
            _app.processEvents()
        ov = _overflows(win, tab)
        check(f"layout: populated Sessions has nothing clipped at {W}x{H}",
              len(ov) == 0, f"overflows: {ov[:4]}")
        win.close()


# ── 7. no row control's text is clipped (folder button regression) ────────────

def _clipped_text_controls(page):
    """Row controls whose own box is narrower than the text/icon they must show.

    A control is clipped when its laid-out width is smaller than the width its
    content needs (sizeHint). Catches the old "Show in Finde" text button that
    didn't fit its actions cell; an icon-only button (no text) can't clip.
    """
    bad = []
    for row in page.findChildren(QFrame):
        if row.objectName() != "sessionRow":
            continue
        for w in row.findChildren(QWidget):
            if not isinstance(w, (QLabel, QPushButton)):
                continue
            if not w.isVisible() or w.width() <= 0:
                continue
            text = w.text() if hasattr(w, "text") else ""
            if not text:
                continue  # icon-only / empty controls can't clip text
            need = w.sizeHint().width()
            if w.width() + 1 < need:
                bad.append((w.__class__.__name__, repr(text),
                            w.width(), need))
    return bad


def test_row_controls_not_clipped():
    rows = ss.list_sessions(BASE_LIB)
    for W, H in [(1440, 900), (1280, 800), (1180, 720)]:
        win = QMainWindow()
        tab = st.SessionsScreen(BASE_LIB)
        win.setCentralWidget(tab)
        win.resize(W, H)
        win.show()
        tab._on_scanned(rows)
        for _ in range(5):
            _app.processEvents()
        bad = _clipped_text_controls(tab)
        check(f"row controls: no clipped text at {W}x{H}",
              len(bad) == 0, f"clipped: {bad[:4]}")
        win.close()


if __name__ == "__main__":
    from tools.generate_demo_session import generate_library
    BASE_LIB = tempfile.mkdtemp(prefix="bb_base_")
    generate_library(BASE_LIB, seed=7)   # the ONLY ProcessPool use — before any QThread

    test_discovery()
    test_folder_naming()
    test_filter_sort()
    test_banner_and_dismissal()
    test_performance_200()
    test_layout_strict()
    test_row_controls_not_clipped()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
