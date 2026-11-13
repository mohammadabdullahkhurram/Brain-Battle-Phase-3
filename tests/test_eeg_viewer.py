"""
EEG viewer tests (ui/eeg_viewer.py) — the conventional EEG display used in
Review & Label and on the Connect / Recording steps.

Checks the things a reader of the display relies on: the window follows the
playhead (review) or the newest sample (live); the µV scale is what the scale
bar says, so a known-amplitude signal is drawn at the right height; the display
filter removes drift and mains-type noise without touching the data; markers
and channel labels are really painted; and the Review card's controls drive
both boxers' viewers.

Run:  python3 tests/test_eeg_viewer.py
"""
import os
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BRAIN_BATTLE_CONFIG"] = os.path.join(tempfile.mkdtemp(), "settings.json")

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

import brain_battle.ui.eeg_viewer as ev
from brain_battle.data.signals import EEG_CHANNELS

_failures = []
SR = 256


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _signal(seconds=60, amp=50.0, hz=10.0, drift=300.0):
    t = np.arange(int(seconds * SR)) / SR
    y = amp * np.sin(2 * np.pi * hz * t)
    chans = {c: y + drift for c in EEG_CHANNELS}      # big constant offset on every channel
    return t, chans


def _colour_rows(img, colour, tol=40):
    """Row indices of an image that contain pixels close to `colour`."""
    c = QColor(colour)
    rows = set()
    for y in range(0, img.height()):
        for x in range(60, img.width() - 60, 3):
            p = img.pixelColor(x, y)
            if abs(p.red() - c.red()) + abs(p.green() - c.green()) + abs(p.blue() - c.blue()) < tol:
                rows.add(y)
                break
    return rows


def test_windows():
    t, chans = _signal()
    v = ev.EEGViewer()
    v.set_data(t, chans); v.set_timebase(10); v.set_view(30.0)
    check("review: the window is centred on the playhead", v.window() == (25.0, 35.0))
    tt, yy = v.visible_samples()
    check("review: only the window's samples are drawn, all five channels",
          abs(tt[0] - 25.0) < 0.01 and abs(tt[-1] - 35.0) < 0.01 and yy.shape[1] == 5)
    v.set_timebase(2)
    check("timebase control changes the span", v.window() == (29.0, 31.0))

    live = ev.EEGViewer(channels=EEG_CHANNELS[:4], live=True)
    live.set_timebase(5); live.set_data(t, chans)
    check("live: the window ends at the newest sample",
          abs(live.window()[1] - t[-1]) < 1e-9 and abs(live.window()[0] - (t[-1] - 5)) < 1e-9)
    live.set_data(None, {})
    check("no data → nothing to draw (and no crash)", live.visible_samples() == (None, None)
          and not live.grab().isNull())


def test_display_processing():
    t, chans = _signal(amp=50.0, hz=10.0, drift=300.0)
    v = ev.EEGViewer(); v.set_data(t, chans); v.set_view(30.0)
    v.set_filter(False)
    _tt, raw = v.visible_samples()
    check("drift removal: a 300 µV offset is taken out of the picture",
          abs(float(np.median(raw[:, 0]))) < 1.0 and 45 < np.abs(raw[:, 0]).max() < 55)
    v.set_filter(True)
    _tt, flt = v.visible_samples()
    check("display filter keeps a 10 Hz rhythm (within 10 %)", 45 < np.abs(flt[:, 0]).max() < 55,
          f"{np.abs(flt[:, 0]).max():.1f}")
    # 0.2 Hz sway + 60 Hz mains on top: both outside 1–45 Hz
    noisy = {c: y + 200 * np.sin(2 * np.pi * 0.2 * t) + 80 * np.sin(2 * np.pi * 60 * t)
             for c, y in chans.items()}
    v.set_data(t, noisy); v.set_view(30.0)
    _tt, flt = v.visible_samples()
    check("display filter suppresses slow sway and mains noise",
          np.abs(flt[:, 0]).max() < 80, f"{np.abs(flt[:, 0]).max():.1f} µV left of ~330")
    check("the recorded data is never modified", np.array_equal(v._y[:, 0], noisy["TP9"]))


