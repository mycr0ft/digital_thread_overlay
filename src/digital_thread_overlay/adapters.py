"""Adapters: how an artifact becomes something the overlay can point at.

The paper names three patterns, and they differ in what they are allowed to do to the
artifact:

    facade    the artifact already has a service; the overlay reads it and adds nothing
    shadow    no service exists; the adapter extracts selected facts into a derived graph
    sidecar   the artifact must not be modified, so facts live beside it under a digest

Every adapter here obeys the same contract, and the contract's most important clause is
what it does NOT promise: no adapter claims to understand more of a format than its
extraction can justify. A STEP scanner that reads the header and the product entities is a
scanner, and it says so in the facts it returns, so a downstream reviewer can see how a
fact was obtained rather than trusting a tool name.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .baselines import file_digest, kind_of


@dataclass
class Fact:
    """One extracted statement about an artifact, with the means of extraction."""

    key: str
    value: str
    method: str
    anchor: str = ""          # where in the artifact the fact came from (line, entity id)

    def as_dict(self) -> dict:
        return {"key": self.key, "value": self.value, "method": self.method,
                "anchor": self.anchor}


@dataclass
class Extraction:
    """What an adapter found: identity always, anchors when the format allows it."""

    path: str
    kind: str
    sha256: str
    size: int
    adapter: str
    facts: list[Fact] = field(default_factory=list)
    truncated: bool = False   # an extraction that stopped early says so

    def as_dict(self) -> dict:
        return {
            "path": self.path, "kind": self.kind, "sha256": self.sha256,
            "size": self.size, "adapter": self.adapter, "truncated": self.truncated,
            "facts": [f.as_dict() for f in self.facts],
        }


class Adapter(Protocol):
    """An adapter reads an artifact; it never modifies it."""

    name: str

    def handles(self, path: Path) -> bool: ...
    def extract(self, path: Path) -> Extraction: ...


class SidecarAdapter:
    """For artifacts that must not be touched: facts live beside the file, keyed by digest.

    The sidecar is named after the artifact and carries the digest of the content it
    describes. That pairing is what makes a stale sidecar detectable rather than silently
    authoritative - if the artifact changes and the sidecar is not regenerated, the digests
    disagree and the reader knows the facts are about an older revision.
    """

    name = "sidecar/1"

    def __init__(self, suffixes: tuple[str, ...] = (".stp", ".step", ".xmi", ".sysml")):
        self.suffixes = suffixes

    def handles(self, path: Path) -> bool:
        return Path(path).suffix.lower() in self.suffixes

    @staticmethod
    def sidecar_path(path: Path) -> Path:
        path = Path(path)
        return path.with_name(path.name + ".dt.json")

    def extract(self, path: Path) -> Extraction:
        path = Path(path)
        facts = [Fact(key="content-digest", value=file_digest(path),
                      method="sha256 over the file", anchor="whole-file")]
        side = self.sidecar_path(path)
        if side.exists():
            try:
                payload = json.loads(side.read_text())
            except json.JSONDecodeError:
                facts.append(Fact(key="sidecar", value="unparseable", method=self.name,
                                  anchor=side.name))
                return Extraction(str(path), kind_of(path), facts[0].value,
                                  path.stat().st_size, self.name, facts)
            for entry in payload.get("facts", []):
                facts.append(Fact(key=entry.get("key", ""), value=str(entry.get("value", "")),
                                  method=f"sidecar:{entry.get('method', 'unknown')}",
                                  anchor=entry.get("anchor", side.name)))
            if payload.get("sha256") and payload["sha256"] != facts[0].value:
                facts.append(Fact(key="sidecar-stale", value="true", method=self.name,
                                  anchor=side.name))
        return Extraction(str(path), kind_of(path), facts[0].value, path.stat().st_size,
                          self.name, facts)

    def write(self, path: Path, facts: list[Fact]) -> Path:
        """Write the sidecar. Facts about the artifact, never a copy of it."""
        path = Path(path)
        side = self.sidecar_path(path)
        payload = {"artifact": path.name, "sha256": file_digest(path),
                   "facts": [f.as_dict() for f in facts]}
        side.write_text(json.dumps(payload, indent=1) + "\n")
        return side


_PRODUCT_RE = re.compile(r"#(\d+)\s*=\s*PRODUCT\s*\(\s*'([^']*)'", re.I)
_FILE_NAME_RE = re.compile(r"FILE_NAME\s*\(\s*'([^']*)'", re.I)
_FILE_SCHEMA_RE = re.compile(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']*)'", re.I)
_FILE_DESC_RE = re.compile(r"FILE_DESCRIPTION\s*\(\s*\(\s*'([^']*)'", re.I)


class StepAdapter:
    """A line-oriented reader for ISO 10303-21 exchange files.

    It extracts the header and the product entities, and nothing else. It does not parse
    geometry, it does not resolve references, and it does not claim to: the facts it
    returns carry `method` values that say "header scan" and "entity scan", so a reader can
    see exactly how thin the evidence is. A thicker extraction belongs in a real STEP
    parser, and pretending otherwise here would put a confident label on a guess.
    """

    name = "step-header-scan/1"
    MAX_BYTES = 8 << 20       # a scan is a scan; do not read a gigabyte to find a header

    def handles(self, path: Path) -> bool:
        return Path(path).suffix.lower() in (".stp", ".step")

    def extract(self, path: Path) -> Extraction:
        path = Path(path)
        digest = file_digest(path)
        size = path.stat().st_size
        facts: list[Fact] = []
        truncated = False
        head = path.read_bytes()[: self.MAX_BYTES].decode("latin-1", errors="replace")
        if size > self.MAX_BYTES:
            truncated = True

        for regex, key, method in (
            (_FILE_NAME_RE, "step:file-name", "header scan"),
            (_FILE_SCHEMA_RE, "step:schema", "header scan"),
            (_FILE_DESC_RE, "step:description", "header scan"),
        ):
            m = regex.search(head)
            if m:
                facts.append(Fact(key=key, value=m.group(1).strip(), method=method,
                                  anchor="HEADER"))

        for entity, name in _PRODUCT_RE.findall(head):
            facts.append(Fact(key="step:product", value=name.strip(), method="entity scan",
                              anchor=f"#{entity}"))
            if len(facts) > 500:
                truncated = True
                break

        if not any(f.key == "step:schema" for f in facts):
            facts.append(Fact(key="step:schema", value="unrecognised", method="header scan",
                              anchor="HEADER"))
        return Extraction(str(path), "step", digest, size, self.name, facts, truncated)


def default_adapters() -> list[Adapter]:
    """Order matters: the specific reader first, the generic sidecar reader after."""
    return [StepAdapter(), SidecarAdapter()]


def extract(path, adapters: list[Adapter] | None = None) -> Extraction:
    """First adapter that claims the target; otherwise identity only, honestly labelled.

    A URL is a façade: there is nothing on disk to hash, so the façade adapter reads the
    service instead. The import is local because facade imports this module for Fact.
    """
    target = str(path)
    if target.startswith(("http://", "https://")):
        from .facade import FacadeAdapter
        return FacadeAdapter().extract(target)
    path = Path(path)
    for adapter in (adapters or default_adapters()):
        if adapter.handles(path):
            return adapter.extract(path)
    return Extraction(str(path), kind_of(path), file_digest(path), path.stat().st_size,
                      "identity-only/1")
