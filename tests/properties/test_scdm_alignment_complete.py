"""SCDM 8.2.2 full-surface review — completeness validators.

Machine checks for the follow-up review's acceptance criteria (cheap,
always-on: file reads and one git subprocess; no DuckDB, no
Hypothesis):

* AC.1 — every table in qrp.scdm.SCDM has a row in the audit matrix
  with a verdict and a reference citation.
* AC.2 — the review's whole diff from its recorded base touches only
  tests/ and investigation/ paths (no production src/ or tools/
  change).
* AC.3 — every actionable matrix row (misaligned-fixed, probe-needed)
  resolves to a committed test, probe, or decision brief, and every
  referenced tests/ file and test name exists.
* AC.5 — every finding document (template excluded; B2 covered by its
  2026-11-03 addendum) carries an SCDM 8.2.2 alignment note.
* AC.6 — FINAL-VALIDATION.md carries the SCDM alignment review section
  and FINAL-VALIDATION-STE.md exists as the STE rendering, with a
  header naming the original and the same final-revision SHA in both.

The audit matrix format is fixed by investigation/packages/
SCDM-ALIGNMENT.md's "## Audit matrix" table: one markdown row per item
with columns | table | reference consulted | fixtures/builders checked
| item | verdict | outcome (test id or probe path) |.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AUDIT = _REPO_ROOT / "investigation" / "packages" / "SCDM-ALIGNMENT.md"
_FINDINGS = _REPO_ROOT / "investigation" / "findings"
_FINAL = _REPO_ROOT / "investigation" / "packages" / "FINAL-VALIDATION.md"
_STE = _REPO_ROOT / "investigation" / "packages" / "FINAL-VALIDATION-STE.md"
# The discovery tip this review started from (recorded in SCDM-ALIGNMENT.md).
_BASE_SHA = "7165d77"
# The review's final records commit: the scope check is a closed-range
# historical check. Later operator rulings (ARBITRATION.md, 2026-11-03)
# authorize production changes for the named items; those live beyond
# _REVIEW_TIP and are intentionally outside this check.
_REVIEW_TIP = "844a1da"

_VERDICTS = ("aligned", "aligned-with-annotation", "misaligned-fixed",
             "probe-needed")
_ROW_RE = re.compile(r"^\|[^|]+\|[^|]+\|[^|]+\|[^|]+\|[^|]+\|[^|]+\|$")


def _matrix_rows() -> list[list[str]]:
    """Cells of every data row of the audit matrix table."""
    lines = _AUDIT.read_text(encoding="utf-8").splitlines()
    rows: list[list[str]] = []
    in_table = False
    for line in lines:
        if line.startswith("| table | reference consulted"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            if set(line) <= {"|", "-", " ", ":"}:   # separator row
                continue
            if _ROW_RE.match(line):
                rows.append([c.strip() for c in line.strip("|").split("|")])
    assert rows, "audit matrix table not found or empty"
    return rows


def _cells(rows: list[list[str]], idx: int) -> list[str]:
    return [r[idx] for r in rows]


# ---------------------------------------------------------------------------
# AC.1 — audit rows per table
# ---------------------------------------------------------------------------


def test_scdm_audit_rows_present() -> None:
    from qrp.scdm import SCDM

    rows = _matrix_rows()
    assert len(rows[0]) == 6, f"matrix rows must have 6 cells: {rows[0]}"
    by_table: dict[str, list[list[str]]] = {}
    for r in rows:
        assert r[4] in _VERDICTS, f"unknown verdict {r[4]!r} in row: {r}"
        assert r[1], f"reference cell empty in row: {r}"
        assert r[5], f"outcome cell empty in row: {r}"
        by_table.setdefault(r[0], []).append(r)
    missing = [s.name for s in SCDM if s.name not in by_table]
    assert not missing, (
        f"audit matrix has no row for SCDM table(s): {missing}; "
        f"tables covered: {sorted(by_table)}")
    # Every one of the eight pipeline-read tables must carry at least one
    # row whose reference cites a per-table reference file.
    for spec in SCDM:
        assert any(".md" in r[1] or "reference" in r[1].lower()
                   for r in by_table[spec.name]), (
            f"table {spec.name}: no row cites an SCDM reference file")


def test_audit_matrix_records_unresolved_ambiguities() -> None:
    """The three documented ambiguities must stay recorded as
    unresolved, never turned into rules."""
    text = _AUDIT.read_text(encoding="utf-8")
    assert "Unresolved documentation ambiguities" in text
    for needle in ("Encounter unique-row", "ImputedRace", "Result_Loc"):
        assert needle in text, needle


# ---------------------------------------------------------------------------
# AC.2 — scope: tests/ and investigation/ only
# ---------------------------------------------------------------------------


def test_scdm_audit_scope_is_tests_and_investigation_only() -> None:
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{_BASE_SHA}..{_REVIEW_TIP}"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    files = [f for f in proc.stdout.splitlines() if f]
    assert files, "no diff recorded against the audit base — stale base?"
    outside = [f for f in files
               if not (f.startswith("tests/") or f.startswith("investigation/"))]
    assert not outside, (
        f"SCDM review touched paths outside tests/ and investigation/: "
        f"{outside}")
    # The base itself must be recorded in the audit doc.
    assert _BASE_SHA in _AUDIT.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC.3 — actionable items resolve to committed evidence
# ---------------------------------------------------------------------------


def test_scdm_actionable_items_resolve() -> None:
    rows = _matrix_rows()
    actionable = [r for r in rows
                  if r[4] in ("misaligned-fixed", "probe-needed")]
    assert actionable, "no actionable rows found — matrix regressed"
    file_ref = re.compile(r"(?:tests|investigation)/[A-Za-z0-9_./-]+")
    node_ref = re.compile(r"::([A-Za-z0-9_]+)")
    for r in actionable:
        outcome = r[5]
        assert ("test_" in outcome or file_ref.search(outcome)
                or "DB-" in outcome), (
            f"row {r[3]!r}: outcome names no test, committed path, or "
            f"decision brief: {outcome!r}")
        for path in file_ref.findall(outcome):
            assert (_REPO_ROOT / path).exists(), (
                f"row {r[3]!r}: referenced path does not exist: {path}")
        for name in node_ref.findall(outcome):
            # The test name must appear in some tests/ file named in the
            # same outcome cell (or the alignment module).
            haystacks = [(_REPO_ROOT / p) for p in file_ref.findall(outcome)]
            haystacks.append(_REPO_ROOT / "tests/properties/"
                             "test_scdm_alignment.py")
            assert any(h.is_file() and name in h.read_text(encoding="utf-8")
                       for h in haystacks), (
                f"row {r[3]!r}: test name {name!r} not found in the "
                "referenced files")


def test_scdm_alignment_tests_exist_and_are_named_in_the_matrix() -> None:
    text = _AUDIT.read_text(encoding="utf-8")
    module = (_REPO_ROOT / "tests/properties/test_scdm_alignment.py")
    assert module.is_file()
    names = re.findall(r"^def (test_[A-Za-z0-9_]+)", module.read_text(
        encoding="utf-8"), re.MULTILINE)
    assert len(names) >= 8, names
    for name in names:
        assert name in text, (
            f"boundary test {name} is not named in the audit matrix")


# ---------------------------------------------------------------------------
# AC.5 — per-finding alignment notes
# ---------------------------------------------------------------------------


def test_scdm_finding_notes_present() -> None:
    docs = sorted(p for p in _FINDINGS.glob("*.md")
                  if p.name != "000-template.md")
    assert len(docs) >= 12, docs
    note = re.compile(r"SCDM 8\.2\.2 (?:data-model )?alignment")
    for doc in docs:
        assert note.search(doc.read_text(encoding="utf-8")), (
            f"{doc.name} carries no SCDM 8.2.2 alignment note")


# ---------------------------------------------------------------------------
# AC.6 — final report section + STE copy
# ---------------------------------------------------------------------------


def test_scdm_final_report_and_ste_copy_present() -> None:
    final = _FINAL.read_text(encoding="utf-8")
    assert "## SCDM alignment review" in final, (
        "FINAL-VALIDATION.md lacks the SCDM alignment review section")
    assert _STE.is_file(), "FINAL-VALIDATION-STE.md does not exist"
    ste_head = "\n".join(_STE.read_text(encoding="utf-8").splitlines()[:12])
    assert "Simplified Technical English rendering" in ste_head, (
        "STE copy header does not state its relationship to the original")
    assert "FINAL-VALIDATION.md" in ste_head
    rev = re.compile(r"Final revision: ([0-9a-f]{7,40})")
    final_rev = rev.findall(final)
    ste_rev = rev.findall(_STE.read_text(encoding="utf-8"))
    assert final_rev and ste_rev, (final_rev, ste_rev)
    assert final_rev[0] == ste_rev[0], (final_rev, ste_rev)
