"""Package F — tools/compare_runs.py, the run-log comparator.

Cheap pure properties over fabricated .jsonl logs: the parser's
header/stage/finished extraction, its tolerance of malformed lines,
last-writer-wins for repeated stage names, and the CLI smoke path.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load() -> Any:
    spec = importlib.util.spec_from_file_location(
        "f_oracle_compare_runs", _REPO_ROOT / "tools" / "compare_runs.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("f_oracle_compare_runs", module)
    spec.loader.exec_module(module)
    return module


compare_runs = _load()


def _write_log(path: Path, events: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(e) + "\n" for e in events))


def test_load_extracts_header_stages_and_finished(tmp_path: Path) -> None:
    log = tmp_path / "run1.jsonl"
    _write_log(log, [
        {"type": "Header", "run_id": "doctor", "indata": "/x"},
        {"type": "StageStarted", "name": "normalize"},
        {"type": "StageFinished", "name": "normalize", "seconds": 1.5},
        {"type": "StageFinished", "name": "exposure", "seconds": 0.5},
        {"type": "RunFinished", "ok": True, "seconds": 2.0,
         "peak_memory_bytes": 123_000_000, "peak_spill_bytes": 0},
    ])
    loaded = compare_runs.load(log)
    assert loaded["header"]["run_id"] == "doctor"
    assert loaded["stages"] == {"normalize": 1.5, "exposure": 0.5}
    assert loaded["finished"]["ok"] is True
    assert loaded["finished"]["seconds"] == 2.0


def test_load_last_stage_finish_wins(tmp_path: Path) -> None:
    """A stage that restarts (retry after failure) reports its LAST
    duration — the dict is overwritten in file order."""
    log = tmp_path / "run1.jsonl"
    _write_log(log, [
        {"type": "StageFinished", "name": "normalize", "seconds": 9.0},
        {"type": "StageFinished", "name": "normalize", "seconds": 1.0},
    ])
    assert compare_runs.load(log)["stages"]["normalize"] == 1.0


def test_load_skips_malformed_lines(tmp_path: Path) -> None:
    """A truncated or corrupt line must not break the parse of the
    lines around it — logs are appended under crashes."""
    log = tmp_path / "run1.jsonl"
    log.write_text(
        "\n".join([
            json.dumps({"type": "Header", "run_id": "r"}),
            '{"type": "StageFinished", "name": "a", "seconds": 1.0',
            "not json at all {{{",
            "",
            json.dumps({"type": "StageFinished", "name": "b",
                        "seconds": 2.0}),
        ]) + "\n")
    loaded = compare_runs.load(log)
    assert loaded["header"]["run_id"] == "r"
    # The malformed line carries NO data: its stage simply never
    # happened as far as the log is concerned.
    assert loaded["stages"] == {"b": 2.0}


def _finished(ok: bool = True) -> dict[str, Any]:
    return {"type": "RunFinished", "ok": ok, "seconds": 3.0,
            "peak_memory_bytes": 50_000_000, "peak_spill_bytes": 1024}


def test_main_smoke_over_fabricated_logs(tmp_path: Path) -> None:
    """The CLI path renders a stage table across runs and an outcome
    line per run; exit is quiet (None) on success."""
    for i, ok in enumerate((True, True, False)):
        _write_log(tmp_path / f"run{i}.jsonl", [
            {"type": "Header", "run_id": f"r{i}"},
            {"type": "StageFinished", "name": "normalize",
             "seconds": 1.0 + i},
            {"type": "StageFinished", "name": "exposure", "seconds": 2.0},
            _finished(ok),
        ])
    out = io.StringIO()
    saved = sys.argv
    try:
        sys.argv = ["compare_runs.py", str(tmp_path)]
        with redirect_stdout(out):
            result = compare_runs.main()
    finally:
        sys.argv = saved
    text = out.getvalue()
    assert result is None                       # no exit code, success
    for name in ("normalize", "exposure", "TOTAL", "outcome"):
        assert name in text, text
    assert "ok" in text and "FAILED" in text, text


def test_main_last_bounds_the_number_of_runs(tmp_path: Path) -> None:
    for i in range(5):
        _write_log(tmp_path / f"run{i}.jsonl", [
            {"type": "StageFinished", "name": "normalize",
             "seconds": float(i)},
            _finished()])
    out = io.StringIO()
    saved = sys.argv
    try:
        sys.argv = ["compare_runs.py", str(tmp_path), "--last", "2"]
        with redirect_stdout(out):
            compare_runs.main()
    finally:
        sys.argv = saved
    # The two newest runs (run3, run4) carry the table columns.
    text = out.getvalue()
    for stem in ("run3", "run4"):
        assert stem[-15:] in text, f"{stem} missing from:\n{text}"


def test_main_says_when_there_are_no_logs(tmp_path: Path) -> None:
    out = io.StringIO()
    saved = sys.argv
    try:
        sys.argv = ["compare_runs.py", str(tmp_path)]
        with redirect_stdout(out):
            compare_runs.main()
    finally:
        sys.argv = saved
    assert "no .jsonl logs" in out.getvalue()
