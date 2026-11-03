# Final validation — qrp-duckdb property campaign

**PRIVATE — NOT AUTHORIZED FOR PUBLICATION.**

State: discovery `31846dc` (final integrated campaign state). Report
date: 2026-11-02. Worker: closing-records, worktree
`wt/closing-records`.

**Status: final validation COMPLETE with known baseline residue.**
Both post-integration runs executed at discovery `31846dc`; the
campaign is implemented and validated with its recorded pre-existing
baseline failures. Scope: property-based correctness on synthetic
bounded inputs against independent oracles and SAS citations — **no
universal correctness claim and no universal SAS-parity claim** is
made or implied.

| run | command | result | state |
|---|---|---|---|
| final full properties, quick | `python -m pytest tests/properties --hypothesis-profile=quick -q` at 31846dc | exit 0; 424 passed, 13 skipped, 1 xfailed, 13 warnings, 299.29s | COMPLETE (log: session `final-properties.log`) |
| original suites | `python -m pytest --ignore=tests/properties -q` at 31846dc | exit 1; 373 passed, 2 failed, 3 skipped, 437 warnings, 464.08s | COMPLETE — baseline-matched: the 2 failures are exactly the pre-existing spill-attribution OOM tests; zero new failures (log: session `final-existing.log`) |

**How the plan's "full pytest after integration" was satisfied:** as
two disjoint pytest selections — `tests/properties` and
`--ignore=tests/properties` — which together partition the entire test
suite (every test file belongs to exactly one selection). They ran
serially, one after the other, on the shared machine; this was a split
serial validation, not one full-suite command.

The 13 skips are the opt-in replay wrappers (the mutation-replay module
runs 1 + skips 6; the finding-replay module skips 7); the replays
themselves were executed separately (sections below). The 1 xfailed is
the suite's single recorded xfail pin.

## Acceptance criteria AC.1–AC.9

| AC | scope | state | evidence |
|---|---|---|---|
| AC.1 | hypothesis dev extra, profiles, isolated harness | satisfied | infra complete (d4ea62e → 387f94b); `test_ac1_infrastructure` in the green 424 |
| AC.2 | config/input/SQL-split/SCDM semantics | satisfied | package A pass2-complete (f5a2a85); A.md pass 2 |
| AC.3 | enrollment/stockpiling/index/episodes vs independent models | satisfied, with a recorded ordering caveat | package B pass2-complete (4891dfa); B.md pass 3; see limitations below |
| AC.4 | inclusion/dose/censoring/follow-up vs window models | satisfied | package C pass2-complete (8645179; pass-2 at 3dfe463/d3b6043); C-1 reclassified → DB-5 open |
| AC.5 | denominator day enumeration, optional stages, config sharing | satisfied | package D pass2-complete (cc4945d, integrated at 5d0ac0a after independent review APPROVE); D.md final battery |
| AC.6 | lifecycle/cancellation/faults/output honesty/CLI+TUI | satisfied | package E pass2-complete (0b391e5; pass-2 6bc4171/96204bd); E-3/E-4 → DB-3/DB-4 open |
| AC.7 | comparator corruption detection, equivalence acceptance, metamorphic runs | satisfied | package F pass2-complete (c4b61a5; pass-2 6aaba22); F1/F2 fixed via arb2 |
| AC.8 | two recorded passes per package; killed meaningful mutations; no mutation left integrated | satisfied | manifest validator green incl. strict completion tier; 6 calibration groups with committed genuine red→green verdicts (`investigation/audit-replays/replay-verdicts.json`); FINAL-AUDIT independently re-executed all 6 |
| AC.9 | every confirmed defect has a standalone minimal patch with reproducible red/green; all findings classified | satisfied | 7 replay groups over 8 fixed findings (`investigation/finding-replay-records.json`, committed logs + verdicts); 7 findings without fixes documented separately (B2, A-3, D2, C-1, E-3, E-4, D1) |

Static gates on the merged tree (checked in this worktree after the
merge, campaign venv): `ruff check --select E4,E7,E9,F tests/properties`
clean; `mypy src tests/properties` — "Success: no issues found in 83
source files" (re-verified by the parent after the final runs), and the
parent's `git diff --check` clean.

