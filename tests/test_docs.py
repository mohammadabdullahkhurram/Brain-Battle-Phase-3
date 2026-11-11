"""
The in-app Documentation tab (docs_screen.py, docs_nav.py) + the docs/ content
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

import brain_battle.support.docs_nav as docs_nav

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
    from brain_battle.ui.docs_screen import DocsScreen
    nav = docs_nav.load_nav()
    check("nav.yml has at least one section", len(nav.sections) > 0)
    check("nav.yml has at least one page", len(nav.all_pages()) > 0)

    missing = docs_nav.missing_page_files(nav)
    check("every nav.yml page's .md file exists", not missing,
          str([p.file for p in missing]))

    tab = DocsScreen()
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
    "recording-folder": "copy the folder",
    "file-formats": "microvolts",
    "timing-and-accuracy": "shared origin",
    "loading-the-data": "merge_asof",
    "limitations": "buzzer test",
    "personal-data": "data protection",
    # Developer guide (D3)
    "architecture": "zoom levels",
    "module-reference": "grouped overview",
    "key-flows": "seek coalescing",
    "threading-model": "never blocks",
    "design-system": "Light mode only",
    "testing-strategy": "acceptance gates",
    "simulation-and-demo": "failure injection",
    "setup-and-workflow": "owner-approval",
    "how-to-recipes": "honesty gates",
    "known-issues": "listed, not fixed",
    # Decisions / ADRs (D3)
    "decisions": "Michael Nygard",
    "adr-0001": "capture-time timestamping",
    "adr-0002": "audio cross-correlation",
    "adr-0003": "background writer thread",
    "adr-0004": "crash-safe fragmented",
    "adr-0005": "drift camera",
    "adr-0006": "between-boxer analysis",
    "adr-0007": "theme toggle",
    "adr-0008": "nothing to click",
    "adr-0009": "self-describing",
    "adr-0010": "one place to look",
    "adr-0011": "device boundary",
    "adr-0012": "QSvgRenderer",
    "adr-0013": "Reorganise into",
    "adr-0014": "Documents folder",
    # Project (D3)
    "goals": "Community Impact Project",
    "status": "working agreement",
    "roadmap": "per-round clip",
    "version-history": "continuous take",
    "history": "grounding items",
    "glossary": "Photoplethysmography",
    "planning-documents": "left as-written",
    "credits": "high-shelf cut",
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
    from brain_battle.ui.docs_screen import DocsScreen
    tab = DocsScreen()
    tab.open_page("troubleshooting")
    tab._highlight("Reconnect")
    check("highlighting a known phrase produces at least one match",
          len(tab._browser.extraSelections()) > 0)
    tab._highlight("")
    check("clearing the query clears highlighting",
          len(tab._browser.extraSelections()) == 0)


# ─── 5b. search UX (count / section / clear / Esc / Enter) ──────────────────

def test_search_ux():
    from brain_battle.ui.docs_screen import DocsScreen
    tab = DocsScreen()

    # The × clear button is enabled on the search box.
    check("search box has a clear (×) button", tab._search_box.isClearButtonEnabled())

    # Typing a query shows a result count and swaps the left pane to results.
    # The exact count is corpus-dependent (more pages → more matches), so derive
    # the expected number from the search index rather than hard-coding it.
    _nav = docs_nav.load_nav()
    _idx = docs_nav.build_search_index(_nav)
    n_expected = len(docs_nav.search(_idx, "Reconnect"))
    check("'Reconnect' matches more than one page", n_expected >= 2, str(n_expected))
    tab._search_box.setText("Reconnect")
    check("searching switches the left pane to the results list",
          tab._left_stack.currentWidget() is tab._results_list)
    check("a result count is shown",
          tab._result_count.text().startswith(f"{n_expected} results for"),
          tab._result_count.text())
    check("the count line is visible while searching", not tab._result_count.isHidden())
    # Every result row names its section and page title.
    rows = [tab._results_list.item(i).text() for i in range(tab._results_list.count())]
    check(f"there are {n_expected} results for 'Reconnect'", len(rows) == n_expected, str(rows))
    check("each result row shows its section",
          all(" · " in r for r in rows), str(rows))

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


# ─── 5b-ii. snippet trimming + results row structure ────────────────────────

def test_snippet_word_boundaries_and_match():
    """`_snippet` trims to whole words and reports the match offset within the
    snippet so the UI can bold exactly the matched text."""
    body = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet "
            "kilo lima mike november oscar papa quebec romeo sierra tango")
    query = "november"
    snip, ms, ml = docs_nav._snippet(body, query)
    check("snippet reports the match offset", ms >= 0 and ml == len(query),
          f"ms={ms} ml={ml}")
    check("the reported offset actually points at the query",
          snip[ms:ms + ml].lower() == query.lower(), repr(snip))
    core = snip
    if core.startswith("… "):
        core = core[2:]
    if core.endswith(" …"):
        core = core[:-2]
    words = set(body.split())
    check("snippet starts on a word boundary (no partial leading word)",
          core.split()[0] in words, repr(snip))
    check("snippet ends on a word boundary (no partial trailing word)",
          core.split()[-1] in words, repr(snip))


def test_result_rows_show_title_section_and_bold_match():
    """Each result row carries structured data (title / section / snippet with
    the match located) for the rich-text delegate — the title is rendered
    bold, the section beneath it, then the snippet with the match bold."""
    from brain_battle.ui.docs_screen import DocsScreen, _RESULT_ROLE
    tab = DocsScreen()
    tab._search_box.setText("Reconnect")
    n = tab._results_list.count()
    check("the search produced result rows", n > 0, str(n))
    for i in range(n):
        it = tab._results_list.item(i)
        data = it.data(_RESULT_ROLE)
        check(f"result {i} carries structured fields",
              isinstance(data, dict) and data.get("title") and "section" in data,
              str(data))
        # a body match points at the query inside the snippet
        ms, ml = data.get("match_start", -1), data.get("match_len", 0)
        if ms >= 0:
            sn = data["snippet"]
            check(f"result {i} match offset points at the query",
                  sn[ms:ms + ml].lower() == "reconnect", repr(sn))


def test_results_list_no_horizontal_scroll():
    from brain_battle.ui.docs_screen import DocsScreen
    from PyQt6.QtWidgets import QMainWindow
    tab = DocsScreen()
    win = QMainWindow(); win.setCentralWidget(tab); win.resize(1180, 720); win.show()
    for _ in range(3):
        _app.processEvents()
    tab._search_box.setText("session")
    for _ in range(3):
        _app.processEvents()
    sb = tab._results_list.horizontalScrollBar()
    check("results list never shows a horizontal scrollbar",
          not sb.isVisible() and sb.maximum() == 0,
          f"visible={sb.isVisible()} max={sb.maximum()}")
    win.close()


# ─── 5b-iii. scaled images reserve only their displayed height ───────────────

def test_image_height_matches_displayed_size():
    """A screenshot scaled down to the column must reserve only its *displayed*
    height, not its full intrinsic height (which left a large gap below it and
    made search land on blank space). Each image fragment gets an explicit
    width AND height, proportional to the source aspect ratio."""
    from brain_battle.ui.docs_screen import DocsScreen
    from PyQt6.QtWidgets import QMainWindow
    from PyQt6.QtGui import QTextDocument
    tab = DocsScreen()
    win = QMainWindow(); win.setCentralWidget(tab); win.resize(1280, 800); win.show()
    for _ in range(4):
        _app.processEvents()
    nav = docs_nav.load_nav()
    img_page = next((p for p in nav.all_pages()
                     if docs_nav.extract_images(docs_nav.read_page(p))), None)
    check("there is at least one page with an image to check", img_page is not None)
    if img_page is None:
        win.close(); return
    tab.open_page(img_page.id)
    for _ in range(3):
        _app.processEvents()
    doc = tab._browser.document()
    block = doc.begin(); checked = 0
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid() and frag.charFormat().isImageFormat():
                imf = frag.charFormat().toImageFormat()
                src = tab._browser._img_src.get(imf.name())
                if src is not None and src.width() > 0:
                    checked += 1
                    check(f"{imf.name()}: explicit width set",
                          imf.width() > 0, str(imf.width()))
                    check(f"{imf.name()}: explicit height set",
                          imf.height() > 0, str(imf.height()))
                    expected_h = round(src.height() * imf.width() / src.width())
                    check(f"{imf.name()}: reserved height == displayed height (±1)",
                          abs(imf.height() - expected_h) <= 1,
                          f"fmt={imf.height()} expected={expected_h}")
            it += 1
        block = block.next()
    check("at least one image fragment was checked", checked > 0, str(checked))
    win.close()


def test_search_match_scrolled_into_view():
    """Opening a body-match result scrolls so the match sits near the top of the
    viewport (roughly the upper third), not off-screen or in a blank gap."""
    from brain_battle.ui.docs_screen import DocsScreen
    from PyQt6.QtWidgets import QMainWindow
    tab = DocsScreen()
    win = QMainWindow(); win.setCentralWidget(tab); win.resize(1280, 800); win.show()
    for _ in range(4):
        _app.processEvents()
    # A phrase that appears well down a long page so the view must scroll.
    tab._search_box.setText("Reconnect")
    for _ in range(3):
        _app.processEvents()
    tab._open_first_result()
    for _ in range(3):
        _app.processEvents()
    sels = tab._browser.extraSelections()
    check("the opened page has the query highlighted", len(sels) > 0)
    if sels:
        rect = tab._browser.cursorRect(sels[0].cursor)
        vh = tab._browser.viewport().height()
        check("the match is visible on screen", 0 <= rect.top() <= vh,
              f"top={rect.top()} vh={vh}")
        check("the match sits in roughly the upper half (not jammed to the bottom)",
              rect.top() <= vh * 0.6, f"top={rect.top()} vh={vh}")
    win.close()


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
    # The min-x/min-y can be negative (Mermaid sequence diagrams place actor
    # boxes at negative coordinates), so allow a leading '-' on the first two.
    m = re.search(r'viewBox="-?[\d.]+ -?[\d.]+ ([\d.]+)', svg_text)
    return float(m.group(1)) if m else None


def test_diagram_text_is_readable():
    """At the content display width (docs_screen.MAX_IMG_WIDTH), each diagram's
    label text must be at least ~12 px — otherwise it's unreadable once scaled
    into the reading column. displayed = declared_font × MAX_IMG_WIDTH / svg
    width (see docs/contributing/docs-guide.md 'Diagram size')."""
    import brain_battle.ui.docs_screen as docs_screen
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
            displayed = font * docs_screen.MAX_IMG_WIDTH / vbw
            check(f"{p.stem}: label text is ≥12px when displayed "
                  f"({displayed:.1f}px at {docs_screen.MAX_IMG_WIDTH}px wide; "
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


def test_docs_images_have_no_sim_or_demo_artefacts():
    """Committed doc screenshots must not show dev/sim artefacts a real user
    never sees — the simulated camera's 'SIM CAM' overlay or the in-app 'Demo'
    badge. Enforced at the source: render_screens.py --docs installs a
    text-free camera and clears the demo flag from its fixture manifests."""
    src_path = os.path.join(ROOT, "tools", "render_screens.py")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    check("docs mode installs a text-free camera (no 'SIM CAM' overlay)",
          "_install_text_free_camera()" in src)
    check("docs mode strips the 'Demo' flag from its fixtures",
          "_strip_demo_flag_from_fixtures(DOCS_RECORDINGS_DIR)" in src)


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
    test_snippet_word_boundaries_and_match()
    test_result_rows_show_title_section_and_bold_match()
    test_results_list_no_horizontal_scroll()
    test_image_height_matches_displayed_size()
    test_search_match_scrolled_into_view()
    test_no_horizontal_overflow()
    test_diagram_text_is_readable()
    test_docs_images_have_no_temp_folder_name()
    test_docs_images_have_no_sim_or_demo_artefacts()
    test_help_buttons_route_to_docs()
    test_docs_layout_fit()
    test_placeholder_gate_does_not_scan_docs()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
