# pattern: Functional Core
"""Pure model for the AC.8 mutation replay and completion validation.

Everything here computes and classifies; nothing here touches the
filesystem, spawns processes, or reads the clock. The imperative half
that actually runs git/uv/pytest lives in replay_mutation.py, which is
the only file allowed to call into this module.

Two concerns live here:

* the replay machinery — a calibration record is one manifest.json
  `calibrations[]` entry (schema documented in investigation/README.md,
  campaign-log section). The replay proves, per record: the recorded
  diff applies at the recorded base, the recorded command FAILS against
  the mutation for the recorded reason (expected_failure_pattern), and
  PASSES again after the mutation is reverted.
* the strict completion predicates over the whole campaign record set
  (two recorded passes per package, genuine committed replay verdicts,
  referenced evidence files), shared by the always-on manifest validator
  tests. Callers inject every filesystem/git touchpoint so these stay
  cheap to run and cheap to test against synthetic manifests.
"""

from collections.abc import Callable, Container, Mapping
from dataclasses import dataclass
from pathlib import Path
import re
import shlex

# A record id doubles as a path component (worktree `replay-<id>`), so
# it is restricted to filesystem-safe characters from the first byte.
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "package",
    "base_revision",
    "diff_path",
    "failing_command",
    "expected_failure_pattern",
    "red_evidence",
    "green_evidence",
    "reverted",
    "provenance",
)
OPTIONAL_FIELDS: tuple[str, ...] = ("base_note",)
PROVENANCES: frozenset[str] = frozenset({"recorded", "reconstructed"})

# Matches `python`, `python3`, `.venv/bin/python`, `/usr/bin/python3.13`
# and friends — the interpreter token a record's failing_command may
# lead with. The replay substitutes the replay venv's interpreter.
_PYTHON_TOKEN_RE = re.compile(
    r"(?:.*/)?python(?:3(?:\.1[0-9]*)?)?(?:\.exe)?"
)


@dataclass(frozen=True)
class CalibrationRecord:
    """One validated manifest `calibrations[]` entry."""

    id: str
    package: str
    base_revision: str
    base_note: str
    diff_path: str
    failing_command: str
    expected_failure_pattern: str
    red_evidence: str
    green_evidence: str
    reverted: bool
    provenance: str


@dataclass(frozen=True)
class ReplayPlan:
    """Everything the shell needs to replay one record; no handles."""

    record: CalibrationRecord
    repo_root: str
    worktree: str
    diff_file: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class StepVerdict:
    """Outcome of one replay step; `detail` is empty on success."""

    step: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ReplayOutcome:
    """Full result of one record's red/green cycle."""

    record_id: str
    ok: bool
    red_exit_code: int | None
    green_exit_code: int | None
    red_pattern_matched: bool
    worktree_removed: bool
    steps: tuple[StepVerdict, ...]
    red_tail: str
    green_tail: str

    def as_dict(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "ok": self.ok,
            "red_exit_code": self.red_exit_code,
            "green_exit_code": self.green_exit_code,
            "red_pattern_matched": self.red_pattern_matched,
            "worktree_removed": self.worktree_removed,
            "steps": [
                {"step": s.step, "ok": s.ok, "detail": s.detail}
                for s in self.steps
            ],
            "red_tail": self.red_tail,
            "green_tail": self.green_tail,
        }


