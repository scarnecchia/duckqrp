# E-2: the CLI failure path never closes its engine

- **id:** E-2
- **proposed issue title:** cli.main closes the engine only on the
  success path — failures exit 1 with the DuckDB connection open
- **contract:** same resource-release convention as E-1 (`Engine` is a
  context manager, engine.py:564-568; `eng.close()` already sat in
  `main()`'s success path). Process exit bounds the leak for a
  subprocess run — which is why this is low severity — but `main()` is
  a library-callable function returning int (the test suite calls it
  in-process), and any long-lived in-process caller of `main()`
  accumulates engines the same way the TUI does.
- **trigger:** Any CLI run that fails after `Engine(...)` construction:
  a failed stage, a missing SCDM table, a bad table map — anything that
  raises between `eng = Engine(...)` (cli.py:258) and the `eng.close()`
  at cli.py:293.
- **witness/repro command:**
  `investigation/witnesses/e1-e2-frontend-engine-leak-evidence.txt`;
  `tests/test_regression_e1.py::test_cli_failure_closes_the_engine`
  (runs the same `main()` a subprocess would run, in-process; the
  engine release is observed through a CloseProbeEngine subclass — an
  exit-path hook, since a subprocess cannot report engine state).
- **expected vs actual:** Expected: the failure path releases the
  engine before returning 1. Actual: `closed == []` — zero close calls;
  the exception jumps from inside the `try` straight to `except`,
  skipping `eng.close()`.
- **first failing revision:** since c3e60db5d08343b1cebbb90d234602f09cb
  0d70d (campaign base).
- **mechanism:** `eng.close()` sits inside the `try` AFTER `run()` and
  the parity-dump/stage-summary block; any exception in that block (or
  in `run()` itself, the common case) bypasses it. Severity: low (the
  assignment's classification is confirmed — process exit releases
  everything in the subprocess case; the same-shape in-process caller
  is where it matters).
- **severity:** resource-release defect, low (process-bounded for the
  subprocess case; unbounded for in-process `main()` callers).
- **affected paths:** src/qrp/cli.py (`main()` run block).
- **minimal fix/commit:** inv/0f462p/fix3-e1 (SAME branch as E-1 — the
  assignment allowed this only if the fix shape matched, and it does:
  both are guaranteed-release try/finally), red 323b231 → green
  0a26573. `eng` is bound to None before the `try` (a bad study can
  raise before the Engine exists) and `if eng is not None: eng.close()`
  moved into the existing `finally`, ahead of the runlog close. The
  success-path order is unchanged: the parity dump and the stage
  summary still run on an open engine.
- **red/green commands+results:**
  - red: `.venv/bin/python -m pytest tests/test_regression_e1.py -v`
    → 2 failed (this test: `assert closed == [True]` got `[]`)
  - green: same command → 2 passed in 2.59s
- **broader validation:** same as E-1 (one branch, one validation pass:
  test_ui.py + determinism + rewrites 50 passed/2 skipped, mypy and
  campaign ruff clean, package E quick suite green on the merged tree).
- **related findings:** E-1 (same class, same branch), E-3 (the SIGINT
  path also bypasses this finally today — a KeyboardInterrupt is not an
  `Exception`, so the DB-3 contract choice interacts with this fix:
  after the fix the finally DOES release the engine even on SIGINT,
  because finally runs on unwinding for BaseException too).
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
