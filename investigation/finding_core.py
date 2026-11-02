# pattern: Functional Core
"""Pure model for the AC.9 finding regression replay.

Mirrors investigation/replay_core.py (AC.8) but models *finding*
red/green evidence instead of calibration mutations. Everything here
computes and classifies; nothing here touches the filesystem, spawns
processes, or reads the clock. The imperative half that actually runs
git/uv/pytest lives in finding_regression_replay.py, which is the only
file allowed to call into this module.

A replay group is one `replay_groups[]` entry of
investigation/finding-replay-records.json. The replay proves, per
group: every recorded revision resolves to an immutable commit with
the recorded ancestry (base -> red -> green -> [final]); the red
revision carries the standalone regression and FAILS for the recorded
reason (every expected_pattern matched, pytest reported real failures,
no collection/import errors); and every later revision (green, and
final where the branch carries a follow-up commit) PASSES with a
fresh venv. Groups may cover several findings at once (E-1/E-2 share
one branch and one test file); non-fixed findings live in
`non_fixed_findings[]` and are never counted as fix evidence.
"""

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

SCHEMA = "qrp-finding-replay-records/1"

ROLE_FAIL = "fail"
ROLE_PASS = "pass"
EXPECTS: frozenset[str] = frozenset({ROLE_FAIL, ROLE_PASS})
ROLE_RED = "red"
ROLE_GREEN = "green"
ROLE_FINAL = "final"
ROLES: frozenset[str] = frozenset({ROLE_RED, ROLE_GREEN, ROLE_FINAL})

REQUIRED_GROUP_FIELDS: tuple[str, ...] = (
    "id",
    "findings",
    "package",
    "branch",
    "base_sha",
    "revisions",
    "regression_node_ids",
    "minimality",
    "doc",
)
OPTIONAL_GROUP_FIELDS: tuple[str, ...] = ("fixture_dependency", "note",
                                          "branch_note")
REQUIRED_REVISION_FIELDS: tuple[str, ...] = (
    "role",
    "sha",
    "command",
    "expect",
    "expected_patterns",
)
REQUIRED_MINIMALITY_FIELDS: tuple[str, ...] = (
    "red_test_only",
    "max_commits_after_base",
    "shared_test_file",
    "note",
)
REQUIRED_EXECUTION_FIELDS: tuple[str, ...] = (
    "tool",
    "results",
    "worktree_removed",
    "executed_at",
)
REQUIRED_RESULT_FIELDS: tuple[str, ...] = (
    "role",
    "sha",
    "exit_code",
    "pattern_matched",
    "ok",
    "duration_seconds",
    "log_path",
)
REQUIRED_NONFIXED_FIELDS: tuple[str, ...] = (
    "id",
    "doc",
    "disposition",
    "evidence_note",
)

# A group id names the fr-<id> worktree, so restrict it like
# replay_core's calibration ids.
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# Immutable refs are full 40-hex commit ids; short refs are refused so
# a record can never silently pin a moving target.
_SHA_RE = re.compile(r"[0-9a-f]{40}")

# Matches `python`, `python3`, `.venv/bin/python`, `/usr/bin/python3.13`
# and friends — the interpreter token a revision command may lead with.
# Mirrors replay_core._PYTHON_TOKEN_RE; kept local so the two replay
# models stay decoupled.
_PYTHON_TOKEN_RE = re.compile(
    r"(?:.*/)?python(?:3(?:\.1[0-9]*)?)?(?:\.exe)?"
)

# Pytest short-summary counters, e.g. `21 failed, 3 passed in 4.2s`
# or `24 passed in 16.6s`. Plural forms (`errors`) are folded into one
# bucket so counting does not depend on pytest's singular/plural rules.
_SUMMARY_RE = re.compile(r"\b(\d+) (failed|passed|skipped|errors?)\b")

# Text that proves a red run died before reaching the recorded
# assertion: dependency/import/collection failures are NEVER accepted
# as the recorded red reason.
_MISREASON_MARKERS: tuple[str, ...] = (
    "ImportError",
    "ModuleNotFoundError",
    "INTERNALERROR",
    "error collecting",
    "no tests ran",
)


@dataclass(frozen=True)
class RevisionSpec:
    """One recorded revision of a group: where to run, what to expect."""

    role: str
    sha: str
    command: str
    expect: str
    expected_patterns: tuple[str, ...]


