# digital_thread_overlay

Evidence-backed assertions across artifacts that no single tool owns.

A digital thread is usually attempted by converting everything into one repository. That
fails on the artifacts that matter: the formats are authoritative as they stand, and
converting them destroys the configuration identity a reviewer needs. This project
implements the alternative: leave every file alone, and keep a separate record of what is
claimed about them, on what evidence, against which revision.

## The three ideas

**A baseline is identified by content.** `bl:<sha256>` is computed over the `(kind, digest)`
pairs of the pinned files, never over their paths. A rename is invisible to the identity; an
edit changes it. That single decision is why a thread survives a repository reorganisation
and still notices a changed byte.

**Records are append-only.** There is no update and no delete in the ledger. A correction is
a new record naming what it supersedes, and the old one stays, because the record of what
was believed is the artifact the thread exists to keep.

**Level needs evidence; status needs a person.** The ladder runs
`L0 locator → L1 identity → L2 structure → L3 asserted → L4 verified → L5 operational`.
A human review moves *status*; only objective evidence that passed moves *level*. The gate
is a `model_validator`, so an unearned L4 record cannot be constructed at all.

## Usage

```sh
uv pip install -e .

dtob baseline model.sysml part.stp                  # pin the current content
dtob assert urn:sysml:Vehicle correspondsToProduct urn:step242:CTC-01 --level L2
dtob trace urn:sysml:Vehicle                        # what is claimed about it
dtob impact urn:step242:CTC-01                      # what cites it
dtob promote as:<id> --tool step242 --method structure-check --result pass
dtob report                                         # counts, and what has gone stale
```

Staleness is computed and reported, never repaired:

```sh
dtob stale
as:2b0f…  edited   part.stp 036683e8358a -> 864c9b48e63a
```

Re-attaching that claim to a new baseline automatically would launder a check that never
ran. The reader decides; the ledger remembers.

## Adapters

An adapter reads an artifact and never modifies it. Three patterns, and the differences
are about what an adapter is allowed to do to the file:

| Pattern | When | What it may do |
|---|---|---|
| façade | the artifact has a service | read it; add nothing (implemented) |
| shadow | no service exists | extract selected facts into a derived graph |
| sidecar | the file must not be touched | write facts beside it, keyed by digest |

The contract's important clause is what it does *not* promise. `StepAdapter` scans the
ISO 10303-21 header and the product entities and says so in every fact it returns
(`method: "header scan"` / `"entity scan"`, with the entity id as the anchor). It does not
parse geometry and does not pretend to — a thicker read belongs in a real STEP parser, and
labelling a scan as a parse is how a thread acquires confident nonsense.

The sidecar pairs facts with the digest of the content they describe, so a sidecar that
was not regenerated after an edit is visibly stale (`sidecar-stale`) rather than silently
authoritative.

`FacadeAdapter` reads a live OSLC service and is read-only by construction: the fetcher
contract carries no method, so there is no code path that could POST. It reads each kind in
both renderings and records a `rendering-mismatch` fact when RDF and JSON-LD disagree,
because a service disagreeing with itself is a finding rather than an inconvenience.

```sh
dtob extract http://127.0.0.1:5099/oslc/sysml
sysml  kind=oslc-service  adapter=oslc-facade/1
  facade:root-status          405
  facade:element:resources    142
  facade:partDefinition:resources  25
  facade:relationship:resources    0
```

A service has no bytes to hash, so the identity is a digest over the descriptions actually
received - labelled `digest_of="served-descriptions"` so nobody mistakes it for a digest of
the artifact. The root status is reported as `facade:root-status` rather than a health
verdict: a collection root usually answers 405 to GET, which says nothing about the service.

Live check (needs a server, so it is skipped by default):

```sh
DTO_FACADE_URL=http://127.0.0.1:5099/oslc/sysml pytest tests/test_facade_live.py
```

```sh
dtob extract art/part.stp
art/part.stp  kind=step  adapter=step-header-scan/1
  sha256 036683e8358ac27d  395283 bytes
  step:schema      AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF { 1 0 10303 442 1 1 4 }  [header scan @ HEADER]
  step:product     NIST Test Case 1                                    [entity scan @ #4374]

dtob sidecar art/part.stp --fact owner=jrf      # file byte-identical afterwards
```

## PROV-O

`dtob prov --out overlay.ttl` projects the ledger onto PROV-O, closing the gap the paper's
discussion names. The directions are the ones that keep the graph queryable:

    artifact digest   prov:Entity        the thing used
    tool run / review prov:Activity      something that happened, at a time
    assertion         prov:Entity        generated by an activity, used a baseline
    baseline          prov:Entity        a Collection whose members are the artifacts
    tool / reviewer   prov:Agent         attributed responsibility

A claim is an **Entity generated by** an Activity, not an Activity itself. Reversing that
makes `prov:wasGeneratedBy` point the wrong way, which is the usual reason a provenance
graph cannot be queried for "where did this come from".

## Layout

```
<root>/baselines/<id>.json      one per pinned configuration (immutable)
<root>/baselines/current        the baseline the tree now corresponds to
<root>/assertions/<id>.jsonl    assertions partitioned by the baseline they cite
```

Partitioning by baseline makes staleness a partition-level question: a sweep touches only
the partitions whose baseline has been superseded, so it does not get slower as the thread
grows.

## Shape of a record

| Field | Why it is there |
|---|---|
| subject, predicate, object | what is claimed |
| source_anchor, target_anchor | where in each artifact the claim attaches; an L2+ claim without one is refused, because a claim you cannot locate cannot be checked |
| baseline | which revision of which artifacts it was claimed against |
| level | how strong the claim is: a locator is not a verification |
| status | where it sits in review |
| evidence | the check that ran, or the review that happened |
| validity | an optional window; the one field the paper calls optional |
| supersedes | which earlier record this replaces, for a correction |
| created | when it was recorded |

## Checks, not verdicts

`promote --result pass` records an operator's word. `verify` runs a check and takes the
verdict from the check:

```sh
dtob verify as:d324482a256c1f59 --check "name-occurs:art/part.stp"
as:1f617489eef025b3  L3  requires-review  on name-occurs over part.stp (output 847219b1d2082939)

dtob verify <id> --check "command:step242 structure-check art/part.stp"
dtob verify <id> --check "command:not-a-real-tool --check"
refused: command not runnable: not-a-real-tool produced no verdict; recorded as:283b… at L3
```

A failing check and an unrunnable one are both recorded — the attempt is evidence — and
neither raises the level. A missing tool reports **no verdict**, which the gate treats as
unsupported: absence of a check must never read as a pass.

## Baselines cover derived payloads too

`dtob baseline --with-adapters` folds each adapter's extraction digest into the baseline
identity, because the paper's baseline band says "artifacts and derived payloads". Without
it, an extractor change that alters what the overlay knows would leave the baseline
untouched, and staleness would miss precisely the case it exists to catch.

## What this does not do

- The façade adapter is not implemented (no service to talk to here); the shadow pattern is
  represented by the extraction step, and the sidecar is implemented in full.
- No server. The ledger is files, deliberately: it survives the tooling that wrote it, and
  it diffs.
- No concurrency control. Two writers appending simultaneously is a problem this version
  does not solve.

## Origin

Implements the overlay described in "An Evidence-Backed Digital Thread" (CSER 2027):
content-addressed baselines, a relationship evidence ladder, and staleness detected rather
than re-attached. The failures that shaped the design are recorded in that paper's
evaluation, and the rename-insensitivity here is the same property the paper's experiment
demonstrates on STEP exchange files.

## Licence

MPL-2.0.
