# Package B — Enrollment, stockpiling, index dates, episodes

> **Pass 1 verification correction (follow-up):** see the section
> "Pass 1 verification correction — exact-duplicate index claims" at the
> bottom. The parity-fan-out oracles described there supersede the
> single-containing-span language in the earlier sections below; those
> sections are kept as written for the pass-1 record.

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
| `test_enrollment_overlapping_raw_spans_parity` | positive pin of the B1/B2 overlap fan-out (replaced the pass-1 strict-xfail `test_enrollment_overlapping_raw_spans_known_divergence` at 88edf91; supports the nonoverlap exact domain + overlap invariants) | lag model vs shipped SQL |

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
- Witness in suite (pass-1 record; superseded at 88edf91): the
  strict-xfail `test_enrollment_overlapping_raw_spans_known_divergence`
  became the positive parity pin
  `test_enrollment_overlapping_raw_spans_parity` plus the stockpile
  fan-out pin and explicit one-claim/46-day/numdispensing-2 witness
  asserts — see the pass 2 section below and
  investigation/findings/B2-overlapping-enrollment-double-count.md.

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

## Pass 1 verification correction — exact-duplicate index claims

**Symptom.** On integrated discovery 98197f2,
`tests/properties/test_pkg_b_index.py::test_index_matches_prior_claim_scan`
failed deterministically on a Hypothesis-saved shrunk example (observed
in a fresh-database full-suite run in the main checkout). Salient shape:
exact same-day duplicates where amount AND supply tie exactly (a
sup=1/amt=0.5 triple plus a day-5 single), twin=False — not the
differing-amount twins the generators build deliberately.

**Reproduction (main-checkout-independent).** My worktree passed: its
example database held different entries. Reproduced by copying the main
checkout's saved Hypothesis examples (READ-ONLY source:
`/home/scarndp/dev/duckqrp/tests/properties/.hypothesis-db/examples/`)
into my worktree's DB and re-running — replay failed in ~1s. Knob-scan
probe (wash_per × span length × single span, `probes/b_exact_dup_probe.py`,
since deleted) did NOT reproduce: the missing dimensions were the
two-span flag-change shape and two patients. The replayed spec:
patient 2 = spans `[("overlap",1,"YY"), ("overlap",2,"AY")]` + the
exact triple on day 0 + one day-5 fill; no death/dup-demographics/leap.

**Root cause.** Failure site was MY oracle:
`b_enrollment_model.containing_episode` raised
"day model produced overlapping episodes" while pre-shaving patient 2's
day-8 (day 5) fill. Two compounding facts:

1. The pass-1 oracle docstring claimed the accumulator "cannot produce
   overlapping episodes" — WRONG. A coverage-flag rupture emits the
   accumulated episode even when a later span reaches back inside it
   (spans [0,10]=YY, [5,8]=NY, [7,9]=YY under MD emit [0,10], [5,8],
   [7,9]). The SQL's lag-based segmentation emits the same overlapping
   segments on this input, so both models agree on the segmentation —
   my belief was the only wrong part.
2. Given overlapping output episodes, the shipped SQL — per the
   OPERATOR RULING on B2 (arb branch: "inherited defect, parity kept,
   no fix branch"; fix-b2 red f4d2e6a / green c87dc0b was merged and
   then reverted) — fans each claim out PER CONTAINING SPAN at every
   `enrollment_spans` join: the stockpile pre-shave and re-shave
   (30_exposure.sql), POV1 pass 2 (45_pov1.sql), the episodes `located`
   LEFT JOIN (50_episodes.sql). The pass-1 oracle asserted at most ONE
   containing span and refused to model the arbitrated behavior.

**Classification: (a) oracle bug.** The oracle predated the arbitration
and encoded the one-span semantics that the operator explicitly ruled
against. NOT (b): production behaves exactly as the ruling requires and
the parity case is documented (40_index.sql's dedupe QUALIFY has a
total-order tie-break and its SEMI JOIN is fan-out immune; the SQL
comments promise deterministic tie-breaking, which exact-duplicate
payloads satisfy trivially — any survivor is interchangeable). NOT (c):
fix-a2 keeps present-column rxamt semantics bit-for-bit (`IS NOT NULL
AND rxamt > 0`, ms_cidanum.sas:617 rule); amt=0.5 is untouched by it.

**Fix (oracle only; production untouched).**
- `containing_episode` (single hit + raising guard) replaced by
  `containing_episodes` (ALL hits), with the parity rationale recorded.
- Pre-shave and re-shave: one shaved/stockpiled row PER (claim ×
  containing episode), matching the SQL JOIN fan-out (the same-day
  collapse then sums the copies; numdispensing counts fan-out copies —
  the arbitrated inflation).
