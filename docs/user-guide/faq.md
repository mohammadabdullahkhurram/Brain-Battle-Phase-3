# FAQ

**What this page covers:** quick answers to questions that come up but
aren't really "something's broken" — see
[Troubleshooting](troubleshooting.md) for those.

**Does Brain Battle need an internet connection?**
No. Everything runs locally — recording, review, labelling, and export.
Headbands connect over Bluetooth, not WiFi.

**Where is my data stored — some cloud service?**
No. Everything saves to your recordings folder, on your own computer (or a
shared drive you've pointed it at) — see [Settings](settings.md). Nothing
leaves that folder unless you export it or copy it yourself.

**Can I rename a boxer after I've started recording?**
Not from the app. Boxer names are set on the Set up session step, before
you press Start recording.

**What happens if I close my laptop's lid while recording?**
Brain Battle asks the operating system not to let the machine sleep while a
recording is in progress, specifically to avoid this. Still, it's best
practice to keep the laptop awake and plugged in for the whole session.

**Can two people label the same recording at the same time?**
Not safely yet — labelling one recording from two copies of the app at once
isn't supported. Label one recording from one place at a time.

**Can I undo a label after placing it?**
Yes — **Undo** (or ⌘Z) in [Review & Label](review-and-label.md) reverses
your last label action. Labels autosave as you go, so your work is safe
even if you close and reopen the recording.

**Can I use more than 2 boxers or more than 4 cameras?**
No — the app is built around exactly two boxers (A and B) and four cameras
(two side cameras, one POV camera per boxer).

**Does it run on Windows as well as macOS?**
Yes — see [Installation](../getting-started/installation.md) for
platform-specific notes. macOS is the primary development platform.

**Is the recorded brain activity something I can trust for analysis yet?**
Be cautious with early recordings. As of this writing, nobody on the
project has yet reviewed a brain-activity recording taken from a real
sparring session with real headbands — development and testing have used a
built-in example recording instead (see the "Known limitations" note at the
bottom of this page). Check with your team before treating early brain-
activity data as research-ready.

**Can I change which camera is "Side camera 1" versus "Side camera 2"?**
The four camera roles are fixed, but which physical device fills each role
is not — use the **Change** button on that tile in the Connect devices step
to pick a different camera for that role.

## Known limitations

- Hardware verification is still pending for this build — see the note
  above about brain-activity data.
- Shared-folder labelling is single-writer only (see "two people labelling"
  above).
- There's no built-in way to merge two separate recordings of the same
  session.

For the full, currently-maintained list, see `CLAUDE.md`'s "Deferred work"
section and `docs/ROADMAP.md` in the repository — this page summarizes the
parts relevant to day-to-day use, not every open engineering item.
