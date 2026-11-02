"""AC.8: validate investigation/manifest.json as the record of truth.

Cheap on purpose — JSON parsing, filesystem existence, and one git
call per revision token; no DuckDB, no Hypothesis. Deliberately part of
the normal quick suite: a manifest that fails this test invalidates
every claim that references it.

Two tiers live here:

* Always-on record validity — schema/version; every known package
  (infra, A–F) has a branch and a settled status (pending/in_progress
  is a completion failure, never a skip); every passes[] entry is
  field-complete with a known package and a recorded result vocabulary;
  calibration records parse through the shared record parser
  (investigation/replay_core.parse_record) and their references
  resolve; finding documents exist and carry every template-required
  field; every referenced witness path exists.

* Strict completion tier — the campaign record only counts as complete
  when every package A–F records a pass-1 AND a pass-2 run with result
  'pass' (id marks the pass: 'pass2' in the id, or an explicit integer
  campaign_pass field), each pass-2 record names a resolvable revision
  and real test files, every package carries a deliberate-mutation
  calibration record, the package report is referenced by name, and the
  committed machine-verdict record
  (investigation/audit-replays/replay-verdicts.json) covers every
  calibration id with genuine red→green evidence — red exit 1 for the
  recorded reason, green exit 0, no collection/skip/empty-run masking.

The completion tier is expected to FAIL while packages B and D have not
integrated their pass-2 records. That failure is legitimate and
informative: its message lists exactly which records are still missing.
Do not invent evidence to turn it green — integrate the records.

Fail closed: an unreadable or malformed manifest raises
ManifestUnreadable in every check that touches it. Nothing here skips.

The validation predicates themselves are pure functions in
investigation/replay_core.py; the negative tests below exercise them
against synthetic manifests so a validator hole cannot hide behind the
accidentally-correct real record.
"""

import glob
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "investigation"))
import replay_core  # noqa: E402  (campaign script module, deliberately shared)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "investigation" / "manifest.json"
TEMPLATE_PATH = REPO_ROOT / "investigation" / "findings" / "000-template.md"
FINDINGS_DIR = REPO_ROOT / "investigation" / "findings"
WITNESS_DIR = REPO_ROOT / "investigation" / "witnesses"
CALIBRATIONS_DIR = REPO_ROOT / "investigation" / "calibrations"
VERDICTS_PATH = REPO_ROOT / replay_core.VERDICT_RECORD_PATH

MANIFEST_SCHEMA = "qrp-investigation-manifest/1"
PACKAGE_IDS = ("infra", *replay_core.CAMPAIGN_PACKAGES)
REQUIRED_TOP_LEVEL = (
    "schema", "campaign", "environment", "profiles", "packages",
    "passes", "calibrations", "evidence", "findings", "open_questions",
    "follow_up_leads",
)
# Matches file references like
# investigation/witnesses/a-demographics-full-tie-evidence.txt (the
# witness *directory* itself is checked separately).
WITNESS_REF_RE = re.compile(r"investigation/witnesses/[A-Za-z0-9._-]+")
# The `- **field:**` bullets of the finding template define the field
# names every filed finding document must carry.
TEMPLATE_FIELD_RE = re.compile(r"^\s*-\s*\*\*(.+?):\*\*", re.MULTILINE)


class ManifestUnreadable(Exception):
    """The campaign manifest cannot be loaded as a JSON object."""


