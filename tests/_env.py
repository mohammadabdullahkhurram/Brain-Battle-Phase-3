"""
_env.py — isolate a test script from the real computer
──────────────────────────────────────────────────────
Import this FIRST in a test script (``import _env``). It points the app's
settings file at a throwaway temp file (unless the script already chose one) so
a test run never reads or changes the real user's settings, and gives that
settings file a labeller name ("Test Labeller") — Review & Label saves labels
under the name of whoever is labelling, and asks for one when there is none.
It also selects Qt's offscreen platform when none is set, so scripts can be run
directly on a machine (or CI runner) with no display.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LABELLER = "Test Labeller"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if not os.environ.get("BRAIN_BATTLE_CONFIG"):
    _cfg = os.path.join(tempfile.mkdtemp(prefix="bb_test_cfg_"), "settings.json")
    with open(_cfg, "w", encoding="utf-8") as _f:
        json.dump({"labeller_name": LABELLER}, _f)
    os.environ["BRAIN_BATTLE_CONFIG"] = _cfg


def set_labeller(name: str = LABELLER) -> str:
    """Make ``name`` the saved "Your name" in the (temporary) settings file."""
    import brain_battle.data.app_settings as app_settings
    return app_settings.set_labeller_name(name)
