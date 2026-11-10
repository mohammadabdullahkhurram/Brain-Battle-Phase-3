"""Thin launcher shim — kept for backward compatibility.

The application now lives in the ``brain_battle`` package; the canonical way to
run it is ``python3 -m brain_battle`` (or the ``brain-battle`` console script
after ``pip install -e .``). This module re-exports the shell and ``main()`` so
``python3 brain_battle_main.py`` keeps working, and so existing tests/tools that
``import brain_battle_main`` continue to resolve ``BrainBattleApp`` / ``MIN_WINDOW``.
"""
from brain_battle.app import BrainBattleApp, MIN_WINDOW, main
# Re-exported so tests that monkeypatch ``brain_battle_main.QMessageBox`` still
# resolve the same symbol the shell uses (closeEvent looks it up in app.py).
from brain_battle.app import QMessageBox

__all__ = ["BrainBattleApp", "MIN_WINDOW", "QMessageBox", "main"]

if __name__ == "__main__":
    main()