def load_manifest(path: Path) -> dict:
    """Read the manifest; any failure is loud (fail closed)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestUnreadable(f"cannot read {path}: {exc}") from exc
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestUnreadable(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ManifestUnreadable(f"{path} must contain a JSON object")
    return manifest


def _manifest() -> dict:
    return load_manifest(MANIFEST_PATH)


def _packages_by_id(manifest: dict) -> dict[str, dict]:
    return {str(p["id"]): p for p in manifest["packages"]}


def _walk_strings(node: object) -> "list[str]":
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for key in node for s in _walk_strings(node[key])]
    if isinstance(node, list):
        return [s for item in node for s in _walk_strings(item)]
    return []


def _revision_resolves(revision: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False)
    return proc.returncode == 0


def _repo_path_exists(repo_relative: str) -> bool:
    """Existence check for repo-relative paths, glob tokens included."""
    if any(char in repo_relative for char in "*?["):
        return bool(glob.glob(str(REPO_ROOT / repo_relative)))
    return (REPO_ROOT / repo_relative).is_file()


def _template_required_fields() -> list[str]:
    return TEMPLATE_FIELD_RE.findall(TEMPLATE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Always-on record validity
# ---------------------------------------------------------------------------


def test_manifest_schema_and_campaign_record() -> None:
    manifest = _manifest()
    assert manifest["schema"] == MANIFEST_SCHEMA
    missing = [key for key in REQUIRED_TOP_LEVEL if key not in manifest]
    assert not missing, f"manifest is missing top-level key(s): {missing}"
    campaign = manifest["campaign"]
    base_sha = campaign["base_sha"]
    assert re.fullmatch(r"[0-9a-f]{40}", base_sha), (
        f"campaign.base_sha must be a 40-hex SHA, got {base_sha!r}")
    ledger = REPO_ROOT / str(campaign["ledger"])
    assert ledger.is_file(), f"campaign.ledger does not exist: {ledger}"
    assert (ledger.name == "README.md"
            and ledger.parent == REPO_ROOT / "investigation")


def test_all_known_packages_are_recorded() -> None:
    packages = _packages_by_id(_manifest())
    unrecorded = [pid for pid in PACKAGE_IDS if pid not in packages]
    assert not unrecorded, (
        f"manifest packages[] does not record: {unrecorded}")


def test_package_branches_and_statuses() -> None:
    manifest = _manifest()
    packages = _packages_by_id(manifest)
    passes = manifest["passes"]
    not_settled: list[str] = []
    for pid in PACKAGE_IDS:
        entry = packages[pid]
        branch = entry.get("branch")
        assert isinstance(branch, str) and branch.strip(), (
            f"package {pid} has no branch recorded")
        status = entry.get("status")
        assert isinstance(status, str) and status.strip(), (
            f"package {pid} has no status recorded")
        if status in replay_core.IN_FLIGHT_STATUSES:
            recorded_passes = any(p.get("package") == pid for p in passes)
            assert not recorded_passes, (
                f"package {pid} records passes in passes[] but its status "
                f"is still {status!r} — the record contradicts itself")
            not_settled.append(f"{pid} (status={status!r})")
    assert not not_settled, (
        "campaign record is not complete — package(s) still unsettled: "
        + "; ".join(not_settled))


def test_pass_entries_are_complete() -> None:
    manifest = _manifest()
    passes = manifest["passes"]
    assert isinstance(passes, list) and passes
    seen_ids: set[str] = set()
    known_packages = set(PACKAGE_IDS) | replay_core.CAMPAIGN_WORKER_IDS
    for entry in passes:
        errors = replay_core.validate_pass_entry(entry, known_packages)
        assert not errors, "; ".join(errors)
        pass_id = str(entry.get("id"))
        assert pass_id not in seen_ids, f"duplicate pass id {pass_id!r}"
        seen_ids.add(pass_id)


def test_calibration_records_are_schema_valid() -> None:
    manifest = _manifest()
    calibrations = manifest["calibrations"]
    assert isinstance(calibrations, list)
    seen_ids: set[str] = set()
    for raw in calibrations:
        record, errors = replay_core.parse_record(raw)
        assert record is not None, (
            f"invalid calibration record {raw!r}: {list(errors)}")
        assert record.id not in seen_ids, f"duplicate calibration {record.id!r}"
        seen_ids.add(record.id)
        assert record.reverted is True


def test_calibration_references_resolve() -> None:
    manifest = _manifest()
    packages = _packages_by_id(manifest)
    for raw in manifest["calibrations"]:
        record, _ = replay_core.parse_record(raw)
        assert record is not None  # guarded by the schema-valid test
        assert record.package in packages, (
            f"calibration {record.id!r}: unknown package {record.package!r}")
        diff_file = REPO_ROOT / record.diff_path
        assert diff_file.is_file(), (
            f"calibration {record.id!r}: diff {record.diff_path!r} missing")
        assert CALIBRATIONS_DIR in diff_file.parents or (
            diff_file.parent == REPO_ROOT / "investigation" / "packages"), (
            f"calibration {record.id!r}: diff {record.diff_path!r} lives "
            "outside investigation/calibrations/ (or the recorded legacy "
            "packages/ location)")
        assert diff_file.resolve().is_relative_to(REPO_ROOT.resolve())
        assert _revision_resolves(record.base_revision), (
            f"calibration {record.id!r}: base revision "
            f"{record.base_revision!r} does not resolve")
        tokens = shlex.split(record.failing_command)
        test_files = [t.split("::")[0] for t in tokens
                      if t.startswith("tests/")]
        assert test_files, (
            f"calibration {record.id!r}: failing_command names no test file")
        for tf in test_files:
            assert (REPO_ROOT / tf).is_file(), (
                f"calibration {record.id!r}: test file {tf!r} does not exist")


def test_template_required_fields_are_discoverable() -> None:
    fields = _template_required_fields()
    assert len(fields) >= 10, (
        f"finding template yields only {len(fields)} fields: {fields}")
    for essential in ("id", "witness/repro command", "mechanism",
                      "red/green commands+results", "disclosure status"):
        assert essential in fields, (
            f"finding template lost the {essential!r} field")


def test_findings_have_documents_with_template_fields() -> None:
    manifest = _manifest()
    findings = manifest["findings"]
    assert isinstance(findings, list)  # may legitimately be empty (pre-arbiter)
    required = _template_required_fields()
    for entry in findings:
        assert isinstance(entry, dict), f"malformed finding entry: {entry!r}"
        finding_id = entry.get("id")
        assert isinstance(finding_id, str) and finding_id.strip(), (
            f"finding entry without id: {entry!r}")
        docs = sorted(FINDINGS_DIR.glob(f"{finding_id}-*.md"))
        assert docs, (
            f"finding {finding_id!r} has no document "
            f"{FINDINGS_DIR / f'{finding_id}-<slug>.md'}")
        for doc in docs:
            text = doc.read_text(encoding="utf-8")
            absent = [f for f in required if f"**{f}:**" not in text]
            assert not absent, (
                f"finding document {doc.name} is missing template "
                f"field(s): {absent}")


def test_witness_references_exist_on_disk() -> None:
    manifest = _manifest()
    referenced: set[str] = set()
    for text in _walk_strings(manifest):
        referenced.update(WITNESS_REF_RE.findall(text))
    for md in (REPO_ROOT / "investigation").rglob("*.md"):
        referenced.update(WITNESS_REF_RE.findall(md.read_text(encoding="utf-8")))
    assert referenced, (
        "no witness path is referenced anywhere in the campaign record; "
        "the witnesses are the evidence, so this smells like a lost "
        "reference")
    for ref in sorted(referenced):
        path = REPO_ROOT / ref
        assert path.is_file(), f"referenced witness does not exist: {ref}"


def test_ledger_tree_present() -> None:
    assert MANIFEST_PATH.is_file()
    assert TEMPLATE_PATH.is_file(), (
        "investigation/findings/000-template.md is the field contract for "
        "findings; it must not go missing")
    assert WITNESS_DIR.is_dir()
    assert CALIBRATIONS_DIR.is_dir()
    assert any(CALIBRATIONS_DIR.glob("*.patch")), (
        "investigation/calibrations/ holds no patch files despite "
        "calibrations[] being populated")


# ---------------------------------------------------------------------------
# Strict completion tier (red until the parent integrates the pass records)
# ---------------------------------------------------------------------------


def test_campaign_record_is_strictly_complete() -> None:
    manifest = _manifest()
    problems = replay_core.completion_problems(
        manifest, path_exists=_repo_path_exists,
        revision_resolves=_revision_resolves)
    assert not problems, (
        "campaign record is not complete — remaining record requirements:\n"
        + "\n".join(f"- {problem}" for problem in problems))


def test_calibration_replays_have_committed_genuine_verdicts() -> None:
    manifest = _manifest()
    ids, error = replay_core.manifest_calibration_ids(manifest)
    assert not error, f"manifest unusable for verdict coverage: {error}"
    assert VERDICTS_PATH.is_file(), (
        f"no committed machine-verdict record at {replay_core.VERDICT_RECORD_PATH}"
        " — every calibration needs executed red→green evidence, not prose")
    verdicts = json.loads(VERDICTS_PATH.read_text(encoding="utf-8"))
    problems = replay_core.verdict_coverage_problems(ids, verdicts)
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# Negative validator self-tests (synthetic; the predicates must reject
# absent, fabricated, and malformed fields — a validator that only ever
# saw the real record proves nothing)
# ---------------------------------------------------------------------------


def _valid_pass_entry() -> dict:
    return {"id": "x-1", "package": "A", "command": "python -m pytest t.py",
            "revision": "aaaaaaaa", "result": "pass", "detail": "ok",
            "duration_seconds": None}


def _synthetic_manifest() -> dict:
    """Minimal manifest that satisfies completion_problems by construction."""
    packages = [
        {"id": pid, "branch": f"inv/x/{pid.lower()}",
         "status": "pass2-complete", "notes": "recorded"}
        for pid in replay_core.CAMPAIGN_PACKAGES
    ]
    passes = []
    for pid in replay_core.CAMPAIGN_PACKAGES:
        for number in (1, 2):
            marker = "pass2" if number == 2 else "pass1"
            passes.append({
                "id": f"{pid.lower()}-{marker}", "package": pid,
                "command": "python -m pytest tests/test_x.py",
                "revision": "aaaaaaaa", "result": "pass",
                "detail": f"run {number} recorded in "
                          f"investigation/packages/{pid}.md",
                "duration_seconds": 1.0,
            })
    calibrations = [{"id": f"{pid.lower()}-1", "package": pid}
                    for pid in replay_core.CAMPAIGN_PACKAGES]
    return {"schema": MANIFEST_SCHEMA, "packages": packages,
            "passes": passes, "calibrations": calibrations}


def _fake_path_exists(repo_relative: str) -> bool:
    return repo_relative == "tests/test_x.py" or repo_relative.startswith(
        "investigation/packages/")


def _fake_revision_resolves(revision: str) -> bool:
    return revision == "aaaaaaaa"


def _completion_problems(manifest: dict) -> tuple[str, ...]:
    return replay_core.completion_problems(
        manifest, path_exists=_fake_path_exists,
        revision_resolves=_fake_revision_resolves)


def test_synthetic_record_is_complete_by_construction() -> None:
    assert _completion_problems(_synthetic_manifest()) == ()


def test_missing_pass2_entry_is_flagged_with_exact_requirement() -> None:
    manifest = _synthetic_manifest()
    manifest["passes"] = [p for p in manifest["passes"] if p["id"] != "f-pass2"]
    problems = _completion_problems(manifest)
    flagged = [p for p in problems if p.startswith("package F")]
    assert flagged, problems
    assert "pass-2" in flagged[0]
    assert "campaign_pass" in flagged[0]
    assert "investigation/packages/F.md" in flagged[0]


def test_stale_status_is_flagged() -> None:
    manifest = _synthetic_manifest()
    for entry in manifest["packages"]:
        if entry["id"] == "C":
            entry["status"] = "pass1-complete"
    assert any("stale status" in problem and "package C" in problem
               for problem in _completion_problems(manifest))


def test_unsettled_status_is_flagged() -> None:
    manifest = _synthetic_manifest()
    for entry in manifest["packages"]:
        if entry["id"] == "B":
            entry["status"] = "in_progress"
    assert any("not settled" in problem and "package B" in problem
               for problem in _completion_problems(manifest))


def test_unresolvable_revision_and_missing_test_file_are_flagged() -> None:
    manifest = _synthetic_manifest()
    for record in manifest["passes"]:
        if record["id"] == "b-pass2":
            record["revision"] = "deadbeef"
            record["command"] = "python -m pytest tests/gone.py"
    problems = _completion_problems(manifest)
    assert any("resolvable commit" in problem for problem in problems), problems
    assert any("nonexistent test path" in problem
               and "tests/gone.py" in problem for problem in problems), problems


def test_missing_calibration_and_missing_report_are_flagged() -> None:
    manifest = _synthetic_manifest()
    manifest["calibrations"] = [c for c in manifest["calibrations"]
                                if c["package"] != "D"]
    problems = replay_core.completion_problems(
        manifest,
        path_exists=lambda token: (token != "investigation/packages/E.md"
                                   and _fake_path_exists(token)),
        revision_resolves=_fake_revision_resolves)
    assert any("no calibration record" in problem and "package D" in problem
               for problem in problems), problems
    assert any("does not exist" in problem
               and "investigation/packages/E.md" in problem
               for problem in problems), problems


def test_unreferenced_report_is_flagged() -> None:
    manifest = _synthetic_manifest()
    for record in manifest["passes"]:
        if record["package"] == "C":
            record["detail"] = "no reference here"
    for entry in manifest["packages"]:
        if entry["id"] == "C":
            entry["notes"] = "no reference either"
    problems = _completion_problems(manifest)
    assert any("not referenced" in problem and "package C" in problem
               for problem in problems), problems


def test_pass_entry_conventions() -> None:
    assert replay_core.validate_pass_entry(_valid_pass_entry(), {"A"}) == ()
    assert replay_core.validate_pass_entry("not an entry", {"A"})
    number, error = replay_core.campaign_pass_number(
        {"id": "pkg-b-pass2-quick-1"})
    assert (number, error) == (2, "")
    number, error = replay_core.campaign_pass_number({"id": "pkg-b-quick-1"})
    assert (number, error) == (1, "")
    number, error = replay_core.campaign_pass_number(
        {"id": "x-pass2", "campaign_pass": 1})
    assert (number, error) == (1, "")
    number, error = replay_core.campaign_pass_number(
        {"id": "x", "campaign_pass": 0})
    assert number is None and "campaign_pass" in error


def test_pass_entry_absent_fields_are_rejected() -> None:
    for field in ("id", "package", "command", "revision", "result", "detail",
                  "duration_seconds"):
        entry = {key: value for key, value in _valid_pass_entry().items()
                 if key != field}
        errors = replay_core.validate_pass_entry(entry, {"A"})
        assert errors and any(field in error for error in errors), (
            field, errors)


def test_pass_entry_fabricated_values_are_rejected() -> None:
    mutations: list[tuple[dict[str, object], str]] = [
        ({"result": "PASS"}, "not in"),
        ({"result": ""}, "non-empty"),
        ({"package": "Z"}, "unknown package"),
        ({"package": ""}, "unknown package"),
        ({"campaign_pass": 3}, "campaign_pass"),
        ({"campaign_pass": "2"}, "campaign_pass"),
        ({"campaign_pass": True}, "campaign_pass"),
        ({"id": ""}, "id"),
        ({"detail": None}, "detail"),
    ]
    for override, needle in mutations:
        entry = {**_valid_pass_entry(), **override}
        errors = replay_core.validate_pass_entry(entry, {"A"})
        assert errors and any(needle in error for error in errors), (
            override, errors)


def _genuine_verdict(record_id: str = "a-1") -> dict:
    return {
        "record_id": record_id, "ok": True,
        "red_exit_code": 1, "green_exit_code": 0,
        "red_pattern_matched": True, "worktree_removed": True,
        "steps": [{"step": step, "ok": True, "detail": ""}
                  for step in replay_core.VERDICT_STEPS],
        "red_tail": "FAILED tests/test_x.py::test_y - AssertionError: boom\n"
                    "1 failed in 0.1s",
        "green_tail": "1 passed in 0.2s",
    }


def test_genuine_verdict_passes_and_mutations_are_rejected() -> None:
    assert replay_core.validate_replay_verdict(_genuine_verdict()) == ()
    mutations: list[tuple[dict, str]] = [
        ({"ok": False}, "ok must be true"),
        ({"red_exit_code": 0}, "red_exit_code"),
        ({"red_exit_code": 2}, "red_exit_code"),
        ({"red_exit_code": 5}, "red_exit_code"),
        ({"red_exit_code": "1"}, "integer"),
        ({"green_exit_code": 1}, "green_exit_code"),
        ({"red_pattern_matched": False}, "red_pattern_matched"),
        ({"worktree_removed": False}, "worktree_removed"),
        ({"steps": [{"step": "red", "ok": True, "detail": ""}]}, "steps"),
        ({"steps": [{"step": step, "ok": False, "detail": "boom"}
                    for step in replay_core.VERDICT_STEPS]}, "not ok"),
        ({"red_tail": "collected 0 items"}, "masked"),
        ({"red_tail": "all quiet"}, "no failure evidence"),
        ({"green_tail": "1 passed, 1 skipped in 0.2s"}, "masked"),
        ({"green_tail": "nothing ran"}, "no passing test"),
    ]
    for override, needle in mutations:
        verdict = {**_genuine_verdict(), **override}
        errors = replay_core.validate_replay_verdict(verdict)
        assert errors and any(needle in error for error in errors), (
            override, errors)
    missing = replay_core.validate_replay_verdict({"record_id": "a-1"})
    assert any("missing field" in error for error in missing), missing
    assert replay_core.validate_replay_verdict("not a verdict")


def test_verdict_coverage_must_match_records_exactly() -> None:
    ids = ["a-1", "b-1"]
    problems = replay_core.verdict_coverage_problems(
        ids, [_genuine_verdict("a-1")])
    assert any("no committed replay verdict" in problem and "b-1" in problem
               for problem in problems), problems
    problems = replay_core.verdict_coverage_problems(
        ids, [_genuine_verdict("a-1"), _genuine_verdict("z-9")])
    assert any("stale evidence" in problem and "z-9" in problem
               for problem in problems), problems
    problems = replay_core.verdict_coverage_problems(
        ids, [_genuine_verdict("a-1"), _genuine_verdict("a-1")])
    assert any("duplicate verdict" in problem for problem in problems), problems
    assert replay_core.verdict_coverage_problems(ids, {"not": "a list"}) == (
        "replay verdicts must be a list of verdict objects",)


def test_manifest_loader_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ManifestUnreadable, match="cannot read"):
        load_manifest(tmp_path / "absent.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestUnreadable, match="not valid JSON"):
        load_manifest(broken)
    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(ManifestUnreadable, match="JSON object"):
        load_manifest(array)
    _, error = replay_core.manifest_calibration_ids([])
    assert error
