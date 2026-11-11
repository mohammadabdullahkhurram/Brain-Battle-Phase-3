# Settings

**What this page covers:** every section of the Settings screen and what
each one controls. Everything here autosaves immediately — there's no Save
button — and applies to your **next** recording, not one already in
progress.

![The Settings screen.](../images/settings.png)

## Recordings folder

Shows the folder's name, full path, and free space. By default this is a
**Brain Battle Data** folder inside your **Documents** folder. **Change
folder…** picks an existing folder or lets you create a new one; **Show in
Finder** opens it. Recordings already in the old folder are **not** moved — you're
only changing where new ones go. If a folder is shared (for example on a
shared drive), everyone pointed at it sees the same sessions and labels.

If the `BRAIN_BATTLE_CAPTURE_DIR` environment variable is set, the folder
path is shown with a note that it's set outside the app, and **Change
folder…** is turned off — an administrator-level override always wins over
a folder picked here.

## Headbands

A dropdown per boxer (**Boxer A**, **Boxer B**) listing every headband
Brain Battle has seen before, plus **Search for a new headband** to scan
for one nearby. The same headband can't be assigned to both boxers — trying
to shows an inline message and the change is rejected.

## Video quality

Three options, each showing its resolution/frame rate and an "about X GB
per hour per camera" estimate computed from its actual recording bitrate
(not a fixed number — it updates if the quality level's bitrate ever
changes):

| Option | Resolution | Notes |
|---|---|---|
| **Standard** | 960×540, 30 fps | Recommended; selected by default |
| **High** | 1280×720, 30 fps | Larger files, sharper video |
| **Light** | 640×360, 30 fps | Smallest files |

## Session defaults

Four steppers — **Rounds**, **Round length**, **Break between rounds**,
**Rest before round 1** — plus **"Ring a bell when each round starts"** and
a **Test sound** button that plays the actual bell: a real boxing-bell
recording (one clean strike with its natural ring, about two seconds), not a
synthesized tone. When it's on, it rings once at the start of the rest
before round 1 and once at the start of each round — never during a break.
These pre-fill the Set up step of every [new recording](new-recording.md);
each recording can still adjust them for that session only.

## Label list

Your team's shared set of labels — stored in the recordings folder itself,
so everyone pointed at that folder sees the same list. Each row: a number
key (1–9, set by its position in the list), a colour swatch (click to
change it), an editable name, whether it's a **Moment** or **Lasts a
while**, and reorder (↑/↓) and remove buttons. **+ Add label** appends a new
one with an unused colour from the palette, ready to rename.

Removing a label from this list does **not** touch labels already placed
with it in any recording — those keep their own name and colour.

## Footer

"Brain Battle \<version\> · Constructor University."
