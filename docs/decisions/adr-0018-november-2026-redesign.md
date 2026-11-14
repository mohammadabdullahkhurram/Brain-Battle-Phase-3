# ADR-0018 — The November 2026 redesign

**What this page covers:** why the interface was redesigned in November 2026,
what changed on each screen, and what was deliberately kept. It replaces the
screen descriptions approved on 2026-10-27; the rewritten
`docs/DESIGN.md` is the result.

- **Status:** Accepted (supersedes the screen layouts of the 2026-10-27 design;
  [ADR-0007](adr-0007-light-only-design.md) light-only and
  [ADR-0008](adr-0008-merged-review-label.md) one merged Review & Label still
  stand; [ADR-0016](adr-0016-standard-eeg-viewer.md) the EEG viewer still
  stands)
- **Date:** 2026-11-13

## Context

The first design did its job — it replaced a dense engineering tool with
plain-language screens — but the owner's verdict on the previous pass was that
the interface had been polished, not improved. Three things had also changed:

- **More than one person labels a recording**
  ([ADR-0019](adr-0019-one-labels-file-per-labeller.md)), so Review & Label
  needs a notion of "who am I" and a way to compare.
- The lab uses **Mac and Windows**, so wording like "Show in Finder" is wrong
  half the time.
- The recordings folder may be **shared between computers**
  ([ADR-0020](adr-0020-shared-folder-safety.md)), so "the folder is not there"
  is a normal state the screens must explain.

The users are the same: non-technical researchers and assistants running
sessions in a gym or lab, and labellers and analysts at a desk for hours.

## Decision

Redesign around the three things people come to do — **start a recording, label
a recording, export** — and make each of them obvious and short.

**The frame.** *New recording* is the one filled button at the top of the
sidebar, on every screen. Below the pages the sidebar now carries who is
labelling and a one-click *Hide names* switch. Type is one step larger
throughout (14 px body), status is always an icon *shape* plus words, and one
shared component library (`ui/widgets.py`) replaces per-screen copies of
buttons, cards and status lines.

**Sessions.** One row of controls instead of two; one clean list instead of a
stack of bordered boxes; the whole row is clickable; the length moved under the
date to free a column for *who labelled*; the button says **Label** until *you*
have labelled it; export is available from the row's menu without opening the
recording.

**New recording: four steps became three screens.** "Connect devices" and "Set
up session" were two steps only because of history. Devices connect by
themselves and take a few seconds; typing names takes about as long. Putting
the devices on top and the plan with the Start button underneath lets both
happen at once, removes a click and a screen from every recording, and keeps
the reason Start is unavailable (no space, no folder) next to the button.

**Recording.** Built for a glance from across a gym: a single status strip that
is green or says what is wrong; the phase in words on a card *tinted* by the
phase; a countdown as large as the card allows; and *Mark this moment* as a
very large button in the middle of the screen rather than a small one in a
corner — it is the one thing the operator presses under pressure. The four
sensor-contact indicators are back on this screen (the previous pass had
reduced them to a line to save space) as a compact strip that keeps each
sensor in a fixed place.

**Review & Label.** The transport and the timeline are pinned under the videos
and never scroll away — they are how a labeller navigates. The brain-activity
card can be folded away and gives up height on a small window, because a
labeller mostly watches video. Lane names moved into a gutter so they never
sit on top of labels. Added: 2× speed, jump to previous/next label, one-second
skip, and the Compare tab.

**Settings.** A list of sections on the left (the page had grown long), with
"You" first.

## What was kept, deliberately

- **Light mode only** (owner requirement).
- The vocabulary: plain words everywhere, electrode names only in the EEG
  display.
- Boxer colours (teal A, orange B), always with the letter.
- Every safety behaviour: inline stop confirmation, quit confirmation while
  recording, the disk check.

## Consequences

- `docs/DESIGN.md` was rewritten; where code and DESIGN.md disagree, ask.
- The user guide, screenshots and Help-button anchors changed
  (`step-1-get-ready`, `step-2-recording`, `step-3-saved`).
- In code, `ConnectStep` and `SetupStep` still exist as the two halves of the
  Get ready page, and `STEP_CONNECT` / `STEP_SETUP` are aliases of
  `STEP_READY`, so existing callers keep working.
- At the smallest window (1180×720) the Get ready screen is dense and the
  brain-activity display on it is short; it is comfortable from 1280×800.
- Before/after pictures of every screen are in
  `docs/reviews/redesign/`, with the reasoning in
  `docs/reviews/2026-11-redesign.md`.

## Alternatives considered

- **Keep four steps, restyle them.** Rejected: it is what the previous pass
  did.
- **A wizard with one question per screen.** Rejected: slower for people who do
  this every day, and it hides the device status while you type.
- **A dark Recording screen** for contrast in a gym. Rejected: light mode only
  is an owner requirement; phase tinting gives the at-a-glance signal instead.
