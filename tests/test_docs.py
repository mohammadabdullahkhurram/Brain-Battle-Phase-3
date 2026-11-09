"""
The in-app Documentation tab (docs_tab.py, docs_nav.py) + the docs/ content
itself (Phase 3 step D1 — Getting started + User guide).

Covers:
  1. Every page in docs/nav.yml exists and renders in the Documentation tab
     without raising, with non-trivial content.
  2. Every image/diagram a page references exists on disk.
  3. Link checker: every internal link and anchor resolves.
  4. Diagram freshness: every docs/diagrams/*.mmd has an up-to-date render
     (tools/render_diagrams.py's hash sidecar matches the current source).
  5. Search finds a known phrase from each page, and highlighting produces
     at least one match.
  6. Help (?) buttons route to the right page/anchor.
  7. Strict layout fit for the Documentation tab at the three sizes (reuses
     test_layout_fit.py's overflow detector — one algorithm, not two).
  8. The placeholder/jargon gate's scope contract: it does not, and must
     not, scan docs/*.md (see docs/contributing/docs-guide.md "Scope of the
     placeholder gate") — a change to that scope should be a deliberate,
     reviewed decision, not a silent side effect.

Run:  python3 tests/test_docs.py
"""
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BRAIN_BATTLE_SIMULATE", "1")
import tempfile
os.environ.setdefault("BRAIN_BATTLE_CAPTURE_DIR", tempfile.mkdtemp(prefix="bb_docs_test_cap_"))
try:
    import vlc  # noqa: F401
except Exception:
    import types
    sys.modules["vlc"] = types.ModuleType("vlc")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

_app = QApplication.instance() or QApplication(sys.argv)

