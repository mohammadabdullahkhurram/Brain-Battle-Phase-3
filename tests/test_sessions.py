"""
Part 2 — Sessions screen (DESIGN §1) acceptance tests.

Covers:
  • discovery on a --library demo folder (statuses classified, a damaged
    manifest surfaces as an "unreadable" row, boxer names + dates parsed)
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
)

_app = QApplication.instance() or QApplication(sys.argv)

import session_storage as ss
import sessions_tab as st
from session_controller import SessionConfig

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

    check("discovery: 6 sessions found (5 demo + 1 damaged)", len(rows) == 6,
          f"{len(rows)}")
    check("discovery: one interrupted", len(by_status.get(ss.STATUS_INTERRUPTED, [])) == 1)
    check("discovery: one exported", len(by_status.get(ss.STATUS_EXPORTED, [])) == 1)
    check("discovery: one labelled", len(by_status.get(ss.STATUS_LABELLED, [])) == 1)
    check("discovery: two needs-labelling (incl. the no-name one)",
          len(by_status.get(ss.STATUS_NEEDS_LABELLING, [])) == 2)
    check("discovery: damaged manifest surfaces as 'unreadable' (never hidden)",
          len(by_status.get(ss.STATUS_UNREADABLE, [])) == 1)

    sam = next((r for r in rows if r.boxer_a == "Sam"), None)
    check("discovery: boxer names parsed (Sam vs Alex)",
          sam is not None and sam.boxer_b == "Alex",
          f"{sam.boxer_a if sam else None}/{sam.boxer_b if sam else None}")
    check("discovery: interrupted session carries a stopped time",
          sam is not None and sam.stopped_sec is not None, f"{sam.stopped_sec if sam else None}")
    exported = by_status.get(ss.STATUS_EXPORTED, [None])[0]
    check("discovery: exported session has >= 1 label", exported and exported.labels_count >= 1,
          f"{exported.labels_count if exported else None}")
    noname = next((r for r in rows if r.readable and not r.boxer_a and not r.boxer_b), None)
    check("discovery: a no-name session exists and is readable", noname is not None)
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
    check("naming: both names", ss._session_folder_name(d, "Sam", "Alex") == "2026-10-29 16-20 Sam vs Alex")
    check("naming: one name", ss._session_folder_name(d, "Sam", "") == "2026-10-29 16-20 Sam")
    check("naming: no names → timestamp only", ss._session_folder_name(d, "", "") == "2026-10-29 16-20")

    base = tempfile.mkdtemp(prefix="bb_name_")
    cfg = SessionConfig()
    l1 = ss.create_session(base, cfg, started_at=d, boxer_a="Sam", boxer_b="Alex")
    l2 = ss.create_session(base, cfg, started_at=d, boxer_a="Sam", boxer_b="Alex")
    l3 = ss.create_session(base, cfg, started_at=d, boxer_a="Sam", boxer_b="Alex")
    names = sorted(os.path.basename(str(x.root)) for x in (l1, l2, l3))
    check("naming: collisions get (2)/(3), originals not renamed",
          names == ["2026-10-29 16-20 Sam vs Alex",
                    "2026-10-29 16-20 Sam vs Alex (2)",
                    "2026-10-29 16-20 Sam vs Alex (3)"], names)


# ── 3. search / filter / sort ─────────────────────────────────────────────────

def test_filter_sort():
    rows = ss.list_sessions(BASE_LIB)

    counts = st.status_counts(rows)
    check("chips: counts (all=5, needs=2, labelled=1, exported=1, interrupted=1)",
          counts == {"all": 5, "needs_labelling": 2, "labelled": 1,
                     "exported": 1, "interrupted": 1}, str(counts))

    check("search: by boxer name 'Sam' → 1",
          len(st.filter_sort_sessions(rows, query="sam")) == 1)
    check("search: by boxer name 'Leo' → 1",
          len(st.filter_sort_sessions(rows, query="Leo")) == 1)
    check("search: by ISO date '2026-10-27' → 1",
          len(st.filter_sort_sessions(rows, query="2026-10-27")) == 1)
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

    tab = st.SessionsTab(base)
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

    tab2 = st.SessionsTab(base)
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
        tab = st.SessionsTab(BASE_LIB)
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
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
