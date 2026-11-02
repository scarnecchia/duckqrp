# Package C — Dose, inclusion, follow-up, censoring

Worker: package C (`inv/0f462p/c-dose-inclusion-followup`, base a0d0b93).
Scope: stages 42 (dose restrictions), 52 (inclusion criteria), 55 (dose
censoring), 60 (follow-up + events), and the `_claim_dose_view.sql`
glue view. All work private to this campaign.

## Files read (fully)

* `src/qrp/sql/42_dose.sql`, `52_inclusion.sql`, `55_dose_censor.sql`,
  `60_followup.sql`, `_claim_dose_view.sql`
* upstream context: `30_exposure.sql` (exposure_claims / event_claims /
  stockpile chaining), `40_index.sql` (index_candidates, washout
  semantics), `50_episodes.sql` (episode_criteria / ptsmasterlist
  columns and flags), `00_macros.sql`, `10_normalize.sql` (dispensing
  filters)
* `docs/INCLUSION.md`, README stage map sections
* `src/qrp/config.py` (CohortConfig dose fields, InclusionRule,
  StudyConfig validation), `src/qrp/pipeline.py` (register_config,
  COVAR_SOURCE_VIEW, stage gates)
* shared infra: `tests/properties/synth.py`, `harness.py`,
  `conftest.py`, `strategies.py`, `test_harness_stages.py`

## Oracle-independence statement

Oracles live in `tests/properties/oracles/c_models.py` as pure
functions over plain dataclasses. They restate the documented contract
(INCLUSION.md, the stage SQL contract comments, SAS semantics cited
there) with day-by-day date arithmetic; no shipped SQL is copied or
transliterated. Stage INPUTS (episode/candidate keys, claim sets,
event claims) are snapshotted from the harness between passes — the
warm-prefix pattern — so the model reads exactly what the stage read,
while the stage's DECISIONS are re-derived independently. Shared
infrastructure is used only through its documented API (`synth` rows
via `rows_for_table`, `StageHarness.run_through`, `fetch_rows`-style
reads). Numeric fidelity was matched to the shipped expressions
(operator order for pro-rated cumdose, round-half-away-from-zero for
all integer comparisons, one-decimal half-away rounding for afdd) so
inexact-binary floats agree bit for bit rather than by luck.

## Properties (name → contract → oracle)

| test | contract pinned | oracle |
|---|---|---|
| `test_pkg_c_inclusion.py::test_inclusion_codedays_counts_distinct_days` | codedays counts DISTINCT days; same-day duplicates count once | `inclusion_verdict` |
| `...window_ends_are_inclusive` | window ends inclusive; first_failed_cond = min failing cond; INC/EXC set | 〃 |
| `...exc_fails_when_met_same_cond_number` | criteria is part of the condition key (INC+EXC both cond 1) | 〃 |
| `...subconditions_are_anded` | subconditions AND within a condition | 〃 |
| `...subcond_exclusion_inverts_its_subcondition` | subcondinclusion=0 inverts | 〃 |
| `...codes_within_subcond_pool_their_days` | CHARACTER TEST: pooled day counts across a subcondition's codes (OC-1) | 〃 |
| `...rx_supply_overlap_and_mincrxdays` | RX supply-overlap matching; minrxdays sums overlap days; cumdose pro-rated; ≥ inclusive at 6.0 | 〃 |
| `...episodeend_anchor_is_forward_looking` | EPISODEENDDT anchors each end independently; INDEXDT vs EPISODEENDDT differ | 〃 |
| `...afdd_is_window_average_of_matched_claims` | afdd = round(mean per-claim cfdd, 1) over MATCHED claims; band inclusive | 〃 |
| `...interaction_one30_vs_three10_supply_pooled_equal` | mandatory interaction: same supply span via 1 vs 3 chained fills; pooled supply equal, pro-rated dose separates on amount change | 〃 |
| `...interaction_same_day_triple_claim_day_vs_row_counting` | mandatory interaction: 3 same-day claims satisfy codedays=1 not 2 | 〃 |
| `...test_inclusion_matches_condition_model` (property) | full inclusion_excluded + ptsmasterlist membership vs model, generated rule shapes × grid fixtures | 〃 |
| `...test_inclusion_replay_determinism` | two-pass determinism of survivors | n/a |
| `test_pkg_c_dose.py::test_dose_lookback_window_boundaries` | prior-dose frame [orig−per, orig−1] closed; own day never counts | `dose_verdict` |
| `...dose_cfdd_rounds_half_away_to_integer` | cfdd bounds compare round(cfdd) (half away, integer) | 〃 |
| `...dose_any_same_day_claim_out_of_bounds_excludes` | any same-day claim out of cfdd bounds excludes (DISTINCT over fan-out) | 〃 |
| `...dose_washout_zero_prior_fill_feeds_second_index` | wash_per=0 keeps all candidates; exposure claims unfiltered by washout | 〃 |
| `...interaction_dose_one30_vs_three10` | mandatory interaction: prior-dose granularity immaterial (30 units as 1×30d vs 3×10d), same-day granularity decisive for cfdd | 〃 |
| `...test_dose_boundaries_match_model` (property) | dose_excluded (with reason) + surviving candidates vs model | 〃 |
| `test_pkg_c_followup.py::test_followup_washout_boundaries` (+ prior_event_inside_window) | washout [index−wash, index−1] closed; wash=0 disables; NULL = any prior event | `episode_outcome` |
| `...blackout_includes_the_index_day` | blackout [index, index+blackout−1]; blackout=0 disables; post-blackout event attributed | 〃 |
| `...zero_length_followup_via_at_risk_start` | followuptime floors at 0; person_days NOT floored (OC-3) | 〃 |
| `...same_day_competing_reasons` | exit_reason CASE precedence (event > death > disenrollment > study_end > exposure_end); dose-censor truncation falls through to exposure_end; fup_*/cens_* independence | 〃 |
| `...iev_drops_event_not_episode` | IEV/EEV drop events, keep episodes; feventdt/numevents after exclusion | `event_exclusion` |
| `...ioc_claim_disqualifies_like_an_event` | IOC codes disqualify through the washout only; codetype matching | 〃 |
| `...same_event_different_claim_counts` | mandatory interaction: 1 vs 3 same-day event claims — feventdt/followuptime identical; numevents follows event_count | 〃 |
| `...test_followup_and_censoring_match_day_model` (property) | episode_hoi flags, cohort_final membership + 10 columns, first_event vs day model | 〃 |

