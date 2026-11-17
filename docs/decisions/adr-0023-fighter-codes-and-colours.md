# ADR-0023 — Fighters are codes and colours, never names

**What this page covers:** how a fighter is identified in a recording, and why
the name-hiding switches are gone. It **supersedes
[ADR-0021](adr-0021-boxer-names-and-hiding.md)**.

- **Status:** Accepted
- **Date:** 2026-11-17

## Context

ADR-0021 kept boxer names in the manifest, `recording.xdf` and exports, with a
switch to hide them per export and on screen. The professor supervising the
study asked for the opposite: names are personal data and must not be stored at
all. The team still needs to tell the two fighters apart on video and to link a
recording to a person through a list kept outside the app.

## Decision

- **Each fighter gets a 4-character code** — capital letters A–Z and digits
  0–9, exactly four (`K7Q2`). The two codes in a recording must differ. "New
  code" makes one up (it avoids 0/O and 1/I so codes read easily; a typed code
  may use any of A–Z and 0–9).
- **Each fighter gets a colour** — what they wear. Red `#D3241B`, Blue
  `#1B45C4` and Black `#111111` are always available; the team can add more in
  Settings. The colour list is **shared**: `fighter_colours.json` in the
  recordings folder, written atomically under the folder lock exactly like
  `label_list.json` (ADR-0020). The two fighters' colours must differ.
- **Codes and colours are stored everywhere a name used to be:** the manifest
  (`boxers.A = {code, colour, colour_hex, headband}`), the folder name
  (`2026-11-17 14-05 K7Q2 vs M3XA`), every `recording.xdf` stream description
  (`boxer_a_code`, `boxer_a_colour`, … and `boxer_code` / `boxer_colour` on a
  fighter's own streams) and the export README.
- **Name hiding is removed:** `data/privacy.py`, the export option "Replace
  boxer names", the Settings switches and the sidebar's Hide names switch.
  There is nothing to hide.
- **Old recordings with names still open.** A manifest with
  `boxers.A.name` is shown with that name (`fighters.display`), listed,
  labelled and exported as before; its `recording.xdf` keeps `boxer_a_name` /
  `boxer_name`. Nothing rewrites an old recording.

## Consequences

- No name of a fighter is ever written by the app. The key that links codes to
  people lives outside the app, as the data-protection plan requires.
- Faces in the videos still identify people; the
  [Personal data note](../research-and-data/personal-data.md) says so.
- Code is the only searchable identifier on Sessions; searching for an old
  recording's name still works.

## Alternatives considered

- **Keep names, hide by default.** Rejected: the name would still be on disk.
- **Free-text pseudonyms.** Rejected: people type real names into free text;
  a fixed 4-character format makes that impossible by construction.
- **Colour only (no code).** Rejected: colours repeat across sessions; the
  code is what links a recording to the external list.
