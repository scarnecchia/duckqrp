# Package B — Enrollment, stockpiling, index dates, episodes

Branch: `inv/0f462p/b-enrollment-stockpiling` (base a0d0b93). Worker B.
Private, local-only; nothing pushed.

## Scope

AC.3 correctness for the stages between normalize and the master list:
enrollment episode construction (20_enrollment.sql), exposure extraction
and stockpiling (30_exposure.sql), index detection (40_index.sql), POV1
(45_pov1.sql), episode chaining / episode_criteria / ptsmasterlist
(50_episodes.sql), plus the macros they lean on (00_macros.sql) and the
normalize contract they read (10_normalize.sql).

## Files read

- `tests/properties/`: synth.py, harness.py, strategies.py, conftest.py,
  test_harness_stages.py (conventions), test_ac1_infrastructure.py (skim)
- `tests/_stockpile_reference.py` (audit, see below)
- `src/qrp/sql/`: 00_macros.sql, 10_normalize.sql, 20_enrollment.sql,
  30_exposure.sql, 40_index.sql, 45_pov1.sql, 50_episodes.sql
- `src/qrp/config.py`: CohortConfig, StockpilingRule,
  parse_stock_range, StudyConfig.validate, widest_lookback_days,
  effective_censor_date, AgeStrata defaults
- `src/qrp/pipeline.py`: register_config (cfg_* tables incl.
  cfg_stockpiling(_ranges), cfg_codes stockgroup `_default`),
  `_enr_cfg_id` format `coverage|enrol_gap|chart`
- `docs/SAS_PARITY.md`: stockpiling recurrence, enrollment break,
  episode gap type P, age yrdif — the contracts the oracles encode
- probes: `investigation/probes/b_overlap_probe.py` (finding evidence)

## Oracle-independence statement

All oracles are pure Python re-derivations of the DOCUMENTED contracts,
never translations of the SQL:

- Enrollment (`oracles/b_enrollment_model.py`): sorted interval scan
  with a running episode accumulator (merge while blank gap <= enrol_gap
  or spans overlap; rupture on coverage-flag change; chart-required
  patient drop; coverage filter). The shipped SQL breaks each span
  against the immediately PREVIOUS span via `lag()` and numbers islands
  with a running sum — a different mechanism.
- Stockpiling (`oracles/b_stockpile_model.py`): claim-at-a-time loop
  s(i)=max(a(i), e(i-1)+1) plus PERCENTDAYS push/truncate sequences,
  same-day collapse (sum/min/max/mean, mean rounded half-away-from-zero
  as probed on DuckDB), SUPRANGE/AMTRANGE on shaved values, and the two
  enrollment shaves. The shipped SQL solves the recurrence in closed
  form (cumulative supply + running anchor max over window functions).
- Index/POV1 (same module): dedupe per patient-day keeping the longest
  supply (documented nodupkey order), then a sequential washout scan
  over one sorted list (SQL: window running maximum); demographics
  tie-break, duplicate-demographic exclusion, age years + SAS yrdif
  (leap-day aware) re-derived from the documented SAS intent.
- Episodes (`oracles/b_episode_model.py`): sequential chain scan with
  running runout (SQL: gap-and-islands windows), point mode, criteria
  flags re-derived from the SQL's documented SAS mappings.

`tests/_stockpile_reference.py` AUDIT VERDICT: genuinely independent —
it is a line-by-line transliteration of the SAS macro
ms_stockpiling.sas (docstring states this; structure differs from both
the SQL closed form and my loop). REUSED ONLY as a second oracle for the
always-push recurrence core (`test_stockpile_sequential_recurrence_matches_sas_reference`),
NOT for PERCENTDAYS truncation, where it diverges from the shipped SQL
by design: the reference keeps a 0-day truncated record (sup=0) where
the SQL drops records trimmed to nothing (`WHERE ... OR date_diff(...)+1 > 0`).
My own truncation model follows the SQL's documented drop rule and
matched the SQL on all percentdays cases. Supplemented with hand
examples (overlap pushes, exact duplicates).

## Properties

