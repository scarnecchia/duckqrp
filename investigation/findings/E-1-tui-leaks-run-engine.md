# E-1: the TUI never releases the run's engine — one leaked DuckDB
# connection per completed run in a session

- **id:** E-1
- **proposed issue title:** TUI event pump closes only the RunLog; the
  run's engine stays open after every completed run
- **contract:** `RunHandle.close()` exists precisely for the caller
  that constructed the handle (runner.py:226-228, "close is
  caller-owned" harness convention [M11 in packages/E.md]) — and the
  TUI is that caller: it constructs the RunHandle in `action_run`
  (tui.py:404-415). The pump owns terminal-state teardown by the
  code's own convention: it already closes the RunLog there
  (tui.py `_pump`). No doc promises explicit release — ARCHITECTURE.md
  and UI.md are silent on engine lifetime — but the designed TUI usage
  is a long-lived interactive session with repeated runs, so
  process-lifetime release does not apply (contrast E-2/CLI).
- **trigger:** Run any study to completion in a `QRPApp` session
  (success, failure, or cancellation — the leak is identical); run
  again in the same session.
- **witness/repro command:**
  `investigation/witnesses/e1-e2-frontend-engine-leak-evidence.txt`;
  `tests/test_regression_e1.py::test_tui_run_closes_the_run_engine`
  (Textual Pilot drives the real app, the tests/test_ui.py pattern).
- **expected vs actual:** Expected: a terminal state releases the
  run's engine (bounded resources per session). Actual: after
  `app.running` goes False the engine's connection still executes SQL —
  `_pump` closed only `self._runlog` and never called
  `self.handle.close()`. One live DuckDB connection per completed run,
  each holding that run's in-memory database (pipeline intermediates
  included), referenced by `app.handle`; repeated runs accumulate
  them until the session ends.
- **first failing revision:** since c3e60db5d08343b1cebbb90d234602f09cb
  0d70d (campaign base).
- **mechanism:** `_pump` iterates `self.handle.events()`; the queue
  always ends with the None sentinel (`RunHandle._work` puts it in
  `finally`), so the loop ends on every terminal state — but the only
  teardown after the loop is the RunLog close. `handle.close()` is
  never called anywhere in tui.py. Severity was elevated above
  package E's "medium-low" estimate: every leaked engine pins its
  in-memory database with the run's full intermediates — on a
  2m-row study that is GBs per run, and the TUI exposes a Memory
  setting precisely because DuckDB memory matters on these hosts.
- **severity:** resource-release defect (no wrong answers; bounded by
  session length but multiplied by study size per run).
- **affected paths:** src/qrp/tui.py (`_pump` only).
- **minimal fix/commit:** inv/0f462p/fix3-e1 red 323b231 → green
  0a26573, merged into inv/0f462p/arb3-findings. The event loop runs in
  try/finally; the finally flips `running` False FIRST (so a Cancel
  landing right at completion reaches an open, idle engine — the
  documented silent no-op — instead of a closed one, whose
  `con.interrupt()` raises) and then calls `self.handle.close()`,
  which guards `engine is None` (runner.py:226-228). Double-close
  remains out-of-contract (OQ-2) but is not introduced: the pump calls
  close exactly once.
- **red/green commands+results:**
  - red: `.venv/bin/python -m pytest tests/test_regression_e1.py -v`
    → 2 failed (this test: `engine_closed(app)` never True within the
    bounded budget; the CLI test in the same file fails for E-2)
  - green: same command → 2 passed in 2.59s
- **broader validation:** tests/test_ui.py + tests/test_determinism.py
  + tests/test_rewrites.py → 50 passed, 2 skipped (pre-existing
  /tmp/qrp_data/2m skips); mypy on both changed files clean; ruff
  --select E4,E7,E9,F clean; package E quick suite green on the merged
  arb3 tree.
- **related findings:** E-2 (same defect class in the other shipped
  frontend; fixed on the same branch because the fix shape is the same
  try/finally), OQ-2 (close idempotency observed-safe-but-not-promised).
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
- **SCDM 8.2.2 alignment:** reviewed 2026-11-03 — UI engine-lifecycle
  behavior; no SCDM data-model-bearing assumption.
