"""The claims the package exists to make, each as a test that can fail.

Five properties:
  1  a baseline's identity is its content, so a rename is invisible;
  2  an edit to a cited artifact makes the claim stale, and staleness is reported not repaired;
  3  review moves status and never level;
  4  promotion refuses anything but objective evidence that passed;
  5  the ledger has no destructive path - a correction is a new record naming what it replaced.
"""
import shutil
import pytest

from digital_thread_overlay import baselines, operations, query
from digital_thread_overlay.ledger import Ledger
from digital_thread_overlay.model import Assertion, Level, Status


@pytest.fixture()
def tree(tmp_path):
    """A tiny artefact tree: one model, one product structure."""
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "model.sysml").write_text("part def Vehicle { part front; }\n")
    (root / "part.stp").write_text("ISO-10303-21;\nHEADER;\nFILE_NAME('CTC-01');\n")
    return root


@pytest.fixture()
def ledger(tmp_path):
    return Ledger(tmp_path / ".overlay")


def pin(ledger, tree, label=""):
    b = baselines.create(sorted(tree.glob("*")), label=label)
    ledger.put_baseline(b)
    ledger.set_current(b.id)
    return b


# 1 ---------------------------------------------------------------------------
def test_identity_is_content_not_location(ledger, tree):
    """Renaming the tree must not change the baseline: content is the identity."""
    first = pin(ledger, tree)
    moved = tree.parent / "artifacts-renamed"
    shutil.move(str(tree), str(moved))
    second = baselines.create(sorted(moved.glob("*")))
    assert second.id == first.id, "a move changed the baseline identity"


def test_edit_changes_identity(ledger, tree):
    first = pin(ledger, tree)
    (tree / "model.sysml").write_text("part def Vehicle { part rear; }\n")
    assert baselines.create(sorted(tree.glob("*"))).id != first.id


# 2 ---------------------------------------------------------------------------
def test_edit_makes_the_claim_stale_and_does_not_repair_it(ledger, tree):
    pinned = pin(ledger, tree)
    a = Assertion(source_anchor="urn:test:anchor", subject="urn:sysml:Vehicle", predicate="correspondsToProduct",
                  object="urn:step242:CTC-01", baseline=pinned.id, level=Level.STRUCTURE)
    ledger.append(a)

    (tree / "part.stp").write_text("ISO-10303-21;\nHEADER;\nFILE_NAME('CTC-02');\n")
    pin(ledger, tree)                       # a new baseline for the changed tree

    rows = query.stale(ledger)
    assert len(rows) == 1, "the claim should be stale after the artifact it cites changed"
    stale_assertion, changes = rows[0]
    assert stale_assertion.id == a.id
    assert changes[0]["change"] == "edited"
    # and the record still cites the baseline it was made against
    assert stale_assertion.baseline == pinned.id


def test_staleness_distinguishes_a_move_from_an_edit(ledger, tree):
    """Same content under a new name is a move; the claim is not stale."""
    pinned = pin(ledger, tree)
    ledger.append(Assertion(source_anchor="urn:test:anchor", subject="urn:s", predicate="p", object="urn:o",
                            baseline=pinned.id))
    moved = tree.parent / "elsewhere"
    moved.mkdir()
    shutil.move(str(tree / "model.sysml"), str(moved / "renamed.sysml"))
    new = baselines.create(sorted((moved).glob("*")) + sorted(tree.glob("*")))
    ledger.put_baseline(new)
    ledger.set_current(new.id)
    assert query.stale(ledger) == [], "a rename was reported as a content change"