| test | contract | oracle |
|---|---|---|
| `test_enrollment_matches_day_model` | gap-threshold merge/rupture, coverage modes MD/M/D, chart-required drop, coverage-flag (Y/A) rupture | day-model accumulator (`b_enrollment_model.enrollment_episodes`) |
| `test_stockpile_matches_sequential_model` | shave -> ranges -> sameday ops -> sequential push (incl. PERCENTDAYS) -> reshave; dates AND payloads | sequential recurrence (`b_stockpile_model.model_stockpiled`) |
| `test_stockpile_sequential_recurrence_matches_sas_reference` | same recurrence, cross-checked against the audited SAS transliteration | my loop == `_stockpile_reference.stockpile` |
| `test_index_matches_prior_claim_scan` | enrollment-bounded candidates, longest-supply dedupe, washout scan; full pov1 payload incl. age/deathdt | prior-claim scan + demographics/age model (`model_pov1`) |
| `test_episode_selection_matches_model` | all supported modes (chain F/P/NULL gap, exp_ext_per, max_epis_dur, flag criteria, point) ; payloads, flags, multiplicity | chain scan + criteria model (`model_episodes`, `model_episode_criteria`) |
| `test_index_washout_boundary_is_exact` | blank gap == wash_per keeps, one less blocks | hand calculation + model |
| `test_enrollment_chart_required_drops_nonchart_patients` | chart restriction drops the whole patient | hand + model |
| `test_enrollment_overlapping_raw_spans_known_divergence` | xfail(strict) documents lead B1/B2 below | accumulator vs shipped SQL |

## Interaction cases (deterministic @example)

- Gap threshold all three sides in one run (gap−1/gap/gap+1): enrollment
  @example 1 (patients 1–2, shapes gap_minus/gap_exact/gap_plus).
- Same-day dispensing pairs flipping selection on amount/supply ties:
  stockpile @example 1 (twin claims); episodes @example 1 (16+30 supply
  sum chains a fill a tie-break defect would orphan).
- Death before/inside/after candidate index and episode windows: index
  @example 1 (days 30/53/90 vs fill day 50) and episodes @example 4.
- Duplicate demographics excluded from POV1: index @example 2
  (`dup_demog` knob) — oracle reproduces the exclusion.
- Leap-day birthdays and leap Februaries in enrollment/index/episode
  arithmetic: index @example 3 (born 2000-02-29, index ON 2012-02-29),
  enrollment @example 5 (leap February inside a span), episodes
  @example 6 (episode spanning 2012-02-29).
- Coverage-flag rupture and the A-flag boundary; stockgroup
  independence; mean-supply rounding (16+7 -> 12); PERCENTDAYS exact
  boundary (stockpile @examples).

## Calibration record (seeded defect, REVERTED, never committed)

- Patch: `src/qrp/sql/20_enrollment.sql` line 67,
  `... - 1 > enrol_gap THEN 1` -> `... - 1 >= enrol_gap THEN 1`
  (gap-threshold off-by-one: spans exactly at the threshold wrongly
  rupture).
- Command: `python -m pytest
  tests/properties/test_pkg_b_enrollment.py::test_enrollment_matches_day_model
  --hypothesis-profile=quick`
- Result: FAILED in 0.39s on the gap-threshold interaction @example, for
  exactly the expected reason:
  `AssertionError: enrollment_spans: SQL returned 4 rows, model 3`
  — patient 2's spans (adjacent + gap_exact + gap_plus, enrol_gap=1)
  split into three episodes (…17, 19–28, 31–…) where the model (and
  correct SQL) merges the first pair (…28). Detection chain complete:
  boundary generated -> rupture activates -> enrollment output differs
  -> oracle assertion fires. No witness file written (explicit-example
  failure; conftest writes witnesses for shrunk falsifying examples).
- Revert: `git checkout -- src/qrp/sql/20_enrollment.sql`; clean tree
  re-verified: `2 passed, 1 xfailed in 1.06s`.

## Pass records (command / revision / result / duration)

All on branch `inv/0f462p/b-enrollment-stockpiling`, working tree clean
of src/ changes:

1. `.venv/bin/python -m pytest tests/properties/test_pkg_b_enrollment.py
   tests/properties/test_pkg_b_stockpile.py tests/properties/test_pkg_b_index.py
   tests/properties/test_pkg_b_episodes.py tests/properties/test_harness_stages.py
   --hypothesis-profile=quick` -> 14 passed, 1 xfailed, 7.41s
2. Discovery statistics run (cheapest family + enrollment):
   `... test_pkg_b_stockpile.py::test_stockpile_sequential_recurrence_matches_sas_reference
   tests/properties/test_pkg_b_enrollment.py --hypothesis-profile=discovery
   -q --hypothesis-show-statistics` -> 3 passed, 1 xfailed, 3.78s.
   Crosscheck: 500/500 passing, 88.2% overlapping-pair cases.
   Enrollment (expensive budget 50): 50 passing / 0 failing / 2 invalid,
   ~55 ms/example; events: nonempty output 96.15%, overlapping raw spans
   69.23%, coverage-flag variety 59.62%, gap-threshold boundary 32.69%,
   coverage MD/M/D = 21/33/42%, enrol_gap 0/1/2/3 = 31/40/10/15%,
   wash_per 0/30/183/None = 23/19/23/31%.
3. `python -m pytest tests/test_rewrites.py tests/test_determinism.py`
   -> 15 passed, 2.36s
4. `python -m ruff check --select E4,E7,E9,F tests/properties` -> clean
5. `python -m mypy src tests/properties` -> clean (36 files)

