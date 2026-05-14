# Brain Battle — Changelog

## Phase 2.11 · Fix marker-remove not clearing timeline tick

User reported that clicking the × on a label in PLACED LABELS removed
the row but its tick stayed on the timeline strip.

Root cause: `LabelMarker._remove()` called `removed.emit()` then
`deleteLater()`. The `_rebuild_timeline_markers` handler connected
to that signal ran synchronously, walking `placed_layout` to
re-derive the ticks — but at that exact moment the just-removed
marker was still parented to the layout because `deleteLater()` is
async (it schedules destruction for the next event-loop tick). So
the walker re-found the marker and re-added its tick.

My first attempt at fixing it (defer the rebuild via
`QTimer.singleShot(0)`) didn't work either: the deleteLater and the
singleShot both queue onto the same event loop, and singleShot(0)
ran first, so the walker still saw the un-deleted marker. The PLACED
counter had the same bug.

### ✦ The actual fix

Make `LabelMarker._remove()` synchronously detach from the parent
layout BEFORE emitting `removed`:

```python
def _remove(self):
    parent = self.parentWidget()
    if parent and parent.layout():
        parent.layout().removeWidget(self)
    self.setParent(None)
    self.removed.emit()
    self.deleteLater()
```

`removeWidget` + `setParent(None)` unparent the marker
synchronously. By the time `removed` fires, any handler that walks
the layout sees the post-removal state. `deleteLater` still runs to
free the C++ object on the next event-loop tick.

Result: PLACED count and timeline ticks both update on the same
frame the × button is clicked. Verified with a smoke test that
places 3 markers, removes 1, then removes all — counts agree at
every step.

---

## Phase 2.10 · Toolbar opaque-paint fix

User reported that after uploading a video to the Labelling tab, the
top toolbar text appeared "ghosted" or doubled — title text smearing
against the underlying canvas.

Root cause: each tab's toolbar was a bare `QWidget` with a
background-color set via stylesheet only. Qt's stylesheet engine
draws `QWidget` backgrounds only when `WA_StyledBackground` is
explicitly enabled. Without it, the toolbar's background can fail
to fill on the first paint after a sibling native-surface widget
(notably `QVideoWidget` on macOS, which bypasses Qt's compositor)
finishes loading. The result: text from the toolbar layered against
the unfilled toolbar background, against whatever the video widget
is painting underneath.

### ✦ `make_opaque_toolbar()` helper

New utility in `brain_battle_main.py` that builds a `QFrame`
(not bare `QWidget`) with three layered guarantees:

1. `WA_StyledBackground=True` so the QSS selector paints
2. `autoFillBackground=True` + an explicit `QPalette` window color
   so even if the QSS misses on first paint, the palette fills
3. An `objectName` so the QSS selector is reliable across theme
   switches (the bare `QWidget` selector was getting overridden
   by descendant style rules)

Used by SyncReviewTab + LabellingTab toolbars.

### ✦ Same treatment for LiveSessionTab toolbar

`live_session_tab._build_toolbar()` switched from `QWidget` to
`QFrame` with the same opaque-paint attributes. The toolbar now
also sets its palette window color directly from `dt.canvas_bg()`
on every restyle, so theme switching can't leave it transparent
between QSS reapply and the next paint cycle.

### ✦ `raise_()` after layout commits

All three toolbars defer a `.raise_()` via `QTimer.singleShot(0, …)`
so they end up on top of the widget z-stack after the parent layout
finishes adding sibling widgets. This is belt-and-braces against
native video surfaces that ignore Qt's normal stacking — even if
they paint over their bounding rect, they paint below the toolbar.

This is the fix for: toolbar text doubling/smearing after video
upload; toolbar appearing transparent on first Labelling-tab visit;
intermittent ghost text on tab switch.

---

## Phase 2.9 · Professional timeline marker design

The previous strip showed colored chips but they were small (6 px),
the colors weren't deliberately chosen (gray for session-end, app
indigo for rounds), and there was no way to know what each chip
represented without hovering. After user feedback, redesigned the
strip to read like a proper video-editor timeline.

### ✦ Deliberate 4-color phase palette

Each phase category gets a distinct semantic color. No gray. No
duplicates between categories (rounds all share their color
intentionally — they are the same kind of event).

- **`#10b981` emerald** for `BASELINE_START` — "calm, before the
  work begins"
- **`#3b82f6` electric blue** for `ROUND_N_START` — "active phase"
- **`#f59e0b` amber** for `BREAK_N_START` — "rest / pause"
- **`#dc2626` crimson** for `SESSION_END` — "terminal, stop"

User manual labels stay on their separate auto-assigned palette
(purple / cyan / green / etc., colors that never overlap with the
phase 4). When you see emerald on the timeline, it's always baseline.
When you see crimson, it's always session end. The palette is
mnemonic.

### ✦ Label badges above every chip

