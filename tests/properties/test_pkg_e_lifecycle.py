"""AC.6 package E — the run lifecycle state machine.

Model choice, as required by the assignment
-------------------------------------------
This is an OPERATION-AST GENERATOR executed against the model in
`../oracles/e_lifecycle_model.py`, not a RuleBasedStateMachine.
Justification: the lifecycle is one-shot — `RunHandle.start()` raises
on reuse and each engine is disposable — so a persistent machine would
rebuild its system under test on every rule anyway; the interesting
dimension is a small INDEPENDENT CHOICE SET along a linear pipeline
(cancel timing × fault point × repeat × follow-on run), which a
generated `History` record expresses constructively and shrinks to a
minimal schedule; and every history is legal by construction, so
shrinking never filters out the interesting operation.

Model scope — DOCUMENTED outcomes only. Each oracle assertion cites its
source (doc section or code promise); see oracles/e_lifecycle_model.py.
Behaviours the sources do not promise (restartability of the same
engine, double close, direct-run() failure events) are characterized in
tests and ledger open questions, never silently asserted as contracts.

Deterministic barriers: between-statement cancels fire from a sink
inside the engine's synchronous emit path (exact boundary); mid-query
cancels wait for `query_progress() > 0` (documented in-flight signal).
No sleeps, no race windows.
"""

from __future__ import annotations

import tempfile
import warnings
from functools import partial
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import event, example, given, strategies as st, target

from qrp.config import StudyConfig
from qrp.engine import CancelledRun, Engine
from qrp.events import RunFinished, StatementFinished, StageFinished, StageStarted
from qrp.pipeline import plan_stages, run as pipeline_run
from qrp.runner import RunHandle

from .conftest import expensive_settings
from .harness import full_pipeline_run
from .synth import hand_fixture, write_parquet
from .doubles.e_faults import (
    EventLog,
    ScriptFaultEngine,
    cancel_mid_query,
    cancel_on,
    close_quietly,
    multi_sink_recorded,
)
from .oracles import e_lifecycle_model as model

# A real shipped, data-free, idempotent multi-statement script: every
# statement goes through the production script_stage path (splitting,
# per-statement events, cancel checks) without needing SCDM tables.
MACROS = "00_macros.sql"


# ---------------------------------------------------------------------
# Engine tier: generated legal histories
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class History:
    """One legal lifecycle history over an n-stage engine-level run."""

    n_stages: int            # stages attempted
    cancel_point: str        # where the (single) cancel lands
    cancel_stage: int        # stage the cancel relates to (clamped)
    repeat_cancel: bool      # cancel twice, per RunHandle's idempotency
    fail_stage_at: int       # 0 = no fault; else kth script_stage call fails
    run_after: bool          # follow the terminal with a NEW independent run


histories = st.builds(
    History,
    n_stages=st.integers(min_value=1, max_value=4),
    # weighted so boundary windows dominate; mid_query has its own
    # deterministic test (the in-flight barrier runs a real query)
    cancel_point=st.sampled_from([
        "never", "never", "never",
        "before_start", "before_start",
        "after_stage", "after_stage", "after_stage",
        "after_statement", "after_statement", "after_statement",
    ]),
    cancel_stage=st.integers(min_value=1, max_value=4),
    repeat_cancel=st.booleans(),
    fail_stage_at=st.sampled_from([0, 0, 0, 1, 2, 3, 4]),
    run_after=st.booleans(),
)


