"""
docs_nav.py
───────────
Pure-Python (no Qt) logic for the in-app Documentation tab (`docs_tab.py`)
and its tests (`tests/test_docs.py`): `docs/nav.yml` loading, a page
registry, GitHub-style heading slugs (for jump-to-anchor), a small search
index (titles/headings/body text), and internal link/image extraction for
the link checker.

Kept Qt-free and headlessly testable, the same way `session_controller.py`
and `label_vocab.py` are — everything here is plain file/string handling.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(ROOT, "docs")
NAV_PATH = os.path.join(DOCS_DIR, "nav.yml")


# ─── data model ────────────────────────────────────────────────────────────

@dataclass
class Page:
    id: str
    title: str
    file: str          # path relative to DOCS_DIR, e.g. "user-guide/sessions.md"
    section: str        # the section title it belongs to


@dataclass
class NavSection:
    title: str
    pages: list[Page] = field(default_factory=list)


@dataclass
class Nav:
    sections: list[NavSection] = field(default_factory=list)

    def all_pages(self) -> list[Page]:
        return [p for s in self.sections for p in s.pages]

    def page_by_id(self, page_id: str) -> Optional[Page]:
        for p in self.all_pages():
            if p.id == page_id:
                return p
        return None

    def page_by_file(self, file_rel: str) -> Optional[Page]:
        norm = os.path.normpath(file_rel)
        for p in self.all_pages():
            if os.path.normpath(p.file) == norm:
                return p
        return None


def load_nav(nav_path: str = NAV_PATH) -> Nav:
    with open(nav_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    nav = Nav()
    for sec in raw.get("sections", []):
        section = NavSection(title=sec.get("title", ""))
        for p in sec.get("pages", []):
            section.pages.append(Page(
                id=str(p.get("id", "")),
                title=str(p.get("title", "")),
                file=str(p.get("file", "")),
                section=section.title,
            ))
        nav.sections.append(section)
    return nav


def page_path(page: Page, docs_dir: str = DOCS_DIR) -> str:
    return os.path.join(docs_dir, page.file)


def read_page(page: Page, docs_dir: str = DOCS_DIR) -> str:
    with open(page_path(page, docs_dir), "r", encoding="utf-8") as f:
        return f.read()


def missing_page_files(nav: Nav, docs_dir: str = DOCS_DIR) -> list[Page]:
    """Nav pages whose .md file doesn't actually exist — should always be
    empty; `tests/test_docs.py` asserts that."""
    return [p for p in nav.all_pages() if not os.path.isfile(page_path(p, docs_dir))]


# ─── slugify (GitHub-style heading anchors) ────────────────────────────────

def slugify(text: str) -> str:
    """The same algorithm GitHub uses for heading anchors: lowercase, drop
    anything that isn't a word character/space/hyphen, collapse runs of
    space/hyphen into one hyphen. Qt's Markdown renderer doesn't generate
    heading anchors itself (verified — see docs-guide.md), so docs_tab.py
    computes these itself to scroll to a heading."""
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s-]+", "-", s)
    return s.strip("-")


# ─── markdown scanning (headings / links / images / plain text) ───────────

_FENCE_RE = re.compile(r"^```")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
# Negative lookbehind excludes image syntax ![alt](url) from matching as a link.
_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _non_fenced_lines(markdown_text: str):
    """Yield (line, in_code_fence) — fenced code content is skipped by
    callers that only care about prose (headings, links, search body)."""
    in_fence = False
    for line in markdown_text.splitlines():
        if _FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        yield line, in_fence


@dataclass
class Heading:
    level: int
    text: str
    slug: str


def extract_headings(markdown_text: str) -> list[Heading]:
    out = []
    for line, in_fence in _non_fenced_lines(markdown_text):
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            text = m.group(2).strip()
            out.append(Heading(level=len(m.group(1)), text=text, slug=slugify(text)))
    return out


def extract_links(markdown_text: str) -> list[str]:
    """Every link TARGET in the document (images excluded)."""
    out = []
    for line, in_fence in _non_fenced_lines(markdown_text):
        if in_fence:
            continue
        out.extend(m.group(2) for m in _LINK_RE.finditer(line))
    return out


def extract_images(markdown_text: str) -> list[str]:
    out = []
    for line, in_fence in _non_fenced_lines(markdown_text):
        if in_fence:
            continue
        out.extend(m.group(2) for m in _IMAGE_RE.finditer(line))
    return out


def plain_text(markdown_text: str) -> str:
    """Rough markdown -> plain text for the search index: strips heading
    markers, emphasis markers, and link/image syntax while KEEPING the link
    text / image alt text (a screenshot's alt text is real descriptive
    content a reader might search for), inline code backticks, and fenced
    code blocks stay as their raw text (still searchable)."""
    out_lines = []
    for line, in_fence in _non_fenced_lines(markdown_text):
        if in_fence:
            out_lines.append(line)
            continue
        s = line
        s = _IMAGE_RE.sub(r"\1", s)
        s = _LINK_RE.sub(r"\1", s)
        s = re.sub(r"^#{1,6}\s+", "", s)
        s = re.sub(r"[*_`#|]", " ", s)
        out_lines.append(s)
    return "\n".join(out_lines)


# ─── link resolution (for the link checker) ────────────────────────────────

EXTERNAL_SCHEMES = ("http://", "https://", "mailto:")


@dataclass
class LinkCheck:
    source_page: str   # page id the link was found on
    target: str         # raw link target text
    ok: bool
    reason: str = ""


def is_external(target: str) -> bool:
    return target.startswith(EXTERNAL_SCHEMES)


def resolve_internal_link(target: str, current_page: Page, nav: Nav,
                          docs_dir: str = DOCS_DIR) -> LinkCheck:
    """Resolve a non-external link target against the docs tree. `target`
    may be `#anchor` (same page), `relative/path.md`, or
    `relative/path.md#anchor`."""
    if is_external(target):
        return LinkCheck(current_page.id, target, True, "external")

    path_part, _, anchor = target.partition("#")

    if not path_part:
        # same-page anchor
        headings = extract_headings(read_page(current_page, docs_dir))
        slugs = {h.slug for h in headings}
        if anchor in slugs:
            return LinkCheck(current_page.id, target, True)
        return LinkCheck(current_page.id, target, False,
                         f"no heading with anchor #{anchor} on {current_page.file}")

    current_dir = os.path.dirname(page_path(current_page, docs_dir))
    resolved = os.path.normpath(os.path.join(current_dir, path_part))
    if not os.path.isfile(resolved):
        return LinkCheck(current_page.id, target, False,
                         f"target file not found: {os.path.relpath(resolved, docs_dir)}")

    if anchor:
        with open(resolved, "r", encoding="utf-8") as f:
            headings = extract_headings(f.read())
        slugs = {h.slug for h in headings}
        if anchor not in slugs:
            return LinkCheck(current_page.id, target, False,
                             f"target file has no heading with anchor #{anchor}: "
                             f"{os.path.relpath(resolved, docs_dir)}")
    return LinkCheck(current_page.id, target, True)


def check_all_links(nav: Nav, docs_dir: str = DOCS_DIR) -> list[LinkCheck]:
    results = []
    for page in nav.all_pages():
        text = read_page(page, docs_dir)
        for target in extract_links(text):
            results.append(resolve_internal_link(target, page, nav, docs_dir))
    return results


def check_all_images(nav: Nav, docs_dir: str = DOCS_DIR) -> list[LinkCheck]:
    """Every image reference resolves to a real file on disk."""
    results = []
    for page in nav.all_pages():
        text = read_page(page, docs_dir)
        current_dir = os.path.dirname(page_path(page, docs_dir))
        for target in extract_images(text):
            resolved = os.path.normpath(os.path.join(current_dir, target))
            ok = os.path.isfile(resolved)
            reason = "" if ok else f"image not found: {os.path.relpath(resolved, docs_dir)}"
            results.append(LinkCheck(page.id, target, ok, reason))
    return results


# ─── search ─────────────────────────────────────────────────────────────────

@dataclass
class SearchHit:
    page_id: str
    title: str
    snippet: str
    score: int


def build_search_index(nav: Nav, docs_dir: str = DOCS_DIR) -> dict[str, dict]:
    index = {}
    for page in nav.all_pages():
        text = read_page(page, docs_dir)
        index[page.id] = {
            "title": page.title,
            "headings": [h.text for h in extract_headings(text)],
            "body": plain_text(text),
        }
    return index


def _snippet(body: str, query: str, width: int = 60) -> str:
    low = body.lower()
    i = low.find(query.lower())
    if i < 0:
        collapsed = " ".join(body.split())
        return collapsed[:width]
    start = max(0, i - width // 2)
    end = min(len(body), i + len(query) + width // 2)
    collapsed = " ".join(body[start:end].split())
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{collapsed}{suffix}"


def search(index: dict[str, dict], query: str) -> list[SearchHit]:
    """Case-insensitive substring search across title/headings/body.
    Ranked: title match (3) > heading match (2) > body-only match (1)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    hits = []
    for page_id, entry in index.items():
        title_hit = q in entry["title"].lower()
        heading_hit = any(q in h.lower() for h in entry["headings"])
        body_hit = q in entry["body"].lower()
        if not (title_hit or heading_hit or body_hit):
            continue
        score = 3 if title_hit else (2 if heading_hit else 1)
        snippet = _snippet(entry["body"], q)
        hits.append(SearchHit(page_id=page_id, title=entry["title"],
                              snippet=snippet, score=score))
    hits.sort(key=lambda h: (-h.score, h.title.lower()))
    return hits


__all__ = [
    "Page", "NavSection", "Nav", "load_nav", "page_path", "read_page",
    "missing_page_files", "slugify", "Heading", "extract_headings",
    "extract_links", "extract_images", "plain_text", "is_external",
    "LinkCheck", "resolve_internal_link", "check_all_links", "check_all_images",
    "SearchHit", "build_search_index", "search", "DOCS_DIR", "NAV_PATH",
]
