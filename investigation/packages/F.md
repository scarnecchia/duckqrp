# Package F — comparison tools, validators, cross-stage metamorphic checks

## Pass 2 — aggregate comparison correction

Integrated discovery with `git merge --no-edit discovery`; merged revision
`6aaba22ade249c8d6f10123aeede4bfc3e9abb92`. All pass-2 commands below
used this revision plus the owned F changes in the resulting commit.
No production changes, push, publication or other worktree changes.

Classification: **test-oracle false positive**, not a production defect.
Seed 118, four patients, utilization+covariates, permutation seed 0:
threads=4 changed baseline std_age from 4.511922447625936 to
4.511922447625941 (about 1.18e-15 relative). DEPLOYMENT.md:220-221
explicitly allows last-binary-digit aggregate variation <4e-15 relative
while requiring exact counts/dates/flags. 96_baseline.sql:107-141 computes
unrounded avg/stddev_samp values, tags them kind='stat', then pivots them.

The oracle now preserves typed schema/rows for baseline and baseline_long.
Tolerance is rel_tol=4e-15, abs_tol=0, inclusive comparison (boundary policy
explicit); only baseline mean_*/std_* floats and baseline_long.v where
kind='stat' and stat names a mean_/std_ aggregate qualify. No other table
or column is relaxed. No rounding or float-to-string normalization is
introduced. Zero versus nonzero remains unequal. None differs from NaN;
NaN equals NaN, finite differs from NaN. Schema and exact other cells remain
checked. Perfect bipartite matching consumes each row once, preserving
multiplicity even when near-equality is nontransitive. Existing tie payload
assertions are unchanged. Seed118 is an explicit @example running all five
variants including engine baseline_long and emitted baseline parquet.

New sensitivity family: duplicate row addition/replacement, schema drift,
nonaggregate columns, null/NaN, zero/subnormal, gross corruption, reported
seed118 values, and 0–40 nextafter steps over binary exponents -100..100.
At powers of two 18 ulps pass and 19 fail; explicit 17/18/19/20 pins cover
the immediate representable threshold neighborhood independently of sampling.

### Commands and observed results

All commands use `.venv/bin/python -m`; pytest processes ran sequentially.

| command suffix | result | duration |
|---|---|---|
| pytest tests/properties/test_pkg_f_*.py --hypothesis-profile=discovery --hypothesis-show-statistics -q | 74 passed, 4 xfailed | 146.84s |
| pytest test_pkg_f_comparators.py test_pkg_f_compare_runs.py test_pkg_f_aggregate_comparison.py --hypothesis-profile=deep --hypothesis-show-statistics -q (paths under tests/properties/) | 48 passed | 0.58s |
| pytest tests/properties/test_pkg_f_*.py --hypothesis-profile=quick -q, first pair | 74 passed, 4 xfailed each | 40.84s / 41.79s |
| same quick command after adding four explicit threshold pins | 78 passed, 4 xfailed each | 42.16s / 41.63s |
| pytest tests/test_rewrites.py tests/test_determinism.py -q | 15 passed | 2.41s |
| ruff check --select E4,E7,E9,F tests/properties | clean | not timed |
| mypy src tests/properties | clean, 73 source files; three informational untyped-body notes in D | not timed |

Discovery/deep preceded only the four final deterministic threshold pins
and annotation correction; final quick gates cover those additions.
Logs retained locally: /tmp/f-pass2-{discovery,deep,quick1,quick2,existing,final1,final2}.log.
A final tail display command failed due to syntax; subsequent log extraction
verified actual results. No failed test run is counted as successful.

Stats: discovery aggregate boundary 500 passing, 0 invalid; 55.2% inside,
44.8% outside. Deep: 2000 passing, 0 invalid; 50.75% inside, 49.25% outside.
Original corruption family exhausted 36 cases (9x4), acceptance 28 (7x4).
Metamorphic discovery: 50 passing, 7 invalid, **12 distinct pair event
labels**, plus explicit examples and dense fixture; not exhaustive 28-pair
execution. Totals ordinary properties: 50 passing each. Known xfail
properties each replayed one failing case. No new production finding.

### Corrections and remaining gaps

Earlier claims below of 28/28 generated feature-pair coverage and 28
corruption cases were inaccurate: 28 pairs are representable, not guaranteed
executed; corruption space has 36 cases. Also the engine/files comparison
is not a proof of every output sidecar: txt/manifest content and raw file
layout are not compared; runtimes/signature contents are exempt. Nonbaseline
schema checks remain limited by the original exact row serialization.

