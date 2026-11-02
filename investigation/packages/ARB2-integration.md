# Arb2 integration

Private local integration on `inv/0f462p/integrate-arb2`, starting at
`96204bd3cdb4ef41a96b7f2b75105275cafb8042`, merging `a2c19cf`.
No push, publication, SAS execution, history rewrite, or discovery-checkout edit.

## Resolution and preservation

Four conflicts: `investigation/manifest.json`,
`tests/properties/oracles/a_optional_columns.py`,
`tests/properties/oracles/a_scdm_layouts.py`, and
`tests/properties/test_pkg_a_optional_columns.py`.

- Kept current package statuses, all 29 passes, and open decisions exactly.
  Every incoming pass ID is retained. Combined distinct findings from both sides.
- Selected the incoming success-required optional-column suite and populated lab
  fixture, including nonempty baseline lab output assertions. Kept current layout
  helper behavior (`all8` and table-qualified omission).
- Kept wave-3 normalization and denominator header corrections, E/C changes,
  B1 removal, B2 ruling, E4/D1 open decisions, and F pass-2 files.
  No old D oracle or B package files were edited.
- F1/F2 totals pins now pass rather than xfail. Table-qualified optional-column
  placeholders merged into the harness with its existing fixes intact.
- No architectural change required new AGENTS.md files.

### Calibration recovery

Both merge parents had an empty `calibrations` array. This was a pre-existing
record-loss error, not evidence of complete calibration coverage. Restored the
five A/B/C/E/F records from `c5a8117:investigation/manifest.json`; appended D from
`22fad196:investigation/packages/d-calibration.json` and copied that record file
and `investigation/calibrations/d-1.patch`. Python equality checks confirmed all
six records match the source data exactly and the D patch is byte-identical.
The five original patch files remained present. Historical calibration evidence
was preserved, not rerun in this integration.

## Commands and actual results

Environment: `uv venv .venv && uv pip install --python .venv/bin/python -e '.[dev]'`
returned **0**. Local CPython 3.13.13, DuckDB 2.0.0, Hypothesis 6.170.14,
pytest 9.2.0. Operator environment unchanged. Tests ran serially, no full baseline.

1. `.venv/bin/python -m pytest -q tests/test_regression_f1.py tests/test_regression_f2.py tests/test_regression_a4.py tests/test_optional_claim_columns.py tests/properties/test_pkg_f_totals.py tests/properties/test_pkg_a_optional_columns.py tests/properties/test_pkg_d_denominators.py tests/properties/test_campaign_manifest_complete.py --hypothesis-profile=quick`
   - **exit 1**, 85 passed, 1 failed, 100.36s.
   - Only failure: D `test_denominator_matches_day_enumeration`.
   - Initial standalone regression tests used the engine default thread count;
     properties explicitly request one thread. This run is not claimed to be
     wholly single-threaded.
2. `taskset -c "$(.venv/bin/python -c 'import os; print(min(os.sched_getaffinity(0)))')" .venv/bin/python -m pytest -q tests/test_regression_f1.py tests/test_regression_f2.py tests/test_regression_a4.py tests/test_optional_claim_columns.py tests/properties/test_pkg_f_totals.py tests/properties/test_pkg_a_optional_columns.py --hypothesis-profile=discovery`
   - **exit 0**, 69 passed, 168.53s.
   - Affinity did NOT change DuckDB default threads: a subsequent
     `current_setting('threads')` probe returned 32. Thus affinity alone is not
     evidence of a one-thread DuckDB run. Property harness uses `threads=1`.
3. Enforced single-thread rerun, without repository code changes:

```sh
.venv/bin/python -c 'import duckdb,pytest; original=duckdb.connect; exec("def connect(*args, **kwargs):\n    kwargs[\"config\"] = {**kwargs.get(\"config\", {}), \"threads\": 1}\n    return original(*args, **kwargs)\n"); duckdb.connect=connect; assert duckdb.connect().execute("select current_setting(\x27threads\x27)").fetchone()==(1,); raise SystemExit(pytest.main(["-q","tests/test_regression_f1.py","tests/test_regression_f2.py","tests/test_regression_a4.py","tests/test_optional_claim_columns.py","tests/properties/test_pkg_f_totals.py","tests/properties/test_pkg_a_optional_columns.py","--hypothesis-profile=quick"]))'
```

   - **exit 0**, 69 passed, 65.06s; one pytest process, connections configured
     with `threads=1`. Includes 24 standalone optional-column cases and 34
     optional-column property cases.
4. After calibration recovery:
   `.venv/bin/python -m pytest -q tests/properties/test_campaign_manifest_complete.py`
   - **exit 0**, 10 passed, 0.03s. Validator was not weakened.
5. Semantic preservation assertions: **exit 0**. Six calibrations exactly equal
   recovered records plus D; current packages/passes/open_decisions unchanged;
   incoming pass IDs a subset of retained IDs; D patch byte-identical.
   `git diff --check` also returned **0**.

## Remaining denominator-oracle difference

The old D oracle assigns age at segment start, whereas F2 splits eligible time
at age-band thresholds. Its generated failing case includes patients=2,
seed=63260, deaths and encounters enabled, wash=(0,0), outden=('Y','Y').
This failure is retained, not adjusted to production SQL and not interpreted
as proof that F2 is wrong. Parent independently commissioned attained-age-per-day
validation. Commits `fe9b5d17bd104a2d9ace755f1bf89f7a539323f5` and
`9072fb6e7f2090c22218c9811a8c4bab1c201be3` on `inv/0f462p/d-f2-days` are
reserved for separate integration after this merge; their baseline merge is not
included here. Contiguous open-top year bands are their stated validation scope;
finite/gapped bands remain ambiguous. This merge is not a claim of a green full
suite or completed D oracle integration.
