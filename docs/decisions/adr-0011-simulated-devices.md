# ADR-0011 — Simulated devices for development and testing

**What this page covers:** why the app can run with fake cameras and headbands
through the real capture paths, and why that code never ships.

- **Status:** Accepted
- **Date:** 2026-11-03

## Context

The hardware is broken and nobody has a camera rig or Muse-S bands to develop
against (see [AGENTS.md's Project situation](../project/status.md)). The entire
recording flow — Connect, Set up, Record, Saved, plus crash and reconnect
handling — still needs to be built and tested. Mocking out the capture code would
test the mocks, not the real paths that actually run during a recording.

## Options considered

1. **Mock the capture layer in tests only** — tests pass, but the real record
   flow, storage, markers, and history never execute together.
2. **Simulate at the device boundary**, through the **real** capture paths — fake
   only the camera frames and EEG samples; everything above runs for real.

## Decision

**Simulate at the device boundary** (`simulation.py`), gated entirely by
`BRAIN_BATTLE_SIMULATE`. `SimCapture` is a cv2-shaped synthetic camera and
`make_muse_reader` returns a simulated reader; both feed the **real**
`USBCameraThread`/`FrameEncoder` and `_MuseDirectReader` streaming paths, the real
record flow, storage, markers, contact log, and history. Failure injection covers
a missing camera and a headband drop. With the flag unset there is no trace of it
in the UI, and the module is excluded from the packaged app (Phase 6).

## Consequences

- **The whole record flow is exercised end to end** against the real code paths
  (`tests/test_record_flow.py`), producing a real v3 session folder that opens in
  Review & Label.
- **Failure handling is testable** without unplugging anything — missing camera,
  headband drop/reconnect, disk-blocked start.
- **Nothing ships** — `simulation.py`, `tools/`, and `tests/` are developer-only
  and excluded from the bundle; the unset flag guarantees no UI leakage.
- It is a stand-in for **live** devices, not a substitute for the real-signal and
  eyes-on-glass checks that remain open
  ([known limitations](../research-and-data/limitations.md)). The complementary
  on-disk fixture is the [demo generator](../developer-guide/simulation-and-demo.md).

## Links

- Commit `49753c6` (simulated cameras + headbands through the real paths).
- [Simulation & demo](../developer-guide/simulation-and-demo.md),
  [ROADMAP Phase 6](../project/roadmap.md).
