"""Content-addressed baselines.

The identity of a baseline is computed over what the artifacts *are*, not where they sit.
That single decision is what makes a rename invisible and an edit visible, and it is the
difference between a thread that survives a repository reorganisation and one that fires
on every one.

Artifact paths are recorded, so a reader can find the files, but they are not part of the
identifier. A baseline that hashed paths would answer "did the tree change?" when the
question is "did the content change?".
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

DIGEST_CHUNK = 1 << 20


class Artifact(BaseModel):
    """One file, identified by content."""

    kind: str                      # 'sysml', 'step', 'schema', 'source', ...
    sha256: str
    size: int = 0
    path: str = ""                 # recorded for humans; deliberately not part of the id


class Baseline(BaseModel):
    """A pinned configuration: the artifacts and the digests they had."""

    artifacts: list[Artifact] = Field(default_factory=list)
    # Derived payloads: what an adapter extracted about an artifact (its facts digest).
    # The paper's baseline band says "artifacts and derived payloads", and the distinction
    # matters: a sidecar's facts can change while the artifact does not, and a baseline that
    # covered only the artifact would miss exactly the staleness it exists to detect.
    derived: list[Artifact] = Field(default_factory=list)
    created: str = ""
    label: str = ""

    @property
    def id(self) -> str:
        """sha256 over (kind, digest) pairs only - content, never location."""
        payload = json.dumps(
            sorted([[a.kind, a.sha256] for a in self.artifacts] +
                   [["derived:" + d.kind, d.sha256] for d in self.derived]),
            separators=(",", ":"))
        return "bl:" + hashlib.sha256(payload.encode()).hexdigest()[:16]

    def digest_of(self, path: str) -> str | None:
        for a in self.artifacts:
            if a.path == path:
                return a.sha256
        return None


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(DIGEST_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def kind_of(path: Path, explicit: str | None = None) -> str:
    """A coarse kind, enough to label an artifact and to group the log."""
    if explicit:
        return explicit
    suffix = Path(path).suffix.lower()
    return {
        ".sysml": "sysml", ".kerml": "kerml", ".stp": "step", ".step": "step",
        ".xmi": "xmi", ".json": "json", ".jsonld": "jsonld", ".py": "source",
        ".exp": "schema", ".express": "schema",
    }.get(suffix, "other")


def create(paths, kinds: dict[str, str] | None = None, label: str = "") -> Baseline:
    """Hash the files and pin them. Paths are stored, digests are the identity."""
    kinds = kinds or {}
    artifacts = []
    for p in paths:
        path = Path(p)
        if not path.is_file():
            continue
        artifacts.append(Artifact(kind=kinds.get(str(path)) or kind_of(path),
                                  sha256=file_digest(path),
                                  size=path.stat().st_size,
                                  path=str(path)))
    return Baseline(artifacts=sorted(artifacts, key=lambda a: a.path),
                    created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    label=label)


def add_derived(baseline: Baseline, extractions) -> Baseline:
    """Attach adapter digests to a baseline, so derived facts are part of its identity."""
    derived = []
    for extraction in extractions:
        derived.append(Artifact(kind=f"extracted-{extraction.kind}",
                                sha256=extraction.sha256,
                                size=len(extraction.facts),
                                path=f"{extraction.adapter}:{extraction.path}"))
    baseline.derived = sorted(derived, key=lambda a: a.path)
    return baseline


def staleness(pinned: Baseline, current: Baseline) -> list[dict]:
    """Which artifacts a claim was made against have changed since.

    Compared per path: a path that vanished counts as changed, because the claim was made
    against content that is no longer there. A path that only moved is not compared here
    at all - the caller decides what the move means, and the digest is what it must not
    silently ignore.
    """
    changes = []
    current_digests = {a.sha256 for a in current.artifacts}
    for old in pinned.artifacts:
        now_digest = current.digest_of(old.path)
        if now_digest is not None and now_digest != old.sha256:
            changes.append({"path": old.path, "was": old.sha256, "now": now_digest,
                            "change": "edited"})
        elif now_digest is None and old.sha256 not in current_digests:
            # gone by path and gone by content: the content the claim cited is not present
            changes.append({"path": old.path, "was": old.sha256, "now": None,
                            "change": "removed"})
        # else: the same content is present under another path, which is a move, not a change

    for old in pinned.derived:
        now = next((d.sha256 for d in current.derived if d.path == old.path), None)
        if now is not None and now != old.sha256:
            changes.append({"path": old.path, "was": old.sha256, "now": now,
                            "change": "derived-changed"})
    return changes


def is_stale(pinned: Baseline, current: Baseline) -> bool:
    return bool(staleness(pinned, current))