@dataclass(frozen=True)
class Minimality:
    """Declared branch shape; the validator enforces these allowances."""

    red_test_only: bool
    max_commits_after_base: int
    shared_test_file: bool
    note: str


@dataclass(frozen=True)
class GroupRecord:
    """One validated `replay_groups[]` entry plus its execution record."""

    id: str
    findings: tuple[str, ...]
    package: str
    branch: str
    base_sha: str
    revisions: tuple[RevisionSpec, ...]
    regression_node_ids: tuple[str, ...]
    minimality: Minimality
    doc: str
    fixture_dependency: str
    note: str
    execution: dict[str, object] | None


@dataclass(frozen=True)
class NonFixedFinding:
    """A finding with NO fix evidence: ruled, comment-only, or pending."""

    id: str
    doc: str
    disposition: str
    evidence_note: str


@dataclass(frozen=True)
class GroupPlan:
    """Everything the shell needs to replay one group; no handles."""

    record: GroupRecord
    repo_root: str
    worktree: str
    ancestry_pairs: tuple[tuple[str, str], ...]
    commands: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class StepVerdict:
    """Outcome of one replay step; `detail` is empty on success."""

    step: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class RevOutcome:
    """Result of running one recorded revision."""

    role: str
    sha: str
    exit_code: int | None
    pattern_matched: bool
    ok: bool
    duration_seconds: float
    output: str


@dataclass(frozen=True)
class GroupOutcome:
    """Full result of one group's red/green(/final) cycle."""

    group_id: str
    ok: bool
    revisions: tuple[RevOutcome, ...]
    worktree_removed: bool
    steps: tuple[StepVerdict, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "ok": self.ok,
            "worktree_removed": self.worktree_removed,
            "revisions": [
                {
                    "role": r.role,
                    "sha": r.sha,
                    "exit_code": r.exit_code,
                    "pattern_matched": r.pattern_matched,
                    "ok": r.ok,
                    "duration_seconds": round(r.duration_seconds, 2),
                }
                for r in self.revisions
            ],
            "steps": [
                {"step": s.step, "ok": s.ok, "detail": s.detail}
                for s in self.steps
            ],
            "tails": {
                r.role: tail(r.output, 30)
                for r in self.revisions
                if r.output
            },
        }


def _is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_sha(raw: object, field: str, errors: list[str]) -> str | None:
    if not isinstance(raw, str) or not _SHA_RE.fullmatch(raw):
        errors.append(
            f"field {field!r} must be a full 40-hex commit sha "
            f"(immutable ref), got {raw!r}")
        return None
    return raw


def _parse_revision(raw: object, group_id: str,
                    errors: list[str]) -> RevisionSpec | None:
    if not isinstance(raw, dict):
        errors.append(f"group {group_id!r}: revision must be an object")
        return None
    known = set(REQUIRED_REVISION_FIELDS)
    for field in REQUIRED_REVISION_FIELDS:
        if field not in raw:
            errors.append(f"group {group_id!r}: revision missing "
                          f"required field {field!r}")
    unknown = sorted(set(raw) - known)
    if unknown:
        errors.append(f"group {group_id!r}: revision unknown field(s) "
                      f"{unknown}")
    rev_errors: list[str] = []
    role = raw.get("role")
    if role not in ROLES:
        rev_errors.append(f"role must be one of {sorted(ROLES)}, "
                          f"got {role!r}")
    sha = _parse_sha(raw.get("sha"), "sha", rev_errors)
    command = raw.get("command")
    if not _is_nonempty_str(command):
        rev_errors.append("command must be a non-empty string")
    expect = raw.get("expect")
    if expect not in EXPECTS:
        rev_errors.append(f"expect must be one of {sorted(EXPECTS)}, "
                          f"got {expect!r}")
    patterns_raw = raw.get("expected_patterns")
    patterns: list[str] = []
    if not isinstance(patterns_raw, list):
        rev_errors.append("expected_patterns must be an array")
    else:
        for pattern in patterns_raw:
            if not _is_nonempty_str(pattern):
                rev_errors.append(f"expected_patterns entries must be "
                                  f"non-empty strings, got {pattern!r}")
                continue
            try:
                re.compile(str(pattern))
            except re.error as exc:
                rev_errors.append(f"expected_pattern {pattern!r} does not "
                                  f"compile: {exc}")
            else:
                patterns.append(str(pattern))
        if expect == ROLE_FAIL and not patterns:
            rev_errors.append("a fail revision requires at least one "
                              "expected_pattern")
    errors.extend(f"group {group_id!r}: {e}" for e in rev_errors)
    if errors or rev_errors or sha is None or not _is_nonempty_str(command):
        return None
    return RevisionSpec(
        role=str(role),
        sha=sha,
        command=str(command),
        expect=str(expect),
        expected_patterns=tuple(patterns),
    )


