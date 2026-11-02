# QRP-DuckDB property-based correctness investigation — campaign ledger

**PRIVATE — NOT AUTHORIZED FOR PUBLICATION.** Everything in this tree is
a local-only artifact of a correctness investigation of qrp-duckdb
(Sentinel QRP Type 2). Do not push, share, or reference it outside the
campaign.

Base revision: `c3e60db5d08343b1cebbb90d234602f09cb0d70d` (discovery ==
Testing at campaign start). Each worker commits to its own
`inv/0f462p/<pkg>` branch inside its own worktree. The main checkout,
`main`, `Testing`, `fix/sas-int-dates`, the preexisting stash and all
preexisting worktrees are off-limits to campaign workers.

## Ground rules

1. **Synthetic data only.** No external datasets, no network, no SAS.
   `tests/properties/synth.py` is the single data builder; `tests/properties/harness.py`
   is the single execution harness (stage prefix/subset via
   `Engine.script_stage`, or the real pipeline end to end via
   `full_pipeline_run`). Nothing may copy shipped SQL snippets or
   re-derive their semantics locally.
2. **Oracle independence.** An oracle must express the intended
   behavior independently of the implementation: no importing pipeline
   helpers to compute an expectation, no reusing `_combo_sql`-style
   generators as the expectation. Serialization may be shared
   (`synth.rows_for_table`); semantics may not. Two "independent"
   oracles that share one wrong assumption agree on the same wrong
   answer — derive expectations from the CONTRACT (INPUTS.md, docs,
   SAS references cited in code comments), then re-derive the second
   time from a different angle.
3. **Zero production changes.** No edits under `src/qrp/` and no edits
   to existing tests. Confirmed findings get their own fix branch later,
   outside this ledger's scope.
4. **Witnesses over seeds.** When a property fails, the shrunk example
   is written to `investigation/witnesses/` by the conftest hook.
   Reproduce from the witness file, not from a seed: strategy changes
   silently change what a seed generates.
5. **Shared CPU.** An operator-wide baseline `python -m pytest` may be
   running concurrently. Generated runs use DuckDB `threads=1`
   (harness default); serialize any full-suite run.

## Ownership

| Package | Branch | Scope | Status |
|---|---|---|---|
| infra | `inv/0f462p/infra` | Property-test infrastructure: profiles/budgets/witnesses, synthetic builder + StudyConfig builder, stage + parquet harnesses, harness self-tests, AC.1 | complete |
| A | *assigned by orchestrator* | *contract under test recorded in manifest when known* | pending |
| B | *assigned by orchestrator* | *as above* | pending |
| C | *assigned by orchestrator* | *as above* | pending |
| D | *assigned by orchestrator* | *as above* | pending |
| E | *assigned by orchestrator* | *as above* | pending |
| F | *assigned by orchestrator* | *as above* | pending |

Package workers: read "Contracts under test" and "Ambiguity log" below
before designing properties — several pipeline semantics are easy to
assume wrong (and were assumed wrong once already; see the log).

## Contracts under test (from infra's audit, with evidence paths)

* **Normalization contract** (`src/qrp/sql/10_normalize.sql`): lowercase
  names, DATE-typed dates, one demographics row per patid via a TOTAL
  order tie-break (`birth_date, sex, race, hispanic, postalcode`), a
  `demographics_multi` set for patients with >1 demographic rows, and
  death extraction from both the death table and encounters discharged
  'EX'.
* **Duplicate demographics disqualify.** A patient present in
  `demographics_multi` is dropped from POV1 entirely
  (`45_pov1.sql` pass 1, `WHERE mx.patid IS NULL`) — not deduped.
  Ambiguous demographics remove the patient from every cohort.
* **Exposure vs index bounding.** `exposure_claims` is query-period
  bounded only; the enrollment bound applies at index detection and the
  POV1 enrollment containment join. A claim outside every enrollment
  span still enters exposure/washout/covariate windows.
* **Pre-index enrollment.** Master-list admission requires
  `enr_start <= indexdt - enr_days` (50_episodes.sql `min_enr_days_met`)
  plus post-index availability and the other `_met` flags. Fixtures
  wanting membership must enroll patients ≥ enr_days before the index.
* **Same-day dispensings** are combined per stockgroup by the
  STOCKPILINGFILE ops (default sum/sum) before episode construction;
  same-supply ties with different amounts are the tie-break payload.
