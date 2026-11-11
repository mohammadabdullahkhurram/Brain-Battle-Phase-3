# ADR-0013 — Reorganise into the `brain_battle` package

**What this page covers:** why the app moved from a flat pile of root-level
modules into a `brain_battle` package grouped by responsibility, and the
packaging/path decisions that came with it.

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

Through Phases 1–3 the app grew to ~30 modules sitting flat at the repository
root, imported by bare name (`from session_storage import …`). That worked, but
it had costs: no at-a-glance structure, no package to install or ship, two dead
modules (`circular_timer`, `sync_utils`) and one stale binary doc lingering, a
module (`sync_beep`) whose name no longer described its job, and asset/doc
lookup scattered across several modules each deriving the repo root from its own
`__file__`. The owner approved a one-off cleanup + reorganisation + merge to
`main` as the first tagged pre-release.

## Options considered

1. **Leave it flat** — zero churn, but the structure problems and the
   impossibility of a clean `pip install` / console script remain.
2. **A package with one flat module list** (`brain_battle/*.py`) — installable,
   but still no grouping.
3. **A package grouped by responsibility into subpackages** — installable,
   self-documenting structure, a natural home for a shared paths helper.

## Decision

**Move every module into a `brain_battle` package** with subpackages
`ui/` · `capture/` · `playback/` · `data/` · `support/`, plus package-top
`app.py` (the shell + `main()`), `version.py`, and `paths.py`. All moves use
`git mv` so history is preserved.

- **Entry points:** `python3 -m brain_battle` (`__main__.py`) and a
  `brain-battle` console script (`pyproject.toml`). `brain_battle_main.py` stays
  at the repo root as a thin launcher shim re-exporting from `brain_battle.app`,
  so the old command and `import brain_battle_main` keep working.
- **`brain_battle/paths.py`** is the single source of repo-relative paths
  (`REPO_ROOT`, `ASSETS_DIR`, `DOCS_DIR`, the default recordings folder —
  then `REPO_ROOT/Captures`, since moved to Documents by
  [ADR-0014](adr-0014-documents-default-folder.md)). `fonts`,
  `round_bell`, `docs_nav` and `app_settings` resolve through it instead of
  their own `__file__`, so modules survive moving between subpackages.
- **Renames:** `sync_beep.py` → `capture/round_bell.py`, class `SyncBeep` →
  `RoundBell` (the name predated the removal of the audio-xcorr sync engine;
  [ADR-0002](adr-0002-remove-audio-xcorr.md)).
- **Packaging:** `pyproject.toml` holds metadata, abstract dependencies, the
  console script, and a dynamic version read from `brain_battle/version.py`;
  `requirements.txt` stays the pinned lockfile (minus the unused
  `ffmpeg-python`). `pip install -e .` (editable) is the supported install for
  now because assets/docs resolve relative to the checkout.
- **Dead code removed:** `circular_timer.py`, `sync_utils/`, and the stale
  `Documentation/Brain_Battle_README.docx`.

## Consequences

- **Clear structure + an installable package** with real entry points; the test
  harness (`test_smoke`, `test_developer_docs`, `test_light_theme`,
  `test_no_placeholders`) now walks the package.
- **Editable-only install for now.** A non-editable install would not find
  `assets/`/`docs/` (they are not yet package data) and the default recordings
  folder is still `REPO_ROOT/Captures`. Both are deferred to **ROADMAP Phase 6**
  (ship assets/docs/sounds/fonts as package data; default the recordings folder
  to the user's Documents for the packaged app).
- **Deferred rename (since done).** The screen classes kept their historical
  `*Tab` names in this pass to bound the diff. They were renamed to `*Screen`
  (`SessionsScreen`, `ReviewLabelScreen`, `SettingsScreen`, `DocsScreen`), with
  their module files, in v3.0.0-beta.2.
- **No behaviour change** — this is structure, packaging, and cleanup; all 27
  test scripts pass, and `python3 -m brain_battle` plus the shim both launch.

## Links

- [Module reference](../developer-guide/module-reference.md),
  [architecture overview](../developer-guide/architecture.md),
  [developer setup & workflow](../developer-guide/setup-and-workflow.md).
- [ADR-0002 — remove audio cross-correlation](adr-0002-remove-audio-xcorr.md).
