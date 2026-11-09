# ADR-0007 — Light-only design

**What this page covers:** why the dark theme, the theme toggle, and the
restart-to-apply theme flow were all removed, leaving light mode only.

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

The app had a dark palette, a light palette, a theme toggle, and a
restart-to-apply theme-switch flow with per-screen `_apply_theme`
hex-replacement maps. That machinery added real complexity — every screen had to
maintain two sets of colours, and the switch flow was a source of inconsistency.
The approved mockups ([DESIGN.md](../project/planning-documents.md)) specify
**light mode only** for a non-technical research audience.

## Options considered

1. **Keep both themes** — more flexible, but doubles the styling surface and the
   maintenance cost, and isn't what DESIGN specifies.
2. **Light mode only** — one palette, one set of tokens, no switch flow.

## Decision

**Light mode only.** Rebuild `design_tokens.py` from DESIGN's light tokens and
delete the dark palette, the theme toggle, `set/current/on_theme_change`, the
prefs file, and every `_apply_theme` hex-replacement map. Bundle the Inter font
so the UI looks the same regardless of installed fonts.

## Consequences

- **One styling surface** — a token change propagates once; there's no second
  palette to keep in sync.
- **No theme toggle and no restart-to-apply flow** — one less piece of chrome and
  one less source of inconsistency.
- A guard test (`tests/test_light_theme.py`) fails if any dark token or theme
  machinery reappears, so the simplification can't silently regress.
- If a dark mode is ever genuinely needed, it would be a new decision (a new ADR),
  not a revival of the old toggle.

## Links

- Commit `0481a11` (light-only design system + new DESIGN app shell).
- [Design system](../developer-guide/design-system.md#light-mode-only), DESIGN.md.
