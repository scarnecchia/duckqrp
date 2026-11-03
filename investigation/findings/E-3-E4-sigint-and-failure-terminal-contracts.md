# E-3 / E-4: characterization records — SIGINT mid-run and the missing
# failure-path RunFinished (contract choices, NOT fixed; decision
# briefs DB-3 / DB-4)

- **id:** E-3 (DB-3), E-4 (DB-4)
- **proposed issue title:** Decide the CLI SIGINT contract and the failure-path RunFinished ownership (dual record: E-3 SIGINT, E-4 failure terminal event)
- **proposed issue titles:**
  - E-3: Decide the CLI SIGINT contract: exit code, diagnostics, and
    the terminal log record for a killed run
  - E-4: Decide who owns the failure-path terminal event: pipeline.run
    emits none; only the RunHandle driver adds RunFinished(ok=False)
- **contract:** Both are UNSPECIFIED contract choices, deliberately not
  invented by the finder (package E, findings 3-4 and OQ-1/OQ-3) nor by
  this arbiter. Evidence base:
  - errors.py module docstring: "The runbook promises actionable
    errors; that promise has to hold wherever the failure happens."
  - ARCHITECTURE.md "Cancellation, and a bug this review found": the
    IN-APP cancellation contract is fully specified (flag + boundary ->
    CancelledRun -> RunFinished(cancelled=True), 0 stages after an
    early cancel) — but it governs the cancel button, not process
    signals.
  - LOGGING.md:81-91: RunStarted/RunFinished both "come from
    pipeline.run, so the CLI and the UI observe an identical stream",
    pinned by test_cli_and_ui_emit_the_same_events — a parity promise
    that today holds only for successes (E-4).
  - LOGGING.md:18-20: the .log "is what you attach to a parity report
    or a bug"; a SIGINT-killed run ends mid-stream with no terminal
    record and no FAILED line (E-3) — it reads as a truncated log.
  - OUTPUTS.md (M10): manifest written last; its absence on a killed
    run is the one part of today's behavior that is already correct and
    must be preserved by any contract choice.
- **trigger:**
  - E-3: SIGINT (ctrl+c) during `qrp run`; KeyboardInterrupt is a
    BaseException, so `except Exception` in cli.main cannot catch it:
    raw traceback, exit −2/SIGINT, JSONL stream ends without a terminal
    record (last observed line StageFinished(normalize)), no FAILED
    line, no manifest.
  - E-4: any direct `pipeline.run()` failure (the CLI path): the
    exception propagates; no RunFinished reaches the JSONL stream,
    while the UI path (RunHandle._work) synthesizes
    RunFinished(ok=False, error=explain(exc)).
- **witness/repro command:** package E characterization tests on the
  discovery branch: tests/properties/test_pkg_e_cli_tui.py
  ::test_cli_sigint_mid_run_is_characterized (subprocess SIGINT after
  the first unbuffered stage line) and
  tests/properties/test_pkg_e_faults.py
  ::test_direct_run_sql_fault_propagates_without_ok (the safe direction
  is asserted: nothing claims ok).
- **expected vs actual:** Expected: whatever the operator rules — see
  DB-3 and DB-4. Actual: E-3 as above; E-4 a CLI/JSONL failure stream
  with no terminal event, vs the UI's RunFinished(ok=False).
- **first failing revision:** since c3e60db5d08343b1cebbb90d234602f09cb
  0d70d (campaign base) — in the "not yet specified" sense, not a
  regression.
- **mechanism:** E-3: `except Exception` cannot catch
  KeyboardInterrupt; the finally that exists closes only the runlog.
  E-4: `pipeline.run` emits RunStarted/RunFinished around the happy
  path only; the failure synthesis lives in `RunHandle._work`, so the
  two drivers diverge exactly where a terminal event matters most.
- **severity:** E-3: low-medium (log-completeness gap: a killed run is
  indistinguishable from a truncated log, no machine-readable
  terminal). E-4: contract ambiguity only — the asserted direction is
  safe (no false success).
- **affected paths:** src/qrp/cli.py (both), src/qrp/pipeline.py
  (E-4's emission point), src/qrp/runner.py (the UI-side synthesis).
- **minimal fix/commit:** none by charter. Note one interaction with
  the SHIPPED fix E-2: after fix3-e1, the CLI's `finally` DOES release
  the engine even on SIGINT (finally unwinds for BaseException too) —
  the resource half of the SIGINT path is now clean; what remains open
  is purely the contract (exit code, diagnostics, terminal record).
- **red/green commands+results:** N/A (characterization; no behavior
  change proposed).
- **broader validation:** n/a.
- **related findings:** DB-3 (E-3's brief), DB-4 (E-4's brief), E-2
  (shared failure path), package E findings 3-4 / OQ-1 / OQ-3.
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
- **SCDM 8.2.2 alignment:** reviewed 2026-11-03 — signal/failure
  terminal contracts; no SCDM data-model-bearing assumption.
