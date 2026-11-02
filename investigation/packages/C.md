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

## Pass 2

Merged discovery at `3dfe463c5be9ecca444ed22b41d8a4accc3f04bb` before this pass. All runs below used that revision plus the package-C changes committed with this section; no production/shared-infrastructure edits were made.

Changes: added the C-1 pre-truncation length-filter characterization with non-strict xfail; added washout/window equality and washout/blackout boundary cases; expanded inclusion generation with RX supply rules and exact-fit events; added pure day-set overlap and enumerated at-risk-day checks. Wildcard care-setting rows remain in use. The merged min==max cfdd pin remains. The merged negative-pre-floor witness now asserts zero, and pure generation also covers empty at-risk spans.

Corrections to pass-1 findings: negative person_days was fixed by fix3-c3; the oracle now floors at zero. C-1 is a SAS/Python ordering parity gap (DB-5), not a resolved comment-only issue: SAS checks inclusion flags after truncation, whereas Python filters before truncation and does not recheck. The new expected-failure test characterizes that shape, not approval of the behavior.

### Recorded runs

Revision for all three records: `3dfe463c5be9ecca444ed22b41d8a4accc3f04bb` + pass-2 package changes. Durations are Bash `time` wall seconds; pytest durations shown separately.

1. Command: `.venv/bin/python -m pytest tests/properties/test_pkg_c_*.py --hypothesis-profile=discovery --hypothesis-show-statistics`
   Result: **35 passed, 1 xfailed**, pytest 51.40s, wall **51.577s**. Dose/inclusion/follow-up each generated 50 passing examples; pure properties each generated 500.
2. Command: `.venv/bin/python -m pytest tests/properties/test_pkg_c_pure.py --hypothesis-profile=deep --hypothesis-show-statistics`
   Result: **2 passed**, pytest 0.82s, wall **1.008s**. Each property generated 2000 passing examples, zero failing/invalid.
3. Command: `.venv/bin/python -m pytest tests/properties/test_pkg_c_*.py tests/properties/test_harness_stages.py --hypothesis-profile=quick`
   Result: **42 passed, 1 xfailed**, pytest 25.62s, wall **25.774s**. Replay check repeated the same command: **42 passed, 1 xfailed**, pytest 25.81s, wall **25.964s**.

The earlier discovery launch did not execute pytest because `/usr/bin/time` was absent (exit 1). It was replaced with Bash timing; the successful record above supersedes it.

### Statistics excerpts and gaps

```text
Discovery dose: 50 passing, 0 failing, 8 invalid
55.17% distinct-day vs row divergence visible
32.76% lookback boundary exact hit
5.17% wash == cum_per degenerate window
Discovery follow-up: 50 passing, 0 failing, 0 invalid
8.00% event attribution: some
16.00% washout-or-blackout drops: some
Discovery inclusion: 50 passing, 0 failing, 7 invalid
33.33% window boundary exact hit
17.54% codedays exact fit
Discovery pure: 500 passing per property
40.80% negative pre-floor span; 32.20% coincident starts
Deep pure: 2000 passing per property, 0 failing, 0 invalid
47.25% negative pre-floor span; 26.55% coincident starts
```

RX minrxdays exact-fit instrumentation produced no event in this discovery sample; deterministic RX overlap threshold tests remain, but randomized exact-fit coverage needs improvement. Event attribution also remains sparse. No new lead was found in this pass. C-1 remains open; the non-strict xfail is expected, not a passing parity assertion.

Additional verification: `.venv/bin/python -m pytest tests/test_rewrites.py tests/test_determinism.py` — 15 passed (2.56s); `.venv/bin/python -m ruff check --select E4,E7,E9,F tests/properties` — clean; `.venv/bin/python -m mypy src tests/properties` — success, 73 files (only informational untyped-body notes in package D).

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

## Pass 1 verification correction

The orchestrator's verification of pass 1 found a deterministic
failure on the committed tree that my final pass-1 run did not:

    test_pkg_c_dose.py::test_dose_boundaries_match_model FAILS
    (quick profile, example-DB replay): one patient, one dispensing
    2014-02-15 rxsup=4 rxamt=8.0, dose params (None, None, 1.0, 1.0);
    SQL reason = 'max_cfdd', model expected 'min_cfdd'.