def parse_record(raw: object) -> tuple[CalibrationRecord | None, tuple[str, ...]]:
    """Validate one raw manifest entry; return (record, errors).

    `record` is None whenever `errors` is non-empty. All problems are
    reported at once so a broken manifest can be repaired in one pass.
    """
    errors: list[str] = []
    if not isinstance(raw, dict):
        return None, ("calibration entry must be an object",)

    known = set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS)
    unknown = sorted(set(raw) - known)
    if unknown:
        errors.append(f"unknown field(s): {unknown}")
    for field in REQUIRED_FIELDS:
        if field not in raw:
            errors.append(f"missing required field {field!r}")
        elif field != "reverted" and not _is_nonempty_str(raw[field]):
            errors.append(f"field {field!r} must be a non-empty string")
    if "reverted" in raw and raw["reverted"] is not True:
        errors.append("field 'reverted' must be true: every calibration "
                      "must be recorded as reverted")
    if "provenance" in raw and raw["provenance"] not in PROVENANCES:
        errors.append(f"field 'provenance' must be one of "
                      f"{sorted(PROVENANCES)}, got {raw['provenance']!r}")
    if "base_note" in raw and not _is_nonempty_str(raw.get("base_note")):
        errors.append("field 'base_note' must be a non-empty string")
    if errors:
        return None, tuple(errors)

    rec_id = str(raw["id"])
    if not _ID_RE.match(rec_id):
        errors.append(f"id {rec_id!r} must match {_ID_RE.pattern} (it "
                      "names the replay-<id> worktree)")
    try:
        re.compile(str(raw["expected_failure_pattern"]))
    except re.error as exc:
        errors.append(f"expected_failure_pattern does not compile: {exc}")
    if errors:
        return None, tuple(errors)

    record = CalibrationRecord(
        id=rec_id,
        package=str(raw["package"]),
        base_revision=str(raw["base_revision"]),
        base_note=str(raw.get("base_note", "")),
        diff_path=str(raw["diff_path"]),
        failing_command=str(raw["failing_command"]),
        expected_failure_pattern=str(raw["expected_failure_pattern"]),
        red_evidence=str(raw["red_evidence"]),
        green_evidence=str(raw["green_evidence"]),
        reverted=bool(raw["reverted"]),
        provenance=str(raw["provenance"]),
    )
    return record, ()


def venv_command(failing_command: str, python_exe: str,
                 ) -> tuple[tuple[str, ...], str]:
    """Rewrite a record's failing_command into argv under the replay venv.

    Returns (argv, error); argv is empty when error is non-empty. The
    leading interpreter token (`python`, `.venv/bin/python`, ...) is
    replaced with the replay worktree's venv python; anything else is
    refused so a record can never silently run against the wrong
    environment.
    """
    tokens = shlex.split(failing_command)
    if not tokens:
        return (), "failing_command is empty after shell splitting"
    if not _PYTHON_TOKEN_RE.fullmatch(tokens[0]):
        return (), (f"failing_command must lead with a python interpreter "
                    f"token, got {tokens[0]!r}")
    tokens[0] = python_exe
    return tuple(tokens), ""


def build_plan(record: CalibrationRecord, repo_root: str, worktree_root: str,
               ) -> tuple[ReplayPlan | None, tuple[str, ...]]:
    """Derive the replay plan (pure path/argv arithmetic).

    `repo_root` and `diff_file` must already be resolved absolute paths
    (resolving is the shell's job). Containment of the diff under the
    repo root is checked here as a string relation; existence and the
    revision check are the shell's.
    """
    diff_file = str(_resolve(repo_root, record.diff_path))
    if not diff_file.startswith(repo_root.rstrip("/") + "/"):
        return None, (f"diff_path {record.diff_path!r} escapes the repo "
                      "root; refusing to apply it",)
    worktree = str(_resolve(worktree_root, f"replay-{record.id}"))
    python_exe = str(_resolve(worktree, ".venv/bin/python"))
    command, cmd_error = venv_command(record.failing_command, python_exe)
    if cmd_error:
        return None, (cmd_error,)
    return ReplayPlan(
        record=record,
        repo_root=repo_root,
        worktree=worktree,
        diff_file=diff_file,
        command=command,
    ), ()


def red_failed_as_expected(exit_code: int, output: str,
                           pattern: str) -> tuple[bool, str]:
    """The red run must fail AND fail for the recorded reason."""
    if exit_code == 0:
        return False, "red run exited 0: the mutation did not fail the test"
    if re.search(pattern, output) is None:
        return False, (f"red run failed (exit {exit_code}) but the output "
                       f"never matched expected_failure_pattern {pattern!r}")
    return True, ""


def green_recovered(exit_code: int) -> tuple[bool, str]:
    """The green run (mutation reverted) must pass."""
    if exit_code != 0:
        return False, f"green run exited {exit_code}: reverting did not recover"
    return True, ""


def tail(text: str, lines: int) -> str:
    """Last `lines` lines of `text`, for evidence output."""
    if lines <= 0 or not text:
        return ""
    kept = text.rstrip().splitlines()[-lines:]
    return "\n".join(kept)