Lint scope, stated plainly: the campaign's ruff gate is the
`--select E4,E7,E9,F` subset named in README's Commands section — it is
NOT the full lint. Running ruff 0.17.3's full default ruleset reports
pre-existing expanded-defaults findings (about 100 in `src/`), and in
campaign-owned files exactly one: **PYI034** at
`tests/properties/harness.py:179` (`__enter__` should return `Self`;
the explicit `StageHarness` return annotation conflicts with the repo's
`mypy` `python_version = "3.10"` target, which predates `typing.Self`).
Verified live in this worktree; recorded as known version drift, not a
campaign regression, and not fixed here.

## Fixed findings: eight, across seven branches

| finding | branch | red / green | regression |
|---|---|---|---|
| A-1 duplicate cohortgrp | inv/0f462p/fix-a1 | f971710 / 5bbc4ff | tests/test_regression_a1.py |
| A-2 optional rxamt | inv/0f462p/fix-a2 | 1577f83 / 564eb7a | tests/test_regression_a2.py |
| A-4 eighteen optional columns | inv/0f462p/fix3-a4-complete | 8bc521e / a6a74e4 (final f1dca19) | tests/test_optional_claim_columns.py |
| C-3 person_days floor | inv/0f462p/fix3-c3 | 26ddf87 / 1a810a2 | tests/test_regression_c3.py |
| E-1 + E-2 engine leaks (one branch) | inv/0f462p/fix3-e1 | 323b231 / 0a26573 | tests/test_regression_e1.py |
| F1 attrition forward-fill | inv/0f462p/fix2-f1 | 0bcb1a8 / 3001086 | tests/test_regression_f1.py |
| F2 denominator band split | inv/0f462p/fix2-f2 | 0e7eeee / 37e3d89 | tests/test_regression_f2.py |

Seven replay groups cover these eight findings (E-1 and E-2 share the
branch and the group). Each group's standalone regression failed at the
red revision for the recorded reason and passed at every later
revision; per-revision logs and verdicts are committed under
investigation/replays/.

## Mutation replay: six calibration groups

`a-1`, `b-1`, `c-1`, `e-1`, `f-1` (recorded/reconstructed seeded
defects, executed red→green in the campaign venv on 2026-11-02) and
`d-1-pass2-20261102` (offline replay of the D wash calibration). All
six carry genuine machine verdicts; the FINAL-AUDIT worker
independently re-executed all six (6/6 red→green, disposable worktrees
removed). No seeded mutation remains integrated.

## Unresolved decision briefs (no code by charter)

- **DB-2** (D1): CIDA denominators omit SAS's inclusion-window and dose
  restrictions under excl_incl=Y; INC-condition studies overstate
  member-days. Implement/document decision open.
- **DB-3** (E-3): SIGINT mid-run has no terminal contract (exit code,
  diagnostics, log record, manifest). Options drafted.
- **DB-4** (E-4): failure-path RunFinished ownership is unspecified;
  CLI/UI parity holds for successes only.
- **DB-5** (C-1): SAS re-checks length criteria on the post-truncation
  episode; this implementation does not. Magnitude unmeasured — needs a
  witness study first (B2-standard evidence).

## Recorded limitations from the final passes

- **D lab DOUBLE mean rounding.** `lab_summary.mean_result` rounds
  `avg(result_num)` over DOUBLEs; float sum order can flip an exact
  .0005 boundary (observed once: true mean 134.0825, production 134.083
  vs oracle 134.082 by summation order alone). Unresolved in
  production. A DECIMAL-based mean is proposed for reproducibility
  (deterministic, order-immune summation); SAS exactness of decimal
  arithmetic is unverified and not claimed — an earlier
  "SAS-decimal-exact" phrasing in the campaign record is corrected.
- **D RX DISTINCT parity nuance.** 80_covariates.sql's `clipped` CTE is
  SELECT DISTINCT, so an identical duplicate dispensing contributes once
  before the same-day sum. The shipped SQL calls this an
  output-preserving performance rewrite, not a SAS contract statement;
  whether SAS dedupes identical dispensing rows before chaining is
  unverified and asserted neither way.
