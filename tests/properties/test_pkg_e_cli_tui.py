"""AC.6 package E — CLI exit status and TUI terminal outcomes.

Correctness work: assert WHAT the UI reports (exit codes, terminal
labels, error text), not how it looks. CLI scenarios run the real
`.venv/bin/python -m qrp` in subprocesses; TUI scenarios drive the real
QRPApp headlessly through Textual's Pilot — the pattern tests/test_ui.py
established.

The study fixture is demo_type2.json trimmed to the two codes the hand
fixture actually supplies (E00001 / E00021), so a successful CLI/TUI run
produces real outputs; note (from package C's calibration) that these
runs will warn about empty event-derived results but MUST still succeed
and report honestly.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import warnings
from pathlib import Path

import pytest

from .oracles import e_lifecycle_model as model
from .synth import hand_fixture, write_parquet

REPO = Path(__file__).resolve().parents[2]
STUDY_DIR = REPO / "study"
PY = str(REPO / ".venv" / "bin" / "python")
SMALL = Path("/tmp/qrp_data/100k")


@pytest.fixture(scope="module")
def cli_env(tmp_path_factory) -> "dict[str, Path]":
    """A trimmed real study JSON plus a hand_fixture parquet root."""
    raw = json.loads((STUDY_DIR / "demo_type2.json").read_text())
    raw["cohortcodes"] = [
        c for c in raw["cohortcodes"] if c["code"] in ("E00001", "E00021")]
    study_json = tmp_path_factory.mktemp("pkg-e-cli") / "tiny_study.json"
    study_json.write_text(json.dumps(raw))
    dataset, _ = hand_fixture()
    root = write_parquet(dataset, tmp_path_factory.mktemp("pkg-e-cli") / "indata")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return {"study": study_json, "indata": root}


def _run_cli(*args: str, timeout: float = 180.0) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    return subprocess.run(
        [PY, "-m", "qrp", *args],
        capture_output=True, text=True, timeout=timeout, cwd=str(REPO), env=env,
    )


# ---------------------------------------------------------------------
# CLI exit status and diagnostics
# ---------------------------------------------------------------------


def test_cli_failure_exit_status(cli_env, tmp_path) -> None:
    """Failure vs success vs cancelled must be distinguishable from the
    outside: exit status plus an actionable diagnostic [errors.py: the
    runbook's actionable-errors promise holds on the CLI too]."""
    out = tmp_path / "out"
    ok = _run_cli("run", "--study", str(cli_env["study"]),
                  "--indata", str(cli_env["indata"]),
                  "--out", str(out), "--threads", "1")
    assert ok.returncode == 0, ok.stderr[-2000:]
    assert "outputs ->" in ok.stdout, (
        "contract [CLI]: a successful run names where outputs went")
    model.read_manifest(out)

    # missing study file: exit 1 with the explain()ed failure on stderr
    bad = _run_cli("run", "--study", str(tmp_path / "nope.json"),
                   "--indata", str(cli_env["indata"]),
                   "--out", str(tmp_path / "out2"))
    assert bad.returncode == 1, bad.stderr[-2000:]
    assert "Failed:" in bad.stderr, (
        "contract [CLI]: failures are announced with the actionable "
        "diagnostic, not a traceback")

    # indata that cannot satisfy the SCDM: exit 1, actionable text
    empty_root = tmp_path / "empty_indata"
    empty_root.mkdir()
    missing = _run_cli("run", "--study", str(cli_env["study"]),
                       "--indata", str(empty_root),
                       "--out", str(tmp_path / "out3"))
    assert missing.returncode == 1, missing.stderr[-2000:]
    assert ("Could not identify" in missing.stderr
            or "table-map" in missing.stderr or "inspect" in missing.stderr), (
        "contract [errors.py]: the missing-table failure names the fix")

    # a misspelt --table-map name fails fast with a suggestion
    typo = _run_cli("run", "--study", str(cli_env["study"]),
                    "--indata", str(cli_env["indata"]),
                    "--out", str(tmp_path / "out4"),
                    "--table-map", "dispensng=x")
    assert typo.returncode != 0, (
        "contract [CLI/tui parity]: a misspelt table name must fail fast")
    assert "dispensng" in (typo.stdout + typo.stderr)

    # validate: cheap identity checks mirror the exit contract
    v_ok = _run_cli("validate", "--study", str(cli_env["study"]))
    assert v_ok.returncode == 0
    v_bad = _run_cli("validate", "--study", str(tmp_path / "nope.json"))
    assert v_bad.returncode == 1


@pytest.mark.skipif(not SMALL.exists(),
                    reason="run tools/gen_synthetic.py first")
def test_cli_sigint_mid_run_is_characterized(cli_env, tmp_path) -> None:
    """SIGINT mid-run: the documented CLI contract is silent about
    KeyboardInterrupt (`except Exception` does not catch it). This test
    CHARACTERIZES the behaviour for the ledger and asserts only the
    safe direction: the process terminates nonzero and never leaves a
    manifest claiming success. See ledger OQ-3."""
    out = tmp_path / "out"
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [PY, "-m", "qrp", "run", "--study", str(STUDY_DIR / "demo_full.json"),
         "--indata", str(SMALL), "--out", str(out), "--threads", "1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(REPO),
        env=env,
    )
    saw_stage_line = False
    deadline = 60.0
    import time
    t0 = time.monotonic()
    assert proc.stdout is not None
    while time.monotonic() - t0 < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if "normalize" in line and "s]" in line:
            saw_stage_line = True
            break
    assert saw_stage_line, (
        "the 100k run never printed a stage line; the SIGINT scenario "
        "cannot be exercised")
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise AssertionError("SIGINT did not terminate the CLI run")
    assert proc.returncode != 0, (
        "a SIGINTed run must not report success")
    assert not (out / "manifest.json").exists(), (
        "contract [OUT]: no manifest may survive an interrupted run")


# ---------------------------------------------------------------------
# TUI terminal outcomes
# ---------------------------------------------------------------------

textual = pytest.importorskip("textual")


def _log_text(app) -> str:
    from textual.widgets import RichLog

    return "\n".join(str(line.text) if hasattr(line, "text") else str(line)
                     for line in app.query_one("#log", RichLog).lines)


def _label(app) -> str:
    from textual.widgets import Static

    return str(app.query_one("#stage_label", Static).content)


def _redirect_tui_log(app, tmp_path) -> None:
    """The Log dir field defaults to 'logs'; point it into the test's
    tmp tree so TUI runs never write into the repository."""
    from textual.widgets import Input

    app.query_one("#logdir", Input).value = str(tmp_path / "tui-logs")


def test_tui_terminal_outcomes(cli_env, tmp_path) -> None:
    """Visible terminal states: success reports done; failure reports
    failed with the diagnostic; cancellation reports cancelled."""
    import asyncio

    from textual.widgets import DataTable

    from qrp.tui import QRPApp

    async def scenario() -> "tuple[str, str, int]":
        app = QRPApp(str(cli_env["study"]), str(cli_env["indata"]))
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            _redirect_tui_log(app, tmp_path)
            await pilot.press("ctrl+r")
            for _ in range(300):
                await pilot.pause()
                await asyncio.sleep(0.2)
                if not app.running and app.query_one(
                        "#stages", DataTable).row_count:
                    break
            assert not app.running, "success run never went idle"
            assert _label(app).startswith("done"), (
                "contract [tui.py _apply]: success updates the terminal "
                f"label to 'done…'; got {_label(app)!r}")
            done_label = _label(app)
            results = app.query_one("#results", DataTable).row_count
            assert results > 0, (
                "contract [tui.py]: RunFinished(ok) fills the Results tab")
        return done_label, "", results

    done_label, _, results = asyncio.run(scenario())
    assert "done" in done_label and results > 0


def test_tui_failure_reports_failed(cli_env, tmp_path) -> None:
    import asyncio

    from qrp.tui import QRPApp

    empty = tmp_path / "empty_indata"
    empty.mkdir()

    async def scenario() -> "tuple[str, str]":
        app = QRPApp(str(cli_env["study"]), str(empty))
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            _redirect_tui_log(app, tmp_path)
            await pilot.press("ctrl+r")
            for _ in range(300):
                await pilot.pause()
                await asyncio.sleep(0.2)
                if not app.running:
                    break
            assert not app.running
            label = _label(app)
            log = _log_text(app)
        return label, log

    label, log = asyncio.run(scenario())
    assert label == "failed", (
        "contract [tui.py _apply]: RunFinished(ok=False) must label the "
        f"terminal 'failed'; got {label!r}")
    assert "failed:" in log, (
        "contract [RUN]: the failure diagnostic reaches the visible log")


@pytest.mark.skipif(not SMALL.exists(),
                    reason="run tools/gen_synthetic.py first")
def test_tui_cancelled_reports_cancelled(tmp_path) -> None:
    """Escape cancels a run and the terminal label says 'cancelled'
    [tui.py _apply RunFinished(cancelled) branch; UI.md 'Escape
    cancels'].

    Deterministic barrier: the two key presses are processed in order on
    the event loop, so escape lands while the run is STARTING — before
    the worker builds its Engine. runner.py documents this exact window
    as honoured ('a fast click must not be lost') and ARCHITECTURE.md
    pins its outcome (0 stages, cancelled=True). A 100k demo run
    completes in ~1.6s here, so a poll-timed mid-query escape cannot be
    made deterministic; the mid-query interrupt mechanism is proven at
    engine level (test_cancel_during_in_flight_query) and RunHandle
    level (tests/test_ui.py, repeated-cancel test)."""
    import asyncio

    from qrp.tui import QRPApp

    async def scenario() -> "tuple[str, str]":
        app = QRPApp(str(STUDY_DIR / "demo_full.json"), str(SMALL))
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            _redirect_tui_log(app, tmp_path)
            # Direct action calls (the tests/test_ui.py pattern) are the
            # deterministic barrier here: pilot.press waits for the
            # message queue to idle, which would let the whole 1.6s run
            # finish before escape could land. Calling action_cancel()
            # immediately after action_run() cancels BEFORE the worker
            # builds its Engine — the window runner.py documents as
            # honoured ('a fast click must not be lost').
            app.action_run()
            app.action_cancel()
            for _ in range(300):
                await pilot.pause()
                await asyncio.sleep(0.2)
                if not app.running:
                    break
            assert not app.running, "run never settled after cancel"
            label = _label(app)
            log = _log_text(app)
        return label, log

    label, log = asyncio.run(scenario())
    assert label == "cancelled", (
        "contract [tui.py]: a cancelled run must label the terminal "
        f"'cancelled'; got {label!r}")
    assert "cancelled after" in log, (
        "contract [tui.py]: the cancelled outcome reaches the log")
    assert "cancelling" in log, (
        "contract [tui.py]: the escape ack reaches the log")


def _study_json(tmp_path: Path) -> Path:
    raw = json.loads((STUDY_DIR / "demo_type2.json").read_text())
    raw["cohortcodes"] = [
        c for c in raw["cohortcodes"] if c["code"] in ("E00001", "E00021")]
    path = tmp_path / "tiny_study.json"
    path.write_text(json.dumps(raw))
    return path
