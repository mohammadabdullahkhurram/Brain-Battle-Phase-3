"""Repo-relative paths for Brain Battle.

Anything that needs to locate the bundled ``assets/`` (fonts, the round-bell
WAV), the ``docs/`` tree (the in-app Documentation screen), or the default
recordings folder resolves it through here — so modules can live anywhere
inside the ``brain_battle`` package without each re-deriving the repository
root from their own ``__file__`` (which breaks the moment a module moves
between subpackages).

``REPO_ROOT`` is the directory that contains the ``brain_battle`` package —
the checkout root, where ``assets/`` and ``docs/`` live. With the supported
``pip install -e .`` (editable) install that is still the checkout. A future
packaged build (ROADMAP Phase 6) will ship these as package data and point the
default recordings folder at the user's Documents instead.
"""
from __future__ import annotations

from pathlib import Path

# brain_battle/paths.py -> brain_battle/ -> <repo root>
REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "assets"
DOCS_DIR = REPO_ROOT / "docs"


def default_capture_root() -> str:
    """The default recordings folder when the user hasn't chosen one and
    ``BRAIN_BATTLE_CAPTURE_DIR`` isn't set: a ``Captures/`` folder at the repo
    root. This is the single definition of that default (ROADMAP Phase 6 will
    switch the packaged app to a Documents-based default)."""
    return str(REPO_ROOT / "Captures")
