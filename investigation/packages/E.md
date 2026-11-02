# Package E — Runtime state, cancellation, event delivery, and filesystem results

## Pass2 — frontend release fixes and fault variants

Tested production revision: `6bc41715defcf553ec06e35af887fb4643f45e54`
(owned branch after `git merge --no-edit discovery`). Test change state: that
revision plus new `tests/properties/test_pkg_e_pass2.py`; this report is the
only other changed file. No production/shared-infrastructure edits, network,
publication, pushes, or SAS execution in pass2.

New checks:
- Fresh hand-fixture parquet roots for each TUI success/error/cancelled case.
  Actual Textual workers are awaited with a 30-second bound, without sleeps.
  Engine close count is exactly one; a real query independently proves the
  DuckDB connection is closed. Cancellation is installed before handle start,
  repeated twice, and produces no StageStarted/StageFinished events.
- Deterministic missing-input CLI failure followed by a NEW successful run,
  with distinct engines, exact close counts after each call, actual return
  codes 1 then 0, and closed-connection checks. This exercises the fixed
  finally paths, rather than assuming the doubles release resources.
- Cheap generated RunHandle histories: one legal start, bounded wait,
  events-or-drain consumption, then one close; 1–6 stage steps, success,
  failure, or boundary cancellation, repeated cancellation 1–3 times.
  Expected executed prefixes and terminal flags come from the input schedule,
  not the adapter's recorded state. Exactly one final terminal and one release
  are required. No post-cancel stage is allowed. Deep exhausts 648 input tuples
  (some map to the same boundary through modulo); no invalid histories.
- Single and persistent kth-log-call failures over up to 24 event deliveries:
  production multi_sink must deliver every event, in order and by identity,
  to its healthy subscriber despite the failing subscriber.
- Four real tiny-pipeline partial-write variants: parquet/CSV, failure before
  or after the second write. Each reports failure through RunHandle, leaves no
  manifest, retains actual partial parquet output, and releases its connection.

All tests use threads=1 where constructing engines in the new tests. Existing
pass1 tests and existing regression suites retain their prior configuration.
Only one test process ran at a time. Commands used `.venv/bin/python` and
`timeout 300` (initial focused run used `timeout 180`); no piped pytest status.

Final-state verification (actual exit codes):

| command suffix after `timeout 300 .venv/bin/python -m pytest` | exit | duration | result |
|---|---:|---:|---|
| `tests/properties/test_pkg_e_*.py --hypothesis-profile=discovery --hypothesis-show-statistics -q` | 0 | 39s | 31 passed |
| `tests/properties/test_pkg_e_pass2.py -k 'bounded_handle or logging_fault' --hypothesis-profile=deep --hypothesis-show-statistics -q` | 0 | 2s | 2 passed, 8 deselected |
| `tests/properties/test_pkg_e_*.py --hypothesis-profile=quick --hypothesis-show-statistics -q` (first) | 0 | 17s | 31 passed |
| same quick command (second) | 0 | 17s | 31 passed |
| `tests/test_rewrites.py tests/test_determinism.py tests/test_regression_e1.py tests/test_ui.py -k 'not cancel_stops_mid_query and not spilling_is_detected_and_reported' -q` | 0 | 44s | 52 passed, 2 deselected |

`ruff check --select E4,E7,E9,F tests/properties`: exit 0.
`mypy src tests/properties`: exit 0, 75 files; informational unchecked-body
notes in package D only. Regression run preceded the final four partial-write
variants; those variants do not alter any production code.

Discovery statistics: existing lifecycle 500 passing total (4 reuse + 496
new), cancel timings never 57.26%, between statements 15.73%, between stages
15.32%, before start 11.69%; terminal failed 45.77%, cancelled 27.22%, ok
27.02%. New handle family 500 passing, no invalid cases, terminal distribution
33.4% cancel / 33.4% ok / 33.2% fault. Deep: handle family 648 passing,
0 invalid, exhausted; logging 2000 passing, 417 Hypothesis-invalid generation
attempts (no assume/filter or illegal lifecycle steps). Fault hits are explicit
statistics, and deterministic examples force an activated logging failure.

Initial test-authoring failure: recording only Engine.emit missed the runner's
failure terminals. Corrected to observe RunHandle.extra_sink; this was an
observer defect, not a product lead. Initial mypy inherited-field inference
error corrected with an explicit EventSink annotation. All final checks green.

E1/E2 release fixes verified on real connections. No new confirmed product
bug from these variants. DB3/DB4 (SIGINT semantics and ownership of direct-run
failure terminals) remain characterized, pending operator decisions; no new
contract or production fix imposed.

