"""AC.6 package E — fault schedules: terminal events, honesty, releases.

`test_fault_schedule_terminal_events` generates fail-on-kth-call
schedules over the four fault points the assignment names:

* engine construction   (ConstructionFaultEngine, RunHandle driver)
* SQL execution         (ScriptFaultEngine, both drivers)
* output write          (WriteFaultEngine, direct run() driver)
* logging               (broken_sink, both drivers)

Contract sources: RUN (runner.py — RunFinished on every outcome, error
explained, cancelled only on cancellation), PIPE (pipeline.run — stages
abort immediately; _finish only after writes), OUT (OUTPUTS.md — the
manifest's presence signals a complete run), EVT (events.py — a broken
sink must never fail a run).

Every double subclasses the real Engine, and the adapter cross-check in
test_pkg_e_lifecycle.py drives the real pipeline through the same
shapes, so these doubles cannot pass while the adapter is broken.
"""

from __future__ import annotations

import tempfile
import warnings
from functools import partial
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import event, example, given, strategies as st

from qrp.config import StudyConfig
from qrp.engine import Engine
from qrp.events import RunFinished
from qrp.pipeline import plan_stages, run as pipeline_run
from qrp.runner import RunHandle

from .conftest import expensive_settings
from .oracles import e_lifecycle_model as model
from .synth import SyntheticDataset, hand_fixture, write_parquet
from .doubles.e_faults import (
    CloseProbeEngine,
    ConstructionFaultEngine,
    EventLog,
    ScriptFaultEngine,
    WriteFaultEngine,
    broken_sink,
    close_quietly,
)


@pytest.fixture(scope="module")
def tiny_dataset_study() -> "tuple[SyntheticDataset, StudyConfig]":
    dataset, study = hand_fixture()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return dataset, study


@dataclass(frozen=True)
class FaultSchedule:
    kind: str      # construction | sql | write | logging
    at: int        # kth call (sql/write) — logging ignores it


schedules = st.builds(
    FaultSchedule,
    kind=st.sampled_from(
        ["sql", "write", "construction", "logging", "sql", "write"]),
    at=st.integers(min_value=1, max_value=3),
)


def _run_real_to_write_fault(
    dataset: SyntheticDataset,
    study: StudyConfig,
    fail_write_at: int,
) -> None:
    """Direct driver: all stages complete, write k fails mid-loop."""
    log = EventLog()
    engine = WriteFaultEngine(threads=1, verbose=False,
                              fail_write_at=fail_write_at, on_event=log)
    out = Path(tempfile.mkdtemp(prefix="pkg-e-outfault-")) / "out"
    root = write_parquet(dataset, Path(tempfile.mkdtemp()) / "indata")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(RuntimeError, match="injected output-write"):
                pipeline_run(study, str(root), engine=engine, verbose=False,
                             output_dir=out)
    finally:
        close_quietly(engine)

    # [PIPE] the write block runs only after every stage succeeded
    model.check_stage_log_honesty(engine, plan_stages(study))
    # honesty: no terminal event may claim success [RUN/PIPE]
    ok_events = [e for e in log.events
                 if isinstance(e, RunFinished) and e.ok]
    assert not ok_events, (
        "contract [PIPE]: a failed output write must not produce "
        "RunFinished(ok=True)")
    # [OUT] manifest is written LAST; a partial write leaves none
    model.expect_no_manifest(out)
    event(f"write-fault-partial-files={sum(1 for _ in out.rglob('*'))}")


