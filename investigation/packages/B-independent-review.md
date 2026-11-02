# Independent B episode ordering review

> **Disposition (b-final worker, FINAL-AUDIT §4 integration).** This
> document records the investigation as performed on unmerged branch
> `0f76539`. Its recommendation to keep the broad generated comparison
> standing red was NOT adopted: an all-green, all-failures-classified
> suite is required, and the tie-order failure is distinct from the
> classified B2 defect. What was salvaged into `inv/0f462p/b-final`
> (base 132fa87): the nonoverlap day-set oracle and exact checks
> (`oracles/b_day_sets.py`, `test_pkg_b_pass2.py`, incl. the 384-case
> exhaustive domain and a shipped-SQL twin), the allowed-set
> enumeration and shipped-CTE inspection (`test_pkg_b_independent_review.py`),
> and the bounded permutation grid — with the exact pairing assertion
> and the input-order pin replaced by order-agnostic invariants
> (upstream multiset stability, supply conservation, allowed-set
> membership). The exact `@example` pairing pin from `d63a8fb` was NOT
> ported (thread/optimizer-sensitive: (3,6) at threads 1,
> (3,3,3)/(6,3) at threads 4). No production tie or rejection policy
> is approved; see the B2 finding addendum.

## Decision

The latest failure is both an over-specified oracle and an observable production
ordering ambiguity on malformed overlapping enrollment. It is **not** evidence
that span-major ordering is the intended contract. Do not tune the oracle again.
No production changes were made. B2 remains an inherited overlap double-count
bug; upstream QA is expected to reject overlaps. This investigation does not
verify that QA actually runs or rejects this fixture.

The broad generated comparison remains RED, without skip or xfail, on the
review branch (not integrated; superseded in b-final by the invariant
resolution above). Keeping it
red is deliberate there: production variability was demonstrated, so replacing its
assertion with one arbitrary engine order would conceal a contract decision.
A separate nonoverlap property now compares all episode, criteria, and master
payloads using the existing sequential oracle. It constructs nonoverlapping
spans (including replacing gap_minus at gap zero), retains flags, duplicate
claims, deaths, modes and other configuration variation, and uses no assume.
This is an additional supported-domain check, not a claim that every other
input field has been validated against upstream QA.

## Witness and first divergence

Reproducer: `tests/properties/test_pkg_b_independent_review.py:overlap_case`.
The original exact `@example` remains in `test_pkg_b_episodes.py:271-284`.
Fresh local `uv venv .venv` and editable `.[dev]` installed DuckDB 2.0.0,
Python 3.13.13, Hypothesis 6.170.14. Default threads=1.

For the unmodified raw order, the materialized enrollment rows for patient 2
are Jan 7–8 twice (different episode IDs) and Jan 8–8 once. Patient 1 has
Jan 8–8 and no surviving stockpiled row. The three patient-2 stockpiled rows
are identical: cohort_a, _default, Jan 8 admission/expiry/original date,
supply 1, amount .5, numdispensing 3. SQL stockpiled and POV1 rows exactly
match their Python models before episode construction.

`located` contains nine rows: six Jan-7 enrollment-start labels (A), three
Jan-8 labels (B). All nine tie on (adate, expiredt, rxsup), with Jan 8,
Jan 8, 1. Model insertion order is AAAAAABBB; observed single-thread claims
order is BBBAAAAAA. The first semantic divergence is therefore predecessor
selection in `claims`, not stockpile arithmetic or final grouping:

- First B: prev_runout/prev_enr_start NULL; break=1.
- Other two B: previous B, break=0.
- First A: previous B, break=1.
- Other five A: previous A, break=0.
- `numbered` assigns B rows episode 1, A rows episode 2.
- SQL episode supplies (3,6); model (6,3). Both start/end Jan 8.

The probe reads CTE definitions from shipped `50_episodes.sql`, not a copied
implementation. Separate diagnostic SELECTs may have different plans than
CREATE TABLE; they are evidence of evaluated intermediates, not a guarantee
that every instrumented run uses the same physical order as the table build.

## SQL semantics and full allowed set for the fixed nine-row input

`50_episodes.sql:46-59`: partition is cohortgrp, patid, NOT enrollment span.
Previous runout explicitly uses ROWS through 1 PRECEDING. LAG sees individual
peers; RANGE defaults do not make LAG a peer-group operation.
`50_episodes.sql:79-91`: span changes cause breaks; numbering is a SECOND
ROWS running sum, again without a tie key. `:94-103` groups only by cohort,
patient, episode and sums supply. Enrollment start is not a grouping key.

