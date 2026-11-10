"""
render_diagrams.py — render every docs/diagrams/*.mmd (Mermaid source) into
two committed artefacts next to it:

  <name>.svg   — full-fidelity Mermaid output, for GitHub (which runs its own
                 real browser-grade SVG+CSS renderer) and as the zoomable
                 source of record.
  <name>.png   — a raster of the SAME render, for Markdown to actually
                 reference (see below for why).

Both come from `npx @mermaid-js/mermaid-cli` (no global npm install — npx
fetches/caches the package into its own cache on first use), which renders
through a real headless Chromium via puppeteer — so both outputs are
pixel-faithful to what Mermaid intends.

Why PNG, not SVG, in the Markdown
──────────────────────────────────
Qt's QTextBrowser/QTextDocument (used by docs_tab.py, no QtWebEngine) loads
images through QtSvg's QSvgRenderer, which only implements a small, old
subset of SVG+CSS. Mermaid's SVG output relies on two things that subset
doesn't support: (1) element styling from an embedded <style> block with
class selectors (Qt only honours inline `style=`/presentation attributes, so
every shape renders with default black fill), and (2) node labels inside
<foreignObject><span>...</span></foreignObject> (Qt's SVG engine doesn't
implement foreignObject at all, so labels render as empty boxes). Verified
empirically against this project's actual diagrams before choosing this
approach — see docs/contributing/docs-guide.md. A rasterized PNG from the
real Chromium render sidesteps both: GitHub still gets the full-fidelity
.svg (it has a real browser), the app gets a PNG it can display correctly
with zero special-casing (`QImage`/`QPixmap` already load PNG natively).

Freshness
─────────
Each diagram gets a `<name>.hash` sidecar (sha256 of the .mmd source) next to
its outputs. `tests/test_docs.py` recomputes the hash of each `.mmd` and
compares — if a `.mmd` changed without this script re-running, the hash
sidecar is stale and the test fails.

    python3 tools/render_diagrams.py              # render only stale/missing
    python3 tools/render_diagrams.py --force       # re-render everything
    python3 tools/render_diagrams.py --check       # exit 1 if anything is stale, renders nothing

Requires Node.js + npx on PATH. If Node isn't available, this prints what it
would have done and exits nonzero — it does not silently skip (a missing
diagram render should be noticed, not swallowed).
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIAGRAMS_DIR = ROOT / "docs" / "diagrams"

# Pinned for reproducible renders (same diagram source -> same output across
# machines/time), not left floating on @latest.
MERMAID_CLI_PKG = "@mermaid-js/mermaid-cli@12.2.0"

# Raster scale factor for the PNG (puppeteer device-pixel-ratio) — 2x gives a
# sharp result at the ~700-900px column width docs_tab.py displays images at.
PNG_SCALE = "2"


def source_hash(mmd_path: Path) -> str:
    return hashlib.sha256(mmd_path.read_bytes()).hexdigest()


def hash_sidecar(mmd_path: Path) -> Path:
    return mmd_path.with_suffix(".hash")


def is_stale(mmd_path: Path) -> bool:
    sidecar = hash_sidecar(mmd_path)
    svg_path = mmd_path.with_suffix(".svg")
    png_path = mmd_path.with_suffix(".png")
    if not sidecar.is_file() or not svg_path.is_file() or not png_path.is_file():
        return True
    return sidecar.read_text(encoding="utf-8").strip() != source_hash(mmd_path)


def render_one(mmd_path: Path) -> None:
    svg_path = mmd_path.with_suffix(".svg")
    png_path = mmd_path.with_suffix(".png")
    digest = source_hash(mmd_path)
    print(f"  rendering {mmd_path.relative_to(ROOT)}")
    for out_path, extra in ((svg_path, []), (png_path, ["-s", PNG_SCALE])):
        cmd = (["npx", "-y", MERMAID_CLI_PKG, "-i", str(mmd_path), "-o", str(out_path),
               "-b", "white"] + extra)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            sys.stderr.write(r.stdout)
            sys.stderr.write(r.stderr)
            raise RuntimeError(f"mermaid-cli failed for {mmd_path.name} -> {out_path.name} "
                               f"(exit {r.returncode})")
    hash_sidecar(mmd_path).write_text(digest + "\n", encoding="utf-8")


def main() -> int:
    force = "--force" in sys.argv
    check = "--check" in sys.argv

    mmd_files = sorted(DIAGRAMS_DIR.glob("*.mmd"))
    if not mmd_files:
        print(f"no .mmd files under {DIAGRAMS_DIR}")
        return 0

    stale = [p for p in mmd_files if force or is_stale(p)]

    if check:
        if stale:
            print("STALE diagrams (re-run `python3 tools/render_diagrams.py`):")
            for p in stale:
                print(f"  {p.relative_to(ROOT)}")
            return 1
        print(f"all {len(mmd_files)} diagram(s) up to date")
        return 0

    if not stale:
        print(f"all {len(mmd_files)} diagram(s) already up to date")
        return 0

    if shutil.which("node") is None or shutil.which("npx") is None:
        print("Node.js / npx not found on PATH — cannot render diagrams.\n"
              f"Would have rendered: {[p.name for p in stale]}", file=sys.stderr)
        return 1

    for p in stale:
        render_one(p)
    print(f"rendered {len(stale)} diagram(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
