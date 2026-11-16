"""Overlay records: what a qualified relationship assertion is.

An assertion is deliberately larger than a triple. The triple says what is claimed; the
rest says who claims it, on what evidence, against which revision, and whether that
revision is still current. Those are the fields that make the claim auditable, and the
validation below is the ladder's gate expressed in the type rather than in a convention.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Level(str, Enum):
    """The evidence ladder, weakest to strongest.

    The names are the paper's: a locator says where something is, an identity says two
    things are the same thing, a structure says they fit together, an assertion is a
    human claim, a verified relationship has a check behind it, and operational evidence
    says the claim holds in a running system.
    """

    LOCATOR = "L0"
    IDENTITY = "L1"
    STRUCTURE = "L2"
    ASSERTED = "L3"
    VERIFIED = "L4"
    OPERATIONAL = "L5"

    @property
    def objective(self) -> bool:
        """L4 and above are claims about the world; below that they are claims about text."""
        return self in (Level.VERIFIED, Level.OPERATIONAL)


class Status(str, Enum):
    ASSERTED = "asserted"
    REQUIRES_REVIEW = "requires-review"
    VERIFIED = "verified"
    STALE = "stale"


class ToolRun(BaseModel):
    """Objective evidence: a program ran and said whether the claim holds."""

    kind: Literal["tool_run"] = "tool_run"
    tool: str
    version: str = ""
    method: str
    # None means the run produced no verdict, which is not evidence for a promotion.
    passed: bool | None = None
    output_digest: str = ""

    @property
    def objective(self) -> bool:
        return True

    @property
    def supports(self) -> bool:
        return self.passed is True


class InterfaceCheck(BaseModel):
    """A check against an interface contract - objective, but not a tool run."""

    kind: Literal["interface_check"] = "interface_check"
    check: str
    passed: bool | None = None

    @property
    def objective(self) -> bool:
        return True

    @property
    def supports(self) -> bool:
        return self.passed is True


class ManualReview(BaseModel):
    """A human looked at it. Raises status, never level: review is not evidence."""

    kind: Literal["manual_review"] = "manual_review"
    reviewer: str
    note: str = ""

    @property
    def objective(self) -> bool:
        return False

    @property
    def supports(self) -> bool:
        return False


Evidence = ToolRun | InterfaceCheck | ManualReview


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Validity(BaseModel):
    """When the claim is held to be true, where that is known.

    The paper calls this the one optional field: a claim without a window is a claim
    about "now", and a claim with one can outlive its evidence in a way a reader can see.
    """

    valid_from: str = ""
    valid_until: str = ""


class Assertion(BaseModel):
    """One qualified relationship, immutable once written."""

    subject: str
    predicate: str
    object: str
    baseline: str                     # the content-addressed baseline this was made against
    level: Level = Level.ASSERTED
    status: Status = Status.ASSERTED
    evidence: list[Evidence] = Field(default_factory=list)
    created: str = Field(default_factory=_now)
    supersedes: str | None = None     # append-only: corrections point back at what they replace
    note: str = ""
    # Where in each artifact the claim attaches. The paper's list names the source anchor;
    # the target anchor is the same idea applied to the other end, because a relationship
    # anchored at one end only is still ambiguous when you go to check it.
    source_anchor: str = ""
    target_anchor: str = ""
    validity: Validity | None = None

    @model_validator(mode="after")
    def _gate(self):
        """The ladder's gate, enforced where a record is constructed.

        A check that can fail is the only thing that lifts a claim above a human
        assertion. A reviewer's approval moves status, because confidence is not
        evidence, and an implementation that let review promote a level would be
        indistinguishable from one that promotes everything.
        """
        if self.level.objective:
            supporting = [e for e in self.evidence if getattr(e, "objective", False)
                          and getattr(e, "supports", False)]
            if not supporting:
                raise ValueError(
                    f"{self.level.value} requires objective evidence that passed; "
                    f"manual review raises status, not level")
        if self.level not in (Level.LOCATOR, Level.IDENTITY) and not self.source_anchor:
            raise ValueError(
                f"{self.level.value} needs a source anchor: a claim you cannot locate in "
                f"the artifact cannot be checked, and an uncheckable claim is a rumour")
        if self.level in (Level.LOCATOR, Level.IDENTITY, Level.STRUCTURE) and \
                self.status == Status.VERIFIED:
            raise ValueError(
                f"{self.level.value} cannot be 'verified': the level is weaker than the status")
        return self

    @property
    def id(self) -> str:
        """Content-addressed over the whole record, not just the claim.

        Status and evidence are part of the identity: a reviewed record and the record it
        replaces are different records, and an id that ignored status would make the
        supersede relation point at itself - which silently deleted both from the live set.
        """
        payload = json.dumps(
            [self.subject, self.predicate, self.object, self.baseline, self.level.value,
             self.status.value,
             [e.model_dump(mode="json") for e in self.evidence],
             self.supersedes, self.source_anchor, self.target_anchor,
             self.validity.model_dump(mode="json") if self.validity else None],
            sort_keys=True, separators=(",", ":"), default=str)
        return "as:" + hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_jsonl(self) -> str:
        data = self.model_dump(mode="json")
        data["id"] = self.id
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_jsonl(cls, line: str) -> "Assertion":
        data = json.loads(line)
        data.pop("id", None)
        return cls(**data)