Remaining gaps: pass1 still contains timing-based query-progress/SIGINT and UI
polling machinery; its prior claim of universally race-free barriers was too
strong. Pass2 additions introduce no sleeps and use worker completion or
pre-start cancellation, but do not replace all legacy machinery. The old
History generator also conflates some fault/cancel precedence through terminal
rewrites; the new independently asserted handle family avoids that combination
rather than claiming to repair the old model. Post-stage cancellation inside a
real blocked SQL operator, logging file-open/close failures, and stale manifests
on writes into an already-populated output tree remain beyond the added cases.


Branch: `inv/0f462p/e-runtime-lifecycle` (base a0d0b93)
Scope: AC.6 — lifecycle/cancellation/fault-injection/output-honesty/CLI+TUI.
Production files read fully: `src/qrp/engine.py`, `runner.py`, `events.py`,
`runlog.py`, `pipeline.py`, `outputs.py`, `cli.py`, `tui.py`; also
`errors.py`, `scdm.py` (table-map check), `00_macros.sql`; docs:
`ARCHITECTURE.md`, `LOGGING.md`, `OUTPUTS.md`, `UI.md`; existing tests:
`tests/test_ui.py`, `tests/test_pipeline.py`, `tests/properties/*`.

## Files added (all new; nothing under src/ or shared infra touched)

| file | role |
|---|---|
| `tests/properties/doubles/e_faults.py` | Fault doubles (Script/Write/Construction/CloseProbe engines), EventLog sink, `cancel_on` sink, `broken_sink`, `cancel_mid_query` barrier, `wait_until` |
| `tests/properties/oracles/e_lifecycle_model.py` | The lifecycle model: pure oracles over recorded event streams, every assertion annotated with its contract source |
| `tests/properties/test_pkg_e_lifecycle.py` | `TestRunLifecycle` equivalent: op-AST history property + deterministic cases + RunHandle-tier real runs + adapter cross-check |
| `tests/properties/test_pkg_e_faults.py` | `test_fault_schedule_terminal_events` (construction/sql/write/logging kth-call faults), resource-release tests, direct-driver honesty |
| `tests/properties/test_pkg_e_outputs.py` | `test_output_failure_is_not_reported_complete`, filenames/manifest/preservation, rerun scoping, failure-preserves-previous-outputs |
| `tests/properties/test_pkg_e_cli_tui.py` | `test_cli_failure_exit_status` (subprocess matrix + SIGINT characterization), `test_tui_terminal_outcomes` (+ failure/cancelled states) |

## Model choice (justification required by assignment)

**Operation-AST generator executed against the model**, not a
RuleBasedStateMachine. Reasons: (1) the lifecycle is one-shot —
`RunHandle.start()` raises on reuse and engines are disposable — so a
persistent machine would rebuild its SUT per rule anyway; (2) the
interesting dimension is a small independent choice set along a linear
pipeline (cancel timing × fault point × repeat × follow-on run), which
a generated `History` dataclass expresses constructively and shrinks to
a minimal schedule; (3) histories are legal by construction, so
shrinking never filters out interesting operations.

## The lifecycle model and where each guarantee came from

States: `IDLE → CONSTRUCTED → RUNNING(stage i) → {SUCCEEDED |
FAILED | CANCELLED}`. Sources: **[ARCH]** = docs/ARCHITECTURE.md
"Cancellation…" section; **[UI]** = docs/UI.md; **[LOG]** =
docs/LOGGING.md "A bug this found"; **[OUT]** = docs/OUTPUTS.md
"Rerun safety"/"What a failure does"; **[ENG]** = engine.py promises;
**[RUN]** = runner.py promises; **[PIPE]** = pipeline.run/_finish
structure; **[EVT]** = events.py docstrings; **[CLI]** = cli.py /
errors.py promises.

