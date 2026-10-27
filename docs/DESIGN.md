# Brain Battle — Design spec (approved 2026-10-27)

Source: clickable mockups approved by the owner. This file is the source of truth for all UI work. If code and this file disagree, ask; don't guess.

## Principles
- Users are non-technical researchers. Plain language only. Never show: sync, align, offset, xcorr, capture clock, POV, DSLR, EEG channel codes (TP9 etc.), baseline, monotonic.
- Light mode only. No theme toggle, no dark palette.
- Every status uses icon + text, never colour alone. Minimum click target 44 px. Text contrast WCAG AA (4.5:1).
- Fits a 13–14" MacBook (1440×900 points) with nothing clipped; usable down to 1280×800.
- Errors say what happened and what to do ("Not found. Check the USB cable and the video receiver, then press Search again.").

## Vocabulary (UI word ← internal name)
- Side camera 1 / Side camera 2 ← DSLR/Side cams
- Boxer A view / Boxer B view ← POV 01 / POV 02
- Headband ← Muse-S; Brain activity ← EEG
- Rest before round 1 ← baseline
- Round N, Break ← phase markers
- Recording ← session capture; Recordings folder ← capture dir
- "Weak contact: left ear" etc. ← per-channel signal quality (TP9 = left ear, AF7 = left forehead, AF8 = right forehead, TP10 = right ear)

## Design tokens
Font: Inter (fallback -apple-system, Segoe UI). Numbers in timers use tabular figures.
- Ground #F6F7F9 · Surface #FFFFFF · Border #DFE2E8 · Strong border #C9CED7
- Ink #15171C · Muted text #555D6B · Secondary ink #3A404B
- Accent #3D4FD6 (hover/pressed #2B3AA8) · Accent soft #EEF0FD
- Success #15803D on #E8F5EC (text #11632F) · Warning #B45309 on #FDF3E7 (text #6B3205) · Danger #B91C1C on #FDECEC (text #991B1B) · Record red #DC2626
- Boxer A: #0E7490 line, #E6F4F7 soft, #0B5566 text · Boxer B: #C2410C line, #FDEFE7 soft, #8F300A text. Always paired with the letter A/B and the name.
- Phases: Rest #E3E7ED/#3A404B · Round #DDE1FB/#2B3AA8 · Break #F6E3C8/#6B3205
- Radii: 10 px controls, 12–14 px cards. Spacing scale 4/8/12/16/20/24/32.
- Label palette defaults: Jab #3D4FD6, Cross #7C3AED, Hook #0F766E, Uppercut #B45309, Block #4B5563, Clinch #BE185D (lasts a while), Knockdown #B91C1C, Referee stop #15171C (lasts a while).

## App structure
Left sidebar (232 px): logo, Sessions, New recording, Review & Label, then at the bottom a recordings-folder card (name + free-space bar + "412 GB free") and Settings. On Review & Label the sidebar collapses to a 72 px icon rail. The recording flow (steps 1–3 + Saved) is full-screen with no sidebar: a top bar with Cancel and a 3-step indicator (Connect devices → Set up session → Record).

## Screens

### 1. Sessions (home, app opens here)
- Title "Sessions", subtitle "Every recording in your recordings folder. Open one to watch it back and add labels."
- Buttons: "Show folder in Finder" (secondary), "New recording" (primary).
- Interrupted-recording banner (replaces the Phase 2 popup): warning style, "A recording was interrupted — The recording from <date, time> (<A name> vs <B name>) stopped unexpectedly at <m:ss>. Everything recorded up to that point has been kept." [Open it] [Dismiss]. Dismiss marks it so it doesn't come back.
- Search ("Search by boxer name or date"), status filter chips with counts (All, Needs labelling, Labelled, Exported, Interrupted), sort (Newest first, Oldest first).
- Table columns: Recorded (date bold, time below) · Boxers (A name badge, B name badge) · Length ("12 min · 3 rounds" or "Stopped at 9:41 of 12:00") · Labels (count) · Status badge · actions (folder icon "Show in Finder", Open/Label button).
- Status rules: Interrupted = manifest interrupted; Needs labelling = 0 labels; Labelled = ≥1 label; Exported = an export exists.
- Footer: "<n> of <m> sessions · <size> used in <folder name>".
- Empty state: "No recordings yet. Press New recording to make your first one."

### 2. Record — Connect devices (step 1)
- "Connect the cameras and headbands" / "Plug in the cameras and switch both headbands on. When everything shows a green tick, you are ready."
- Cameras card: 2×2 tiles (Side camera 1, Side camera 2, Boxer A view, Boxer B view), each with live preview, ✓/! icon, status text ("Connected · 30 frames per second" or the not-found message), and a Change button (pick a different device). A missing camera tile gets a red border.
- Headbands card: one block per boxer: "Boxer A · Muse-S 7538", "Connected · battery 82%", then four sensor chips (Left ear, Left forehead, Right forehead, Right ear) each "Good contact" or "Weak contact". A weak sensor shows a tip: "Move the band so the sensor sits flat against the skin behind the left ear. Brush hair out of the way."
- Bottom bar: summary "5 of 6 devices ready" + explanation; [Search again] [Continue →]. Continue is allowed with a missing device but the summary says it won't be recorded.

