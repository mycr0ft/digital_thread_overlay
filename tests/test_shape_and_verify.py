"""The record shape from §3, and promotion driven by a check rather than a typed verdict.

Two claims under test:
  1  a claim you cannot locate in the artifact is refused at construction - the paper says
     nothing in the record shape is optional except the validity interval;
  2  `verify` takes its verdict from the check that ran, records the attempt when the check
     fails or cannot run, and never promotes on either.
"""
from pathlib import Path

import pytest

from digital_thread_overlay import baselines, checks, operations, query
from digital_thread_overlay.ledger import Ledger
from digital_thread_overlay.model import Assertion, Level, Status, ToolRun, Validity


@pytest.fixture()
def artifact(tmp_path):
    p = tmp_path / "part.stp"
    p.write_text("ISO-10303-21;\nHEADER;\n#10=PRODUCT('CTC-01');\n")
    return p


@pytest.fixture()
def ledger(tmp_path):
    return Ledger(tmp_path / ".overlay")


def pinned(ledger, artifact, with_adapters=False):
    b = baselines.create([artifact])
    if with_adapters:
        from digital_thread_overlay import adapters
        b = baselines.add_derived(b, [adapters.extract(artifact)])
    ledger.put_baseline(b)
    ledger.set_current(b.id)
    return b


# ---- record shape -----------------------------------------------------------
def test_a_claim_that_cannot_be_located_is_refused():
    with pytest.raises(ValueError, match="source anchor"):
        Assertion(subject="urn:sysml:Vehicle", predicate="correspondsTo",
                  object="urn:step:CTC-01", baseline="bl:x", level=Level.STRUCTURE)


def test_locator_and_identity_levels_do_not_need_an_anchor():
    """L0/L1 are claims about position, so the position is the claim."""
    a = Assertion(subject="urn:sysml:Vehicle", predicate="isAt", object="urn:file:a",
                  baseline="bl:x", level=Level.LOCATOR)
    assert a.source_anchor == ""


def test_anchors_and_validity_are_part_of_the_record_identity():
    base = dict(subject="urn:s", predicate="p", object="urn:o", baseline="bl:x",
                level=Level.STRUCTURE, source_anchor="line 3")
    one = Assertion(**base)
    two = Assertion(**{**base, "source_anchor": "line 4"})
    three = Assertion(**{**base, "validity": Validity(valid_until="2027-03-01")})
    assert one.id != two.id, "a different anchor is a different claim"
    assert one.id != three.id, "a different validity window is a different claim"


def test_review_and_promotion_carry_the_anchors_forward(ledger, artifact):
    b = pinned(ledger, artifact)
    a = Assertion(subject="urn:s", predicate="p", object="urn:o", baseline=b.id,
                  level=Level.STRUCTURE, source_anchor="#10", target_anchor="HEADER",
                  validity=Validity(valid_from="2026-11-16"))
    ledger.append(a)
    reviewed = operations.review(ledger, a, reviewer="jrf")
    assert (reviewed.source_anchor, reviewed.target_anchor) == ("#10", "HEADER")
    assert reviewed.validity and reviewed.validity.valid_from == "2026-11-16"
    promoted = operations.promote(ledger, reviewed,
                                  operations.tool_run("t", "m", passed=True))
    assert promoted.source_anchor == "#10"


# ---- verify: the verdict comes from the check -------------------------------
def test_name_occurs_check_passes_when_the_fact_is_present(artifact):
    v = checks.NameOccursCheck(artifact).run("urn:step242:CTC-01", "urn:step242:product:CTC-01")
    assert v.passed is True
    assert "CTC-01=found" in v.output


def test_name_occurs_check_fails_on_unrelated_artifacts(artifact):
    v = checks.NameOccursCheck(artifact).run("urn:sysml:StartEngine", "urn:step:CTC-01")
    assert v.passed is False, "a check that passes on everything is not a check"


def test_command_check_reports_a_missing_tool_as_no_verdict():
    v = checks.CommandCheck("definitely-not-a-real-command --check").run("s", "o")
    assert v.passed is None, "a tool that could not run must not read as a pass"


def test_command_check_takes_the_exit_status_as_the_verdict():
    assert checks.CommandCheck("true").run("s", "o").passed is True
    assert checks.CommandCheck("false").run("s", "o").passed is False


def test_a_failing_check_records_the_attempt_without_promoting(ledger, artifact):
    b = pinned(ledger, artifact)
    a = Assertion(subject="urn:sysml:StartEngine", predicate="correspondsTo",
                  object="urn:step:CTC-01", baseline=b.id, level=Level.STRUCTURE,
                  source_anchor="#10")
    ledger.append(a)

    verdict = checks.NameOccursCheck(artifact).run(a.subject, a.object)
    assert verdict.passed is False
    evidence = ToolRun(tool=verdict.tool, method=verdict.method, passed=verdict.passed,
                       output_digest=verdict.output_digest)
    recorded = Assertion(subject=a.subject, predicate=a.predicate, object=a.object,
                         baseline=a.baseline, level=a.level,
                         status=Status.REQUIRES_REVIEW,
                         evidence=[evidence], supersedes=a.id,
                         source_anchor=a.source_anchor, note="check failed")
    ledger.append(recorded)

    live = query.trace(ledger, a.subject)
    assert len(live) == 1 and live[0].id == recorded.id
    assert live[0].level == Level.STRUCTURE, "a failed check must not raise the level"
    assert live[0].evidence[0].passed is False


def test_a_passing_check_promotes_and_records_the_output_digest(ledger, artifact):
    b = pinned(ledger, artifact)
    a = Assertion(subject="urn:step242:CTC-01", predicate="correspondsTo",
                  object="urn:step:CTC-01", baseline=b.id, level=Level.STRUCTURE,
                  source_anchor="#10")
    ledger.append(a)
    verdict = checks.NameOccursCheck(artifact).run(a.subject, a.object)
    assert verdict.passed is True
    evidence = ToolRun(tool=verdict.tool, method=verdict.method, passed=verdict.passed,
                       output_digest=verdict.output_digest)
    promoted = operations.promote(ledger, a, evidence)
    assert promoted.level == Level.ASSERTED
    assert promoted.evidence[-1].output_digest, "the run's output must be identifiable"


# ---- derived payloads in the baseline ---------------------------------------
def test_derived_payloads_change_the_baseline_identity(ledger, artifact):
    """The artifact is untouched; the extraction changed. The baseline must move."""
    plain = baselines.create([artifact])
    with_extraction = baselines.add_derived(baselines.create([artifact]),
                                           [adapters_extract(artifact)])
    assert with_extraction.id != plain.id, \
        "an extractor that changed what the overlay knows must move the baseline"


def test_staleness_reports_changed_derived_payloads(ledger, artifact):
    b1 = baselines.add_derived(baselines.create([artifact]), [adapters_extract(artifact)])
    ledger.put_baseline(b1)
    a = Assertion(subject="urn:s", predicate="p", object="urn:o", baseline=b1.id,
                  level=Level.STRUCTURE, source_anchor="#10")
    ledger.append(a)

    b2 = baselines.add_derived(baselines.create([artifact]), [adapters_extract(artifact)])
    b2.derived[0].sha256 = "0" * 64          # same artifact, different extraction
    ledger.put_baseline(b2)
    ledger.set_current(b2.id)
    changes = baselines.staleness(b1, b2)
    assert any(c["change"] == "derived-changed" for c in changes)


def adapters_extract(path):
    from digital_thread_overlay import adapters
    return adapters.extract(path)
