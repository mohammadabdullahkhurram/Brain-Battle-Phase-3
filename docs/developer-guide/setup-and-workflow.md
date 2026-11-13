# Developer setup & workflow

**What this page covers:** how to get the code running for development, the
branch and commit conventions, the role `AGENTS.md` plays, the AI-assisted
development workflow, and the coding conventions a change should follow.

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

Recordings land in `~/Documents/Brain Battle Data` by default (change it in
Settings, or set `BRAIN_BATTLE_CAPTURE_DIR`); see
[ADR-0014](../decisions/adr-0014-documents-default-folder.md).

## Branch policy: `main` and `develop`

- **`main` is the stable, reviewed version.** It only ever advances when the
  owner has reviewed and approved the work, and each such merge is marked with a
  version tag (e.g. `v3.0.0-beta.2`).
- **`develop` is the staging area.** All new work happens on `develop` first.
- **The flow:** work on `develop` → run the full `tests/` suite → commit per
  logical change → the owner reviews → on approval, merge `develop` into `main`
  (a merge commit) and tag the release. After such a release `main` and
  `develop` point at the same commit.

Don't push to `main` without that owner approval. If you're starting fresh work,
do it on `develop`.

## Commits

- **Commit locally per logical change.** One coherent change per commit, with a
  message that says what changed and why.
- Add a co-author trailer when an AI agent made the commit (for example, with
  Claude Code: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`).
- **Run everything in `tests/` before and after every change, and report
  pass/fail** — not just "I ran the tests" (the [working agreement](../project/status.md)).
- **Documentation is part of every change** — a change isn't done until its docs
  are updated. The checklist (user guide for UI/wording changes, file-formats for
  any recording/export file change, module reference for added/removed/renamed
  modules, a new ADR for a significant decision, re-rendered diagrams, a CHANGELOG
  entry) is enforced by the honesty tests. See
  [docs-guide.md](../contributing/docs-guide.md#documentation-is-part-of-every-change-owner-rule).

## The role of `AGENTS.md`

`AGENTS.md` at the repo root is the shared, tool-neutral instructions file for
AI coding agents — read automatically by OpenAI Codex, and by Claude Code via a
one-line `CLAUDE.md` that just imports it (`@AGENTS.md`), so there is a single
source. It describes the **present** state of the project for whoever (human or
AI agent) is working on it: the current architecture, the module map, how to run
and test, the project situation (broken hardware), the working agreement, and
the branch policy. It is the orientation document a session reads first. The
long-form **history** lives in [`docs/archive/history.md`](../archive/history.md),
not `AGENTS.md` — `AGENTS.md` keeps only a pointer. When the architecture
changes, update `AGENTS.md`'s module map in the same change.

## AI-assisted development workflow

Brain Battle is developed with AI coding agents — primarily
[Claude Code](https://claude.com/claude-code) and
[OpenAI Codex](https://openai.com/codex), reading the same `AGENTS.md` — under
an explicit **owner-approval workflow**:

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

## Versions & tags

Git tags are how we find our way back to any earlier version of the project.

- **Every merge into `main` gets a version tag** — `vX.Y.Z` or `vX.Y.Z-beta.N`
  (e.g. `v3.0.0-beta.2`), matching `brain_battle/version.py`.
- **A snapshot tag is created on `develop` before any large or autonomous
  change** — `pre-<task>-<date>` (e.g. `pre-fable-2026-11-13`), so there is
  always a known-good point to return to.
- **`milestone/<name>` tags mark finished roadmap phases** (e.g.
  `milestone/phase-2-data-safety`, `milestone/phase-3-redesign`).

Tags are annotated (`git tag -a <name> <commit> -m "..."`) and pushed with
`git push origin --tags`. Never move or delete an existing tag.

**Look at an old version** (read-only; don't commit here):

```bash
git tag -l -n1            # list tags with their descriptions
git checkout <tag>        # e.g. git checkout v2.1.0
git checkout develop      # come back
```

**Start a branch from an old version** (to restore or build on it):

```bash
git switch -c restore-<name> <tag>
```

> **Warning — resetting a shared branch to a tag rewrites history.** Running
> `git reset --hard <tag>` on `develop` or `main` (and force-pushing) discards
> every commit after the tag for everyone. Only do this after asking the owner.
> Prefer a `restore-<name>` branch or a revert commit.

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
