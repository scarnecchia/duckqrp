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
| A | *assigned by orchestrator* | *contract under test recorded in manifest packages[] when known* | pass2-complete |
| B | *assigned by orchestrator* | *as above* | pass2-complete |
| C | *assigned by orchestrator* | *as above* | pass2-complete |
| D | *assigned by orchestrator* | *as above* | pass2-complete |
| E | *assigned by orchestrator* | *as above* | pass2-complete |
| F | *assigned by orchestrator* | *as above* | pass2-complete |

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
* `_cdm_procedure_raw` references `px_codetype`/`enctype`
  unconditionally (10_normalize.sql) though INPUTS.md declares them
  optional for the optional `procedure` table — A-4's exact class
  (candidate A-5; found during the A-4 arbitration, not fixed).
* Denominator time stratification still does not split eligible
  windows at year/month boundaries (SAS's period splitting); after
  fix2-f2 the age dimension splits per band, the `year` stratum is the
  span-start year. Same mechanism family as F2; unquantified.

## Campaign log (orchestrator)

**2026-11-02 — baseline recorded before any campaign change.**
Campaign venv (session-local, `uv venv` + `-e '.[dev]'` at base SHA
c3e60db): Python 3.13.13, duckdb 2.0.0, pytest 9.2.0, pandas 3.1.0.
First run was 15 passed / 327 skipped — the documented fixtures at
`/tmp/qrp_data/100k` were absent; generated exactly per README
(`tools/gen_synthetic.py --out /tmp/qrp_data/100k --patients 100000`,
seed 42; repo untouched). Full baseline then:

- `python -m pytest -q` → **337 passed, 2 failed, 3 skipped, 433 s**.
- Failures (both deterministic, reproduced identically in the operator's
  own `.venv` on a quiet machine, so NOT load flakes and NOT campaign
  artifacts):
  - `tests/test_pipeline.py::test_spilling_is_attributed_to_the_statement_that_caused_it`
  - `tests/test_pipeline.py::test_spill_summary_names_the_statement_in_the_log`
  - Mechanism: `_duckdb.OutOfMemoryException` inside the tests' forced
    ~286 MiB DuckDB limit (spill path never attributed). Pre-existing
    on discovery/Testing; classification deferred to Phase 4 — not a
    campaign discovery.
- Skips (environmental, documented): production input file unavailable
  (test_pipeline.py:6050); 2m dataset not generated (test_ui.py:137,330).

**2026-11-02 — infrastructure integrated.**
`inv/0f462p/infra` (d4ea62e; 3 commits, scope-verified: no `src/qrp/`,
no existing tests, no requirements.lock) merged to `discovery` as
387f94b (no-ff, provenance preserved). Independently re-verified:
`tests/properties --hypothesis-profile=quick` → 17 passed. Campaign
venv re-synced; hypothesis 6.170.14 now arrives via the dev extra.

**2026-11-02 — wave 1 dispatched** (branch from 387f94b):
A `inv/0f462p/a-input-contracts`, B `inv/0f462p/b-enrollment-stockpiling`,
C `inv/0f462p/c-dose-inclusion-followup`. D/E/F queued behind the
three-concurrent-DuckDB-worker limit.

**2026-11-02 — AC.8/AC.9 campaign-validation machinery (branch
`inv/0f462p/campaign-infra`).** Manifest completeness validation and
mutation replay are now machine-checkable. New artifacts:
`investigation/calibrations/` (normalized seeded-defect patches),
`investigation/replay_core.py` + `investigation/replay_mutation.py`
(replay machinery), `tests/properties/test_campaign_manifest_complete.py`
(runs in the normal quick suite; cheap JSON/filesystem/git checks),
`tests/properties/test_campaign_mutation_replay.py` (opt-in, skipped
unless selected), and a `mutation_replay` pytest marker (additive
registration in pyproject.toml).

### Calibration-record schema (`manifest.json calibrations[]`)

One object per seeded-defect calibration, exactly these fields:

| field | meaning |
|---|---|
| `id` | stable record id (`<pkg>-<n>`); names the disposable worktree `replay-<id>`, so `[A-Za-z0-9][A-Za-z0-9._-]*` |
| `package` | package id from `packages[]` |
| `base_revision` | commit the replay worktree is created at: the package's pass-1 HEAD (branch point a0d0b93; `src/` and `tools/` are byte-identical from a0d0b93 through every pass-1 HEAD, verified via `git diff a0d0b93 3085eb4 -- src tools` empty). The base is NOT a0d0b93 itself because the package test files the failing command needs do not exist there |
| `base_note` | optional provenance string for the base revision |
| `diff_path` | repo-relative path of the seeded-defect patch (under `investigation/calibrations/`) |
| `failing_command` | the recorded red command, leading with a python interpreter token (`python -m pytest ...`); the replay substitutes the replay venv's interpreter |
| `expected_failure_pattern` | regex the RED output must match — the recorded reason, not just any failure |
| `red_evidence` | verbatim red record from the package notes |
| `green_evidence` | verbatim green record from the package notes |
| `reverted` | always `true`; every calibration was reverted before commit |
| `provenance` | `recorded` (diff saved by the worker at calibration time) or `reconstructed` (diff re-derived from the notes by the campaign-infra worker) |

Current records: `a-1` (recorded, copy of
`investigation/packages/a-calibration-seeded-defect.diff`), `b-1`,
`c-1`, `e-1`, `f-1` (all reconstructed from the package notes; each
verified to apply at a0d0b93 via `git apply --check` before replay).

### Mutation replay (AC.8 red/green re-verification)

Per record, fully disposable and always cleaned up (success or failure):

1. `git worktree add --detach …/wt/replay-<id> <base_revision>` —
   never touches any preexisting worktree; refuses to run at all from
   a non-`inv/0f462p/*` branch;
2. `uv venv .venv --python 3.13 && uv pip install --python
   .venv/bin/python -e '.[dev]'` in that worktree (warm uv cache);
3. `git apply` the record's diff;
4. run `failing_command` there — REQUIRED to fail AND to match
   `expected_failure_pattern`;
5. `git checkout -- .` (mutation reverted) — REQUIRED: same command
   passes;
6. `git worktree remove --force` in a finally block.

Run explicitly (never part of normal runs):

```bash
python -m pytest tests/properties/test_campaign_mutation_replay.py -m mutation_replay
# or directly:
python investigation/replay_mutation.py            # all records
python investigation/replay_mutation.py --id b-1 --json
```

Executed evidence (campaign venv, 2026-11-02): all five records proved
red->green with the worktree removed — a-1 RED "11 failed, 7 passed" /
GREEN 18 passed; b-1 RED `test_enrollment_matches_day_model` at
`oracles/b_pkg_fixtures.py:213` (row-count divergence on the
gap-threshold @example) / GREEN 1 passed; c-1 RED
`test_followup_washout_boundaries` "cohort_final membership differs
from the model", generated property still passed (1 failed, 1 passed —
the recorded red shape exactly) / GREEN 2 passed; e-1 RED both fault
tests "Failed: DID NOT RAISE RuntimeError" / GREEN 2 passed; f-1 RED
`test_comparators_detect_targeted_corruption` (`assert code == 1` —
duplicate_row accepted, exit 0) / GREEN 1 passed.

