"""
Developer guide + Decisions (ADRs) + Project honesty tests (D3).

These keep the developer-facing docs truthful by checking them against the repo,
not against memory — the same idea as test_research_docs.py for the file-format
reference:

  1. Module reference completeness — every root *.py file has an entry in
     developer-guide/module-reference.md, and no entry names a .py file that no
     longer exists. Add/remove/rename a module without updating the page and this
     goes red.
  2. ADR bookkeeping — every docs/decisions/adr-*.md file is listed in the ADR
     index (decisions/index.md) AND in nav.yml, and the index links no ADR file
     that doesn't exist.
  3. nav.yml completeness — every docs page inside a section folder (not the
     top-level planning docs, not contributing/) is listed in nav.yml.
  4. No leftover placeholder — the phrase "later documentation step" (the D1/D2
     "coming in a later documentation step" placeholder) appears nowhere in docs.
  5. CHANGELOG ↔ version — the CHANGELOG's top entry matches version.py's
     __version__.

Pure file/string checks + docs_nav (Qt-free). Run: python3 tests/test_developer_docs.py
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import docs_nav

DOCS = os.path.join(ROOT, "docs")
DEV_DIR = os.path.join(DOCS, "developer-guide")
DEC_DIR = os.path.join(DOCS, "decisions")
MODULE_REF = os.path.join(DEV_DIR, "module-reference.md")
ADR_INDEX = os.path.join(DEC_DIR, "index.md")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
VERSION_PY = os.path.join(ROOT, "version.py")

# Section folders whose every page must be in nav.yml. (The top-level planning
# docs and contributing/ are intentionally not nav pages.)
SECTION_DIRS = {"getting-started", "user-guide", "research-and-data",
                "developer-guide", "decisions", "project"}

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── 1. module reference completeness ────────────────────────────────────────

def test_every_module_has_a_reference_entry():
    text = _read(MODULE_REF)
    # backticked token spans (same idea as test_research_docs)
    backticked = set(re.findall(r"`([^`]+)`", text))
    root_py = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "*.py")))
    check("found the root modules", len(root_py) > 20, str(len(root_py)))
    missing = [m for m in root_py if m not in backticked]
    check("every root *.py has a backticked entry in module-reference.md",
          not missing, f"undocumented: {missing}")


def test_no_reference_entry_for_a_deleted_module():
    """Every '### `name.py`' entry heading names a file that still exists."""
    text = _read(MODULE_REF)
    heading_files = re.findall(r"^###\s+`([^`]+\.py)`", text, flags=re.MULTILINE)
    check("module-reference.md has ### module entries", len(heading_files) > 20,
          str(len(heading_files)))
    stale = [f for f in heading_files if not os.path.isfile(os.path.join(ROOT, f))]
    check("no module-reference entry names a .py file that no longer exists",
          not stale, f"stale entries: {stale}")


# ── 2. ADR bookkeeping ──────────────────────────────────────────────────────

def test_every_adr_is_indexed_and_in_nav():
    nav = docs_nav.load_nav()
    nav_files = {os.path.normpath(p.file) for p in nav.all_pages()}
    index_text = _read(ADR_INDEX)
    adr_files = sorted(os.path.basename(p)
                       for p in glob.glob(os.path.join(DEC_DIR, "adr-*.md")))
    check("found ADR files", len(adr_files) >= 12, str(len(adr_files)))

    not_in_nav = [f for f in adr_files
                  if os.path.normpath(f"decisions/{f}") not in nav_files]
    check("every ADR file is listed in nav.yml", not not_in_nav, str(not_in_nav))

    not_in_index = [f for f in adr_files if f not in index_text]
    check("every ADR file is linked from the ADR index", not not_in_index,
          str(not_in_index))


def test_index_links_no_missing_adr():
    index_text = _read(ADR_INDEX)
    linked = set(re.findall(r"\(([^)]*adr-\d+[^)]*\.md)\)", index_text))
    missing = [l for l in linked if not os.path.isfile(os.path.join(DEC_DIR, l))]
    check("the ADR index links no ADR file that doesn't exist", not missing,
          str(missing))


# ── 3. nav.yml completeness ─────────────────────────────────────────────────

def test_every_section_page_is_in_nav():
    nav = docs_nav.load_nav()
    nav_files = {os.path.normpath(p.file) for p in nav.all_pages()}
    on_disk = glob.glob(os.path.join(DOCS, "**", "*.md"), recursive=True)
    unlisted = []
    for path in on_disk:
        rel = os.path.normpath(os.path.relpath(path, DOCS))
        top = rel.split(os.sep)[0]
        if os.sep not in rel:          # a top-level planning doc (DESIGN.md …)
            continue
        if top not in SECTION_DIRS:    # contributing/, etc.
            continue
        if rel not in nav_files:
            unlisted.append(rel)
    check("every docs page in a section folder is listed in nav.yml",
          not unlisted, f"unlisted: {unlisted}")


# ── 4. no leftover placeholder text ─────────────────────────────────────────

def test_no_coming_in_a_later_step_text():
    hits = []
    for path in glob.glob(os.path.join(DOCS, "**", "*.md"), recursive=True):
        if "later documentation step" in _read(path).lower():
            hits.append(os.path.relpath(path, DOCS))
    check('no "later documentation step" placeholder remains in any docs page',
          not hits, str(hits))


# ── 5. CHANGELOG top entry matches version.py ───────────────────────────────

def test_changelog_top_matches_version():
    m = re.search(r'__version__\s*=\s*"([^"]+)"', _read(VERSION_PY))
    check("version.py defines __version__", m is not None)
    version = m.group(1) if m else ""
    top = None
    for line in _read(CHANGELOG).splitlines():
        if line.startswith("## "):
            top = line
            break
    check("CHANGELOG has a top '## ' entry", top is not None, str(top))
    check(f"CHANGELOG's top entry names the current version ({version})",
          top is not None and version in top, str(top))


if __name__ == "__main__":
    test_every_module_has_a_reference_entry()
    test_no_reference_entry_for_a_deleted_module()
    test_every_adr_is_indexed_and_in_nav()
    test_index_links_no_missing_adr()
    test_every_section_page_is_in_nav()
    test_no_coming_in_a_later_step_text()
    test_changelog_top_matches_version()

    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
