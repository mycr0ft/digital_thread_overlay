"""Command line: pin a baseline, record a claim, ask the three questions.

Kept thin on purpose. The CLI is how a reviewer reproduces what the paper says, so every
subcommand prints the thing it computed rather than a cheerful confirmation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import baselines, operations, query
from .ledger import Ledger
from .model import Assertion, Level, Status


def _ledger(args) -> Ledger:
    return Ledger(args.root)


def cmd_baseline(args) -> int:
    ledger = _ledger(args)
    baseline = baselines.create(args.paths, label=args.label)
    if not baseline.artifacts:
        print("no files matched; nothing pinned", file=sys.stderr)
        return 1
    ledger.put_baseline(baseline)
    ledger.set_current(baseline.id)
    print(f"{baseline.id}  {len(baseline.artifacts)} artifacts")
    for a in baseline.artifacts:
        print(f"  {a.kind:8s} {a.sha256[:12]}  {a.path}")
    return 0


def cmd_assert(args) -> int:
    ledger = _ledger(args)
    baseline_id = args.baseline or ledger.current_id()
    if not baseline_id:
        print("no baseline: run `baseline` first or pass --baseline", file=sys.stderr)
        return 1
    try:
        assertion = Assertion(
            subject=args.subject, predicate=args.predicate, object=args.object,
            baseline=baseline_id, level=Level(args.level),
            status=Status.ASSERTED, note=args.note or "",
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
    b.set_defaults(func=cmd_baseline)

    a = sub.add_parser("assert", help="record a relationship claim")
    a.add_argument("subject")
    a.add_argument("predicate")
    a.add_argument("object")
    a.add_argument("--level", default=Level.ASSERTED.value,
                   choices=[lvl.value for lvl in Level])
    a.add_argument("--baseline", default=None)
    a.add_argument("--note", default="")
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

    t = sub.add_parser("trace", help="what is claimed about an element")
    t.add_argument("subject")
    t.set_defaults(func=cmd_trace)

    i = sub.add_parser("impact", help="what cites this")
    i.add_argument("object")
    i.set_defaults(func=cmd_impact)

    s = sub.add_parser("stale", help="claims whose baseline has been superseded")
    s.set_defaults(func=cmd_stale)

    rep = sub.add_parser("report", help="counts and the staleness headline")
    rep.set_defaults(func=cmd_report)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