**Root cause (oracle bug, not a production defect).** My
`dose_verdict` evaluated ONE lumped `any_cfdd_fail` boolean and then
applied a min-first PRIORITY to pick the reason. The shipped stage
does neither: the exclusion CASE gives each bound its OWN predicate
(`round(cfdd) < min_cfdd` for the min branch, `round(cfdd) > max_cfdd`
for the max branch) evaluated PER JOINED ROW, and dose_excluded is
DISTINCT over (candidate, reason). With min_cfdd = max_cfdd = 1.0 and
round(cfdd) = 2.0, only the max predicate holds, so the reason is
'max_cfdd' — the equal-min/max corner makes the lumped-priority
reading wrong systematically (any candidate with a cfdd at or above
the shared bound). I re-read 42_dose.sql under arbitration and
CONCUR the SQL is internally consistent: the WHERE and CASE use the
same per-bound predicates, INCLUSION.md and the stage header specify
no reason attribution, and per-row CASE attribution with DISTINCT is
a coherent reading. No contract document contradicts it, so this was
fixed in the oracle, not flagged as a lead.

A second, latent modeling gap surfaced with the same fix: two
same-day claims failing DIFFERENT bounds produce TWO reason rows for
ONE candidate (DISTINCT over the fanned join). A single-reason model
cannot represent that; the model and the comparison now carry reason
SETS. The CASE's cfdd branches also lack the NULL guard its WHERE
has, so a NULL-cfdd row is excluded with reason None — modeled
explicitly (unreachable through conformant SCDM, rxsup > 0), along
with the LEFT JOIN's virtual all-NULL row when the index day has no
claim_dose rows.

**Why generation + hand cases missed it.** The failing conjunction
needs min_cfdd and max_cfdd BOTH drawn (~25% of knob profiles) AND an
index-day claim with round(cfdd) above the bound (~30% of the
amount/supply grid) — roughly 8% per fresh example. My hand cases
never set min_cfdd and max_cfdd together (each exercised one bound
with the other None), a genuine oracle-test design gap; the
generated property had a ~10-example quick budget, most of it spent
replaying the DB's saved covering examples, and never drew the
conjunction during development.

**Why the final run passed while the committed tree fails.** The
failing example entered the suite's example database at 12:15,
AFTER my final verification (~11:55) and after commit afb31f9:
`find tests/properties/.hypothesis-db -newer <test file>` shows the
dose-test entries written by the orchestrator's runs, and
`investigation/witnesses/` is empty — my runs never failed, so
Hypothesis had nothing to persist as failing. Plainly: my final
verification ran WITHOUT this example present; the orchestrator's
run drew it fresh (single-test invocation), failed, and Hypothesis
SAVED it — their two repros then replayed it deterministically from
the DB. After the fix, my re-run replays the saved example first and
passes, and Hypothesis prunes the failing entry once it replays
clean. Lesson recorded: an all-green run only covers the examples it
actually drew; the DB makes later runs STRICTLY stronger, which is
exactly how a corner that survived pass 1 surfaces on replay.

**New boundary pins** (deterministic tests in test_pkg_c_dose.py):

* `test_dose_reason_equal_min_max_corners` — min=max=1.0 with cfdd
  2.0 → reasons {max_cfdd} (the replayed failing shape); cfdd 0.25 →
  {min_cfdd}; cfdd exactly 1.0 and cfdd 0.5 (rounds to 1) → NOT
  excluded (exactly-at-bound is inclusive on both sides).
* `test_dose_rounding_crosses_bound_raw_does_not` — cfdd 1.5 with
  max 1.5: raw passes, round 2 excludes; cfdd 0.5 with min 0.8: raw
  fails, round 1 passes.
* `test_dose_two_same_day_rows_two_reason_rows` — two same-day
  claims failing different bounds: dose_excluded carries TWO rows
  for the candidate; model and comparison carry the reason SET
  {min_cfdd, max_cfdd}.

**DB sweep for other saved failures.** The database holds three test
keys matching the three generated properties (5 + 2 + 1 blobs). After
the fix, the full suite replays every saved entry and passes
(38 passed with the DB present); witnesses/ still contains only the
README, so no other pkg C property has ever recorded a failure. No
other failing examples existed.

## Pass records / verification (post-correction)

* `.venv/bin/python -m pytest tests/properties/test_pkg_c_*`
  `tests/properties/test_harness_stages.py --hypothesis-profile=quick`
  → **38 passed** (31 package C + 7 harness self-tests), with the
  example DB present — the orchestrator's saved failing example
  replays and passes against the corrected oracle.
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