import docs_nav

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _load_test_layout_fit():
    """Import tests/test_layout_fit.py as a module (its __main__ guard means
    importing it only defines functions/constants, it doesn't run) so the
    overflow detector isn't duplicated here."""
    path = os.path.join(ROOT, "tests", "test_layout_fit.py")
    spec = importlib.util.spec_from_file_location("_test_layout_fit_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─── 1 + 2. every nav page exists, renders, and its images exist ─────────────

def test_nav_pages_render():
    from docs_tab import DocsTab
    nav = docs_nav.load_nav()
    check("nav.yml has at least one section", len(nav.sections) > 0)
    check("nav.yml has at least one page", len(nav.all_pages()) > 0)

    missing = docs_nav.missing_page_files(nav)
    check("every nav.yml page's .md file exists", not missing,
          str([p.file for p in missing]))

    tab = DocsTab()
    for page in nav.all_pages():
        try:
            tab.open_page(page.id)
            text = tab._browser.toPlainText()
            ok = len(text.strip()) > 20
        except Exception as e:
            ok = False
            text = f"{type(e).__name__}: {e}"
        check(f"page '{page.id}' renders with real content", ok, text[:60])


def test_images_exist():
    nav = docs_nav.load_nav()
    results = docs_nav.check_all_images(nav)
    bad = [r for r in results if not r.ok]
    check(f"every referenced image exists ({len(results)} checked)",
          not bad, "; ".join(f"{b.source_page}: {b.target} ({b.reason})" for b in bad[:5]))


# ─── 3. link checker ────────────────────────────────────────────────────────

def test_links_resolve():
    nav = docs_nav.load_nav()
    results = docs_nav.check_all_links(nav)
    check("at least one internal link was actually checked",
          any(not docs_nav.is_external(r.target) for r in results))
    bad = [r for r in results if not r.ok]
    check(f"every internal link/anchor resolves ({len(results)} checked)",
          not bad, "; ".join(f"{b.source_page}: {b.target} ({b.reason})" for b in bad[:5]))


# ─── 4. diagram freshness ───────────────────────────────────────────────────

def test_diagrams_fresh():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import render_diagrams as rd
    mmd_files = sorted(rd.DIAGRAMS_DIR.glob("*.mmd"))
    check("at least one diagram exists", len(mmd_files) > 0)
    stale = [p for p in mmd_files if rd.is_stale(p)]
    check("every diagram's .svg/.png/.hash is up to date with its .mmd "
          "(run `python3 tools/render_diagrams.py` if not)",
          not stale, str([p.name for p in stale]))
    for p in mmd_files:
        svg = p.with_suffix(".svg")
        png = p.with_suffix(".png")
        check(f"{p.stem}.svg exists", svg.is_file())
        check(f"{p.stem}.png exists", png.is_file())


# ─── 5. search ──────────────────────────────────────────────────────────────

# One phrase per nav page, each drawn verbatim from that page's own text.
_KNOWN_PHRASES = {
    "overview": "two boxers",
    "installation": "ffmpeg -version",
    "first-run": "No recordings yet",
    "quick-tour": "labelling panel",
    "sessions": "Needs labelling",
    "new-recording": "Reconnect",
    "review-and-label": "Who did it",
    "export": "combined video",
    "settings": "Test sound",
    "troubleshooting": "QThread destroyed",
    "faq": "Bluetooth",
}


def test_search_finds_known_phrases():
    nav = docs_nav.load_nav()
    check("every nav page has a known-phrase search test",
          set(_KNOWN_PHRASES) == {p.id for p in nav.all_pages()},
          str(set(p.id for p in nav.all_pages()) ^ set(_KNOWN_PHRASES)))
    index = docs_nav.build_search_index(nav)
    for page_id, phrase in _KNOWN_PHRASES.items():
        hits = docs_nav.search(index, phrase)
        check(f"search({phrase!r}) finds page '{page_id}'",
              any(h.page_id == page_id for h in hits),
              str([h.page_id for h in hits]))


def test_search_highlighting():
    from docs_tab import DocsTab
    tab = DocsTab()
    tab.open_page("troubleshooting")
    tab._highlight("Reconnect")
    check("highlighting a known phrase produces at least one match",
          len(tab._browser.extraSelections()) > 0)
    tab._highlight("")
    check("clearing the query clears highlighting",
          len(tab._browser.extraSelections()) == 0)


# ─── 5b. search UX (count / section / clear / Esc / Enter) ──────────────────

def test_search_ux():
    from docs_tab import DocsTab
    tab = DocsTab()

    # The × clear button is enabled on the search box.
    check("search box has a clear (×) button", tab._search_box.isClearButtonEnabled())

    # Typing a query shows a result count and swaps the left pane to results.
    tab._search_box.setText("Reconnect")
    check("searching switches the left pane to the results list",
          tab._left_stack.currentWidget() is tab._results_list)
    check("a result count is shown", tab._result_count.text().startswith("2 results for"),
          tab._result_count.text())
    check("the count line is visible while searching", not tab._result_count.isHidden())
    # Every result row names its section and page title.
    rows = [tab._results_list.item(i).text() for i in range(tab._results_list.count())]
    check("there are 2 results for 'Reconnect'", len(rows) == 2, str(rows))
    check("each result row shows its section",
          all(" · " in r and ("User guide" in r or "Getting started" in r) for r in rows),
          str(rows))

    # Enter opens the first result, scrolled to the match, with it highlighted.
    first_id = tab._last_hits[0].page_id
    tab._open_first_result()
    check("Enter opens the first result's page",
          tab._current_page_id() == first_id, tab._current_page_id())
    check("the opened page has the query highlighted",
          len(tab._browser.extraSelections()) > 0)

    # Esc clears the search and returns to the nav menu.
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent as _QEvent
    ev = QKeyEvent(_QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    tab.eventFilter(tab._search_box, ev)
    check("Esc empties the search box", tab._search_box.text() == "")
    check("Esc returns the left pane to the nav tree",
          tab._left_stack.currentWidget() is tab._nav_tree)
    check("Esc hides the result count", tab._result_count.isHidden())

    # No-results query reports zero, not an error.
    tab._search_box.setText("zzzqqnotaphrase")
    check("a no-match query reports 'No results'",
          tab._result_count.text().startswith("No results for"), tab._result_count.text())


def test_no_result_for_missing_page_section():
    """Search hits carry their section (docs_nav), used in each result row."""
    nav = docs_nav.load_nav()
    index = docs_nav.build_search_index(nav)
    hits = docs_nav.search(index, "Reconnect")
    check("search hits carry a non-empty section",
          hits and all(h.section for h in hits), str([(h.page_id, h.section) for h in hits]))


# ─── 5c. no horizontal overflow inside the content column ───────────────────

def test_no_horizontal_overflow():
    """Every docs page's rendered content fits its column at all three sizes —
    images/diagrams scale down and prose/tables wrap, so the content's laid-out
    width never exceeds the viewport (no horizontal scrollbar ever appears)."""
    import brain_battle_main as bbm
    tlf = _load_test_layout_fit()
    win = bbm.BrainBattleApp()
    try:
        nav = docs_nav.load_nav()
        for W, H in tlf.SIZES:
            win.resize(W, H); win.show()
            win._shell.set_current_index(win.IDX_DOCS)
            for _ in range(4):
                _app.processEvents()
            docs = win._docs_page
            for page in nav.all_pages():
                docs.open_page(page.id)
                for _ in range(4):
                    _app.processEvents()
                br = docs._browser
                content_w = br.document().documentLayout().documentSize().width()
                vp = br.viewport().width()
                check(f"[{W}x{H}] '{page.id}' content fits the column (no h-scroll)",
                      content_w <= vp + 1, f"content={content_w:.0f} > viewport={vp}")
    finally:
        try:
            win.record_flow.shutdown(); win._review_page.shutdown()
            win._sessions_page.shutdown()
        except Exception:
            pass


# ─── 5d. diagrams are readable at the content width ─────────────────────────

def _diagram_declared_font_px(mmd_text: str):
    """The themeVariables fontSize declared in a diagram's init directive, in
    px, or None if it declares none (docs-guide.md requires every diagram to
    declare one so its on-screen label size is predictable)."""
    m = re.search(r"fontSize['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)px", mmd_text)
    return float(m.group(1)) if m else None


def _svg_viewbox_width(svg_text: str):
    m = re.search(r'viewBox="[\d.]+ [\d.]+ ([\d.]+)', svg_text)
    return float(m.group(1)) if m else None


def test_diagram_text_is_readable():
    """At the content display width (docs_tab.MAX_IMG_WIDTH), each diagram's
    label text must be at least ~12 px — otherwise it's unreadable once scaled
    into the reading column. displayed = declared_font × MAX_IMG_WIDTH / svg
    width (see docs/contributing/docs-guide.md 'Diagram size')."""
    import docs_tab
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import render_diagrams as rd
    mmd_files = sorted(rd.DIAGRAMS_DIR.glob("*.mmd"))
    check("at least one diagram to check", len(mmd_files) > 0)
    for p in mmd_files:
        font = _diagram_declared_font_px(p.read_text(encoding="utf-8"))
        check(f"{p.stem}.mmd declares a themeVariables fontSize", font is not None)
        svg = p.with_suffix(".svg")
        vbw = _svg_viewbox_width(svg.read_text(encoding="utf-8")) if svg.is_file() else None
        check(f"{p.stem}.svg has a viewBox width", vbw is not None)
        if font and vbw:
            displayed = font * docs_tab.MAX_IMG_WIDTH / vbw
            check(f"{p.stem}: label text is ≥12px when displayed "
                  f"({displayed:.1f}px at {docs_tab.MAX_IMG_WIDTH}px wide; "
                  f"narrow the diagram or raise fontSize if not)",
                  displayed >= 12.0, f"{displayed:.1f}px")


# ─── 5e. doc images were rendered with a clean recordings-folder name ───────

def test_docs_images_have_no_temp_folder_name():
    """The committed doc screenshots must show a realistic recordings folder
    ('Brain Battle Data'), never a throwaway temp-folder name. Enforced at the
    source of those images: render_screens.py --docs points the app at a
    cleanly-named folder (see that file's DOCS_RECORDINGS_DIR)."""
    src_path = os.path.join(ROOT, "tools", "render_screens.py")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    m = re.search(r'DOCS_RECORDINGS_DIR\s*=\s*"([^"]+)"', src)
    check("render_screens.py defines DOCS_RECORDINGS_DIR", m is not None)
    if not m:
        return
    path = m.group(1)
    check("the docs recordings folder is named 'Brain Battle Data'",
          os.path.basename(path) == "Brain Battle Data", path)
    # No path segment is a throwaway/random temp-folder name — the kind the
    # old screenshots showed (e.g. "bb_render_cfg_a1b2c3", "tmp8xk2qd"). The
    # stable system-temp root ("tmp", "private", "var") is fine; what must not
    # appear is a generated, run-specific name.
    segs = [s for s in path.split("/") if s]
    temp_like = [s for s in segs
                 if re.match(r"(bb_render_|bb_docs_)", s)
                 or re.fullmatch(r"tmp[0-9A-Za-z_]{5,}", s)
                 or re.fullmatch(r"[0-9a-f]{8,}", s)]
    check("no docs-image folder segment is a throwaway temp-folder name",
          not temp_like, str(temp_like))
    # Docs mode must NOT point the env override at the docs folder — that would
    # show Settings' "set outside the app" note, which a real user wouldn't see.
    # (The override is only used for the throwaway screens/-dump folder.)
    check("docs mode does not set BRAIN_BATTLE_CAPTURE_DIR to the docs folder",
          'BRAIN_BATTLE_CAPTURE_DIR"] = DOCS_RECORDINGS_DIR' not in src)


# ─── 6. Help buttons route correctly ───────────────────────────────────────

def test_help_buttons_route_to_docs():
    import brain_battle_main as bbm
    win = bbm.BrainBattleApp()
    try:
        win._sessions_page.help_requested.emit()
        check("Sessions Help opens the Documentation tab",
              win._shell.current_index() == win.IDX_DOCS)
        check("Sessions Help opens the 'sessions' page",
              win._docs_page._current_page_id() == "sessions")

        win.record_flow.help_requested.emit("step-2-set-up-session")
        check("New recording Help (step 2) opens 'new-recording'",
              win._docs_page._current_page_id() == "new-recording")
        cur = win._docs_page._browser.textCursor()
        check("New recording Help (step 2) jumps to the right heading",
              "Step 2" in cur.block().text(), cur.block().text())

        win._review_page.help_requested.emit()
        check("Review & Label Help opens 'review-and-label'",
              win._docs_page._current_page_id() == "review-and-label")

        win._settings_page.help_requested.emit()
        check("Settings Help opens 'settings'",
              win._docs_page._current_page_id() == "settings")
    finally:
        try:
            win.record_flow.shutdown(); win._review_page.shutdown()
            win._sessions_page.shutdown()
        except Exception:
            pass


# ─── 7. strict layout fit ───────────────────────────────────────────────────

def test_docs_layout_fit():
    import brain_battle_main as bbm
    tlf = _load_test_layout_fit()
    win = bbm.BrainBattleApp()
    try:
        for W, H in tlf.SIZES:
            win.resize(W, H); win.show()
            for _ in range(4):
                _app.processEvents()
            win._shell.set_current_index(win.IDX_DOCS)
            for _ in range(4):
                _app.processEvents()
            page = win._shell.page_widget(win.IDX_DOCS)
            ov = tlf._overflows(win, page)
            check(f"[{W}x{H}] 'Documentation' has nothing clipped",
                  len(ov) == 0, f"overflows: {ov[:4]}")
    finally:
        try:
            win.record_flow.shutdown(); win._review_page.shutdown()
            win._sessions_page.shutdown()
        except Exception:
            pass


# ─── 8. placeholder-gate scope contract ────────────────────────────────────

def test_placeholder_gate_does_not_scan_docs():
    spec = importlib.util.spec_from_file_location(
        "_test_no_placeholders_mod", os.path.join(ROOT, "tests", "test_no_placeholders.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    files = mod._app_files()
    docs_files = [f for f in files if os.sep + "docs" + os.sep in f or f.endswith(os.sep + "docs")]
    check("the placeholder/jargon gate's file scan does not include docs/*.md "
          "(by design — see docs-guide.md 'Scope of the placeholder gate'; "
          "docs content is for developers and may use technical terms)",
          not docs_files, str(docs_files))
    check("the placeholder/jargon gate only scans *.py", all(f.endswith(".py") for f in files))


if __name__ == "__main__":
    test_nav_pages_render()
    test_images_exist()
    test_links_resolve()
    test_diagrams_fresh()
    test_search_finds_known_phrases()
    test_search_highlighting()
    test_search_ux()
    test_no_result_for_missing_page_section()
    test_no_horizontal_overflow()
    test_diagram_text_is_readable()
    test_docs_images_have_no_temp_folder_name()
    test_help_buttons_route_to_docs()
    test_docs_layout_fit()
    test_placeholder_gate_does_not_scan_docs()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
