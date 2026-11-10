# Developer setup & workflow

**What this page covers:** how to get the code running for development, the
branch and commit conventions, the role `CLAUDE.md` plays, how this project is
built with Claude Code, and the coding conventions a change should follow.

## Setup

See [Installation](../getting-started/installation.md) for the full, OS-specific
walkthrough. In short:

```bash
pip install -e .
```

`pip install -e .` is an **editable** install: it reads `pyproject.toml`
(metadata, dependencies, the `brain-battle` console script) and installs the
pinned `requirements.txt` set. It is the supported install for now because the
app resolves its bundled `assets/` and `docs/` relative to the checkout root
via `brain_battle/paths.py` (`REPO_ROOT`); a non-editable install would not
find them yet — proper packaging of assets/docs is [ROADMAP](../ROADMAP.md)
Phase 6.

You also need `ffmpeg` on `PATH` (per-camera H.264 recording, export, probing;
recording is blocked at pre-flight without it). Review & Label playback is
in-process via PyAV (`av`, which bundles its own ffmpeg) — **no VLC**. Then run:

```bash
python3 -m brain_battle
```

(or the `brain-battle` console script, or the repo-root shim
`python3 brain_battle_main.py` — all three run the same `main()`).

With no hardware, develop against the [simulation mode and demo
generator](simulation-and-demo.md):

```bash
BRAIN_BATTLE_SIMULATE=1 python3 -m brain_battle
python3 tools/generate_demo_session.py --out /tmp/demo
```

Recordings land in `Captures/` next to the source unless
`BRAIN_BATTLE_CAPTURE_DIR` is set.

## Branches: `develop` and `main`

- **`main`** is the stable, shippable baseline — currently `2.1.0`.
- **`develop`** is the long-lived working branch — currently `3.0.0-dev`.
- `develop` merges to `main` **only** when a version is verified and ready to
  ship. `3.0.0` is a deliberate breaking change (alignment by the capture-time
  clock; pre-3.0 sessions are not supported), and the eyes-on-glass + EEG
  clock-source checks are still open, so that merge has not happened yet — it is
  explicitly not automatic (see [ROADMAP.md](../project/roadmap.md) Phase 1).

Never push unless explicitly told to. If you're on the default branch, branch
first.

## Commits

- **Commit locally per logical change.** One coherent change per commit, with a
  message that says what changed and why.
- Co-author trailer on commits made with Claude Code:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Run everything in `tests/` before and after every change, and report
  pass/fail** — not just "I ran the tests" (the [working agreement](../project/status.md)).
- **Documentation is part of every change** — a change isn't done until its docs
  are updated. The checklist (user guide for UI/wording changes, file-formats for
  any recording/export file change, module reference for added/removed/renamed
  modules, a new ADR for a significant decision, re-rendered diagrams, a CHANGELOG
  entry) is enforced by the honesty tests. See
  [docs-guide.md](../contributing/docs-guide.md#documentation-is-part-of-every-change-owner-rule).

## The role of `CLAUDE.md`

`CLAUDE.md` at the repo root describes the **present** state of the project for
whoever (human or Claude Code) is working on it: the current architecture, the
module map, how to run and test, the project situation (broken hardware), and the
working agreement. It is the orientation document a session reads first. The
long-form **history** lives in [`docs/project/history.md`](../project/history.md),
not `CLAUDE.md` — `CLAUDE.md` keeps only a pointer. When the architecture changes,
update `CLAUDE.md`'s module map in the same change.

## How this project is built with Claude Code

Brain Battle is developed with [Claude Code](https://claude.com/claude-code)
under an explicit **owner-approval workflow**:

- Work is sequenced as **phases** in [`ROADMAP.md`](../project/roadmap.md). **The
  owner approves each phase before it starts** — a session never starts the next
  phase on its own initiative, even if the current one is finished.
- Each phase is driven by a written prompt from the owner, carried out against the
  demo session and simulated devices, and **gated by the test suite** before and
  after.
- Changes are reviewed by the owner (the `ROADMAP.md` "review fixes" entries in
  the [CHANGELOG](../project/version-history.md) are examples of that loop), and
  committed locally per logical change; pushes happen only when told.
- `DESIGN.md` is the approved UI contract; where the build must differ, the
  difference is written down (`ROADMAP.md`'s "DESIGN differences" note) rather
  than guessed.

The decisions that came out of this process are written up as
[ADRs](../decisions/index.md).

## Coding conventions

- **Match the surrounding code.** New code should read like the file it's in —
  the same naming, comment density, and idiom.
- **Keep timing/parsing/alignment logic Qt-free** so it stays unit-testable
  (`session_controller`, `labels_store`, `label_vocab`, `docs_nav`,
  `pyav_player`'s pure layers are the models).
- **Read settings, don't hardcode.** Resolution, bitrate, the headband→boxer
  mapping, defaults, and the label list all live in `app_settings`/`label_vocab`;
  don't reintroduce a hardcoded constant ([ADR-0010](../decisions/adr-0010-app-settings-store.md)).
- **Use the design tokens.** No hex literals or ad-hoc fonts in screens — call a
  `design_tokens` accessor or `qss_*` factory ([design system](design-system.md)).
- **Plain language in the UI.** Follow `DESIGN.md`'s vocabulary; the
  placeholder/jargon gate enforces it.
- **Threads must be stopped and waited before teardown** — see the
  [threading model](threading-model.md#safe-teardown-and-the-bug-that-taught-us-how).
- **Logging, not `print`.** `import logging; log = logging.getLogger(__name__)`;
  `log_setup` configures console + per-session file handlers.
- **One authoritative version** in `version.py`; bump there and update the
  CHANGELOG top entry (the honesty test checks they match).
