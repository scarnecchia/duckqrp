# pattern: Imperative Shell
"""Pass2 frontend release checks use fresh synthetic input and real DuckDB."""
import asyncio
from pathlib import Path

import duckdb
import pytest
from hypothesis import event, example, given, strategies as st
from textual.widgets import Input, Select, Static

from qrp import cli, runner, tui
from qrp.engine import Engine
from qrp.events import Event, EventSink, RunFinished, StageFinished, StageStarted, multi_sink
from .synth import hand_fixture, write_parquet


def assert_closed(engine: Engine) -> None:
    with pytest.raises(duckdb.ConnectionException):
        engine.con.execute("SELECT 1").fetchall()


@pytest.mark.parametrize("outcome", ["success", "error", "cancelled"])
def test_pass2_tui_cleanup(outcome: str, tmp_path: Path,
                           monkeypatch: pytest.MonkeyPatch) -> None:
    dataset, study = hand_fixture()
    root = write_parquet(dataset, tmp_path / "input")
    engines: list[Engine] = []
    closes: list[Engine] = []
    observed: list[Event] = []

    class TrackedEngine(Engine):
        def __post_init__(self) -> None:
            super().__post_init__()
            engines.append(self)

        def close(self) -> None:
            closes.append(self)
            super().close()

    class PreparedHandle(runner.RunHandle):
        extra_sink: EventSink | None

        def start(self) -> runner.RunHandle:
            self.extra_sink = multi_sink(self.extra_sink, observed.append)
            if outcome == "cancelled":
                self.cancel()
                self.cancel()
            return super().start()

    monkeypatch.setattr(runner, "Engine", TrackedEngine)
    monkeypatch.setattr(tui, "RunHandle", PreparedHandle)
    monkeypatch.setattr(tui, "load_study", lambda _: study)
    if outcome == "error":
        root = tmp_path / "empty"
        root.mkdir()

    async def scenario() -> None:
        app = tui.QRPApp("synthetic.json", str(root))
        async with app.run_test(size=(120, 45)):
            app.query_one("#threads", Select).value = "1"
            app.query_one("#logdir", Input).value = str(tmp_path / "logs")
            app.action_run()
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=30)
            assert not app.running
            label = str(app.query_one("#stage_label", Static).content)
            expected = {"success": "done in", "error": "failed",
                        "cancelled": "cancelled"}[outcome]
            assert label.startswith(expected)
            assert len(engines) == len(closes) == 1
            assert closes[0] is engines[0]
            assert_closed(engines[0])
            terminal = [e for e in observed if isinstance(e, RunFinished)]
            assert len(terminal) == 1
            assert terminal[0].ok == (outcome == "success")
            assert terminal[0].cancelled == (outcome == "cancelled")
            if outcome == "cancelled":
                assert not any(isinstance(e, (StageStarted, StageFinished))
                               for e in observed)

    asyncio.run(scenario())


