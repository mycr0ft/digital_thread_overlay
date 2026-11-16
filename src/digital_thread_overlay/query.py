"""The three queries the overlay answers, and staleness.

Each query returns a report rather than a list, because the useful answer is not "here are
four rows" but "here are four rows, one of them verified, and two of them are stale" -
which is what a reviewer needs and what a trace matrix cannot say.

Staleness is computed, never repaired. An assertion stays in the ledger; when the artifact
it was made against changes, the assertion is reported as stale and the reader decides.
Re-attaching it silently to a new baseline would launder a check that never ran.
"""
from __future__ import annotations

from .baselines import Baseline, staleness
from .ledger import Ledger
from .model import Assertion, Level, Status


def _live(ledger: Ledger) -> list[Assertion]:
    """Assertions not replaced by a later record."""
    superseded = ledger.superseded()
    return [a for a in ledger.assertions() if a.id not in superseded]


def trace(ledger: Ledger, subject: str) -> list[Assertion]:
    """What is claimed about this element, whichever artifacts it points into."""
    return [a for a in _live(ledger) if a.subject == subject]


def impact(ledger: Ledger, obj: str) -> list[Assertion]:
    """What cites this thing - the query that answers 'what breaks if this changes?'."""
    return [a for a in _live(ledger) if a.object == obj]


def stale(ledger: Ledger, current: Baseline | None = None) -> list[tuple[Assertion, list[dict]]]:
    """Assertions whose baseline no longer matches the tree, with what changed."""
    current = current or ledger.current()
    if current is None:
        return []
    out = []
    for a in _live(ledger):
        pinned = ledger.get_baseline(a.baseline)
        changes = staleness(pinned, current)
        if changes:
            out.append((a, changes))
    return out


def summary(ledger: Ledger) -> dict:
    """Counts by level and status, plus the staleness headline."""
    live = _live(ledger)
    by_level = {lvl.value: 0 for lvl in Level}
    by_status = {st.value: 0 for st in Status}
    for a in live:
        by_level[a.level.value] += 1
        by_status[a.status.value] += 1
    stale_rows = stale(ledger)
    return {
        "assertions": len(live),
        "superseded": len(ledger.superseded()),
        "by_level": by_level,
        "by_status": by_status,
        "stale": len(stale_rows),
        "verified": sum(1 for a in live if a.level.objective),
        "baselines": len(ledger.baselines()),
    }


def report(ledger: Ledger) -> str:
    lines = []
    s = summary(ledger)
    lines.append(f"assertions   {s['assertions']}  ({s['superseded']} superseded)")
    lines.append(f"baselines    {s['baselines']}  (current {ledger.current_id()})")
    levels = ", ".join(f"{k}:{v}" for k, v in s["by_level"].items() if v)
    lines.append(f"by level     {levels or 'none'}")
    statuses = ", ".join(f"{k}:{v}" for k, v in s["by_status"].items() if v)
    lines.append(f"by status    {statuses or 'none'}")
    lines.append(f"verified     {s['verified']}")
    lines.append(f"stale        {s['stale']}")
    for a, changes in stale(ledger):
        for c in changes:
            lines.append(f"  stale  {a.id}  {c['change']}: "
                         f"{c['path'].rsplit('/', 1)[-1]} {c['was'][:12]} -> "
                         f"{(c['now'] or 'absent')[:12]}")
    return "\n".join(lines)
