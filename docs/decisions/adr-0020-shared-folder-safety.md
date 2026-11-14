# ADR-0020 — A recordings folder shared between computers

**What this page covers:** how the app stays safe when several computers use
the same recordings folder on a shared or network drive, and what that means
for a future server.

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

The team will keep recordings on a shared drive now, and plans a server later.
Several computers — Macs and Windows PCs — may have the same folder, and the
same recording, open at once. A network drive also behaves differently from a
local disk: it can be slow, it can refuse to replace a file another program has
open, and it can disappear.

## Decision

**No file in the recordings folder is ever written by two people for different
purposes, and no file is ever seen half-written.**

| File | How it is kept safe |
|---|---|
| A labeller's labels | Their own file ([ADR-0019](adr-0019-one-labels-file-per-labeller.md)); written atomically; merged on save |
| `history.jsonl` | Only ever appended to, one whole line per write |
| `session_manifest.json` | Written atomically; later edits (dismiss, interrupted) go through a lock |
| `label_list.json` (the team's label list) | Written atomically under a lock |
| `recording.xdf` | Derived; built under a temporary name unique to the writer, then swapped in |
| Videos, EEG, markers | Written once, during recording, by the one computer that records |

The pieces (`data/safe_io.py`):

- **Atomic writes** — write to a temporary file whose name no other computer can
  pick, flush it, then swap it into place. The swap is retried briefly because
  Windows and network drives refuse it while another program has the file open.
- **A lock file** for the few read-change-write edits. It is advisory: a lock
  left by a crashed computer is taken over after 20 seconds, and if a lock
  cannot be taken at all the edit still happens — a rare lost edit to a small
  settings file is better than an app that hangs.
- **Never create a recording folder by accident.** Saving labels or history
  into a recording whose folder has gone must fail, not quietly create an empty
  folder with the same name.
- **Say when the folder is gone.** Sessions, the sidebar, Get ready and Review &
  Label each say that the folder cannot be reached and what to do. Unsaved
  labels are kept in memory and saved when the folder is back.
- **Scan only what changed.** Sessions re-reads a recording only when one of its
  small files changed, so the list stays quick over a slow network.

**Recording straight onto a network drive is allowed but warned about.** Four
video streams depend on the connection for the whole session; the safer
practice is to record to the computer and copy the finished folder.

## Consequences

- Labelling and exporting from several computers at once is safe.
- Each recording folder is self-contained and changes in small, whole-file
  steps — which is what a sync tool or a future server needs. The server notes
  are in `docs/ROADMAP.md` Phase 7.
- Other labellers' new labels appear within a few seconds (polling), not
  instantly.
- Not verified on a real network drive or a real Windows machine: the tests use
  several processes on one local disk.

## Alternatives considered

- **Operating-system file locks** (`flock`, `LockFileEx`). Rejected: they behave
  differently on SMB, NFS and between macOS and Windows, and fail silently.
- **A small server now.** Rejected by the owner for this pass: no accounts or
  cloud services yet.