- **B legacy overlap exact-checks scope.** The legacy row-for-row
  episode checks and deterministic @examples in
  test_pkg_b_episodes.py (overlap fan-out 3.77% of discovery draws)
  characterize the OBSERVED DuckDB 2.0 threads=1 span-major ordering
  only — the model reproduces that ordering by construction, so these
  checks can fail on legal engine drift and are not independent
  correctness evidence for the overlap domain. Non-span-major
  decompositions occur under input permutation at threads=1 and
  run-varying decompositions at threads=4. The durable
  order-independent overlap evidence is
  test_pkg_b_independent_review.py; the production-side thread is lead
  L-B3 (chaining-window ORDER BY is not a total order under fan-out
  ties; not authorized to fix). Post-campaign SCDM 8.2.2 alignment
  review (2026-11-03, run `scdm-b2-alignment-1`): trigger scoped to
  RUPTURING overlaps — the SCDM's only sanctioned overlap form
  (plan/payer-only duplicates) is unrepresentable here and its
  representable residue, exact duplicate rows, was execution-verified
  to collapse to one episode with no fan-out; see the addendum in the
  B2 finding document.
- **D approval limits.** The integration review of D final e5d4dd4
  returned APPROVE (per the parent's integration record; no separate
  review document was committed). Limits, from the committed record:
  the suite pins shipped stages' documented behavior from documented
  inputs; year-band integration claims hold only for contiguous
  open-top bands (finite/gapped bands ambiguous); lab_results/mfu
  nonempty asserts cover only documented-evidence cases; the production
  DOUBLE mean and the DISTINCT parity question are outside what the
  approval asserts.

## Baseline residue (pre-existing, not campaign findings)

The recorded pre-campaign baseline (`python -m pytest -q` at c3e60db):
337 passed, 2 failed, 3 skipped, 433s. The final post-integration run
of the same suites at 31846dc: 373 passed, 2 failed, 3 skipped, 437
warnings, 464.08s (exit 1). The +36 passing tests are the
campaign-integrated standalone regression and pin tests. The residue is
unchanged:

- **2 failures, both deterministic** (reproduced in the operator's own
  venv on a quiet machine at baseline, and reproduced again in the
  final run — log lines 52/98), so not load flakes and not campaign
  artifacts: `tests/test_pipeline.py::test_spilling_is_attributed_to_the_statement_that_caused_it`
  and `tests/test_pipeline.py::test_spill_summary_names_the_statement_in_the_log`
  — `_duckdb.OutOfMemoryException` ("failed to pin block of size
  256.0 KiB") inside the tests' forced ~286 MiB DuckDB limit (286.2 /
  286.4 of 286.1 MiB used); the spill path is never attributed.
  Manifest record: `orchestrator-final-existing-suites-1`, result
  `confirmed` — the run FAILED (exit 1) and is recorded as failed,
  never relabeled; "confirmed" means the documented pre-existing
  baseline failure state reproduced exactly, with zero new failures.
- **3 environmental skips** (count unchanged from baseline; the
  documented reasons): production input file unavailable
  (test_pipeline.py:6050); 2m dataset not generated (test_ui.py:137,
  test_ui.py:330).

Skip/xfail meanings, kept explicit: the properties run's 13 skips are
the opt-in replay wrappers (six mutation-replay, seven finding-replay;
the replays themselves were executed separately — AC.8/AC.9 evidence
above); the properties run's 1 xfailed is the suite's single recorded
xfail characterization pin; the original suites' 3 skips are the
environmental data-availability skips above. None of these is a hidden
failure.

## Close-out

The completion criterion set earlier was met: the original-suites run
finished with only the known residue (same 2 failures, same 3 skips,
zero new failures). Final validation is complete with that recorded
residue. The campaign's deliverables: a property-based correctness
suite with independent oracles, eight fixed findings across seven fix
branches, one inherited defect documented per operator ruling (B2),
four decision briefs awaiting an operator call (DB-2..DB-5), six
mutation-replay calibration groups, and committed machine-verdict
evidence for all of it. What the campaign does NOT claim: universal
correctness of qrp-duckdb, universal SAS parity, or that unmeasured
divergences (C-1 magnitude, D1 impact size) are harmless.

## SCDM alignment review

Follow-up review (2026-11-03), operator-requested: the B2/enrollment
alignment work extended to every synthetic builder, fixture generator,
and oracle in the repo. Record of truth:
[SCDM-ALIGNMENT.md](SCDM-ALIGNMENT.md) — audit matrix (42 items over
the 8 pipeline-read tables), probes, unresolved reference ambiguities,
and the scope evidence. Raw audit reports:
`scdm-audit-notes/audit-{a,b,c,d}-raw.md`. Boundary tests:
`tests/properties/test_scdm_alignment.py`. Machine checks:
`tests/properties/test_scdm_alignment_complete.py`.
Final revision: fedfea3 (test content; the records commits after it
touch investigation/ only).