## Findings / leads

### B2 (finding, CONFIRMED): dispensings double-counted when enrollment output overlaps
- Reproduce: `investigation/probes/b_overlap_probe.py`
  (`python investigation/probes/b_overlap_probe.py` with repo root on
  sys.path; output transcribed below).
- Raw spans for one patient: A=[2011-01-01, 2011-12-31],
  B=[2011-02-15, 2011-06-15], C=[2011-09-01, 2011-09-30], enrol_gap=0.
- Expected (any sane reading; day-model): ONE enrollment episode
  [2011-01-01..2011-12-31]; the single 30-day/10.0 fill on 2011-09-15
  stockpiles to ONE row, numdispensing=1, rxsup=30.
- Actual: `enrollment_spans` emits TWO overlapping episodes
  ([01-01..12-31] and [09-01..09-30]); `stockpiled` emits TWO rows for
  the one fill, EACH with numdispensing=2 (supplies 16 and 46 days,
  amounts 5.33 and 15.33); `index_candidates` carries the inflated
  payload (rxsup=46, numdispensing=2).
- Mechanism: the break test compares each span to the immediately
  previous span's end (`lag(enr_end)`), so C breaks from B (77 blank
  days) while still lying inside A; the output episodes then overlap.
  The stockpile pre-shave and re-shave JOINs (`adate BETWEEN enr_start
  AND enr_end`) fan out per containing span, duplicating the claim and
  double-prorating its amount.
- Suspected contract: one dispensing is one dispensing — numdispensing
  counts claims and supply cannot exceed what was dispensed; enrollment
  episodes are maximal continuous intervals.
- Severity: medium (silent numeric inflation of supply/amount/count
  downstream into index dates, episode_rxsup -> min_days_supp
  eligibility; requires overlapping raw enrollment rows, which messy
  SCDM extracts do contain — the same motivation as the demographics
  dedup). Not a crash; correctness only.
- Witness in suite: strict-xfail
  `test_enrollment_overlapping_raw_spans_known_divergence` (fails the
  day the SQL is fixed, prompting removal of the marker).

### B1 (lead, AMBIGUITY): overlapping enrollment episodes may be SAS parity
- docs/SAS_PARITY.md documents the break formula as SAS's
  `ENRSTART - lag(ENREND) - 1 > ENROLGAP`, i.e. SAS also breaks against
  the immediately previous span. SAS may therefore emit the same
  overlapping episodes on such input. The segmentation itself is hence a
  parity AMBIGUITY, not a demonstrated implementation bug; B2's
  double-count is a demonstrated internal-arithmetic violation either
  way. If the segmentation is intended parity, the fix for B2 belongs in
  the shave joins (shave to exactly one containing span), not in
  20_enrollment.sql.

## Coverage stats summary

See pass record 2 for the discovery event distribution. Quick-profile
properties additionally assert hand examples for every mandatory
interaction case listed above. Mode coverage: stockpiling sup_op/amt_op
sum|min|max|mean, SUPRANGE/AMTRANGE inclusive/exclusive pieces,
PERCENTDAYS {None, 0.0, 0.25, 0.5, 1.0}, shared/per_code stockgroups;
episodes chain F (0/2), P (50%), NULL gap, exp_ext_per, max_epis_dur,
flag criteria (min_days_supp/min_epis_dur/req_days_aft_ind), point
mode — all eight `_MODES` entries generated + example-pinned.

## Open questions for the orchestrator

1. B1: is the lag-break enrollment segmentation intended SAS parity?
   If yes, package the B2 fix as a shave-join dedup; if no, fix the
   break to compare against the running episode end.
2. Should `enrollment_spans` output carry a data-quality guard
   (non-overlap assertion) for this input family?
3. Episodes chaining tie-order is unspecified for rows equal on
   (adate, expiredt, rxsup) from DIFFERENT stockgroups with different
   containing spans (`claim_enr_start` break can depend on arbitrary
   order). Not exercised here (generated patients have single spans);
   worth a targeted property if multi-span + multi-stockgroup studies
   matter.
4. My oracles cover plain studies only (no covariates/inclusions/dose/
   FUT truncation codes; guard raises otherwise). FUT truncation
   stockpiling (trunc_claims) is untested in this package.
5. Encounter-derived deaths (discharge_status 'EX') and month-unit age
   strata are not modeled (death-table deaths and year strata only).

## Incomplete / limitations

- `generate()` itself never produces multi-span patients, so the
  generated strategies build multi-span enrollment directly from spec
  knobs (`b_pkg_fixtures.spec_strategy`) rather than via
  `datasets()`; single-span generator coverage is inherited from the
  shared infra.
- The 2 "invalid" enrollment examples under discovery are Hypothesis
  duplicate/replayed draws, not filters (no assume() in the suite).