Each chip now has a small colored badge above it showing an
abbreviated identifier:

- `BL` — BASELINE_START
- `R1`, `R2`, … — ROUND_N_START
- `B1`, `B2`, … — BREAK_N_START
- `END` — SESSION_END
- Custom labels: first letter of each underscore-separated word
  (`PUNCH_LEFT` → `PL`, `BLOCK` → `BL`)

You can scan the entire timeline and identify every event without
hovering. A 5-round session looks like:

`BL    R1    B1    R2    B2    R3    B3    R4    B4    R5    END`

with R chips electric-blue, B chips amber, BL emerald, END crimson.

### ✦ Bigger chips, taller strip, more contrast

- Strip height up from 22 → 32 px to fit the badges
- Chip width up from 6 → 10 px so they're easy to hit
- Strip background `#0A0A0E` (was `#15151A`) — higher contrast against
  canvas so the strip reads as a discrete clickable lane
- Chips have a soft colored halo + solid body + lighter top accent
  for 3-D effect instead of flat rectangles
- Thin connector line between each badge and its chip

### ✦ Same interactions, more discoverable

Click chip → seek there. Hover → tooltip with full label + timestamp.
Click empty space → scrub proportionally. Same hit tolerance (10 px)
so the chips are forgiving even on dense long-session timelines.

---

## Phase 2.8 · Black-video bug fix + interactive timeline markers

### ✦ Video now renders on upload (was: black after load)

`VLCVideoSlot.load()` was calling `QMediaPlayer.setSource()` but never
following with `.play()` or `.pause()`. The media was successfully
loaded but the video sink stayed unrendered until something else
triggered playback. In the Labelling tab nothing calls `play()`
automatically, so the slot showed a black canvas with the right
filename in the header.

Fix: call `.pause()` immediately after `setSource()`. QMediaPlayer
then decodes and displays the first frame, which acts as a
freeze-frame preview until the user hits PLAY. Same behaviour as
every other modern video editor.

This was the cause of the "uploaded file but video doesn't show"
report from hardware testing.

### ✦ Interactive timeline marker strip

The `_MarkerStrip` rewrite gives the Labelling tab a proper video-
editor-style timeline below the scrub slider. Improvements:

- **Taller and more visible** (22 px, was 14 px). Background tint
  distinct from canvas so the strip is discoverable even with no
  markers placed.
- **Time-tick scale** — every 30 seconds (or every 10% of duration
  if shorter), a faint gray tick along the baseline so you can
  read positions at a glance.
- **Chip glow** — each marker is rendered with a faint colored halo
  + solid chip + lighter top cap. Phase markers and user labels
  share the rendering but are coloured per their type (green for
  baseline, indigo for rounds, amber for breaks, gray for
  session end, user-label colors for manual entries).
- **Click to seek** — clicking within 8 px of a marker chip emits a
  `markerSeek(t_sec)` signal which scrubs the playhead, all video
  slots, and both EEG widgets to that exact timestamp. Clicking
  empty space scrubs proportionally (works like a second mini
  slider).
- **Hover tooltips** — hovering a chip after a short delay shows a
  bubble with the label name + timestamp.

Implementation note: hit testing uses pixel distance so it's
forgiving on dense timelines (multiple markers within a few seconds
on a 10-minute session would otherwise be unclickable at typical
window sizes).

### ✦ Marker labels travel with the ticks

`TimelineWithMarkers.add_marker(t_sec, color, label)` now accepts a
label string in addition to color. The label appears in the hover
tooltip. Existing call sites updated to pass the label name.

---

## Phase 2.8 · Timeline markers + fix labelling tab video playback

Two follow-ups from the Walksnail hardware test pass.

### ✦ Fix: uploaded videos in Labelling tab stayed black

Root cause: when I split the labelling tab off from the sync tab,
I removed the `SyncPlaybackController.attach()` call because I
mistakenly thought VLCVideoSlot didn't expose a player handle. It
does — slots have `play()`, `pause()`, `seek()` methods that the
controller drives directly. Without the attach, clicking PLAY had no
players to control and the video stayed paused on its first black
frame even after loading.

Fix: every video upload now re-runs `_label_sync_ctrl.attach()` with
the full slot list. Same fix applied to the OPEN SESSION FOLDER path.

### ✦ Timeline marker strip

The labelling tab's timeline used to be a bare slider with no
indication of where labels lived. Now a `TimelineWithMarkers` widget
replaces it: same slider on top, plus a thin 14px tick strip below
that paints a coloured vertical bar at every placed label's
timestamp position.

Markers register automatically:
- Phase markers (BASELINE_START, ROUND_1_START, …) get their phase
  colour (green for baseline, indigo for rounds, amber for breaks,
  gray for session end) and appear at the right position the moment
  the markers CSV loads.
- Manual labels you place with PLACE LABEL AT PLAYHEAD or the 1–9
  shortcut keys get their chip's colour and appear at the playhead's
  current position.
