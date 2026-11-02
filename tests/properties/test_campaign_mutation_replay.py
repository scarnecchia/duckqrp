"""AC.8 mutation replay, wrapped as an opt-in pytest module.

EXPENSIVE by design: every calibration record from
investigation/manifest.json `calibrations[]` is replayed in its own
DISPOSABLE git worktree with a fresh uv-built venv — the recorded diff
is applied, the recorded command is REQUIRED to fail for the recorded
reason, the mutation is reverted, and the command is REQUIRED to pass
again. Minutes per record; no DuckDB usage outside the disposable
worktrees.

Normal suite runs SKIP every test here with an explicit reason. Run
them explicitly:

    python -m pytest tests/properties/test_campaign_mutation_replay.py -m mutation_replay

Selection is honored through the standard -m marker expression; the
autouse guard below only turns "not explicitly selected" into a skip,
so default runs and marker-deselected runs both stay green.

Requires: the `uv` tool on PATH and a campaign worktree on an
`inv/0f462p/*` branch (the replay script refuses anything else).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPLAY_SCRIPT = _REPO_ROOT / "investigation" / "replay_mutation.py"
_MANIFEST = _REPO_ROOT / "investigation" / "manifest.json"

# One replay record may build a venv and run two pytest invocations;
# 1800 s per record is a generous ceiling, not a target.
_PER_RECORD_TIMEOUT = 1800

pytestmark = [pytest.mark.mutation_replay]


@pytest.fixture(autouse=True)
def _require_explicit_marker_selection(request: pytest.FixtureRequest) -> None:
    """Skip unless pytest was invoked with -m ... mutation_replay ..."""
    markexpr = str(request.config.getoption("markexpr") or "")
    tokens = re.split(r"[()\s!,&|]", markexpr)
    if "mutation_replay" not in tokens:
        pytest.skip("run explicitly with -m mutation_replay")


def _calibration_ids() -> list[str]:
    """Record ids for parametrization; [] keeps collection alive if the
    manifest is momentarily unreadable (the manifest validator fails
    loudly for that case in normal runs)."""
    try:
        manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
        return [str(record["id"]) for record in manifest["calibrations"]]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return []


def _run_replay(record_id: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_REPLAY_SCRIPT), "--id", record_id, "--json"],
        capture_output=True, text=True, timeout=_PER_RECORD_TIMEOUT,
        cwd=str(_REPO_ROOT), check=False,
    )


@pytest.mark.parametrize("record_id", _calibration_ids())
def test_calibration_record_replays_red_then_green(record_id: str) -> None:
    """One full replay cycle per calibration record, via the real CLI."""
    proc = _run_replay(record_id)
    combined = (proc.stderr or "")[-4000:]
    assert proc.returncode == 0, (
        f"replay of {record_id} exited {proc.returncode}:\n{combined}")
    try:
        outcomes = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"replay of {record_id} emitted no JSON verdict: {exc}")
    assert len(outcomes) == 1, f"expected one verdict for {record_id}"
    outcome = outcomes[0]
    assert outcome["record_id"] == record_id
    assert outcome["ok"], (
        f"replay of {record_id} did not prove red->green: "
        f"{outcome['steps']}\nred tail:\n{outcome['red_tail']}")
    assert outcome["red_exit_code"] != 0
    assert outcome["red_pattern_matched"] is True
    assert outcome["green_exit_code"] == 0
    assert outcome["worktree_removed"] is True
    step_names = [step["step"] for step in outcome["steps"]]
    assert step_names == ["base", "worktree", "venv", "apply", "red",
                          "reset", "green", "cleanup"]
