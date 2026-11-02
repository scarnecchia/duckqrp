# pattern: Test doubles (fault injection) -- Mixed (unavoidable): doubles must
# subclass the real Engine adapter so their interfaces are exercised against
# the production code paths they stand in for.
"""Fault doubles and deterministic barriers for the runtime/lifecycle package.

Everything here wraps or subclasses the REAL `qrp.engine.Engine` — the
doubles differ from production only at the single seam named by their
class, so a passing double suite still proves the real adapter's
interfaces.

Deterministic barriers, not sleeps
----------------------------------
* Between statements / between stages: `Engine.emit` calls the sink
  synchronously on the thread executing the stage, so a sink that calls
  `engine.cancel()` upon seeing a `StatementFinished` lands EXACTLY
  between two statements, and a direct `engine.cancel()` between two
  `script_stage` calls lands exactly on a stage boundary.
* Mid-query: `cancel_mid_query` runs a deliberately long scan on a
  worker thread and waits for `Engine.con.query_progress() > 0` — the
  documented in-flight signal ("a polling thread sees 0->100 during
  scans"; idle returns -1) — before cancelling. No timing guesses: if
  the query were not in flight, the wait would fail loudly instead of
  racing.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from qrp.engine import Engine
from qrp.events import Event, EventSink

# A scan long enough (~4 s at threads=1) that "wait for progress > 0,
# then cancel" cannot miss it, yet cheap once interrupted (~0.3 s).
# range()'s column must be aliased for DuckDB 2 (`AS t(i)`).
LONG_QUERY = "SELECT sum(i % 97) FROM range(2000000000) AS t(i)"


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float,
    what: str,
    poll: float = 0.01,
) -> None:
    """Wait for a condition, not for time. Raises (fails loudly) on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(poll)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


# ---------------------------------------------------------------------
# Event recording / acting sinks
# ---------------------------------------------------------------------


@dataclass
class EventLog:
    """A sink that records every event it is given."""

    events: list[Event] = field(default_factory=list)

    def __call__(self, ev: Event) -> None:
        self.events.append(ev)

    def of(self, cls: type) -> list[Event]:
        return [e for e in self.events if isinstance(e, cls)]

    def type_names(self) -> list[str]:
        return [type(e).__name__ for e in self.events]


def cancel_on(
    engine: Engine,
    cls: type,
    nth: int = 1,
) -> EventSink:
    """A sink that cancels the engine on the nth event of type `cls`.

    Because Engine.emit invokes sinks synchronously on the executing
    thread, this lands the cancel at a precise point in the stage loop
    (e.g. between two statements), never in a timing race.
    """
    seen = 0

    def sink(ev: Event) -> None:
        nonlocal seen
        if isinstance(ev, cls):
            seen += 1
            if seen == nth:
                engine.cancel()

    return sink


def multi_sink_recorded(
    *sinks: EventSink | None,
) -> Callable[[Event], None]:
    """Chain sinks left to right; every event reaches every sink."""

    active = [s for s in sinks if s is not None]

    def sink(ev: Event) -> None:
        for s in active:
            s(ev)

    return sink


def broken_sink() -> EventSink:
    """A sink that always raises — the documented 'broken UI' double."""
    def sink(ev: Event) -> None:
        raise RuntimeError("broken sink raised (by design)")
    return sink


# ---------------------------------------------------------------------
# Engine fault doubles
# ---------------------------------------------------------------------


@dataclass
class ScriptFaultEngine(Engine):
    """A real Engine whose `script_stage` raises on the kth call.

    The fault fires BEFORE the real stage boundary code runs, so the
    failing call emits no StageStarted — the shape a pipeline-level
    fault must be modelled against.
    """

    fail_script_stage_at: int = 0      # 0 disables the fault
    _script_calls: int = field(default=0, init=False)

    def script_stage(self, name: str, script: str, **params: object) -> None:
        self._script_calls += 1
        if self.fail_script_stage_at and self._script_calls == self.fail_script_stage_at:
            raise RuntimeError(
                f"injected sql failure: script_stage call "
                f"{self._script_calls} ({name!r})")
        super().script_stage(name, script, **params)


@dataclass
class WriteFaultEngine(Engine):
    """A real Engine whose `write_parquet` raises on the kth call."""

    fail_write_at: int = 0             # 0 disables the fault
    _write_calls: int = field(default=0, init=False)

    def write_parquet(self, table: str, path: str | Path,
                      partition_by: str | None = None) -> None:
        self._write_calls += 1
        if self.fail_write_at and self._write_calls == self.fail_write_at:
            raise RuntimeError(
                f"injected output-write failure on call "
                f"{self._write_calls} ({table!r})")
        super().write_parquet(table, path, partition_by=partition_by)


class ConstructionFaultEngine(Engine):
    """A real Engine whose construction raises on the kth attempt.

    The attempt counter is class-level because every attempt is a fresh
    instance; `reset_attempts()` must be called between examples.
    """

    _attempts: ClassVar[int] = 0

    def __init__(self, *args: object, fail_construction_at: int = 1,
                 **kwargs: object) -> None:
        type(self)._attempts += 1
        if type(self)._attempts >= fail_construction_at:
            raise RuntimeError(
                "injected engine-construction failure "
                f"(attempt {type(self)._attempts})")
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    @classmethod
    def reset_attempts(cls) -> None:
        cls._attempts = 0


@dataclass
class CloseProbeEngine(Engine):
    """A real Engine that records close() calls, to prove release."""

    close_calls: int = field(default=0, init=False)

    def close(self) -> None:
        self.close_calls += 1
        super().close()


# ---------------------------------------------------------------------
# Mid-query barrier
# ---------------------------------------------------------------------


def cancel_mid_query(engine: Engine, *, progress_timeout: float = 20.0) -> str | None:
    """Cancel a provably in-flight query; return the worker's error type.

    Runs `LONG_QUERY` on a worker thread and waits until
    `query_progress() > 0` — the documented live-percentage signal for
    the query currently executing on the connection — before calling
    `engine.cancel()`. This is a deterministic barrier: the cancel can
    only fire once the query is genuinely executing, so the documented
    mid-query interrupt path (`InterruptException` in the blocked
    thread) is exercised without a single timing guess.

    Returns the exception type name observed in the worker thread, or
    None if the query completed (which means the barrier failed to hold
    and the caller must fail the test).
    """
    outcome: dict[str, str | None] = {"err": None}

    def worker() -> None:
        try:
            engine.con.execute(LONG_QUERY).fetchall()
        except Exception as exc:            # noqa: BLE001 - recorded, not raised
            outcome["err"] = type(exc).__name__

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    wait_until(
        lambda: (engine.con.query_progress() or -1) > 0,
        timeout=progress_timeout,
        what="query_progress() > 0 (long query in flight)",
    )
    engine.cancel()
    thread.join(timeout=30)
    if thread.is_alive():
        raise AssertionError("long-query worker did not stop after cancel")
    return outcome["err"]


def close_quietly(engine: Engine | None) -> None:
    """Best-effort close for teardown paths; never masks a test failure."""
    if engine is not None:
        try:
            engine.close()
        except Exception:                   # noqa: BLE001 - teardown only
            pass
