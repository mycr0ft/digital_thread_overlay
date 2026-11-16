"""Operations that change what is believed, without changing what was recorded.

Every function here appends. Nothing is edited in place, because the record of what was
believed on what evidence is the artifact the thread exists to keep - and a thread whose
history can be rewritten cannot answer "why did we think this in March?".
"""
from __future__ import annotations

from .ledger import Ledger
from .model import (
    Assertion, Evidence, Level, ManualReview, Status, ToolRun, InterfaceCheck,
)

_PROMOTION_ORDER = [Level.LOCATOR, Level.IDENTITY, Level.STRUCTURE,
                    Level.ASSERTED, Level.VERIFIED, Level.OPERATIONAL]


def review(ledger: Ledger, assertion: Assertion, reviewer: str, note: str = "") -> Assertion:
    """A human confirms the claim. Status moves; level does not.

    This is the rule that keeps the ladder honest: someone reading carefully is worth a
    review tick, and is not evidence that the claim holds in the world.
    """
    replacement = Assertion(
        subject=assertion.subject, predicate=assertion.predicate, object=assertion.object,
        baseline=assertion.baseline, level=assertion.level,
        status=Status.REQUIRES_REVIEW,
        evidence=list(assertion.evidence) + [ManualReview(reviewer=reviewer, note=note)],
        supersedes=assertion.id,
        note=("reviewed: " + note) if note else "reviewed",
        source_anchor=assertion.source_anchor, target_anchor=assertion.target_anchor,
        validity=assertion.validity,
    )
    ledger.append(replacement)
    return replacement


def promote(ledger: Ledger, assertion: Assertion, evidence: Evidence) -> Assertion:
    """Raise the level by one, but only on objective evidence that passed.

    The refusals matter more than the success: an implementation that promotes on a name
    match, a manual review, or a tool run that failed is indistinguishable from one that
    promotes everything.
    """
    if not getattr(evidence, "objective", False):
        raise ValueError(
            f"{type(evidence).__name__} is not objective evidence; "
            f"review raises status, not level")
    if getattr(evidence, "passed", None) is not True:
        raise ValueError("objective evidence must have passed; a failed or unverdictive "
                         "run does not support the claim")

    index = _PROMOTION_ORDER.index(assertion.level)
    if index + 1 >= len(_PROMOTION_ORDER):
        raise ValueError(f"{assertion.level.value} is the top of the ladder")
    target = _PROMOTION_ORDER[index + 1]

    replacement = Assertion(
        subject=assertion.subject, predicate=assertion.predicate, object=assertion.object,
        baseline=assertion.baseline, level=target,
        status=Status.VERIFIED if target.objective else Status.REQUIRES_REVIEW,
        evidence=list(assertion.evidence) + [evidence],
        supersedes=assertion.id,
        note=f"promoted to {target.value} on {evidence.kind}",
        source_anchor=assertion.source_anchor, target_anchor=assertion.target_anchor,
        validity=assertion.validity,
    )
    ledger.append(replacement)
    return replacement


def tool_run(tool: str, method: str, passed: bool | None, version: str = "",
             output_digest: str = "") -> ToolRun:
    return ToolRun(tool=tool, method=method, passed=passed, version=version,
                   output_digest=output_digest)


def interface_check(check: str, passed: bool | None) -> InterfaceCheck:
    return InterfaceCheck(check=check, passed=passed)