def test_scale_is_honest():
    """A ±50 µV sine at scale 100 µV must swing half of a row's half-height;
    at scale 50 µV, all of it."""
    t, chans = _signal(amp=50.0, drift=0.0)
    v = ev.EEGViewer(channels=("TP9",), color="#C2410C")
    v.show_marker_labels = False; v.show_axis = False
    v.resize(900, 220); v.set_filter(False)
    v.set_data(t, chans); v.set_view(30.0)
    plot_h = v._geometry().height()
    heights = {}
    for scale in (100, 50):
        v.set_scale(scale)
        rows = _colour_rows(v.grab().toImage(), "#C2410C")
        heights[scale] = (max(rows) - min(rows)) / v.devicePixelRatioF() if rows else 0
    check("at 100 µV a ±50 µV wave spans half the row", abs(heights[100] - plot_h / 2) < 6,
          f"{heights[100]:.0f}px of {plot_h:.0f}px")
    check("at 50 µV it spans the whole row", abs(heights[50] - plot_h) < 6, f"{heights[50]:.0f}px")
    v.set_scale(None)
    v.grab()
    check("automatic scale picks a standard value that fits the signal",
          v.shown_scale in ev.SCALES and 50 <= v.shown_scale <= 500, str(v.shown_scale))
    check("nice_scale rounds up to a standard value",
          ev.nice_scale(37) == 50 and ev.nice_scale(100) == 100 and ev.nice_scale(9999) == 1000)


def test_markers_and_labels_are_painted():
    t, chans = _signal(drift=0.0)
    v = ev.EEGViewer(); v.resize(900, 260)
    v.set_data(t, chans); v.set_view(30.0)
    before = v.grab().toImage()
    v.set_markers([(28.0, "Round 2", "#B91C1C"), (99.0, "Out of view", "#B91C1C")])
    after = v.grab().toImage()
    # the marker sits 30 % across the plot: the picture must change there, top to bottom
    plot = v._geometry()
    dpr = v.devicePixelRatioF()
    x_mid = int((plot.left() + 0.3 * plot.width()) * dpr)
    changed = {y for y in range(after.height()) for x in range(x_mid - 3, x_mid + 4)
               if after.pixel(x, y) != before.pixel(x, y)}
    check("an event marker inside the window is drawn top to bottom, one out of view is not",
          len(changed) > 0.4 * plot.height() * dpr, f"{len(changed)} rows changed")
    far = {y for y in range(after.height()) for x in range(int(plot.right() * dpr) - 40, int(plot.right() * dpr) - 30)
           if after.pixel(x, y) != before.pixel(x, y)}
    check("…and nothing else in the picture changed", not far)
    check("the tooltip explains every electrode name in plain language",
          all(f"{c} = " in v.toolTip() for c in EEG_CHANNELS) and "left ear" in v.toolTip())


def test_band_power():
    t = np.arange(SR * 4) / SR
    alpha = np.column_stack([30 * np.sin(2 * np.pi * 10 * t)] * 4)
    p = ev.band_powers(t, alpha)
    names = [b[0] for b in ev.BANDS]
    check("band power: a 10 Hz rhythm is alpha-dominant", names[int(np.argmax(p))] == "Alpha", str(p))
    check("band power: too little signal → None", ev.band_powers(t[:10], alpha[:10]) is None)
    bars = ev.BandPowerBars(); bars.resize(400, 120)
    bars.set_window(t, alpha); bars.set_window(None, None)
    check("band power widget paints with and without data", not bars.grab().isNull())


def test_review_card_controls():
    import pandas as pd
    from brain_battle.ui.review_label_screen import BrainActivityCard
    t, chans = _signal(seconds=40, drift=0.0)
    df = pd.DataFrame({**chans, "t_session_seconds": t})
    card = BrainActivityCard(); card.resize(900, card.HEIGHT_NORMAL); card.show()
    card.load([df, None], ["Boxer A · Sam", "Boxer B"])
    card.set_playhead(20.0)
    a, b = card.viewers
    check("card: one viewer per boxer; a boxer without a recording says so",
          a.has_data() and not b.has_data() and "No brain recording" in b.empty_text)
    check("card: event names along the top viewer, one shared time axis under the bottom one",
          a.show_marker_labels and not a.show_axis and b.show_axis and not b.show_marker_labels)
    card._timebase.setCurrentIndex(card._timebase.findData(20))
    card._scale.setCurrentIndex(card._scale.findData(200))
    check("card: timebase + scale controls drive both viewers",
          a.timebase == 20 and b.timebase == 20 and a._scale == 200 and b._scale == 200)
    card._scale.setCurrentIndex(card._scale.findData(None))
    card._filter.click()
    check("card: Auto scale and the filter switch reach the viewers",
          a._scale is None and a._filter is False and b._filter is False)
    card._tall.click()
    check("card: the taller-display button changes the card's height",
          card.height() == card.HEIGHT_TALL)
    card.set_markers([(19.0, "Round 1", "#2B3AA8")])
    card._band.click(); card.set_playhead(21.0)
    check("card: band-power view shows without error",
          card._stacks[0].currentIndex() == 1 and card._bars[0]._values is not None)


if __name__ == "__main__":
    test_windows()
    test_display_processing()
    test_scale_is_honest()
    test_markers_and_labels_are_painted()
    test_band_power()
    test_review_card_controls()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
