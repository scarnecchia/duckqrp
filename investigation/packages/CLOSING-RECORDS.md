# Closing records — B/D pass-2 batteries and record repairs (2026-11-02)

Worker: general-purpose subagent, branch `inv/0f462p/closing-records`
(base 3709ab2, which already integrates B final 4891dfa and the strict
AC.8 validator 2c936f0). PRIVATE — NOT AUTHORIZED FOR PUBLICATION.

Owned files touched: `investigation/manifest.json`,
`investigation/packages/ARBITRATION.md`, this file. No tests, no source,
no other worktrees; private local only.

## What was recorded (genuine, executed runs only)

### Package B — pass-3 FINAL BATTERY (integrated)

Source: `investigation/packages/B.md`, pass records 1-6, worktree
`inv/0f462p/b-final` (base 132fa87, fresh `.venv` via `uv venv --python
3.13` + `-e '.[dev]'`, hypothesis 6.170.14, serial pytest, no xdist,
StageHarness threads=1). Head 4891dfa is an ancestor of this branch's
base — these runs are integrated. Recorded as `pkg-b-pass2-quick-1`,
`pkg-b-pass2-quick-2`, `pkg-b-pass2-discovery-1`, `pkg-b-pass2-deep-1`
(B.md's own manifest note says this section closes AUDIT-2's missing B
pass-2 record; the battery subsumes the integrated pass-2 correction at
88edf91).

| record | command (as recorded in B.md) | result | time |
|---|---|---|---|
| quick pass 1 | `.venv/bin/python -m pytest tests/properties/test_pkg_b_{enrollment,stockpile,index,episodes,pass2,independent_review}.py tests/properties/test_harness_stages.py --hypothesis-profile=quick -q` | 21 passed, exit 0 | 20.48s |
| quick pass 2 | same command | 21 passed, exit 0 | 20.36s |
| discovery + statistics | same six `test_pkg_b_*.py` files (harness stages omitted), `--hypothesis-profile=discovery --hypothesis-show-statistics -q` | 14 passed, exit 0 | 37.35s |
| deep, bounded serial | same six files, `--hypothesis-profile=deep -q` | 14 passed, exit 0 | 57.62s |

Static checks in the same battery: ruff `--select E4,E7,E9,F` clean on
the five touched/new files; mypy 3 errors, all pre-existing at 132fa87
in files outside B's ownership. The instrumented tie-signature
observation (grid helpers, not committed) stayed inside the allowed
492-decomposition set across all 24 runs.

### Package D — final battery (recorded, NOT integrated)

Source: `investigation/packages/D.md` as of `inv/0f462p/d-final-check`
battery commit e5d4dd4, read via `git show` (the branch is NOT merged
into this tree; independent review is pending). Tested code state:
cc4945d on `inv/0f462p/d-final-check`. Recorded as
`pkg-d-pass2-quick-1`, `pkg-d-pass2-quick-2`, `pkg-d-pass2-discovery-1`
— each record names the d-final-check revision and says the run was
executed on the d-final-check worktree, NOT on this integrated branch.
No record claims integration.

| record | command (D.md "complete D" battery convention: the four explicit files under tests/properties/, `uv run --offline --extra dev`) | result | time |
|---|---|---|---|
| quick pass 1 | `timeout 300 uv run --offline --extra dev pytest tests/properties/test_pkg_d_age_days.py tests/properties/test_pkg_d_config_sharing.py tests/properties/test_pkg_d_denominators.py tests/properties/test_pkg_d_optional_stages.py --hypothesis-profile=quick -q` | 46 passed, exit 0 | 119.56s |
| quick pass 2 | same command | 46 passed, exit 0 | 121.08s |
| discovery + statistics | same four files, `timeout 900 ... --hypothesis-profile=discovery --hypothesis-show-statistics -q --tb=short` | 46 passed, exit 0 | 593.40s |

Battery ancestry preserved in the records: earlier rounds at
af8021a/f1eb9c4 surfaced two counterexamples (distindex rxdate, covariates
DISTINCT — diagnosed and fixed) and one labs float-order boundary, fixed
by the order-immune exact-decimal oracle mean at cc4945d (labs-case
discovery re-run 1 passed, 12.6s at cc4945d parent f1eb9c4). Static:
ruff clean; mypy 3 errors, all pre-existing outside D's ownership.