def _parse_minimality(raw: object, group_id: str,
                      errors: list[str]) -> Minimality | None:
    if not isinstance(raw, dict):
        errors.append(f"group {group_id!r}: minimality must be an object")
        return None
    for field in REQUIRED_MINIMALITY_FIELDS:
        if field not in raw:
            errors.append(f"group {group_id!r}: minimality missing "
                          f"required field {field!r}")
    m_red_only = raw.get("red_test_only")
    if not isinstance(m_red_only, bool):
        errors.append(f"group {group_id!r}: minimality.red_test_only "
                      "must be a boolean")
    m_shared = raw.get("shared_test_file")
    if not isinstance(m_shared, bool):
        errors.append(f"group {group_id!r}: minimality.shared_test_file "
                      "must be a boolean")
    m_max = raw.get("max_commits_after_base")
    if not isinstance(m_max, int) or isinstance(m_max, bool) or m_max < 2:
        errors.append(f"group {group_id!r}: minimality."
                      "max_commits_after_base must be an integer >= 2 "
                      "(red + at least one fix commit)")
    if not _is_nonempty_str(raw.get("note")):
        errors.append(f"group {group_id!r}: minimality.note must be a "
                      "non-empty string")
    if errors and not all(
            isinstance(raw.get(f), (bool, int, str))
            for f in REQUIRED_MINIMALITY_FIELDS):
        return None
    return Minimality(
        red_test_only=bool(m_red_only),
        max_commits_after_base=int(m_max) if isinstance(m_max, int) else 0,
        shared_test_file=bool(m_shared),
        note=str(raw.get("note", "")),
    )


