# pattern: Functional Core -- pure assertions over recorded event streams; no
# I/O, no engine construction, fully deterministic.
"""The lifecycle model: what the docs and code PROMISE, stated as oracles.

Every rule cites its source — a doc section or a code promise — so a
failing assertion names the contract it enforces. Anything the sources
do NOT promise is deliberately absent from this module and recorded as
an open question in the package ledger instead of asserted.

Sources used throughout
-----------------------
* ARCH  = docs/ARCHITECTURE.md, "Cancellation, and a bug this review found"
* UI    = docs/UI.md, "What DuckDB gives you (verified, not assumed)"
* LOG   = docs/LOGGING.md, "A bug this found"
* OUT   = docs/OUTPUTS.md, "Rerun safety" / "What a failure does"
* ENG   = src/qrp/engine.py docstrings/promises
* RUN   = src/qrp/runner.py docstrings/promises
* PIPE  = src/qrp/pipeline.py run()/_finish structure
* EVT   = src/qrp/events.py docstrings ("callbacks must not raise...")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qrp.events import (
    Event,
    LogMessage,
    Level,
    RunFinished,
    RunStarted,
    StageFinished,
    StageStarted,
)

# Terminal classifications over a recorded event stream.
OK = "ok"
CANCELLED = "cancelled"
FAILED = "failed"
NO_TERMINAL = "no-terminal-event"


def classify_terminal(events: list[Event]) -> str:
    """The outcome the stream's RunFinished events claim.

    Model rule: exactly one RunFinished per run on the RunHandle path
    (RUN: `_work` emits it on both success and failure; PIPE: `run()`
    emits it on success). More than one is a defect; zero is recorded
    as NO_TERMINAL because the direct-`run()` failure path does not
    promise one (open question OE-1 in the ledger).
    """
    finished = [e for e in events if isinstance(e, RunFinished)]
    if not finished:
        return NO_TERMINAL
    if len(finished) > 1:
        raise AssertionError(
            f"contract [RUN/PIPE]: a run reports one outcome; got "
            f"{len(finished)} RunFinished events")
    ev = finished[0]
    if ev.cancelled:
        return CANCELLED
    return OK if ev.ok else FAILED


def _run_finished(events: list[Event]) -> RunFinished:
    finished = [e for e in events if isinstance(e, RunFinished)]
    assert finished, "contract [RUN]: RunHandle must always deliver RunFinished"
    assert len(finished) == 1, (
        f"contract [RUN/PIPE]: exactly one RunFinished expected, "
        f"got {len(finished)}")
    return finished[0]


def stage_names(events: list[Event], cls: type) -> list[str]:
    return [e.name for e in events if isinstance(e, cls)]  # type: ignore[attr-defined]


def check_success_stream(events: list[Event], plan: list[str]) -> None:
    """The documented success shape.

    [LOG] RunStarted and RunFinished both come from pipeline.run, so
    CLI and UI observe the same stream.  [PIPE] RunFinished(ok=True)
    is emitted by _finish AFTER all output writes, so ok=True claims
    the outputs and manifest exist.  [ARCH] the declared plan (from
    plan_stages) must match what actually ran or progress bars lie.
    """
    started = stage_names(events, StageStarted)
    finished = stage_names(events, StageFinished)
    assert started, "contract [PIPE]: a successful run starts stages"
    assert started == finished, (
        "contract [test_ui convention]: every stage that starts finishes, "
        f"paired and in order; started={started} finished={finished}")
    assert len(finished) == len(plan), (
        f"contract [ARCH]: a completed run executes the full plan "
        f"({len(finished)} of {len(plan)} stages finished)")
    it = iter(plan)
    assert all(name in it for name in finished), (
        f"contract [ARCH]: executed stages must follow the declared plan "
        f"order; executed={finished} plan={plan}")

    run_finished = _run_finished(events)
    assert run_finished.ok and not run_finished.cancelled and not run_finished.error, (
        f"contract [PIPE]: success must be reported honestly; got {run_finished}")
    assert events[-1] is run_finished, (
        "contract [PIPE]: RunFinished is the terminal event")

    begun = [e for e in events if isinstance(e, RunStarted)]
    assert begun, "contract [LOG]: the stream carries RunStarted"
    first_started = next(i for i, e in enumerate(events)
                         if isinstance(e, RunStarted))
    assert all(not isinstance(e, (StageStarted, StageFinished))
               for e in events[:first_started]), (
        "contract [LOG]: no stage runs before RunStarted")
    if begun:
        assert tuple(begun[0].stages) == tuple(plan), (
            "contract [ARCH]: the declared plan must match what actually ran")


def check_cancelled_stream(events: list[Event], plan: list[str]) -> None:
    """The documented cancellation shape.

    [ARCH table] after a cancel: 0 stages complete in the early-cancel
    case, RunFinished.cancelled is True, ok is False.  [ENG] cancel at
    a boundary refuses the NEXT stage before emitting StageStarted; a
    mid-stage interrupt leaves that one stage started-but-unfinished.
    [RUN] a cancelled run's error string stays empty (a cancel is not
    a failure) and no ERROR-level LogMessage is emitted.
    """
    started = stage_names(events, StageStarted)
    finished = stage_names(events, StageFinished)
    assert len(started) - len(finished) <= 1, (
        f"contract [ENG]: cancel stops within one operator boundary; "
        f"started={len(started)} finished={len(finished)}")
    assert len(finished) < len(plan), (
        f"contract [ARCH]: cancelling must stop the run before the plan "
        f"completes ({len(finished)}/{len(plan)} finished)")

    run_finished = _run_finished(events)
    assert run_finished.cancelled, (
        "contract [ARCH]: the run reports itself cancelled")
    assert not run_finished.ok, (
        "contract [ARCH]: a cancelled run is not a successful run")
    assert run_finished.error == "", (
        "contract [RUN]: a cancelled run carries no error text "
        f"(got {run_finished.error!r})")
    errors = [e for e in events
              if isinstance(e, LogMessage) and e.level is Level.ERROR]
    assert not errors, (
        "contract [RUN]: no ERROR LogMessage is emitted for a cancellation")


def check_failed_stream(events: list[Event]) -> None:
    """The documented failure shape (RunHandle driver).

    [RUN] an exception in _work yields RunFinished(ok=False,
    cancelled=False, error=explain(exc)) plus an ERROR LogMessage
    BEFORE the terminal event. Reporting must be honest: a failed run
    never claims ok=True.
    """
    run_finished = _run_finished(events)
    assert not run_finished.ok, (
        "contract [RUN]: a failed run must not report success")
    assert not run_finished.cancelled, (
        "contract [RUN]: an ordinary failure is not a cancellation")
    assert run_finished.error, (
        "contract [RUN]: a failed run explains itself (error non-empty)")

    started = stage_names(events, StageStarted)
    finished = stage_names(events, StageFinished)
    assert len(started) - len(finished) <= 1, (
        "contract [PIPE]: an exception aborts the pipeline immediately; "
        "at most the failed stage may be started-but-unfinished")

    errors = [i for i, e in enumerate(events)
              if isinstance(e, LogMessage) and e.level is Level.ERROR]
    assert errors, "contract [RUN]: failure is announced at ERROR level"
    assert max(errors) < events.index(run_finished), (
        "contract [RUN]: the ERROR LogMessage precedes the terminal event")


def check_stage_log_honesty(engine: Any, completed: list[str]) -> None:
    """Engine.log records exactly the stages that completed.

    [ENG] StageResult is appended only after a stage's statements run;
    a refused or aborted stage must not appear, or the summary and the
    runtimes output lie about what happened.
    """
    got = [r.name for r in engine.log]
    assert got == completed, (
        f"contract [ENG]: engine stage log must equal completed stages; "
        f"got {got}, expected {completed}")


# ---------------------------------------------------------------------
# Filesystem / manifest contract (OUT)
# ---------------------------------------------------------------------


def read_manifest(out: Path) -> dict[str, Any]:
    path = out / "manifest.json"
    assert path.exists(), (
        "contract [OUT]: manifest.json is written LAST; a completed run "
        f"must have left it at {path}")
    return json.loads(path.read_text())


def expect_no_manifest(out: Path) -> None:
    path = out / "manifest.json"
    assert not path.exists(), (
        "contract [OUT]: the manifest's PRESENCE signals a complete run; "
        "a partial write must not leave one behind")


def check_manifest_shape(manifest: dict[str, Any], run_id: str) -> None:
    """The documented manifest fields (OUT 'Reading the manifest')."""
    assert manifest["runid"] == run_id, (
        "contract [OUT]: manifest names the run")
    assert manifest["layout"] == "split", (
        "contract [OUT]: the split layout is what run() writes")
    assert manifest["names"] in ("sas", "logical"), (
        "contract [OUT]: naming mode is recorded")
    for logical, entry in manifest["tables"].items():
        assert entry["library"] in ("msoc", "dplocal"), (
            f"contract [OUT]: table {logical} declares its library")
        assert entry["file"], f"contract [OUT]: table {logical} names its file"
        if not entry["sas_contract"]:
            assert "note" in entry, (
                f"contract [OUT]: a non-contract output carries its note "
                f"({logical})")


def check_output_files(out: Path, manifest: dict[str, Any]) -> None:
    """Every manifest entry resolves to a real parquet file or partition.

    [OUT] outputs land under dplocal/ or msoc/ named <runid>_<sas>;
    the manifest maps logical names to files either way.
    """
    run = manifest["runid"].lower()
    for logical, entry in manifest["tables"].items():
        lib_dir = out / entry["library"]
        expected_base = entry["file"]
        assert expected_base.startswith(f"{run}_"), (
            f"contract [OUT]: file names carry the runid prefix "
            f"({expected_base} for {logical})")
        candidates = [lib_dir / f"{expected_base}.parquet", lib_dir / expected_base]
        assert any(p.exists() for p in candidates), (
            f"contract [OUT]: manifest table {logical} has no file under "
            f"{lib_dir} (tried {[str(p) for p in candidates]})")