* **Stage gates ↔ plan_stages.** `STAGES` is the execution order;
  `plan_stages()` returns gated-in names only. `any_*` flags on
  StudyConfig map to gates as recorded in `pipeline.STAGES`.
* **fmt plumbing.** Stage SQL is `.format(**fmt)`-substituted; the key
  set (`read_*`, `dispensing_code`, `dispensing_codetype`,
  `enrollment_chart`, `opt_*`, `di_rel`, `incl_rel`, `dose_rel`,
  `start_date`, `end_date`, `censor_date`, `claims_from`, `claims_to`,
  `indata`, `run_envelope`) is mirrored by the harness and pinned by
  test_harness_pipeline.py::test_stage_harness_matches_full_pipeline.
* **Optional SCDM columns** resolve to themselves when present and to
  `NULL` when absent (`opt_*`); a fixture omitting them must still run.
* **`claims_from` scan bound** = start_date − widest_lookback_days
  (unbounded if any rule reaches beyond); claims outside never enter the
  pipeline, so a wrong bound is invisible downstream.

## Commands

```bash
# property suite (profiles: quick 50 / discovery 500 / deep 2000;
# expensive database-backed properties get 10/50/100 per profile)
python -m pytest tests/properties --hypothesis-profile=quick --hypothesis-show-statistics
python -m pytest tests/properties --hypothesis-profile=discovery

# existing suite smoke (do NOT run the full suite; baseline is recorded
# separately by the operator)
python -m pytest tests/test_rewrites.py

# style/type gates used by infra
python -m ruff check --select E4,E7,E9,F tests/properties
python -m mypy src tests/properties
```

Environment: `.venv` in the worktree,
`uv venv .venv --python 3.13 && uv pip install --python .venv/bin/python -e '.[dev]'`.

## SHAs, profiles, durations

Recorded in `manifest.json` (`passes` and `packages`; update both when
a pass re-runs). infra's recorded passes: quick-profile suite ~9s wall;
`tests/test_rewrites.py` smoke ~1.2s.

## Witnesses

`pytest_exception_interact` in `tests/properties/conftest.py` writes
one self-contained file per falsifying example to `investigation/witnesses/`
(profile, hypothesis version, reproduce command, and the verbatim
"Falsifying example" block). Findings must reference a witness file;
the template's `witness/repro command` field is mandatory.

## Ambiguity log

See `manifest.json` `open_questions` and the table below. These are
genuine contract questions — surface them, do not invent a spec to make
a test pass.

1. **Total-order tie-break completeness.** The demographics QUALIFY
   orders by `birth_date, sex, race, hispanic, postalcode`. If two rows
   of one patid agree on ALL of those, the order is not total and the
   survivor is arbitrary — reproducibility, then parity, then break.
   Lead for whoever owns normalization properties.
2. **INTEGER rxamt in the operator generator.** `tools/gen_synthetic.py`
   writes `rxamt` as an integer expression (BIGINT), which triggers the
   pipeline's own "rxamt stored as INTEGER" truncation warning and
   changes `rxamt > 0` semantics for fractional amounts. infra's builder
   deliberately writes DOUBLE. Any oracle calibrating against gen_synthetic
   data inherits the truncation semantics.
3. **`claims_from` interaction with enrollment spans.** Enrollment spans
   are not claim rows; is the scan bound meant to apply to them (it
   does not — spans are read whole)? Contract question, low impact.
4. **Encounter tables are never column-matched** (`fingerprint=False`):
   a site-extract shape test that relies on fingerprinting will not find
   them; only name/table_map resolution applies.
5. **`denominators` row grain** is cohort × stratum level, not cohort:
   cohort-level assertions need DISTINCT cohortgrp (infra hit this).

## Follow-up leads

* Tie-breaking family (dispensing same-day/same-supply, demographics
  QUALIFY, stockpile push-forward cutoffs) — packages owning episode and
  stockpiling properties should generate near-tie families, not just
  exact ties: amounts differing by epsilon, supplies differing by 1,
  spans overlapping by 1 day.
* `run_envelope` paths with encounter `ddate` NULL (one-day stays) and
  discharge-status deaths colliding with death-table rows.
* Utilization counts when several encounter rows share `encounterid`.
* Combo covariates (codecat 'CC') reference earlier covarnums only —
  generate studies whose CC references a LATER covarnum and compare
  against the documented one-at-a-time semantics.