def parse_group(raw: object) -> tuple[GroupRecord | None, tuple[str, ...]]:
    """Validate one raw replay_groups[] entry; return (record, errors).

    All problems for the group are reported at once. `record` is None
    whenever `errors` is non-empty.
    """
    if not isinstance(raw, dict):
        return None, ("replay group entry must be an object",)
    group_id = raw.get("id")
    gid = str(group_id) if isinstance(group_id, str) else "<missing>"
    errors: list[str] = []

    known = set(REQUIRED_GROUP_FIELDS) | set(OPTIONAL_GROUP_FIELDS) | {
        "execution"}
    unknown = sorted(set(raw) - known)
    if unknown:
        errors.append(f"unknown field(s): {unknown}")
    for field in REQUIRED_GROUP_FIELDS:
        if field not in raw:
            errors.append(f"missing required field {field!r}")
    if not isinstance(group_id, str) or not _ID_RE.fullmatch(group_id):
        errors.append(f"id {group_id!r} must match {_ID_RE.pattern} (it "
                      "names the fr-<id> worktree)")
    findings = raw.get("findings")
    if (not isinstance(findings, list) or not findings
            or not all(_is_nonempty_str(f) for f in findings)):
        errors.append("findings must be a non-empty array of non-empty "
                      "strings")
    for field in ("package", "branch", "doc"):
        if not _is_nonempty_str(raw.get(field)):
            errors.append(f"field {field!r} must be a non-empty string")
    base_sha = _parse_sha(raw.get("base_sha"), "base_sha", errors)
    nodes = raw.get("regression_node_ids")
    if (not isinstance(nodes, list) or not nodes
            or not all(_is_nonempty_str(n) for n in nodes)):
        errors.append("regression_node_ids must be a non-empty array of "
                      "non-empty strings")
    revisions_raw = raw.get("revisions")
    revisions: list[RevisionSpec] = []
    if not isinstance(revisions_raw, list) or len(revisions_raw) < 2:
        errors.append("revisions must be an array with at least a red "
                      "revision and a later passing revision")
    else:
        bad_shape = False
        for i, rev_raw in enumerate(revisions_raw):
            rev = _parse_revision(rev_raw, gid, errors)
            if rev is None:
                bad_shape = True
                continue
            if i == 0:
                if rev.expect != ROLE_FAIL:
                    errors.append("the first revision must expect "
                                  f"{ROLE_FAIL!r} (the recorded red)")
                if rev.role != ROLE_RED:
                    errors.append(f"the first revision must have role "
                                  f"{ROLE_RED!r}, got {rev.role!r}")
            else:
                if rev.expect != ROLE_PASS:
                    errors.append(f"revision[{i}] must expect "
                                  f"{ROLE_PASS!r} (every revision after "
                                  "red)")
                if rev.role not in (ROLE_GREEN, ROLE_FINAL):
                    errors.append(f"revision[{i}] role must be "
                                  f"{ROLE_GREEN!r} or {ROLE_FINAL!r}, got "
                                  f"{rev.role!r}")
                if rev.role == ROLE_FINAL \
                        and revisions_raw[i - 1] is not None \
                        and i + 1 != len(revisions_raw):
                    errors.append("the final revision must be last")
            revisions.append(rev)
        roles_parsed = [r.role for r in revisions]
        if len(set(roles_parsed)) != len(roles_parsed):
            errors.append(f"duplicate revision role(s) in "
                          f"{roles_parsed}; each role must appear once")
        if bad_shape:
            revisions = []
    if base_sha is not None and revisions:
        chain_shas = [base_sha] + [r.sha for r in revisions]
        for parent, child in zip(chain_shas, chain_shas[1:]):
            if parent == child:
                errors.append(f"revision sha {child} repeats its parent; "
                              "each revision must be a distinct commit")
    minimality = _parse_minimality(raw.get("minimality"), gid, errors)
    dep = raw.get("fixture_dependency")
    if dep is not None and not _is_nonempty_str(dep):
        errors.append("field 'fixture_dependency' must be a non-empty "
                      "string when present")
    note = raw.get("note")
    if note is not None and not _is_nonempty_str(note):
        errors.append("field 'note' must be a non-empty string when "
                      "present")
    execution = raw.get("execution")
    if execution is not None and not isinstance(execution, dict):
        errors.append("field 'execution' must be an object when present")

    if errors:
        return None, tuple(errors)
    record = GroupRecord(
        id=gid,
        findings=tuple(str(f) for f in (findings or [])),
        package=str(raw["package"]),
        branch=str(raw["branch"]),
        base_sha=base_sha or "",
        revisions=tuple(revisions),
        regression_node_ids=tuple(str(n) for n in (nodes or [])),
        minimality=minimality,  # type: ignore[arg-type]
        doc=str(raw["doc"]),
        fixture_dependency=str(dep) if dep is not None else "",
        note=str(note) if note is not None else "",
        execution=execution if isinstance(execution, dict) else None,
    )
    return record, ()


def parse_records(raw: object) -> tuple[list[GroupRecord],
                                        list[NonFixedFinding],
                                        tuple[str, ...]]:
    """Validate a whole records document; return (groups, nonfixed, errs).

    Rejects an empty or missing replay_groups[] array outright — an
    empty record set is never tolerated as "no fix evidence".
    """
    errors: list[str] = []
    if not isinstance(raw, dict):
        return [], [], ("records document must be a JSON object",)
    if raw.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA!r}, got {raw.get('schema')!r}")
    if not _is_nonempty_str(raw.get("privacy")) or "NOT AUTHORIZED" \
            not in str(raw.get("privacy")):
        errors.append("privacy must mark the campaign as NOT AUTHORIZED "
                      "FOR PUBLICATION")
    base = raw.get("campaign_base_sha")
    if base is not None:
        _parse_sha(base, "campaign_base_sha", errors)
    groups_raw = raw.get("replay_groups")
    groups: list[GroupRecord] = []
    if not isinstance(groups_raw, list) or not groups_raw:
        errors.append("replay_groups must be a non-empty array: an empty "
                      "record set is missing evidence, not zero findings")
    else:
        seen: set[str] = set()
        for entry in groups_raw:
            record, group_errors = parse_group(entry)
            errors.extend(group_errors)
            if record is not None:
                if record.id in seen:
                    errors.append(f"duplicate group id {record.id!r}")
                seen.add(record.id)
                groups.append(record)
    nonfixed: list[NonFixedFinding] = []
    nf_raw = raw.get("non_fixed_findings")
    if not isinstance(nf_raw, list) or not nf_raw:
        errors.append("non_fixed_findings must be a non-empty array: "
                      "ruled/comment-only/pending findings must be "
                      "documented, never folded into fix evidence")
    else:
        seen_nf: set[str] = set()
        for entry in nf_raw:
            if not isinstance(entry, dict):
                errors.append("non_fixed_findings entries must be objects")
                continue
            missing = [f for f in REQUIRED_NONFIXED_FIELDS if f not in entry]
            if missing:
                errors.append(f"non_fixed_findings {entry.get('id')!r} "
                              f"missing field(s): {missing}")
                continue
            nf = NonFixedFinding(
                id=str(entry["id"]),
                doc=str(entry["doc"]),
                disposition=str(entry["disposition"]),
                evidence_note=str(entry["evidence_note"]),
            )
            if nf.id in seen_nf:
                errors.append(f"duplicate non-fixed id {nf.id!r}")
            seen_nf.add(nf.id)
            nonfixed.append(nf)
    group_finding_ids = [f for g in groups for f in g.findings]
    overlap = sorted(set(group_finding_ids) & {n.id for n in nonfixed})
    if overlap:
        errors.append(f"finding(s) {overlap} appear both in replay_groups "
                      "and non_fixed_findings; a finding is either fixed "
                      "with replay evidence or documented without a fix, "
                      "never both")
    return groups, nonfixed, tuple(errors)


