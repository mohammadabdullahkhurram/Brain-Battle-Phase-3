"""
_env.py — isolate a test script from the real computer
──────────────────────────────────────────────────────
Import this FIRST in a test script (``import _env``). It points the app's
settings file at a throwaway temp file (unless the script already chose one) so
a test run never reads or changes the real user's settings, and gives that
settings file a labeller name ("Test Labeller") — Review & Label saves labels
under the name of whoever is labelling, and asks for one when there is none.
It also selects Qt's offscreen platform when none is set, so scripts can be run
directly on a machine (or CI runner) with no display, and shortens the 15 s
lead-in before the baseline to 2 s so simulated recordings stay quick.

LSL: every script gets its own ``BRAIN_BATTLE_LSL_TAG``, which goes into the
source id of every stream it publishes (``capture/lsl_outlets.source_id``).
``lsl_resolve`` / ``lsl_inlet`` find a stream by name AND that source id, so a
script never picks up another script's (or a running app's) stream of the same
name, and they wait properly — retrying until a deadline — instead of trusting
one fixed timeout.
"""
import json
import os
import sys
import tempfile
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LABELLER = "Test Labeller"
# Recordings start with a 15 s lead-in; simulated test recordings use 2 s so
# the suite stays quick. Tests that check the real length read LEAD_IN_DEFAULT.
TEST_LEAD_IN_SEC = 2

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BRAIN_BATTLE_LSL_TAG", f"test-{os.getpid()}-{uuid.uuid4().hex[:6]}")
if not os.environ.get("BRAIN_BATTLE_CONFIG"):
    _cfg = os.path.join(tempfile.mkdtemp(prefix="bb_test_cfg_"), "settings.json")
    with open(_cfg, "w", encoding="utf-8") as _f:
        json.dump({"labeller_name": LABELLER}, _f)
    os.environ["BRAIN_BATTLE_CONFIG"] = _cfg


import brain_battle.capture.session_controller as _session_controller  # noqa: E402
_session_controller.LEAD_IN_SEC = TEST_LEAD_IN_SEC


def set_setting(key: str, value) -> None:
    """Change one saved setting in the (temporary) settings file — e.g.
    ``set_setting("lsl_streams", True)`` for a test that checks the live LSL
    streams, which are off by default."""
    import brain_battle.data.app_settings as app_settings
    s = app_settings.load()
    setattr(s, key, value)
    s.save()


def set_labeller(name: str = LABELLER) -> str:
    """Make ``name`` the saved "Your name" in the (temporary) settings file."""
    import brain_battle.data.app_settings as app_settings
    return app_settings.set_labeller_name(name)


def lsl_resolve(name: str, timeout: float = 10.0):
    """The StreamInfo of THIS process's stream ``name`` (None if it does not
    appear within ``timeout``)."""
    import pylsl
    from brain_battle.capture.lsl_outlets import source_id
    pred = f"name='{name}' and source_id='{source_id(name)}'"
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        found = pylsl.resolve_bypred(pred, 1, max(0.5, min(2.0, left)))
        if found:
            return found[0]
        if left <= 0:
            return None


def lsl_inlet(name: str, outlet=None, timeout: float = 10.0):
    """An open inlet on THIS process's stream ``name``, or None. With the
    ``outlet`` that publishes it, also waits until that outlet sees the inlet
    as a consumer, so a sample pushed next is not lost."""
    import pylsl
    info = lsl_resolve(name, timeout)
    if info is None:
        return None
    inlet = pylsl.StreamInlet(info)
    deadline = time.monotonic() + timeout
    while True:
        try:
            inlet.open_stream(timeout=max(0.5, min(3.0, deadline - time.monotonic())))
            break
        except RuntimeError:          # pylsl.util.TimeoutError (a RuntimeError)
            if time.monotonic() >= deadline:
                return None
    if outlet is not None:
        outlet.wait_for_consumers(max(0.5, deadline - time.monotonic()))
    return inlet
