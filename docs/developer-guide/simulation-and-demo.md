# Simulation mode & the demo generator

**What this page covers:** the two ways to develop and test Brain Battle without
hardware — a live **simulation mode** that fakes the devices through the real
capture code, and a **demo-session generator** that writes a real session folder
to disk. What each one fakes, what stays real, how to use them, and the fact that
neither ships in the packaged app.

Both exist because the hardware is currently broken and nobody has inspected real
Muse-S EEG from a sparring round (see [CLAUDE.md's Project situation](../project/status.md)).
They are stand-ins, not substitutes for the real-hardware checks that remain
open — see [known limitations](../research-and-data/limitations.md).

## Simulation mode (`simulation.py`, `BRAIN_BATTLE_SIMULATE`)

Setting the environment variable `BRAIN_BATTLE_SIMULATE=1` makes the capture
layer use **simulated cameras and headbands that run through the real capture
paths** — the same `USBCameraThread`, `FrameEncoder`, `_MuseDirectReader`, record
flow, storage writers, and history. Only the device at the very bottom is faked.

```bash
BRAIN_BATTLE_SIMULATE=1 python3 -m brain_battle
```

| Faked | Real (unchanged) |
|---|---|
| `SimCapture` — a cv2-shaped synthetic camera yielding generated frames | `USBCameraThread`, the ffmpeg encoder, the sidecar writer |
| `SimMuseReader` — a simulated Muse band yielding synthetic EEG | `_MuseDirectReader`'s streaming/flush path, the CSV writer |
| Device discovery (`camera_devices`, `muse_devices`) | the record flow, markers, manifest, history, contact log |

Because the real paths run, a simulated recording produces a **real** v3 session
folder you can open in Review & Label. This is how `test_record_flow.py` drives
the whole flow end to end.

### Failure injection

Simulation can inject the failures the UI must handle, via environment
variables read in `simulation.py`:

- **A missing camera** (`missing_camera()`) — a tile shows the not-found message
  and a red border; the summary says it won't be recorded.
- **A headband that drops mid-session** (`drop_headband_after()`) — exercises the
  reconnect/append path (`HeadbandSlot.reconnect`).

`test_record_flow.py` uses both to assert the Connect step, the recording
health line, and reconnect all behave.

## The demo generator (`tools/generate_demo_session.py`)

Where simulation fakes **live** devices, the generator writes a **finished**
session folder straight to disk — for working on Sessions, Review & Label,
export, and the docs without recording anything.

```bash
python3 tools/generate_demo_session.py --out /tmp/demo
python3 tools/generate_demo_session.py --out /tmp/demo --rounds 3 --seed 7
python3 tools/generate_demo_session.py --out /tmp/demo --interrupted
python3 tools/generate_demo_session.py --out /tmp/library --library
```

The default makes one session (2 rounds, seed 0); `--interrupted` makes a
crash-looking session; `--library` fills the folder with ~5 sessions covering
every Sessions status. `--out` is **required** so a demo can never land in your
real recordings folder
by accident, and the generator **refuses** an `--out` equal to the configured
recordings folder unless you pass `--force` (the `_refuse_real_folder` guard).

### It reuses the real writers

Each run writes a real v3 session — 4 camera MP4s + sidecars, 2 EEG CSVs,
`phase_markers.csv`, and a `session_manifest.json` with `demo:true` — by calling
the **same** writers the app uses (`session_storage.SessionLayout`,
`session_controller._build_timeline`, `USBCameraThread`'s sidecar methods,
`_MuseDirectReader`'s chunk/flush path) wherever they're headlessly callable. So
the fixture **can't silently drift** from the format the app actually reads and
writes. Output is deterministic for a given `--seed`.

### What makes it a useful fixture

- **One camera deliberately drifts** — 30 fps declared container vs. ~29.5 fps
  true capture — so the drift-handling of the playback clock can be exercised and
  the Phase 5 full-sidecar work has a reproduction.
- **Eyes-on-glass flash/spike** — every camera shows a full-frame white flash and
  every EEG channel gets a sharp spike at each phase marker, so a human can open
  the session and confirm all four cameras + both traces land on the same instant
  (`test_flash_sync.py` automates the arithmetic side).
- **`--library`** fills a folder with ~5 short sessions covering every Sessions
  status (interrupted, needs labelling, labelled, exported, no-name) — handy for
  the Sessions screen and the doc screenshots.

## Neither ships

`tools/`, `tests/`, and `simulation.py` are **developer-only** and are excluded
from the packaged app (Phase 6). With `BRAIN_BATTLE_SIMULATE` unset there is no
trace of simulation anywhere in the UI, so dropping the module from the bundle is
safe. The doc screenshots are generated under simulation but with the `SIM CAM`
overlay and `Demo` badge stripped (see
[docs guide](../contributing/docs-guide.md#screenshots)) so a committed image
never shows a dev artefact.