def combine_verdicts(record_id: str, steps: list[StepVerdict],
                     red_exit_code: int | None, green_exit_code: int | None,
                     red_pattern_matched: bool, red_output: str,
                     green_output: str, tail_lines: int) -> ReplayOutcome:
    """Assemble the outcome; ok iff every step succeeded."""
    return ReplayOutcome(
        record_id=record_id,
        ok=all(s.ok for s in steps),
        red_exit_code=red_exit_code,
        green_exit_code=green_exit_code,
        red_pattern_matched=red_pattern_matched,
        worktree_removed=any(
            s.step == "cleanup" and s.ok for s in steps),
        steps=tuple(steps),
        red_tail=tail(red_output, tail_lines),
        green_tail=tail(green_output, tail_lines),
    )


def _is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _resolve(base: str, relative: str) -> str:
    """Join and normalize without touching the filesystem (pure)."""
    return str(Path(base) / relative)


# ---------------------------------------------------------------------------
# Strict completion validation (AC.8 evidence contract)
#
# Pure predicates over the campaign manifest record set, consumed by the
# always-on validator tests. Every filesystem or git touchpoint is
# injected (`path_exists`, `revision_resolves`), so these functions stay
# cheap, testable, and free of side effects.
# ---------------------------------------------------------------------------

CAMPAIGN_PACKAGES: tuple[str, ...] = ("A", "B", "C", "D", "E", "F")
# Worker ids that record passes[] entries without being property
# packages; they carry no completion obligations of their own.
CAMPAIGN_WORKER_IDS: frozenset[str] = frozenset(
    {"arb", "arb2", "arb3", "campaign-infra", "orchestrator"})
PASS_RESULTS: frozenset[str] = frozenset(
    {"pass", "confirmed", "red then green", "red", "green"})
IN_FLIGHT_STATUSES: frozenset[str] = frozenset({"pending", "in_progress"})
REPORT_PATH_TEMPLATE = "investigation/packages/{pid}.md"
# Committed machine-verdict record for the calibration replays (the
# durable, executable counterpart of the red/green prose evidence).
VERDICT_RECORD_PATH = "investigation/audit-replays/replay-verdicts.json"
# Expected step sequence of one committed mutation-replay verdict
# (ReplayOutcome.as_dict / replay_mutation.py).
VERDICT_STEPS: tuple[str, ...] = (
    "base", "worktree", "venv", "apply", "red", "reset", "green", "cleanup")
_SHA_TOKEN_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
# Fragments that betray a masked run instead of a real one.
_MASKED_RED_MARKS = ("no tests ran", "collected 0 items")
_MASKED_GREEN_MARKS = (
    "no tests ran", "collected 0 items", " failed", " skipped")


def campaign_pass_number(raw: Mapping[str, object]) -> tuple[int | None, str]:
    """Campaign pass (1 or 2) a passes[] entry belongs to.

    An explicit integer `campaign_pass` field wins; without it the
    recorded id convention decides ('pass2' in the id marks pass 2,
    everything else is pass 1).
    """
    value = raw.get("campaign_pass")
    if value is not None:
        if isinstance(value, int) and not isinstance(value, bool) \
                and value in (1, 2):
            return value, ""
        return None, (f"campaign_pass must be the integer 1 or 2, "
                      f"got {value!r}")
    pass_id = raw.get("id")
    pass_id = pass_id if isinstance(pass_id, str) else ""
    return (2 if "pass2" in pass_id else 1), ""


def validate_pass_entry(raw: object,
                        known_packages: Container[str]) -> tuple[str, ...]:
    """Field-level problems for one passes[] entry, all reported at once."""
    if not isinstance(raw, Mapping):
        return ("passes[] entry must be an object",)
    errors: list[str] = []
    pass_id = raw.get("id")
    label = pass_id if isinstance(pass_id, str) and pass_id.strip() else "<no id>"
    if not _is_nonempty_str(pass_id):
        errors.append("id must be a non-empty string")
    for field in ("command", "revision", "result", "detail"):
        if not _is_nonempty_str(raw.get(field)):
            errors.append(f"field {field!r} must be a non-empty string")
    result = raw.get("result")
    if _is_nonempty_str(result) and result not in PASS_RESULTS:
        errors.append(f"result {result!r} not in {sorted(PASS_RESULTS)}")
    if "duration_seconds" not in raw:
        errors.append("duration_seconds key missing (None allowed)")
    package = raw.get("package")
    if package not in known_packages:
        errors.append(f"unknown package {package!r}")
    _, pass_error = campaign_pass_number(raw)
    if pass_error:
        errors.append(pass_error)
    return tuple(f"pass {label!r}: {error}" for error in errors)