def venv_command(command: str, python_exe: str) -> tuple[tuple[str, ...],
                                                         str]:
    """Rewrite a revision command into argv under the replay venv.

    Returns (argv, error); argv is empty when error is non-empty. The
    leading interpreter token is replaced with the replay worktree's
    venv python; anything else is refused so a record can never
    silently run against the wrong environment.
    """
    tokens = shlex.split(command)
    if not tokens:
        return (), "command is empty after shell splitting"
    if not _PYTHON_TOKEN_RE.fullmatch(tokens[0]):
        return (), (f"command must lead with a python interpreter token, "
                    f"got {tokens[0]!r}")
    tokens[0] = python_exe
    return tuple(tokens), ""


def ancestry_pairs(record: GroupRecord) -> tuple[tuple[str, str], ...]:
    """(parent, child) commit pairs the shell must verify in git."""
    shas = [record.base_sha] + [r.sha for r in record.revisions]
    return tuple(zip(shas, shas[1:]))


def build_plan(record: GroupRecord, repo_root: str, worktree_root: str,
               ) -> tuple[GroupPlan | None, tuple[str, ...]]:
    """Derive the replay plan (pure path/argv arithmetic).

    `repo_root` and `worktree_root` must already be resolved absolute
    paths. The first revision names the worktree's start commit; the
    shell checks out later revisions inside the same disposable
    worktree with a fresh venv each.
    """
    worktree = str(_resolve(worktree_root, f"fr-{record.id}"))
    python_exe = str(_resolve(worktree, ".venv/bin/python"))
    commands: list[tuple[str, ...]] = []
    for revision in record.revisions:
        argv, error = venv_command(revision.command, python_exe)
        if error:
            return None, (
                f"group {record.id!r} revision {revision.role!r}: {error}",)
        commands.append(argv)
    return GroupPlan(
        record=record,
        repo_root=repo_root,
        worktree=worktree,
        ancestry_pairs=ancestry_pairs(record),
        commands=tuple(commands),
    ), ()


def count_summary(output: str) -> dict[str, int]:
    """Counts of pytest's short-summary tokens in `output`."""
    counts = {"failed": 0, "passed": 0, "skipped": 0, "error": 0}
    for count, token in _SUMMARY_RE.findall(output):
        counts["error" if token.startswith("error") else token] += int(count)
    return counts


def red_failed_as_expected(exit_code: int | None, output: str,
                           patterns: tuple[str, ...],
                           ) -> tuple[bool, str]:
    """The red run must fail, fail for the recorded reason, and fail in
    the tests themselves — never as a dependency/import/collection
    error, and never with all tests skipped past the regression."""
    if exit_code is None:
        return False, "red run never produced an exit code"
    if exit_code == 0:
        return False, "red run exited 0: the regression did not fail"
    counts = count_summary(output)
    for marker in _MISREASON_MARKERS:
        if marker in output:
            return False, (f"red output contains {marker!r}: the run "
                           "failed before reaching the recorded "
                           "assertion (dependency/import/collection "
                           "failure is not fix evidence)")
    if counts["failed"] < 1:
        return False, (f"red summary shows failed=0 (counts {counts}): "
                       "the regression did not report real test "
                       "failures")
    for pattern in patterns:
        if re.search(pattern, output) is None:
            return False, (f"red output never matched expected_pattern "
                           f"{pattern!r}")
    return True, ""


