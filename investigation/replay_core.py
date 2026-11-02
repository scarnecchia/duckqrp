# pattern: Functional Core
"""Pure model for the AC.8 mutation replay.

Everything here computes and classifies; nothing here touches the
filesystem, spawns processes, or reads the clock. The imperative half
that actually runs git/uv/pytest lives in replay_mutation.py, which is
the only file allowed to call into this module.

A calibration record is one manifest.json `calibrations[]` entry
(schema documented in investigation/README.md, campaign-log section).
The replay proves, per record: the recorded diff applies at the
recorded base, the recorded command FAILS against the mutation for the
recorded reason (expected_failure_pattern), and PASSES again after the
mutation is reverted.
"""

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
                      "root; refusing to apply it")
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