| run | command | result |
|---|---|---|
| properties, quick | `python -m pytest tests/properties --hypothesis-profile=quick -q` | exit 0; counts recorded in manifest pass `scdm-audit-final-1` |
| original suites | `python -m pytest --ignore=tests/properties -q` at fedfea3 | exit 1; 373 passed, 2 failed, 3 skipped, 437 warnings, 465.76s — the 2 failures are exactly the pre-existing spill-attribution OOM tests; zero new failures vs the recorded baseline; recorded as `scdm-audit-original-1` (result `confirmed`) |
| static gates | `ruff check --select E4,E7,E9,F tests/properties`; `mypy src tests/properties` | ruff clean; mypy clean (83 source files) |

Audit verdicts (42 items): aligned 13 · aligned-with-annotation 12 ·
misaligned-fixed 12 (all applied, tests-side only) · probe-needed 5
(four to decision briefs DB-11/DB-12, one to an executed boundary
test).

| table | end state |
|---|---|
| enrollment | coverage flags SCDM-legal everywhere (`_FLAG_POOL` draws 7 legal MedCov/DrugCov pairs; generator spans Y/Y/Y with variety owned by package B); span-date and chart semantics annotated |
| demographic | all four Sex values and all seven Race values drawn; Hispanic N/Y/U; null Birth_Date supported as a legal shape (pipeline drops it at attrition level 1); duplicate rows labeled as DEM unique-row violations |
| dispensing | all drawn fields in domain; codetype equality pinned by a boundary test; ProviderID inert (annotated) |
| diagnosis | full EncType domain including IS; Pdx populated only on IP/IS claims; IP-stay enveloping probes labeled; the 'XX' fallback documented as pipeline-internal, never a data value |
| death | two-column subset equals the pipeline read set; the rules the subset can express hold by construction and are re-guarded by normalize |
| procedure | probe rows field-legal; the enveloping rewrite is pinned for claims inside IP stays |
| encounter | IP/IS/AV drawn with ENC-legal dates; discharge_status stays minimal (the EX→death derivation is pinned in tests/test_pipeline.py, including lowercase 'ex' and missing ddate); EncounterID uniqueness caveat recorded unresolved |
| lab_result | Result_Type 'N'/'C' triples legal ('U' belongs to UNMAPPED rows only and is never drawn); unit only on numeric rows |

Boundary behaviors pinned (8 deterministic tests, SQL == expectation,
SCDM citation in each docstring): sex U/A eligibility with 'O'
collapse and pov1-only exclusion; race 'M' eligibility; null
Birth_Date drop at attrition level 1; IS counted in `enc_is` and the
'**' distindex expansion while IS days stay non-enveloping;
pdx-by-enctype matching under restricted care settings; the 'XX'
event/distindex asymmetry; alternate Rx codetype capture; procedure
enveloping inside IP stays.

Oracle state: `expected_utilization` reads the normalized dispensing
columns and models 74_utilization.sql's diagnosis-claims fallback for
empty encounter data; `expected_lab_summary` emits NULL aggregates for
character-only groups; `expected_denomcounts` emits the collapsed sex
covariate (the raw value governs eligibility). The d-package
utilization fixtures carry real cohortgrps, so MED/DRUG counts compare
live values against the oracles.

Findings: every finding document carries an SCDM 8.2.2 alignment note
(grounding for A-3 and A-4; reviewed-no-bearing lines elsewhere; B2
via its 2026-11-03 addendum). No finding status changed.

Open operator decisions: DB-6..DB-12 (ARBITRATION.md, wave 4) —
attrition level-1 sex-filter vacuity and blank-string gap; NULL-race →
'M' default; blank-Pdx unreachability for DX claims; NumIS SAS basis;
XX asymmetry parity confirmation; gen_synthetic.py canonical-domain
defects; selfcheck.py doctor Pdx on ambulatory claims.

Limitations: the synthetic extracts are pipeline-relevant-column
subsets, not full SCDM tables; three reference ambiguities stay
unresolved (encounter unique-row key, ImputedRace code-3 text,
Result_Loc nullability); production `src/` and `tools/` untouched —
the diff against base `7165d77` contains only `tests/` and
`investigation/` paths; no SAS-parity claim added or removed.