def validate_replay_verdict(raw: object) -> tuple[str, ...]:
    """Shape and genuineness problems for one committed replay verdict.

    A verdict is evidence only when it proves a genuine red→green cycle:
    red exit 1 (a real assertion-level failure — exit 2/4/5 would mean a
    collection, usage, or empty-run mask), the recorded failure pattern
    matched, green exit 0, worktree removed, every replay step ok, and
    tails carrying the failure and the recovery rather than a masked run.
    """
    if not isinstance(raw, Mapping):
        return ("replay verdict must be an object",)
    errors: list[str] = []
    record_id = raw.get("record_id")
    label = (record_id if isinstance(record_id, str) and record_id.strip()
             else "<no record_id>")
    for field in ("record_id", "ok", "red_exit_code", "green_exit_code",
                  "red_pattern_matched", "worktree_removed", "steps",
                  "red_tail", "green_tail"):
        if field not in raw:
            errors.append(f"missing field {field!r}")
    if errors:
        return tuple(f"verdict {label!r}: {error}" for error in errors)
    if raw["ok"] is not True:
        errors.append("ok must be true: a verdict that did not prove "
                      "red→green is a failed replay, not evidence")
    for field in ("red_exit_code", "green_exit_code"):
        if not isinstance(raw[field], int) or isinstance(raw[field], bool):
            errors.append(f"{field} must be an integer")
    red_exit = raw["red_exit_code"]
    if isinstance(red_exit, int) and not isinstance(red_exit, bool) \
            and red_exit != 1:
        errors.append(f"red_exit_code must be 1 — a genuine assertion-level "
                      f"failure (0/2/4/5 mean no-failure or a collection/"
                      f"usage/empty mask) — got {red_exit}")
    green_exit = raw["green_exit_code"]
    if isinstance(green_exit, int) and not isinstance(green_exit, bool) \
            and green_exit != 0:
        errors.append(f"green_exit_code must be 0, got {green_exit}")
    if raw["red_pattern_matched"] is not True:
        errors.append("red_pattern_matched must be true: the red run must "
                      "fail for the recorded reason")
    if raw["worktree_removed"] is not True:
        errors.append("worktree_removed must be true: the disposable "
                      "replay worktree must be cleaned up")
    errors.extend(_verdict_step_errors(raw.get("steps")))
    errors.extend(_verdict_tail_errors(raw))
    return tuple(f"verdict {label!r}: {error}" for error in errors)


def _verdict_step_errors(steps: object) -> list[str]:
    if not isinstance(steps, list):
        return ["steps must be a list of step objects"]
    errors: list[str] = []
    names = [step.get("step") for step in steps if isinstance(step, Mapping)]
    if names != list(VERDICT_STEPS):
        errors.append(f"steps must be exactly {list(VERDICT_STEPS)} in "
                      f"order, got {names}")
    not_ok = [step.get("step") for step in steps
              if isinstance(step, Mapping) and step.get("ok") is not True]
    if not_ok:
        errors.append(f"step(s) not ok: {not_ok}")
    return errors


def _verdict_tail_errors(raw: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    red_tail, green_tail = raw["red_tail"], raw["green_tail"]
    for field, value in (("red_tail", red_tail), ("green_tail", green_tail)):
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field} must be a non-empty string (the run's "
                          "evidence tail)")
    if isinstance(red_tail, str):
        if not any(mark in red_tail
                   for mark in ("FAILED", "AssertionError", " failed")):
            errors.append("red_tail carries no failure evidence (expected "
                          "FAILED/AssertionError)")
        masked = [mark for mark in _MASKED_RED_MARKS if mark in red_tail]
        if masked:
            errors.append(f"red_tail shows a masked run {masked}, not a "
                          "genuine test failure")
    if isinstance(green_tail, str):
        if " passed" not in green_tail:
            errors.append("green_tail records no passing test")
        masked = [mark for mark in _MASKED_GREEN_MARKS if mark in green_tail]
        if masked:
            errors.append(f"green_tail shows a masked run {masked}")
    return errors


