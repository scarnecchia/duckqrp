"""AC.8: validate investigation/manifest.json as the record of truth.

Cheap on purpose — JSON parsing, filesystem existence, and one git
call per base revision; no DuckDB, no Hypothesis. Deliberately part of
the normal quick suite: a manifest that fails this test invalidates
every claim that references it.

What is enforced, per the campaign acceptance criteria:

* schema/version; every known package (infra, A, B, C, D, E, F) has a
  branch and a settled status. A pending/in_progress status is
  tolerated ONLY for packages still in flight (status pending/
  in_progress AND no pass-1 record in passes[]) — D while its worker
  runs, and any other package whose record still lags its merged
  branch (today: F, merged at 3085eb4 with its status still
  "pending"; only the orchestrator may refresh packages[], so the
  validator must not lie about it either). A pending status on a
  package that already records passes is a self-contradiction and
  fails loudly.
* every passes[] entry has command/revision/result/detail, a known
  package, one of the recorded result vocabularies, and a
  duration_seconds key (None allowed).
* every calibrations[] entry passes the shared record parser
  (investigation/replay_core.parse_record — the same validation the
  replay machinery applies) and its references resolve: diff file
  exists inside the repo, base revision resolves as a commit, the
  failing command names at least one existing test file, the package
  id is known.
* every findings[] entry has a matching document under
  investigation/findings/ carrying every template-required field.
  The array may legitimately be empty until the findings arbiter
  merges.
* every witness path referenced in the manifest or in the
  investigation tree exists on disk.
"""

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

MANIFEST_SCHEMA = "qrp-investigation-manifest/1"
PACKAGE_IDS = ("infra", "A", "B", "C", "D", "E", "F")
PASS_RESULTS = frozenset({"pass", "confirmed", "red then green"})
IN_FLIGHT_STATUSES = frozenset({"pending", "in_progress"})
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


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


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


def _template_required_fields() -> list[str]:
    return TEMPLATE_FIELD_RE.findall(TEMPLATE_PATH.read_text(encoding="utf-8"))


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
    in_flight: list[str] = []
    for pid in PACKAGE_IDS:
        entry = packages[pid]
        branch = entry.get("branch")
        assert isinstance(branch, str) and branch.strip(), (
            f"package {pid} has no branch recorded")
        status = entry.get("status")
        if status in IN_FLIGHT_STATUSES:
            recorded_passes = any(p.get("package") == pid for p in passes)
            assert not recorded_passes, (
                f"package {pid} records passes in passes[] but its status "
                f"is still {status!r} — the record contradicts itself")
            in_flight.append(f"{pid} (status={status!r})")
        else:
            assert isinstance(status, str) and status.strip(), (
                f"package {pid} has no status recorded")
    if in_flight:
        pytest.skip(
            "still in flight; AC.8 completeness assertions deferred until "
            "each package records its pass-1 run: " + "; ".join(in_flight))


def test_pass_entries_are_complete() -> None:
    manifest = _manifest()
    passes = manifest["passes"]
    assert isinstance(passes, list) and passes
    seen_ids: set[str] = set()
    for entry in passes:
        pass_id = entry.get("id")
        assert isinstance(pass_id, str) and pass_id.strip(), (
            f"pass entry without id: {entry!r}")
        assert pass_id not in seen_ids, f"duplicate pass id {pass_id!r}"
        seen_ids.add(pass_id)
        for field in ("command", "revision", "result", "detail"):
            value = entry.get(field)
            assert isinstance(value, str) and value.strip(), (
                f"pass {pass_id!r}: field {field!r} must be a non-empty "
                f"string, got {value!r}")
        assert entry["result"] in PASS_RESULTS, (
            f"pass {pass_id!r}: result {entry['result']!r} not in "
            f"{sorted(PASS_RESULTS)}")
        assert "duration_seconds" in entry, (
            f"pass {pass_id!r}: duration_seconds key missing (None allowed)")
        package = entry.get("package")
        assert package in PACKAGE_IDS, (
            f"pass {pass_id!r}: unknown package {package!r}")


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
