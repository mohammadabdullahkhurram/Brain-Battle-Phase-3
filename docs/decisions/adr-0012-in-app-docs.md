# ADR-0012 — In-app docs: Markdown + pre-rendered diagrams

**What this page covers:** why the in-app Documentation screen renders plain
Markdown with Qt's own engine (no browser) and references pre-rendered PNG
diagrams rather than SVG.

- **Status:** Accepted
- **Date:** 2026-11-09

## Context

The app needs documentation the researcher can read **inside** the product, and
the same files should render on GitHub without a separate website or build step.
Bundling a browser engine (QtWebEngine) to render docs is heavy — a large
dependency and a bigger packaged app — for what is essentially formatted text and
a few diagrams.

## Options considered

1. **A separate docs website** — another thing to host, build, and keep in sync;
   not available offline inside the app.
2. **QtWebEngine + a Markdown/Mermaid pipeline in-app** — full fidelity, but a
   heavy dependency and a much larger bundle.
3. **Qt's built-in Markdown (`QTextDocument.setMarkdown` via `QTextBrowser`)** —
   no browser engine; the same `.md` files render on GitHub and in the app.

## Decision

**Render the `docs/` Markdown at runtime with Qt's built-in Markdown support**
(`docs_screen.py`), driven by a `nav.yml` menu, with search and back/forward. No
QtWebEngine, no bundled web assets. Because Qt loads images through `QSvgRenderer`
— which doesn't support Mermaid's CSS-class styling or `foreignObject` labels
(verified empirically) — diagrams are **pre-rendered to PNG** by
`tools/render_diagrams.py` and Markdown references the **`.png`**; GitHub still
gets the full `.svg`. Qt-free logic (`docs_nav.py`) handles nav/search/links so
it's headlessly testable.

## Consequences

- **One set of Markdown files** renders both on GitHub and in the app; no website,
  no hosting, no separate build beyond rendering diagram images.
- **No QtWebEngine dependency** — a smaller, simpler bundle.
- **Diagrams must be PNG and stay narrow** — the app scales images into a ~720 px
  column, so a diagram's SVG viewBox must be ≤ ~960 px for its labels to display
  at ≥ 12 px (`tests/test_docs.py` enforces it). Typography is tuned in code since
  Qt's Markdown importer ignores most stylesheets.
- **The docs are testable against the code** — `docs_nav` + the honesty tests
  (`test_docs.py`, `test_research_docs.py`, `test_developer_docs.py`) check links,
  images, diagram freshness, format accuracy, and completeness.
- Phase 6 must include the `docs/` content (nav + Markdown + rendered images) in
  the packaged app.

## Links

- Commits `50c3623` (docs infrastructure: nav.yml + diagram pipeline),
  `31e0ac8` (build the in-app Documentation screen).
- [Docs guide](../contributing/docs-guide.md),
  [design system — diagrams](../developer-guide/design-system.md#diagrams).
