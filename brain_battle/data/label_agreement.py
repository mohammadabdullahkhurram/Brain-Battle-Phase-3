"""
label_agreement.py — how well do two labellers agree?
─────────────────────────────────────────────────────
Two people who label the same recording will not click at exactly the same
instant. Two labels count as **the same event** when they have

  • the same label name (Jab, Clinch, …),
  • the same boxer (A, B or Both), and
  • start times within ``tolerance`` seconds of each other (default 0.5 s).
    For labels that last a while, the two spans overlapping also counts.

Each label is matched at most once, closest pair first. The result is counts
and one plain sentence — deliberately not a statistic like Cohen's kappa, which
needs a fixed set of items to rate and would mean nothing to most users. The
matched / unmatched lists are returned so an analyst can compute whatever
measure they prefer from the exported ``labels.csv`` (it has a ``labeller``
column).

Pure logic, no Qt.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_TOLERANCE = 0.5
TOLERANCES = (0.25, 0.5, 1.0, 2.0)      # seconds, offered in Review & Label


@dataclass
class Agreement:
    name_a: str
    name_b: str
    tolerance: float
    matched: list = field(default_factory=list)   # [(label_a, label_b), …]
    only_a: list = field(default_factory=list)
    only_b: list = field(default_factory=list)

    @property
    def total(self) -> int:
        """Distinct events either labeller marked."""
        return len(self.matched) + len(self.only_a) + len(self.only_b)

    @property
    def percent(self) -> int:
        return int(round(100.0 * len(self.matched) / self.total)) if self.total else 0

    @property
    def mean_time_difference(self) -> float:
        """Average gap (seconds) between the two labellers on matched events."""
        if not self.matched:
            return 0.0
        return sum(abs(float(a["t_start"]) - float(b["t_start"]))
                   for a, b in self.matched) / len(self.matched)

    def sentence(self, a_is_you: bool = True) -> str:
        """Plain-language summary for the screen and the export README."""
        a = "You" if a_is_you else self.name_a
        if self.total == 0:
            return f"Neither {a.lower() if a_is_you else a} nor {self.name_b} has placed a label yet."
        both = (f"{a} and {self.name_b} marked the same event {len(self.matched)} "
                f"time{'s' if len(self.matched) != 1 else ''} out of {self.total} "
                f"({self.percent}% agreement).")
        extra = []
        if self.only_a:
            extra.append(f"{len(self.only_a)} only {a.lower() if a_is_you else a} marked")
        if self.only_b:
            extra.append(f"{len(self.only_b)} only {self.name_b} marked")
        tail = (" " + ", ".join(extra) + ".") if extra else ""
        return both + tail


def _overlap(a: dict, b: dict) -> bool:
    if a.get("t_end") is None or b.get("t_end") is None:
        return False
    return float(a["t_start"]) <= float(b["t_end"]) and float(b["t_start"]) <= float(a["t_end"])


def _same_event(a: dict, b: dict, tolerance: float) -> bool:
    if str(a.get("label", "")).strip().lower() != str(b.get("label", "")).strip().lower():
        return False
    if a.get("who") != b.get("who"):
        return False
    return abs(float(a["t_start"]) - float(b["t_start"])) <= tolerance + 1e-9 or _overlap(a, b)


def compare(labels_a, labels_b, name_a: str = "A", name_b: str = "B",
            tolerance: float = DEFAULT_TOLERANCE) -> Agreement:
    """Match two labellers' labels (closest start times first)."""
    la, lb = list(labels_a or []), list(labels_b or [])
    pairs = sorted(((abs(float(a["t_start"]) - float(b["t_start"])), i, j)
                    for i, a in enumerate(la) for j, b in enumerate(lb)
                    if _same_event(a, b, tolerance)), key=lambda p: p[0])
    used_a, used_b, matched = set(), set(), []
    for _gap, i, j in pairs:
        if i in used_a or j in used_b:
            continue
        used_a.add(i); used_b.add(j)
        matched.append((la[i], lb[j]))
    matched.sort(key=lambda p: float(p[0]["t_start"]))
    return Agreement(name_a, name_b, float(tolerance), matched,
                     [x for i, x in enumerate(la) if i not in used_a],
                     [x for j, x in enumerate(lb) if j not in used_b])


def pairwise(by_labeller: dict, tolerance: float = DEFAULT_TOLERANCE) -> list[Agreement]:
    """Agreement for every pair of labellers (``{name: [labels]}``)."""
    names = list(by_labeller)
    return [compare(by_labeller[a], by_labeller[b], a, b, tolerance)
            for i, a in enumerate(names) for b in names[i + 1:]]


__all__ = ["Agreement", "compare", "pairwise", "DEFAULT_TOLERANCE", "TOLERANCES"]
