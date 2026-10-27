"""
Subprocess runner for tests/test_history_log.py — NOT a test itself.

Appends history events to a session's history.jsonl as fast as it can
(each with its own append+flush+fsync). The parent SIGKILLs it mid-stream to
check the append-only log stays readable after a hard crash.

Usage (parent only):  python3 tests/_history_kill_runner.py <session_root>
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import session_history


def main():
    session_root = sys.argv[1]
    i = 0
    while True:
        session_history.log_event(session_root, "tick", {"i": i})
        i += 1
        time.sleep(0.001)


if __name__ == "__main__":
    main()