def _run_history(h: History) -> None:
    ns = h.n_stages
    ck = min(h.cancel_stage, ns)
    fault_at = h.fail_stage_at
    if fault_at:
        fault_at = min(fault_at, ns)
        engine: Engine = ScriptFaultEngine(
            threads=1, verbose=False,
            fail_script_stage_at=fault_at, on_event=None)
    else:
        engine = Engine(threads=1, verbose=False, on_event=None)

    log = EventLog()
    engine.on_event = log
    completed: list[str] = []
    terminal = "ok"
    cancel_requested = False

    if h.cancel_point == "before_start":
        engine.cancel()
        if h.repeat_cancel:
            engine.cancel()
        cancel_requested = True

    for k in range(1, ns + 1):
        between_statements = (
            h.cancel_point == "after_statement" and k == ck)
        if between_statements:
            # Cancel from INSIDE the synchronous emit path: fires on the
            # first StatementFinished of THIS stage, i.e. exactly between
            # two statements (emit runs on the stage-executing thread).
            # Whether it fires at all is decided later: an injected fault
            # on this same call raises before the first statement.
            engine.on_event = multi_sink_recorded(
                log, cancel_on(engine, StatementFinished, nth=1))
        try:
            engine.script_stage(f"stage{k}", MACROS)
        except CancelledRun:
            terminal = "cancelled"
            break
        except RuntimeError as exc:
            assert "injected sql failure" in str(exc), exc
            terminal = "failed"
            break
        finally:
            if between_statements:
                engine.on_event = log
        completed.append(f"stage{k}")
        if h.cancel_point == "after_stage" and k == ck:
            engine.cancel()
            if h.repeat_cancel:
                engine.cancel()
            cancel_requested = True

    if h.cancel_point == "after_stage" and ck == ns and len(completed) == ns:
        # cancel after the LAST stage: everything completed, flag set.
        # (If a fault preempts stage ck this branch must not fire — the
        # loop never reached the post-stage cancel.)
        terminal = "cancelled"
        cancel_requested = True
    if h.cancel_point == "before_start":
        terminal = "cancelled"
        cancel_requested = True
    if (h.cancel_point == "after_statement" and terminal == "cancelled"):
        # the sink fired inside the stage, so a cancel was requested
        cancel_requested = True

    # ---- coverage statistics ----------------------------------------
    event(f"cancel-timing={h.cancel_point}")
    event(f"terminal={terminal}")
    event(f"fault={'none' if not h.fail_stage_at else f'stage{fault_at}'}")
    event(f"n_stages={ns}")
    target(float(len(log.events)))

    # ---- model assertions (each cites its source) --------------------
    started = model.stage_names(log.events, StageStarted)
    finished = model.stage_names(log.events, StageFinished)
    stmts_in_cancelling_stage = sum(
        1 for ev in log.events
        if isinstance(ev, StatementFinished) and ev.stage == f"stage{ck}")

    assert engine.cancelled == cancel_requested, (
        "contract [ENG]: the cancelled flag reflects exactly the "
        "requested cancels")

    if terminal == "cancelled":
        if h.cancel_point == "before_start":
            assert not started and not finished and not completed, (
                "contract [ARCH table]: an early cancel completes 0 stages")
        if h.cancel_point == "after_statement":
            assert stmts_in_cancelling_stage == 1, (
                "contract [ENG]: the cancelling sink fired on exactly one "
                f"statement; saw {stmts_in_cancelling_stage}")
            assert len(finished) == ck - 1, (
                "contract [ENG]: the interrupted stage never emits "
                "StageFinished")
            assert len(started) == ck, (
                "contract [ENG]: the interrupted stage did start")
            assert completed == [f"stage{i}" for i in range(1, ck)], (
                "contract [ENG]: stages before the cancel completed; "
                f"completed={completed}")
        if h.cancel_point == "after_stage" and ck < ns:
            assert completed == [f"stage{i}" for i in range(1, ck + 1)], (
                "contract [ARCH]: stages through the boundary completed; "
                f"completed={completed}")
            assert started == finished == completed, (
                "contract [ENG]: the refused stage emits no StageStarted")
        if h.cancel_point == "after_stage" and ck == ns:
            assert completed == [f"stage{i}" for i in range(1, ns + 1)], (
                "contract [ENG]: a post-run cancel completes everything "
                "first")

    if terminal == "failed":
        assert fault_at, "a failure without an injected fault is a bug"
        assert completed == [f"stage{i}" for i in range(1, fault_at)], (
            "contract [PIPE]: the exception aborts the pipeline at stage "
            f"{fault_at}; completed={completed}")
        if not cancel_requested:
            assert not engine.cancelled, (
                "contract [RUN]: an injected fault is not a cancellation")
        else:
            # the fault double raises BEFORE the boundary check, so the
            # fault preempts a pending cancel; the latched flag remains
            assert engine.cancelled, (
                "contract [ENG]: a requested cancel stays latched even "
                "when a fault preempts it")
        assert started == finished == completed, (
            "contract [ENG]: the faulted call raises before its stage "
            "boundary emits anything")

    if terminal == "ok":
        assert completed == [f"stage{i}" for i in range(1, ns + 1)]
        assert started == finished == completed, (
            "contract [test_ui convention]: starts and finishes pair up")

    model.check_stage_log_honesty(engine, completed)

    engine.close()
    engine.close()   # characterization: observed safe; see ledger OQ-4

    if h.run_after:
        # failure/cancel followed by a NEW independent run: a fresh
        # engine must reach full completion untouched by the old one.
        fresh = Engine(threads=1, verbose=False, on_event=None)
        fresh.script_stage("fresh-1", MACROS)
        fresh.script_stage("fresh-2", MACROS)
        model.check_stage_log_honesty(fresh, ["fresh-1", "fresh-2"])
        fresh.close()


