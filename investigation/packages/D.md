# Package D — Denominators, aggregations, and optional features

Worker D, branch `inv/0f462p/d-denominators-outputs`, base a0d0b93.
Date: 2026-06-17 (session). Everything below is local-only; no network, no pushes.

## Scope

* `src/qrp/sql/92_cidadenom.sql` — TOP PRIORITY: enrolled member-days
  (`denomcounts`) against an independent day-enumeration oracle.
* Optional/output stages: 70_outputs (attrition, denominators, censoring),
  72_codedistribution, 74_utilization, 76_labs, 78_mfu, 80_covariates,
  85_riskscores, 90_cidatables, 94_followuptime, 96_baseline,
  47_geography, 62_mstr_wide — each against a per-stage aggregation
  oracle.
* Config sharing: `_enr_cfg_id`, `_exclusion_key`, `_denom_cfg_id`
  (src/qrp/pipeline.py) and the cohort fan-out — together vs separate.

Files read in full: all of the above plus 00_macros, 10_normalize,
20_enrollment, 30_exposure, 60_followup (upstream inputs the oracles
must model), qrp/config.py (CohortConfig/StudyConfig/StratumLevel/
InclusionRule/RiskScoreCode/Covariate + cida_levels/denominator_cohorts),
tests/properties/{conftest,harness,synth,strategies}.py, docs/SAS_PARITY.md,
docs/OUTPUTS.md, docs/PARITY_FINDINGS.md, README stage map.

SAS reference (operator-provided, READ-ONLY, cited with paths+lines):
`/home/scarndp/dev/Sentinel/qrp/inputfiles/macros/ms_cidadenom.sas`,
`ms_cidanum.sas`, `ms_setnumloopmacrovars.sas`.

## Oracle independence

* `oracles/d_denom.py` shares NO expression with the shipped SQL:
  eligibility is decided per CALENDAR DAY (window day-set minus ineligible
  days, maximal contiguous runs = segments); enrollment merging,
  demographics tie-break, enveloping, exposure/washout chains, exclusion
  inverse-mapping and stratum algebra are re-derived in Python from the
  documented contract. The implementation's closed-form interval algebra,
  lag/lead gap-splitting and scope arithmetic are never mirrored.
* `oracles/d_stages.py` re-derives each optional stage from ITS inputs
  (upstream stage semantics trusted to their own packages; the stage's
  own windows, aggregation, multiplicity, ranking, rounding and
  bookkeeping are recomputed in Python). No shipped SQL text is executed
  or copied anywhere in `oracles/`.
