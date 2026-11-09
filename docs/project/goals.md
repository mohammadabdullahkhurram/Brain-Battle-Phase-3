# Goals & research context

**What this page covers:** what Brain Battle is for, the research question behind
it, and who is building it. Read this to understand *why* the app exists before
reading what it does.

## The research

Brain Battle is a **Community Impact Project at Constructor University Bremen**.

- **Author:** Mohammad Abdullah Khurram
- **Supervisor:** Prof. Jakob Fruchtmann
- **Institution:** Constructor University Bremen

The research question it serves is **between-brain neural synchrony between two
boxers during sparring** — whether, and when, two people's brain activity becomes
coupled during direct physical interaction. Answering that needs a recording of a
sparring session where both boxers' brain activity and the physical action are
captured **on one shared timeline**, so a researcher can line up a punch, a block,
or a clinch with what both brains were doing at that instant.

## What the app does, in one paragraph

Brain Battle records a sparring session from four USB cameras (two angles per
boxer) and two Muse-S EEG headbands (one per boxer, over Bluetooth) at the same
time, stamping every camera frame and EEG sample from one software clock so
everything lands on one `t_session_seconds` axis. A researcher then opens the
recording in one merged **Review & Label** screen, watches the video with both
boxers' brain-activity traces alongside it, and marks events (who threw what, and
when). Finally they export an **analysis bundle** — the labels, both EEG streams
co-registered onto one time grid, the raw media, and the round/break timing — for
analysis in Python or R. The labels plus a separate questionnaire feed the
synchrony analysis.

## Why it's built the way it is

Three facts drive the whole design:

1. **The hardware has no shared clock** — four independent USB cameras, two
   independent Bluetooth headbands. So "record everything together" means
   stamping every sample from one software clock at acquisition and aligning by
   those timestamps, not a single hardware capture
   ([ADR-0001](../decisions/adr-0001-capture-time-clock.md)).
2. **The audience is non-technical** — researchers and research assistants, not
   developers. The UI is plain language, light mode, and guided; the
   implementation terms (sync, alignment, capture clock) stay out of sight
   ([design system](../developer-guide/design-system.md)).
3. **The synchrony analysis method is not yet chosen**, so the app targets
   tens-of-ms alignment (which power/envelope measures tolerate) and deliberately
   does **not** chase the sub-ms precision that only phase-based measures would
   need — that decision belongs to whoever owns the analysis
   ([timing & accuracy](../research-and-data/timing-and-accuracy.md)).

## Honesty about what's verified

The project is **built and tested against a generated demo session and simulated
devices**, because the hardware is currently broken and nobody has inspected real
Muse-S EEG from a sparring round. The arithmetic and the flows are unit-tested;
the real-signal and eyes-on-glass questions are **Not yet verified on hardware**
and are tracked honestly — see [known limitations](../research-and-data/limitations.md)
and [current status](status.md).

## Where to go next

- What's done and what's next: [Current status](status.md) and [Roadmap](roadmap.md).
- How the product got here: [Version history](version-history.md) and
  [Project history](history.md).
- The vocabulary used across these docs and the app: [Glossary](glossary.md).
- The design/architecture source documents: [Planning documents](planning-documents.md).
