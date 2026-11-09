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
    test_help_buttons_route_to_docs()
    test_docs_layout_fit()
    test_placeholder_gate_does_not_scan_docs()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
