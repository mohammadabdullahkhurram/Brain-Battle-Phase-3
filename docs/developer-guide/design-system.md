# Design system & UI conventions

**What this page covers:** how the look and language of the app are kept
consistent — the design tokens and QSS factories, `DESIGN.md` as the source of
truth, the plain-language vocabulary rules and the gate that enforces them, the
strict layout test, and the accessibility rules every screen follows.

## `DESIGN.md` is the source of truth

[`docs/DESIGN.md`](../project/planning-documents.md) (approved 2026-10-27) is the
source of truth for **all** UI work — the actual screens, copy, vocabulary,
tokens, and layout from the approved mockups. If code and `DESIGN.md` disagree,
**ask; don't guess.** `ROADMAP.md` is the build plan; `DESIGN.md` is what it must
look and read like. The handful of places the build intentionally differs are
listed in `ROADMAP.md`'s "DESIGN differences" note.

## Design tokens & QSS factories (`design_tokens.py`)

All colour, type, spacing, and component styling comes from one module so the
app reads as one system. Two kinds of thing live there:

- **Token accessors** — `canvas_bg()`, `surface()`, `border()`, `accent()`,
  `text_primary()`, `boxer_colors(which)`, `phase_colors()`, `ui_font(pt,
  weight)`, etc. — the DESIGN tokens as functions, not scattered hex literals.
- **QSS factories** — `qss_card()`, `qss_primary_button()`, `qss_chip(active)`,
  `qss_input()`, `qss_status_pill(kind)`, and so on. A screen composes these
  rather than hand-writing stylesheets, so a token change propagates everywhere.

**Rule:** never hardcode a colour or font in a screen — call the token/factory.
A new component that needs a style gets a new `qss_*` factory in
`design_tokens.py`.

### Shared components (`ui/widgets.py`)

Screens are assembled from one small component set rather than each styling
its own buttons and cards: `title` / `heading` / `body` / `muted` / `caption`
for text; `Card` and `Banner` for surfaces; `primary_button`,
`secondary_button`, `ghost_button`, `danger_button`, `icon_button`,
`help_button` and `Segmented` for actions; `StatusLine`, `pill`, `boxer_badge`
and `boxer_chip` for status; `TimerLabel` for numbers that change (equal-width
digits). They were introduced with the November 2026 redesign
([ADR-0018](../decisions/adr-0018-november-2026-redesign.md)). Reach for these
first; add to them rather than around them.

Two conventions the components enforce:

- **One primary button per screen** — the main action. Everything else is
  secondary or ghost.
- **Scoped stylesheets.** `Card` names itself and scopes its style to that
  name, so a background never leaks into child widgets (the "grey boxes behind
  labels" bug of 3.0).

### Platform wording

Anything that reads differently on Mac and Windows — the file manager's name,
a keyboard shortcut — comes from `support/platform_utils.py`, never from a
string in a screen. See [Cross-platform & CI](cross-platform-and-ci.md).

### Light mode only

There is no dark palette, no theme toggle, and no restart-to-apply theme flow —
the app is light mode only ([ADR-0007](../decisions/adr-0007-light-only-design.md)).
`tests/test_light_theme.py` guards that the theme machinery stays gone (no dark
tokens, no `_apply_theme`). Don't reintroduce a theme switch.

## Vocabulary: plain language, no jargon

The audience is **non-technical researchers and research assistants**, not
developers. The UI uses plain words; the implementation terms stay out of sight.
`DESIGN.md`'s Vocabulary section is the mapping (UI word ← internal name):

| UI word | Internal name |
|---|---|
| Side camera 1/2 · Boxer A/B view | Side cams · POV 01/02 |
| Headband · Brain activity | Muse-S · EEG |
| Rest before round 1 | baseline |
| Round N · Break | phase markers |
| Recording · Recordings folder | session capture · capture dir |
| "Weak contact: left ear" | per-channel signal quality (TP9 etc.) |

Words that must **never** appear in the app's UI: `sync`, `align`, `offset`,
`xcorr`, `capture clock`, `POV`, `DSLR`, `baseline`, `monotonic`.

**EEG channel codes** (`TP9`, `AF7`, `AF8`, `TP10`, `AUX`) are shown in exactly
one place: the EEG display (`ui/eeg_viewer.py`), which is deliberately a
conventional EEG viewer ([ADR-0016](../decisions/adr-0016-standard-eeg-viewer.md)).
Everywhere else — contact chips, tips, warnings — use the plain names ("left
ear"). The gate enforces this split.

### The EEG display

`EEGViewer` is the one widget for showing EEG, live or recorded. Keep it
conventional: stacked labelled traces, µV scale bar, time axis, dashed event
markers. Traces take the boxer's text colour (A teal, B orange) and AUX is grey.
Anything that alters the *picture* (drift removal, the display filter) must stay
display-only and be labelled as such; never write processed values back.

### The placeholder/jargon gate

`tests/test_no_placeholders.py` scans the app's own Python source (`*.py` at the
repo root + `sync_utils/`) for placeholder UI copy and forbidden jargon in
widget-text calls. It is an **acceptance gate**: zero placeholder copy, zero
jargon in UI strings, zero placeholder screens. It deliberately does **not** scan
`docs/*.md` — the docs are for developers and necessarily use the technical
vocabulary the app's UI bans (this page uses `xcorr` and `capture clock` freely).
The gate's job is "the product doesn't show jargon to a researcher," not "no
document may use a technical word." `tests/test_docs.py` asserts that scope
contract so it can't change silently.

## Strict layout fit

Every screen must fit a 13–14" laptop with **nothing clipped**.
`tests/test_layout_fit.py` holds every page to three sizes — 1440×900, 1280×800,
and the app's minimum window — and fails if any widget overflows its container. A
visible scrollbar is treated as chrome, not as a licence to overflow. When you
add or change a screen, run this test; if something clips, the layout is wrong,
not the test.

The Documentation screen has its own fit check in `tests/test_docs.py`
(`test_no_horizontal_overflow`), because long prose, tables, and scaled diagrams
must wrap/scale to the reading column and never force a horizontal scrollbar.

## Accessibility rules (from `DESIGN.md`)

- **Status is never colour alone** — every status uses an icon **and** text
  (a green tick *and* "Connected", a red border *and* the not-found message).
- **Minimum click target 44 px.**
- **Text contrast WCAG AA (4.5:1).** The light tokens are chosen to meet this.
- **Errors say what happened and what to do** — "Not found. Check the USB cable
  and the video receiver, then press Search again.", not "Error".
- **Tabular figures** for timers/counters so digits don't jump.

## Diagrams

Diagrams in the docs are Mermaid source (`docs/diagrams/*.mmd`) rendered by
`tools/render_diagrams.py` to **both** `.svg` (GitHub) and `.png` (what Markdown
references). The app's Markdown renderer (Qt's `QTextBrowser`, no QtWebEngine)
loads images through `QSvgRenderer`, which can't render Mermaid's CSS-class
styling or `foreignObject` labels — so pages reference the `.png`
([ADR-0012](../decisions/adr-0012-in-app-docs.md); the full empirical reasoning
is in the [docs guide](../contributing/docs-guide.md#why-png-not-svg-in-markdown)).

Because the app scales every image into a ~720 px reading column, diagrams must
stay **narrow** (SVG viewBox ≤ ~960 px) so label text displays at ≥ 12 px.
`tests/test_docs.py` (`test_diagram_text_is_readable`) enforces it. Prefer
vertical layouts, short labels, and splitting a complex diagram into smaller ones
— see the [docs guide](../contributing/docs-guide.md#diagram-size-must-stay-readable-in-the-app).
