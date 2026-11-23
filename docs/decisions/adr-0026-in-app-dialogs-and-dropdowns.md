# ADR-0026 — One in-app dialog and one dropdown, no native ones

**What this page covers:** why every warning, question and message in the app
is the app's own dialog (and every dropdown the app's own), what the native
ones did wrong, and how tests keep it that way.

- **Status:** Accepted
- **Date:** 2026-11-23

## Context

The owner's click-through on a Mac found the questions that matter most —
"Start anyway?" for a poor signal, "Stop the recording now?", quitting while
recording — appearing as native macOS alert boxes, with the system's own
buttons, wording order and (in dark mode) dark colours. Dropdowns used the
system's popup menu. Text boxes and the history drawer turned dark on a Mac set
to dark mode, because the app never told Qt to use a light palette: any widget
without its own stylesheet took the computer's colours. None of this matched
the design (`docs/design/prototype.html`, light only — ADR-0007).

## Decision

- **One dialog component**, `widgets.AppDialog` with the helpers `ask` (a
  question, True/False) and `tell` (a message with one button): a white card
  with 20 px corners on a dimmed cover over the window, a title, the text, and
  buttons on the right; `warn` (orange border) and `danger` (red border and
  title) variants. It is a frameless Qt window, never a native alert. Anything
  that stops a recording makes the safe answer the blue button. The Moment,
  Timing check and Export dialogs and the colour picker are built on it.
- **One dropdown**, `widgets.Dropdown`: the app's colours, its own chevron and
  a light list (`combobox-popup: 0`, so not the macOS menu).
- **Light palette pinned** at start-up (`design_tokens.pin_light_palette`), and
  the app stylesheet styles every text field white with dark text.
- The only system windows left are the **folder / file pickers** — people
  expect their computer's own, with its sidebar and network drives.

## Consequences

- `tests/test_dialogs.py` fails if any `QMessageBox`, `QColorDialog`,
  `QInputDialog`, `QFontDialog`, `QErrorMessage`, `QProgressDialog` or bare
  `QComboBox` appears in the app, or a dialog class does not derive from
  `AppDialog`; it also checks fields stay light with a dark palette handed in.
- Tests answer dialogs with `widgets.dialog_responder` instead of patching Qt
  classes; `widgets.answered_dialogs` records what was asked.
- A dialog covers the window it belongs to; on Recording the cover is light so
  the cameras stay visible (the recording never pauses).
