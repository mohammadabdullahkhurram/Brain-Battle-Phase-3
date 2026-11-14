# Brain Battle — Design spec

**This file is the source of truth for all UI work.** If code and this file
disagree, ask; don't guess.

**History.** First approved 2026-10-27 from clickable mockups. Amended
2026-11-13 for the standard EEG viewer
(`docs/decisions/adr-0016-standard-eeg-viewer.md`). **Rewritten 2026-11-13 for
the November 2026 redesign** — the owner asked for a real redesign and left
everything open except light mode; the reasoning is in
`docs/decisions/adr-0018-november-2026-redesign.md`, and before/after pictures
of every screen are in `docs/reviews/redesign/`. This rewrite awaits the owner's
review like the rest of `develop`.

## Who it is for

- **Operators** — researchers and assistants running a session in a gym or lab,
  often standing, often in a hurry, sometimes looking at the screen from a few
  metres away.
- **Labellers and analysts** — at a desk, for hours, with a keyboard. More than
  one person may label the same recording.

None of them are developers.

## Principles

1. **The three jobs are obvious and short:** start a recording, label a
   recording, export. Each is reachable in one click from where you are.
2. **Plain language only.** Never show: sync, align, offset, xcorr, capture
   clock, POV, DSLR, baseline, monotonic, schema, slug.
   *One deliberate exception:* the brain-activity display is a standard EEG
   viewer and shows the electrode names TP9, AF7, AF8, TP10 and AUX,
   microvolts and a time axis, always with the plain location available beside
   them ("left ear"). Channel codes appear nowhere else.
3. **Light mode only.** No theme toggle, no dark palette. (The area behind
   video is dark because it is picture, not interface.)
4. **Status is never colour alone:** an icon with its own shape, or a glyph
   (✓ ! ✕), plus words.
