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
| baseline | which revision of which artifacts it was claimed against |
| level | how strong the claim is: a locator is not a verification |
| status | where it sits in review |
| evidence | the tool run, interface check or review behind it |
| supersedes | which earlier record this replaces, for a correction |
| created | when it was recorded |

## What this does not do

- No adapters yet. The paper's façade / shadow / sidecar patterns are how artifacts get
  read; this package is the record and the queries, and adapters plug into `baselines`.
- No server. The ledger is files, deliberately: it survives the tooling that wrote it, and
  it diffs.
- No PROV-O emission yet. Records carry what PROV-O would express (tool, method, inputs,
  digests); projecting them is a mapping, not a redesign, and it is not done here.
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
