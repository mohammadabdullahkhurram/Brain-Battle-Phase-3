# Planning documents

**What this page covers:** the source documents behind the design and
architecture — the approved UI spec, the build plan, the playback investigation,
the architecture/evaluation report, and the long-form history — so they're
reachable from inside the app, not just on GitHub.

`DESIGN.md` and `ROADMAP.md` are **current** and live at the `docs/` root. The
superseded planning reports and the long-form history have been moved to
`docs/archive/`; they're referenced, not duplicated, by the rest of the
documentation. Each is linked below with what it is and when to read it.

## The documents

| Document | What it is | Read it when… |
|---|---|---|
| [DESIGN.md](../DESIGN.md) | The approved UI spec (mockups, copy, vocabulary, tokens, every screen) — the **source of truth for all UI work**. | You're changing anything a user sees. |
| [ROADMAP.md](../ROADMAP.md) | The full build plan: every phase and item with rationale and the files it will touch. | You're planning or picking up a phase. |
| [PLAYBACK_PROPOSAL.md](../archive/PLAYBACK_PROPOSAL.md) | The measured comparison of three video-playback approaches that chose PyAV. | You're touching playback or want the numbers behind [ADR-0005](../decisions/adr-0005-pyav-playback.md). |
| [ARCHITECTURE_AND_EVALUATION.md](../archive/ARCHITECTURE_AND_EVALUATION.md) | The honest gap analysis of the pre-capture-clock `main`, and the ranked improvement survey that led to the capture clock. | You want the reasoning behind [ADR-0001](../decisions/adr-0001-capture-time-clock.md)/[ADR-0002](../decisions/adr-0002-remove-audio-xcorr.md). |
| [2026-11 review](../reviews/2026-11-fable-review.md) | The write-up of the November 2026 improvement pass (3.1.0-dev): what changed and why, what was removed, test results, what still needs hardware, and the decisions waiting for the owner. | You're reviewing `develop` before merging it to `main`. |
| [Project history](../archive/history.md) | The long-form narrative: the capture-clock build (STEPs 1–5), the v2 cleanup phase, and the v1→v2 migration. | You want the story behind today's architecture. |

## How these relate to the rest of the docs

- **DESIGN.md** is enforced by the UI tests and the design system; see
  [design system & UI conventions](../developer-guide/design-system.md).
- **ROADMAP.md** is summarised (not duplicated) in [Roadmap](roadmap.md).
- **PLAYBACK_PROPOSAL.md** and **ARCHITECTURE_AND_EVALUATION.md** are the
  evidence behind the [Decisions (ADRs)](../decisions/index.md).
- **history.md** is the narrative behind [Version history](version-history.md).

> Note on the two historical reports: `ARCHITECTURE_AND_EVALUATION.md` and parts
> of `history.md` mention modules that have since been **removed or renamed**
> (`SyncReviewTab`, `live_session_tab.py`, `vlc_module.py`, `sync_engine.py`).
> Both say so at the top — they're left as-written for the record. For what exists
> now, use the [module reference](../developer-guide/module-reference.md) and
> `AGENTS.md`'s module map.