## Honesty state

- Package B: status `pass2-complete`, head 4891dfa (b-final, integrated
  into base 3709ab2).
- Package D: status `pass2-complete` (the pass-2 WORK is complete and
  recorded), head cc4945d on `inv/0f462p/d-final-check` with an explicit
  NOT-merged / review-pending marker. The preserved executable-red test
  `tests/properties/test_pkg_d_optional_stages.py::test_optional_stage_matches_reference[attrition_person_time]`
  (verified red at d5fb14b on THIS branch) is reconciled on
  d-final-check (final battery 46 passed, exit 0) but remains
  executable-red here until the parent merges D final; the manifest
  open question says exactly that.
- Every historical record and calibration entry preserved verbatim; no
  policy changes; no invented evidence.

## Stale strict-xfail B2 witness repairs

The pass-1 strict-xfail pin `test_enrollment_overlapping_raw_spans_known_divergence`
no longer exists — it was replaced at B pass 2 (88edf91; b-final head
4891dfa) by the positive parity pins:

- `tests/properties/test_pkg_b_enrollment.py::test_enrollment_overlapping_raw_spans_parity`
  (explicit B2 witness asserts: one dispensing, supply 46 > 30,
  numdispensing 2 > 1 — witness, not endorsement)
- `tests/properties/test_pkg_b_stockpile.py::test_stockpile_fanout_parity_on_overlapping_spans`

Repaired locations: manifest `findings[]` B2 `witness` and the B package
`notes`; ARBITRATION.md B2 rulings-table cell, a witness repair note in
the B2 outcome section, and a historical note on the wave-1 branch
inventory. The B2 ruling itself is unchanged (doc-only, SAS inherited,
QA-expected).

## Unresolved leads now recorded (follow_up_leads)

1. D production lead: `lab_summary.mean_result` computes
   `round(avg(result_num), 3)` over DOUBLEs — float sum order/ulp can
   land on either side of an exact .0005 rounding boundary (observed
   once: true exact mean 134.0825, production 134.083 vs oracle 134.082
   purely by summation order; no semantic defect on either side). The D
   oracle is order-immune (exact decimals) at cc4945d; the production
   DOUBLE mean is unchanged; a DECIMAL-based mean would be deterministic
   and SAS-decimal-exact. Classify future boundary draws by recomputing
   the exact decimal mean first.
2. D parity nuance (UNVERIFIED, asserted neither way): 80_covariates.sql's
   `clipped` CTE is SELECT DISTINCT — a duplicate dispensing with the
   same shape contributes once BEFORE the same-day sum. The shipped SQL
   documents DISTINCT-before-sum as an output-preserving performance
   rewrite, NOT a SAS contract statement; whether SAS's stockpile
   dedupes identical dispensing rows before chaining is unverified.
3. B legacy overlap exact-characterization caveat: the legacy generated
   row-for-row episode checks and deterministic @examples in
   test_pkg_b_episodes.py characterize the OBSERVED DuckDB 2.0 threads=1
   span-major ordering only (model reproduces it by construction); they
   can fail on legal engine drift and are not independent correctness
   evidence for the overlap domain. Non-span-major decompositions
   reproduce under input permutation at threads=1 and run-varying
   decompositions at threads=4. The durable order-independent evidence
   is test_pkg_b_independent_review.py; the production-side thread
   remains lead L-B3.

## Validator evidence (actual runs, this worktree)

- Baseline before edits: `pytest tests/properties/test_campaign_manifest_complete.py
  tests/properties/test_finding_evidence_complete.py` → 66 passed,
  1 failed — the strict completion tier listing exactly the missing B/D
  pass-2 records (the validator's own documented expected failure).
- After the edits: both files green (66 + 1 = 67 passed, 0 failed),
  including the strict completion tier and the committed-verdict
  coverage over the 6/6 calibration ids. Commands and exit codes are in
  the commit message.

## What remains for the parent

- Merge `inv/0f462p/d-final-check` (cc4945d, battery record e5d4dd4)
  after its independent review completes; this worktree deliberately
  did not merge, rebase, stash, reset, clean, or publish anything.
- After the merge, the executable-red attrition_person_time divergence
  on this tree resolves; the manifest open question tracks it.