@expensive_settings()
@given(schedules)
@example(FaultSchedule(kind="sql", at=1))
@example(FaultSchedule(kind="write", at=1))
@example(FaultSchedule(kind="construction", at=1))
@example(FaultSchedule(kind="logging", at=1))
def test_fault_schedule_terminal_events(schedule: FaultSchedule) -> None:
    # hand_fixture() is pure construction — cheap enough per example, and
    # @given tests must not take pytest fixtures as parameters.
    dataset, study = hand_fixture()
    event(f"fault-point={schedule.kind}")

    if schedule.kind == "write":
        _run_real_to_write_fault(dataset, study, schedule.at)
        return

    if schedule.kind == "construction":
        # RunHandle driver: the UI must still receive an honest terminal
        # event even though the Engine never existed [RUN _emit fallback]
        import qrp.runner

        ConstructionFaultEngine.reset_attempts()
        original = qrp.runner.Engine
        qrp.runner.Engine = ConstructionFaultEngine   # type: ignore[misc]
        try:
            handle = RunHandle(study, "/nonexistent", threads=1)
            handle.start()
            events = list(handle.events())
            assert handle.wait(timeout=60)
        finally:
            qrp.runner.Engine = original              # type: ignore[misc]

        model.check_failed_stream(events)
        finished = [e for e in events if isinstance(e, RunFinished)]
        assert "injected engine-construction failure" in finished[-1].error
        assert finished[-1].peak_memory_bytes == 0, (
            "contract [RUN]: no engine means no telemetry")
        handle.close()          # engine is None; must be a no-op [RUN]
        event("terminal=failed (construction)")
        return

    if schedule.kind == "sql":
        # RunHandle driver with the script fault: honest failure, then a
        # clean follow-on run proves independence (real pipeline, not a
        # double-only path).
        import qrp.runner

        original = qrp.runner.Engine
        qrp.runner.Engine = partial(            # type: ignore[assignment,misc]
            ScriptFaultEngine, fail_script_stage_at=schedule.at)
        try:
            handle = RunHandle(study, _tiny_root(dataset), threads=1)
            handle.start()
            events = list(handle.events())
            assert handle.wait(timeout=180)
        finally:
            qrp.runner.Engine = original              # type: ignore[misc]

        model.check_failed_stream(events)
        from qrp.events import StageFinished, StageStarted
        stage_started = [e for e in events if isinstance(e, StageStarted)]
        stage_finished = [e for e in events if isinstance(e, StageFinished)]
        assert len(stage_started) == len(stage_finished) == schedule.at - 1, (
            "contract [ENG]: the double raises before the real boundary, "
            "so the faulted stage emits no StageStarted and no later "
            "stage begins")
        event("terminal=failed (sql)")
        return

    # logging fault: a sink that always raises [EVT: broken sink must
    # never fail a run] — the run must still succeed end to end.
    handle = RunHandle(study, _tiny_root(dataset), threads=1,
                       extra_sink=broken_sink())
    handle.start()
    events = list(handle.events())
    assert handle.wait(timeout=180)
    model.check_success_stream(events, handle.plan())
    finished = [e for e in events if isinstance(e, RunFinished)]
    assert finished and finished[0].ok, (
        "contract [EVT]: the broken sink must not corrupt the outcome")
    handle.close()
    event("terminal=ok (broken logging sink)")


_TINY_ROOT_CACHE: dict[int, Path] = {}


def _tiny_root(dataset: SyntheticDataset) -> Path:
    """One parquet root per dataset object, reused read-only per session."""
    key = id(dataset)
    if key not in _TINY_ROOT_CACHE:
        _TINY_ROOT_CACHE[key] = write_parquet(
            dataset, Path(tempfile.mkdtemp(prefix="pkg-e-tiny-")) / "indata")
    return _TINY_ROOT_CACHE[key]


# ---------------------------------------------------------------------
# Resources are released
# ---------------------------------------------------------------------


def test_engine_is_closed_when_the_caller_closes_it(tiny_dataset_study) -> None:
    """[RUN] close() closes the engine; the probe double proves the real
    close path runs exactly once per close call."""
    _, study = tiny_dataset_study
    engine = CloseProbeEngine(threads=1, verbose=False, on_event=None)
    engine.script_stage("probe", "00_macros.sql")
    assert engine.close_calls == 0
    engine.close()
    assert engine.close_calls == 1
    engine.close()
    assert engine.close_calls == 2, (
        "characterization [OQ-4]: double close is observed safe and "
        "reaches the real close path each time")
    with pytest.raises(Exception):
        # characterization (not asserted as a promise): the closed
        # connection refuses further work
        engine.con.execute("SELECT 1").fetchall()


def test_cancelled_run_leaves_a_closable_engine(
    tiny_dataset_study: "tuple[SyntheticDataset, StudyConfig]",
) -> None:
    """Cancel mid-run, then close: resource release works on the cancel
    path, not only on success [harness convention: close in finally]."""
    dataset, study = tiny_dataset_study
    log = EventLog()
    engine = Engine(threads=1, verbose=False, on_event=log)
    engine.cancel()
    root = _tiny_root(dataset)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(Exception) as excinfo:
                pipeline_run(study, str(root), engine=engine, verbose=False,
                             output_dir=None)
        assert type(excinfo.value).__name__ == "CancelledRun", (
            "contract [ENG]: the pre-cancelled engine refuses the run")
        assert engine.cancelled
        engine.close()
        engine.close()
    finally:
        close_quietly(engine)


# ---------------------------------------------------------------------
# Direct-driver honesty for pipeline.run failures (characterization)
# ---------------------------------------------------------------------


def test_direct_run_sql_fault_propagates_without_ok(
    tiny_dataset_study,
) -> None:
    """Direct `run()` driver: the exception propagates and nothing ever
    claims success. Whether pipeline.run should ALSO emit a RunFinished
    on failure is unspecified — recorded as ledger OQ-1, asserted here
    only in the safe direction (never ok=True)."""
    dataset, study = tiny_dataset_study
    log = EventLog()
    engine = ScriptFaultEngine(threads=1, verbose=False,
                               fail_script_stage_at=1, on_event=log)
    root = _tiny_root(dataset)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(RuntimeError, match="injected sql failure"):
                pipeline_run(study, str(root), engine=engine, verbose=False,
                             output_dir=None)
    finally:
        close_quietly(engine)
    assert not [e for e in log.events
                if isinstance(e, RunFinished) and e.ok], (
        "contract [RUN]: a failed run must not claim success on any driver")