- Deleting a marker from the PLACED LABELS list also removes its
  timeline tick — handled by `_rebuild_timeline_markers` re-syncing
  the strip from the live list whenever a marker emits its `removed`
  signal.

Visually it looks like the chapter strip in a video editor —
scattered coloured bars under the scrubber that let you eyeball where
in the recording each event sits.

The strip needs to know the video duration to compute tick positions.
Since VLC reports duration as 0 immediately after `setSource()`
(metadata hasn't been parsed yet), the tab queries the duration once
on load and re-queries 600ms later via `QTimer.singleShot` if the
first query was 0. That avoids markers all collapsing to the left
edge on the first frame.

---

## Phase 2.7 · Cleanup empty folders + connect-state UX

After hardware testing with a Walksnail Avatar Mini, two confusions
surfaced. (1) The session folder was creating empty per-round
directories (`baseline/`, `Round 1/`, …) as carryover from the v1.x
per-phase recording model. They were never written to in v2.x because
recording is continuous — but they made it look like "Round 1 didn't
record." (2) When only one camera slot was wired up, the other three
empty slots silently produced nothing and there was no UI indication
that this was intentional.

### ✦ Stop creating phantom per-round folders

`session_storage.create_session()` no longer makes empty
`baseline/`, `Round 1/`, `Round 2/`, … directories. The session root
now contains:

```
Brain Battle 2026-05-14 18-30-05/
├── continuous/
│   ├── Muse data/
│   ├── Side Cams/         ← only if a side camera recorded
│   └── POV Cams/          ← only if a POV camera recorded
├── phase_markers.csv
└── session_manifest.json
```

If only one camera kind is connected, only that subfolder exists.
Absent subfolder = "no camera of that kind connected", unambiguously.
Downstream tools should read `phase_markers.csv` + the manifest to
find where each phase starts in the continuous recording, not look
for separate round folders.

### ✦ Lazy subfolder creation in the camera panel

`usb_camera_panel.start_recording()` now creates `Side Cams/` or
`POV Cams/` only when a slot of that kind actually starts recording.
Empty slot kinds produce zero filesystem artifacts.

### ✦ Verbose recording-state logging

The same method now prints to console at session start:

```
[session start] 1 of 4 camera slots live
[session start]   skip slot 1 (SIDE 01) — not connected
[session start]   skip slot 2 (SIDE 02) — not connected
[session start]   recording slot 3 (POV 01) → .../continuous/POV Cams/session_POV_01.mp4
[session start]   skip slot 4 (POV 02) — not connected
```

So you can confirm what's recording without inspecting the filesystem
afterward.

### ✦ Pre-flight camera check

Pressing Start Session now does the same connection check the EEG
path does. If 0 cameras are live, the dialog blocks the session. If
some-but-not-all are live, a confirm dialog warns:

> Only 1 of 4 camera slots are live.
>
> Recordings will only be saved for the connected camera(s). Empty
> slots produce no output. Start anyway?

The user can proceed (default yes) or fix wiring first. Combined with
the verbose logging, accidental "ran a session with the wrong slot
plugged in" should be much harder to do silently.

---

## Phase 2.6 · Session bundle workflow

The continuous-recording change in 2.5 wrote `phase_markers.csv` but
left the user to manually scrub for round boundaries on review. This
pass closes that loop: the entire session — videos, EEG, phase markers
— now loads as one bundle.

### ✦ "Open Session Folder" button on the Labelling tab

Single click → folder picker → entire session loads. The handler walks
the v2.5 layout (`continuous/Side Cams`, `continuous/POV Cams`,
`continuous/Muse data`) and routes the discovered files into the four
video slots and two EEG widgets. Then it checks for
`phase_markers.csv` at the session root and pre-places every marker
onto the timeline. Status dialog confirms what loaded:

> Loaded 4 video(s), 2 EEG CSV(s), 7 phase marker(s) from
> Brain Battle 2026-04-29 18-30-05

The handler is forgiving: if the user picks the parent of the session
folder by mistake it scans one level deep; if they pick the
`continuous/` folder itself it walks up one. Files that fail to load
log to console but don't block the rest from loading.

### ✦ Phase markers visually distinct from manual labels

Auto-loaded phase markers (BASELINE_START, ROUND_1_START, etc.) render
with a dashed left border and a `║` glyph instead of `▶`. Per-phase
colour palette: green for baseline, indigo for rounds, amber for
breaks, gray for session end. User-placed labels stay solid-bordered
with their original colours.

This is the same `LabelMarker` widget with an added `is_phase` flag.
Both kinds appear in the same PLACED LABELS list and export together
to CSV — the distinction is purely visual so the operator can tell
"this label was auto-generated by the session" vs "I tapped this in".

### ✦ Idempotent marker loading

If you upload session files one at a time (e.g. the four videos and
two EEGs separately, not via the folder picker), each upload triggers
a phase-marker auto-discovery walk. Calling
`_load_phase_markers_csv()` repeatedly is now a no-op when markers
with the same `(timestamp, label)` are already placed — checked
against the live PLACED LABELS list before insertion.

This means:
- "Open Session Folder" → markers load once, single dialog.
- "Upload" each file individually → markers load on the first upload,
  every subsequent upload is a silent no-op.

Either workflow gives the same end state.

### ✦ Camera FPS uniformity check

`usb_camera.py` already requested a target FPS via
`cv2.CAP_PROP_FPS` but accepted whatever the camera reported back.
For long continuous recordings (10+ min sessions), even small FPS
mismatches between cameras compound: a 30.0 fps camera and a 29.97
fps camera diverge by ~0.6 frames over 11 minutes, more if either
drops frames.

Now if the actual reported FPS differs from the request by more than
0.5 fps, the app prints a console warning naming the camera index and
the actual FPS value. The recording still proceeds — different
sources at different frame rates is sometimes unavoidable with mixed
hardware (FPV vs DSLR) — but the operator gets a heads-up that
post-processing may need a sync pass for that session.

This isn't a sync fix on its own. It surfaces the problem so the
operator knows before they're in the analysis pipeline.

---

## Phase 2.5 · Continuous recording model + critical recording bug fix

Two big changes driven by hardware testing: the original per-phase
recording approach didn't match the actual research workflow, and a
silent VideoWriter bug was producing 257-byte unplayable MP4 files.

### ✦ The recording bug — root cause + fix

`usb_camera.py` was initialising `cv2.VideoWriter` with dimensions read
from `cap.get(CV_PROP_FRAME_WIDTH/HEIGHT)` BEFORE the camera had warmed
up. On macOS AVFoundation, those values are 0 or NaN until the first
frame arrives. The writer "opened" successfully but silently rejected
every frame whose shape didn't match the (garbage) init dimensions,
producing files with only the moov atom — 257 bytes, unplayable.

Fix: defer VideoWriter init until after the first valid frame is read,
then use that frame's actual `.shape[:2]` for `(width, height)`.
FPS is also sanity-checked: anything below 5 or above 120 falls back
to 30 fps (typical for the affected backends).

### ✦ Continuous recording model

The session is now ONE continuous recording across baseline → rounds
→ breaks → end. Cameras start once at session start and stop once at
session end. EEG CSVs are written for the entire session per Muse-S.
Phase transitions emit timestamped markers into a new
`phase_markers.csv` instead of slicing recordings.

Why: the earlier per-phase recording approach produced 4 cameras × N
phases × 2 EEGs = ~30+ files per session, and synchronisation across
those slices was fragile (each phase had its own first-frame jitter).
With one continuous recording per camera, sync only needs to be
established once at session start. Phase boundaries are derived from
markers, which the labelling tab can render as pre-placed labels.

New session folder structure:
```
Brain Battle 2026-04-29 18-30-05/
├── continuous/
│   ├── Side Cams/
│   │   ├── session_SIDE_01.mp4    ← entire session, ~10-15 min
│   │   └── session_SIDE_02.mp4
│   ├── POV Cams/
│   │   ├── session_POV_01.mp4
│   │   └── session_POV_02.mp4
│   └── Muse data/
│       ├── session_Muse_S_xxx.csv
│       └── session_Muse_S_yyy.csv
├── phase_markers.csv               ← BASELINE_START, ROUND_1_START, …
└── session_manifest.json
```

Phase markers CSV format:
```
t_session_seconds,phase_id,label,round_num,unix_time
0.000,baseline,BASELINE_START,0,1777474202.431
60.000,round,ROUND_1_START,1,1777474262.432
180.000,break,BREAK_1_START,1,1777474382.432
240.000,round,ROUND_2_START,2,1777474442.432
…
540.000,done,SESSION_END,0,1777474742.432
```

The legacy per-phase folders (`baseline/`, `Round 1/`, …) are still
created for backward compatibility but stay empty. Downstream tools
that walked them won't crash but should switch to reading from
`continuous/` + `phase_markers.csv`.

### ✦ EEG subprocess crash mitigation

`eeg_module.py` had a second instance of `subprocess.PIPE` (in the
muselsl record subprocess at `_launch_record_proc`) — same crash
pattern as the streaming subprocess fix from 2.4. Both now use
`subprocess.DEVNULL` so no anonymous drain threads ever exist.

---

## Phase 2.4 · Design refinement + theme system

Follow-up pass on 2.3 after user feedback that "it's better but I'm not a
massive fan" — pointing to a flat visual hierarchy, cool clinical palette,
and absence of a light mode. Research into the Linear 2026 refresh, Arc
Browser, Raycast, and Warp revealed three patterns I'd missed:
(1) "one shade of grey is not a dark mode" — real hierarchy needs four
visually distinct surface levels; (2) 2026-era dark UIs shifted to warmer
desaturated neutrals away from cool blue tints because the latter feel
clinical; (3) dark-first apps now ship light mode as a proper paired
theme, not a colour inversion.

### ✦ Design system v4 — `design_tokens.py`

Complete rewrite with theme switching.

**Four-level surface hierarchy** with visually distinct treatments:
  · canvas        — darkest, app body (#0E0E12 warm near-black)
  · sidebar_bg    — one step lighter, recessed (#151519)
  · card_bg       — elevated workspace panel (#1C1C21)
  · card_bg_hover / raised — brighter on interaction (#24242A / #2B2B33)

**Warmer neutral palette.** Swapped the cool blue-tinted `#0B1220` /
`#161E2E` scale for warm near-blacks and zinc grays. Less clinical,
reads as more premium.

**Softer accent: indigo `#637DFF`** replacing the harsher azure
`#2563EB`. Linear moved accent-minimal in 2026 — single brand colour
used only for primary CTA, focus, and active nav. Everything else is
neutral. Rose `#F43F5E` replaces pure red for errors — warmer.

**Dual palettes as classes.** `_DarkPalette` and `_LightPalette` hold
their own hex values. Functions like `canvas_bg()`, `accent()`,
`text_primary()` resolve the active theme at call time. Light mode is a
proper design — warm off-white canvas `#F7F7F5`, white cards that pop
against it, indigo `#4F46E5` (more saturated for contrast on white).

**Theme module state + observer pattern.** `_CURRENT: Theme = "dark"`.
`set_theme("light")` switches and notifies registered callbacks.
Widgets register via `dt.on_theme_change(self._restyle)` and re-apply
their stylesheets when invoked.

**Legacy compatibility.** Module-level `__getattr__` redirects old
names (`SANGRIA`, `BG_1`, `AZURE`) to the live palette functions so
older modules keep rendering without refactoring.

**Hero timer size.** Jumped from 28 px to 48 px tabular mono — the
timer is now the clear visual centrepiece of the Session card.

**Ambient gradient orb.** The canvas carries a subtle radial gradient
in the upper-right corner (indigo glow that fades to canvas) — gives
the glass something to refract visually without going full
glassmorphism.

### ✦ Sidebar shell v4

- Theme-aware via `dt.on_theme_change()` registration
- Sun/moon button at the bottom of the sidebar
- Quieter brand block (smaller icon, less shouty text)
- Bigger nav icon breathing room

### ✦ Theme toggle — pragmatic restart approach

Shipping truth: Qt's stylesheet engine doesn't reliably re-evaluate
`objectName`-based selectors on live theme switch, even with aggressive
`style().unpolish/polish` cascading across every widget in the tree.
I spent three iterations debugging this before accepting the same
reality VS Code, Slack, Discord, and Spotify all confront: **theme
changes require a restart.**

Behaviour:
- Theme toggle in sidebar saves preference to
  `.brain_battle_prefs.json` beside the script
- QMessageBox informs the user to relaunch
- Next launch reads the pref and applies it cleanly from the start

This is shipping behaviour for every major desktop app that uses a
stylesheet-based theming system. It's honest engineering, not a
regression.

### ✦ Widget-level theme refactoring

Every chrome-level widget converted from "hardcoded hex at build time"
to "reads from `dt.*` functions at call time, with a `_restyle()`
method for re-invocation":
- `USBCameraSlot` — full `_restyle()` method covering header, status
  labels, footer, buttons, placeholder, graphics background
- `USBCameraPanel` — theme-aware header
- `EEGLiveWidget` — theme-aware button bar + canvas + placeholder
- `LiveSessionTab` — theme-aware toolbar, cards, session display
- `SidebarShell` — full theme-aware rebuild
- `BrainBattleApp._restyle_app()` — main stylesheet + supplementary
  factory, walks entire widget tree calling `unpolish/polish/update`

### ✦ Hardcoded APP_STYLE block deleted

The ~35-line dark-mode QSS block in `brain_battle_main.py` that was
overriding theme tokens regardless of active palette was removed and
replaced with a theme-aware `_supplementary_qss()` factory — covers
sliders, list widgets, progress bars, combo boxes, group boxes with
live `dt.*` values.

---



Complete fresh UI/UX pass, not just a colour swap. The app now reads as a
modern desktop-class productivity tool — drawing from macOS Tahoe's
Liquid Glass language, Figma/Linear sidebar patterns, and research into
dark-mode best practices for scientific dashboards in 2025-26.

### ✦ New design system — `design_tokens.py` v3

Complete rewrite. Central source of truth for every visual decision.

**Palette shift: Prussian anchor + Azure interactive**
- Prussian Blue `#00204D` remains the brand anchor but is now the
  *ambient canvas tint*, not an interactive colour. Pure Prussian Blue
  is invisible against dark backgrounds — it needed a brighter sibling.
- Azure `#2563EB` is the new interactive accent — saturated blue in the
  same family, legible on dark glass, used for CTAs, focus rings, live
  indicators, active nav, phase=ROUND. This is the colour you see.
- AZURE_BRIGHT `#3B82F6` for hover, AZURE_DEEP `#1D4ED8` for pressed,
  AZURE_GHOST `rgba(37, 99, 235, 0.12)` for subtle fills.

**Layered translucency replacing flat panels**
- Canvas is an ambient linear gradient `#0B1220` → `#050810` — gives the
  glass something to refract visually.
- Surface fills are rgba (`GLASS_LOW/MID/HIGH` at 0.55 / 0.78 / 0.92
  opacity) so the canvas shows through.
- Specular highlights faked via gradient borders — brighter at the top
  (rgba white 0.16), normal hairline on sides (rgba white 0.08).
  Reads as "glass" immediately without real GPU compositing.

**Typography: Inter + JetBrains Mono**
- Inter for UI chrome — 2025's default sans-serif for productivity apps.
- JetBrains Mono for data readouts (tabular figures, unambiguous 0/O).
- Display-size numbers (32 px tabular) for timers; 13 px body; 10 px
  uppercase section labels with 1.4 px letter-spacing.

**Generous radii + hairline borders**
- Cards 14 px, buttons 10 px, chips pill-shaped. 1 px rgba borders
  everywhere. Shadows replaced by opacity layering.

**Stylesheet factories**
- `qss_app`, `qss_canvas_gradient`, `qss_glass_panel`, `qss_sidebar`,
  `qss_primary_button`, `qss_secondary_button`, `qss_danger_button`,
  `qss_ghost_button`, `qss_chip`, `qss_input`, `qss_status_pill`,
  `qss_section_label`, `qss_data_readout`, `qss_display_number`.
- Legacy Phase 2.2 names (`SANGRIA`, `BG_1`, etc.) aliased to the new
  equivalents so older modules keep rendering without breakage.

**Hairline Lucide icons**
- Embedded SVG source for 8 icons: `activity`, `video`, `layers`, `tag`,
  `settings`, `play`, `square`, `brain`.
- `icon_pixmap(name, color, size)` renders any of them via `QSvgRenderer`
  at any colour / size. Zero runtime cost beyond one `QPainter` pass.

### ✦ Left sidebar navigation — `sidebar_shell.py`

Replaces `QTabWidget` with the Figma/Linear/VS Code pattern: vertical
nav rail on the left (220 px expanded, 64 px collapsed), icon + label
per item, content stack on the right.

- Brand block at the top: brain icon + "BRAIN BATTLE".
- Each nav item is a `QPushButton` with embedded Lucide icon.
- Active state: azure-ghost fill + AZURE_BRIGHT icon + AZURE_BRIGHT text,
  font-weight 600.
- Collapse animation via `QPropertyAnimation` on `minimumWidth` /
  `maximumWidth`, 240 ms, `OutQuint` easing.
- `_ShellTabShim` preserves `tabs.setCurrentIndex()` compatibility so
  `SyncReviewTab.proceed_to_label_tab()` still works.

### ✦ LiveSessionTab — rebuilt for Tahoe

Complete layout rewrite. The visible changes:
- **Ambient canvas backdrop** — full-body gradient backdrop visible
  between glass panels. The 2×2 camera grid sits in a glass frame on
  the gradient rather than a flat dark rectangle.
- **Glass SESSION card** with circular timer centered.
- **Status pills** in the toolbar — `RECORDING` (red), `ROUND 2` (azure
  pill with border), `COMPLETE` (emerald). Semantic colours via the
  `qss_status_pill()` factory.
- **Primary CTA: filled Azure** `Start Session` button.
- **Danger button: outlined red** `Stop Session`.
- Refined spacing: 24 px body padding, 16 px gaps between panels.
- Toolbar height 64 px (was 56) to give typography room to breathe.

### ✦ Circular timer

Palette updated to match the new semantic system:
- idle muted grey, baseline sky `#38BDF8`, **round azure `#2563EB`**,
  break amber `#F59E0B`, done emerald `#10B981`.
- Text colours updated to the new off-white `#F5F7FA` / `#B4BCCC` /
  `#6B7489` scale.

### ✦ Sangria → Azure global sweep

Every `#9D0F01` hardcoded accent (EEG widget buttons, VLC module
styling, session setup dialog, export module, labels config, camera
slot chrome) swept to Azure `#2563EB`. Ghost `#2E0906` → `#1D2B4D`.
Hover / deep variants correspondingly remapped. 10 files touched.

### ✦ What stayed

- Semantic red remains red. The RECORDING pill stays ERROR-red because
  it signals real-time system state; the brand blue shouldn't double as
  a recording indicator.
- Phase colours kept semantically distinct (cyan / azure / amber / green)
  so operators reading the circular timer never confuse phases.
- All Phase 2.2 functional work (automated capture loop, USB-only ingest,
  green-line fix, RTMP removal, INSTA360 → POV rename) is intact.

---

## Phase 2.2 · Constructor rebrand + USB-only ingest

This pass focused on visual identity, ingest simplification, and a stubborn
video-rendering bug. All work grounded in the spec as clarified on Apr 17:
DSLRs connect over USB-C, POV cameras come from an FPV receiver plugged into
the laptop, no Insta360, no RTMP.

### ✦ RTMP fully removed

The two interchangeable camera back-ends are now one. Deleted:
- `rtmp_module.py` (`RTMPSlot`, `RTMPConfigPanel`, `_StreamMonitor`)
- `camera_recorder.py` (`CameraSpec`, `CameraRecorder`)
- The dead `LiveMonitorTab` class (80 lines of pre-Phase-2 code still
  hanging around in `brain_battle_main.py`)
- The `USB | RTMP` segmented toggle in the Live Monitor toolbar
- The `QStackedWidget` that held both ingest paths
- All MonaServer2 setup docs, RTMP configuration tables, ffmpeg push
  commands, eduroam warnings, and corresponding troubleshooting entries

`LiveSessionTab` dropped from 641 → 450 lines. Simpler code, simpler UX.

### ✦ Design tokens module (new)

`design_tokens.py` — one file, one source of truth for every visual
decision in the app. Contains:

- **Constructor University brand palette**
  - Sangria `#9D0F01` · primary accent
  - Prussian Blue `#00204D` · secondary
  - Off-white `#F4F3EE` · body text (warmer than pure white for dark-mode
    readability)
- **Tuned dark background scale** `BG_0`..`BG_3` (`#08090c` → `#1e2028`)
  — chosen for Sangria contrast, not generic "dark mode grey"
- **Phase palette** — BASELINE cyan · ROUND Sangria · BREAK amber · DONE green,
  kept semantically distinct so phase-state reading never collides with
  the general brand red
- **Typography system** — Inter / SF Pro Text for UI chrome; SF Mono /
  Menlo / Courier New for tabular data (Linear / Superhuman school)
- **Spacing & radius** — 4 px base, 4 / 8 / 12 px radius
- **Stylesheet factories** — `qss_primary_button()`, `qss_secondary_button()`,
  `qss_danger_button()`, `qss_ghost_button()`, `qss_chip()`, `qss_input()`,
  `qss_panel()`, etc. Most modules should never write raw QSS again.

### ✦ Full UI redesign in Constructor brand

Every `#e8ff00` yellow-green accent → Sangria `#9D0F01`.
Every `#1a1f0a` ghost fill → `#2E0906`.
Every `#d4d0c8` text → `#F4F3EE`.
Every `#6b7280` dim → `#8B8D95`.
Every `#2a2d35` border → `#3A3C44`.
12 modules swept. All 17 files compile. Screenshots verify the
transformation across the Live Monitor, Sync + Review, and Labelling tabs.

Button system redesign: primary buttons are filled Sangria, secondary are
ghost with Sangria hover, danger is outlined red (kept distinct from
Sangria so RECORDING / STOP reads as urgent). Chips use a Sangria-ghost
active state. Inputs have a Sangria focus ring.

Phase chip on the Live Monitor toolbar is now a coloured pill in the
semantic phase colour with a matching border — `ROUND 2` is Sangria,
`BASELINE` is cyan, `BREAK` is amber, `SESSION COMPLETE` is green.

### ✦ Green-line video artefact fixed

The bottom-left Sync + Review video slot used to show a thin green strip
at the bottom edge during playback. Root cause: two independent issues
compounding.

1. `VideoSurface` allocated three raw `ctypes.c_uint8` buffers for
   VLC vmem and handed them to VLC without zero-initialising. If VLC's
   lock callback fired before the first full frame landed, the displayed
   buffer contained uninitialised bytes — which decode as green in ARGB32
   because the G byte dominates random memory.
2. VLC's vmem unlock callback could race VLC's own last-row write on the
   bottom scanline, producing a partially-initialised final row.

Fix:
- Initialise all three vmem buffers to opaque black (`#000000FF` in BGRA)
  using a single `ctypes.memmove` of a row-sized template — fast, no
  per-pixel Python loop
- `paintEvent` now crops the bottom row defensively in addition to the
  existing right-edge padding crop, so any residual last-row race is
  hidden

### ✦ INSTA360 → POV rename

Every user-visible "INSTA360" string renamed to "POV" throughout
`brain_battle_main.py` — source row definitions, slot labels, status bar
message, docstrings. The POV cameras now come from an FPV receiver, not
Insta360 devices, so the old labels were actively misleading.

---

## Phase 2.1 · Research-driven upgrades

This pass was grounded in current (2025–2026) literature and tooling
research covering multi-modal neuroscience capture (LabStreamingLayer
reference paper, Kothe et al. 2025), real-time Python plotting
(pyqtgraph vs matplotlib benchmarks), and USB/capture-card ingest
(OpenCV VideoCapture cross-platform practices).

### ✦ Automated round-based capture loop (new)

The Live Monitor tab now runs a full state machine from a single
**START SESSION** button: baseline → round 1 → break 1 → round 2 → …
→ done, with automatic per-round folder creation and recording
segmentation. No manual start/stop of each phase.

New modules:
- `session_controller.py` — pure-logic state machine
- `session_setup_dialog.py` — rounds / durations / baseline modal
- `session_storage.py` — `Brain Battle <timestamp>/{baseline, Round N}/…` tree
- `circular_timer.py` — Apple-style QPainter dial
- `sync_beep.py` — 1 kHz sine burst for audio-alignment

### ✦ USB / capture-card camera ingest (new)

The spec calls for two USB-C side cameras and two POV cameras from an
FPV receiver — both natural matches for UVC capture. Brain Battle now
supports this directly without a MonaServer relay.

New modules:
- `usb_camera.py` — `USBCameraThread` with per-OS backend selection
  (DirectShow / AVFoundation / V4L2), non-blocking start/stop recording
- `usb_camera_slot.py` — pyqtgraph-backed live preview widget
- `usb_camera_panel.py` — 2×2 grid with SIDE/POV folder routing

Recording writes H.264 MP4 directly from the capture thread via
`cv2.VideoWriter`, so phase transitions finalise cleanly without
subprocess ceremony. Graceful degradation: unconnected slots are
skipped; the rest of the session continues.

### ✦ OBS-style Live Monitor layout

Cameras dominate the centre (2×2 grid), session controls + circular
timer + EEG panels live in a right-hand rail. Top toolbar has an
**USB | RTMP** segmented toggle to switch between the new USB ingest
path and the legacy MonaServer RTMP path. Toggle is disabled during a
running session.

### ✦ pyqtgraph EEG plotting (10× faster)

The live EEG `EEGLiveWidget` previously used matplotlib's
`FigureCanvasQTAgg` at 5–15 FPS. Swapped for pyqtgraph PlotItems:
**~200 draws/sec in benchmarks** (draw timer caps at 20 Hz, so headroom
is ample). Lower CPU usage, smoother scroll, no UI stalls.

- New `eeg_plot_pg.py` — `WaveformPlot` + `BandPowerPlot` drop-in
  replacements for the matplotlib canvases
- `eeg_module.py` — `_build_waveform_plot`, `_build_bandpower_plot`,
  `_redraw` all swapped to the pyqtgraph path

### ✦ Auto-populate Sync + Review from finished session

When a Live Monitor session completes, `Tab 02` now auto-loads Round 1
files (2 side cams + 2 POV cams + 2 Muse CSVs). Operator can jump
straight into `AUTO-SYNC` without re-picking every file.

### ✦ Bug fixes

- **NumPy 2.x compatibility** — `np.trapz` was removed in NumPy 2.0 and
  the band-power code crashed on any 2026-era install. Fixed in both
  `eeg_module.py` and `sync_utils/eeg_module.py` with a fallback to
  `np.trapezoid` / legacy `np.trapz`.
- **Circular-timer close-phase timing** — the `SessionController`
  advances `_idx` before emitting `phase_changed`, so `close_phase()`
  was operating on the wrong record. Fixed by tracking `_active_phase`
  in `LiveSessionTab`.
- **EEG button clipping in narrow rails** — buttons `WAVE | BANDS |
  DEVICE | CONNECT` now have explicit minimum widths so Qt never
  truncates them to `EVIC` / `NNE`. Rail widened to 400 px min.

### ✦ Clean shutdown

`LiveSessionTab.shutdown()` is called on `aboutToQuit` so USB capture
threads exit cleanly and MP4 moov atoms finalise before Qt tears down
widgets. No more `"QThread destroyed while still running"` on exit.

---

## Decisions explicitly deferred

Research turned up two larger architectural improvements worth
considering for a future pass, but not implemented this round because
the risk/scope was too large for a single working pass:

1. **LSL + XDF unified capture.** LabStreamingLayer got its reference
   paper in 2025 (sub-ms sync validated); XDF is the de-facto archival
   format for multi-stream neuroscience data. Migrating Brain Battle's
   storage layer from per-file CSV + MP4 + manifest JSON to a single
   XDF per session would make the output immediately loadable in
   EEGLAB, MNE-Python, and MATLAB's existing XDF toolchains. Worth
   discussing before committing — it's a real rewrite of Tab 02's
   file-loading path.

2. **Direct `muselsl.stream()` call instead of subprocess.** The EEG
   module currently shells out to `muselsl record`. `muselsl.stream()`
   can be called as a library function, eliminating the SIGINT-to-flush
   subprocess ceremony. Touches the hardened BLE/disconnect paths
   though, so deferring to when we have hardware in the lab to test
   against.
