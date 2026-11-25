# Brain Battle — Design spec

**This file is the source of truth for all UI work.** If code and this file
disagree, ask; don't guess.

**History.** First approved 2026-10-27; rewritten 2026-11-13 for the November
redesign (ADR-0018). **Rewritten 2026-11-17 for design F** — the approved
clickable prototype `docs/design/prototype.html` (open it in a browser), with
the professor's changes: fighter codes and colours instead of names
([ADR-0023](decisions/adr-0023-fighter-codes-and-colours.md)), a 15 s lead-in,
automatic system markers, moment notes, custom notes, the bell at both ends of a
round, and the timing check ([ADR-0024](decisions/adr-0024-timing-check-and-buzzer-calibration.md)).
Where the prototype and the written brief differed, the brief won; the
differences are listed at the end. Awaits the owner's review with the rest of
`develop`.

## Who it is for

- **Operators** — researchers and assistants running a session in a gym or lab,
  often standing, sometimes reading the screen from a few metres away.
- **Labellers and analysts** — at a desk, for hours, with a keyboard. Several
  people may label the same recording.

None of them are developers.

## Principles

1. **Three jobs, one click each:** start a recording, label a recording,
   export it.
2. **Plain language only.** Never show: sync, align, offset, xcorr, capture
   clock, POV, monotonic, schema, slug. *Deliberate exceptions:* the EEG
   display is a standard viewer (TP9, AF7, AF8, TP10, AUX, µV, a time axis); the
   automatic markers are shown by their code (`ROUND_1_START`) in grey; the
   timing check speaks of delay, jitter and milliseconds — the people who read
   those screens need the real words.
3. **Fighters are codes and colours, never names.** A labeller has a name.
4. **Light mode only.** The area behind video is dark because it is picture.
5. **Status is never colour alone:** a glyph (✓ ! ✕ ●) plus words.
6. **Errors say what happened and what to do.**
7. **Nothing is lost silently.** Autosave everywhere; a failed save says so.
8. **Recording never pauses** — not for a note, not for the timing check.
9. **Mac and Windows read the same** (Finder / Explorer, ⌘Z / Ctrl+Z).
10. **Sizes:** main actions ≥ 42 px; text contrast ≥ 4.5:1; everything fits
    1440×900 and 1280×800, usable at 1180×720.

## Vocabulary (UI word ← internal name)

- Side camera 1 / 2 ← side cams; Boxer A view / Boxer B view ← POV 01 / 02
- Headband ← Muse-S; the EEG display shows the electrode names
- Fighter code (K7Q2) and colour ← `boxers.A.code` / `colour`
- Lead-in · Baseline · Round N · Break ← phases; automatic markers ← system
  markers (`RECORDING_START` … `RECORDING_END`)
- Moment (⚑, M) ← a "Mark this moment"; its note ← `moments[].note`
- Custom note ← a label with `type: "note"`
- "signal looks poor" ← the contact heuristic (the band does not report fit)
- Test timing / buzzer test ← the timing check (ADR-0024)
- Your name / labeller

## Design tokens

Fonts: **Inter** (UI) and **JetBrains Mono** (codes, times, file names), both
bundled. Timers use equal-width digits.

- **Surfaces:** background `#F6F8FC` · surface `#FFFFFF` · line `#E3E8F0` ·
  strong line `#D9DFEA` · behind video `#1D2433`
- **Text:** ink `#0F172A` · secondary `#344054` · muted `#556070`
- **Blue (the one accent):** `#2155D6` · hover/text `#1A44AD` · soft `#EAF0FD`
- **EEG:** traces navy `#1C2A8C` · frame `#30343C` · grid `#DCDFE5` · automatic
  markers grey `#8A93A3` · moments red · labels in their colours
- **Good** `#1E7B3C` on `#E5F3EA` · **Warn** `#8A4200` on `#FDF0E3` (border
  `#F2B57A`) · **Red** `#D93025` (text `#B3261E`, soft `#FCE4E2`)
- **Head sensor dots:** good `#2E9E5B` · poor `#E8890C`
- **Fighter colours:** Red `#D3241B` · Blue `#1B45C4` · Black `#111111` (more
  can be added for the team)
- **Status chips:** Needs labels (warn) · Labelled (good) · Exported (blue) ·
  Interrupted (red)