Only baseline means/SDs currently receive tolerance. Summed amount floats
elsewhere remain exact, intentionally: broaden only after a witnessed
case and field-specific contract inspection. Baseline dynamic mean_/std_
columns are justified by the SQL pivot, not arbitrary output float types.
No exhaustive optional-feature-pair matrix or strengthened event-presence
guard was added; exposure presence alone does not guarantee outcome events.
Doctor still uses its production-generated fixture/default engine settings;
this pass did not refactor that preexisting test. Cohort batching remains a
single characterization fixture, not a promised invariant. Existing F2 is
still a lead/xfail, not newly established as a production contract violation.


Branch: `inv/0f462p/f-comparators-metamorphic` (base a0d0b93)
Scope: comparator correctness FIRST (AC.7), then metamorphic equivalence,
totals reconciliation, doctor/fingerprint/show/browse behavior.

## Files read

* Production under test: `src/qrp/parity.py`, `tools/parity_compare.py`,
  `tools/compare_runs.py`, `src/qrp/selfcheck.py`, `src/qrp/show.py`,
  `src/qrp/browse.py`.
* Contract/context: `docs/OUTPUTS.md`, `README.md` (determinism section),
  `docs/RUNBOOK.md` (partitioned layouts), `docs/PERFORMANCE.md`
  (cohort-config dedup; cores), `docs/DEPLOYMENT.md` (DuckDB 1.5/2.0),
  `docs/SAS_PARITY.md` (order-independent censoring), `docs/ARCHITECTURE.md`,
  `src/qrp/sql/30_exposure.sql`, `70_outputs.sql`, `92_cidadenom.sql`,
  `76_labs.sql`, `74_utilization.sql`, `78_mfu.sql`, `src/qrp/config.py`
  (care-setting parsing, gates), `src/qrp/scdm.py`, `src/qrp/outputs.py`,
  `src/qrp/pipeline.py` (gates, register_config, run), `tests/test_determinism.py`.
* Infra: `tests/properties/{conftest,synth,harness,strategies}.py`.

## Comparator-correctness evidence BEFORE metamorphic use (order)

1. Detection property (`test_comparators_detect_targeted_corruption`),
   evidence order in DETECTION_KINDS: row-set defects first —
   `missing_table` → `extra_row_new_key` (key set) → `duplicate_row`
   (multiplicity, caught ONLY by ROW COUNT) — then schema
   (`missing_column`), then values (`changed_string/count/date/code`),
   then `perturb_far` (tripled cell at tolerance 0.5, proving the
   relative rule still reports gross numeric corruption). Every case
   must exit 1, carry its classification marker, and name the table.
2. Acceptance property (`test_comparators_accept_equivalent_outputs`):
   row permutation, type-canonical forms ('30.0', DATE9. dates),
   missing sentinels ('NaN' vs '.', 'NULL' vs ''), the tool's own
   docstring float pair (34.5333251953125 vs 34.5333333, default 1e-6),
   and the two documented NOTE-only differences (extra column, extra
   table) — accepted AND still visible in the report.
3. Exact boundary pins: `same_value` unit level + through-the-files
   with exact binary fractions (4.0 anchor, boundary at 8.0; rule is
   `<=` so exactly-at is accepted).
4. `norm()` canonical forms pinned: DATE9./MMDDYY10./midnight
   timestamps → ISO; integral floats → int text; sentinel collapse;
   leading-zero codes kept.

Only after all of the above passed was the comparator used to judge
anything else.

## Properties added

