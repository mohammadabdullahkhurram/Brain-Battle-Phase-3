"""
Acceptance gate for the Phase-3 owner rule: by the end of Phase 3 there must be
ZERO placeholder / temporary / "coming later" wording and ZERO placeholder
screens left in the shipped UI code — no exceptions (the Settings screen, the
last one, shipped in Phase 3 step 7).

It greps the first-party app modules (root *.py, excluding tests/ and the
dev-only tools/) for distinctive placeholder UI copy, and for any class named
*Placeholder, and lists what survives. Nothing is allowed to survive.

Run:  python3 tests/test_no_placeholders.py
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


# Distinctive "this isn't the real thing yet" UI copy. These don't collide with
# Qt API names or incidental identifiers (setPlaceholderText, TemporaryDirectory,
# self._placeholder), so a hit means real placeholder wording.
PHRASES = [
    "arrives in", "arrive in a later step", "coming soon", "old labelling",
    "sessions list arrives", "arrives in the next step", "lands in the next",
    "to be added later", "not yet implemented", "coming in a future",
]

# Zero exceptions now that Settings (the last placeholder screen) has shipped.
ALLOWED_PLACEHOLDER_CLASSES: set = set()
ALLOWED_PHRASE_LINES: set = set()


def _app_files():
    files = glob.glob(os.path.join(ROOT, "*.py"))
    files += glob.glob(os.path.join(ROOT, "sync_utils", "*.py"))
    return files


def test_no_stray_placeholder_copy():
    offenders = []
    allowed = []
    for fn in _app_files():
        rel = os.path.relpath(fn, ROOT)
        for i, line in enumerate(open(fn, encoding="utf-8").read().splitlines(), 1):
            low = line.lower()
            for ph in PHRASES:
                if ph in low:
                    if (rel, ph) in ALLOWED_PHRASE_LINES:
                        allowed.append((rel, i, ph))
                    else:
                        offenders.append((rel, i, ph, line.strip()[:80]))
    for rel, i, ph in allowed:
        print(f"      (allowed) {rel}:{i} — Settings placeholder copy ({ph!r})")
    check("no stray placeholder/temporary UI copy", not offenders,
          "; ".join(f"{r}:{i} {ph!r}" for r, i, ph, _ in offenders))


# EEG channel codes + the old "UPLOAD CSV" placeholder must never be shown in
# the UI (DESIGN vocabulary: plain sensor names only). We flag channel codes
# only inside widget-text calls, so data-key usages (dict keys, column names)
# don't false-positive.
_CHANNEL_CODES = ["TP9", "AF7", "AF8", "TP10"]
_DISPLAY_CALL = re.compile(
    r'(QLabel|setText|setToolTip|setWindowTitle|LabelItem|addLabel|setPlaceholderText)\s*\(')


def test_no_channel_codes_or_upload_in_ui():
    offenders = []
    for fn in _app_files():
        rel = os.path.relpath(fn, ROOT)
        for i, line in enumerate(open(fn, encoding="utf-8").read().splitlines(), 1):
            low = line.lower()
            if "upload csv" in low:
                offenders.append((rel, i, "UPLOAD CSV", line.strip()[:80]))
            # a channel code inside a widget-text call = jargon shown to users
            if _DISPLAY_CALL.search(line):
                for code in _CHANNEL_CODES:
                    if re.search(rf'["\']{code}["\']', line):
                        offenders.append((rel, i, code, line.strip()[:80]))
    check("no EEG channel codes or UPLOAD text in the UI", not offenders,
          "; ".join(f"{r}:{i} {c!r}" for r, i, c, _ in offenders))


def test_only_settings_placeholder_class():
    found = {}
    for fn in _app_files():
        rel = os.path.relpath(fn, ROOT)
        for m in re.finditer(r"class\s+(\w*Placeholder)\b", open(fn, encoding="utf-8").read()):
            found.setdefault(m.group(1), rel)
    extra = {k: v for k, v in found.items() if k not in ALLOWED_PLACEHOLDER_CLASSES}
    for k, v in found.items():
        print(f"      placeholder class: {k} ({v})"
              + ("  [allowed — Settings]" if k in ALLOWED_PLACEHOLDER_CLASSES else "  [NOT ALLOWED]"))
    check("only the Settings placeholder screen remains", not extra, str(extra))


if __name__ == "__main__":
    test_no_stray_placeholder_copy()
    test_no_channel_codes_or_upload_in_ui()
    test_only_settings_placeholder_class()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
