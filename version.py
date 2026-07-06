"""
Single authoritative version for Brain Battle. Bump HERE and nowhere else.

Surfaced to: the main window title (brain_battle_main.BrainBattleApp),
session_manifest.json's "version" field (session_storage._write_manifest),
and the human-facing version lines in README.md / CHANGELOG.md (updated by
hand — grep for the old value when bumping).

Note: CHANGELOG "Phase 2.N" headings are historical dev-log numbering, not
release versions; they intentionally do not track __version__.
"""
__version__ = "2.1.0"