def green_passed(exit_code: int | None, output: str) -> tuple[bool, str]:
    """A passing revision must exit 0, report at least one real pass,
    and skip or fail nothing — a skip-only exit 0 is not evidence."""
    if exit_code is None:
        return False, "run never produced an exit code"
    if exit_code != 0:
        return False, f"run exited {exit_code}: expected a pass"
    counts = count_summary(output)
    if counts["passed"] < 1:
        return False, (f"summary shows passed=0 (counts {counts}): no "
                       "test actually ran; a skip-only run is not fix "
                       "evidence")
    if counts["skipped"]:
        return False, (f"summary shows skipped={counts['skipped']}: the "
                       "fixture dependency is not satisfied — treating "
                       "a skip as success is forbidden")
    if counts["failed"] or counts["error"]:
        return False, f"summary shows failures (counts {counts})"
    return True, ""


def execution_consistency(record: GroupRecord) -> tuple[str, ...]:
    """Check a group's embedded execution record against its revisions.

    The execution must cover every revision in order with matching
    shas, show red failing with every pattern matched, every later
    revision passing, positive durations, a log path per revision, and
    worktree cleanup.
    """
    execution = record.execution
    if execution is None:
        return (
            (
                f"group {record.id!r}: no execution record — replay "
                "evidence is missing"
            ),
        )
    errors: list[str] = []
    for field in REQUIRED_EXECUTION_FIELDS:
        if field not in execution:
            errors.append(f"execution missing required field {field!r}")
    results = execution.get("results")
    if not isinstance(results, list) or len(results) != len(
            record.revisions):
        errors.append(f"execution.results must have one entry per "
                      f"revision ({len(record.revisions)}), got "
                      f"{results if not isinstance(results, list) else len(results)}")
        return tuple(errors)
    for i, (revision, result) in enumerate(
            zip(record.revisions, results)):
        if not isinstance(result, dict):
            errors.append(f"execution.results[{i}] must be an object")
            continue
        missing = [f for f in REQUIRED_RESULT_FIELDS if f not in result]
        if missing:
            errors.append(f"execution.results[{i}] missing field(s): "
                          f"{missing}")
            continue
        if result["role"] != revision.role or result["sha"] != revision.sha:
            errors.append(f"execution.results[{i}] role/sha "
                          f"({result['role']}, {result['sha']}) do not "
                          f"match revision ({revision.role}, "
                          f"{revision.sha})")
        exit_code = result["exit_code"]
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            errors.append(f"execution.results[{i}].exit_code must be an "
                          "integer")
            continue
        if revision.expect == ROLE_FAIL:
            if exit_code == 0:
                errors.append(f"execution.results[{i}] ({revision.role}): "
                              "exit_code 0 is not a red result")
            if result["pattern_matched"] is not True:
                errors.append(f"execution.results[{i}] ({revision.role}): "
                              "pattern_matched must be true")
        else:
            if exit_code != 0:
                errors.append(f"execution.results[{i}] ({revision.role}): "
                              f"exit_code {exit_code} is not a pass")
        if result["ok"] is not True:
            errors.append(f"execution.results[{i}].ok must be true")
        duration = result["duration_seconds"]
        if not isinstance(duration, (int, float)) or duration <= 0:
            errors.append(f"execution.results[{i}].duration_seconds must "
                          "be > 0")
        if not _is_nonempty_str(result["log_path"]):
            errors.append(f"execution.results[{i}].log_path must be a "
                          "non-empty string")
    if execution.get("worktree_removed") is not True:
        errors.append("execution.worktree_removed must be true: the "
                      "replay must clean up its own disposable worktree")
    if not _is_nonempty_str(execution.get("tool")):
        errors.append("execution.tool must be a non-empty string")
    if not _is_nonempty_str(execution.get("executed_at")):
        errors.append("execution.executed_at must be a non-empty string")
    return tuple(errors)


def tail(text: str, lines: int) -> str:
    """Last `lines` lines of `text`, for evidence output."""
    if lines <= 0 or not text:
        return ""
    kept = text.rstrip().splitlines()[-lines:]
    return "\n".join(kept)


def _resolve(base: str, relative: str) -> str:
    """Join and normalize without touching the filesystem (pure)."""
    return str(Path(base) / relative)
