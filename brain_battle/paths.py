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

import os
import sys
from pathlib import Path

# brain_battle/paths.py -> brain_battle/ -> <repo root>
REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "assets"
DOCS_DIR = REPO_ROOT / "docs"

#: Name of the folder (inside the user's Documents) where recordings are saved
#: by default.
DEFAULT_CAPTURE_DIRNAME = "Brain Battle Data"


def documents_dir() -> Path:
    """The current user's OS "Documents" folder, cross-platform.

    macOS and Windows both use ``~/Documents``. On Linux the XDG user-dirs
    ``XDG_DOCUMENTS_DIR`` is honoured if configured, else ``~/Documents``.
    """
    home = Path(os.path.expanduser("~"))
    if sys.platform not in ("darwin", "win32"):
        xdg = os.environ.get("XDG_DOCUMENTS_DIR")
        if xdg:
            return Path(os.path.expandvars(xdg))
    return home / "Documents"


def default_capture_root() -> str:
    """The default recordings folder when the user hasn't chosen one and
    ``BRAIN_BATTLE_CAPTURE_DIR`` isn't set: a ``Brain Battle Data`` folder in
    the user's OS Documents folder. This is the single definition of that
    default. A folder chosen in Settings and ``BRAIN_BATTLE_CAPTURE_DIR`` both
    take priority (see ``AppSettings.capture_dir_resolved``)."""
    return str(documents_dir() / DEFAULT_CAPTURE_DIRNAME)
