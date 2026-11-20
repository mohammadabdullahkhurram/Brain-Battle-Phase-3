"""How late does this computer wake a sleeping thread?

A camera reader (and every simulated device) sleeps until its next frame is
due. If the operating system wakes it more than one frame period late, a frame
is lost — whatever the app does. This measures that, for 20 s at 60 Hz, in
this process and in a freshly spawned child process (how camera processes
start, ADR-0025), and prints how late the wake-ups were.

Run:  python3 tools/wake_probe.py [seconds]
"""
import multiprocessing as mp
import sys
import time

PERIOD = 1 / 60


def probe(seconds: float) -> str:
    late = []
    t0 = time.perf_counter()
    n = 1
    while time.perf_counter() - t0 < seconds:
        due = t0 + n * PERIOD
        wait = due - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        late.append((time.perf_counter() - due) * 1000)
        n += 1
    late.sort()
    k = len(late)
    over = sum(1 for x in late if x > PERIOD * 1000 / 2)
    lost = sum(1 for x in late if x > PERIOD * 1000)
    return (f"{k} wake-ups: median {late[k // 2]:.2f} ms late, p99 {late[int(k * 0.99)]:.2f} ms, "
            f"worst {late[-1]:.1f} ms; over half a frame late {over} "
            f"({100 * over / k:.1f} %), over a whole frame (a lost frame) {lost}")


def _child(seconds, q):
    q.put(probe(seconds))


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    print("this process:   ", probe(seconds), flush=True)
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_child, args=(seconds, q))
    p.start()
    print("spawned process:", q.get(timeout=seconds + 60), flush=True)
    p.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