If both windows preserve the same peer order, all outcomes are exactly the
run lengths of every binary word containing six A and three B labels. There
are 84 such words, permitting 2 through 7 runs. Examples include (6,3),
(3,6), (3,3,3), and (1,1,1,1,1,1,3). This is not just choosing one of the
first two permutations.

SQL does not promise that the separate numbering window preserves the first
window's peer order. Given k breaks (every integer k=2..7 is possible), put
those k one-flags in ANY k of nine positions; cumulative summation labels each
position and grouping counts the labels. This is the complete allowed set
for these fixed inputs assuming the compatible predecessor windows share
one order. The probe enumerates it without calling SQL or the episode oracle.
Leading zero flags can produce episode zero; their absence in observed runs
is not a semantic guarantee. If an engine independently orders the compatible
LAG and MAX windows too, additional combinations are possible; this probe
characterizes DuckDB's shared predecessor-window execution, not all legal
implementations of arbitrary SQL optimizers. A RANGE sum would instead assign
all peers the total break count: a single supply-9 group, not the shipped
ROWS behavior. Changing ROWS to RANGE is not an ordering fix.

## Observed production sensitivity

Eight sequential runs covered threads 1/4, original/reversed enrollment and
dispensing input order, optimizer enabled/disabled. Original order at threads
1 gave (3,6) with both optimizer settings. Threads 4 gave (3,3,3) with both;
a later threads-4 run gave (6,3). The later comparison explicitly confirmed
identical enrollment_spans, stockpiled, POV1, and located multisets against
the threads-1 baseline. Thus variability is observed, not merely theoretical.
No test requires variability to recur on demand.

Reversing raw input gave one supply-4 episode under all four variants. That
is NOT isolated evidence about the episode window: tied, differently flagged
raw enrollment rows already encounter non-total ordering in
`20_enrollment.sql:54-85`. Raw-order tests must compare upstream multisets.

Multiplicity is independently preserved as evidence: three stockpiled copies
become nine located copies and total episode supply nine. The stockpile
join at `30_exposure.sql:331-360` and the second join at
`50_episodes.sql:23-32` both match every containing span. Existing B2 tests
remain unchanged, including the one-claim/46-day/count-two invariant violation
in `test_pkg_b_pass2.py`. Passing parity does not endorse that inflation.

## SAS reference and required contract choice

Read only: `/home/scarndp/dev/Sentinel/qrp/inputfiles/macros/ms_createclaimepi.sas`.
Lines 43–44 sort by Patid Adate Expiredt, not Enr_Start. Lines 63–92 retain
runout, prior enrollment start and supply, and break on enrollment-start
change. This supports sequential scan behavior but does not establish
span-major ordering for these ties. No SAS or clinical data execution occurred.

Before a production change, choose explicitly:

1. Is overlap outside the contract with mandatory upstream rejection? Identify
   the actual QA check and whether the SQL entry point needs a rejection guard.
2. If overlaps must be accepted, which enrollment span is authoritative, or is
   fan-out intentionally retained for SAS parity? B2 policy currently forbids
   silently removing that fan-out.
3. If fan-out stays, specify a deterministic order for enrollment-start peers
   and use a consistent ordering identity through predecessor and numbering
   windows. Also resolve raw enrollment date/coverage ties upstream. The SAS
   BY keys alone do not select this policy.

## Verification

- Exact original quick replay: 1 failed, SQL supply 3 vs model 6 (1.41s).
- New characterization + nonoverlap property: 2 passed (1.94s).
- All ten saved episode examples replayed individually in Hypothesis explicit
  context: nine pass; latest fails with the same supply mismatch.
- Full B quick twice, sequential, timeout 300, exit 1 both times:
  **1 failed, 13 passed**, 11.36s and 11.07s. Selection: enrollment, stockpile,
  index, episodes, pass2, independent_review test modules. Logs local at
  `/tmp/b-independent-quick1.log` and `...quick2.log`.
- Ruff E4/E7/E9/F on the new file passed. No full static typing claim.
- Two initial individual-replay probes failed on Hypothesis context/empty
  strategy setup; corrected explicit-context replay produced the results above.

No existing test/oracle assertions were weakened; no production, shared
manifest, original B report, SAS files, or publication were changed.