### 3. Record — Set up session (step 2)
- Date/time row: "Mon 27 Oct 2026, 14:05 · the recording is named by its date and time".
- Boxer A name, Boxer B name (text fields, A/B badges). Note: "Boxer A wears headband Muse-S 7538 · Boxer B wears Muse-S 7564." (from Settings mapping).
- Steppers (− value +): Rounds, Round length, Break between rounds, Rest before round 1. Defaults from Settings.
- "Ring a bell when each round starts" checkbox.
- Right card "What will happen": proportional phase bar, list of phases with start times, "Recording stops by itself at m:00", total length, disk check ("Enough space — This session needs about 1.7 GB. You have 412 GB free." / warning / block, from Phase 2), and "Saves to <folder> › 2026-10-27 14-05 Sam vs Alex".
- Footer: [← Back] "Recording starts with the rest period." [● Start recording] (red primary).

### 4. Record — Recording (step 3)
- Top bar: red pill "● Recording 05:12", "Sam vs Alex", date; [Mark this moment (M)] [■ Stop recording].
- Stop asks inline: "Stop recording now? Round 2 is still running. Everything recorded so far is kept, but the rest of the session will not happen." [Keep recording] [Stop and save].
- Main card: phase chip, "Round 2 of 3" (44 px), countdown "1:48" (~128 px) "left in this round", progress bar, "Next: Break, 1 min · the bell rings when round 3 starts", whole-session phase bar with a now-marker.
- Mark this moment: M key or button; shows "Moment marked at 05:12 · 3 marked so far. You can name them later in Review."
- Side: health line ("✓ All 4 cameras and 2 headbands are recording" or what's wrong), 2×2 small camera previews with ✓ OK / problem text (dropped frames, encoder died), per-boxer headband row with live trace + contact quality.

### 5. Record — Saved
- ✓ "Recording saved", "<date, time> · <A> vs <B> · 12 min · 3 rounds".
- Checklist: 4 camera videos (lost frames if any), 2 brain recordings, rounds and breaks marked automatically, n moments you marked, and "Worth checking" warnings (e.g. weak contact for ~2 min in round 2).
- "Saved in <folder> › <recording folder> · 1.7 GB" [Show in Finder]. Buttons: [Start labelling →] [Back to sessions] [Record another].

### 6. Review & Label (one merged screen; replaces both old Review and Labelling tabs)
- Top bar: ← Sessions · title "<date, time> · <A> vs <B>" · subtitle "12 min · 3 rounds · Boxer A <name> · Boxer B <name>" · autosave status "✓ All labels saved" · [Export for analysis].
- Left: 2×2 video grid with camera name chips and an enlarge button per tile. Brain activity card: [Waves | Band power] toggle, one row per boxer (badge + name, contact-quality line, trace with the playhead fixed in the centre). Timeline card: Phases row (Rest/Round/Break bands, automatic and read-only), lanes "Boxer A", "Boxer B", "Both" with point ticks and duration bars, then a scrubber. Transport: back-one-frame, play/pause, forward-one-frame, "04:12.40 / 12:00.00", current-phase chip ("Round 2 · 0:12 in"), Speed [0.25× | 0.5× | 1×].
- Right panel "Labels": "1 · Who did it?" [A · name] [B · name] [Both]; "2 · What happened?" 2-column label buttons with colour swatch and key 1–8 ("Click or press the number. It is placed at the current moment."); clinch note ("press 6 when it starts and again when it ends"); "Placed labels (n)" list, newest first, rows: time · who · label · delete, plus [Undo]. Footer: "Space play / pause · ← → one frame · 1–8 place a label · Tab switch boxer · ⌘Z undo".
- Moments marked during recording appear as unnamed labels to be named here.

### 7. Settings (autosaves; applies to next recording)
- Recordings folder: name, path, free space; [Change folder…] [Show in Finder]. Text explains date-named folders and that a shared folder shares sessions and labels.
- Headbands: Boxer A → device dropdown, Boxer B → device dropdown; [Search for a new headband].
- Video quality: Standard 960×540 30 fps (recommended, "about 2.2 GB per hour per camera") · High 1280×720 30 fps · Light 640×360.
- Session defaults: rounds, round length, break, rest before round 1; "Ring a bell when each round starts" + [Test sound].
- Label list (stored in the recordings folder, shared by the team): key, swatch, name, "Moment"/"Lasts a while", remove; [+ Add label]. Removing a label keeps already-placed ones.
- Footer: "Brain Battle <version> · Constructor University".

## New scope (add to ROADMAP Phase 3/4)
- Recording folder naming: "YYYY-MM-DD HH-MM <A name> vs <B name>" (sanitize names for the filesystem; sessions are discovered by manifest, not folder prefix). Boxer names stored in the manifest.
- Headband battery + per-sensor contact quality on the connect and recording screens (battery from device telemetry if available; contact quality computed from signal).
- "Mark this moment" during recording → stored in the session → shown as unnamed labels in Review & Label.
- Labels have who = A | B | Both; point and duration ("lasts a while") types.
- Sessions status model (Interrupted / Needs labelling / Labelled / Exported).
