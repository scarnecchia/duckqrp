# A4 complete-family validation (private, 2026-11-02)

Validated on arb2 after db2571b. No publication or parent merge was performed.
The minimal delivery branch is `inv/0f462p/fix3-a4-complete` at `f1dca19`:
base `c3e60db`, red `8bc521e` (21 failed, 3 passed, exit 1), green
`a6a74e4`, final typing correction `f1dca19`. The original `fix2-a4` is
superseded and preserved. Only pipeline.py, 10_normalize.sql, and
 tests/test_optional_claim_columns.py differ from base on the clean branch.

## Property coverage

The pass-2 files from f5a2a85 were integrated selectively, not by merging
discovery. All 31 declared optional columns require successful runs; no
CRASHERS map, broad exception acceptance, or xfail remains. Generated
omission sets range from one column to all optionals in a selected table.
The 18 fixed columns retain absent-versus-explicit-NULL comparisons.
The existing rxamt contract is intentionally different: absence disables
the amount rule, while explicit NULL does not.

The original probe had no eligible final cohort and used a nonmatching
LOINC code. The revised probe uses the existing deterministic hand fixture,
a matching 2160-0 lab, and a date in the eligible patient's lookback.
Baseline lab_results and lab_summary must be nonempty. Tests verify full
rows, summary counts and values, meaningful empty results when a matching
field is absent, and NULL numeric results when the type/value pair is absent.
The standalone 24 tests retain present-value controls for all three tables.

## Commands and captured outputs

Output files below are under
`/home/scarndp/.local/share/polytoken/sessions-v1/0f462p-motto/shell/`.
Each command was run serially. New property runs and standalone runs use
threads=1. Existing regression/determinism tests retain their original
thread settings; they were not rewritten for this validation.

- `HYPOTHESIS_PROFILE=quick .venv/bin/python -m pytest tests/properties/test_pkg_a_optional_columns.py -q --tb=short`
  — 34 passed, 33.10s, exit 0.
- Same command with `HYPOTHESIS_PROFILE=discovery`
  — 34 passed, 69.29s, exit 0.
  Both: `call_c50ef4be3cda49a6af2adcb5404990e2.stdout`.
- From `../fix3-a4-complete`: `PYTHONPATH=src ../arb2/.venv/bin/python -m pytest tests/test_optional_claim_columns.py -q --tb=short`
  — 24 passed, 24 missing-encounter warnings, 16.60s, exit 0, at f1dca19.
  Output: `call_1587367fbcd140bb9746e2066c888ff9.stdout`.
- `.venv/bin/python -m pytest tests/test_optional_claim_columns.py tests/test_pipeline.py::test_claim_views_envelope_inpatient_stays tests/test_regression_a4.py tests/test_regression_f1.py tests/test_regression_f2.py tests/test_rewrites.py tests/test_determinism.py -q --tb=short`
  — 45 passed, 29 missing-encounter warnings, 21.21s, exit 0.
  Output: `call_3eb658df5baf4f62b7b2a42fbeda415d.stdout`.
- `HYPOTHESIS_PROFILE=quick .venv/bin/python -m pytest tests/properties/test_pkg_a_scdm.py tests/properties/test_pkg_a_tiebreak.py -q --tb=short`
  — 36 passed, 2 warnings, 26.67s, exit 0.
  Output: `call_eab6a5c3015c4034b115212d7e3a23d7.stdout`.
- `.venv/bin/mypy src/qrp/pipeline.py tests/test_optional_claim_columns.py tests/properties/test_pkg_a_optional_columns.py tests/properties/oracles/a_optional_columns.py tests/properties/oracles/a_scdm_layouts.py`
  — success, five files, exit 0.
  Output: `call_bb4a3a1c4de7445b80934115967e9daf.stdout`.
- `.venv/bin/ruff check tests/properties/test_pkg_a_optional_columns.py tests/properties/oracles/a_optional_columns.py tests/properties/oracles/a_scdm_layouts.py tests/test_optional_claim_columns.py`
  — all checks passed, exit 0. The helper's inherited import-order error was corrected.
- `.venv/bin/ruff check src/qrp/pipeline.py`
  — exit 1, six preexisting import/broad-exception findings; no production
  file changed during this follow-through.
  Ruff output: `call_8a618ffbbf184a4ca38ead3a28d9be9a.stdout`.

Initial quick attempts exited 1 due to the vacuous probe, then incorrect
new assertions about rxamt and result_type. These were test-fixture and
expectation errors, not newly discovered production defects. Outputs:
`call_6f366edaea7d4933bb74d70dcd291270.stdout` and
`call_362790a4cc4a4d36a55dfedd3f7bf925.stdout`.

## AC9 collection evidence

`.venv/bin/python -m pytest tests/properties/test_pkg_a_optional_columns.py tests/test_optional_claim_columns.py --collect-only -q`
collected 58 tests, exit 0. Full node IDs are in
`call_8a618ffbbf184a4ca38ead3a28d9be9a.stdout`:

- TestOptionalColumnDeclaration::test_hand_list_matches_declaration
- TestOptionalColumnCharacterization::test_omitted_column (31 column IDs)
- TestOptionalColumnCharacterization::test_generated_omission_sets
- TestOptionalColumnCharacterization::test_present_lab_values
- test_each_optional_column_matches_null (18 column IDs)
- test_all_optional_columns_match_null (diagnosis, procedure, lab_result)
- test_present_columns_keep_values (diagnosis, procedure, lab_result)

This is bounded validation, not a full-suite or exhaustive combination claim.
No SAS or external datasets were used. No F source or tests were modified.
