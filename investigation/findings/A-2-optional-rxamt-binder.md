# A-2: documented-optional dispensing.rxamt crashes normalize with a
# BinderException — FIXED via the existing optional-column mechanism

- **id:** A-2
- **proposed issue title:** An extract without the documented-optional
  dispensing.rxamt dies at the first stage with `Binder Error:
  Referenced column "rxamt" not found` while `qrp inspect` calls the
  schema compatible
- **contract:** INPUTS.md ("Columns actually read", line 179) declares
  `dispensing` required columns `patid, rxdate, rx, rxsup` and OPTIONAL
  `rxamt (dose only), rx_codetype`; scdm.py:86-87 lists rxamt in the
  dispensing spec's optional tuple ("rxamt only for dose"), and `qrp
  inspect` therefore reports an extract missing rxamt as `ok` /
  `RESULT: schema is compatible.` — the tool's published compatibility
  promise. The code's own stated invariant (pipeline.py:1153-1161,
  written for `chart`) says "Every column the SCDM declares OPTIONAL is
  resolved against the actual extract and falls back to NULL";
  10_normalize.sql violated it for rxamt by referencing the column
  twice unconditionally (projection line 83 and the SAS-rule filter
  line 94). SAS nuance, verified in the read-only reference tree: SAS
  itself REQUIRES the column on the Type 2 path — ms_shaveoutside with
  shaverx=Y references RxAmt on every drug claim (`if RxAmt > 0 then
  RxAmt = ...`), and ms_stockpiling's same-day collapse computes
  `&sameday_amt_oper.(&CLMAMT.)` with CLMAMT=RxAmt — an extract without
  the column cannot run in SAS at all. INPUTS.md is not stale,
  though: the optional-rxamt promise is live in three places
  (INPUTS.md, scdm.py, probe), i.e. it is THIS tool's deliberate
  published interface, in a doc that already documents deliberate
  divergences from SAS's accepted inputs (aliases, CSV, layouts).
  Classification: production defect against the published contract;
  fixed so the code honors it, keeping present-column semantics
  bit-for-bit.
- **trigger:** An SCDM extract whose dispensing table carries every
  required column but omits rxamt, run through the pipeline (probe
  says compatible first).
- **witness/repro command:** regression tests
  `tests/test_regression_a2.py::test_optional_rxamt_absent_runs` and
  `::test_rxamt_present_zero_amount_still_excluded` (standalone):
  `.venv/bin/python -m pytest tests/test_regression_a2.py -q`.
  Property witness: `tests/properties/test_pkg_a_scdm.py::
  TestDocumentedOptionalRxamt::test_optional_rxamt_runs` (was
  xfail(strict); marker removed on the arb branch now that it passes).
- **expected vs actual:** Expected (INPUTS.md): run without dose
  restrictions. Actual pre-fix: `duckdb.BinderException: Binder Error:
  Referenced column "rxamt" not found!` at the first stage.
- **first failing revision:** since c3e60db5d08343b1cebbb90d234602f09cb
  0d70d (campaign base).
- **mechanism:** 10_normalize.sql referenced rxamt in the
  cdm_dispensing view with no optional-column fallback. The fix mirrors
  the existing mechanism exactly: pipeline.py resolves rxamt against
  the actual extract columns (`rxamt_col = "rxamt" if "rxamt" in
  disp_cols else None`) and substitutes `{dispensing_rxamt}` (column or
  NULL) for the projection and `{dispensing_amt_rule}` for the filter —
  the full SAS rule `(rxamt) IS NOT NULL AND (rxamt) > 0` when the
  column exists, `TRUE` when it does not (there is no amount to test;
  substituting NULL into the rule would have dropped every dispensing —
  a crash swapped for silent emptiness). Extracts WITH the column keep
  the rule verbatim: a true zero or missing amount is still not a
  dispensing (ms_cidanum.sas:617 rule, comment preserved).
- **severity:** crash (probe says ok, run dies — the failure mode the
  package works hardest to avoid). **+ unproven impact:** post-fix
  residuals, documented not demonstrated: an extract without rxamt
  yields NULL amounts, so a study that configures stockpiling AMTRANGE
  ranges excludes every shaved row (NULL fails all range comparisons),
  and dose studies see NULL amounts rather than numbers — both
  configurations are outside the "dose only" contract's implied use,
  and an operator sees missing amounts in outputs rather than a crash.
- **affected paths:** src/qrp/sql/10_normalize.sql (cdm_dispensing),
  src/qrp/pipeline.py (rxamt resolution + fmt keys). Related lead found
  during arbitration, NOT fixed here: diagnosis optionals
  `dx_codetype`, `pdx`, `enctype` (declared optional in INPUTS.md:180)
  are also referenced unconditionally in 10_normalize.sql
  (`_cdm_diagnosis_raw`, lines 147-149) — the same defect class;
  encountered because the standalone regression extract needed those
  columns to get past normalize.
- **minimal fix/commit:** branch `inv/0f462p/fix-a2` (from c3e60db):
  red commit 1577f83 (standalone regression tests), green commit
  564eb7a (pipeline resolution + SQL placeholders).
- **red/green commands+results:**
  - red: `.venv/bin/python -m pytest tests/test_regression_a2.py -q` at
    c3e60db+1577f83 → `BinderException: Binder Error: Referenced
    column "rxamt" not found` in test_optional_rxamt_absent_runs.
  - green: same at 564eb7a → `2 passed` (both the absent-column run and
    the present-column zero-amount exclusion pin).
- **broader validation:** fix worktree: tests/test_determinism.py +
  tests/test_rewrites.py + tests/test_ui.py → `50 passed, 2 skipped`;
  tests/test_pipeline.py → `287 passed, 2 failed` (the two pre-existing
  exempt spill-attribution OOM tests), `1 skipped`, 6:55 — the 100k
  fixture carries rxamt, so the present-column path is exercised
  unchanged. Arb branch after merge: the property witness passes
  unmarked; package A quick set + determinism + rewrites + regression
  tests → `109 passed`; the campaign harness needed its fmt mirror
  extended with the two new keys (harness.py:243-245, 267-271) — the
  first arb run failed with `KeyError: 'dispensing_rxamt'`, fixed, then
  package C + harness stages → `38 passed`.
- **related findings:** A-1 (same package, different mechanism); the
  diagnosis-optional lead above (unfixed, same class).
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
- **SCDM 8.2.2 alignment:** reviewed against dispensing.md (RxAmt) on
  2026-11-03 — the fix implements exactly the column optionality
  INPUTS.md documents (absent column resolves to NULL, "no amount
  recorded", not zero); no further data-model assumption.
