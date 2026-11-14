# ADR-0021 — Boxer names are kept, and can be hidden

**What this page covers:** where boxer names are stored, and the two switches
that hide them. It reverses one choice of the previous pass.

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

The previous pass left boxer names out of `recording.xdf` to keep personal data
out of a file that gets passed around. The owner's decision: keep the names in
`recording.xdf` and in exports so the team can keep track, with an option to
hide them.

## Decision

- **Names are kept by default** — in the manifest, in `recording.xdf`
  (`boxer_a_name`, `boxer_b_name`, and `boxer_name` on a boxer's own streams),
  and in exports.
- **Per export: "Replace boxer names with Boxer A / Boxer B".** That export's
  XDF, manifest copy, history copy, README and file name carry no names. A
  Settings switch sets what the export window starts with.
- **On screen: "Hide names".** One click in the sidebar (or a keyboard
  shortcut) shows "Boxer A / Boxer B" in Sessions and Review & Label, for
  presenting or screen-sharing.
- **Neither switch changes the recording on disk**, and the documentation says
  plainly that videos still show the boxers.
- A test builds a name-free export and searches every file in it for the names.

## Consequences

- An analyst opening `recording.xdf` sees who is who without a second file.
- A name-free export is pseudonymised, not anonymous: faces and brain signals
  remain. The [Personal data note](../research-and-data/personal-data.md) says
  so.
- The recording folder's own name still contains the names; handing over data
  without names means handing over a name-free export, not the folder.

## Alternatives considered

- **Hide names by default.** Rejected by the owner: the team needs them day to
  day.
- **Rename the folder / rewrite the recording when hiding.** Rejected: it would
  destroy information and break the rule that a recording's source files are
  never rewritten.
