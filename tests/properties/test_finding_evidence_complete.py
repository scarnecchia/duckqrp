"""AC.9 finding-evidence completeness validator (cheap, always-on).

Every CONFIRMED (fixed) finding must name an existing, collectible
standalone regression, pin immutable full-SHA red/green(/final)
references with the recorded ancestry, respect its declared branch
minimality allowance, and carry an execution record proving the replay
actually ran (red failed with every expected pattern matched; every
later revision passed with zero skips). Findings WITHOUT a fix — the
B2 parity ruling, the A-3/D2 comment-only doc fixes, and the
C-1/E-3/E-4/D1 pending contract decisions — must be documented in
`non_fixed_findings[]` and must NOT appear as replay evidence.

This module NEVER replays anything: all checks are file reads, cheap
git queries, and one pytest --collect-only pass. The expensive red/
green replay lives behind the opt-in wrapper
tests/properties/test_finding_regression_replay.py.

The negative tests at the bottom mutate an in-memory copy of the
records document to prove the machinery REJECTS missing or fabricated
evidence instead of tolerating it.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "investigation"))
import finding_core  # noqa: E402  (repo-root tool import)

_RECORDS = _REPO_ROOT / "investigation" / "finding-replay-records.json"
_MANIFEST = _REPO_ROOT / "investigation" / "manifest.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(*argv: str) -> str:
    proc = subprocess.run(
        ["git", *argv], cwd=str(_REPO_ROOT), capture_output=True,
        text=True, check=False)
    if proc.returncode != 0:
        pytest.fail(f"git {' '.join(argv)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _records():
    return _load_json(_RECORDS)


def _validated_groups():
    groups, _nonfixed, errors = finding_core.parse_records(_records())
    assert not errors, f"records document invalid: {list(errors)}"
    assert groups, "records document has no replay groups"
    return groups


def test_records_document_rejects_empty_and_missing_evidence() -> None:
    """The machinery must reject missing evidence, not tolerate it."""
    raw = _records()
    empty = dict(raw, replay_groups=[])
    _groups, _nf, errors = finding_core.parse_records(empty)
    assert errors, "empty replay_groups must be rejected"

    no_exec = json.loads(json.dumps(raw))
    del no_exec["replay_groups"][0]["execution"]
    groups, _nf, _errors = finding_core.parse_records(no_exec)
    problems = finding_core.execution_consistency(groups[0])
    assert problems, "a group without an execution record must be rejected"

    fabricated = json.loads(json.dumps(raw))
    fabricated["replay_groups"][0]["revisions"][0]["sha"] = "0" * 40
    _groups, _nf, _errors = finding_core.parse_records(fabricated)
    # A format-valid but fabricated sha passes the pure format check and
    # is rejected at the git layer, where immutability is decided.
    resolves, detail = _verify_commit("0" * 40)
    assert not resolves, "a fabricated (non-resolvable) sha must be rejected"
    assert "does not resolve" in detail

    short_sha = json.loads(json.dumps(raw))
    short_sha["replay_groups"][0]["base_sha"] = \
        short_sha["replay_groups"][0]["base_sha"][:12]
    _groups, _nf, errors = finding_core.parse_records(short_sha)
    assert errors, "a short (non-immutable) sha must be rejected"


def test_every_fixed_finding_is_replayed_exactly_once() -> None:
    """Fixed findings from the manifest each own exactly one replay
    group; nothing else is counted as fix evidence."""
    manifest = _load_json(_MANIFEST)
    fixed_ids = {f["id"] for f in manifest["findings"]
                 if str(f.get("status", "")).startswith("fixed")}
    nonfixed_ids = {f["id"] for f in manifest["findings"]
                    if not str(f.get("status", "")).startswith("fixed")}
    nonfixed_ids |= {d["id"] for d in manifest.get("open_decisions", [])}

    groups = _validated_groups()
    nonfixed, _errors = _validated_nonfixed()
    replayed = [f for g in groups for f in g.findings]
    assert sorted(replayed) == sorted(fixed_ids), (
        f"replayed findings {sorted(replayed)} must equal the manifest's "
        f"fixed findings {sorted(fixed_ids)} — no false fix evidence")
    documented = {n.id for n in nonfixed}
    assert documented == nonfixed_ids, (
        f"non_fixed_findings {sorted(documented)} must equal the "
        f"manifest's non-fixed findings {sorted(nonfixed_ids)}")
    assert not (set(replayed) & nonfixed_ids)


def _validated_nonfixed():
    _groups, nonfixed, errors = finding_core.parse_records(_records())
    return nonfixed, list(errors)


@pytest.mark.parametrize("group", _validated_groups(),
                         ids=lambda g: g.id)
def test_group_documents_exist(group: finding_core.GroupRecord) -> None:
    assert (_REPO_ROOT / group.doc).is_file(), group.doc
    tool = _records().get("replay_tool", "")
    assert (_REPO_ROOT / tool).is_file(), tool


@pytest.mark.parametrize("group", _validated_groups(),
                         ids=lambda g: g.id)
def test_group_shas_resolve_with_recorded_ancestry(
        group: finding_core.GroupRecord) -> None:
    """Every sha is an immutable commit and every recorded (parent,
    child) pair holds in git."""
    ok, detail = _verify_commit(group.base_sha)
    assert ok, detail
    for parent, child in finding_core.ancestry_pairs(group):
        ok, detail = _verify_commit(child)
        assert ok, detail
        parent_of_child = _git("rev-parse", "--verify", f"{child}^")
        assert parent_of_child == parent, (
            f"{child[:12]} parent is {parent_of_child[:12]}, record says "
            f"{parent[:12]}")
    # The branch head must be the final recorded revision.
    final_sha = group.revisions[-1].sha
    assert _git("rev-parse", "--verify", group.branch) == final_sha, (
        f"{group.branch} head must be the final revision {final_sha[:12]}")


def _verify_commit(sha: str) -> tuple[bool, str]:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"{sha}^{{commit}}"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return False, f"{sha} does not resolve: {proc.stderr.strip()}"
    if proc.stdout.strip() != sha:
        return False, f"{sha} resolves to {proc.stdout.strip()}"
    return True, ""


@pytest.mark.parametrize("group", _validated_groups(),
                         ids=lambda g: g.id)
def test_group_minimality_allowance_holds(
        group: finding_core.GroupRecord) -> None:
    """The red commit adds ONLY the recorded test file(s); every
    post-base commit touches at least one src/ path; the branch stays
    within its declared commit allowance."""
    red_files = _git(
        "diff-tree", "--no-commit-id", "--name-only", "-r",
        group.revisions[0].sha).splitlines()
    assert red_files, "red commit must add the standalone regression"
    for path in red_files:
        assert path.startswith("tests/"), (
            f"red commit touches non-test file {path}")
    added = _git(
        "diff-tree", "--no-commit-id", "--name-only", "-r",
        "--diff-filter=A", group.revisions[0].sha).splitlines()
    assert any(node.split("::")[0] in added
               for node in group.regression_node_ids), (
        "the red commit must add the recorded regression file(s)")
    commits = _git("rev-list", "--count",
                   f"{group.base_sha}..{group.revisions[-1].sha}")
    assert int(commits) <= group.minimality.max_commits_after_base, (
        f"branch {group.branch} has {commits} commits after base, "
        f"allowance {group.minimality.max_commits_after_base}")
    test_edits: set[str] = set()
    for revision in group.revisions[1:]:
        files = _git("diff-tree", "--no-commit-id", "--name-only", "-r",
                     revision.sha).splitlines()
        assert any(f.startswith("src/") for f in files), (
            f"{revision.role} commit {revision.sha[:12]} touches no "
            "production path — it is not a fix commit")
        test_edits.update(f for f in files if f.startswith("tests/"))
    # Post-base commits may edit at most the declared shared test file
    # (the e1e2 allowance: green tweaks the shared regression by two
    # lines). Any other post-base test edit breaks the allowance.
    allowed = ({node.split("::")[0] for node in
                group.regression_node_ids}
               if group.minimality.shared_test_file else set())
    assert test_edits <= allowed, (
        f"post-base commits edit test files {sorted(test_edits)} beyond "
        f"the declared allowance {sorted(allowed)}")


@pytest.mark.parametrize("group", _validated_groups(),
                         ids=lambda g: g.id)
def test_group_regressions_are_collectible(
        group: finding_core.GroupRecord) -> None:
    """Each named standalone regression exists and collects at the
    current integrated HEAD — one cheap --collect-only pass per
    group."""
    targets = list(group.regression_node_ids)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         *targets],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, check=False)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, (
        f"regression node id(s) {targets} are not collectible:\n"
        f"{combined[-2000:]}")
    for target in targets:
        if "::" in target:
            assert target in proc.stdout, (
                f"node id {target} was not collected")
        else:
            assert any(line.startswith(target)
                       for line in proc.stdout.splitlines()), (
                f"file target {target} collected nothing")


@pytest.mark.parametrize("group", _validated_groups(),
                         ids=lambda g: g.id)
def test_group_has_complete_execution_evidence(
        group: finding_core.GroupRecord) -> None:
    """Every group carries an execution record: red failed with every
    expected pattern matched, every later revision passed, durations
    and log paths recorded, worktree cleaned up."""
    problems = finding_core.execution_consistency(group)
    assert not problems, f"{group.id}: {list(problems)}"
    execution = group.execution or {}
    for result in execution["results"]:
        log_path = _REPO_ROOT / str(result["log_path"])
        assert log_path.is_file(), (
            f"execution log {result['log_path']} does not exist")
        content = log_path.read_text(encoding="utf-8")
        revision = next(r for r in group.revisions
                        if r.role == result["role"])
        for pattern in revision.expected_patterns:
            assert re.search(pattern, content), (
                f"log {result['log_path']} does not contain the expected "
                f"pattern {pattern!r}")
        if revision.expect == finding_core.ROLE_PASS:
            counts = finding_core.count_summary(content)
            assert counts["passed"] >= 1 and counts["skipped"] == 0, (
                f"log {result['log_path']} shows a skip or no real pass: "
                f"{counts}")


def test_shared_branch_grouping_is_explicit() -> None:
    """The E-1/E-2 shared-branch allowance (and any other multi-finding
    group) is declared in minimality, never implicit."""
    for group in _validated_groups():
        multi = len(group.findings) > 1
        assert group.minimality.shared_test_file == multi, (
            f"{group.id}: shared_test_file must mirror multi-finding "
            "grouping")
        assert group.minimality.note, "allowance note required"


def test_e1e2_fixture_dependency_is_recorded() -> None:
    """The E-1/E-2 regression's /tmp/qrp_data/100k dependency is
    recorded with its synthetic provenance and skip behavior."""
    groups = {g.id: g for g in _validated_groups()}
    dep = groups["e1e2"].fixture_dependency
    assert "/tmp/qrp_data/100k" in dep
    assert "gen_synthetic" in dep, "synthetic provenance must be named"
    assert "skip" in dep.lower(), "skip behavior must be documented"


def test_b2_parity_ruling_is_documented_not_replayed() -> None:
    """B2 carries the operator's parity ruling and no replay group
    claims it."""
    raw = _records()
    groups, nonfixed, errors = finding_core.parse_records(raw)
    assert not errors
    b2 = next(n for n in nonfixed if n.id == "B2")
    assert "parity" in b2.disposition.lower()
    assert "no fix branch" in b2.disposition.lower()
    assert "not fix evidence" in b2.disposition.lower()
    assert all("B2" not in g.findings for g in groups)


def test_comment_only_findings_document_zero_executable_change() -> None:
    for finding_id in ("A-3", "D2"):
        raw = _records()
        _groups, nonfixed, _errors = finding_core.parse_records(raw)
        entry = next(n for n in nonfixed if n.id == finding_id)
        assert "comment-only" in entry.disposition.lower()
        assert "zero executable changes" in entry.disposition.lower()


def test_pending_decision_findings_are_separate() -> None:
    raw = _records()
    _groups, nonfixed, _errors = finding_core.parse_records(raw)
    by_id = {n.id: n for n in nonfixed}
    for finding_id, brief in (("C-1", "DB-5"), ("E-3", "DB-3"),
                              ("E-4", "DB-4"), ("D1", "DB-2")):
        assert finding_id in by_id, f"{finding_id} missing"
        assert brief in by_id[finding_id].disposition, (
            f"{finding_id} must name its decision brief {brief}")
