"""
eeg_viewer.py — the EEG display
───────────────────────────────
A conventional EEG viewer, drawn the way lab software (for example the
BrainVision LSL Viewer) draws it, so anyone who has looked at EEG before can
read it at once:

  • one horizontal trace per channel, stacked, each labelled with its standard
    electrode name — TP9, AF7, AF8, TP10 and AUX — plus, in small print, where
    that electrode sits ("left ear" …) for people new to EEG;
  • amplitude in microvolts (µV), with a scale bar showing the current scale;
  • one shared time axis along the bottom;
  • a chosen *timebase* (how many seconds fit across) and *scale* (how many µV
    a row's half-height stands for);
  • event markers — round starts, breaks, marked moments, labels — as vertical
    lines with their names written along the top.

``EEGViewer`` is used in two ways:

  review  the whole recording is loaded; the window is centred on the playhead
          (``set_view(t)``), which is drawn as a fixed line in the middle.
  live    a rolling buffer is pushed in many times a second; the window ends at
          the newest sample, so the trace scrolls right-to-left.

Display only: nothing here changes recorded data. Two things are done to the
*picture* so the traces are readable — each channel's slow drift is removed
(its median over the visible window is subtracted), and, when the display
filter is on, a 1–45 Hz band-pass is applied.

``BandPowerBars`` is the alternative "band power" picture of the same window.
"""
from __future__ import annotations

import math

import numpy as np
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QSizePolicy, QWidget

import brain_battle.ui.design_tokens as dt
from brain_battle.data.signals import CHANNEL_PLACES, EEG_CHANNELS, SAMPLE_RATE

TIMEBASES = (2, 5, 10, 20, 30)                 # seconds across the window
SCALES = (10, 20, 50, 100, 200, 500, 1000)     # µV per half row
DEFAULT_TIMEBASE = 10
DEFAULT_SCALE = 100
FILTER_BAND = (1.0, 45.0)                      # display filter, Hz

BANDS = (("Delta", 1, 4), ("Theta", 4, 8), ("Alpha", 8, 13),
         ("Beta", 13, 30), ("Gamma", 30, 45))

_GAP = 4.0 / SAMPLE_RATE      # a pause longer than this breaks the trace
_sos_cache = None


def _display_filter(x: np.ndarray) -> np.ndarray:
    """Zero-phase 1–45 Hz band-pass along axis 0 (too-short input is returned
    unchanged)."""
    global _sos_cache
    if x.shape[0] < 64:
        return x
    from scipy.signal import butter, sosfiltfilt
    if _sos_cache is None:
        _sos_cache = butter(2, FILTER_BAND, btype="bandpass", fs=SAMPLE_RATE, output="sos")
    return sosfiltfilt(_sos_cache, x, axis=0)


def nice_scale(uv: float) -> int:
    """The smallest standard scale that is at least ``uv``."""
    for s in SCALES:
        if s >= uv:
            return s
    return SCALES[-1]


def _polyline(xs: np.ndarray, ys: np.ndarray) -> QPolygonF:
    """A QPolygonF from two arrays without a Python loop per point."""
    n = len(xs)
    poly = QPolygonF()
    if n == 0:
        return poly
    try:
        poly.fill(QPointF(0.0, 0.0), n)
        buf = poly.data()
        buf.setsize(16 * n)
        mem = np.frombuffer(buf, dtype=np.float64).reshape(n, 2)
        mem[:, 0] = xs
        mem[:, 1] = ys
    except Exception:                       # very old PyQt: fall back to the loop
        poly = QPolygonF([QPointF(float(x), float(y)) for x, y in zip(xs, ys)])
    return poly


