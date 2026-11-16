"""Command line: pin a baseline, record a claim, ask the three questions.

Kept thin on purpose. The CLI is how a reviewer reproduces what the paper says, so every
subcommand prints the thing it computed rather than a cheerful confirmation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import adapters, baselines, checks, operations, prov, query
from .ledger import Ledger
from .model import Assertion, Level, Status, ToolRun, Validity


def _ledger(args) -> Ledger:
    return Ledger(args.root)


def cmd_baseline(args) -> int:
    ledger = _ledger(args)
    baseline = baselines.create(args.paths, label=args.label)
    if getattr(args, "with_adapters", False):
        # derived payloads are part of the identity: an extractor change that alters what
        # the overlay knows must move the baseline, even when every artifact is untouched
        baseline = baselines.add_derived(baseline, [adapters.extract(p) for p in args.paths])
    if not baseline.artifacts:
        print("no files matched; nothing pinned", file=sys.stderr)
        return 1
    ledger.put_baseline(baseline)
    ledger.set_current(baseline.id)
    print(f"{baseline.id}  {len(baseline.artifacts)} artifacts"
          f"{f', {len(baseline.derived)} derived' if baseline.derived else ''}")
    for a in baseline.artifacts:
        print(f"  {a.kind:8s} {a.sha256[:12]}  {a.path}")
    for d in baseline.derived:
        print(f"  derived  {d.kind:22s} {d.sha256[:12]}  {d.path}")
    return 0


def cmd_assert(args) -> int:
    ledger = _ledger(args)
    baseline_id = args.baseline or ledger.current_id()
    if not baseline_id:
        print("no baseline: run `baseline` first or pass --baseline", file=sys.stderr)
        return 1
    try:
        validity = None
        if args.valid_from or args.valid_until:
            validity = Validity(valid_from=args.valid_from or "",
                                valid_until=args.valid_until or "")
        assertion = Assertion(
            subject=args.subject, predicate=args.predicate, object=args.object,
            baseline=baseline_id, level=Level(args.level),
            status=Status.ASSERTED, note=args.note or "",
            source_anchor=args.anchor or "", target_anchor=args.target_anchor or "",
            validity=validity,
        )
        ledger.append(assertion)
    except (ValueError, KeyError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(f"{assertion.id}  {assertion.level.value}  {assertion.status.value}")
    return 0


def cmd_review(args) -> int:
    ledger = _ledger(args)
    target = _find(ledger, args.id)
    if target is None:
        print(f"no assertion {args.id}", file=sys.stderr)
        return 1
    updated = operations.review(ledger, target, args.reviewer, args.note or "")
    print(f"{updated.id}  {updated.level.value}  {updated.status.value}  "
          f"(status moved, level did not)")
    return 0


def cmd_promote(args) -> int:
    ledger = _ledger(args)
    target = _find(ledger, args.id)
    if target is None:
        print(f"no assertion {args.id}", file=sys.stderr)
        return 1
    evidence = operations.tool_run(args.tool, args.method, passed=(args.result == "pass"),
                                   version=args.version or "")
    try:
        updated = operations.promote(ledger, target, evidence)
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(f"{updated.id}  {updated.level.value}  {updated.status.value}")
    return 0


def cmd_verify(args) -> int:
    """Run a check; promote only if the check itself passed.

    This is the difference between evidence and a typed claim: the verdict in the record is
    the one the check produced, and a check that could not run records no verdict at all.
    """
    ledger = _ledger(args)
    target = _find(ledger, args.id)
    if target is None:
        print(f"no assertion {args.id}", file=sys.stderr)
        return 1

    try:
        check = checks.parse_check(args.check)
    except ValueError as e:
        print(f"bad check: {e}", file=sys.stderr)
        return 1

    verdict = check.run(target.subject, target.object)
    evidence = ToolRun(tool=verdict.tool, version=verdict.version,
                       method=verdict.method, passed=verdict.passed,
                       output_digest=verdict.output_digest)

    if verdict.passed is not True:
        # the attempt is recorded, the level is not raised: a failed check is evidence and
        # a missing tool is not a pass
        record = Assertion(
            subject=target.subject, predicate=target.predicate, object=target.object,
            baseline=target.baseline, level=target.level, status=Status.REQUIRES_REVIEW,
            evidence=list(target.evidence) + [evidence], supersedes=target.id,
            source_anchor=target.source_anchor, target_anchor=target.target_anchor,
            validity=target.validity,
            note=f"check {verdict.method}: "
                 f"{'failed' if verdict.passed is False else 'no verdict'}")
        ledger.append(record)
        print(f"refused: {verdict.method} "
              f"{'failed' if verdict.passed is False else 'produced no verdict'}; "
              f"recorded {record.id} at {record.level.value}", file=sys.stderr)
        return 2

    updated = operations.promote(ledger, target, evidence)
    print(f"{updated.id}  {updated.level.value}  {updated.status.value}  "
          f"on {verdict.method} (output {verdict.output_digest or 'none'})")
    return 0


def cmd_trace(args) -> int:
    rows = query.trace(_ledger(args), args.subject)
    _print_rows(rows)
    return 0 if rows else 1


def cmd_impact(args) -> int:
    rows = query.impact(_ledger(args), args.object)
    _print_rows(rows)
    return 0 if rows else 1


def cmd_stale(args) -> int:
    rows = query.stale(_ledger(args))
    for assertion, changes in rows:
        for c in changes:
            print(f"{assertion.id}  {c['change']:8s} {Path(c['path']).name} "
                  f"{c['was'][:12]} -> {(c['now'] or 'absent')[:12]}")
    if not rows:
        print("nothing stale")
    return 0


def cmd_extract(args) -> int:
    """Show what an adapter can justify about a file, and how it got there."""
    rows = []
    for path in args.paths:
        extraction = adapters.extract(path)
        rows.append(extraction.as_dict())
        print(f"{Path(path).name}  kind={extraction.kind}  adapter={extraction.adapter}"
              f"{'  TRUNCATED' if extraction.truncated else ''}")
        print(f"  sha256 {extraction.sha256[:16]}  {extraction.size} bytes")
        for fact in extraction.facts:
            print(f"  {fact.key:22s} {fact.value[:60]:60s} "
                  f"[{fact.method} @ {fact.anchor}]")
        if not extraction.facts:
            print("  (identity only: no adapter claims this format, which is honest rather "
                  "than guessed)")
    if args.json:
        print(json.dumps(rows, indent=1))
    return 0


def cmd_sidecar(args) -> int:
    """Write facts beside an artifact without touching it."""
    path = Path(args.path)
    adapter = adapters.SidecarAdapter()
    facts = [adapters.Fact(key=k, value=v, method="manual entry", anchor="cli")
             for k, v in (item.split("=", 1) for item in args.fact)]
    side = adapter.write(path, facts)
    print(f"wrote {side}  ({len(facts)} facts, artifact unchanged)")
    return 0


def cmd_prov(args) -> int:
    """Project the ledger onto PROV-O."""
    text = prov.project(_ledger(args))
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}  ({len(text.splitlines())} lines of Turtle)")
    else:
        print(text)
    return 0


def cmd_report(args) -> int:
    print(query.report(_ledger(args)))
    return 0


def _find(ledger: Ledger, prefix: str) -> Assertion | None:
    for a in ledger.assertions():
        if a.id == prefix or a.id.endswith(prefix.removeprefix("as:")):
            return a
    return None


def _print_rows(rows) -> None:
    for a in rows:
        print(f"{a.id}  {a.level.value}  {a.status.value:14s} {a.subject} "
              f"{a.predicate} {a.object}")
    if not rows:
        print("no records")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dtob", description="evidence-backed overlay ledger")
    p.add_argument("--root", default=".overlay", help="ledger directory (default .overlay)")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("baseline", help="pin a content-addressed baseline")
    b.add_argument("paths", nargs="+")
    b.add_argument("--label", default="")
    b.add_argument("--with-adapters", action="store_true",
                   help="include adapter extractions as derived payloads in the identity")
    b.set_defaults(func=cmd_baseline)

    a = sub.add_parser("assert", help="record a relationship claim")
    a.add_argument("subject")
    a.add_argument("predicate")
    a.add_argument("object")
    a.add_argument("--level", default=Level.ASSERTED.value,
                   choices=[lvl.value for lvl in Level])
    a.add_argument("--baseline", default=None)
    a.add_argument("--note", default="")
    a.add_argument("--anchor", default="", help="where in the subject artifact the claim attaches")
    a.add_argument("--target-anchor", default="", help="where in the object artifact")
    a.add_argument("--valid-from", default="")
    a.add_argument("--valid-until", default="")
    a.set_defaults(func=cmd_assert)

    r = sub.add_parser("review", help="a human confirms a claim (status only)")
    r.add_argument("id")
    r.add_argument("--reviewer", required=True)
    r.add_argument("--note", default="")
    r.set_defaults(func=cmd_review)

    pr = sub.add_parser("promote", help="raise the level on a passing check")
    pr.add_argument("id")
    pr.add_argument("--tool", required=True)
    pr.add_argument("--version", default="")
    pr.add_argument("--method", required=True)
    pr.add_argument("--result", required=True, choices=["pass", "fail", "no-verdict"])
    pr.set_defaults(func=cmd_promote)

    v = sub.add_parser("verify", help="run a check and promote on its verdict")
    v.add_argument("id")
    v.add_argument("--check", required=True,
                   help="name-occurs:<artifact> or command:<shell command>")
    v.set_defaults(func=cmd_verify)

    t = sub.add_parser("trace", help="what is claimed about an element")
    t.add_argument("subject")
    t.set_defaults(func=cmd_trace)

    i = sub.add_parser("impact", help="what cites this")
    i.add_argument("object")
    i.set_defaults(func=cmd_impact)

    s = sub.add_parser("stale", help="claims whose baseline has been superseded")
    s.set_defaults(func=cmd_stale)

    x = sub.add_parser("extract", help="what an adapter can justify about a file")
    x.add_argument("paths", nargs="+")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_extract)

    sc = sub.add_parser("sidecar", help="write facts beside an artifact (never into it)")
    sc.add_argument("path")
    sc.add_argument("--fact", action="append", default=[], metavar="KEY=VALUE")
    sc.set_defaults(func=cmd_sidecar)

    pv = sub.add_parser("prov", help="project the ledger onto PROV-O (Turtle)")
    pv.add_argument("--out", default="")
    pv.set_defaults(func=cmd_prov)

    rep = sub.add_parser("report", help="counts and the staleness headline")
    rep.set_defaults(func=cmd_report)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
