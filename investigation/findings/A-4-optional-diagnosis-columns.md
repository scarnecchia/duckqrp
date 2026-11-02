# A-4: declared-optional diagnosis columns crash normalize with a
# BinderException — FIXED via the existing optional-column mechanism

- **id:** A-4
- **proposed issue title:** An extract whose diagnosis table carries only
  the required columns (`patid`, `adate`, `dx`) dies at the first stage
  with `Binder Error: Referenced column "dx_codetype" not found` while
  `qrp inspect` calls the schema compatible
- **contract:** INPUTS.md:180 ("Columns actually read") declares the
  diagnosis table's required columns `patid, adate, dx` and OPTIONAL
  `dx_codetype, pdx, enctype`; scdm.py:93-97 lists all three in the
  diagnosis spec's optional tuple, so `qrp inspect` reports such an
  extract as `ok` — the tool's published compatibility promise. The
  code's own stated invariant (pipeline.py:1162-1170, written for
  `chart` and covering `race`, `ddate`, `discharge_status`) says every
  declared-OPTIONAL column "is resolved against the actual extract and
  falls back to NULL"; 10_normalize.sql's `_cdm_diagnosis_raw`
  violated it by referencing all three unconditionally
  (`dx_codetype` in the projection, `pdx`/`enctype` inside the view's
  COALESCE wildcards). Same defect class as A-2 (rxamt) and the
  pre-campaign `chart` fix. SAS nuance, for the record: the SAS
  original reads these columns on claims that carry them
  (ms_caresettingprincipal.sas matches EncType/Pdx against
  `codes.EncType`/`codes.Pdx`), and this tool's care-setting parser
  already treats a missing care setting as match-any — the fix keeps
  that published convention.
- **trigger:** An SCDM extract whose diagnosis table omits
  `dx_codetype`/`pdx`/`enctype` but carries every required column,
  run through the pipeline after `qrp inspect` says compatible.
- **witness/repro command:** regression tests
  `tests/test_regression_a4.py::test_optional_diagnosis_columns_absent_runs`
  and `::test_diagnosis_columns_present_keep_semantics` (standalone):
  `.venv/bin/python -m pytest tests/test_regression_a4.py -q`.
  Independent probe:
  `investigation/probes/arb2_a4_probe.py` (builds the minimal extract,
  runs `qrp.run()`).
- **expected vs actual:** Expected (INPUTS.md:180): the run proceeds;
  the absence of the trio means "no code type / no principal flag / no
  care setting" on every diagnosis claim. Actual pre-fix:
  `duckdb.BinderException: Binder Error: Referenced column
  "dx_codetype" not found in FROM clause!` at the first stage.
- **first failing revision:** c3e60db5d08343b1cebbb90d234602f09cb0d70d
  (campaign base) — reproduced identically at the base and at the
  discovery HEAD 2a71a0c; fix-a2's rxamt resolution does not cover the
  diagnosis columns. (At the bare base an extract missing rxamt AND the
  diagnosis trio crashes on rxamt first — A-2's mechanism — so the
  probe carries rxamt to isolate A-4.)
