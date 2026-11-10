"""Brain Battle — PyQt6 desktop app for neuroscience boxing capture.

The application code lives in subpackages grouped by responsibility:

- ``brain_battle.ui``       — the sidebar shell, the four DESIGN screens, design tokens, fonts
- ``brain_battle.capture``  — cameras, headbands/EEG, the phase controller, contact quality, keep-awake, round bell
- ``brain_battle.playback`` — the PyAV review-time player
- ``brain_battle.data``     — session storage/history, labels + vocabulary, export, settings
- ``brain_battle.support``  — logging and the Qt-free docs-navigation helpers

Run it with ``python3 -m brain_battle`` (or the ``brain-battle`` console
script); ``brain_battle_main.py`` at the repo root is a thin launcher kept for
backward compatibility.
"""
from brain_battle.version import __version__

__all__ = ["__version__"]