## Interaction cases (mandatory, deterministic)

1. one 30-day vs three 10-day prior fills — equal prior dose, same
   decision (inclusion supply + dose max boundary).
2. same-day claim-split — same total dose, per-claim cfdd out of
   bounds on one row excludes the whole candidate.
3. 1×30d vs 3 chained 10d fills reaching the same supply span —
   pooled supply/dose equal, mincumdose separates after an amount
   change (episode structure held fixed: supply gap before index).
4. 1 vs 3 same-day event claims — numevents vs event_count, feventdt
   and followuptime invariant.
All are plain deterministic tests (the @example requirement is met by
construction: every input is explicit and fixed).

## Calibration record (seeded defect)

* Patch (uncommitted): `src/qrp/sql/60_followup.sql` line 67,
  `pe.adate < m.indexdt - c.fup_wash_per` → `<=` (washout horizon
  boundary off by one day).
* Command: `.venv/bin/python -m pytest
  "tests/properties/test_pkg_c_followup.py::test_followup_washout_boundaries"
  "tests/properties/test_pkg_c_followup.py::test_followup_and_censoring_match_day_model"
  --hypothesis-profile=quick`
* Result: `test_followup_washout_boundaries` FAILED for the expected
  reason — case (wash=6, event at −7): stage dropped the episode the
  model kept, assertion
  `cohort_final membership differs from the model`. The generated
  property still passed (boundary needs an event exactly at
  horizon−1, rare in generated data): the hand boundary cases carry
  detection power the generator alone lacks.
* Reverted with `git checkout -- src/qrp/sql/60_followup.sql`;
  `git status` shows only new package files; re-run green.

## Coverage statistics

Discovery profile, `test_dose_boundaries_match_model`: candidates
present 83%, exclusions fired 82% (min_cumdose 48%, max_cumdose 17%,
min_cfdd 15%, max_cfdd 2%), lookback-boundary exact hits 30%,
same-day divergence 30%, assume-filter 17% (all-None knob profiles).

Quick profile, inclusion generated property: episodes evaluated 100%,
exclusions 60%, INC/EXC split 70/30, window-boundary exact hits 10%,
same-day divergence visible (recorded at discovery too).

Discovery profile, follow-up generated property (after widening the
fixture pool to half grid fixtures): episodes present 65%, kept 53%,
washout/blackout drops 15%, event attribution 13%, competing censor
reason exercised (disenrollment 16%; death at deeper tail), washout
mode coverage null/disabled/windowed = 31/27/33%.

Empty-result rates are recorded via the `episodes: none` /
`candidates: none` events; nothing is vacuous.