def manifest_calibration_ids(manifest: object) -> tuple[list[str], str]:
    """Calibration record ids, or an error when the shape is unusable."""
    if not isinstance(manifest, Mapping):
        return [], "manifest must be a JSON object"
    records = manifest.get("calibrations")
    if not isinstance(records, list):
        return [], "'calibrations' must be a list"
    ids: list[str] = []
    for index, raw in enumerate(records):
        if not isinstance(raw, Mapping) or not _is_nonempty_str(raw.get("id")):
            return [], f"calibrations[{index}] has no usable id"
        ids.append(str(raw["id"]))
    return ids, ""


def verdict_coverage_problems(calibration_ids: list[str],
                              verdicts: object) -> tuple[str, ...]:
    """Problems in the committed machine-verdict set vs the records.

    Coverage must be exact: every calibration record needs one genuine
    committed verdict, and no verdict may reference a record that no
    longer exists.
    """
    if not isinstance(verdicts, list):
        return ("replay verdicts must be a list of verdict objects",)
    errors: list[str] = []
    seen: set[str] = set()
    for raw in verdicts:
        errors.extend(validate_replay_verdict(raw))
        if isinstance(raw, Mapping) and isinstance(raw.get("record_id"), str):
            record_id = str(raw["record_id"])
            if record_id in seen:
                errors.append(f"duplicate verdict for {record_id!r}")
            seen.add(record_id)
    missing = [cid for cid in calibration_ids if cid not in seen]
    if missing:
        errors.append(
            "no committed replay verdict for calibration record(s): "
            + ", ".join(missing)
            + f" — re-run investigation/replay_mutation.py for them and "
              f"commit the verdicts to {VERDICT_RECORD_PATH}")
    unknown = sorted(seen - set(calibration_ids))
    if unknown:
        errors.append(
            "committed replay verdict(s) without a matching calibration "
            "record (stale evidence): " + ", ".join(unknown))
    return tuple(errors)


