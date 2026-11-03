# Final validation — STE rendering

This document is the Simplified Technical English rendering of
FINAL-VALIDATION.md. The two documents carry the same facts, numbers,
SHAs, and limitations.

**PRIVATE — NOT AUTHORIZED FOR PUBLICATION.**

Final revision: fedfea3 (test content). The records commits after
this revision touch investigation/ files only.

## Scope

The campaign covers property-based correctness on synthetic bounded
inputs. It compares the pipeline SQL against independent oracles. It
makes no claim of universal correctness. It makes no claim of
universal SAS parity.

## Final runs

| run | command | result |
|---|---|---|
| final properties, quick | `python -m pytest tests/properties --hypothesis-profile=quick -q` at 31846dc | exit 0. 424 passed, 13 skipped, 1 xfailed, 13 warnings, 299.29s. |
| original suites | `python -m pytest --ignore=tests/properties -q` at 31846dc | exit 1. 373 passed, 2 failed, 3 skipped, 437 warnings, 464.08s. The 2 failures are the known spill-attribution OOM tests. Zero new failures against the recorded baseline. |

The plan asked for one full pytest run. The campaign ran two disjoint
selections. Together the two selections cover every test file. The
runs executed serially.

The 13 skips are the opt-in replay wrappers. The replays themselves ran
separately. The 1 xfail is the single recorded xfail pin. The 3 skips
in the original suites are environmental. One skip needs a production
input file. Two skips need a larger generated dataset.

## Acceptance criteria AC.1 to AC.9

| AC | scope | state | evidence |
|---|---|---|---|
| AC.1 | hypothesis dev extra, profiles, isolated harness | met | infra complete (d4ea62e to 387f94b). test_ac1_infrastructure passed in the 424. |
| AC.2 | config, input, SQL split, SCDM semantics | met | package A pass2-complete (f5a2a85). A.md pass 2. |
| AC.3 | enrollment, stockpiling, index, episodes vs independent models | met, with a recorded ordering caveat | package B pass2-complete (4891dfa). B.md pass 3. See limitations. |
| AC.4 | inclusion, dose, censoring, follow-up vs window models | met | package C pass2-complete (8645179, pass-2 at 3dfe463 and d3b6043). C-1 reclassified. DB-5 stays open. |
| AC.5 | denominator day enumeration, optional stages, config sharing | met | package D pass2-complete (cc4945d, integrated at 5d0ac0a after review APPROVE). D.md final battery. |
| AC.6 | lifecycle, cancellation, faults, output honesty, CLI and TUI | met | package E pass2-complete (0b391e5, pass-2 at 6bc4171 and 96204bd). E-3 and E-4 became DB-3 and DB-4. |
| AC.7 | comparator corruption detection, equivalence acceptance, metamorphic runs | met | package F pass2-complete (c4b61a5, pass-2 at 6aaba22). F1 and F2 fixed via arb2. |
| AC.8 | two recorded passes per package, killed meaningful mutations, no mutation left integrated | met | manifest validator green, including the strict tier. Six calibration groups carry committed red-to-green verdicts in investigation/audit-replays/replay-verdicts.json. FINAL-AUDIT re-executed all six. |
| AC.9 | every confirmed defect has a standalone minimal patch with reproducible red and green, and all findings have a class | met | Seven replay groups cover eight fixed findings. Seven findings carry no fix and have separate records (B2, A-3, D2, C-1, E-3, E-4, D1). |

Static gates at the merged tree: `ruff check --select E4,E7,E9,F
tests/properties` clean. `mypy src tests/properties` reports no issues
in 83 source files. `git diff --check` clean.

Lint scope: the campaign gate is the E4,E7,E9,F subset. The full ruff
default ruleset reports about 100 pre-existing findings in src/. In
campaign-owned files it reports one PYI034 at
tests/properties/harness.py:179. This is known version drift, not a
campaign regression.

## Fixed findings

Eight findings have fixes across seven branches.

