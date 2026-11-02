# Package A — Input contracts, normalization, SQL parsing

Branch `inv/0f462p/a-input-contracts`, worktree
`sessions-v1/0f462p-motto/wt/a`. PRIVATE campaign artifact.

## Scope

`src/qrp/config.py`, `src/qrp/inputfile.py`, `src/qrp/scdm.py`,
`src/qrp/sqlsplit.py`, `tools/sas_inputfiles_to_json.py`, INPUTS.md —
config translation semantics, rejection boundaries, SQL split
execution equivalence, SCDM alias/partition/optional-column
equivalence, plus the assigned demographics full-tie lead.

## Files read (production)

config.py (in full, incl. load_study_dict/_parse_inclusions/
_parse_cohort_codes/_query_period/_parse_stockpiling/validate paths),
inputfile.py, scdm.py, sqlsplit.py, engine.py, pipeline.py (run +
register_config + fmt construction), sql/10_normalize.sql,
sql/00_macros.sql, 30/45/50/70/92 excerpts, INPUTS.md,
tools/sas_inputfiles_to_json.py, investigation/README.md,
investigation/manifest.json.

## Oracle-independence statement

* **Config** (`oracles/a_config_semantics.py`): every expected value is
  a hand-written LITERAL derived from INPUTS.md (field tables, SAS
  date-integer epoch rule, casing/indirection rules). SAS date
  integers are re-derived as `date.toordinal() - date(1960,1,1)
  .toordinal()` — the epoch spelled out here, never imported. The
  representation-variant builder only re-spells (whitespace, key
  casing, list-vs-string, indirection, ISO vs integer dates); the
  expected semantics stay fixed. No production translation code is
  called to compute an expectation.
* **SQL split** (`oracles/a_sql_grammar.py`): the grammar AUTHORS
  programs and records boundaries, (kind, target) classifications and
  typed effects as it writes; `replay()` computes the final state with
  plain Python list ops. Structural invariant checked against raw text
  for any input: returned statements are verbatim in-order slices.
  Unsupported syntax (dollar quotes, BEGIN/END, macro bodies) is
  characterized via split counts, never asserted correct.
* **SCDM** (`oracles/a_scdm_layouts.py`): row values come only from
  `synth.rows_for_table` (shared serializer boundary); the module
  varies PHYSICAL FORM only (shards, hive nesting, alias names,
  fingerprint-only names, optional columns absent vs NULL vs present,
  `rx` vs `ndc`), then real `qrp.run` runs are compared. The thin
  `run_at` wrapper exists because `harness.full_pipeline_run` pins the
  layout to `synth.write_parquet`; it is the same production
  `qrp.run` call, not a re-implementation.
* **Tie-break** (`oracles/a_demographics_tie.py`): asserts only
  documented contracts (one survivor row, demographics_multi
  membership, pov1 exclusion, total-order outcome where an order
  column separates) and RECORDS the undefined cases' survivors. No
  spec invented.

## Properties written

| test | contract | oracle |
|---|---|---|
| `test_config_translation_semantics` (2 classes: authored cases + representation metamorphic) | load_study(path) of SAS-shape/hand-written/lookup-supplied files lands on the hand-written semantics; different accepted spellings of one study translate identically | literal expectation table + fixed-core metamorphic comparison |
| `test_invalid_config_boundaries` | near-valid variants classify as documented rejection (message fragment), documented accept (exact semantics), or incidental crash (typed characterization) | pre-classified hand case table |
| `test_sql_split_execution_equivalence` (+ mandatory shapes + degenerate inputs) | split+sequential execution ≡ DuckDB single-call multi-statement execution: final schemas, values, multiplicities in fresh threads=1 connections; per-statement returns never asserted | authored grammar replay |
| `TestSqlSplitTextIsVerbatim` | every returned statement is a verbatim in-order slice of the source, for any input incl. unsupported syntax | structural (text find) |
| `test_scdm_alias_and_partition_equivalence` | sharded/hive/alias/fingerprint/mixed layouts, permuted rows, optional columns absent≡NULL, `ndc` code column: end-to-end outputs identical; one demographics row per patid; demographics_multi == duplicate patids; pov1 excludes them; no invented codes | same dataset, re-materialised, real runs compared |
| `TestCodeIdentityPreserved::test_case_is_not_normalized` | `E00001` vs `e00001` are different codes end-to-end | injected pair + count checks |
| `TestDemographicsFullTie` battery | documented contracts across 3 fixtures × 2 orders × threads {1,4} × {registered tables, parquet}; evidence file written | fixtures + observation records |

