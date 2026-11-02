#!/usr/bin/env python3
# pattern: Imperative Shell
"""AC.9 finding regression replay: prove each fixed finding red->green.

For every replay group in investigation/finding-replay-records.json
`replay_groups[]` (or the `--group` subset) this script:

1. resolves every recorded revision and verifies the recorded ancestry
   (base -> red -> green -> [final]) with git, before creating anything,
2. creates a DISPOSABLE detached worktree `fr-<group>` next to the
   current worktree (the only filesystem thing it ever creates; it
   never touches branches, stashes, or other worktrees),
3. bootstraps a fresh venv in it via uv (`.[dev]` — the E-1/E-2
   regression needs textual; nothing in these regressions needs
   Hypothesis, but the dev extra is the campaign's documented dev
   environment and the uv cache keeps this fast),
4. runs the red revision's command and REQUIRES a failure whose pytest
   summary shows real test failures and whose output matches every
   recorded expected_pattern — a dependency/import/collection error or
   a skip is refused as the red reason,
5. checks out each later recorded revision (green, then final where
   the branch carries a follow-up commit), rebuilds the venv from
   scratch for a fresh isolated environment per revision, and REQUIRES
   the command to pass with at least one real pass and zero skips,
6. removes the worktree — on success and on every failure path, and
   only the worktree this run created.

Unlike investigation/replay_mutation.py there is NO campaign-branch
guard: this tool only ever writes its own uniquely-named disposable
worktrees, so it may run from any checkout, including discovery.
Existing paths collide hard (the group fails) instead of being reused.

Usage (from any checkout):

    python investigation/finding_regression_replay.py             # all
    python investigation/finding_regression_replay.py --group a1 --json

Exit status: 0 every replayed group proved red->green; 1 some replay
step failed (tails printed); 2 the records document is invalid. Human
progress goes to stderr; with --json the machine verdict goes to
stdout. Per-revision full outputs are written under --log-dir
(default investigation/replays/) and their paths are the evidence the
records file references. The opt-in pytest wrapper is
tests/properties/test_finding_regression_replay.py. Runs strictly
sequentially, one group at a time.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finding_core  # noqa: E402  (script-local sibling module)

# Per-step subprocess budgets. The venv bootstrap dominates the first
# revision; later revisions rebuild it from the warm uv cache.
GIT_TIMEOUT = 120
VENV_TIMEOUT = 600
PYTEST_TIMEOUT = 900
DEFAULT_TAIL_LINES = 30

RECORDS_ENV_HINT = (
    "QRP_FINDING_REPLAY=1 python -m pytest "
    "tests/properties/test_finding_regression_replay.py")


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
        return 124, partial_out, f"timed out after {timeout}s: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def log(message: str) -> None:
    """Human progress goes to stderr; stdout is reserved for --json."""
    print(message, file=sys.stderr, flush=True)


def resolve_commit(repo_root: str, sha: str) -> tuple[bool, str]:
    code, _, err = run_cmd(
        ["git", "rev-parse", "--verify", f"{sha}^{{commit}}"],
        repo_root, GIT_TIMEOUT)
    if code != 0:
        return False, f"revision {sha!r} does not resolve: {err.strip()}"
    return True, ""


def verify_ancestry(repo_root: str, plan: finding_core.GroupPlan,
                    ) -> tuple[bool, str]:
    """Every recorded (parent, child) pair must hold in git."""
    for parent, child in plan.ancestry_pairs:
        ok, detail = resolve_commit(repo_root, child)
        if not ok:
            return False, detail
        code, out, _ = run_cmd(
            ["git", "rev-parse", "--verify", f"{child}^"],
            repo_root, GIT_TIMEOUT)
        if code != 0:
            return False, f"cannot read parent of {child}: {out.strip()}"
        if out.strip() != parent:
            return False, (f"recorded ancestry broken: {child} parent is "
                           f"{out.strip()[:12]}, record says {parent[:12]}")
    return True, ""


def create_worktree(repo_root: str, worktree: str, revision: str,
                    ) -> tuple[bool, str]:
    if Path(worktree).exists():
        return False, (f"{worktree} already exists; refusing to reuse or "
                       "remove a path this run did not create")
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
    """A FRESH venv, built from scratch. Called once per revision."""
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


def drop_venv(worktree: str) -> tuple[bool, str]:
    venv_dir = Path(worktree) / ".venv"
    if not venv_dir.exists():
        return True, ""
    import shutil
    try:
        shutil.rmtree(venv_dir)
    except OSError as exc:
        return False, f"cannot remove {venv_dir}: {exc}"
    return True, ""


def checkout_revision(repo_root: str, worktree: str, sha: str,
                      ) -> tuple[bool, str]:
    code, _, err = run_cmd(
        ["git", "checkout", "--detach", sha], worktree, GIT_TIMEOUT)
    if code != 0:
        return False, f"git checkout {sha[:12]} failed: {err.strip()}"
    return True, ""


def run_revision(plan: finding_core.GroupPlan, index: int,
                 pytest_timeout: int) -> tuple[finding_core.RevOutcome,
                                               str]:
    """Run one recorded revision; return (outcome, combined output)."""
    record = plan.record
    revision = record.revisions[index]
    started = time.perf_counter()
    code, out, err = run_cmd(
        list(plan.commands[index]), plan.worktree, pytest_timeout)
    duration = time.perf_counter() - started
    combined = out + err
    if revision.expect == finding_core.ROLE_FAIL:
        ok, detail = finding_core.red_failed_as_expected(
            code, combined, revision.expected_patterns)
    else:
        ok, detail = finding_core.green_passed(code, combined)
    pattern_matched = all(
        re.search(p, combined) is not None
        for p in revision.expected_patterns)
    if not ok:
        log(f"[{record.id}] {revision.role} FAILED: {detail}")
    return finding_core.RevOutcome(
        role=revision.role,
        sha=revision.sha,
        exit_code=code,
        pattern_matched=pattern_matched,
        ok=ok,
        duration_seconds=duration,
        output=combined,
    ), detail


def replay_group(plan: finding_core.GroupPlan, log_dir: Path,
                 pytest_timeout: int, tail_lines: int,
                 ) -> finding_core.GroupOutcome:
    """One full disposable-worktree red/green(/final) cycle."""
    started = time.perf_counter()
    record = plan.record
    steps: list[finding_core.StepVerdict] = []
    revisions: list[finding_core.RevOutcome] = []
    worktree_created = False

    roles = " -> ".join(
        f"{r.role}@{r.sha[:12]}" for r in record.revisions)
    log(f"[{record.id}] findings {list(record.findings)}: "
        f"base {record.base_sha[:12]} then {roles}")

    ok, detail = verify_ancestry(plan.repo_root, plan)
    steps.append(finding_core.StepVerdict("ancestry", ok, detail))
    if ok:
        ok, detail = create_worktree(
            plan.repo_root, plan.worktree, record.revisions[0].sha)
        worktree_created = ok
        steps.append(finding_core.StepVerdict("worktree", ok, detail))

    try:
        for index, revision in enumerate(record.revisions):
            if not ok:
                break
            if index > 0:
                ok, detail = checkout_revision(
                    plan.repo_root, plan.worktree, revision.sha)
                steps.append(finding_core.StepVerdict(
                    f"checkout-{revision.role}", ok, detail))
                if ok:
                    ok, detail = drop_venv(plan.worktree)
                    steps.append(finding_core.StepVerdict(
                        f"drop-venv-{revision.role}", ok, detail))
            if ok:
                ok, detail = bootstrap_venv(plan.worktree)
                steps.append(finding_core.StepVerdict(
                    f"venv-{revision.role}", ok, detail))
            if ok:
                rev_result, fail_detail = run_revision(
                    plan, index, pytest_timeout)
                revisions.append(rev_result)
                log_dir.mkdir(parents=True, exist_ok=True)
                log_file = log_dir / f"{record.id}-{revision.role}.log"
                log_file.write_text(
                    f"# group {record.id} revision {revision.role} "
                    f"{revision.sha}\n# command: {revision.command}\n"
                    f"# exit: {rev_result.exit_code} duration: "
                    f"{rev_result.duration_seconds:.1f}s ok: "
                    f"{rev_result.ok}\n"
                    f"\n{rev_result.output}", encoding="utf-8")
                steps.append(finding_core.StepVerdict(
                    f"run-{revision.role}", rev_result.ok, fail_detail))
                ok = rev_result.ok
    finally:
        if worktree_created:
            removed, detail = remove_worktree(plan.repo_root, plan.worktree)
            steps.append(finding_core.StepVerdict("cleanup", removed, detail))

    elapsed = time.perf_counter() - started
    group_result = finding_core.GroupOutcome(
        group_id=record.id,
        ok=all(s.ok for s in steps) and all(r.ok for r in revisions),
        revisions=tuple(revisions),
        worktree_removed=any(
            s.step == "cleanup" and s.ok for s in steps),
        steps=tuple(steps),
    )
    if group_result.ok:
        rev_summary = ", ".join(
            f"{r.role} exit {r.exit_code} ({r.duration_seconds:.1f}s)"
            for r in revisions)
        log(f"[{record.id}] OK: {rev_summary}, worktree removed "
            f"(total {elapsed:.1f}s)")
    else:
        failed = [s for s in group_result.steps if not s.ok]
        log(f"[{record.id}] FAILED after {elapsed:.1f}s at step(s): "
            + ", ".join(f"{s.step} ({s.detail})" for s in failed))
        for rev_result in revisions:
            if not rev_result.ok:
                log(f"[{record.id}] {rev_result.role} tail:\n"
                    + finding_core.tail(rev_result.output, tail_lines))
    return group_result


def load_records(records_path: Path) -> tuple[dict, str]:
    try:
        raw = json.loads(records_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {}, f"cannot read {records_path}: {exc}"
    except json.JSONDecodeError as exc:
        return {}, f"{records_path} is not valid JSON: {exc}"
    return raw, ""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AC.9 finding regression replay for fixed campaign "
                    "findings")
    parser.add_argument(
        "--records", type=Path, default=None,
        help="path to the records document (default: "
             "investigation/finding-replay-records.json next to this "
             "script)")
    parser.add_argument(
        "--group", action="append", default=None, metavar="GROUP_ID",
        help="replay only this group (repeatable; default: all)")
    parser.add_argument(
        "--list", action="store_true",
        help="print group ids and findings, then exit")
    parser.add_argument(
        "--json", action="store_true",
        help="emit machine verdicts as a JSON array on stdout")
    parser.add_argument(
        "--tail-lines", type=int, default=DEFAULT_TAIL_LINES,
        help="lines of failing output kept per revision (default "
             f"{DEFAULT_TAIL_LINES})")
    parser.add_argument(
        "--pytest-timeout", type=int, default=PYTEST_TIMEOUT,
        help=f"per pytest invocation budget in seconds (default "
             f"{PYTEST_TIMEOUT})")
    parser.add_argument(
        "--log-dir", type=Path, default=None,
        help="directory for per-revision full outputs (default: "
             "investigation/replays next to this script)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    script_dir = Path(__file__).resolve().parent
    records_path = (args.records if args.records is not None
                    else script_dir / "finding-replay-records.json")
    log_dir = (args.log_dir if args.log_dir is not None
               else script_dir / "replays")
    repo_root = str(script_dir.parent)

    raw, error = load_records(records_path)
    if error:
        log(error)
        return 2
    groups, _nonfixed, errors = finding_core.parse_records(raw)
    if errors:
        log("records document failed validation:")
        for e in errors:
            log(f"  - {e}")
        return 2

    if args.group:
        known = {g.id for g in groups}
        missing = [g for g in args.group if g not in known]
        if missing:
            log(f"no such replay group(s) in {records_path}: {missing}")
            return 2
        groups = [g for g in groups if g.id in args.group]

    if args.list:
        for group in groups:
            log(f"{group.id}: findings={list(group.findings)} "
                f"branch={group.branch} revisions="
                f"{[r.role for r in group.revisions]}")
        return 0

    worktree_root = str(Path(repo_root).parent)
    plans: list[finding_core.GroupPlan] = []
    invalid = False
    for record in groups:
        plan, plan_errors = finding_core.build_plan(
            record, repo_root, worktree_root)
        if plan is None:
            invalid = True
            log(f"unusable replay group {record.id!r}:")
            for e in plan_errors:
                log(f"  - {e}")
            continue
        plans.append(plan)
    if invalid:
        log("records failed planning; nothing replayed")
        return 2

    executed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    outcomes = []
    for plan in plans:  # strictly sequential, one group at a time
        outcomes.append(
            replay_group(plan, log_dir, args.pytest_timeout,
                         args.tail_lines))

    if args.json:
        print(json.dumps([o.as_dict() for o in outcomes], indent=2))
    failed = [o.group_id for o in outcomes if not o.ok]
    if failed:
        log(f"replay FAILED for: {failed}")
        return 1
    log(f"replay OK for {len(outcomes)} group(s): "
        f"{[o.group_id for o in outcomes]} "
        f"(executed_at {executed_at}; logs in {log_dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
