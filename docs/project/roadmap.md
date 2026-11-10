# Roadmap

**What this page covers:** a short summary of the build phases — done, current,
and future — with a link to the full plan. The authoritative, detailed roadmap
(with per-item rationale and the files each will touch) is
[`docs/ROADMAP.md`](planning-documents.md); this page is the orientation, not a
duplicate of it.

Per the [working agreement](status.md#the-working-agreement), **the owner
approves each phase before it starts** — nothing below begins on its own
initiative.

## The phases at a glance

| Phase | Theme | Status |
|---|---|---|
| **1** | Foundation (requirements, demo generator) | Done |
| **2** | Data safety (streamed EEG, crash-safe video, crash recovery, disk check) | Done |
| **3** | Structure & design (the four DESIGN screens, light-only, capture clock) | Done (2026-11-06) |
| **4** | Labelling | Delivered inside Phase 3's Review & Label build |
| **D1–D3** | Documentation (the six doc sections + the honesty tests) | Done |
| **5** | Remaining report & research-value items | **Next (not started)** |
| **6** | Shipping (double-click app, bundled ffmpeg + docs, README rewrite) | Future |
| **7** | Team & cloud (shared storage, accounts, questionnaires, analysis mode) | Future, not scheduled |

## Phase 5 — remaining report items (next)

The research-value and report items still open, built against the demo session:

- Use the **full per-frame sidecar** for playback timing (not just the first row)
  and detect/flag capture-rate drift.
- **Configurable record resolution/fps** backed by Settings.
- A single **Export for analysis** button; the merged 2×2 video optional and off
  by default; one master events file.
- Optional **per-round clip export** using the markers.
- Record Muse **accelerometer and PPG** alongside EEG (once plain EEG is confirmed
  usable).
- A per-channel **EEG signal-quality view** in Review.
- Housekeeping: fix the stale `sync_beep.py` docstring; the already-removed
  `sync_engine.py` deletion item is moot (see
  [known issues](../developer-guide/known-issues.md)).

## Phase 6 — shipping (future)

Package as a double-click app (py2app / PyInstaller), **bundling ffmpeg** (the
recorder shells out to it) and the **`docs/` content** (the in-app Documentation
screen reads it at runtime). `tools/`, `tests/`, and `simulation.py` are
developer-only and excluded from the bundle. Rewrite `README.md` for a
non-technical audience.

## Phase 7 — team & cloud (future, not scheduled)

The longer-term vision: shared storage across every copy of the app (small data in
a hosted database, video in object storage), simple accounts with roles, a
per-recording history shared across the team, in-app pre/post-round
questionnaires, and a read-only **analysis mode** of Review & Label. **Blocker:**
boxers' names + brain data + questionnaire answers are personal data under GDPR, so
the university's data-protection officer must clear any hosting choice first (see
[personal data note](../research-and-data/personal-data.md)). A hard constraint on
every earlier phase: keep each recording folder **fully self-contained** so it can
be uploaded or synced as one unit.

## Deferred until hardware works

Items that can't be closed by writing code — a first real sparring recording and
raw-EEG inspection, the eyes-on-glass playback check, and the EEG clock-source
(buzzer/tap) verification. These are tracked in
[known limitations](../research-and-data/limitations.md) and
[known issues](../developer-guide/known-issues.md).

## The full plan

The complete roadmap, with the rationale and likely files for every item, is in
[`docs/ROADMAP.md`](planning-documents.md).
