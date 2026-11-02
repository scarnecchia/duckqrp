"""Package F — doctor, fingerprint, show and browse behavior (AC.7).

Synthetic local fixtures only:

* `fingerprint()` — repeatable, 8 hex chars, hashes CONTENT so a copy
  at a different path digests identically, changes when a shipped
  .py/.sql file changes in the copy, and ignores non-code files. The
  copy is scanned by patching the module's __file__ attribute in THIS
  process; src/ is never modified.
* `doctor()` — runs the whole pipeline on generated data; must exit 0.
* broken study dicts and broken input dirs — the diagnostic must name
  the fault the test caused.
* `show`/`browse` — schema and row preservation through the viewer
  path against a REAL tiny-pipeline output directory.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pytest

from qrp import browse
from qrp import run
from qrp import selfcheck
from qrp import show
from qrp.config import load_study_dict

from .harness import full_pipeline_run
from .test_pkg_f_meta_helpers import wildcard_study
from .synth import hand_fixture

# ---------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------


def test_fingerprint_is_repeatable_and_short_hex() -> None:
    a = selfcheck.fingerprint()
    b = selfcheck.fingerprint()
    assert a == b
    assert re.fullmatch(r"[0-9a-f]{8}", a), a


def _copy_package(tmp_path: Path, name: str = "qrp-copy") -> Path:
    """A copy of the installed package directory, at a DIFFERENT path,
    for content-hash experiments. src/ is never touched."""
    src = Path(selfcheck.__file__).resolve().parent
    dst = tmp_path / name
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst,
                    ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def _fingerprint_over(root: Path) -> str:
    """Run the shipped fingerprint() body with __file__ pointing into
    the copy — the same code, a different layout."""
    saved = selfcheck.__file__
    try:
        selfcheck.__file__ = str(root / "selfcheck.py")
        return selfcheck.fingerprint()
    finally:
        selfcheck.__file__ = saved


def test_fingerprint_is_layout_independent(tmp_path: Path) -> None:
    """An equivalent layout (same content, different path) must
    fingerprint identically — the hash is over sorted relative CONTENT,
    not where the files live."""
    copy = _copy_package(tmp_path)
    assert _fingerprint_over(copy) == selfcheck.fingerprint()


def test_fingerprint_changes_when_code_changes(tmp_path: Path) -> None:
    """A hand-applied patch must be visible: editing one .py file (in
    the copy) changes the digest; so does removing a shipped .sql."""
    copy = _copy_package(tmp_path)
    baseline = _fingerprint_over(copy)

    victim = copy / "sqlsplit.py"
    victim.write_text(victim.read_text() + "\n# patched\n")
    assert _fingerprint_over(copy) != baseline, (
        "editing a shipped .py did not change the fingerprint")

    copy2 = _copy_package(tmp_path, "qrp-copy-2")
    sql = next((copy2 / "sql").glob("*.sql"))
    sql.unlink()
    assert _fingerprint_over(copy2) != selfcheck.fingerprint(), (
        "removing a shipped .sql did not change the fingerprint")


def test_fingerprint_ignores_non_code_files(tmp_path: Path) -> None:
    """The hash covers .py and .sql only: adding a .txt must not move
    it (documented by the docstring: 'every shipped .py and .sql')."""
    copy = _copy_package(tmp_path)
    baseline = _fingerprint_over(copy)
    (copy / "notes.txt").write_text("not code")
    (copy / "sql" / "notes.txt").write_text("also not code")
    assert _fingerprint_over(copy) == baseline


# ---------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------


def test_doctor_passes_end_to_end(capsys: pytest.CaptureFixture[str]) -> None:
    """qrp doctor runs the real pipeline on generated data and checks
    it. No site data; the working tree is untouched."""
    code = selfcheck.doctor()
    out = capsys.readouterr().out
    assert code == 0, out
    assert "All" in out and "checks passed" in out


# ---------------------------------------------------------------------
# Broken inputs: assert the diagnostic the test caused
# ---------------------------------------------------------------------


def _base_study_dict() -> dict[str, Any]:
    return {
        "qrp_parameters": [{"type": 2, "runid": "x",
                            "startdate": "2011-01-01",
                            "enddate": "2014-12-31"}],
        "cohortfile": [{"cohortgrp": "g", "enrollmentnum": 1,
                        "coverage": "MD"}],
        "type2file": [{"group": "g"}],
        "cohortcodes": [],
    }


@pytest.mark.parametrize("mutate,fragment", [
    (lambda d: d.pop("qrp_parameters"), "query period"),
    (lambda d: d["qrp_parameters"][0].__setitem__("type", 7), "Type 2"),
    (lambda d: d.pop("cohortfile"), "no cohorts"),
    (lambda d: d["cohortfile"][0].__setitem__("coverage", "QQ"),
     "coverage must be MD, M or D"),
])
def test_broken_study_dict_diagnoses_the_fault(
        mutate: Any, fragment: str) -> None:
    d = json.loads(json.dumps(_base_study_dict()))
    mutate(d)
    with pytest.raises(Exception) as excinfo:
        load_study_dict(d)
    assert fragment.lower() in str(excinfo.value).lower(), (
        f"diagnostic did not name the caused fault ({fragment!r}): "
        f"{excinfo.value}")


def test_broken_input_dir_names_the_missing_tables(tmp_path: Path) -> None:
    """An input dir missing required tables must fail naming them and
    pointing at --table-map — the exact message a data partner needs."""
    (tmp_path / "enrollment").mkdir()
    study = wildcard_study(hand_fixture()[1])
    with pytest.raises(FileNotFoundError) as excinfo:
        run(study, str(tmp_path), threads=1, verbose=False)
    message = str(excinfo.value)
    for table in ("demographic", "dispensing", "diagnosis"):
        assert table in message, message
    assert "table-map" in message, message


# ---------------------------------------------------------------------
# show / browse smoke against a REAL tiny-pipeline output directory
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class _OutFixture:
    out: Path
    tables: list[str]
    counts: dict[str, int]


def _real_output(tmp_path: Path) -> _OutFixture:
    dataset, study0 = hand_fixture()
    study = wildcard_study(study0)
    out = tmp_path / "results"
    with full_pipeline_run(dataset, study, output_dir=out):
        pass
    manifest = json.loads((out / "manifest.json").read_text())
    con = duckdb.connect()
    try:
        counts: dict[str, int] = {}
        for name, entry in manifest["tables"].items():
            base = out / entry["library"] / entry["file"]
            src = (f"{base}/**/*.parquet" if base.is_dir()
                   else str(base.with_suffix(".parquet")))
            rows = con.execute(
                f"SELECT count(*) FROM read_parquet('{src}')").fetchone()
            assert rows is not None
            counts[name] = int(rows[0])
    finally:
        con.close()
    return _OutFixture(out=out, tables=sorted(manifest["tables"]),
                       counts=counts)


def test_show_available_lists_manifest_tables(tmp_path: Path) -> None:
    fx = _real_output(tmp_path)
    assert show.available(fx.out) == fx.tables
    assert fx.tables, "no output tables at all"


def test_show_listing_reports_true_row_counts(tmp_path: Path) -> None:
    fx = _real_output(tmp_path)
    text = show.show(fx.out)
    for name, n in fx.counts.items():
        assert f"{name:<24}" in text, f"{name} missing from listing"
        assert f"{n:,}" in text, (
            f"listing shows a count other than {n:,} for {name}:\n{text}")


def test_show_single_table_preserves_rows(tmp_path: Path) -> None:
    """Row preservation through the viewer path: the rendered table
    must contain the parquet values verbatim (exact ints; dates; the
    row count in the header)."""
    fx = _real_output(tmp_path)
    name = "attrition" if "attrition" in fx.counts else fx.tables[0]
    text = show.show(fx.out, name)
    n = fx.counts[name]
    assert text.startswith(f"{name}  —  {n:,} rows"), text[:80]

    entry = json.loads((fx.out / "manifest.json").read_text())["tables"]
    e = entry[name]
    base = fx.out / e["library"] / e["file"]
    src = (f"{base}/**/*.parquet" if base.is_dir()
           else str(base.with_suffix(".parquet")))
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT * FROM read_parquet('{src}') LIMIT 3").fetchall()
    finally:
        con.close()
    for row in rows:
        for value in row:
            if isinstance(value, int):
                assert f"{value:,}" in text or str(value) in text, (
                    f"integer {value} missing from rendered {name}:\n"
                    f"{text[:600]}")
            elif isinstance(value, str) and value:
                # show() renders for a TERMINAL: cells are truncated at
                # 30 characters (show._render, width=30). Compare the
                # rendered prefix, not the whole string.
                assert value[:30] in text, (
                    f"string {value[:30]!r} missing from rendered "
                    f"{name}")


def test_show_where_filter_counts(tmp_path: Path) -> None:
    fx = _real_output(tmp_path)
    name = "attrition"
    filtered = show.show(fx.out, name, where="lower(\"group\") = 'cohort_a'")
    assert "rows" in filtered
    con = duckdb.connect()
    entry = json.loads((fx.out / "manifest.json").read_text())["tables"][name]
    base = fx.out / entry["library"] / entry["file"]
    src = (f"{base}/**/*.parquet" if base.is_dir()
           else str(base.with_suffix(".parquet")))
    try:
        want = con.execute(
            f"SELECT count(*) FROM read_parquet('{src}') "
            "WHERE lower(\"group\") = 'cohort_a'").fetchone()
    finally:
        con.close()
    assert want is not None
    assert f"{int(want[0]):,} rows" in filtered, filtered[:120]


def test_show_diagnostics(tmp_path: Path) -> None:
    fx = _real_output(tmp_path)
    unknown = show.show(fx.out, "no_such_table")
    assert "No table called 'no_such_table'" in unknown
    assert "Available:" in unknown
    empty = show.show(tmp_path / "does-not-exist")
    assert "No results found" in empty
    assert show.available(tmp_path / "does-not-exist") == []


def test_to_csv_preserves_row_counts(tmp_path: Path) -> None:
    fx = _real_output(tmp_path)
    dest = tmp_path / "csv"
    written = show.to_csv(fx.out, dest)
    assert written
    con = duckdb.connect()
    try:
        for path in written:
            name = path.stem
            if name in ("runtimes", "signature"):
                continue
            n = con.execute(
                f"SELECT count(*) FROM read_csv('{path}', header=true)"
            ).fetchone()
            assert n is not None
            assert int(n[0]) == fx.counts[name], (
                f"csv for {name} has {n[0]} rows, parquet has "
                f"{fx.counts[name]}")
    finally:
        con.close()


def test_table_text_renders_every_column(tmp_path: Path) -> None:
    fx = _real_output(tmp_path)
    entry = json.loads((fx.out / "manifest.json").read_text())["tables"]
    name = "attrition"
    e = entry[name]
    base = fx.out / e["library"] / e["file"]
    src = (f"{base}/**/*.parquet" if base.is_dir()
           else str(base.with_suffix(".parquet")))
    con = duckdb.connect()
    try:
        text = show.table_text(con, f"read_parquet('{src}')", name)
        cols = [d[0] for d in con.execute(
            f"SELECT * FROM read_parquet('{src}') LIMIT 0").description]
        for col in cols:
            assert col in text, f"column {col} missing from table_text"
        n = con.execute(
            f"SELECT count(*) FROM read_parquet('{src}')").fetchone()
        assert n is not None
        assert f"{int(n[0]):,} rows" in text
    finally:
        con.close()


# ---------------------------------------------------------------------
# browse: the filter/sort logic of the DirectoryTree, headlessly
# ---------------------------------------------------------------------


def _stub(dirs_only: bool, suffixes: tuple[str, ...]) -> Any:
    """browse._Tree.filter_paths is a pure function of (self._dirs_only,
    self._suffixes, paths). Call it unbound on a stub so no Textual app
    is needed."""
    stub = type("_StubTree", (), {})()
    stub._dirs_only = dirs_only
    stub._suffixes = suffixes
    return stub


def test_browse_tree_hides_hidden_and_sorts_dirs_first(
        tmp_path: Path) -> None:
    (tmp_path / "zdir").mkdir()
    (tmp_path / "adir").mkdir()
    (tmp_path / "b.txt").write_text("x")
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / ".hidden").write_text("no")
    all_paths = [tmp_path / "b.txt", tmp_path / ".hidden",
                 tmp_path / "zdir", tmp_path / "adir", tmp_path / "a.json"]
    # Directory mode (SCDM root / output folders): DIRECTORIES ONLY.
    # This is the documented contract of the two modes: "Selecting a
    # folder means use this"; files belong to file mode.
    dirs = list(browse._Tree.filter_paths(_stub(True, ()), all_paths))
    assert [p.name for p in dirs] == ["adir", "zdir"], (
        "dirs-only mode shows only directories, dirs first")
    # File mode (study JSON): files visible too, still sorted, hidden
    # still hidden.
    both = list(browse._Tree.filter_paths(_stub(False, ()), all_paths))
    assert Path(tmp_path / ".hidden") not in both, "hidden entry shown"
    assert [p.name for p in both] == ["adir", "zdir", "a.json", "b.txt"], (
        "dirs-first, then files, case-insensitive by name")


def test_browse_tree_suffix_filter(tmp_path: Path) -> None:
    (tmp_path / "study.json").write_text("{}")
    (tmp_path / "study.JSON").write_text("{}")
    (tmp_path / "data.parquet").write_bytes(b"")
    out = list(browse._Tree.filter_paths(
        _stub(False, (".json",)),
        [tmp_path / "study.json", tmp_path / "study.JSON",
         tmp_path / "data.parquet"]))
    assert {p.name for p in out} == {"study.json", "study.JSON"}, (
        "suffix filter must be case-insensitive")
