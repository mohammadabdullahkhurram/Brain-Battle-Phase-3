"""
Unit test for eeg_module._RingBuffer, focused on the over-capacity write
branch: when a single extend() is larger than the whole buffer, extend()
keeps the newest `capacity` samples but must leave `_n_written` aligned so
to_array() still returns them in chronological order — and so that
SUBSEQUENT small writes land after the newest sample, not mid-buffer.

Never triggered in production today (Muse chunks are <=12 samples into a
7680-capacity buffer) — this pins the latent bug found during grounding.

Run:  python3 tests/test_ring_buffer.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eeg_module import _RingBuffer  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def test_over_capacity_write():
    rb = _RingBuffer(8)
    rb.extend(np.arange(20, dtype=np.float64))       # 20 samples into cap 8
    got = rb.to_array()
    want = np.arange(12, 20, dtype=np.float64)       # newest 8, oldest->newest
    check("over-capacity write: newest `capacity` samples in order",
          np.array_equal(got, want), f"got={got.tolist()} want={want.tolist()}")
    check("length reports full", len(rb) == 8)

    # Subsequent small writes must continue chronologically after sample 19.
    rb.extend(np.array([100.0, 101.0]))
    got2 = rb.to_array()
    want2 = np.array([14, 15, 16, 17, 18, 19, 100, 101], dtype=np.float64)
    check("small write after over-capacity stays chronological",
          np.array_equal(got2, want2), f"got={got2.tolist()}")


def test_normal_small_chunk_path():
    rb = _RingBuffer(8)
    fed = []
    for start in range(0, 30, 3):                    # chunks of 3, wraps often
        chunk = np.arange(start, start + 3, dtype=np.float64)
        rb.extend(chunk)
        fed.extend(chunk.tolist())
        want = np.array(fed[-8:], dtype=np.float64)
        if not np.array_equal(rb.to_array(), want):
            check("small-chunk path chronological at every step", False,
                  f"after {len(fed)} samples: got={rb.to_array().tolist()} want={want.tolist()}")
            return
    check("small-chunk path chronological at every step", True, "10 chunks of 3, cap 8")

    rb2 = _RingBuffer(8)
    rb2.extend(np.arange(5, dtype=np.float64))       # not yet full
    check("pre-fill read returns exactly what was written",
          np.array_equal(rb2.to_array(), np.arange(5, dtype=np.float64)))
    rb2.clear()
    check("clear() empties the buffer", len(rb2) == 0 and rb2.to_array().size == 0)


def test_exact_capacity_write():
    rb = _RingBuffer(8)
    rb.extend(np.arange(8, dtype=np.float64))        # n == capacity boundary
    check("exact-capacity write in order",
          np.array_equal(rb.to_array(), np.arange(8, dtype=np.float64)))
    rb.extend(np.array([50.0]))
    check("write after exact-capacity stays chronological",
          np.array_equal(rb.to_array(),
                         np.array([1, 2, 3, 4, 5, 6, 7, 50], dtype=np.float64)),
          f"got={rb.to_array().tolist()}")


if __name__ == "__main__":
    test_over_capacity_write()
    test_normal_small_chunk_path()
    test_exact_capacity_write()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print(f"  FAIL: {f}")
    sys.exit(1 if _failures else 0)