### Orchestrator action needed (record staleness, not a code issue)

`packages[F].status` is still `"pending"` in this manifest although F
pass-1 merged at 3085eb4 (F.md, two witnesses, 58-passed quick run on
the F branch). The campaign-infra worker may only append to
`calibrations[]`, so the validator tolerates the pending status of
pass-less packages (D in flight; F stale) with an explicit skip reason
and FAILS instead if a pending package ever records passes. Once
packages[F] carries its real status (and F pass rows), the validator
asserts F strictly and the skip disappears.

*(Orchestrator note, later on 2026-11-02: resolved — all six packages
now carry settled pass2-complete statuses and pass rows; the strict
completion tier is green.)*

**2026-11-02 — B/D pass-2 final batteries recorded (closing records,
`inv/0f462p/closing-records` ea8936d).** Manifest gained pkg-b-pass2-*
(four records from B.md pass records 1-6 at b-final 4891dfa) and
pkg-d-pass2-* (three records from D.md's final battery at cc4945d,
recorded at e5d4dd4 — at that time not yet merged, and the records said
so), stale strict-xfail B2 witness references repaired to the current
positive parity pins, and the D DOUBLE-mean / RX DISTINCT / B legacy
overlap-caveat leads recorded. Narrative: packages/CLOSING-RECORDS.md.

**2026-11-02 — final integration into `discovery` (31846dc).**
- `5d0ac0a` — D final e5d4dd4 merged (D.md + reconciled optional-stage
  oracles + anti-vacuity fixtures) after the parent's independent
  review returned **APPROVE**; approval limits recorded in the
  manifest's D package entry (documented-input pinning scope, RX
  DISTINCT SAS parity asserted neither way, production DOUBLE mean
  unresolved, contiguous open-top year-band claims only). The
  previously executable-red
  `test_optional_stage_matches_reference[attrition_person_time]`
  divergence is resolved by this merge.
- `4149258` — the closing-records manifest/ARBITRATION/CLOSING-RECORDS
  update integrated.
- `14a54f1` + `31846dc` — the last three campaign mypy errors fixed in
  property-test files, no production changes
  (investigation/packages/TYPES-FINAL.md).

**2026-11-02 — final validation: properties COMPLETE, original suites
PENDING (not complete overall).** The parent ran the final full
properties quick suite at `31846dc`:
`python -m pytest tests/properties --hypothesis-profile=quick -q` →
exit 0, **424 passed, 13 skipped, 1 xfailed, 13 warnings, 299.29s**
(log: session `final-properties.log`). The 13 skips are the opt-in
replay wrappers — the six mutation-replay and seven finding-replay
replays execute separately. The original non-properties suites
(`python -m pytest --ignore=tests/properties -q`) were still running at
this update; their result is NOT recorded here and final-suite
completion is NOT claimed. The standing final-validation report —
AC.1–AC.9 mapping, known baseline residue (2 pre-existing
spill-attribution OOM failures, 3 environmental skips), eight fixed
findings across seven fix branches with seven replay groups, six
mutation-replay calibration groups, and the unresolved decision briefs
DB-2..DB-5 plus the D rounding/DISTINCT and B overlap limitations —
lives in investigation/packages/FINAL-VALIDATION.md and will be updated
to "complete" only after the original suites actually land.