## Calibration record (seeded defect)

Patch: `investigation/packages/a-calibration-seeded-defect.diff`
(working-tree only, never committed). Mutation: `sqlsplit.py` tail
push conditioned on `tail.rstrip().endswith(";")` — never true — so a
final statement without a trailing semicolon is silently dropped.

```
python -m pytest tests/properties/test_pkg_a_sqlsplit.py \
    --hypothesis-profile=quick   # while mutated
→ 11 failed, 7 passed
```

Failing for the expected reason:

* `test_empty_and_degenerate_inputs`: `split_statements("SELECT 1")`
  → `[] == [None]` failed (tail push gone).
* `test_mandatory_shapes[*]` + `test_sql_split_execution_equivalence`:
  statement shapes `[] != grammar-authored` and split-vs-multi final
  state divergence (final table missing from the sequential run).

Reverted with `git checkout -- src/qrp/sqlsplit.py`; clean tree then
passed 18/18. Verified no `src/` changes remain (`git status`).

## Pass records

| command | revision | result | duration |
|---|---|---|---|
| `pytest tests/properties/test_pkg_a_{config,sqlsplit,scdm,tiebreak}.py tests/properties/test_ac1_infrastructure.py --hypothesis-profile=quick` | pre-commit working tree | 95 passed, 1 xfailed | 29.7s |
| same, after lint fixes | pre-commit | 95 passed, 1 xfailed | 29.3s |
| `pytest tests/properties/test_pkg_a_config.py "…sqlsplit.py::TestSqlSplitTextIsVerbatim" --hypothesis-profile=discovery --hypothesis-show-statistics` | pre-commit | 40 passed | 0.5s |
| `pytest tests/test_rewrites.py` | pre-commit | 11 passed | 1.2s |
| `ruff check --select E4,E7,E9,F tests/properties` | pre-commit | clean | <1s |
| `mypy src tests/properties` | pre-commit | clean (36 files) | ~20s |

Coverage statistics (discovery profile, pure modules):

* `test_representation_metamorphic`: 500/500 passed; events —
  indirect 53.4%, weird_case 51.6%, pad_scalars 50.6%, sex_string
  48.2%, dates iso 45.4% / sasint 29.8% / digitstring 24.8%. Three
  `@example` pins cover the all-off and all-on corners.
* `test_generated_programs_are_slices`: 500/500 passed.
* DB-backed properties run at the expensive budgets (10 examples in
  quick), with deterministic parametrized mandatory shapes carrying
  rare-interaction coverage (tail-without-semicolon fires ~40%, escaped
  quote ~30%, quoted identifier ~20% of generated programs).

## Findings / leads

**A-1. Documented duplicate-ID validations are unreachable from input
files.** `StudyConfig.validate` rejects "duplicate cohortgrp" and
"duplicate covarnum", but `load_study_dict` folds `cohortfile` rows
into a dict keyed by cohortgrp (LAST duplicate row silently wins) and
merges `covariatecodes` rows by covarnum via `setdefault` (FIRST row's
windows + union of codes). No input file can ever reach either guard.
Severity: low-medium (a doubled cohort row with conflicting parameters
is silently resolved instead of rejected; SAS would use one
`enr_&num` build). Witness: characterization cases
`duplicate_cohortgrp_rows_collapse_to_last` and
`duplicate_covarnum_rows_merge` in `test_invalid_config_boundaries`
(no crash — accepted-by-design evidence, classified in
`oracles/a_config_semantics.py`).