# 3 ---------------------------------------------------------------------------
def test_review_moves_status_not_level(ledger, tree):
    pinned = pin(ledger, tree)
    a = Assertion(source_anchor="urn:test:anchor", subject="urn:s", predicate="p", object="urn:o", baseline=pinned.id,
                  level=Level.ASSERTED)
    ledger.append(a)
    reviewed = operations.review(ledger, a, reviewer="jrf", note="looks right")
    assert reviewed.level == Level.ASSERTED
    assert reviewed.status == Status.REQUIRES_REVIEW
    assert reviewed.supersedes == a.id
    # the original stays, and is no longer the live record
    assert query.trace(ledger, "urn:s") == [reviewed]


# 4 ---------------------------------------------------------------------------
def test_manual_review_cannot_promote(ledger, tree):
    pinned = pin(ledger, tree)
    a = Assertion(source_anchor="urn:test:anchor", subject="urn:s", predicate="p", object="urn:o", baseline=pinned.id)
    ledger.append(a)
    from digital_thread_overlay.model import ManualReview
    with pytest.raises(ValueError, match="not objective evidence"):
        operations.promote(ledger, a, ManualReview(reviewer="jrf"))
    # an interface check IS objective, so it may promote - that is the contrast
    promoted = operations.promote(ledger, a, operations.interface_check("x", True))
    assert promoted.level == Level.VERIFIED   # L3 default + one step


def test_failed_check_cannot_promote(ledger, tree):
    pinned = pin(ledger, tree)
    a = Assertion(source_anchor="urn:test:anchor", subject="urn:s", predicate="p", object="urn:o", baseline=pinned.id)
    ledger.append(a)
    with pytest.raises(ValueError, match="must have passed"):
        operations.promote(ledger, a, operations.tool_run("check", "m", passed=False))
    with pytest.raises(ValueError, match="must have passed"):
        operations.promote(ledger, a, operations.tool_run("check", "m", passed=None))


def test_passing_check_promotes_one_step(ledger, tree):
    pinned = pin(ledger, tree)
    a = Assertion(source_anchor="urn:test:anchor", subject="urn:s", predicate="p", object="urn:o", baseline=pinned.id,
                  level=Level.STRUCTURE)
    ledger.append(a)
    promoted = operations.promote(ledger, a, operations.tool_run("check", "m", passed=True))
    assert promoted.level == Level.ASSERTED
    again = operations.promote(ledger, promoted,
                               operations.tool_run("check", "verified", passed=True))
    assert again.level == Level.VERIFIED
    assert again.status == Status.VERIFIED


def test_a_verified_assertion_cannot_be_constructed_without_evidence():
    """The gate is in the type: an unearned L4 record cannot even be built."""
    with pytest.raises(ValueError, match="requires objective evidence"):
        Assertion(source_anchor="urn:test:anchor", subject="s", predicate="p", object="o", baseline="bl:x",
                  level=Level.VERIFIED)
    ok = Assertion(source_anchor="urn:test:anchor", subject="s", predicate="p", object="o", baseline="bl:x",
                   level=Level.VERIFIED,
                   evidence=[operations.tool_run("t", "m", passed=True)])
    assert ok.level == Level.VERIFIED


# 5 ---------------------------------------------------------------------------
def test_ledger_has_no_destructive_operation(ledger, tree):
    pinned = pin(ledger, tree)
    a = Assertion(source_anchor="urn:test:anchor", subject="urn:s", predicate="p", object="urn:o", baseline=pinned.id)
    ledger.append(a)
    for name in ("delete", "update", "remove", "drop"):
        assert not hasattr(ledger, name), f"Ledger exposes {name}: the ledger is append-only"
    assert len(ledger.assertions()) == 1


def test_appending_the_same_claim_twice_is_visible_not_silently_deduped(ledger, tree):
    """Idempotence is the caller's business; the ledger records what it was told twice."""
    pinned = pin(ledger, tree)
    a = Assertion(source_anchor="urn:test:anchor", subject="urn:s", predicate="p", object="urn:o", baseline=pinned.id)
    ledger.append(a)
    ledger.append(a)
    rows = query.trace(ledger, "urn:s")
    assert len(rows) == 2 and rows[0].id == rows[1].id