| finding | branch | red / green | regression |
|---|---|---|---|
| A-1 duplicate cohortgrp | inv/0f462p/fix-a1 | f971710 / 5bbc4ff | tests/test_regression_a1.py |
| A-2 optional rxamt | inv/0f462p/fix-a2 | 1577f83 / 564eb7a | tests/test_regression_a2.py |
| A-4 eighteen optional columns | inv/0f462p/fix3-a4-complete | 8bc521e / a6a74e4, final f1dca19 | tests/test_optional_claim_columns.py |
| C-3 person_days floor | inv/0f462p/fix3-c3 | 26ddf87 / 1a810a2 | tests/test_regression_c3.py |
| E-1 and E-2 engine leaks, one branch | inv/0f462p/fix3-e1 | 323b231 / 0a26573 | tests/test_regression_e1.py |
| F1 attrition forward-fill | inv/0f462p/fix2-f1 | 0bcb1a8 / 3001086 | tests/test_regression_f1.py |
| F2 denominator band split | inv/0f462p/fix2-f2 | 0e7eeee / 373e89 | tests/test_regression_f2.py |

Each standalone regression failed at its red revision for the recorded
reason. Each passes at every later revision. Logs and verdicts live
under investigation/replays/.

## Mutation replay

Six calibration groups carry genuine machine verdicts. The FINAL-AUDIT
worker re-executed all six. No seeded mutation remains integrated.

## Open decision briefs

- **DB-2 (D1):** CIDA denominators omit the SAS inclusion-window and
  dose restrictions under excl_incl=Y. INC-condition studies overstate
  member-days.
- **DB-3 (E-3):** SIGINT mid-run has no terminal contract.
- **DB-4 (E-4):** the failure path emits no RunFinished event.
- **DB-5 (C-1):** SAS re-checks the length criteria on the truncated
  episode. This implementation does not. The magnitude is unmeasured.

## Recorded limitations

- **D lab DOUBLE mean rounding.** The mean over DOUBLEs can flip an
  exact .0005 boundary by summation order alone. The campaign observed
  one case. A DECIMAL-based mean would fix reproducibility.
- **D RX DISTINCT parity nuance.** The DISTINCT dedupe of identical
  dispensing rows has no verified SAS counterpart. The suite asserts
  neither direction.
- **B legacy overlap exact-checks.** The row-for-row episode checks
  characterize one observed engine ordering. They are not independent
  correctness evidence for the overlap domain. The durable evidence is
  test_pkg_b_independent_review.py.
- **D approval limits.** The D integration review returned APPROVE
  within stated limits. The DOUBLE mean and the DISTINCT question sit
  outside the approval.

## Baseline residue

The pre-campaign baseline at c3e60db reads 337 passed, 2 failed, 3
skipped. The final run reads 373 passed, 2 failed, 3 skipped. The 2
failures are deterministic OOM tests inside a forced 286 MiB DuckDB
limit. The suite records them as confirmed, never relabeled.

## Campaign deliverables

- A property-based correctness suite with independent oracles.
- Eight fixed findings across seven fix branches.
- One inherited defect documented under an operator ruling (B2).
- Four decision briefs that wait for an operator call (DB-2 to DB-5).
- Six mutation-replay calibration groups.
- Committed machine-verdict evidence for all of the above.

The campaign does not claim universal correctness of qrp-duckdb. It
does not claim universal SAS parity. It does not claim that unmeasured
divergences are harmless.

## SCDM alignment review

Date: 2026-11-03. The operator requested this review. It extends the
B2 and enrollment alignment work to every synthetic builder, fixture
generator, and oracle in the repository.

Record of truth: SCDM-ALIGNMENT.md. It holds the audit matrix, the
probes, the unresolved reference ambiguities, and the scope evidence.
The raw audit reports live in scdm-audit-notes/. The boundary tests
live in tests/properties/test_scdm_alignment.py. The machine checks
live in tests/properties/test_scdm_alignment_complete.py.

| run | command | result |
|---|---|---|
| properties, quick | `python -m pytest tests/properties --hypothesis-profile=quick -q` | exit 0. The manifest record scdm-audit-final-1 holds the counts. |
| original suites | `python -m pytest --ignore=tests/properties -q` at fedfea3 | exit 1. 373 passed, 2 failed, 3 skipped, 437 warnings, 465.76s. The 2 failures are the known spill-attribution OOM tests. Zero new failures against the baseline. The record scdm-audit-original-1 carries result confirmed. |
| static gates | `ruff check --select E4,E7,E9,F tests/properties` and `mypy src tests/properties` | both clean. mypy checked 85 source files. |