## Findings / leads

No contract violation against the documented semantics was found in
the stages under test: every property passes on unmodified shipped
SQL, and the calibrated defect was caught for the expected reason.

Observations worth the maintainers' attention (none is a demonstrated
bug in my package's stages):

1. **Stale comment in `55_dose_censor.sql`** (severity: doc).
   "Apply, then re-check the criteria that depend on episode length"
   — the statement that follows recomputes `episodeenddt` and
   `episode_days` but re-checks NO flag. `min_epi_dur_met` /
   `min_enr_aft_met` were evaluated in 50 on the pre-truncation end.
   Either the comment is stale or the re-check is missing; SAS
   parity behavior is not pinned by this package.
2. **`build_study` cannot produce event-producing studies**
   (severity: test infra). `event_claims` inner-joins
   `cfg_care_setting`; `CohortConfig.event_care_settings` defaults to
   `()` and `synth.build_study` never sets it, so any study built by
   the shared builder loses EVERY outcome claim. Real loaders always
   emit at least the wildcard `('**','*')` pair
   (`parse_care_setting("")`). Package C's `cohort_a` builder emits
   the wildcard rows; if other packages need events they must do the
   same. A quiet trap: the harness self-test fixtures happen not to
   assert attributed events.
3. **`person_days` can go negative** (severity: question). With
   `at_risk_start > 0` and an episode shorter than the at-risk offset,
   `span_days(atriskindexdt, min(...))` has no `greatest(0, ...)`
   floor (unlike followuptime). Pinned as observed behavior in
   `test_followup_zero_length_followup_via_at_risk_start`
   (person_days == −1). SAS parity unknown.
4. **`dose_excluded` reason may be NULL** (severity: cosmetic, not
   pinned by a test). The CASE in `dose_excluded` lacks the
   `l.cfdd IS NULL` guard the WHERE has, so a candidate excluded via
   NULL cfdd (rxsup ≤ 0 claim on the index day) gets a NULL reason
   row. `rxsup > 0` always holds in generated data (normalize
   filters), so this is unreachable through SCDM-conformant input.

## Open questions

* **OC-1 (pooled vs per-code "alternatives")**: INCLUSION.md says
  codes within a condlevel are alternatives (OR); the shipped SQL
  pools `n_days`/`supply_days`/`cumdose`/`afdd` across the
  subcondition's codes and evaluates the thresholds once. With
  codedays=2 and two codes each on one distinct day, pooled passes
  where per-code-OR would fail. `test_inclusion_codes_within_subcond_pool_their_days`
  pins the pooled reading; a per-code reading would be a semantic
  change, so this needs a contract ruling rather than a fix.
* **OC-2 (censor precedence at equal dates)**: exit_reason CASE order
  is documented and pinned (event > death > disenrollment >
  study_end > exposure_end), including the fall-through when a
  dose-censor truncation equals no recognized end. NOT specified
  anywhere: what should happen when the dose-censor date EQUALS the
  death date (today: 'death' wins because episodeenddt == deathdt) —
  the observed choice is pinned, the intended one is not.
* **OC-3 (person_days floor)**: see finding 3.
* **numevents semantics under event_count=0** duplicates count
  separately (pinned); under event_count=1 they dedup by
  (code, codetype). Which of the three SAS modes is "correct" for a
  given study is config, not contract — behavior only pinned as
  implemented.

## Pass records / verification

* `.venv/bin/python -m pytest tests/properties/test_pkg_c_*`
  `tests/properties/test_harness_stages.py --hypothesis-profile=quick`
  → **35 passed**.
* `.venv/bin/python -m pytest tests/test_rewrites.py` → **11 passed**.
* `.venv/bin/python -m ruff check --select E4,E7,E9,F tests/properties`
  → clean (3 unused-import findings fixed during development).
* `.venv/bin/python -m mypy src tests/properties` → **Success: no
  issues found in 34 source files** (repo config, not strict).
* Discovery statistics run: dose family (cheapest), tail captured
  above.
* No repo-wide drift encountered in mypy/ruff.

## Files added by this package

* `tests/properties/oracles/__init__.py` (package marker; borderline
  vs the "c_*.py only" naming rule — required for imports, empty)
* `tests/properties/oracles/c_models.py`
* `tests/properties/oracles/c_glue.py`
* `tests/properties/test_pkg_c_inclusion.py`
* `tests/properties/test_pkg_c_dose.py`
* `tests/properties/test_pkg_c_followup.py`

Shared infra, src/, manifest and README untouched (the calibration
patch was reverted before commit).
