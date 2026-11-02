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

## F2-dependent independent age-day validation

Baseline: owned D `22fad196a48284a3cceabe2f2df576b20d6f2464` merged with
F2 `37e3d89` in isolated branch `inv/0f462p/d-f2-days`; merge baseline
`7630f386b4ba331917f55aad1385129d6b48414f`. Integrate the subsequent test
commit only after F2. No production edits or SAS execution were performed.
The old segment-start oracle/tests remain unchanged in age semantics and do
not validate F2. The earlier broad no-defect statement is not an F2 verdict.

`d_age_days.py` computes attained whole years separately on each calendar
day, then tests each configured lower/upper bound directly. It never uses
SQL threshold dates, next-band offsets, or intermediate database results.
Its input is a sequence of member-day occurrences: member counts deduplicate
IDs, day counts retain multiplicity. The integration fixtures independently
enumerate enrollment days, retain death day, and remove exclusion day;
duplicate exclusion claims remove that day only once. Full output rows are
compared as sorted lists, not sets.

Contract evidence: read-only `ms_cidadenom.sas:1036-1057`, especially 1046,
uses INTNX with SAMEDAY alignment. Accordingly the oracle uses birthday
anniversaries adjusted to the month's last available day for leap births.
Hand pins cover ordinary birthdays, February 29, February 28 in a non-leap
year, death/exclusion/both on the boundary, two unique members, and exact
nonempty counts. Generated cases vary window extents, death/exclusion
positions, and member counts. A cheap property has independent arithmetic
expectations for days before/on/after a birthday, repeated day occurrences,
and order invariance.

Finite-top/gapped configurations: config.py:55-79 accepts range tokens and
the module example contains gaps, but parser acceptance does not establish
SAS denominator behavior. The SAS loop consumes lower thresholds and does
not apply finite upper bounds. Literal bounds are pinned in a **model-only**
test; no finite-top/gapped production or SAS parity assertion is made.
Integration claims cover contiguous, open-top, year-based bands only.
Months/weeks/days, overlapping bands, year strata, washout/event scopes,
and optional-stage/gapped-RX expansions remain followup coverage.

Instrumentation repair: `d_denom.py` no longer populates empty defaultdict
entries while merging washout/exclusion periods. Hit counters now count
unique demographic-valid members whose periods actually intersect an
eligible window. The new tests verify exclusion counters against actual
window intersection, including outside-window and after-death claims.
Earlier 50/50 exclusion-hit statistics must not be relied on.

Final commands (sequential, one StageHarness database at a time, threads=1):

* `timeout 180 uv run --extra dev pytest tests/properties/test_pkg_d_age_days.py --hypothesis-profile=quick -q`: exit 0; 16 passed, 5.28s.
* `timeout 180 uv run --extra dev pytest tests/properties/test_pkg_d_age_days.py --hypothesis-profile=discovery --hypothesis-show-statistics -q`: exit 0; 16 passed, 16.82s; 50 DB examples, 92% nonempty, 60% crossing bands; 500 cheap examples.
* `timeout 180 uv run --extra dev pytest tests/properties/test_pkg_d_age_days.py -k day_model_independent_counts --hypothesis-profile=deep --hypothesis-show-statistics -q`: exit 0; 2,000 examples, 1 passed, 2.88s.
* `uv run ruff check --select E4,E7,E9,F tests/properties/oracles/d_age_days.py tests/properties/test_pkg_d_age_days.py tests/properties/oracles/d_denom.py`: exit 0.

Initial setup without the dev extra exited 4 (missing Hypothesis); corrected
with the declared dev extra. The first suite run exited 1 because event()
was called outside Hypothesis in deterministic pins; instrumentation moved
to the generated test, and all final commands passed. Neither was a
production defect. Verdict: F2 agrees with the independent bounded age-day
oracle on this tested contract; no genuine F2 defect was found. This is not
an assertion of complete denominator parity or coverage of ambiguous bands.

## D reconciliation after F2 — 2026-11-02

Baseline `14911db`; private branch `inv/0f462p/d-reconcile`. This section
supersedes the historical segment-start age claims above. `d_denom` now
passes each surviving calendar day to `d_age_days.count_age_days`, after
its unchanged independent eligibility, washout, exclusion and event-scope
calculations. Per-stratum member sets still deduplicate IDs; day totals
still accumulate occurrences across windows and scopes. No SQL age
threshold formula was copied. Full-row exact comparisons remain intact.
Only contiguous open-top year-band integration claims are supported;
finite/gapped bands remain ambiguous. Year retains the prior segment-start
convention and is not newly validated here.

All runs used the baseline plus the D-only working-tree changes committed
with this section, serial pytest, one StageHarness database at a time,
its default threads=1, and `uv run --offline --extra dev pytest`.
The complete D suite means the four explicit files `test_pkg_d_age_days.py`,
`test_pkg_d_config_sharing.py`, `test_pkg_d_denominators.py`, and
`test_pkg_d_optional_stages.py` under `tests/properties/`.

| command suffix / bound | UTC completion | exit | result | pytest time |
|---|---|---|---|---|
| denominators, `--hypothesis-profile=quick -q`, timeout 180 | before full quick | 0 | 7 passed | 4.61s |
| complete D, `--hypothesis-profile=quick -q`, timeout 300 | 20:27:06 | 1 | 41 passed, 1 failed | 130.38s |
| complete D, `--hypothesis-profile=quick -q --tb=short`, timeout 300 | 20:29:20 | 1 | 41 passed, 1 failed | 117.09s |
| complete D, `--hypothesis-profile=discovery --hypothesis-show-statistics -q --tb=short`, timeout 900 | 20:38:51 | 1 | 41 passed, 1 failed | 564.51s |
| age_days, `-k day_model_independent_counts --hypothesis-profile=deep --hypothesis-show-statistics -q`, timeout 180 | 20:38:59 | 0 | 1 passed, 2,000 examples | 2.96s |

**Preserved divergence, not classified as a production defect:** all three
complete runs fail `test_optional_stage_matches_reference[attrition_person_time]`.
Minimized input: patients=2, seed=0, GenerateOptions with all five booleans
false. At cohort_a level 11, production has numpts=0 while the existing
optional-stage oracle expects 2. This may require reconciling that oracle
with F1's empty-step contract; it was not changed merely to fit production.
The failure remains executable, with no xfail or weakened comparison.
The full suite is therefore **not green**.

Discovery: denominator property passed 50 examples (98% nonempty windows,
86% nonempty output, 36% actual washout hits, 12% death clipping, **0%
actual exclusion hits**). Deterministic exclusion tests remain necessary;
the former claimed 50/50 exclusion distribution was instrumentation error.
Independent age integration passed 50 examples (one invalid; events 94.12%
nonempty, 64.71% crossing bands); its pure property passed 500 examples.
All seven config-sharing dimensions passed 50 examples each; wash rejected
12 examples. Eleven optional-stage families passed 50 examples each.
Attrition stopped on its reused counterexample, not 50 successful examples.

Remaining bounded gaps: mandatory nonempty optional-stage fixtures and the
gapped-RX piece-per-span clip witness were not implemented in this pass.
The current RX oracle still models only the containing enrollment span;
its extension needs separate contract validation. Optional-stage `*-ran`
events prove execution, not nonempty results. No production, shared harness,
manifest, network/publication, SAS execution, or branch deletion occurred.