- `model_index_dates`: the dedupe SEMI JOIN is any-hit (fan-out immune).
- `model_pov1`: one pov1 row PER containing span of the index date
  (pass-2 JOIN).
- `model_episodes` `located`: chain rows fan out per containing span
  (each copy carrying that span's start; the span-change break then
  separates them); LEFT-JOIN NULL span preserved.
- `model_episode_criteria`: pov1 × episodes is a CARTESIAN over all
  episodes starting on the index date (both sides fan out); the former
  "two episodes start on one index date" assertion removed.

**Pins added.**
- `test_index_matches_prior_claim_scan` @examples: the replayed shape
  (exact TRIPLE on day 0 + day-5 fill, overlapping flag-rupture spans)
  and an exact QUADRUPLE variant.
- `test_stockpile_fanout_parity_on_overlapping_spans`: deterministic
  fan-out parity pin — flag-rupture overlap, one 30-day fill inside
  both episodes → two stockpiled rows, numdispensing=2 each, supplies
  7/27 (trimmed at each span's own end). Fails loudly if the parity
  policy ever changes.

**Scope note.** The fan-out is assertable by the oracle only where BOTH
segmentations agree (flag-change ruptures). Pure spatial overlaps
without a flag change still split the models (lead B1, segmentation
itself ruled parity-intended; pinned by the strict-xfail enrollment
test, replaced at 88edf91 by the positive parity pin
`test_enrollment_overlapping_raw_spans_parity` — see pass 2) — the
stockpile oracle cannot reproduce fan-out for that input
family because its enrollment input differs; recorded here as a known
oracle scope boundary under the ruling.

**Verification after the fix.**
- Replayed failing example: PASSES (DB-copy replay, quick profile).
- Full package quick set + harness stages: 15 passed, 1 xfailed, 8.1s
  (the 1 xfail is the pre-existing segmentation pin).
- `ruff --select E4,E7,E9,F tests/properties` clean; `mypy src
  tests/properties` clean (70 files incl. merged packages D and E/F).
- The exact-duplicate-triple dimension is now pinned deterministically;
  fresh-database exploration of this family is covered by the
  generated spec strategy (flag-rupture + overlap shapes + twin knobs
  draw independently).

## Pass 2 verification correction — lag-break segmentation and chaining tie order

**Symptom.** After merging 028ebfb into integrated discovery, the main
checkout's DB failed
`test_episode_selection_matches_model` deterministically (Hypothesis
reported 2 distinct failures; a follow-up run surfaced a third). All
three are patient-2 shapes built from overlapping "overlap"-family
spans plus exact same-day duplicate fills (sup=1, amt=0.5), mode
chain_f0, wash 0.

**Reproduction.** Same method as pass 1: READ-ONLY copy of the main
checkout's `.hypothesis-db/examples/` into my worktree DB; replays in
~1.4s. My own DB was again example-poor for these shapes.

**Root cause — two layers, both oracle-side (classification (a)).**

1. *Segmentation (B1, arbitrated).* The pass-1 accumulator broke each
   span against the episode built so far (running max end); the
   arbitrated contract is the SAS lag formula — each span breaks
   against the IMMEDIATELY PREVIOUS SPAN's end — so on pure spatial
   overlaps (no flag change) the SQL emits overlapping episodes where
   the accumulator merged. Example (sub-failure 2): spans
   [Jan8,Jan8], [Jan7,Jan9], [Jan10,Jan10] → SQL [Jan7,Jan9] +
   [Jan10,Jan10]; accumulator merged into [Jan7,Jan10]. The pov1 span
   of the Jan-7 index therefore had enr_end Jan 9 (SQL) vs Jan 10
   (model), flowing into dataavail_dt. Sub-failure 1 is the same
   segmentation divergence surfacing as episodes multiplicity
   (SQL 4 vs model 1) via the located fan-out. The B1 arbitration
   already resolved the segmentation as parity-intended
   (ms_episoderec2.sas:87-93), so the accumulator reading was an
   oracle bug, not a production divergence.
2. *Chaining tie order (new, deeper).* With segmentation fixed, the
   third replayed failure exposed the chaining window's tie behavior:
   when fan-out copies tie on the WHOLE window key
   (adate, expiredt, rxsup) — which happens when two output episodes
   with different starts both contain the same-day-summed fill —
   `ORDER BY adate, expiredt, rxsup` in 50_episodes.sql is NOT a total
   order, and DuckDB emits the ties SPAN-MAJOR (all copies of the
   first span, then the next). Span-major pairs the copies into 2
   episodes of summed rxsup 4; row-major would break every copy apart
   into 4 episodes of rxsup 2. The pass-2 oracle inserted copies
   row-major and produced the latter.

**Classification: (a) oracle bug in both layers, against the arbitrated
parity semantics.** Production is untouched and behaves as observed and
ruled. One NEW production robustness lead is recorded below (L-B3):
the chaining window's ORDER BY is not a total order under fan-out
ties, so episode pairing depends on DuckDB's join emission order —
stable in practice on threads=1 for the observed plans, but not
contract-guaranteed (the repo itself calls non-total tie orders
"latent parity failures" in 10_normalize.sql's comments).

**Fix (oracle only; production untouched).**
- `enrollment_episodes` rewritten to the lag break: each span ruptures
  against the immediately previous span's end and flags; an episode is
  the min start / max end of its member spans. Output episodes can
  overlap — that IS the arbitrated segmentation. The pass-1
  "accumulator cannot overlap" docstring claim is retracted.
- `model_episodes` builds chain rows SPAN-MAJOR (episode-outer,
  date-ordered stockpiled rows inner), then a STABLE sort on the
  window key (adate, expiredt, rxsup) — reproducing the observed tie
  pairing; NULL-span rows (unreachable for re-shave-surviving RX rows)
  appended row-major for LEFT-JOIN parity.
- The strict-xfail enrollment pin is REPLACED by a positive parity pin
  `test_enrollment_overlapping_raw_spans_parity` (2 overlapping
  episodes + the 16/46 numdispensing=2 stockpile fan-out, model ==
  SQL); the B1 xfail is obsolete now that the oracle models the ruled
  behavior.
- `test_stockpile_fanout_parity_on_overlapping_spans` now uses the
  pure-spatial A/B/C shape (assertable since segmentation matches) —
  sups [16, 46], numdispensing=2.
- The three replayed examples are pinned as deterministic @examples on
  `test_episode_selection_matches_model`.

**Verification.**
- DB-copy replays: all three failing examples PASS with the fixed
  oracle; the full episodes property (DB examples + random exploration)
  green.
- Full package quick set + harness stages run TWICE: 16 passed both
  times (8.1s / 8.3s; the pass-1 xfail is gone — replaced by the
  positive parity pin).
- `ruff --select E4,E7,E9,F tests/properties` clean; `mypy src
  tests/properties` clean; `tests/test_rewrites.py
  tests/test_determinism.py` 15 passed.

**New lead L-B3 (production robustness, NOT authorized to fix):** the
episodes chaining window `ORDER BY s.adate, s.expiredt, s.rxsup`
(50_episodes.sql) is not a total order when the located fan-out
produces copies tied on all three columns; the resulting episode
pairing/count depends on the join's emission order (span-major
observed). Same input family as the ruled-in fan-out (B2); a fix would
be a tie-break extension of the window ORDER BY and is a
SAS-parity-sensitive change — flagged for arbitration, not applied.
Oracle-side consequence: the span-major tie order is modeled as
OBSERVED behavior and pinned by @example; a DuckDB plan change could
flip it, and this suite would flag that immediately.

**Scope note (supersedes pass 1's).** With the lag segmentation and
span-major chaining modeled, overlapping-span fan-out is assertable for
ALL shapes; the pass-1 boundary ("pure spatial overlaps cannot be
asserted") is void.

## Pass 3 — FINAL-AUDIT §4 resolution: exact nonoverlap domain, order-agnostic overlap domain

**Mandate.** Independent acceptance audit
(investigation/packages/FINAL-AUDIT.md, AUDIT-1/2/3 + §4): replace the
flaky exact pairing pin proposed by `d63a8fb` and the standing red of
`0f76539` with (a) exact assertions on the supported nonoverlap domain,
(b) order-agnostic invariant assertions on the overlap fan-out, (c) the
B2 inflation kept as documented witness; fix the stale strict-xfail
names; produce the missing two-pass B evidence. No production,
manifest, or shared-record edits; no production tie/rejection policy
approved or asserted.

**Changed files (all worker-owned).**
- NEW `tests/properties/oracles/b_day_sets.py` — independent day-set
  oracle (gap-filled connected components over occupied days; shares no
  mechanism with the SQL's lag/window formulation or the lag model).
- NEW `tests/properties/test_pkg_b_pass2.py` — exact supported-domain
  checks: day-set property vs shipped SQL; exhaustive 128-mask x 3-gap
  model-vs-oracle domain sweep (384 cases); a shipped-SQL twin of the
  sweep (128 cases at gap 1, enrollment stage only, bounded).
- NEW `tests/properties/test_pkg_b_independent_review.py` — overlap
  witness characterization: upstream multiset stability across
  threads x optimizer at fixed input; per-run conservation/structural
  invariants; membership in `allowed_numbered_supplies()` (492
  decompositions from the 84 binary words over six A + three B labels);
  bounded 6-permutation x threads x optimizer grid (24 runs) under
  per-run invariants only (input permutation may reorder tied raw
  enrollment rows in 20_enrollment.sql, so cross-run equality is
  asserted ONLY at fixed input order). The shipped CTE text is
  extracted from `SQL_DIR`/50_episodes.sql, never copied.
- `tests/properties/test_pkg_b_enrollment.py` — module docstring
  rewritten (stale accumulator description and the removed strict-xfail
  name — AUDIT-3); explicit B2 witness asserts added to the parity pin
  (one dispensing, supply 46 > 30, numdispensing 2 > 1 — witness, not
  endorsement).
- `tests/properties/test_pkg_b_episodes.py` — coverage event added:
  "dispensing inside multiple episodes (B2 fan-out family)".
- `investigation/packages/B-independent-review.md` — integrated from
  `0f76539` with a disposition header: the standing-red recommendation
  was NOT adopted; salvage list and rejections recorded there.
- `investigation/findings/B2-overlapping-enrollment-double-count.md` —
  witness names updated to the current pins (AUDIT-1); tie-ordering
  ambiguity addendum added (doc-only, per audit §4 item 3).

**Rejected on purpose.**
- `d63a8fb`'s exact `@example` episode pairing pin on the overlap
  witness — thread/optimizer-sensitive; the b-final grid reproduced
  THREE distinct fixed-order decompositions ((3,3,3), (3,6), (6,3)),
  so any single pin is flaky.
- `0f76539`'s standing red (broad generated comparison) — an
  all-green, all-failures-classified suite is required; the tie-order
  ambiguity is now characterized by invariants instead.
- Neither rejection weakens supported semantics: the supported
  (nonoverlap, uniform-flag) domain gained EXACT checks it did not
  have, and the overlap domain is asserted only through laws that hold
  under every legal engine ordering. No observed output is copied into
  an assertion of correctness; the only observed-behavior pins are the
  arbitrated B2 defect witnesses, labeled as such.

**Pass records (worktree `inv/0f462p/b-final`, base 132fa87; fresh
`.venv` via `uv venv --python 3.13` + `-e '.[dev]'`, hypothesis
6.170.14; serial pytest, no xdist; `StageHarness` threads=1 default;
logs session-local, not committed).** Selection B = test_pkg_b_
{enrollment,stockpile,index,episodes,pass2,independent_review} +
test_harness_stages (quick passes include harness stages; statistics
runs omit it). Pre-change baseline at 132fa87: 16 passed, 8.65s.

1. B quick PASS 1: `.venv/bin/python -m pytest $B
   --hypothesis-profile=quick -q` → **21 passed, 20.48s, exit 0**.
2. B quick PASS 2: same command → **21 passed, 20.36s, exit 0**.
3. Discovery statistics: `$B --hypothesis-profile=discovery
   --hypothesis-show-statistics -q` → **14 passed, 37.35s, exit 0**.
   Actual coverage: enrollment nonempty output 96.15%, overlapping raw
   spans 30.77%; stockpile nonempty 83.02%/empty 11.32%; episodes
   nonempty 35.29%, same-day tie pairs 0-10 (17.65% zero);
   nonoverlap day-set property nonempty 69.23%/empty 7.69% (rest
   Hypothesis duplicate draws, no assume in the suite). Episodes-only
   re-run (fresh DB draw, exit 0): all 8 episode modes covered;
   "dispensing inside multiple episodes (B2 fan-out family)" 3.77%
   (the B2 overlap family IS generated, not probe-only).
4. Cheap deep bounded serial (one thread): `$B
   --hypothesis-profile=deep -q` → **14 passed, 57.62s, exit 0**
   (expensive budget 100 examples/property; the 384+128 exhaustive and
   24-run grid cases run identically at every profile).
5. Tie-signature observation (instrumented run of the grid helpers,
   not committed): fixed-order signatures across the 12-run
   threads x optimizer grid = {(1,3),(2,3),(3,3)}, {(1,3),(2,6)},
   {(1,6),(2,3)} — all in the allowed set (492 decompositions);
   permuted inputs additionally yield single-episode {(1,4)} (raw
   enrollment ties re-segment), invariants held in all 24 runs.
6. Static checks: `ruff check --select E4,E7,E9,F` clean on all five
   touched/new test+oracle files. `mypy src tests/properties`: 3
   errors, ALL pre-existing at 132fa87 in files outside this worker's
   ownership (tests/properties/harness.py:262 x2,
   tests/properties/test_property_builder_events.py:251; both
   unmodified by this pass) — reported, not masked, not fixed here.

**Manifest note (for the orchestrator; not edited by this worker):**
B now has two recorded quick passes (records 1-2 above) at
`inv/0f462p/b-final`; AUDIT-2's missing B pass-2 record can be closed
from this section plus the integrated pass-2 correction above.
