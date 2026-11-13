# Review & Label

**What this page covers:** playing a recording back, what the video grid and
the brain-activity (EEG) display show and how to read them, and how to place,
undo, and manage labels.

Open a recording from [Sessions](sessions.md) (or **Start labelling →** from
the end of a [new recording](new-recording.md)) to get here. The top bar
shows **← Sessions**, the recording's date/time and boxers, a subtitle with
the length and round count, an autosave status ("✓ All labels saved"), and
**Export for analysis** (see [Export](export.md)).

![The Review & Label screen.](../images/review-and-label.png)

## Video and brain activity

- **Video grid** — all four cameras in a 2×2 grid, each tile named and with
  an enlarge button.
- **Brain activity (EEG)** — both boxers' brain recordings, drawn the way
  standard EEG software draws them. See
  [Brain activity (EEG)](#brain-activity-eeg) below.
- **Timeline** — a read-only Phases row (rest/round/break, automatic — you
  never set these by hand), then one lane each for Boxer A, Boxer B, and
  Both, showing point ticks and duration bars for placed labels, then the
  scrubber itself.
- **Transport** — step back one frame, play/pause, step forward one frame,
  the current time ("04:12.40 / 12:00.00"), the current phase ("Round 2 ·
  0:12 in"), and a speed control (0.25× / 0.5× / 1×).

## Brain activity (EEG)

One block per boxer (badge, name, and a plain-language contact-quality line such
as "Signal good" or "Weak contact: right ear"), each showing that boxer's
headband signals as a conventional EEG display:

- **One line per sensor**, stacked, named with the standard electrode names:
  **TP9** (left ear), **AF7** (left forehead), **AF8** (right forehead),
  **TP10** (right ear) and **AUX** (the headband's spare input — normally a
  flat, meaningless line). Hover over the display for this list.
- **Height is voltage, in microvolts (µV).** The small bar at the top right
  shows how tall the current scale value is (for example "100 µV").
- **Left to right is time.** The time axis along the bottom is shared by both
  boxers and by the video: the **playhead is the fixed line in the centre**,
  and the signal slides underneath it as the recording plays.
- **Events are vertical dashed lines** with their names along the top: the
  start of each round and break, any moments you marked, and every label you
  have placed.

The controls in the card's top row:

| Control | What it does |
|---|---|
| **Waves / Band power** | Switch between the traces and five bars showing how the signal's strength is spread over the classic frequency bands (delta to gamma) |
| **Timebase** (`10 s`) | How many seconds fit across the display — 2, 5, 10, 20 or 30 |
| **Scale** (`100 µV`) | How many microvolts the scale bar stands for. Choose a larger number if the traces overlap, a smaller one if they look flat, or **Auto scale** |
| **Filter 1–45 Hz** | Tidies the picture by removing slow drift and mains hum. It only affects what you see — the recording itself is never changed. Switch it off to see the raw signal |
| **⤢** | Makes the display taller (and back) |

*Not yet verified on hardware:* no brain recording from a real sparring
session has been reviewed here yet — see the [FAQ](faq.md) on trusting early
brain-activity data. Large, slow swings during a round are most likely
movement, not brain activity.

## Placing a label

The right-hand **Labels** panel is two steps:

1. **"1 · Who did it?"** — choose the boxer. The buttons read **"A · \<Boxer
   A name\>"**, **"B · \<Boxer B name\>"**, and **Both** (they fall back to
   **Boxer A** / **Boxer B** when no name was set).
2. **"2 · What happened?"** — a two-column grid of your team's labels (see
   [Settings](settings.md) → Label list), each with a colour swatch and a
   number key **1–9** (the list can hold more than nine labels; only the
   first nine get a number-key shortcut). Click a label, or press its number,
   and it is placed at the current moment.

Most labels are instant points. A few ("lasts a while" labels — Clinch and
Referee stop by default) are durations: "press the number when it starts,
again when it ends (Esc cancels)."

**Placed labels** lists everything placed so far, newest first — time, who,
label, and a delete (✕) button per row — plus an **Undo** button below it
for the last action. **Click any row to jump the playhead to that label's
moment.**

## Moments marked during recording

If you pressed **M** / **Mark this moment** while recording, each one shows
up at the top of the Placed labels list as a highlighted **"Marked moment —
click to name"** row. Click it to jump there and give it a who/what the same
way as any other label.

## Keyboard shortcuts

| Key | Action |
|---|---|
| Space | Play / pause |
| ← → | Step back / forward one frame |
| 1–9 | Place a label (starts/ends a duration label if it's that kind) |
| Tab | Switch boxer (A / B / Both) |
| ⌘Z (Ctrl+Z on Windows) | Undo |
| ⇧⌘Z (Ctrl+Shift+Z on Windows) | Redo |
| Esc | Cancel an in-progress duration label |

## When you're done

Press **Export for analysis** to produce the full bundle for an analyst —
see [Export for analysis](export.md).
