"""
render_screens.py — headless screenshots of every app page at the two target
window sizes (DESIGN: fits 1440×900, usable at 1280×800), into a gitignored
screens/ folder so the owner can review the light design.

    python3 tools/render_screens.py [out_dir]

Not a test; purely for visual review.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    import vlc  # noqa: F401
except Exception:
    import types
    sys.modules["vlc"] = types.ModuleType("vlc")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QSize

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "screens")
SIZES = [(1440, 900), (1280, 800)]


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    from fonts import register_fonts
    register_fonts()
    import brain_battle_main as bbm

    os.makedirs(OUT, exist_ok=True)
    win = bbm.BrainBattleApp()
    pages = [
        (win.IDX_SESSIONS, "01_sessions"),
        (win.IDX_RECORD,   "02_new-recording"),
        (win.IDX_REVIEW,   "03_review-and-label"),
        (win.IDX_SETTINGS, "04_settings"),
    ]
    saved = []
    for w, h in SIZES:
        win.resize(w, h)
        win.show()
        for idx, name in pages:
            win._shell.set_current_index(idx)
            for _ in range(3):
                app.processEvents()
            pm = win.grab()
            fn = os.path.join(OUT, f"{name}_{w}x{h}.png")
            pm.save(fn)
            saved.append(fn)
    try:
        win.live_tab.shutdown(); win.sync_tab.shutdown()
    except Exception:
        pass
    print(f"rendered {len(saved)} screenshots into {OUT}")
    for fn in saved:
        print("  ", os.path.relpath(fn, ROOT))


if __name__ == "__main__":
    main()