- **mechanism:** `_cdm_diagnosis_raw` projected
  `CAST(dx_codetype AS VARCHAR)` and wrapped `pdx`/`enctype` in
  COALESCE wildcards without resolving the columns against the
  extract. The fix mirrors fix-a2 exactly: pipeline.py resolves the
  trio through the existing `_optional` loop (column, or `NULL`) and
  10_normalize.sql reads `{opt_dx_codetype}`, `{opt_pdx}`,
  `{opt_enctype}`. Absent `dx_codetype` → NULL, which the exposure
  join already treats as match-any ("A NULL SOURCE codetype cannot
  contradict the configured one", 30_exposure.sql:76-82); absent
  `pdx`/`enctype` fall to the view's own COALESCE wildcards
  ('X' any principal flag, 'XX' any care setting, consumed by the
  care-setting match at 30_exposure.sql:403-404). Extracts WITH the
  columns resolve to byte-identical SQL.
- **severity:** crash (probe-compatible extract dies at the first
  stage — the failure mode the campaign hunts). **+ related lead, NOT
  fixed here:** `_cdm_procedure_raw` references `px_codetype` and
  `enctype` the same way (10_normalize.sql, procedure view) — INPUTS.md
  declares them optional for the optional `procedure` table; same
  defect class, left as a follow-up lead.
- **affected paths:** src/qrp/sql/10_normalize.sql (_cdm_diagnosis_raw),
  src/qrp/pipeline.py (_optional loop), tests/test_pipeline.py
  (test_claim_views_envelope_inpatient_stays' placeholder-substitution
  list gains the three new keys — its fixture carries the columns, so
  its semantics are unchanged).
- **minimal fix/commit:** branch `inv/0f462p/fix2-a4` (from c3e60db):
  red commit 054faf1 (standalone regression tests), green commit
  7285bec (pipeline resolution + SQL placeholders + envelope-test
  substitution list).
- **red/green commands+results:**
  - red: `.venv/bin/python -m pytest tests/test_regression_a4.py -q`
    at c3e60db+054faf1 → 1 failed
    (test_optional_diagnosis_columns_absent_runs, BinderException);
    the present-columns pin passes.
  - green: same at 7285bec → `17 passed` for regression + determinism +
    rewrites; `tests/test_pipeline.py` → 287 passed with only the two
    pre-existing exempt spill-attribution OOM failures, 1 skipped
    (7:30).
- **broader validation:** fix worktree: regression + determinism +
  rewrites + ui suites; full tests/test_pipeline.py (above). Probe
  reproduction at base c3e60db AND discovery HEAD 2a71a0c. When merged
  into the arb2 branch: campaign harness's fmt mirror extended with
  the diagnosis keys (the same mirror fix-a2 needed), package A quick
  set + package F totals suites re-run — see
  investigation/packages/ARBITRATION.md wave-2.
- **related findings:** A-2 (same mechanism, dispensing.rxamt); the
  procedure/lab family below (same class, unfixed).

## Complete-family follow-through

The clean standalone branch `inv/0f462p/fix3-a4-complete` supersedes
`inv/0f462p/fix2-a4` for delivery. The old branch is preserved. Its
`tests/test_pipeline.py` compatibility changes remain integration-only.

- Base: `c3e60db5d08343b1cebbb90d234602f09cb0d70d`.
- Red: `8bc521e`; standalone `tests/test_optional_claim_columns.py`.
- Green: `a6a74e4f1116e096572170551aa44085f84d995c`.
- Typing correction: `f1dca19` (separate commit, no history rewrite).
- All 18 individual omissions and all three per-table omission sets
  now match explicit-NULL controls; three present-value controls pass.
- Keys are table-qualified, preventing diagnosis/procedure `enctype`
  availability from leaking between tables. Existing casts and defaults
  are unchanged.
- Red command: `PYTHONPATH=src ../arb2/.venv/bin/python -m pytest
  tests/test_optional_claim_columns.py -q --tb=no` from the clean
  worktree: 21 failed, 3 passed, 11.49s, captured pytest exit 1.
- Green same suite: 24 passed, 16.83s, exit 0.
- Arb2 integration: standalone suite plus
  `tests/test_pipeline.py::test_claim_views_envelope_inpatient_stays`:
  25 passed, 17.43s, exit 0.
- Existing A4/F1/F2 regressions plus determinism and rewrites:
  20 passed, 4.49s, exit 0. Those older tests use their existing thread
  settings; the new standalone suite explicitly uses DuckDB threads=1.
- `mypy src/qrp/pipeline.py`: success after the tuple-loop variable
  correction. `ruff check tests/test_optional_claim_columns.py`: pass.
  Pipeline Ruff reports six existing import/broad-exception findings.

Remaining campaign work: transfer package A's optional-column property
and its `a_scdm_layouts` dependency from `f5a2a85`, remove obsolete
expected-crash handling, and run broader integration validation. The
standalone suite queries `cdm_lab` directly; it does not enable the labs
stage. No claim of that additional stage validation is made here.

## Expanded family reported after the diagnosis fix (historical)

Package A's pass-2 sweep (commit `f5a2a85`, reported by the campaign
orchestrator) extends this same published-contract violation to procedure
`px_codetype`/`enctype` and all 13 declared-optional `lab_result` columns.
Its `tests/properties/test_pkg_a_optional_columns.py` pins all 18 columns.
The diagnosis fix above covers only three of those 18. Procedure views
are bound even in RX-only studies; labs expose the failure when their
stage runs. These additional reproductions were reported by package A,
not independently rerun in this arbitration worktree.

**Status: partially fixed.** The remaining 15 columns need the same
optional-column resolution review and standalone red/green coverage;
no claim is made that distinct mechanics justify leaving them out.
The requested complete-family follow-through is outstanding. Keep them
under A-4, not a new A-5. Do not treat the diagnosis branch as a complete
fix for the expanded finding.

Recommendation (not implemented): enhance `probe()` so `qrp inspect`
distinguishes missing optional columns that SQL actually resolves from
those still referenced unconditionally. A compatibility report should
not promise that an extract can run when such bindings will fail.
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
