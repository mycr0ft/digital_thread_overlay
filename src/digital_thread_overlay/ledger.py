"""The ledger: baselines and assertions on disk, append-only.

Two shapes of file, both content-addressed and neither ever rewritten:

    <root>/baselines/<id>.json     one per pinned configuration
    <root>/baselines/current       the id of the baseline the tree now corresponds to
    <root>/assertions/<id>.jsonl   one line per assertion, partitioned by the baseline it
                                   was made against

Partitioning assertions by baseline is not storage tidiness: staleness is a question about
a baseline, so it is a partition-level question, and a sweep touches only the partitions
whose baseline has been superseded.

There is no update and no delete. A corrected claim is a new line that names what it
supersedes; the old line stays, because a record of what was believed is the point.
"""
from __future__ import annotations

import json
from pathlib import Path

from .baselines import Baseline
from .model import Assertion


class Ledger:
    def __init__(self, root):
        self.root = Path(root)
        (self.root / "baselines").mkdir(parents=True, exist_ok=True)
        (self.root / "assertions").mkdir(parents=True, exist_ok=True)

    # ---- baselines -------------------------------------------------------
    def put_baseline(self, baseline: Baseline) -> str:
        path = self.root / "baselines" / f"{baseline.id}.json"
        if not path.exists():                      # immutable once written
            path.write_text(baseline.model_dump_json(indent=1))
        return baseline.id

    def get_baseline(self, baseline_id: str) -> Baseline:
        path = self.root / "baselines" / f"{baseline_id}.json"
        if not path.exists():
            raise KeyError(f"unknown baseline {baseline_id}")
        return Baseline.model_validate_json(path.read_text())

    def set_current(self, baseline_id: str) -> None:
        (self.root / "baselines" / "current").write_text(baseline_id)

    def current_id(self) -> str | None:
        path = self.root / "baselines" / "current"
        return path.read_text().strip() if path.exists() else None

    def current(self) -> Baseline | None:
        cid = self.current_id()
        return self.get_baseline(cid) if cid else None

    def baselines(self) -> list[str]:
        return sorted(p.stem for p in (self.root / "baselines").glob("bl:*.json"))

    # ---- assertions ------------------------------------------------------
    def append(self, assertion: Assertion) -> str:
        self.put_baseline_if_missing(assertion.baseline)
        path = self.root / "assertions" / f"{assertion.baseline}.jsonl"
        with path.open("a") as f:
            f.write(assertion.to_jsonl() + "\n")
        return assertion.id

    def put_baseline_if_missing(self, baseline_id: str) -> None:
        """An assertion citing a baseline we were not handed is a caller error, not ours."""
        path = self.root / "baselines" / f"{baseline_id}.json"
        if not path.exists():
            raise KeyError(f"assertion cites unknown baseline {baseline_id}")

    def assertions(self, baseline: str | None = None) -> list[Assertion]:
        pattern = f"{baseline}.jsonl" if baseline else "*.jsonl"
        out = []
        for path in sorted((self.root / "assertions").glob(pattern)):
            for line in path.read_text().splitlines():
                if line.strip():
                    out.append(Assertion.from_jsonl(line))
        return out

    def superseded(self) -> set[str]:
        """Records replaced by a later line, by id."""
        return {a.supersedes for a in self.assertions() if a.supersedes}