def completion_problems(
    manifest: Mapping[str, object], *,
    path_exists: Callable[[str], bool],
    revision_resolves: Callable[[str], bool],
) -> tuple[str, ...]:
    """Everything keeping the campaign record from counting as complete.

    Completion, per package A–F: a settled status consistent with the
    recorded passes, a pass-1 and a pass-2 run with result 'pass' whose
    pass-2 record names a resolvable revision and real test files, one
    deliberate-mutation calibration record, and the package report file
    referenced by name. Filesystem/git access is injected: `path_exists`
    answers for repo-relative paths (glob tokens included),
    `revision_resolves` answers for revision tokens. Machine-verdict
    coverage is checked separately by verdict_coverage_problems.
    """
    packages_raw = manifest.get("packages")
    passes_raw = manifest.get("passes")
    calibrations_raw = manifest.get("calibrations")
    if not isinstance(packages_raw, list):
        return ("manifest 'packages' must be a list",)
    if not isinstance(passes_raw, list):
        return ("manifest 'passes' must be a list",)
    if not isinstance(calibrations_raw, list):
        return ("manifest 'calibrations' must be a list",)

    problems: list[str] = []
    packages: dict[str, Mapping[str, object]] = {}
    for raw in packages_raw:
        if isinstance(raw, Mapping) and _is_nonempty_str(raw.get("id")):
            packages[str(raw["id"])] = raw
    missing_entries = [pid for pid in CAMPAIGN_PACKAGES if pid not in packages]
    if missing_entries:
        problems.append(
            "packages[] has no entry for: " + ", ".join(missing_entries))

    known_packages: Container[str] = set(packages) | CAMPAIGN_WORKER_IDS
    pass_records: dict[str, list[tuple[int, Mapping[str, object]]]] = {
        pid: [] for pid in CAMPAIGN_PACKAGES}
    seen_pass_ids: set[str] = set()
    for raw in passes_raw:
        problems.extend(validate_pass_entry(raw, known_packages))
        if not isinstance(raw, Mapping):
            continue
        pass_id = raw.get("id")
        if isinstance(pass_id, str) and pass_id.strip():
            if pass_id in seen_pass_ids:
                problems.append(f"duplicate pass id {pass_id!r}")
            seen_pass_ids.add(pass_id)
        number, _ = campaign_pass_number(raw)  # invalid values reported above
        package = raw.get("package")
        if number is not None and isinstance(package, str) \
                and package in pass_records:
            pass_records[package].append((number, raw))

    for pid in CAMPAIGN_PACKAGES:
        entry = packages.get(pid)
        if entry is None:
            continue  # missing entry already reported
        status = entry.get("status")
        if not _is_nonempty_str(status):
            problems.append(f"package {pid}: no status recorded")
        elif status in IN_FLIGHT_STATUSES:
            problems.append(
                f"package {pid}: status {status!r} is not settled — "
                "campaign completion requires every package settled")
        records = pass_records[pid]
        has_pass1 = _has_result_pass(records, 1)
        has_pass2 = _has_result_pass(records, 2)
        if not has_pass1:
            problems.append(
                f"package {pid}: no recorded pass-1 run with result 'pass' "
                "in passes[]")
        if not has_pass2:
            report_path = REPORT_PATH_TEMPLATE.format(pid=pid)
            problems.append(
                f"package {pid}: no recorded pass-2 run with result 'pass' "
                "in passes[] — add an entry whose id marks it pass 2 (id "
                "containing 'pass2' or explicit campaign_pass: 2) with a "
                "command naming the package's property test files, a "
                "revision naming a resolvable commit, and the package "
                f"report {report_path} referenced in its detail; then set "
                "the package status to 'pass2-complete'")
        elif status == "pass1-complete":
            problems.append(
                f"package {pid}: status 'pass1-complete' but pass-2 pass "
                "records exist — stale status, refresh to 'pass2-complete'")
        if has_pass2:
            problems.extend(_pass2_evidence_problems(
                pid, records, revision_resolves, path_exists))
        if not any(isinstance(raw, Mapping) and raw.get("package") == pid
                   for raw in calibrations_raw):
            problems.append(
                f"package {pid}: no calibration record in calibrations[] — "
                "each package needs one deliberate production mutation "
                "proved detectable (executed red→green)")
        problems.extend(_report_reference_problems(pid, entry, records,
                                                   path_exists))
    return tuple(problems)


def _has_result_pass(records: list[tuple[int, Mapping[str, object]]],
                     number: int) -> bool:
    return any(record_number == number and record.get("result") == "pass"
               for record_number, record in records)


def _pass2_evidence_problems(
    pid: str,
    records: list[tuple[int, Mapping[str, object]]],
    revision_resolves: Callable[[str], bool],
    path_exists: Callable[[str], bool],
) -> list[str]:
    problems: list[str] = []
    for number, record in records:
        if number != 2:
            continue
        pass_id = str(record.get("id", "<no id>"))
        revision = str(record.get("revision", ""))
        if not any(revision_resolves(token)
                   for token in set(_SHA_TOKEN_RE.findall(revision))):
            problems.append(
                f"pass {pass_id!r}: revision names no resolvable commit — "
                "record the real integrated revision")
        command = str(record.get("command", ""))
        test_files = [token.split("::")[0] for token in shlex.split(command)
                      if token.startswith("tests/")]
        if not test_files:
            problems.append(
                f"pass {pass_id!r}: command names no test file — record "
                "the real property-suite command")
        for test_file in test_files:
            if not path_exists(test_file):
                problems.append(
                    f"pass {pass_id!r}: command names nonexistent test path "
                    f"{test_file!r}")
    return problems


def _report_reference_problems(
    pid: str,
    entry: Mapping[str, object],
    records: list[tuple[int, Mapping[str, object]]],
    path_exists: Callable[[str], bool],
) -> list[str]:
    report_path = REPORT_PATH_TEMPLATE.format(pid=pid)
    if not path_exists(report_path):
        return [f"package {pid}: report {report_path} does not exist"]
    referenced = report_path in _references_from(entry, "notes") or any(
        report_path in _references_from(record, "detail")
        for _, record in records)
    if referenced:
        return []
    return [
        f"package {pid}: pass-2 evidence is not referenced — name "
        f"{report_path} in a pass-2 record's detail or in the package "
        "entry's notes"]


def _references_from(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    return value if isinstance(value, str) else ""