@given(histories)
@example(History(n_stages=2, cancel_point="after_statement", cancel_stage=2,
                 repeat_cancel=False, fail_stage_at=0, run_after=False))
@example(History(n_stages=3, cancel_point="after_stage", cancel_stage=2,
                 repeat_cancel=True, fail_stage_at=0, run_after=True))
@example(History(n_stages=2, cancel_point="before_start", cancel_stage=1,
                 repeat_cancel=False, fail_stage_at=1, run_after=True))
@example(History(n_stages=2, cancel_point="after_stage", cancel_stage=2,
                 repeat_cancel=False, fail_stage_at=0, run_after=False))
@example(History(n_stages=1, cancel_point="never", cancel_stage=1,
                 repeat_cancel=False, fail_stage_at=0, run_after=True))
def test_lifecycle_history(h: History) -> None:
    _run_history(h)


# Required deterministic case: cancel during a blocked (in-flight)
# query. Plain test — the barrier waits on the documented progress
# signal, so it carries no timing race.
def test_cancel_during_in_flight_query() -> None:
    engine = Engine(threads=1, verbose=False, on_event=None)
    try:
        err = cancel_mid_query(engine)
        assert err == "InterruptException", (
            "contract [UI/ARCH]: interrupt() must stop the executing "
            f"query; worker saw {err!r}")
        assert engine.cancelled, "contract [ENG]: the flag latches"
        try:
            engine.script_stage("after-cancel", MACROS)
            raise AssertionError(
                "contract [ENG]: stages are refused after cancellation")
        except CancelledRun:
            pass
        # characterization (not a documented promise): the connection
        # survives its own InterruptException
        engine.con.execute("SELECT 1").fetchall()
    finally:
        close_quietly(engine)


# ---------------------------------------------------------------------
# RunHandle tier: real tiny-pipeline runs through the production runner
# ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def tiny_run() -> "tuple[StudyConfig, Path]":
    """hand_fixture dataset as a parquet root, plus its valid study.

    A full REAL pipeline run over a hand-calculated 5-patient fixture:
    this is the adapter cross-check tier that keeps the fast doubles
    honest.
    """
    dataset, study = hand_fixture()
    root = Path(tempfile.mkdtemp(prefix="pkg-e-tiny-")) / "indata"
    write_parquet(dataset, root)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return study, root


def _drain(handle: RunHandle) -> list:
    events = list(handle.events())
    assert handle.wait(timeout=180), "run did not finish"
    return events


def test_runhandle_success_stream_matches_plan(tiny_run) -> None:
    study, root = tiny_run
    handle = RunHandle(study, root, threads=1).start()
    events = _drain(handle)
    plan = handle.plan()
    model.check_success_stream(events, plan)
    handle.close()
    handle.close()   # characterization: observed safe; see ledger OQ-4


