# ADR-0014 — Default recordings folder in the user's Documents

**What this page covers:** why the default recordings folder moved from a
`Captures/` folder at the repository root to a `Brain Battle Data` folder in
the user's OS Documents folder.

- **Status:** Accepted
- **Date:** 2026-11-11

## Context

Through the package reorganisation ([ADR-0013](adr-0013-package-reorganisation.md))
the default recordings folder was `REPO_ROOT/Captures` — the right place for a
developer running from a checkout, but wrong for the real user: a non-technical
researcher running a packaged, double-click app has no "repo root", and burying
their sessions next to the source code is surprising and easy to lose. Sessions
are the owner's primary data; the default location should be somewhere a person
can find in their file manager.

## Decision

**Default the recordings folder to `~/Documents/Brain Battle Data`** (the OS
Documents folder on macOS, Windows, and Linux). This is defined in exactly one
place — `brain_battle/paths.py` (`documents_dir()` + `default_capture_root()`)
— and `brain_battle/data/app_settings.py` resolves through it.

Precedence is unchanged (`AppSettings.capture_dir_resolved`):

1. `BRAIN_BATTLE_CAPTURE_DIR` (set outside the app) always wins.
2. else a folder the user picked in **Settings → Change folder…**.
3. else this default.

On Linux the XDG `XDG_DOCUMENTS_DIR` is honoured if configured, else
`~/Documents`.

## Consequences

- **Recordings land somewhere findable** — the user's Documents, not inside a
  source checkout. The repo-root `Captures/` folder and its `.gitignore` entry
  are removed.
- **Tests and tooling are unaffected** — they either set
  `BRAIN_BATTLE_CAPTURE_DIR` / their own `capture_dir` to a temp folder, or
  (for a few that touch the real default, like the demo generator's
  refuse-real-folder guard) only read the path. The headless verification runs
  under a temporary `HOME` so the real Documents folder is never touched.
- **The packaged-app TODO shrinks** — ROADMAP Phase 6 no longer needs a
  Documents-default item; only shipping `assets/`/`docs/` as package data
  remains for a non-editable install.

## Links

- [ADR-0013 — package reorganisation](adr-0013-package-reorganisation.md),
  [research & data — recording folder](../research-and-data/recording-folder.md),
  [Settings](../user-guide/settings.md).
