#!/usr/bin/env python3
# pattern: Imperative Shell
"""AC.8 mutation replay: prove each calibration record red-then-green.

For every record in manifest.json `calibrations[]` (or the `--id`
subset) this script:

1. resolves the record's base revision,
2. creates a DISPOSABLE detached worktree `replay-<id>` next to the
   current worktree (never touching any other worktree),
3. bootstraps a venv in it via uv (warm cache makes this fast),
4. applies the recorded diff with `git apply`,
5. runs the recorded failing command and REQUIRES a failure whose
   output matches the record's expected_failure_pattern,
6. reverts the mutation (`git checkout -- .`) and REQUIRES the same
   command to pass,
7. removes the worktree — on success and on every failure path.

Usage (from any checkout on an `inv/0f462p/*` branch):

    python investigation/replay_mutation.py                 # all records
    python investigation/replay_mutation.py --id b-1 --json # one, JSON out

Exit status: 0 every replayed record proved red->green; 1 some replay
step failed (tails printed); 2 the manifest records themselves are
invalid. Human progress goes to stderr; with --json the machine verdict
goes to stdout. The pytest wrapper is
tests/properties/test_campaign_mutation_replay.py (run it with
`-m mutation_replay`).

The replay never edits the worktree it runs from: the mutation exists
only inside the disposable replay worktree, which is removed in a
finally block.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_core  # noqa: E402  (script-local sibling module)

# Per-step subprocess budgets. The venv bootstrap dominates; the uv
# cache usually keeps it well under a minute.
GIT_TIMEOUT = 120
VENV_TIMEOUT = 600
PYTEST_TIMEOUT = 600
DEFAULT_TAIL_LINES = 20

# Environment guard (defense-in-depth layer 3): the campaign replay is
# only ever driven from a campaign worktree, never from the protected
# branches the ledger rules put off-limits.
CAMPAIGN_BRANCH_PREFIX = "inv/0f462p/"


def run_cmd(argv: list[str], cwd: str, timeout: int,
            ) -> tuple[int, str, str]:
    """Run one subprocess; never raises, returns (code, out, err)."""
    try:
        proc = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial_out = exc.stdout if isinstance(exc.stdout, str) else ""
        partial_err = exc.stderr if isinstance(exc.stderr, str) else ""
        return 124, partial_out, f"timed out after {timeout}s: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def log(message: str) -> None:
    """Human progress goes to stderr; stdout is reserved for --json."""
    print(message, file=sys.stderr, flush=True)


def current_branch(repo_root: str) -> tuple[str, str]:
    """(branch, error); error non-empty when it cannot be determined."""
    code, out, err = run_cmd(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_root, GIT_TIMEOUT)
    if code != 0:
        return "", f"cannot determine current branch: {err.strip()}"
    return out.strip(), ""


def guard_environment(repo_root: str) -> str:
    """Refuse to run outside a campaign worktree (layer 3 guard)."""
    branch, error = current_branch(repo_root)
    if error:
        return error
    if not branch.startswith(CAMPAIGN_BRANCH_PREFIX):
        return (f"refusing to replay from branch {branch!r}: campaign "
                f"replay runs only on {CAMPAIGN_BRANCH_PREFIX}* worktrees "
                "(the main checkout and protected branches are off-limits)")
    return ""


def resolve_revision(repo_root: str, revision: str,
                     ) -> tuple[bool, str]:
    code, _, err = run_cmd(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        repo_root, GIT_TIMEOUT)
    if code != 0:
        return False, f"base revision {revision!r} does not resolve: {err.strip()}"
    return True, ""


def create_worktree(repo_root: str, worktree: str, revision: str,
                    ) -> tuple[bool, str]:
    code, _, err = run_cmd(
        ["git", "worktree", "add", "--detach", worktree, revision],
        repo_root, GIT_TIMEOUT)
    if code != 0:
        return False, f"git worktree add failed: {err.strip()}"
    return True, ""


def remove_worktree(repo_root: str, worktree: str) -> tuple[bool, str]:
    code, _, err = run_cmd(
        ["git", "worktree", "remove", "--force", worktree],
        repo_root, GIT_TIMEOUT)
    if code != 0:
        return False, (f"git worktree remove --force failed: {err.strip()}; "
                       f"inspect and remove {worktree} by hand")
    return True, ""


def bootstrap_venv(worktree: str) -> tuple[bool, str]:
    code, out, err = run_cmd(
        ["uv", "venv", ".venv", "--python", "3.13"], worktree, VENV_TIMEOUT)
    if code != 0:
        return False, f"uv venv failed: {(out + err).strip()[-500:]}"
    code, out, err = run_cmd(
        ["uv", "pip", "install", "--python", ".venv/bin/python",
         "-e", ".[dev]"],
        worktree, VENV_TIMEOUT)
    if code != 0:
        return False, f"uv pip install failed: {(out + err).strip()[-500:]}"
    return True, ""


def apply_diff(worktree: str, diff_file: str) -> tuple[bool, str]:
    code, _, err = run_cmd(
        ["git", "apply", "--whitespace=nowarn", diff_file],
        worktree, GIT_TIMEOUT)
    if code != 0:
        return False, f"git apply failed: {err.strip()}"
    return True, ""


def reset_mutation(worktree: str) -> tuple[bool, str]:
    code, _, err = run_cmd(
        ["git", "checkout", "--", "."], worktree, GIT_TIMEOUT)
    if code != 0:
        return False, f"git checkout -- . failed: {err.strip()}"
    return True, ""


def run_failing_command(plan: replay_core.ReplayPlan, timeout: int,
                        ) -> tuple[int, str]:
    code, out, err = run_cmd(
        list(plan.command), plan.worktree, timeout)
    return code, out + err


def replay_record(plan: replay_core.ReplayPlan, pytest_timeout: int,
                  tail_lines: int) -> replay_core.ReplayOutcome:
    """One full disposable-worktree red/green cycle. Always cleans up."""
    started = time.perf_counter()
    record = plan.record
    steps: list[replay_core.StepVerdict] = []
    red_exit: int | None = None
    green_exit: int | None = None
    red_matched = False
    red_output = ""
    green_output = ""
    worktree_exists = False

    log(f"[{record.id}] package {record.package}: base {record.base_revision}"
        f" diff {record.diff_path} ({record.provenance})")

    ok, detail = resolve_revision(plan.repo_root, record.base_revision)
    steps.append(replay_core.StepVerdict("base", ok, detail))
    if ok:
        if Path(plan.worktree).exists():
            ok = False
            detail = (f"{plan.worktree} already exists; a previous replay "
                      "left it behind — inspect and remove it by hand "
                      "before re-running")
        else:
            ok, detail = create_worktree(
                plan.repo_root, plan.worktree, record.base_revision)
        steps.append(replay_core.StepVerdict("worktree", ok, detail))
        worktree_exists = ok

    try:
        if ok:
            ok, detail = bootstrap_venv(plan.worktree)
            steps.append(replay_core.StepVerdict("venv", ok, detail))
        if ok:
            ok, detail = apply_diff(plan.worktree, plan.diff_file)
            steps.append(replay_core.StepVerdict("apply", ok, detail))
        if ok:
            red_exit, red_output = run_failing_command(plan, pytest_timeout)
            ok, detail = replay_core.red_failed_as_expected(
                red_exit, red_output, record.expected_failure_pattern)
            red_matched = ok
            steps.append(replay_core.StepVerdict("red", ok, detail))
        if ok:
            ok, detail = reset_mutation(plan.worktree)
            steps.append(replay_core.StepVerdict("reset", ok, detail))
        if ok:
            green_exit, green_output = run_failing_command(plan, pytest_timeout)
            ok, detail = replay_core.green_recovered(green_exit)
            steps.append(replay_core.StepVerdict("green", ok, detail))
    finally:
        if worktree_exists:
            removed, detail = remove_worktree(plan.repo_root, plan.worktree)
            steps.append(replay_core.StepVerdict("cleanup", removed, detail))

    elapsed = time.perf_counter() - started
    outcome = replay_core.combine_verdicts(
        record.id, steps, red_exit, green_exit, red_matched,
        red_output, green_output, tail_lines)
    if outcome.ok:
        log(f"[{record.id}] RED (exit {red_exit}, pattern matched) -> "
            f"GREEN (exit {green_exit}) -> worktree removed  "
            f"({elapsed:.1f}s)  OK")
    else:
        failed = [s for s in outcome.steps if not s.ok]
        log(f"[{record.id}] FAILED after {elapsed:.1f}s at step(s): "
            + ", ".join(f"{s.step} ({s.detail})" for s in failed))
        if outcome.red_tail:
            log(f"[{record.id}] red tail:\n{outcome.red_tail}")
        if outcome.green_tail:
            log(f"[{record.id}] green tail:\n{outcome.green_tail}")
    return outcome


def load_manifest(manifest_path: Path) -> tuple[dict, str]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {}, f"cannot read {manifest_path}: {exc}"
    except json.JSONDecodeError as exc:
        return {}, f"{manifest_path} is not valid JSON: {exc}"
    if not isinstance(manifest.get("calibrations"), list):
        return {}, f"{manifest_path} has no calibrations[] array"
    return manifest, ""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AC.8 mutation replay for campaign calibration records")
    parser.add_argument(
        "--manifest", type=Path, default=None,
        help="path to manifest.json (default: investigation/manifest.json "
             "next to this script)")
    parser.add_argument(
        "--id", action="append", default=None, metavar="RECORD_ID",
        help="replay only this calibration record (repeatable; default: all)")
    parser.add_argument(
        "--json", action="store_true",
        help="emit machine verdicts as a JSON array on stdout")
    parser.add_argument(
        "--tail-lines", type=int, default=DEFAULT_TAIL_LINES,
        help="lines of red/green output kept per record (default "
             f"{DEFAULT_TAIL_LINES})")
    parser.add_argument(
        "--pytest-timeout", type=int, default=PYTEST_TIMEOUT,
        help=f"per pytest invocation budget in seconds (default "
             f"{PYTEST_TIMEOUT})")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    script_dir = Path(__file__).resolve().parent
    manifest_path = (args.manifest
                     if args.manifest is not None
                     else script_dir / "manifest.json")
    repo_root = str(script_dir.parent)

    manifest, error = load_manifest(manifest_path)
    if error:
        log(error)
        return 2

    guard_error = guard_environment(repo_root)
    if guard_error:
        log(guard_error)
        return 2

    records_raw = manifest["calibrations"]
    selected = args.id
    if selected:
        known = {r.get("id") for r in records_raw if isinstance(r, dict)}
        missing = [i for i in selected if i not in known]
        if missing:
            log(f"no such calibration id(s) in {manifest_path}: {missing}")
            return 2
        records_raw = [r for r in records_raw
                       if isinstance(r, dict) and r.get("id") in selected]
    if not records_raw:
        log("no calibration records to replay")
        return 0

    # Validate every record before creating anything (fail fast).
    worktree_root = str(Path(repo_root).parent)
    plans: list[replay_core.ReplayPlan] = []
    invalid = False
    for raw in records_raw:
        record, errors = replay_core.parse_record(raw)
        if record is None:
            invalid = True
            log(f"invalid calibration record {raw.get('id', raw)!r}:")
            for e in errors:
                log(f"  - {e}")
            continue
        plan, plan_errors = replay_core.build_plan(
            record, repo_root, worktree_root)
        if plan is None:
            invalid = True
            log(f"unusable calibration record {record.id!r}:")
            for e in plan_errors:
                log(f"  - {e}")
            continue
        if not Path(plan.diff_file).is_file():
            invalid = True
            log(f"invalid calibration record {record.id!r}:")
            log(f"  - diff_path {record.diff_path!r} does not exist")
            continue
        plans.append(plan)
    if invalid:
        log("manifest calibrations[] failed validation; nothing replayed")
        return 2

    outcomes = [replay_record(plan, args.pytest_timeout, args.tail_lines)
                for plan in plans]

    if args.json:
        print(json.dumps([o.as_dict() for o in outcomes], indent=2))
    failed = [o.record_id for o in outcomes if not o.ok]
    if failed:
        log(f"replay FAILED for: {failed}")
        return 1
    log(f"replay OK for {len(outcomes)} record(s): "
        f"{[o.record_id for o in outcomes]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