def test_runhandle_cancel_before_engine_construction(tiny_run) -> None:
    """[ARCH table] cancel-before-start: 0 stages, cancelled=True."""
    study, root = tiny_run
    handle = RunHandle(study, root, threads=1)
    handle.cancel()
    handle.cancel()          # documented idempotent [RUN]
    handle.start()
    events = _drain(handle)
    model.check_cancelled_stream(events, handle.plan())
    assert not model.stage_names(events, StageFinished), (
        "contract [ARCH table]: no stage completes after an early cancel")
    handle.close()


def test_runhandle_repeated_cancel_is_idempotent(tiny_run) -> None:
    """[RUN] cancel() is safe from any thread and idempotent; the run
    still delivers exactly one terminal RunFinished."""
    study, root = tiny_run
    handle = RunHandle(study, root, threads=1).start()
    cancelled_mid_run = False
    for ev in handle.events():
        if isinstance(ev, StageFinished):
            handle.cancel()
            handle.cancel()
            cancelled_mid_run = True
            break
    assert cancelled_mid_run, "tiny run finished before the cancel landed"
    events = _drain(handle)
    model.check_cancelled_stream(events, handle.plan())
    handle.close()


def test_run_after_failure_new_handle_is_independent(
    tiny_run, monkeypatch,
) -> None:
    """The restart story that IS promised: a NEW RunHandle after a
    failed run reaches success. The one that is NOT promised — reusing
    the same handle — is checked as documented: start() raises [RUN]."""
    import qrp.runner

    study, root = tiny_run

    original = qrp.runner.Engine
    qrp.runner.Engine = partial(ScriptFaultEngine,   # type: ignore[assignment,misc]
                                fail_script_stage_at=2)
    try:
        broken = RunHandle(study, root, threads=1)
        broken.start()
        events = _drain(broken)
        with pytest.raises(RuntimeError, match="already started"):
            broken.start()
    finally:
        qrp.runner.Engine = original                # type: ignore[misc]

    model.check_failed_stream(events)
    finished = [e for e in events if isinstance(e, RunFinished)]
    assert finished and "injected" in finished[-1].error, (
        "contract [RUN]: the failure names its cause")

    fresh = RunHandle(study, root, threads=1).start()
    fresh_events = _drain(fresh)
    model.check_success_stream(fresh_events, fresh.plan())
    fresh.close()


# ---------------------------------------------------------------------
# Adapter cross-check for the doubles' event vocabulary
# ---------------------------------------------------------------------


@expensive_settings()
@given(st.integers(min_value=0, max_value=2))
@example(0)
@example(1)
@example(2)
def test_double_interfaces_match_the_real_adapter(fail_at: int) -> None:
    """A passing double suite with a broken adapter is a false positive.

    Drives the REAL qrp.run() end to end over hand_fixture. Success
    (fault 0): the engine's stage log equals plan_stages() — the same
    success shape the doubles tier enforces. Fault k: the real pipeline
    aborts at the kth script_stage call with the same event shape the
    double models (k-1 finished stages, the kth started, nothing after).
    """
    dataset, study = hand_fixture()
    if not fail_at:
        with full_pipeline_run(dataset, study) as result:
            model.check_stage_log_honesty(
                result.engine, plan_stages(study))
        event("adapter-cross-check=success")
        return

    log = EventLog()
    engine = ScriptFaultEngine(threads=1, verbose=False,
                               fail_script_stage_at=fail_at, on_event=log)
    root = write_parquet(dataset, Path(tempfile.mkdtemp()) / "indata")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(RuntimeError, match="injected sql failure"):
                pipeline_run(study, str(root), engine=engine, verbose=False,
                             output_dir=None)
    finally:
        close_quietly(engine)
    started = model.stage_names(log.events, StageStarted)
    finished = model.stage_names(log.events, StageFinished)
    assert started == finished == plan_stages(study)[: fail_at - 1], (
        "contract [ENG]: the faulted call raises before its stage "
        "boundary, so exactly the k-1 earlier stages started and "
        "finished; no later stage begins")
    event(f"adapter-cross-check=fail-at-{fail_at}")
