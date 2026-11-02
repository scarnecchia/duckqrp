# F1: attrition forward-fills the last member count through empty
# counted steps — FIXED by counting empty relations as 0 (SAS parity)

- **id:** F1
- **proposed issue title:** Attrition funnel reports members that do not
  exist: a counted step whose relation is empty forward-fills the last
  member count down to level 27 ("unique members in final cohort"),
  which then contradicts cohort_final
- **contract:** Two sources, agreeing. (1) The funnel row's own
  description: level 27 is "Information - Unique number of members in
  final cohort" (70_outputs.sql skeleton, docs/OUTPUTS.md's attrition
  section "now reproduces SAS's table"), and every "Exclusion -" level
  counts the rows surviving that step. (2) The SAS original:
  %ms_attrition_cidacompute fills `:remaining` from
  `proc sql select count(*) ... from &datafile.` — count(*) over an
  EMPTY dataset is 0, never missing
  (ms_attrition_cidacompute.sas:77-88) — and ONLY `skip` rows carry
  the previous count forward, with `excluded=0` (:112-119, "when
  attritiontype = SKIP, # remaining will be carried forward and #
  excluded = 0"). The initial-episode and final rows are `information`
  rows carrying the actual count (ms_attrition.sas:291-297 count(*) on
  _keep; :1008-1015 count(distinct PatId)).
- **trigger:** Any cohort whose counted relation at some step is empty:
  a cohort whose members have no cohort-identifying claims, or (as in
  the finder's witness) episodes that all vanish downstream so
  `_att_ep` is empty while earlier episode steps counted candidates.
- **witness/repro command:**
  investigation/witnesses/pkg-f-lead-f1-attrition-l27-forward-fill.txt
  (finder's witness, verified by the arbiter's probes
  investigation/probes/arb2_f1_f2_probe.py and
  arb2_f1_counts_probe.py); xfail pins
  `tests/properties/test_pkg_f_totals.py::test_attrition_final_count_pin_f1`
  and `::test_attrition_final_count_matches_cohort_final`. Regression:
  `tests/test_regression_f1.py` (standalone):
  `.venv/bin/python -m pytest tests/test_regression_f1.py -q`.
- **expected vs actual:** On `generate(patients=1, seed=0)`
  (wildcard+cida study): cohort_final AND ptsmasterlist are EMPTY, yet
  attrition reports remaining=2 at level 27 for cohort_a ("unique
  members in final cohort") — level 7 counts 2 index candidates, every
  later episode step's `excluded` is NULL (its count row is missing),
  and `coalesce(n, last_value(n IGNORE NULLS))` carries 2 down the
  whole funnel. cohort_b (zero claims at all) shows remaining=1 at
  every level, forward-filled from the demographics count. Expected:
  the first step whose relation is empty reports remaining=0 with the
  whole drop as its exclusion, later steps report 0, level 27 reports
  0.
- **first failing revision:** c3e60db5d08343b1cebbb90d234602f09cb0d70d
  (campaign base; the forward-fill construction predates the campaign).
- **mechanism:** Each counted level's count was produced by a GROUP BY
  over the step's relation; an empty relation yields NO row, so `n` is
  NULL in the funnel grid. The blank-row carry
  `coalesce(n, last_value(n IGNORE NULLS) OVER w)` — correct for the
  skeleton's `blank` rows — then forward-filled the last MEMBER count
  through every uncounted episode level and into the `final` row,
  inventing survivors and hiding every exclusion. SAS's count(*)-over-
  the-step's-data semantics never leave a counted step without a
  number: empty means 0, and the first empty step absorbs the drop as
  its exclusion.
- **severity:** wrong answer in the headline funnel downstream readers
  check first (the finder's HIGH guess stands; docs/OUTPUTS.md itself
  warns about plausible-looking counts that "meant nothing").
  **+ unproven impact:** dashboards or gate checks keyed on level-27
  attrition would report a nonzero final cohort for a study that
  produced none; the exclusion counts that explain WHERE the episodes
  went are absent entirely.
- **affected paths:** src/qrp/sql/70_outputs.sql (the attrition
  carried/main CTEs).
- **minimal fix/commit:** branch `inv/0f462p/fix2-f1` (from c3e60db):
  red commit 0bcb1a8 (standalone regression tests), green commit
  3001086 (`counted` CTE: a missing count row counts as 0 at every
  `init`/`excl`/`final` step; `blank` rows keep carrying). Extracts
  whose relations are non-empty are unchanged (n0 = n).
- **red/green commands+results:**
  - red: `.venv/bin/python -m pytest tests/test_regression_f1.py -q`
    at c3e60db+0bcb1a8 → 1 failed
    (test_cohort_with_no_claims_counts_zero_not_carried: level 6 shows
    (2, None) instead of (0, 2)); the surviving-episode pin passes.
  - green: same at 3001086 → 2 passed; determinism + rewrites + ui →
    50 passed, 2 skipped; tests/test_pipeline.py → 287 passed with
    only the two pre-existing exempt spill failures, 1 skipped.
- **broader validation:** the two F1 xfail pins on the arb2 branch flip
  to passing (see ARBITRATION.md wave-2); the funnel monotonicity and
  no-negative-exclusion properties still hold with real counts.
- **related findings:** F2 (same package; independent mechanism);
  A-4 (crash class, unrelated).
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
