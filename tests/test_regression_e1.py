"""Regression: the two shipped frontends must release the run's engine.

Investigation findings E-1 (TUI) and E-2 (CLI): `RunHandle.close()` /
`Engine.close()` exist and are caller-owned, but

* the TUI event pump (qrp/tui.py `_pump`) closed only the RunLog and
  never the RunHandle — every completed run in a TUI session left a
  live DuckDB connection behind, and a session that runs several
  studies accumulated them (each holding that run's in-memory
  database);
* the CLI (qrp/cli.py `main`) closed `eng` only on the success path,
  inside the `try` after `run()`, so any failure after engine
  construction (a failed stage, a missing table) returned exit 1 with
  the engine still open.

Standalone: no campaign infrastructure. The TUI case drives the real
app headlessly through Textual's Pilot (the tests/test_ui.py pattern)
against the same demo study; the CLI case runs the same `main()` a
subprocess would run, in-process, and observes the engine release
through a close-probe subclass of Engine (a subprocess could not
report engine state back).
"""

from __future__ import annotations

import asyncio
import warnings
from pathlib import Path

import duckdb
import pytest

STUDY = Path(__file__).resolve().parents[1] / "study" / "demo_full.json"
SMALL = Path("/tmp/qrp_data/100k")

pytestmark = pytest.mark.skipif(
    not SMALL.exists(), reason="run tools/gen_synthetic.py first"
)


def test_tui_run_closes_the_run_engine():
    """After a TUI run reaches its terminal state, its engine must be
    released: `handle.close()` is the caller's job (runner.py) and the
    TUI is the caller."""
    pytest.importorskip("textual")

    from qrp.tui import QRPApp

    def engine_closed(app) -> bool:
        engine = app.handle.engine
        if engine is None:
            return True
        try:
            engine.con.execute("SELECT 1")
        except duckdb.ConnectionException:
            return True
        return False

    async def scenario():
        from textual.widgets import DataTable

        app = QRPApp(str(STUDY), str(SMALL))
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+r")
            for _ in range(300):
                await pilot.pause()
                await asyncio.sleep(0.2)
                if not app.running and app.query_one(
                    "#results", DataTable
                ).row_count:
                    break
            assert not app.running, "the run never reached its terminal state"
            assert app.query_one("#results", DataTable).row_count > 0, (
                "the run did not complete successfully; the test covers "
                "the completion path, not the failure path"
            )
            # `running=False` is set before the pump releases the engine,
            # so this is deterministic; still wait for the condition with
            # a bounded budget rather than assuming it.
            for _ in range(100):
                if engine_closed(app):
                    break
                await pilot.pause()
                await asyncio.sleep(0.05)
            assert engine_closed(app), (
                "the TUI never released the run's engine after completion"
            )

    asyncio.run(scenario())


def test_cli_failure_closes_the_engine(monkeypatch, tmp_path, capsys):
    """A CLI run that fails after engine construction must still close
    the engine before `main()` returns 1."""
    from qrp import cli
    from qrp.engine import Engine

    closed: list[bool] = []

    class CloseProbeEngine(Engine):
        def close(self) -> None:
            closed.append(True)
            super().close()

    monkeypatch.setattr(cli, "Engine", CloseProbeEngine)

    # An empty SCDM root: engine construction succeeds, the pipeline
    # fails on the missing tables — the mid-pipeline failure shape.
    empty = tmp_path / "scdm"
    empty.mkdir()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rc = cli.main([
            "run", "--study", str(STUDY), "--indata", str(empty),
            "--quiet",
        ])
    capsys.readouterr()

    assert rc == 1, "the CLI must report the failure"
    assert closed == [True], (
        "the CLI closed the engine 0 times on the failure path "
        "(expected exactly once)"
    )