**A-2. Documented-optional `dispensing.rxamt` crashes normalize.**
INPUTS.md lists `rxamt` as Optional ("dose only") and `scdm.py`
declares it optional; `qrp inspect` therefore reports such an extract
as compatible, but `10_normalize.sql` references `rxamt`
unconditionally → `BinderException: Referenced column "rxamt" not
found` at the first stage. Expected (per INPUTS.md): run without dose
restrictions. Actual: crash. Severity: medium (probe says ok, run
dies — the failure mode the package works hardest to avoid). Pinned as
`TestDocumentedOptionalRxamt::test_optional_rxamt_runs` marked
`xfail(strict=True)` — flipping to XPASS when fixed. Repro snippet
recorded in this ledger's command history; no witness file (xfail is
the witness).

**A-3. Demographics full-tie lead — ANSWERED.** Evidence:
`investigation/witnesses/a-demographics-full-tie-evidence.txt`
(24 battery cases). Conclusions from the shipped SQL:

* Rows separating in ANY order column: documented total order decides
  (verified: smaller postalcode always survives; stable across both
  insert orders, both writers, threads 1 and 4).
* Rows agreeing on ALL five order columns (differing only in
  `postalcode_date`, which is NOT ordered): the survivor is
  **insertion-order dependent** — order AB keeps the first row's
  values, order BA keeps the second's. Observed stable across the two
  writers and across threads 1 vs 4 (DuckDB keeps the first-encountered
  row here; small tables, `preserve_insertion_order=false` — larger
  data could differ and was not probed).
  So the tie-break is NOT a total order: re-extracting the same
  logical data with different physical row order changes the survivor's
  observable content (`zip_date`, potentially `sex_raw`/`race`/...).
* Containment: `demographics_multi` exclusion held in every case —
  duplicate-demographics patids never reach pov1, so cohort outputs
  are unaffected. Residual exposure: output-side consumers of
  demographics content by patid (70_outputs level-1 population rows
  use patid only; the attrition/geography join at 70_outputs.sql:109
  could surface the survivor's content for a dup patid).
* Severity: low (contained), but the comment in 10_normalize.sql
  claims a "TOTAL order" — that claim is false for this input family.
  Witness: the evidence file above; reproduction:
  `pytest tests/properties/test_pkg_a_tiebreak.py -q`.

## Open questions for the orchestrator

1. A-3: is the intended demographics tie-break "first row in file
   order" (what DuckDB does today, stable at these sizes) or should
   the QUALIFY extend to a genuinely total key? The observed
   insert-order dependence contradicts the in-file "TOTAL order"
   comment; SAS parity for full-tie pairs is unknown.
2. A-2: fix shape for `rxamt` — resolve via the `opt_*` mechanism
   (treat as optional like `rx_codetype`) or amend INPUTS.md to declare
   it required? (gen_synthetic writes INTEGER rxamt — separate open
   question 2 in README — which interacts with any fix.)
3. A-1: should duplicate cohort rows be REJECTED (align with the
   validate guard) or is last-row-wins intended? Real files have
   produced doubled rows before (per code comments).
4. `tools/sas_inputfiles_to_json.py` writes `qrp_parameters_scalars`
   (a dict) plus logical-name tables, while `inputfile.load()` expects
   a `qrp_parameters` row list — tool output resolves only through the
   literal-name fallback and reports scalars as unmapped extras.
   Harmless today; worth a look when the tool is next touched
   (outside my scope to change).

## Incomplete / caveats

* The tie-break battery used small tables (2 tie rows + 1 control);
  the observed first-row-wins stability at threads=4 is not proof at
  production scale — flagged in the evidence file.
* CSV layouts and `table_map`-driven resolution are covered indirectly
  (INPUTS.md contracts) but not exercised end-to-end here; package D/E
  may want them.
* `tools/sas_inputfiles_to_json.py` was read but not property-tested
  (needs pyreadstat/sas7bdat fixtures; no synthetic SAS writer
  exists). Its JSON contract is covered from the consumer side via
  `inputfile.load()`.
