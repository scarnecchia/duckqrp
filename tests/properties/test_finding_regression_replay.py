"""AC.9 finding regression replay, wrapped as an opt-in pytest module.

EXPENSIVE by design: every replay group from
investigation/finding-replay-records.json is replayed through
investigation/finding_regression_replay.py — a DISPOSABLE detached git
worktree per group with a FRESH uv-built venv per revision, the red
revision REQUIRED to fail for the recorded reason (every expected
pattern matched, never a dependency/import failure), and every later
revision REQUIRED to pass with zero skips. Minutes per group; no
DuckDB usage outside the disposable worktrees.

Normal suite runs SKIP every test here with an explicit reason — a
skip is never success evidence. Run explicitly, either through the
registered mutation_replay marker (which selects both AC.8 calibration
replays and this AC.9 finding replay; no new marker is registered):

    python -m pytest tests/properties/test_finding_regression_replay.py \
        -m mutation_replay

or through the explicit environment opt-in for this module alone:

    QRP_FINDING_REPLAY=1 python -m pytest \
        tests/properties/test_finding_regression_replay.py

Requires: the `uv` tool on PATH and, for the e1e2 group, the synthetic
input /tmp/qrp_data/100k (tools/gen_synthetic.py recipe) — the replay
refuses to count a skip as a pass, so a missing fixture fails the run.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPLAY_SCRIPT = _REPO_ROOT / "investigation" / "finding_regression_replay.py"
_RECORDS = _REPO_ROOT / "investigation" / "finding-replay-records.json"

# One replay group builds up to three fresh venvs and runs up to three
# pytest invocations; 2400 s per group is a generous ceiling.
_PER_GROUP_TIMEOUT = 2400

pytestmark = [pytest.mark.mutation_replay]


@pytest.fixture(autouse=True)
def _require_explicit_opt_in(request: pytest.FixtureRequest) -> None:
    """Skip unless the replay was explicitly selected. Never silently
    green: the skip reason names the exact opt-in commands."""
    markexpr = str(request.config.getoption("markexpr") or "")
    tokens = re.split(r"[()\s!,&|]", markexpr)
    if "mutation_replay" not in tokens \
            and os.environ.get("QRP_FINDING_REPLAY") != "1":
        pytest.skip(
            "opt-in replay: run with -m mutation_replay (AC.8 + AC.9 "
            "replays) or QRP_FINDING_REPLAY=1 (this module only)")


def _group_ids() -> list[str]:
    """Group ids for parametrization; [] keeps collection alive if the
    records document is momentarily unreadable (the always-on validator
    fails loudly for that case in normal runs)."""
    try:
        raw = json.loads(_RECORDS.read_text(encoding="utf-8"))
        return [str(group["id"]) for group in raw["replay_groups"]]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return []


def _run_replay(group_id: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_REPLAY_SCRIPT), "--group", group_id,
         "--json"],
        capture_output=True, text=True, timeout=_PER_GROUP_TIMEOUT,
        cwd=str(_REPO_ROOT), check=False,
    )


@pytest.mark.parametrize("group_id", _group_ids())
def test_finding_replay_group_proves_red_then_green(
        group_id: str) -> None:
    """One full red/green(/final) replay cycle per group, via the real
    CLI, asserting the verdict the records document claims."""
    proc = _run_replay(group_id)
    combined = (proc.stderr or "")[-4000:]
    assert proc.returncode == 0, (
        f"replay of {group_id} exited {proc.returncode}:\n{combined}")
    try:
        outcomes = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"replay of {group_id} emitted no JSON verdict: {exc}")
    assert len(outcomes) == 1, f"expected one verdict for {group_id}"
    outcome = outcomes[0]
    assert outcome["group_id"] == group_id
    assert outcome["ok"], (
        f"replay of {group_id} did not prove red->green: "
        f"{outcome['steps']}\ntails:\n{outcome['tails']}")
    assert outcome["worktree_removed"] is True, (
        f"replay of {group_id} left its disposable worktree behind")

    revisions = outcome["revisions"]
    assert revisions[0]["role"] == "red"
    for i, revision in enumerate(revisions):
        if i == 0:
            assert revision["exit_code"] != 0, "red must fail"
            assert revision["pattern_matched"] is True, (
                "red must fail for the recorded reason")
        else:
            assert revision["exit_code"] == 0, (
                f"{revision['role']} revision must pass")
        assert revision["ok"] is True
        assert revision["duration_seconds"] > 0