* SAS round-half-away-from-zero is reimplemented (Python `round` is
  banker's rounding — a real divergence class), documented per use.

## Properties (name → contract → oracle)

Denominators (`test_pkg_d_denominators.py`):

* `test_denominator_matches_day_enumeration` — for generated datasets
  (1-5 patients, random tie/death/encounter knobs, washout pairs
  (0,0)/(183,183)/(30,0)/(60,90), OUTPUTDENOM pairs Y/M/N) the
  production `denomcounts` equals the day enumeration for EVERY t2cida
  level, member counts and member-days checked as separate columns.
  Deterministic corners: `test_denominator_death_exactly_at_period_edge`
  (death == span end / == denom_start / == query start, hand-built
  fixture, hand-checked window outcomes), and
  `test_denominator_exclusion_exactly_covers_stratum_boundary` — an EXC
  claim (condfrom=condto=0) dated exactly on the first eligible day
  shaves exactly that day, moving the member across their 64th birthday
  (bands 00-63/64+, born 1950-08-02, enr_days 212 → denom_start
  2014-08-01, age 63→64); asserts the single production row
  `('A','cohort_a','64+',2,…,1,152)` exactly. Wrong clip boundary or
  wrong segment-start stratum fails this row assertion.
* `test_denominator_example_pair_differing_one_field` — deterministic
  pairs differing in exactly one field (enrol_gap with distinct
  enrollment_num per SAS's one-enrollment-per-number rule; exclusion
  config), oracle agrees on both sides.

Optional stages (`test_pkg_d_optional_stages.py`),
`test_optional_stage_matches_reference[param]`, one case per stage:
`code_distribution` (distindexmap/distindex/distindex_episodes vs entity
numbering oracle: RX stockgroups sorted, medical defs sorted
(codecat,codetype,enctype,pdx,code), settings×pdx expansion, ids joined
'_' in CHARACTER order, blank-hoi placeholder rules),
`mfu` (claim-row multiplicity for codecount, distinct-patid patcount,
metric DESC → codecount DESC → code tie-break, topxx cut), `labs`
(LABDATETYPE priority ladder, path-02 LOINC + PX paths, result-type
gating, [index-365, index-1] window, DISTINCT rows, summary rounding),
`utilization` (distinct (adate,enctype) encounters, enc_days distinct
days, numrx joined-rows vs numgeneric/numclass breadth, zeros for
windowless episodes, create_baseline gating, summary rounding),
`geography` (Unknown cascade incl. cb_reg 'other', sdi quartiles,
zip_uncertain), `risk_scores` (max weight per condition, sum +
intercept, DM sex/agegroup conditions; NOTE: clinical validity of any
weight set is external to this repo and NOT asserted),
`covariates` (DX/PX window overlap; RX through clip→sameday-sum→chain
per enrollment span, dateonly point semantics), `baseline`
(baseline_long: declared levels squared with zeros — Sex F/M/O, Race
0-5/M, Hispanic Y/N/U, AGE bands from agestrat with '-→_' and '<>+-space'
stripped, query-period years; demographics once per patient on first
episode; covarnum episode counts; means + sample SD with NULL SD for
single-episode cohorts), `cida_tables` (numcounts per level GROUP BY
ALL; t2_cida = FULL merge of numerator and the d_denom denomcounts
oracle incl. dennummemdays NULL-for-M semantics and the fine
time/geography strata columns), `followuptime` (per-level GROUP BY ALL
with level-nulled dims so shell rows merge with same-key episode rows;
squared shell per cfg cohort × category × level),
`mstr_wide` (62's added columns per episode: year/month/quarter,
PeriodID/IndexLookEndDt, RawDisp/AdjustedDisp/TotRxSup/TotRxAmt
passthrough, distindex lists, numevents nullif, fup category buckets +
Censorcat_sort, all 9+2 episode_flags passthrough),
`attrition_person_time` (attrition funnel: level counts recomputed from
the stage's own input relations per SAS definitions, blank/carry and
lag-partition bookkeeping, 18.n information rows; denominators GROUPING
SETS margins; censoring cens_* flags and timetocensor).

Config sharing (`test_pkg_d_config_sharing.py`),
`test_cohorts_together_equal_separate[dim]` — 7 dimensions, each
differing the two cohorts in EXACTLY one field: `enr` (enrol_gap +
enrollment_num, as the cohort file carries), `wash` (wash_per 0/25 with
enr_days 30 and IDENTICAL codes so the codes hash cannot mask the key),
`excl` (EXC rule on one cohort only), `outcome` (different EVENT codes —
deliberately NOT in `_denom_cfg_id`; exercises the a/b scope algebra),
`denomM` (OUTPUTDENOM M), `strata` (different age bands), `demog`
(sex filter). Per-cohort rows of 22 output tables compared canonical
(integers/dates/flags EXACTLY; floats at 9dp — ordering noise only; the
members/episodes/member-days themselves are integers and exact).
Multiplicity pinned by the mfu/labs oracles: codecount counts CLAIM ROWS
(a code twice for one patient counts twice); member-days and lab
DISTINCT rows are per-day/per-row as documented.

## Calibration record (seeded defect, never committed)

* Patch: removed `c.wash_per,` from the `_denom_cfg_id` tuple
  (src/qrp/pipeline.py line ~501), uncommitted.
* Command: `python -m pytest
  "tests/properties/test_pkg_d_config_sharing.py::test_cohorts_together_equal_separate[wash]"
  --hypothesis-profile=quick`
* First attempt: mutation SURVIVED (property passed). Diagnosis: the wash
  dimension gave the cohorts different EXPOSURE CODE pools, and the
  codes hash inside `_denom_cfg_id` kept them in separate configs — the
  omission was unobservable. Second issue: default enr_days 183 put
  eligible time beyond any washout reach in ≤5-patient fixtures, and
  wash_per 45 > enr_days 30 trips the SAS cross-validation
  (`wash_per <= enr_days`, config.py:423).
* Fix to the harness (not the product): wash dimension now uses
  identical codes, enr_days 30, wash_per 0/25, and `assume()`s the
  oracle's `washout-hit-members` fact so examples actually exercise the
  shave.
* Second attempt with the mutation applied: **FAILED for the expected
  reason** — `AssertionError: cohort_a: table denomcounts differs
  together vs separate` (the exact failure mode of the historical bug
  documented in docs/PARITY_FINDINGS.md, where the same omission made
  all 40 cohorts report dennumpts 62,730).
* Revert: `git checkout -- src/qrp/pipeline.py`; `git status src/` clean;
  wash dimension green afterwards (30/30 quick run).
* Lesson recorded for the coordinator: a config-sharing dimension must
  neutralize every OTHER key field (here: the codes hash) and must
  assert the mechanism it guards actually fires, or the property is
  vacuous against exactly the defect class it exists to catch.

## Pass records

| command | revision | result | duration |
|---|---|---|---|
| `pytest tests/properties/test_pkg_d_denominators.py tests/properties/test_pkg_d_optional_stages.py --hypothesis-profile=quick` | pre-commit HEAD (working tree) | 19 passed | ~38s |
| `pytest tests/properties/test_pkg_d_config_sharing.py --hypothesis-profile=quick` | same | 7 passed | ~89s |
| `pytest tests/properties/test_pkg_d_* tests/properties/test_harness_pipeline.py --hypothesis-profile=quick` | same | 30 passed | ~109s |
| `pytest tests/properties/test_pkg_d_denominators.py --hypothesis-profile=discovery --hypothesis-show-statistics` | same | 7 passed | ~15s |
| `pytest tests/test_rewrites.py` | same | 11 passed | 1.3s |
| `ruff check --select E4,E7,E9,F tests/properties` | same | All checks passed | — |
| `mypy src tests/properties` | same | Success: no issues in 35 source files | — |

Discovery-profile statistics (the cheap family), 50 examples:
windows 94% nonempty, output-nonempty 82%, member-counts-nonzero 82%,
member-days-nonzero 82%, washout-shave-hit 50/50 split, exclusion-hit
50/50 split, death-clipping 22%, shaving-split-a-window 16%. Both shave
mechanisms and the death path fire on half or more of examples — the
property is not vacuous on any axis it guards. Deterministic @example
corners additionally pin death-at-boundary and exclusion-at-stratum-
boundary exactly.

## Findings / leads

**D1 — SAS parity: INCLUSION-condition restriction of the denominator is
not implemented (confirmed against the SAS original).**
`ms_cidadenom.sas` gates the whole condition shave on `excl_incl`
(lines 145-165: `%if &excl_incl = Y`), and `ms_setnumloopmacrovars.sas`
sets `excl_incl = Y` whenever a study HAS inclusion codes (lines
653-654 default N, 677-681 set Y) — so the branch is active by default
for any study with conditions. Inside it, INCLUSION subconditions
(`SubCondInclusion = 1`, read at lines 169-186) build claim-index
windows (`EligStart = ADate - CondTo`, `EligEnd = Expiredt/ADate -
CondFrom`, lines 197-212) and RESTRICT eligible time to them via
`%ms_shaveoutside` with `KeepPartBf=Y` (comment at line 388: "includes
all time periods where the potential index dates will meet at least one
of the cond inclusions"; shave at ~lines 420-430). EXCLUSION
subconditions shave OUT via `%ms_shaveinside` (lines 300-306). Dose
thresholds additionally restrict eligibility through `%ms_dose_denom`
(lines 307-322). The Python `92_cidadenom.sql` implements ONLY the EXC
shaveinside half (`WHERE r.criteria = 'EXC'`, line 309); inclusion
windows and dose-denominator restriction are implemented nowhere.
Expected: for a study with INC conditions (excl_incl=Y), member-days
are overstated (SAS narrows eligibility to inclusion windows; this
package keeps all enrolled time minus exclusions). Severity: moderate —
production studies using INC conditions get a denominator that is too
large; consistent with docs/PARITY_FINDINGS.md "Third fix" which
implemented only the exclusion half.

**D2 — demonstrated doc error (comment vs BOTH SAS and implementation).**
The 92_cidadenom.sql opening comment (lines 31-33) says "NOT included:
the inclusion/exclusion shaving (excl_incl = Y)". The implementation
DOES shave by EXCLUSION conditions (lines 261-314), and SAS confirms
exclusion shaving is part of `excl_incl` (ms_cidadenom.sas lines
145-165, 300-306). The comment is stale/wrong on the exclusion half
while being accidentally right only about the inclusion half — which is
itself the D1 parity gap. Recommendation: rewrite the comment to
"exclusion-condition shaving implemented; inclusion-condition
restriction and dose-denominator restriction not implemented (see
D1)". Severity: doc-only.

**D3 — parity nuance, documented deliberate deviation (verified, no
action).** SAS clips the denominator end at `min(Enr_End, &censordate.)`
minus the pullback (ms_cidadenom.sas:709-710); the Python clips at the
QUERY PERIOD end (`DATE '{end_date}'`, 92_cidadenom.sql lines 87-89).
docs/PARITY_FINDINGS.md "Second fix" records that query-period + pullback
matched real SAS output to 0.0035%/0.02% while censor_date-based
clipping ran 45% high — i.e. the production study's SAS `&censordate`
behaved as the query period end. The Python `effective_censor_date`
(study.censor_date or end_date) corresponds to SAS's `&censordate`
semantics (docs/OUTPUTS.md "When denominators are NOT computed"). Where
a future study sets censor_date < end_date the two may diverge; the
oracle models the shipped query-period contract and matches production
exactly on all generated cases. No change requested; noted for parity
reviewers.

**D4 — shared-infra gap (coordinator note, not a production bug).**
`synth.build_study()` produces studies whose cohorts carry NO
care-setting rows, and `event_claims` INNER-joins `cfg_care_setting`
(30_exposure.sql), so builder studies silently have NO outcome events —
hand_fixture()'s docstring claims "one event" for P1 but the probe shows
`event_claims = ()`, `first_event = ()`. The real loader expands a
missing `caresettingprincipal` to the wildcard `('**','*')` per EVENT
code (config.py `parse_care_setting`, `CARE_SETTING_ALL`; applied at
lines 1489-1493). Package C confirmed the same trap independently. All
package-D fixtures construct studies with the loader-equivalent wildcard
rows via `dataclasses.replace` (oracles/d_fixtures.py `wildcard_events`)
— verified to reproduce the loader's row shape (one `(code,'**','*')`
row per EVENT code). Suggest extending `build_study` in shared infra
(not editable from this package) or documenting the trap in synth.py.

**No production defects found by this package.** The denominator oracle
agrees with production exactly on every generated example (hundreds of
examples across profiles incl. washout on/off, deaths, DX+RX exclusion
shaving, OUTPUTDENOM M/N, gapped enrollment) and on all deterministic
boundary fixtures; each optional stage oracle agrees exactly (float
tolerance 9dp on rounded means/sums only); together equals separate on
all 7 one-field dimensions.

## Open questions — RESOLVED this round (was: 92_cidadenom comment vs implementation)

Resolved from the SAS original (see D1/D2/D3 above):

1. Is exclusion shaving intended? YES — SAS `ms_cidadenom.sas` shaves
   EXC subcondition windows out of the denominator when `excl_incl=Y`
   (lines 145-165, 300-306); the gate is set Y for any study with
   inclusion codes (ms_setnumloopmacrovars.sas:653-681). The Python
   implementation matches this half; the file header is a doc error (D2).
2. Is inclusion shaving intended? YES — SAS additionally restricts
   eligible time to INC subcondition windows via shaveoutside (lines
   ~380-430) and applies dose-based restriction via %ms_dose_denom
   (lines 307-322). The Python does neither → D1 parity gap, now a
   cited finding rather than an open question.
3. Where the death truncation belongs: SAS truncates Enr_End at death
   upstream in CIDAnum gated on `enrend_death in ('B','C')`
   (ms_cidanum.sas:1934-1935); the Python truncates unconditionally in
   92 (documented in the SQL as a deliberate deviation, "enrolment
   outliving a member is an extract artefact either way"). The oracle
   models the shipped unconditional behavior. Parity note only.

Remaining open (not resolvable from this evidence, low priority):

* SAS parity behavior when a washout shave splits a window across an age
  band (docs/PARITY_FINDINGS.md:1064 "may be correct — SAS may do the
  same — but it is unverified"): the shipped segment-start stratum
  semantics is now pinned by
  `test_denominator_exclusion_exactly_covers_stratum_boundary`, but SAS's
  own behavior on that boundary remains unverified externally.
* `_att_counts` levels 3-5 are carried from level 2 (70_outputs.sql
  NOTE: "member-level definitions not yet verified against SAS") — the
  attrition oracle pins the carried-forward arithmetic, not SAS's
  member-level definitions for those levels.

## B2 contamination

None of this package's fixtures use overlapping enrollment spans
(`generate()` never creates them; the hand fixtures don't either), so no
finding here is B2-contaminated. The config-sharing `enr` dimension uses
gapped spans (two spans, 9-day gap) which MERGE or SPLIT per enrol_gap —
that is enrollment-episode semantics, not the stockpile double-count.

## Incomplete / caveats

* `expected_attrition` checks the funnel arithmetic and bookkeeping from
  the stage's input relations; it does not re-derive upstream stage
  semantics (episode_criteria flags, stockpiled) — those belong to other
  packages.
* Risk-score oracles test documented mappings/arithmetic only; external
  clinical validity of weights unproven (also stated in the test
  docstring).
* RX covariate chain oracle models the single-span chain exactly; the
  piece-per-span re-clip on GAPPED enrollment (80_covariates "clipped"
  CTE) is modeled only for the claim's containing span — generated
  fixtures enroll continuously, so the gapped case is not exercised.
* Discovery profile was run on the denominators family only (cheapest,
  per instructions); the stage/config families ran on quick (10
  examples each; 12 + 7 dimensions).
