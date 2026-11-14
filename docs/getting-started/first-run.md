# First run

**What this page covers:** what you'll see the first time you open Brain
Battle, where your recordings are saved, and what to check in Settings
before your first real session.

## The empty Sessions screen

The app opens on **Sessions**, titled "Sessions" with the subtitle "Every
recording in your recordings folder. Click one to watch it back and label
it." On a fresh install this is empty:

> No recordings yet. Press New recording to make your first one.

That's expected — there's nothing to show until you record something. The
**New recording** button is there from the start — at the top of the screen
and, on every screen, at the top of the sidebar.

## Tell the app who you are

Near the bottom of the sidebar is **Set your name**. Click it and type your
name. Labels you place are saved under it, separately from anyone else's — see
[Review & Label](../user-guide/review-and-label.md#who-is-labelling). If you
skip this, the app asks the first time you label.

## Where recordings are saved

Brain Battle writes every recording into one **recordings folder** (its
exact name and location are shown in Settings, and at the bottom of the
sidebar on every screen, with a free-space bar). By default that's a folder
named **Brain Battle Data** inside your **Documents** folder.

Each recording gets its own dated subfolder, named
`YYYY-MM-DD HH-MM <Boxer A name> vs <Boxer B name>`, so recordings sort
naturally by date and you can tell sessions apart at a glance in a file
browser too — not just inside the app.

You can point the app at a different folder (for example a shared drive, so
a whole team sees the same recordings and labels) from
[Settings](../user-guide/settings.md) → **Recordings folder** → **Change
folder…**.

## What to check in Settings before your first real session

Open **Settings** from the bottom of the sidebar and look at, in order:

1. **Recordings folder** — is it pointing where you expect? If you're
   sharing a folder with a team, set it here first, before recording
   anything.
2. **Headbands** — Boxer A and Boxer B each need a headband assigned. The
   app ships with two placeholder device names; once you've switched a
   real Muse-S on nearby, press **Search for a new headband** and pick the
   real ones.
3. **Video quality** — Standard (960×540) is recommended and is selected by
   default; change it here if you need higher resolution or want to save
   disk space, and the "about X GB per hour per camera" figure next to each
   option updates to match.
4. **Session defaults** — rounds, round length, break length, and rest
   before round 1. These pre-fill the plan on the **Get ready** screen of
   every new recording, so set them once here instead of re-typing them
   every time.
5. **Boxer names and privacy** — whether exports replace the boxers' names
   with "Boxer A" and "Boxer B" by default.

Everything in Settings saves automatically as you change it — there's no
Save button, just a brief "✓ Saved" confirmation.

## Trying it without cameras or headbands

You can look around with a made-up recording. In Terminal, from the project
folder:

```bash
python3 tools/generate_demo_session.py --library \
    --out "/path/to/an/empty/folder"
```

Then choose that folder in **Settings → Recordings folder**. The recordings it
makes are marked "Demo" so they are never mistaken for real ones.

## Next

Continue to the [Quick tour](quick-tour.md) for a one-screenshot-per-screen
overview, or jump straight into the [User guide](../user-guide/sessions.md)
to start recording.
