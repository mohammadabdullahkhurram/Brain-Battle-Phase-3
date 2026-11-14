# ADR-0019 — One labels file per labeller

**What this page covers:** how the app stores labels now that more than one
person may label the same recording, how a labeller is identified without
accounts, and how agreement between labellers is measured.

- **Status:** Accepted (extends [ADR-0006](adr-0006-labels-v2.md): the label
  entries are unchanged, the file they live in is not)
- **Date:** 2026-11-13

## Context

A recording had one `labels.json`. The owner told us that more than one person
may label the same recording — for instance to check how well labellers agree —
and that the recordings folder will be shared between computers. With one file,
the second person to save silently overwrites the first.

## Decision

**Each labeller's labels live in their own file inside the recording:**
`labels/<labeller>.json`.

- Two labellers never write to the same file, so nothing has to be locked or
  merged between people, on one computer or across a shared folder.
- The recording folder stays self-contained: copy it and you have everybody's
  labels.
- **Identity is a name** — "Your name", asked once and stored in that
  computer's settings. No accounts, no passwords. The file name is the name
  lower-cased with punctuation and spaces turned into hyphens, so "Maya K" and
  "maya  k" are the same person.
- You always label **as yourself**. Other labellers' labels are read-only and
  can be shown beside yours (Compare), in grey.
- The **same person on two computers** is handled by merging on save: a save
  first takes in anything that appeared in the file since it was read, except
  labels deleted locally.
- **Marked moments** stay in the recording's manifest; each labeller's file
  records which ones that person has named. (They used to be removed from the
  manifest when named, which would have taken them away from everyone else.)
- **Migration is automatic.** An old `labels.json` is read everywhere as it is;
  the first time the recording is opened in Review & Label it is moved to
  `labels/<created_by>.json` (or "Earlier labels") and the original kept as
  `labels.json.v2.bak`. A labeller can take such a set over ("These are mine").
- **Downstream:** `recording.xdf` gets one marker stream per labeller
  (`BrainBattle_Labels_<labeller>`), `labels.csv` and `events.csv` get a
  `labeller` column, and an export with two or more labellers includes
  `agreement.csv`. History lines carry the labeller's name.

**Agreement** is a count, in a sentence: two labels are the same event when
they have the same label, the same boxer, and start within a tolerance (0.5 s
by default; overlapping counts for lasting labels). "You and Jonas marked the
same event 18 times out of 24 (75% agreement)."

## Consequences

- Nobody can overwrite anybody else's labels.
- A misspelled name creates a second labeller. The labels are not lost (they
  show up in Compare), and correcting the name in Settings switches back.
- Anyone can type anyone's name. That is acceptable for a small trusting team
  and is the thing real accounts (Phase 7) would fix.
- "Labelled" in Sessions means *somebody* has labelled it; the row's button
  tells you whether *you* have.
- Analysts must pick a labeller (or reconcile) before counting events — the
  documentation says so where the tables are loaded.

## Alternatives considered

- **One file with a `labeller` field per label.** Rejected: two people saving
  the same file is exactly the overwrite problem, and solving it needs locking
  or merging on every save.
- **A database.** Rejected: the recording folder would no longer be
  self-contained, and a database file on a shared network drive is fragile.
- **Cohen's kappa or similar** as the agreement figure. Rejected for the
  screen: it needs a fixed set of items to rate, which free-placed events do
  not have, and the number means little to most users. The export gives
  analysts what they need to compute it.
