"""
Single authoritative version for Brain Battle. Bump HERE and nowhere else.

Surfaced to: the main window title (brain_battle_main.BrainBattleApp),
session_manifest.json's "version" field (session_storage._write_manifest),
and the human-facing version lines in README.md / CHANGELOG.md (updated by
hand — grep for the old value when bumping).

Note: CHANGELOG "Phase 2.N" headings are historical dev-log numbering, not
release versions; they intentionally do not track __version__.

3.0.0 is a deliberate BREAKING change: the review/label path aligns by the
capture-time clock (manifest capture_clock + per-frame/EEG t_session_seconds),
and pre-3.0 sessions recorded without that data are NOT supported — there is
no legacy-fallback alignment path. The "-dev" pre-release label is carried on
`develop`; it becomes "3.0.0" when develop merges to main to ship.
"""
__version__ = "3.0.0-dev"