| file | property | contract | oracle |
|---|---|---|---|
| test_pkg_f_comparators.py | detect_targeted_corruption | every corruption reported, exit 1, actionable diff | real `parity_compare.main()` via argv+stdout capture |
| | accept_equivalent_outputs | documented equivalences exit 0; notes stay visible | same |
| | tolerance boundary (unit + file) | `<=` at boundary; epsilon-above reports | exact float64 cases, tol 0.5 |
| | norm canonical forms | documented normalization | `parity_compare.norm` |
| test_pkg_f_compare_runs.py | load/last-wins/malformed-skips/smoke/--last/no-logs | run-log parser semantics | real `compare_runs` via importlib |
| test_pkg_f_meta_helpers.py | (oracle module, no tests) | — | — |
| test_pkg_f_meta_equivalence.py | pipeline_metamorphic_equivalence | permutation + sharding + threads 4→1 full output membership equality, per feature PAIR | engine `duckdb_tables` membership + manifest files read via a FRESH duckdb connection, rows normalized+sorted |
| | tie payloads identical (perm + threads4) | P4 same-day/same-supply ties SUM in stockpiled: rxsup 60, rxamt 12.5, numdispensing 2 | direct engine queries, values pinned |
| | dense feature combination equivalence | ALL 8 optional features together, perm + threads4 | same as above |
| | sharded input equivalence (hand fixture) | RUNBOOK partitioned layout = single file | same |
| | cohort batching characterization | NO documented promise — characterize, print, record | per-cohort filtered membership, printed counts |
| test_pkg_f_totals.py | totals_reconcile_across_outputs | L1==demographics; funnel monotonicity (member 1-6, episode 7-26); excluded>=0; cohort_final⊆ptsmasterlist⊆demographics; t2_cida == numcounts⋈denomcounts BY NAME | SQL over output parquet (fresh con) vs engine tables |
| | denominator_memberdays_cover_members | dennummemdays >= dennumpts where computed | SQL over denomcounts |
| | attrition_final_count_matches_cohort_final (XFAIL) | L27 descr declares "unique members in final cohort" | L27 remaining vs cohort_final members |
| | numerator_never_exceeds_denominator (XFAIL) | npts <= dennumpts at shared key | SQL join |
| | deterministic pins F1/F2 (XFAIL) | same, generate(1,0) / generate(4,0) | pinned |
| test_pkg_f_doctor_fingerprint.py | fingerprint repeatable/short-hex/layout-independent/content-sensitive/ignores-non-code | selfcheck docstring contract | `__file__` patched onto a COPY of the package (src/ untouched) |
| | doctor passes end to end | doctor exits 0, prints "All checks passed" | real `selfcheck.doctor()` |
| | broken study dict diagnostics | error names the caused fault (query period / Type 2 / no cohorts / coverage) | parametrized load_study_dict |
| | broken input dir | FileNotFoundError names missing tables + `--table-map` hint | real `qrp.run()` |
| | show smoke (available/listing/single table/where/diagnostics/to_csv/table_text) | viewer preserves schema+rows vs manifest+parquet | real tiny-pipeline output dir |
| | browse tree filter/sort | hidden hidden; dirs-first; case-insensitive suffix filter | `_Tree.filter_paths` unbound on a stub (no app) |

## Calibration record (mandatory)

* Patch (uncommitted, applied with file_edit_search_replace):
  `tools/parity_compare.py` — deduplicate rows right after `read_csv`
  in `compare_table`, so byte-identical duplicates vanish before the
  row-count check.
* Command: `pytest tests/properties/test_pkg_f_comparators.py::test_comparators_detect_targeted_corruption --hypothesis-profile=quick`
* Failing output (expected reason): `AssertionError: duplicate_row on
  stockpiled exited 0` — hit the deterministic `@example` immediately;
  ROW COUNT was the only detection left and dedupe killed it.
* Revert: `git checkout -- tools/parity_compare.py`; `git status` shows
  only package-F files; comparator family green again (26 passed).

## Verification

* `pytest tests/properties/test_pkg_f_* --hypothesis-profile=quick` →
  **58 passed, 4 xfailed** (the 4 xfailed are leads F1/F2, strict=False).
  Re-run three times after fixing the vacuity guard: the metamorphic
  property originally HARD-FAILED on generate() draws with zero
  matching exposure (e.g. patients=1, seed=2147483646 — all-noise
  codes). Vacuous draws are now rejected with hypothesis.assume()
  (empty-vs-empty guard preserved); equivalence failures remain hard
  failures.
* Discovery-profile statistics (comparators + compare_runs, cheapest
  family): detection 28 cases, all 9 corruption kinds at uniform
  11.11%; acceptance 28 cases, all 7 equivalence kinds at uniform
  14.29%; targets even (25% each). "Stopped because nothing left to
  do" — the kind×table space is covered, not sample-starved.
* `pytest tests/test_rewrites.py` → 11 passed.
* `ruff check --select E4,E7,E9,F tests/properties` → clean.
* `mypy src tests/properties` → clean (34 files).

## Coverage stats

* Corruption-type coverage: 9/9 kinds × 4 table shapes (uniform).
* Tolerance-boundary hit rate: 3/3 boundary cases (below/at/above)
  both at unit and file level, deterministic.
* Feature-pair coverage: 28/28 pairs generated (quick: 10 sampled
  examples; each example runs baseline+perm+shard+threads4+back-to-1);
  dense 8-feature combination deterministic.
* Together-vs-separate divergence rate: 0 divergent cohort-keyed
  tables on the batching fixture (characterization; printed by the
  test).

## Findings / leads

