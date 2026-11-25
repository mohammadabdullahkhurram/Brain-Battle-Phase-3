"""How late does this computer wake a sleeping thread?

A camera reader (and every simulated device) sleeps until its next frame is
due. If the operating system wakes it more than one frame period late, a frame
is lost — whatever the app does. This measures that, for 20 s at 60 Hz, in
this process and in a freshly spawned child process (how camera processes
start, ADR-0025), and prints how late the wake-ups were. The timing tests
use the same measurement (``stall_probe.wake_lateness``) to decide whether a
computer can judge millisecond precision at all.

Run:  python3 tools/wake_probe.py [seconds]
"""
import multiprocessing as mp
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def describe(seconds: float) -> str:
    from tools.stall_probe import wake_lateness
    w = wake_lateness(seconds)
    return (f"{w['n']} wake-ups: median {w['median']:.2f} ms late, p99 {w['p99']:.2f} ms, "
            f"worst {w['worst']:.1f} ms; over half a frame late {100 * w['over_half']:.1f} %, "
            f"over a whole frame (a lost frame) {w['lost']}")


def _child(seconds, q):
    q.put(describe(seconds))


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    print("this process:   ", describe(seconds), flush=True)
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_child, args=(seconds, q))
    p.start()
    print("spawned process:", q.get(timeout=seconds + 60), flush=True)
    p.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
