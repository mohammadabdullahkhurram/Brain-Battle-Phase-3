# ADR-0010 — App settings store

**What this page covers:** why everything that used to be hardcoded in source now
lives in one user-config JSON file.

- **Status:** Accepted
- **Date:** 2026-11-03

## Context

Several things a researcher needs to change were **hardcoded in source**: the
headband→Boxer A/B mapping (`eeg_module.BOXER_LABELS`), the camera resolution and
bitrate (`usb_camera.py`), session defaults, the round bell, and the recordings
folder. A non-technical user can't edit Python, and the DESIGN mockups specify a
real Settings screen (§7) for all of it.

## Options considered

1. **Keep the constants, edit in source** — unworkable for the target audience.
2. **Scatter settings across several files** (QSettings here, a JSON there) — no
   single source of truth.
3. **One settings store** — a single JSON file in the OS config directory, read by
   everything that needs it, edited by the Settings screen.

## Decision

**One `app_settings` store.** A plain JSON file in the user's config directory
holds the headband→boxer mapping + known-headbands list, session defaults, round
bell, and video quality (resolution + bitrate). `capture_dir_resolved()` resolves
the recordings folder — with `BRAIN_BATTLE_CAPTURE_DIR` always winning when set.
The GB/hour copy and disk estimate are **computed** from the stored bitrate
(`gb_per_hour`, `estimate_session_bytes`), not hardcoded. The Settings screen
(`settings_screen.py`) is the edit surface; everything autosaves.

## Consequences

- **A researcher can change resolution, bitrate, the headband mapping, defaults,
  and the label list without touching code** — and the next recording picks it up
  (no restart; see [the settings flow](../developer-guide/key-flows.md#a-settings-change-reaching-the-next-recording)).
- **No hardcoded `BOXER_LABELS` or fixed resolution/bitrate** — a guard
  (`tests/test_app_settings.py`) asserts the old constant is gone; read the
  setting instead.
- **One place to look** for user config. The shared label vocabulary is kept
  separate, in the recordings folder, so a shared folder shares one vocabulary.
- `BRAIN_BATTLE_CAPTURE_DIR` unconditionally overrides a saved custom folder — so
  Settings' "set outside the app" note stays truthful even after a user has
  picked a folder once.

## Links

- Commit `10d08d2` (app settings store; remove hard-coded BOXER_LABELS).
- [Settings user guide](../user-guide/settings.md),
  [Settings module](../developer-guide/module-reference.md#app_settingspy).