- **Label list defaults (keys 1–8):** Punch initiated `#2155D6`, Punch landed
  `#0F7A62`, Punch missed `#7A3FC4`, Punch blocked `#465063`, Punch dodged
  `#A8570A`, Clinch `#B5246B` (lasts a while), Knockdown `#B42318`, Referee break
  `#161A22` (lasts a while) — the professor's list, no punch types. A team list
  that is still exactly an old default (Jab, Cross, Hook, Uppercut, Block,
  Clinch, Knockdown, Referee stop) becomes this list once, noted in the list's
  history; a list the team edited is kept.
- **Radii:** 10 px controls, 16 px cards, 18–22 px hero cards. Type: 28 page
  title · 22 card title · 17 section · 15 body · 13 small.

## Components

From `brain_battle/ui/widgets.py`: primary button (blue, one per screen), soft
button (blue-soft), secondary (white, bordered), dashed "add" buttons, pill
filters, status chips, the fighter badge (a square A/B in the fighter's
colour), the fighter name (colour dot + code in bold mono), cards, banners,
status lines, the head icon with four sensor dots, segmented controls, the flow
layout for chips.

- **The dialog** (`AppDialog`, `ask`, `tell`): a white card with 20 px corners
  on a dimmed cover over the window (a light cover on Recording, so the
  recording stays visible); a title, the text, buttons on the right. Variants:
  **warn** (orange border — "Stop the recording now?", "Signal looks poor",
  "Low disk space") and **danger** (red border and title — "A recording is
  still running", removing a shared label, a folder or disk problem). Anything
  that stops a recording makes the safe answer ("Keep recording", "Go back")
  the blue button. **No native macOS / Windows alert or message box is used
  anywhere**; the only system windows are the folder / file pickers.
- **The dropdown** (`Dropdown`): white, rounded, Inter, a blue border when
  focused or open, its own chevron and a light list — never the system's
  dropdown. Used for the camera and headband choices, Compare and the EEG
  options.
- **Text fields** are always white with dark text and a normal border, blue
  when focused, whatever the computer's own light/dark setting. The whole app
  is always light: at start-up it uses Qt's Fusion style and its own light
  palette for every colour role (`design_tokens.apply_light_style`), so no
  field, list, menu, dropdown, drawer or dialog takes the system's dark colours.

## App structure

A white **top header**: the logo (back to Sessions) · **Sessions** · (space) ·
**Documentation** · the **Settings** gear. No sidebar. New recording and Review
& Label are full-screen with their own headers. The first launch on a computer
shows **Where are your team's recordings?** once.

## Screens

### 0. First run — Where are your team's recordings?

One card: a shared team folder (path, Choose folder…, "✓ Found 5 recordings
and your team's settings in this folder") or Just this computer (Documents ›
Brain Battle Data); "Can't see the shared drive?" (how to connect on this
system); **Continue**; "You only see this once on this computer."

### 1. Sessions (home)

- A big blue **● New recording** button ("Connect the headbands and cameras,
  give each fighter a code and colour, then follow the checklist."), and two
  cards: **Recordings** (count) and **Labelled** ("3 of 6").
- **Sessions** title, filter pills with counts (All · Needs labels · Labelled
  · Exported · Interrupted), search ("Search a fighter code or date").
- One card per recording: `● K7Q2 vs ● M3XA`; `Mon 16 Nov 2026 · 3 rounds ·
  12 min` (or "Interrupted · saved up to 6:27"); a progress line ("You: 4
  labels, 1 moment without a note"); the status chip; ⟲ (history drawer); the
  right button — **Start labelling**, **Continue labelling**, **Add my labels**
  or **Open**. A recording whose moments still lack a note is *Needs labels*.
- History drawer: slides over the page from the right on a dimmed cover (it
  never squeezes the rows): a white panel with the fighters, who labelled (with
  counts), and every event, newest first — a coloured dot, **who** and what, and
  the date under it.
- Footer: "Saved in Lab Server › Brain Battle Data · 73.9 GB free · fighters
  are stored only as codes".
- Banners: the interrupted-recording notice (Open it · Dismiss); the folder
  can't be reached (Try again).

### 2. New recording — Get ready

Header: ✕ Cancel · **New recording** · 1 Get ready · 2 Record · 3 Saved.

- **Left, the checklist:** Headband A connected · Headband B connected · Good
  signal on all sensors · 4 cameras connected · Fighter codes and colours ·
  Timing checked (recommended) · Enough space. Then **⏱ Test timing** and
  **● Start recording** — unlocked when every required item is green; under it
  "Fix 2 things above to start" or "Recording starts 15 s before the baseline".
  *Good signal* and *Timing checked* advise but never block (the signal is our
  heuristic).
- **Cameras:** the four live pictures ("60 fps", or a dashed "Not found"),
  "4 of 4 connected", Search again. Above each picture (Side camera 1, Side
  camera 2, Boxer A view, Boxer B view) a **dropdown of the connected cameras**:
  picking one moves that camera's picture into the slot; a camera can be in one
  slot only, so picking one that is in another slot swaps the two. Remembered
  for next time.
- **Boxer A / Boxer B cards:** the badge in the chosen colour; **Boxer A code**
  (4 characters, typed in capitals; a wrong code is outlined red with "Use
  exactly 4 capital letters or digits, like K7Q2."); **New code**; colour
  swatches; the head with four sensor dots; **Headband** — a dropdown of the
  connected headbands ("MuseS-7538 · battery 81%"); one headband can't be on
  both boxers, so picking the other boxer's swaps them (remembered, and kept as
  the default in Settings); "✓
  Signal looks good on all 4 sensors" or "The right-ear signal (TP10) looks
  poor. Often the band isn't flat behind the ear or hair is in the way. Adjust
  it, then press the button." + **I've adjusted it**; the live EEG (5 s).
- **⇄ Swap the headbands between Boxer A and Boxer B.**
- **The plan:** Rounds · Round · Break · Baseline steppers; "Ring the bell at
  the start and end of each round"; "0:15 lead-in + 12:00 = 12:15 in total ·
  remembered for next time".
- **Test timing dialog:** a row per device — Rate, Delay (raw), Jitter,
  Dropped, After correction — and **Run buzzer test**: 5 beeps 1.5 s apart, both
  boxers tap their headband at each beep in view of the cameras. Result: "✓
  Largest difference between devices after correction: 4 ms. Under the 10 ms
  goal." or what to do if over.

### 3. New recording — Recording

Header: ✕ Cancel · ● REC 3:27 · **New recording** · `● K7Q2 vs ● M3XA ·
everything is recording` · ✓ Get ready · 2 Record · 3 Saved · **■ Stop
recording**.

- The four cameras with "Recording" and a timecode.
- Under them, a card with the countdown (large mono "2:41" — small next to the
  cameras, readable from a distance) "left in round 2" / "until the baseline
  starts", the phase ("Round 2 of 3", "Lead-in (recording already running)"),
  "1 of 3 rounds done · next: break", and the plan bar with a playhead.
- **⚑ Mark this moment (M)**: a small window opens — "⚑ Moment marked at 3:27 ·
  ● still recording", "What did you see? (optional)", **Add note later** /
  **Save note**; Enter saves, Esc means later. Nothing pauses.
- Right: each boxer — head sensors, badge, code, "Signal looks good on all 4
  sensors" or "Right-ear signal (TP10) looks poor — check the band in the next
  break", battery, the live EEG (5 s) with grey automatic markers and red
  MOMENT markers. Then the timing line ("⏱ Timing: largest difference 4 ms after
  correction ✓ · details" or "not measured this session") which opens the timing
  dialog without pausing.
- Problems appear as banners (a camera stopped sending pictures —
  reconnecting; a headband lost; a file could not be saved).

### 4. New recording — Saved

✓ **Recording saved** (or "(stopped early)", or "Recording saved, but with
problems"), the fighters, date and length, then a list: N camera videos (no
frames lost), N brain recordings (and head movement), N automatic markers, N
moments you marked (with / without notes), **Timing report** or **Timing not
measured**, anything **Worth checking**, the analysis file; "Saved in … · Open
folder". Buttons: **Start labelling →**, Back to sessions, Record another.

### 5. Review & Label

Header: ‹ Sessions · `● K7Q2 vs ● M3XA` with date and length · My labels /
Compare (when someone else has labelled) · "✓ All labels saved" · **Export for
analysis**.

- Compare banner: "You and Jonas R agree on 3 of 4 labels (75%) within half a
  second. Faded chips are Jonas R's."
- The four cameras; beside them a panel with **Label** and **Activity**:
  - Label: "Add a label at 3:27" · "as Maya K · Not you?"; **1 · Who?** A ·
    K7Q2 / B · M3XA / Both; **2 · What happened?** the label list (keys 1–8);
    **Custom note…** (N); the selected label with Delete; "2 marked moments have
    no note yet — click them on the timeline"; "Space play · 1–8 label · N note"
    · Undo.
  - Custom note: who, the text, Cancel / Add note.
  - A moment: "Moment at 3:27", the observer's note (saved as you type), and
    the label list to put a label at that exact moment.
  - Activity: the history.
- The EEG of both boxers (10 s, playhead in the middle, grey automatic
  markers, labels).
- Transport: play, the time, "of 12:15 · Round 2", speeds 0.25–2×. **Lanes:**
  Rounds · System (a grey tick per automatic marker; click shows
  `ROUND_2_START · 05:15.00`) · A · K7Q2 · B · M3XA · Both · Notes · Moments.
- Asked once: "Who is labelling?" — the app's dialog over the dimmed window,
  the moment a recording is opened for labelling with no name set (Start /
  Continue labelling, Add my labels, Open, Saved's "Start labelling →"): one
  sentence on why, a focused name field, Cancel · **Save**, disabled until a
  name is typed; Enter saves. **Cancel goes back to Sessions** — nobody labels
  without a name. Saved: the panel shows "as Maya K · Not you?"; "Not you?"
  opens the same dialog. (If the name is cleared while labelling, the dialog
  comes back at the next label, worded "Save and add the label", and that
  label is then added.) There is no name field in the side panel and no
  "you'll be asked your name" line.

- Leaving with ‹ Sessions after adding labels: "✓ Your labels are saved —
  Every label is saved the moment you add it — 4 new this time." (and how many
  marked moments still have no note) · Keep labelling / Close.

### 6. Export for analysis (the app's dialog)

The zip name; **Always included:** recording.xdf; the same data as separate
CSV files; the timing report (timing_quality.csv); agreement, details, history.
**Videos:** the 4 camera videos (on) · also one combined video (off). A green
note: "Fighters appear only as their codes (K7Q2, M3XA) and colours. No names
are stored, so there is nothing to hide." Save to … Change. Cancel / Export.

### 7. Settings ("Changes save automatically")

- **This computer:** Your name ("Only labellers have names. Fighters are
  always codes."); Recordings folder + Change.
- **Label list — Shared with the team:** chips with ×; + Add (type, Enter).
  "One-off descriptions go in a Custom note while labelling."
- **Fighter colours — Shared with the team:** Red, Blue, Black always there;
  + Add colour (colour + name).
- **Advanced** (folded): Record head movement (on) · Record pulse (off) ·
  Record cameras at 60 fps (on) · Send live signals to other programs (LSL)
  (off — "The app shows everything live without it."); which headband each
  boxer wears by default (neutral grey A / B badges); video quality, whose
  sizes per hour follow the 60 fps switch ("… at 60 fps" / "… at 30 fps").

### 8. Documentation

Search in the screen; the full documentation (getting started, user guide,
research & data, developer guide, decisions, project), technical where it needs
to be. The page uses the whole window width — no empty margins beside it; only
on a very wide window does the text get side padding, to keep lines readable.

## Behaviour that is part of the design

- Recording starts **15 s before the baseline**; the bell rings at the start
  and end of every round; every phase start/end is marked automatically.
- Moments never pause recording; a moment without a note keeps the recording in
  "Needs labels".
- Custom notes never enter the label list and are ignored by agreement.
- Everything saves the moment it changes; failures are shown, never hidden.
- The timing check never pauses recording; the buzzer test is run on Get ready.

## Where the build differs from the prototype

- **Signal** and **Timing** checklist items do not block Start (the brief: the
  signal is a heuristic; timing is recommended). The prototype blocked on the
  signal.
- **Advanced** also holds the headband assignment and the video quality (they
  have no other home); the round plan and bell are remembered from Get ready.
- The prototype's Documentation sample is replaced by all existing pages.
- A small Help (?) button stays on Review & Label.