def test_pass2_cli_failure_then_new_run(tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    dataset, study = hand_fixture()
    root = write_parquet(dataset, tmp_path / "input")
    empty = tmp_path / "empty"
    empty.mkdir()
    engines: list[Engine] = []
    closes: list[Engine] = []

    class TrackedEngine(Engine):
        def __post_init__(self) -> None:
            super().__post_init__()
            engines.append(self)

        def close(self) -> None:
            closes.append(self)
            super().close()

    monkeypatch.setattr(cli, "Engine", TrackedEngine)
    monkeypatch.setattr(cli, "load_study", lambda _: study)
    for index, (source, expected) in enumerate(((empty, 1), (root, 0)), 1):
        assert cli.main(["run", "--study", "synthetic.json", "--indata",
                         str(source), "--threads", "1", "--quiet"]) == expected
        assert len(engines) == len(closes) == index
        assert closes[-1] is engines[-1]
        assert_closed(engines[-1])
    assert engines[0] is not engines[1]


@given(st.lists(st.integers(min_value=0, max_value=20), min_size=1, max_size=24),
       st.integers(min_value=1, max_value=24), st.booleans())
@example([1, 2, 3], 2, True)
def test_pass2_logging_fault_operations(values: list[int], fail_at: int,
                                        persistent: bool) -> None:
    """Production fan-out must preserve the healthy subscriber's entire stream."""
    received: list[Event] = []
    calls = 0
    failures = 0

    def faulty(ev: Event) -> None:
        nonlocal calls, failures
        calls += 1
        if calls == fail_at or (persistent and calls >= fail_at):
            failures += 1
            raise OSError("injected log write failure")

    sink = multi_sink(faulty, received.append)
    sent = [StageFinished(index=n) for n in values]
    for ev in sent:
        sink(ev)
    assert len(received) == len(sent)
    assert all(actual is expected for actual, expected in zip(received, sent))
    assert calls == len(values)
    assert failures == sum(i == fail_at or (persistent and i >= fail_at)
                           for i in range(1, len(values) + 1))
    event(f"logging={'persistent' if persistent else 'single'}")
    event(f"fault-hit={failures > 0}")


@given(st.integers(1, 6), st.sampled_from(["ok", "fault", "cancel"]),
       st.integers(0, 5), st.integers(1, 3), st.booleans())
@example(3, "fault", 1, 1, True)
@example(3, "cancel", 0, 3, True)
def test_pass2_bounded_handle_histories(stages: int, outcome: str, point: int,
                                       repeats: int, consume_first: bool) -> None:
    """A legal one-shot start/consume/close history, maximum six stage steps.

    The adapter only supplies stages and failures; production RunHandle owns
    cancellation propagation, failure classification, queue termination and close.
    Expected prefixes and terminal flags are computed from the input schedule.
    """
    from qrp.engine import CancelledRun
    from qrp.events import RunStarted

    point %= stages
    study = hand_fixture()[1]
    allocated: list[object] = []
    released: list[object] = []
    executed: list[int] = []

    class Adapter:
        def __init__(self, **kwargs: object) -> None:
            self.sink = kwargs["on_event"]
            self.cancelled = False
            allocated.append(self)

        def cancel(self) -> None:
            self.cancelled = True

        def close(self) -> None:
            released.append(self)

    def run_adapter(*args: object, **kwargs: object) -> None:
        adapter = kwargs["engine"]
        assert isinstance(adapter, Adapter)
        assert callable(adapter.sink)
        adapter.sink(RunStarted(stages=tuple(str(i) for i in range(stages))))
        for i in range(stages):
            if outcome == "cancel" and i == point:
                for _ in range(repeats):
                    handle.cancel()
            if adapter.cancelled:
                raise CancelledRun("run cancelled")
            if outcome == "fault" and i == point:
                raise OSError("injected stage failure")
            executed.append(i)
            adapter.sink(StageStarted(name=str(i)))
            adapter.sink(StageFinished(name=str(i)))
        adapter.sink(RunFinished(ok=True))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner, "Engine", Adapter)
        patch.setattr(runner, "run", run_adapter)
        handle = runner.RunHandle(study, "unused", threads=1)
        assert not handle.running
        handle.start()
        assert handle.wait(timeout=5)
        observed = (list(handle.events(timeout=1)) if consume_first
                    else handle.drain())
        assert not handle.running
        assert not released  # RunHandle leaves release to its caller.
        handle.close()
    assert len(allocated) == len(released) == 1
    assert allocated[0] is released[0]
    expected = list(range(stages if outcome == "ok" else point))
    assert executed == expected
    assert [e.name for e in observed if isinstance(e, StageStarted)] == [
        str(i) for i in expected]
    terminal = [e for e in observed if isinstance(e, RunFinished)]
    assert len(terminal) == 1 and observed[-1] is terminal[0]
    assert terminal[0].ok == (outcome == "ok")
    assert terminal[0].cancelled == (outcome == "cancel")
    assert bool(terminal[0].error) == (outcome == "fault")
    event(f"terminal={outcome}")
    event(f"consume={'events' if consume_first else 'drain'}")
    event(f"stage-prefix={len(expected)}")


@pytest.mark.parametrize("method", ["write_parquet", "write_csv"])
@pytest.mark.parametrize("after_write", [False, True])
def test_pass2_partial_write_variants(method: str, after_write: bool,
                                      tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    dataset, study = hand_fixture()
    root = write_parquet(dataset, tmp_path / "input")
    output = tmp_path / "output"
    calls = 0
    original = getattr(Engine, method)

    def faulty(self: Engine, *args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2 and not after_write:
            raise OSError("injected output failure before write")
        original(self, *args, **kwargs)
        if calls == 2:
            raise OSError("injected output failure after write")

    monkeypatch.setattr(Engine, method, faulty)
    handle = runner.RunHandle(study, root, threads=1, output_dir=output,
                              csv=True).start()
    try:
        assert handle.wait(timeout=30)
        observed = handle.drain()
        terminal = [e for e in observed if isinstance(e, RunFinished)]
        assert len(terminal) == 1 and not terminal[0].ok
        assert not terminal[0].cancelled and "injected" in terminal[0].error
        assert calls == 2
        assert not (output / "manifest.json").exists()
        assert any(output.rglob("*.parquet"))
    finally:
        handle.close()
    assert handle.engine is not None
    assert_closed(handle.engine)