| # | Guarantee | Source |
|---|---|---|
| M1 | cancel is checked at every stage boundary AND between statements inside a stage, raising `CancelledRun`; a cancelled run completes **0 stages** in the early case and reports `RunFinished.cancelled=True, ok=False` | ARCH table; ENG (`raise_if_cancelled` call sites) |
| M2 | `interrupt()` stops the executing query (InterruptException in the blocked thread); it is a **silent no-op when idle** — hence the flag+boundary mechanism | ARCH; UI "verified"; empirically confirmed (idle interrupt → next query runs fine) |
| M3 | `RunHandle.cancel()` is thread-safe and idempotent; a cancel before `start()` is honoured ("a fast click must not be lost") | RUN |
| M4 | exactly one terminal `RunFinished` per run on the RunHandle path, on success AND failure; a cancelled run's `error` stays empty and emits no ERROR LogMessage | RUN `_work`; verified in oracle |
| M5 | a failed run: `ok=False, cancelled=False, error=explain(exc)`, ERROR LogMessage before the terminal event; the pipeline aborts immediately (no later stage starts) | RUN; PIPE (no try/except around stages) |
| M6 | success stream: `RunStarted` first, stage start/finish pairs in plan order, `RunFinished(ok=True, tables=…)` LAST — after all output writes incl. the manifest | PIPE; LOG; test_ui conventions |
| M7 | `Engine.log` (stage log) records only completed stages — a refused/aborted stage must not appear | ENG (`StageResult` append points); verified |
| M8 | a broken sink never fails a run; the terminal event still reaches the queue | EVT; RUN `_emit` fallback |
| M9 | a NEW independent run after failure/cancellation succeeds (fresh RunHandle/engine); reusing the same handle is NOT promised (`start()` raises) | RUN; assignment scope |
| M10 | manifest written LAST; its presence signals a complete output set; a partial write leaves tables but NO manifest; failure before the write block leaves previous outputs intact; failure mid-write loses them (documented, NOT all-or-nothing) | OUT |
| M11 | faults release resources via explicit `close()`; close is caller-owned (harness convention), observed idempotent-safe | harness; empirics |

Empirical foundations (probe scripts, not committed):
`Engine.close()` twice is safe; DuckDB table locks do NOT block writers
(optimistic concurrency → lock barriers unusable; the mid-query barrier
instead uses the documented `query_progress() > 0` in-flight signal);
`InterruptException` leaves the connection usable; idle `cancel()` does
not affect the next query; `00_macros.sql` is a real, data-free,
idempotent multi-statement script usable as a stage body through the
REAL `script_stage` path (statement splitting, per-statement events,
cancel checks all production code).

## Deterministic barriers (no sleeps used)

* **Between statements**: `Engine.emit` invokes sinks synchronously on
  the stage-executing thread, so a sink that calls `engine.cancel()`
  upon the first `StatementFinished` of stage k lands EXACTLY between
  two statements. Verified: exactly 1 statement of stage k, no
  `StageFinished` for it, engine refuses further stages.
* **Mid-query**: worker thread runs a ~4s `range(2e9)` mod-scan; main
  thread waits for `query_progress() > 0` (documented live-percentage
  signal; -1 when idle) before `cancel()`. Worker then sees
  `InterruptException`. Fails loudly on timeout rather than racing.
* **TUI cancel**: `pilot.press` waits for the message queue to idle —
  a 100k demo_full run (~1.6s) finishes inside a single `press`, so
  keyboard-timed cancels are not deterministic; the test calls
  `action_run()` then `action_cancel()` directly (the tests/test_ui.py
  pattern), landing in the documented "cancel before the Engine exists"
  window.
* **CLI SIGINT**: waits for the first unbuffered stage line
  (`PYTHONUNBUFFERED=1`) before signalling.

## Properties

| name | contract | oracle/double |
|---|---|---|
| `test_lifecycle_history` (@given History, 5 @examples) | legal start/cancel/fault/close histories match M1–M9 at engine level; fault/cancel precedence characterized | op-AST runner + model oracle + EventLog |
| `test_cancel_during_in_flight_query` | M2 mid-query interrupt; flag latches; later stages refused; connection survives (characterized) | `cancel_mid_query` barrier |
| `test_runhandle_success_stream_matches_plan` | M6 via the REAL runner over hand_fixture parquet | model oracle |
| `test_runhandle_cancel_before_engine_construction` | M1+M3: 0 StageFinished, cancelled=True | model oracle |
| `test_runhandle_repeated_cancel_is_idempotent` | M3+M4 mid-run | model oracle |
| `test_run_after_failure_new_handle_is_independent` | M9 + reused-handle RuntimeError | `partial(ScriptFaultEngine, fail_script_stage_at=2)` |
| `test_double_interfaces_match_the_real_adapter` | adapter cross-check: real qrp.run() abort shape equals the double's model (k-1 stages done, nothing after) | full_pipeline_run + ScriptFaultEngine |
| `test_fault_schedule_terminal_events` (@given, 4 @examples) | all four fault points: honest terminal, no later stages, no false success, no manifest on write fault, broken sink survives | Write/Construction/Script fault doubles + real runs |
| `test_engine_is_closed_when_the_caller_closes_it` | close path runs exactly once per call (CloseProbeEngine) | probe double |
| `test_cancelled_run_leaves_a_closable_engine` | resource release on the cancel path | real engine |
| `test_direct_run_sql_fault_propagates_without_ok` | direct driver: exception propagates, nothing claims ok (safe direction only — see OQ-1) | real run |
| `test_outputs_filenames_manifest_and_preservation` | filenames (runid prefix, SAS naming), disclosure routing, manifest shape, msoc .txt, row+schema preservation, non-empty cohort | `_successful_run` + duckdb read-back |
| `test_output_failure_is_not_reported_complete` (@given write k) | partial write: no manifest, no ok terminal, partial files allowed (documented) | WriteFaultEngine |
| `test_failure_during_pipeline_preserves_previous_outputs` | OUT "What a failure does" table row 1 | real runs, byte-compared file lists |
| `test_rerun_refreshes_and_clears_this_runid_prefix` | OUT rerun safety: scoped clear, sibling survives | real runs |
| `test_manifest_json_is_wellformed_and_maps_logical_names` | OUT manifest mapping | real run |
| `test_cli_failure_exit_status` | exit 0/1 with actionable diagnostics across success/missing-study/missing-tables/bad-table-map/validate | subprocess |
| `test_cli_sigint_mid_run_is_characterized` | SIGINT: nonzero exit, no manifest (characterization, see OQ-3) | subprocess |
| `test_tui_terminal_outcomes` / `test_tui_failure_reports_failed` / `test_tui_cancelled_reports_cancelled` | visible terminal states done/failed/cancelled + diagnostics reach the log | Textual Pilot |