The audit matrix holds 39 items over the 8 tables the pipeline reads.
Verdicts: aligned 11, aligned-with-annotation 12, misaligned-fixed 11,
probe-needed 5. All misaligned-fixed changes are applied on the test
side. Four probe-needed items became decision briefs DB-11 and DB-12.
One probe-needed item became an executed boundary test.

| table | end state |
|---|---|
| enrollment | every coverage flag is SCDM-legal. _FLAG_POOL draws 7 legal pairs. The generator draws uniform Y/Y/Y spans. Package B owns the flag variety. |
| demographic | the generator draws all four Sex values and all seven Race values. Hispanic draws N, Y, and U. A null Birth_Date is a legal shape. The pipeline drops such a member at attrition level 1. Duplicate rows carry a label that names the DEM unique-row violation. |
| dispensing | every drawn field is in domain. A boundary test pins codetype equality. ProviderID is inert payload and carries an annotation. |
| diagnosis | the generator draws the full EncType domain, including IS. Pdx is populated only on IP and IS claims. The IP-stay enveloping probes carry labels. The 'XX' fallback is pipeline-internal and never a data value. |
| death | the two-column subset equals the pipeline read set. The rules the subset can express hold by construction. |
| procedure | the probe rows are field-legal. A boundary test pins the enveloping rewrite for claims inside IP stays. |
| encounter | the generator draws IP, IS, and AV encounters with ENC-legal dates. Discharge_status stays minimal. The original suite pins the EX-to-death derivation, including lowercase 'ex' and a missing ddate. The EncounterID uniqueness caveat stays unresolved. |
| lab_result | the Result_Type triples are legal. The reference ties 'U' to UNMAPPED rows and does not state the converse. The generator never draws 'U'. Only numeric rows carry a unit. |

Eight deterministic boundary tests pin SCDM-legal behavior. Each test
cites its SCDM reference. The pinned behaviors follow.

- Sex U and A members pass conditionless eligibility. The covariate
  collapses them to 'O'. A sex-restricted study drops them at pov1
  only.
- A race 'M' member passes eligibility end to end.
- A member with a null Birth_Date drops out at attrition level 1. pov1
  excludes the member.
- An IS encounter counts in enc_is. The distindex expansion includes
  the IS entity. IS days stay non-enveloping.
- Restricted care settings ('IS','P') and ('IS','') match only the
  legal claim shapes.
- An 'XX' claim fires as an event under a wildcard care setting. It
  matches no distindex entity. The oracle agrees on both sides.
- A codetype SN fill is captured under an SN study and excluded under
  an ND study.
- A procedure claim inside an IP stay re-files to IP with pdx 'X'. A
  claim outside the stay keeps its own values.

Oracle state: expected_utilization reads the normalized dispensing
columns. It models the diagnosis-claims fallback for empty encounter
data. expected_lab_summary emits NULL aggregates for character-only
groups. expected_denomcounts emits the collapsed sex covariate. The
raw value governs eligibility. The d-package utilization fixtures
carry real cohort names. The MED and DRUG counts compare live values.

Findings: every finding document carries an SCDM 8.2.2 alignment note.
A-3 and A-4 carry data-model grounding. The others carry a
reviewed-no-bearing line. B2 carries its own addendum. No finding
status changed.

Open operator decisions DB-6 to DB-12 live in ARBITRATION.md, wave 4.
The list follows.

- DB-6: the attrition level-1 sex filter is vacuous. The
  missing-demographics check misses blank strings.
- DB-7: a NULL race collapses to 'M' instead of the unknown code '0'.
- DB-8: a blank Pdx is unreachable for diagnosis claims.
- DB-9: the pipeline emits enc_is without a cited SAS basis.
- DB-10: the XX event and distindex asymmetry needs a parity
  confirmation.
- DB-11: gen_synthetic.py emits three out-of-domain value families as
  canonical data.
- DB-12: the doctor extract carries Pdx 'P' on ambulatory claims.

Limitations of this review:

- The synthetic extracts carry only the columns the pipeline reads.
  They are not full SCDM tables.
- Three reference ambiguities stay unresolved: the encounter unique-row
  key, the ImputedRace code-3 text, and the Result_Loc nullability.
- Production src/ and tools/ files are untouched. The diff against
  base 7165d77 contains only tests/ and investigation/ paths.
- The review adds no SAS-parity claim and removes none.
