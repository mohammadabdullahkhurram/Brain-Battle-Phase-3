"""
Discrimination test for the xcorr sync-confidence metric.

A confidence metric is only useful if it says "I'm not sure" on bad input.
This test therefore checks three regimes, printing the actual numbers:

  (a) two signals sharing content at a known offset  -> HIGH  (> 0.6)
  (b) two UNRELATED signals (independent noise)      -> LOW   (< 0.3)
  (c) shared content buried under rising added noise -> degrades sensibly
      (non-increasing across the noise ladder; heavy noise < clean)

It also checks the threading: MainEditorSyncWorker carries per-source
confidence (None for audio-less sources), _on_sync_finished feeds the
SourceRow badge and SyncResult, and the sync_offsets.csv rows record an
honest "n/a (<method>)" sentinel instead of a fake number when confidence
was never measured.

Thresholds deliberately mirror the SourceRow badge bands (>=0.6 ✓ / >=0.3 ~).

Run:  python3 tests/test_xcorr_confidence.py
"""
import os
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication  # noqa: E402

# Full QApplication must exist BEFORE importing test_seek_alignment, which
# otherwise creates a widgets-incapable QCoreApplication at import time.
_app = QApplication.instance() or QApplication(sys.argv)

# Reuses the fixture builders AND the conditional vlc stub from the seek test.
from test_seek_alignment import _make_clip, _make_silent_clip, _write_master_wav  # noqa: E402

import brain_battle_main as bbm  # noqa: E402
from sync_utils.audio_analysis import XCORR_SR, _xcorr_delay  # noqa: E402

SR = XCORR_SR
DUR = 30.0
OFFSET = 1.5          # seconds — "other" starts this much later
BURST_T = 12.0

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _synth(seed, burst=True):
    """Quiet noise at XCORR_SR with an optional loud 50ms burst at BURST_T."""
    rng = np.random.default_rng(seed)
    n = int(SR * DUR)
    x = rng.standard_normal(n).astype(np.float64) * 0.02
    if burst:
        b0 = int(BURST_T * SR)
        x[b0:b0 + int(0.05 * SR)] += rng.standard_normal(int(0.05 * SR)) * 0.9
    return x


def test_metric_discrimination():
    master = _synth(seed=1)
    shared = master[int(OFFSET * SR):]

    # (a) shared content at known offset
    d_good, c_good = _xcorr_delay(master, shared, SR)
    check("(a) shared: delay recovered", abs(d_good - OFFSET) < 0.01, f"delay={d_good:.4f}")
    check("(a) shared: HIGH confidence (> 0.6)", c_good > 0.6, f"confidence={c_good:.3f}")

    # (b) unrelated signals — same length/level, independent content, and
    # per the spec NO shared transient (an independent burst at the same
    # wall-clock position would legitimately correlate — any envelope
    # method aligns it — so that is not an "unrelated" case).
    unrelated = _synth(seed=2, burst=False)
    d_bad, c_bad = _xcorr_delay(master, unrelated, SR)
    check("(b) unrelated: LOW confidence (< 0.3)", c_bad < 0.3,
          f"confidence={c_bad:.3f} (delay={d_bad:.4f}, meaningless)")

    # (c) shared content + rising independent noise
    rng = np.random.default_rng(3)
    ladder = []
    for added_std in (0.0, 0.1, 0.5, 2.0):
        noisy = shared + rng.standard_normal(len(shared)) * added_std
        d_n, c_n = _xcorr_delay(master, noisy, SR)
        ladder.append((added_std, d_n, c_n))
    print("  (c) noise ladder (added_std, delay, confidence):")
    for std, d, c in ladder:
        print(f"      std={std:<4}  delay={d:+.4f}  confidence={c:.3f}")
    confs = [c for _, _, c in ladder]
    check("(c) confidence non-increasing as noise rises",
          all(confs[i + 1] <= confs[i] + 0.05 for i in range(len(confs) - 1)),
          f"{[round(c, 3) for c in confs]}")
    check("(c) heavy noise scores clearly below clean",
          confs[-1] < confs[0] - 0.15, f"clean={confs[0]:.3f} heavy={confs[-1]:.3f}")
    # Confidence must not perturb the delay itself while signal survives
    check("(c) delay stable under moderate noise",
          all(abs(d - OFFSET) < 0.01 for _, d, _ in ladder[:3]),
          f"{[round(d, 4) for _, d, _ in ladder[:3]]}")