### F1 — attrition level 27 forward-fills; contradicts cohort_final (HIGH)
* Witness: `investigation/witnesses/pkg-f-lead-f1-attrition-l27-forward-fill.txt`
* Contract: the L27 row's own descr — "Information - Unique number of
  members in final cohort" (70_outputs.sql; docs/OUTPUTS.md "attrition
  now reproduces SAS's table").
* Expected vs actual: on `generate(patients=1, seed=0)` every episode
  vanishes after `index_candidates` (cohort_final and ptsmasterlist
  EMPTY), yet L7..L27 all show remaining=2 — every counted episode
  step's `excluded` is NULL, so `last_value(n IGNORE NULLS)` carries
  the last member count (2, which itself counts 2 index candidates
  against ONE patient) to L27. cohort_b (zero claims at all) shows
  remaining=1 at every level, forward-filled from the demographics
  count at L1.
* Suspected mechanism: episode-level counts computed from a relation
  that is empty in this scenario → NULL instead of 0; forward-fill
  turns "no episodes" into "everything survived". The funnel reports
  final members that do not exist.
* Severity guess: high — attrition is the headline funnel downstream
  readers check first; docs/OUTPUTS.md itself warns about
  plausible-looking counts that "meant nothing".

### F2 — numerator exceeds denominator at stratified cells (MEDIUM)
* Witness: `investigation/witnesses/pkg-f-lead-f2-num-gt-den.txt`
* Contract: a numerator counts members of the population the
  denominator counts (npts <= dennumpts at the shared key).
* Expected vs actual: `('001','cohort_b','75+',NULL) npts=1
  dennumpts=0` and `('002','cohort_b','75+','F',NULL) npts=1
  dennumpts=0` on `generate(patients=4, seed=0)`. denomcounts has NO
  75+ rows at all (dropped by the `HAVING n_pts > 0`), while t2_cida
  keeps a zero 75+ row and numcounts puts a member's episode there.
* Suspected contract: age-group anchor mismatch (numerator ages the
  member at the index date, denominator at a different anchor) or the
  denominator event-shave emptying a cell whose episode survives.
* Severity guess: medium — unstratified (000) reconciles; stratified
  cells disagree, which feeds CIDA reporting.

### Comparator semantics notes (documented behavior, not defects)
* Duplicate rows are caught ONLY by the ROW COUNT check (the keyed
  dict collapses duplicates). The calibration proves this is the load-
  bearing check — do not "optimize" it away.
* Extra table / extra column on the deliverable side are NOTEs (exit
  0) by design; missing table/column are issues (exit 1).
* `same_value` alone does not treat "NaN"/"nan" as missing — only
  `norm()` does; equality of NaN-vs-empty holds only through the file
  path (read_csv applies norm first).

## Open questions

1. No documented equivalence promise exists for running cohorts
   TOGETHER vs SEPARATELY (docs/PERFORMANCE.md's claim is a within-run
   config-dedup statement). Characterized on one fixture: 0 divergent
   cohort-keyed tables. Should this be promised and asserted?
2. Attrition: which relation are the L7+ counts computed from, and is
   a NULL count (forward-filled) ever the intended representation of
   "0 episodes"? (F1)
3. Which anchor date does each stage use for agegroup assignment —
   index date, enrollment, or a per-year boundary? (F2)
4. Tolerance semantics: the relative rule `abs(a-b) <= tol*max(1,|a|,
   |b|)` accepts exactly-at-boundary (`<=`); is that intended (SAS
   parity says yes historically), and should small-magnitude cells
   (slack floor 1.0 * tol) have a tighter absolute floor?
5. threads=1 vs 4 equivalence: asserted here on tiny data (README
   promise); the repo's own test only checks 5 tables byte-identical.

## Incomplete / notes

* The property suite does NOT yet assert the batching equivalence (by
  design — no documented promise; see open question 1).
* Feature `geography` may produce empty outputs on some seeds (zipcode
  join) — the membership comparison still covers the stage's presence;
  a denser zip fixture would strengthen it.
* `oracles/f_*.py` layout was abandoned for mypy reasons: a subpackage
  under the CLI-package dir `tests/properties` gives mypy two module
  names for one file (both with and without `__init__.py`), breaking
  the mandated `mypy src tests/properties`. Helpers were folded into
  allowed `test_pkg_f_*.py` modules (`test_pkg_f_meta_helpers.py`
  carries the shared oracles; pytest collects it as a module with zero
  tests, which is harmless).
* Two stale-detection notes: an early probe "encounter table not
  resolved" lead was a harness-usage artifact (data not written);
  `resolve_table` resolves `encounter/` correctly.
