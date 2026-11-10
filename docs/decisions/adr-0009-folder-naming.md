# ADR-0009 — Date + names folder naming, manifest-based discovery

**What this page covers:** why a recording's folder is named by date and boxer
names, and why sessions are discovered by their manifest rather than a folder-name
prefix.

- **Status:** Accepted
- **Date:** 2026-10-29

## Context

Sessions used to be named by a fixed prefix (`Brain Battle <timestamp>`) and
discovered by matching that prefix. The DESIGN screens show the **boxers' names**
everywhere — in the Sessions list, every screen title, and the folder name — so
names had to be captured and stored. Changing the folder name to include names
would break any discovery that keys off the old prefix.

## Options considered

1. **Keep the prefix, store names only in the manifest** — discovery stays simple,
   but the folder name doesn't show who's in it (worse for someone browsing the
   folder in Finder).
2. **Name the folder `YYYY-MM-DD HH-MM <A> vs <B>` and discover by manifest** — the
   folder name is human-meaningful; discovery no longer depends on a brittle
   prefix.

## Decision

**Name each folder `YYYY-MM-DD HH-MM <A name> vs <B name>`** (filesystem-sanitized),
with boxer names stored in the manifest, and **discover sessions by their
manifest, not by a folder-name prefix** (`session_storage._session_folder_name`,
`session_info`, `list_sessions`, `scan_interrupted_sessions`). The interrupted-
session scanner was updated to drop the prefix check.

## Consequences

- **The folder name is self-describing** — a person browsing the recordings folder
  sees the date and the boxers without opening anything.
- **Discovery is robust to the naming scheme changing** — it reads manifests, so a
  future rename of the folder convention won't break the Sessions list.
- **Names must be sanitized** for the filesystem; a no-name recording is still
  handled (the Sessions screen has a "no names" badge).
- Each recording folder stays **fully self-contained** (video + sidecars + EEG +
  markers + manifest + labels + history), which the Phase 7 upload/sync plans
  depend on.

## Links

- Commit `9834a1e` (recording metadata + boxer-named folders + manifest-based
  discovery).
- [Recording folder anatomy](../research-and-data/recording-folder.md),
  [Sessions user guide](../user-guide/sessions.md).