def _pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        _app.processEvents(); time.sleep(0.02)


def _run_tab_sync(tab, timeout=120):
    done = {}
    tab._run_sync()
    w = tab._worker
    w.finished.connect(lambda *a: done.update(s="finished"))
    w.error.connect(lambda m: done.update(s=f"error: {m}"))
    end = time.time() + timeout
    while "s" not in done and time.time() < end:
        _app.processEvents(); time.sleep(0.05)
    _pump(1.0)
    return done.get("s")


def test_threading(tmp):
    master = os.path.join(tmp, "master.wav")
    _write_master_wav(master)
    clip_a = os.path.join(tmp, "conf_A.mp4"); _make_clip(master, 0.0, clip_a)
    clip_b = os.path.join(tmp, "conf_B.mp4"); _make_clip(master, 1.5, clip_b)
    silent = []
    for i in range(2):
        p = os.path.join(tmp, f"conf_silent_{i}.mp4")
        _make_silent_clip(p); silent.append(p)

    # External path: real confidences on the tab + badge + CSV rows
    tab = bbm.SyncReviewTab()
    tab.sources[0].set_path(clip_a); tab.sources[1].set_path(clip_b)
    _pump(1.0)
    status = _run_tab_sync(tab)
    check("external sync finished", status == "finished", str(status))
    confs = list(getattr(tab._worker, "confidences", []))
    print(f"  worker confidences (external) = "
          f"{[round(c, 3) if c is not None else None for c in confs]}")
    check("worker carries 2 measured confidences",
          len(confs) == 2 and all(isinstance(c, float) for c in confs))
    check("both real-pair confidences are high (> 0.6)",
          all(c > 0.6 for c in confs), str([round(c, 3) for c in confs]))
    so = tab.sync_result.video_offsets
    check("SyncResult carries the measured values",
          [o.confidence for o in so] == confs,
          str([(o.source_id, o.confidence) for o in so]))
    badges = [s.type_lbl.text() for s in tab.sources[:2]]
    check("set_confidence badge wired (✓ on both rows)",
          all(t.endswith("✓") for t in badges), str(badges))
    rows = tab._sync_offsets_rows()
    check("CSV rows carry real rounded confidence",
          all(isinstance(r["confidence"], float) for r in rows if r["method"] == "xcorr"),
          str([(r["source"], r["confidence"]) for r in rows]))
    tab.shutdown()

    # In-app path: confidence never measured -> None + honest CSV sentinel
    tab2 = bbm.SyncReviewTab()
    tab2.sources[2].set_path(silent[0]); tab2.sources[3].set_path(silent[1])
    _pump(1.0)
    status2 = _run_tab_sync(tab2)
    check("in-app sync finished", status2 == "finished", str(status2))
    confs2 = list(getattr(tab2._worker, "confidences", []))
    check("audio-less sources get confidence None", confs2 == [None, None], str(confs2))
    check("SyncResult records None, not a fake number",
          all(o.confidence is None for o in tab2.sync_result.video_offsets))
    rows2 = tab2._sync_offsets_rows()
    print(f"  CSV rows (in-app) = {[(r['source'], r['confidence']) for r in rows2]}")
    check("CSV sentinel is honest",
          all(r["confidence"] == "n/a (simultaneous_start)" for r in rows2),
          str([r["confidence"] for r in rows2]))
    badges2 = [s.type_lbl.text() for s in tab2.sources[2:4]]
    check("no badge on unmeasured rows",
          not any(t.rstrip().endswith(("✓", "~", "?")) for t in badges2), str(badges2))
    tab2.shutdown()


if __name__ == "__main__":
    print("=== metric discrimination (pure signals) ===")
    test_metric_discrimination()
    print("\n=== threading (worker → tab → CSV rows) ===")
    with tempfile.TemporaryDirectory() as tmp:
        test_threading(tmp)
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print(f"  FAIL: {f}")
    sys.exit(1 if _failures else 0)