## Coverage statistics (discovery profile, `--hypothesis-show-statistics`)

`test_lifecycle_history`: 492 cases, 0 invalid, ~12–70 ms each.
Cancel-timing mix: never 57.9%, after_statement 15.2%, after_stage
14.8%, before_start 12.0% (mid_query covered by its deterministic
test). Terminal mix: failed 42.7%, cancelled 29.7%, ok 27.6%. Stage
count 1–4: 30.9/22.8/22.0/24.4%. Fault points: none 48.6%, stage1
22.4%, stage2 14.6%, stage3 10.2%, stage4 4.3%. Highest target score
52 (event count). `test_double_interfaces_match_the_real_adapter`: 3
cases (success, fail@1, fail@2 — exhaustive). `test_fault_schedule_terminal_events`:
18 cases = the complete generated schedule space (sql/write × k,
construction, logging), partial-file counts 2/4/6 observed on write
faults.

## Calibration record (mandatory seeded defect, not committed)

* **Defect**: `src/qrp/pipeline.py` `run()` split-layout branch wrapped
  in `try: _write_split_layout(...) except Exception: pass` — "retain
  success status after a failed output write".
* **Command**: `.venv/bin/python -m pytest
  "tests/properties/test_pkg_e_outputs.py::test_output_failure_is_not_reported_complete"
  tests/properties/test_pkg_e_faults.py::test_fault_schedule_terminal_events
  --hypothesis-profile=quick`
* **Result**: both FAIL for the expected reason — `Failed: DID NOT
  RAISE RuntimeError` (match `injected output-write`). Extra
  confirmation harness (adhoc script, not committed): with the defect,
  `run()` emits `RunFinished(ok=True, cancelled=False)` while
  `manifest.json` is ABSENT — precisely the dishonest terminal the
  properties are built to catch.
* **Reverted**: `git checkout -- src/qrp/pipeline.py`; `git status`
  shows only package-E files; both properties green again.

## Verification record

| command | result |
|---|---|
| `pytest tests/properties/test_pkg_e_* --hypothesis-profile=quick` | **21 passed** (13.4 s) |
| discovery-profile stats run (lifecycle+adapter+faults) | 3 passed, stats above |
| `pytest tests/test_rewrites.py -q` | 11 passed |
| `pytest tests/test_ui.py -q` | 35 passed, 2 skipped (the known 2m-dataset skips: `test_cancel_stops_mid_query`, `test_spilling_is_detected_and_reported`) |
| `ruff check --select E4,E7,E9,F tests/properties` | clean |
| `mypy src tests/properties` | clean (34 files) |

## Findings / leads

1. **TUI never releases the run's engine** (`src/qrp/tui.py` `_pump`).
   `RunHandle.close()` exists precisely for the UI, but nothing in
   `tui.py` calls it — `_pump` closes only the RunLog. Every completed
   run in a TUI session leaves a live DuckDB connection (+ monitor
   cursor) with its in-memory database referenced by `app.handle`.
   Repeated runs in one session accumulate engines. Suspected contract:
   RUN's close-is-caller-owned plus UI.md's "the same runner drives
   the terminal, the TUI, …" imply the frontend must release; the
   shipped frontend doesn't. Expected: bounded resources per session;
   actual: one leaked engine per run. Severity: medium-low (local tool,
   session-length-bounded; no correctness wrong-answer). Not fixed
   (src/ is off-limits); easily reproduced by running two studies in
   one `QRPApp` and comparing `len(gc.get_objects())`/open FDs, or
   simply reading `_pump`.