5. **Errors say what happened and what to do** ("The recordings folder can't be
   reached. Check that the drive is connected, then press Try again.").
6. **Nothing is lost silently.** Autosave everywhere; when something cannot be
   saved the screen says so and retries.
7. **It reads the same on Mac and Windows.** The file manager is "Finder" on a
   Mac and "Explorer" on Windows; shortcuts are written ⌘Z or Ctrl+Z.
8. **Sizes.** Main actions are at least 44 px tall. Text contrast meets WCAG AA
   (4.5:1). Everything fits 1440×900 and 1280×800 with nothing clipped, and
   stays usable at the minimum window, 1180×720.

## Vocabulary (UI word ← internal name)

- Side camera 1 / Side camera 2 ← DSLR / side cams
- Boxer A view / Boxer B view ← POV 01 / POV 02
- Headband ← Muse-S; Brain activity ← EEG (the display's title is "Brain
  activity (EEG)")
- Rest before round 1 ← baseline; Round N, Break ← phase markers
- Recording ← session capture; Recordings folder ← capture dir
- "Weak contact: left ear" ← per-channel signal quality (TP9 = left ear, AF7 =
  left forehead, AF8 = right forehead, TP10 = right ear)
- Your name / labeller ← the person placing labels
- Moment / Lasts a while ← point / duration label
- "the same event" ← two labellers' labels matched within a tolerance
- Show in Finder (Mac) / Show in Explorer (Windows) ← reveal in file manager

## Design tokens

Font: Inter (bundled; fallback -apple-system, Segoe UI). Timers and counts use
equal-width digits so they do not jiggle.

- **Type:** screen title 30 · large 24 · card/dialog title 20 · heading 16 ·
  body 14 · small 13 · extra small 12 · caption 11 (px). Captions above groups
  of controls are small capitals with letter-spacing.
- **Surfaces:** Ground #F3F4F6 · Surface #FFFFFF · Subtle surface #F9FAFB ·
  Border #E4E7EC · Strong border #CDD2DA · Stage (behind video) #0B0F17
- **Text:** Ink #101828 · Secondary #344054 · Muted #5D6B82
- **Accent:** #4F46E5 (hover #4338CA, pressed #3730A3) · Accent soft #EEF2FF
- **Primary dark** (the sidebar's New recording button, the active step):
  Ink #101828 with white text
- **Success** #067647 on #ECFDF3 (text #085D3A) · **Warning** #B54708 on
  #FFFAEB (text #7A2E0E) · **Danger** #B42318 on #FEF3F2 (text #912018) ·
  **Record red** #DC2626
- **Boxer A:** #0E7490 line, #ECFEFF soft, #0B5566 text · **Boxer B:** #C2410C
  line, #FFF4ED soft, #8F300A text. Always paired with the letter A/B.
- **Another labeller's labels:** #667085 grey, drawn hollow — never a boxer or
  label colour.
- **Phases** (soft fill / text): Rest #EAECF0 / #344054 · Round #E0E7FF /
  #3730A3 · Break #FEF0C7 / #93370D
- **Radii:** 8 px small controls, 10 px controls, 14 px cards, 18 px the
  Recording hero. **Spacing scale:** 4 / 8 / 12 / 16 / 20 / 24 / 32.
- **Label palette defaults:** Jab #3D4FD6, Cross #7C3AED, Hook #0F766E,
  Uppercut #B45309, Block #4B5563, Clinch #BE185D (lasts a while), Knockdown
  #B91C1C, Referee stop #15171C (lasts a while).

## Components

One shared set (`brain_battle/ui/widgets.py`); screens do not restyle their own.

- **Buttons:** primary (accent, filled) — one per screen at most, for the main
  action; secondary (white, bordered); ghost (no border until hovered — Back,
  Cancel, icon buttons); danger (record red — Start recording, Stop). Compact
  variants are 34 px for rows and toolbars.
- **Card:** white, 1 px border, 14 px radius. Tinted variants for ok / warning /
  danger / info messages.
- **Banner:** a full-width tinted card: icon, one or two sentences, actions on
  the right.
- **Status line:** icon + words (tick in a circle, warning triangle, cross in a
  circle).
- **Pill:** a small rounded status word ("✓ Labelled", "! Interrupted").
- **Boxer badge / chip:** the round letter, or "A · Sam", in the boxer's colour.
- **Segmented control:** a row of mutually exclusive choices (speed, tabs).
- **Timer label:** hand-painted, equal-width digits, shrinks to fit.

## App structure

**Sidebar (244 px), top to bottom:** the brand · **New recording** (the one
filled, dark button — it is the main thing people come to do) · Sessions ·
Review & Label · (space) · **who is labelling** (a person icon and the name, or
"Set your name"; click to change it) · **Hide names** (an eye; one click shows
"Boxer A / Boxer B" instead of names on screen) · the **recordings-folder
card** (name, free-space bar, "412 GB free · plenty of space"; red with "Can't
be reached — is the drive connected?" when the folder is missing) ·
Documentation · Settings.

On Review & Label the sidebar collapses to a 72 px icon rail. The recording
flow is full-screen with no sidebar: a top bar with **Cancel**, where you are
("1 Get ready — 2 Record — 3 Saved"), and Help.

Every screen has a **?** Help button that opens the documentation at that
screen's page.

## Screens

### 1. Sessions (home; the app opens here)

- Title "Sessions", subtitle "Every recording in your recordings folder. Click
  one to watch it back and label it." Right: Help, **Open folder in Finder /
  Explorer** (secondary), **New recording** (primary).
- **Banners**, when needed, above the controls:
  - folder unreachable (danger): what happened, what to do, **Try again**;
  - interrupted recording (warning): "A recording was interrupted — the
    recording from <date, time> (<A> vs <B>) stopped unexpectedly at <m:ss>.
    Everything recorded up to that point has been kept." **Open it** ·
    **Dismiss** (dismissal is remembered).
- **One row of controls:** status tabs with counts (All, Needs labelling,
  Labelled, Exported, Interrupted) · search ("Search by name or date" — boxer
  or labeller) · sort (Newest first / Oldest first).
- **The list** is one card: a quiet header row, then one row per recording,
  separated by hairlines. The whole row is clickable.
  - *Recorded* — date (bold); under it time · length ("12 min · 3 rounds" or
    "Stopped at 9:41 of 12:00").
  - *Boxers* — "A · name" and "B · name" chips ("No names" if none; "A · Boxer
    A" when names are hidden). A "Demo" pill on generated data.
  - *Labels* — "24 labels" with the labellers' names underneath, or "No labels
    yet".
  - *Status* pill.
  - *Actions* — **Label** (primary) if *you* have not labelled it yet, **Open**
    (secondary) if you have; then **⋯**: Open and label · Export for analysis…
    · Show in Finder / Explorer.
- Status rules: Interrupted = flagged in the manifest; Needs labelling = no
  labels from anyone; Labelled = at least one label from anyone; Exported = an
  export exists.
- Footer: "<n> of <m> sessions · <size> used in <folder name>".
- Empty state: a card with "No recordings yet", "Press New recording to make
  your first one." and the button.

### 2. Record — Get ready (step 1)

Everything before Start on one screen: what you *check* on top, what you *do*
underneath. Devices connect by themselves when the screen opens.

- Title "Get ready"; under it "✓ 6 of 6 devices ready — Everything is
  connected." or "! 5 of 6 devices ready — You can still record, but 1
  headband will not be recorded." Right: **Search again**.
- **Cameras** (left, wider): 2×2 live pictures — Side camera 1, Side camera 2,
  Boxer A view, Boxer B view — each with its name, "✓ Connected · 30 frames per
  second", and a **Change** button. A missing camera has a red border and "Not
  found. Check the USB cable and the video receiver, then press Search again."
- **Headbands** (right): one card per boxer — badge, "Boxer A · Sam ·
  MuseS-7538", "✓ Connected · battery 82%"; the **contact strip** (four equal
  cells in a fixed order: Left ear, Left forehead, Right forehead, Right ear,
  each ✓ or !); a tip when a sensor is weak ("Move the band so the sensor sits
  flat behind the left ear. Brush hair out of the way."); and the live EEG
  display — five traces (TP9, AF7, AF8, TP10, AUX), 10 s across, newest at the
  right, automatic µV scale shown on a scale bar. A lost headband shows
  **Reconnect**.
- **The plan** (one card across the bottom):
  - row 1: A badge + name field, B badge + name field (both optional), "Ring a
    bell when each round starts", and a line "Boxer A wears headband … · Boxer
    B wears …";
  - row 2: four steppers (− value +) — Rounds, Round length, Break between,
    Rest before round 1 (defaults from Settings) · the proportional phase bar
    with a sentence ("1:00 rest, then 3 rounds of 3:00 with 1:00 breaks. Stops
    by itself at 12:00.") · the disk line (enough / low / not enough space /
    folder unreachable / on a network drive) · "Saves to <folder> › <recording
    name>" · **Start recording** (danger, large).
- Start is switched off when there is not enough space or the folder cannot be
  used; the disk line beside it says why.

### 3. Record — Recording (step 2)

Read from across the room; used under pressure.

- **Status strip** (full width, green or amber): red "● REC 05:12" · "Sam vs
  Alex" · "✓ All 4 cameras and 2 headbands are recording" or what is wrong ·
  **Stop recording** (danger).
- Stop asks inline: "Stop recording now? Round 2 is still running. Everything
  recorded so far is kept, but the rest of the session will not happen."
  **Keep recording** · **Stop and save**.
- **The hero card**, tinted by the phase (rest grey, round blue, break amber):
  the phase in words ("Round 2 of 3") · the countdown, as large as the card
  allows · "left in this round" · a progress bar · what comes next ("Next:
  Break, 1:00 · the bell rings when round 3 starts") · **Mark this moment (M)**
  — a very large button · the confirmation line ("✓ Moment marked at 05:12 · 3
  marked so far. You name them later, in Review.") · the whole-session phase
  bar with a red now-line.
- **Side column:** 2×2 small camera pictures ("● Recording", lost frames if
  any); per boxer a headband card with status, the contact strip, and the live
  EEG (the four skin electrodes, 5 s across, session time on the axis, a dashed
  line at each round start and marked moment).

### 4. Record — Saved (step 3)

- A large tick, "Recording saved", "<date, time> · <A> vs <B> · 12 min · 3
  rounds".
- One card, a checklist read from the real files: each camera video (lost
  frames if any), each brain recording, "3 rounds and 2 breaks marked
  automatically", "n moments you marked — name them when you label",
  "Everything on one timeline for analysis (recording.xdf) saved". Then
  **Worth checking** (weak contact for about 2 minutes in round 2; lost
  frames; a camera at an unexpected speed).
- "Saved in <folder> › <recording folder> · 1.7 GB".
- **Label this recording →** (primary) · **Record another** · **Back to
  sessions** · **Show in Finder / Explorer**.

### 5. Review & Label

One screen for watching and labelling. You label **as yourself**; other
labellers' labels can be shown beside yours but never edited.

- **Top bar:** ← Sessions · title "<date, time> · <A> vs <B>" · subtitle "12
  min · 3 rounds · also labelled by Jonas R" · save status ("All labels saved",
  or in red "Not saved — the recordings folder can't be reached…") · Help ·
  **Export for analysis** (primary).
- **Left, scrolling together:** the 2×2 video grid on a dark stage (name chip
  and an enlarge button per picture); the **Brain activity (EEG)** card —
  controls [Waves | Band power] [Timebase 2/5/10/20/30 s] [Scale Auto/10…1000
  µV] [Filter 1–45 Hz] [taller] [Hide]; one block per boxer (badge + name, a
  contact-quality line) with a standard EEG display: stacked traces labelled
  TP9, AF7, AF8, TP10, AUX in the boxer's colour, a µV scale bar, the playhead
  fixed in the centre, event markers as named dashed lines, one shared time
  axis. **Hide** folds the card to its title row; on a small window it gives up
  height so the videos keep the larger share.
- **Left, pinned and always visible — the dock:**
  - transport: back one frame · play/pause · forward one frame · "04:12.40 /
    12:00.00" · the phase ("Round 2 · 0:12 in") · ‹ › previous/next label ·
    speed [0.25× | 0.5× | 1× | 2×];
  - timeline: lane names in a left gutter; a **Phases** row (Rest / Round /
    Break bands, automatic, read-only); your labels in **Boxer A**, **Boxer
    B**, **Both** (a line for a moment, a bar for something that lasted); while
    comparing, one extra grey lane per other labeller; the playhead.
- **Right — the Label panel (348 px):**
  - header "Label" and who is labelling;
  - if no name is set: a card "Who is labelling?" with a name field and **Start
    labelling**; labels cannot be placed until it is filled in;
  - "Who did it?" [A · name] [B · name] [Both];
  - "What happened? · click or press the number": two columns of label
    buttons, each with its colour and key 1–9; a note for labels that last a
    while; an amber note while one is open ("Clinch started at 01:12.40 — press
    6 again when it ends. Esc cancels.");
  - tabs **My labels (n)** | **Compare (n)**, with Undo.
    - *My labels:* marked moments to name first ("Marked moment — click to
      name"), then placed labels newest first: time · who · colour · name · ✕.
      Click a row to jump there. For recordings labelled before names existed:
      a note with **These are mine**.
    - *Compare:* whose labels [Jonas R (12) ▾] · tolerance [within 0.5 s ▾] ·
      the agreement sentence ("You and Jonas R marked the same event 18 times
      out of 24 (75% agreement). 4 only you marked, 2 only Jonas R marked.")
      with one line on what "the same event" means · the list of differences
      ("only you" / "only Jonas R"), each clickable.
  - footer: the keyboard shortcuts — Space, ← → (Shift: one second), ↑ ↓, 1–9,
    Tab, Esc, ⌘Z / Ctrl+Z.

### 6. Export for analysis (a window)

Opened from Review & Label or from a row's ⋯ menu in Sessions.

- "Export for analysis"; a paragraph on what the zip contains, led by
  recording.xdf, and whose labels are included ("Includes 36 labels from Jonas
  R (12), Maya K (24).").
- ☐ **Replace boxer names with Boxer A / Boxer B** — with a line on exactly
  what it does and does not do. Starts as set in Settings.
- ☐ **Also make one combined video of all four cameras (slow)**.
- "Save as <path>" · **Change…**
- **Cancel** · **Export** → a progress bar → "✓ Exported to <path>" with
  **Show in Finder / Explorer** and **Done**.

### 7. Settings (autosaves; applies to the next recording)

A list of the sections on the left; the sections on the right. "✓ Saved"
appears briefly after each change.

- **You** — Your name, with what it is for.
- **Recordings folder** — name, path, free space (and "on a shared (network)
  drive" when it is); **Change folder…**, **Show in Finder / Explorer**; text on
  date-named folders and on sharing the folder between computers; a red line
  when the folder cannot be reached.
- **Headbands** — Boxer A → device, Boxer B → device; **Search for a new
  headband**.
- **Video quality** — Standard 960×540 30 fps (recommended, "about 2.2 GB per
  hour per camera") · High 1280×720 · Light 640×360.
- **Session defaults** — rounds, round length, break, rest before round 1;
  "Ring a bell when each round starts" + **Test sound**.
- **Extra signals and sharing** — "Record head movement" (on), "Record pulse"
  (off; not yet tried with real headbands), "Share live signals with other lab
  software on this network" (on).
- **Boxer names and privacy** — "Replace boxer names with Boxer A / Boxer B in
  exports" (off), "Hide boxer names on screen" (off), each with one line of
  explanation.
- **Label list** (stored in the recordings folder, shared by the team) — key,
  swatch, name, "Moment" / "Lasts a while", ↑ ↓, remove; **+ Add label**.
  Removing a label keeps already-placed ones.
- Footer: "Brain Battle <version> · Constructor University".

### 8. Documentation

The in-app manual: a section tree on the left, an always-visible search, the
page on the right with back/forward. Opened by every Help button at the right
page.

## Behaviour that is part of the design

- **Recording folder naming:** "YYYY-MM-DD HH-MM <A name> vs <B name>" (names
  made safe for Mac and Windows file systems); recordings are found by their
  manifest, not by folder name.
- **Who is labelling:** a name, asked once, no accounts. Each labeller's labels
  are stored separately inside the recording.
- **Agreement:** the same label, the same boxer, start times within the
  tolerance (overlap counts for lasting labels); each label matched once.
- **Boxer names:** kept by default; hidden per export or on screen; neither
  changes the recording on disk; videos still show the boxers.
- **A folder that goes away:** every screen that needs it says so and what to
  do; labels not yet saved are kept and saved when it returns.
- **Moments marked during recording** appear as unnamed labels for every
  labeller to name.
- **Alignment is automatic and invisible** — there is nothing to click to
  "sync".