def _fmt_clock(t: float, decimals: bool) -> str:
    sign = "-" if t < 0 else ""
    t = abs(t)
    m, s = int(t // 60), t - 60 * int(t // 60)
    return f"{sign}{m}:{s:04.1f}" if decimals else f"{sign}{m}:{int(round(s)) % 60:02d}"


class EEGViewer(QWidget):
    """Stacked, labelled EEG traces with a time axis, a µV scale bar and event
    markers. See the module docstring."""

    def __init__(self, channels=EEG_CHANNELS, live: bool = False,
                 color: str = "#15171C", min_row_px: int = 13, parent=None):
        super().__init__(parent)
        self.channels = tuple(channels)
        self.live = live
        self._color = color
        self._t = None                  # (N,) seconds, ascending
        self._y = None                  # (N, C)
        self._view = 0.0                # review: centre · live: right edge
        self._timebase = float(DEFAULT_TIMEBASE)
        self._scale = DEFAULT_SCALE     # µV per half row; None = automatic
        self._shown_scale = DEFAULT_SCALE
        self._filter = True
        self._markers = []              # (t, text, colour)
        self._origin = None             # live: time shown as "session clock" from here
        self.show_axis = True
        self.show_marker_labels = True
        self.empty_text = "Waiting for signal…" if live else "No brain recording for this boxer"
        self.setMinimumHeight(min_row_px * len(self.channels) + 32)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setToolTip(" · ".join(f"{c} = {CHANNEL_PLACES.get(c, c)}" for c in self.channels))

    # ── data ──
    def set_data(self, t, chans: dict) -> None:
        """``t``: sample times in seconds (ascending). ``chans``: channel name →
        samples in µV. Channels that are missing are drawn flat."""
        if t is None or len(t) == 0:
            self._t = self._y = None
        else:
            self._t = np.asarray(t, dtype=float)
            cols = [np.asarray(chans[c], dtype=float) if c in chans and len(chans[c]) == len(self._t)
                    else np.zeros(len(self._t)) for c in self.channels]
            self._y = np.column_stack(cols)
            if self.live:
                self._view = float(self._t[-1])
        self.update()

    def has_data(self) -> bool:
        return self._t is not None

    # ── view ──
    def set_view(self, t: float) -> None:
        """Review: put time ``t`` (the playhead) at the centre."""
        self._view = float(t)
        self.update()

    def set_timebase(self, seconds: float) -> None:
        self._timebase = float(seconds)
        self.update()

    def set_scale(self, uv) -> None:
        """µV per half row, or ``None`` to fit the scale to the signal."""
        self._scale = None if uv is None else float(uv)
        self.update()

    def set_filter(self, on: bool) -> None:
        self._filter = bool(on)
        self.update()

    def set_markers(self, markers) -> None:
        """``[(time, text, colour), …]`` in the same time units as the data."""
        self._markers = sorted(markers or [], key=lambda m: m[0])
        self.update()

    def set_time_origin(self, origin) -> None:
        """Live only: label the axis as time since ``origin`` (the recording's
        start) instead of "seconds ago"."""
        self._origin = origin
        self.update()

    @property
    def timebase(self) -> float:
        return self._timebase

    @property
    def shown_scale(self) -> float:
        """The scale actually drawn last (differs from the setting when automatic)."""
        return self._shown_scale

    def window(self) -> tuple[float, float]:
        if self.live:
            return self._view - self._timebase, self._view
        return self._view - self._timebase / 2, self._view + self._timebase / 2

    def visible_samples(self):
        """(t, y[N, C]) inside the window, drift removed and (if on) filtered —
        exactly what is drawn. (None, None) when there is nothing to show."""
        if self._t is None:
            return None, None
        t0, t1 = self.window()
        pad = 1.0 if self._filter else 0.0          # filter warm-up either side
        i0 = int(np.searchsorted(self._t, t0 - pad))
        i1 = int(np.searchsorted(self._t, t1 + pad))
        if i1 - i0 < 2:
            return None, None
        t, y = self._t[i0:i1], self._y[i0:i1]
        if self._filter:
            y = _display_filter(y)
        keep = (t >= t0) & (t <= t1)
        t, y = t[keep], y[keep]
        if len(t) < 2:
            return None, None
        return t, y - np.median(y, axis=0)

    # ── painting ──
    def _geometry(self):
        W, H = self.width(), self.height()
        gutter = 58 if W >= 420 else 42
        top = 15 if self.show_marker_labels else 3
        bottom = 17 if self.show_axis else 3
        return QRectF(gutter, top, max(10, W - gutter - 6), max(10, H - top - bottom))

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p: QPainter):
        plot = self._geometry()
        n = len(self.channels)
        row_h = plot.height() / n
        t0, t1 = self.window()
        span = max(1e-6, t1 - t0)
        ink, faint = QColor(dt.text_tertiary()), QColor(dt.border())

        def x_of(t):
            return plot.left() + (t - t0) / span * plot.width()

        p.fillRect(self.rect(), QColor(dt.card_bg()))
        p.fillRect(plot, QColor("#FBFBFD"))

        # time grid + axis labels
        # Ticks every `step` seconds — the smallest step that leaves room for
        # the labels — counted from the session start (or from "now" while
        # live and not yet recording).
        max_ticks = max(2, int(plot.width() // 56))
        step = next((s for s in (0.2, 0.5, 1, 2, 5, 10, 30, 60) if span / s <= max_ticks), 60)
        relative = self.live and self._origin is None
        base = t1 if relative else (self._origin or 0.0)
        p.setFont(QFont("Inter", 8))
        k = math.ceil((t0 - base) / step - 1e-9)
        while base + k * step <= t1 + 1e-9:
            tt = base + k * step
            x = x_of(tt)
            p.setPen(QPen(faint, 1))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            if self.show_axis:
                if relative:
                    text = "now" if k == 0 else f"{k * step:g} s"
                else:
                    text = _fmt_clock(k * step, decimals=step < 1)
                p.setPen(ink)
                lx = min(max(x - 30, 0.0), self.width() - 60.0)    # keep the end labels whole
                p.drawText(QRectF(lx, plot.bottom() + 2, 60, 14),
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)
            k += 1

        # row separators + channel labels
        small_rows = row_h < 26
        for ci, ch in enumerate(self.channels):
            y_top = plot.top() + ci * row_h
            if ci:
                p.setPen(QPen(faint, 1))
                p.drawLine(QPointF(plot.left(), y_top), QPointF(plot.right(), y_top))
            p.setPen(QColor(dt.text_primary() if ch != "AUX" else dt.text_tertiary()))
            p.setFont(QFont("Inter", 9, QFont.Weight.DemiBold))
            label_box = QRectF(2, y_top, plot.left() - 8, row_h)
            if small_rows or plot.left() < 50:
                p.drawText(label_box, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, ch)
            else:
                p.drawText(QRectF(2, y_top + row_h / 2 - 13, plot.left() - 8, 13),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, ch)
                p.setPen(ink)
                p.setFont(QFont("Inter", 7))
                p.drawText(QRectF(0, y_top + row_h / 2, plot.left() - 8, 11),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                           CHANNEL_PLACES.get(ch, ""))
        p.setPen(QPen(QColor(dt.border_strong()), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(plot)

        # traces
        t, y = self.visible_samples()
        if t is None:
            p.setPen(ink)
            p.setFont(QFont("Inter", 10))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, self.empty_text)
        else:
            scale = self._scale
            if scale is None:               # automatic: fit the EEG channels
                eeg = [i for i, c in enumerate(self.channels) if c != "AUX"] or [0]
                spread = float(np.median(np.abs(y[:, eeg]))) * 1.4826
                scale = nice_scale(max(10.0, 4.0 * spread))
            self._shown_scale = scale
            px_per_uv = (row_h / 2.0) / scale
            xs = plot.left() + (t - t0) / span * plot.width()
            breaks = np.flatnonzero(np.diff(t) > _GAP) + 1
            bounds = [0, *breaks.tolist(), len(t)]
            p.save()
            p.setClipRect(plot)
            for ci, ch in enumerate(self.channels):
                mid = plot.top() + (ci + 0.5) * row_h
                ys = mid - y[:, ci] * px_per_uv
                p.setPen(QPen(QColor(self._color if ch != "AUX" else dt.text_tertiary()), 1.0))
                for a, b in zip(bounds[:-1], bounds[1:]):
                    if b - a >= 2:
                        p.drawPolyline(_polyline(*self._thin(xs[a:b], ys[a:b], plot.width())))
            p.restore()
            self._paint_scale_bar(p, plot, row_h, scale)

        # event markers
        last_label_right = -1e9
        for mt, text, colour in self._markers:
            if not (t0 <= mt <= t1):
                continue
            x = x_of(mt)
            col = QColor(colour)
            p.setPen(QPen(col, 1.2, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            if self.show_marker_labels:
                p.setFont(QFont("Inter", 8, QFont.Weight.DemiBold))
                w = p.fontMetrics().horizontalAdvance(text)
                lx = min(max(x + 3, plot.left()), plot.right() - w)
                if lx > last_label_right + 6:
                    p.setPen(col)
                    p.drawText(QPointF(lx, plot.top() - 4), text)
                    last_label_right = lx + w

        # playhead (review): fixed in the centre
        if not self.live:
            x = plot.center().x()
            p.setPen(QPen(QColor(dt.accent()), 1.4))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

    @staticmethod
    def _thin(xs, ys, width_px):
        """Reduce a long run of samples to at most ~2 points per pixel column,
        keeping each column's minimum and maximum so spikes survive."""
        n = len(xs)
        cols = max(1, int(width_px))
        if n <= 3 * cols:
            return xs, ys
        per = n // cols
        m = (n // per) * per
        yb = ys[:m].reshape(-1, per)
        xb = xs[:m].reshape(-1, per)
        out_x = np.repeat(xb[:, 0], 2)
        out_y = np.empty(2 * len(yb))
        out_y[0::2], out_y[1::2] = yb.min(axis=1), yb.max(axis=1)
        return out_x, out_y

    def _paint_scale_bar(self, p, plot, row_h, scale):
        """An I-shaped bar, half a row tall, labelled with what it stands for."""
        x = plot.right() - 8
        y0, y1 = plot.top() + 3, plot.top() + 3 + row_h / 2
        text = f"{scale:g} µV"
        p.setFont(QFont("Inter", 8, QFont.Weight.DemiBold))
        w = p.fontMetrics().horizontalAdvance(text)
        p.fillRect(QRectF(x - w - 12, y0 - 2, w + 18, max(row_h / 2, 12) + 4),
                   QColor(255, 255, 255, 215))
        p.setPen(QPen(QColor(dt.text_secondary()), 1.4))
        p.drawLine(QPointF(x, y0), QPointF(x, y1))
        p.drawLine(QPointF(x - 3, y0), QPointF(x + 3, y0))
        p.drawLine(QPointF(x - 3, y1), QPointF(x + 3, y1))
        p.drawText(QRectF(x - w - 8, y0, w, max(row_h / 2, 12)),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)


def band_powers(t, y) -> np.ndarray | None:
    """Relative power in each of ``BANDS`` (largest = 1), averaged over the
    columns of ``y``. None when the window is too short."""
    if t is None or len(t) < SAMPLE_RATE // 2:
        return None
    from scipy.signal import welch
    f, pxx = welch(y, fs=SAMPLE_RATE, nperseg=min(SAMPLE_RATE, len(t)), axis=0)
    total = pxx.mean(axis=1)
    powers = np.array([np.trapezoid(total[(f >= lo) & (f < hi)], f[(f >= lo) & (f < hi)])
                       for _name, lo, hi in BANDS])
    return powers / max(1e-12, float(powers.max()))


class BandPowerBars(QWidget):
    """Five bars — how the signal's power in the current window is spread over
    the classic EEG frequency bands (the four skin electrodes averaged)."""

    def __init__(self, color: str = "#3D4FD6", parent=None):
        super().__init__(parent)
        self._color = color
        self._values = None
        self.setMinimumHeight(70)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_window(self, t, y) -> None:
        """``(t, y)`` as returned by ``EEGViewer.visible_samples()``."""
        self._values = None if y is None else band_powers(t, y[:, :4])
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            W, H = self.width(), self.height()
            p.fillRect(self.rect(), QColor(dt.card_bg()))
            if self._values is None:
                p.setPen(QColor(dt.text_tertiary()))
                p.setFont(QFont("Inter", 10))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                           "Not enough signal here to show band power")
                return
            gap = 14
            bw = (W - (len(BANDS) + 1) * gap) / len(BANDS)
            for i, (name, lo, hi) in enumerate(BANDS):
                x = gap + i * (bw + gap)
                bh = max(2.0, float(self._values[i]) * (H - 26))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(self._color))
                p.drawRoundedRect(QRectF(x, H - 20 - bh, bw, bh), 3, 3)
                p.setPen(QColor(dt.text_tertiary()))
                p.setFont(QFont("Inter", 8))
                p.drawText(QRectF(x - 6, H - 17, bw + 12, 14),
                           Qt.AlignmentFlag.AlignHCenter, f"{name} {lo}–{hi} Hz")
        finally:
            p.end()


__all__ = ["EEGViewer", "BandPowerBars", "band_powers", "nice_scale",
           "TIMEBASES", "SCALES", "DEFAULT_TIMEBASE", "DEFAULT_SCALE", "BANDS"]