2. **CLI failure path leaks the engine** (`src/qrp/cli.py` `main()`).
   `eng.close()` sits inside the `try` AFTER `run()`; any exception
   (bad study, failed stage) returns 1 with the engine open. Bounded by
   process exit, so severity: low. Same shape as finding 1; both are
   "release on the failure path" gaps in the two shipped frontends.
3. **SIGINT mid-run is unhandled and uninformative** (`src/qrp/cli.py`).
   `except Exception` cannot catch KeyboardInterrupt: the CLI dies with
   a raw traceback through `pipeline.run` (exit code -2/SIGINT), the
   JSONL run log ends with NO terminal record (last line observed:
   `StageFinished(normalize)`), and no FAILED line reaches the `.log`
   either (only the `finally` "log closed" line). No manifest survives
   (asserted — good). errors.py's "the runbook promises actionable
   errors … wherever the failure happens" arguably extends here.
   Severity: low-medium for the log-completeness gap (a killed run
   looks like a truncated log, with no machine-readable terminal).
4. **Direct `pipeline.run()` failures emit no RunFinished** (OQ-1,
   characterized in `test_direct_run_sql_fault_propagates_without_ok`).
   LOGGING.md says RunStarted/RunFinished "both come from pipeline.run,
   so the CLI and the UI observe an identical stream" — that parity
   holds only for successes: on failure the UI path adds
   `RunFinished(ok=False)` (from `RunHandle._work`) while the CLI/JSONL
   stream has no terminal event at all. Safe direction asserted; the
   full contract question is open.

## Open questions (unspecified behaviors characterized, not asserted)

* **OQ-1**: should `pipeline.run()` itself emit `RunFinished(ok=False)`
  on exception (direct-driver parity)? Currently only the RunHandle
  driver does. LOGGING.md's parity claim is ambiguous about failures.
* **OQ-2**: `Engine`/`RunHandle.close()` idempotency is observed safe
  (duckdb tolerates double close) but nowhere promised; asserted as
  characterization only, with comments marking it.
* **OQ-3**: CLI SIGINT contract (finding 3): what SHOULD the exit code
  and diagnostics be? What terminal record should the logs carry?
* **OQ-4**: restartability of the SAME Engine after a failure or
  cancellation is not promised anywhere and not asserted; only the
  NEW-independent-run story (M9) is tested, per assignment.
* **OQ-5**: a real mid-statement SQL failure leaves StageStarted
  outstanding (started−finished == 1); my ScriptFaultEngine double
  raises before the boundary (started == finished). The two precedence
  shapes differ; the real-in-stage failure shape is covered indirectly
  (fault inside `run()` raises inside `script_stage`), but a fault
  double that raises BETWEEN StageStarted and the first statement is
  not separately built.
* **OQ-6** (from package C's mid-flight note, verified in my runs):
  studies built by `synth.build_study()` (incl. hand_fixture) have no
  `cfg_care_setting` wildcard rows, so event claims inner-join to
  nothing and event-derived outputs are silently empty. My assertions
  deliberately target exposure-driven tables (cohort_final, attrition,
  censoring); TUI/CLI runs also warn "no encounter table"/"rxamt
  INTEGER" — both runs still complete and report honestly. Any future
  output-content property in this repo must either replicate the
  production loader's wildcard rows or stick to exposure-driven
  tables.
* **OQ-7**: TUI mid-query cancel via keyboard is not deterministically
  testable here (`pilot.press` waits out the ~1.6 s run; poll-timed
  escapes are racy). The mechanism is proven at engine and RunHandle
  level; the TUI-level test uses the documented cancel-during-startup
  window instead.

## Environment notes

* One-shot probe results this package depends on: DuckDB 2.0.0,
  hypothesis 6.170.14, textual 8.2.8 (`Static.content`, not
  `.renderable`); `duckdb_transactions()`/`duckdb_locks()` unavailable
  in this build; `demo_full.json` over `/tmp/qrp_data/100k` completes
  in ~1.6 s single-threaded on this box.
* Fixtures used: `study/demo_type2.json` trimmed to E00001/E00021
  (loads via `load_study`), `hand_fixture()` parquet roots in tmp,
  `/tmp/qrp_data/100k` for the SIGINT/TUI-cancel scenarios.
