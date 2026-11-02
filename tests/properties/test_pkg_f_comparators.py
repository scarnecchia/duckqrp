"""Package F — comparator correctness for tools/parity_compare.py.

A checker must be PROVEN before it judges anything else (AC.7), so this
family runs before any metamorphic comparison relies on the comparator.
Two properties:

* detection — one targeted corruption per paired tree; every corruption
  must be reported with its classification (MISSING TABLE / KEY
  MISMATCH / ROW COUNT / MISSING COLUMN / VALUE) and exit code 1;
* acceptance — paired changes the tool documents as equivalent (row
  permutation, type-canonical forms, missing-value sentinels,
  within-tolerance float differences, note-only extra column/table)
  must exit 0.

The numeric tolerance boundary is pinned EXACTLY by deterministic
examples with tolerance 0.5, and mirrored at the same_value() unit
level. The oracle helpers live in this file directly: a subpackage
under a CLI-package directory gives mypy two module names for one
file, so the former oracles/ layout was folded in here.

Tolerance boundary values are exact in float64 (4.0, 7.5, 8.0, 8.5)
with tolerance 0.5; the rule is |a-b| <= tol * max(1, |a|, |b|), whose
boundary for a=4.0 sits at b=8.0 because slack grows with max(|a|,|b|):

    4.0 vs 7.5 -> diff 3.5 <= 3.75  accepted (epsilon-below)
    4.0 vs 8.0 -> diff 4.0 == 4.0   accepted (exactly-at, rule is <=)
    4.0 vs 8.5 -> diff 4.5 >  4.25  reported (epsilon-above)
"""

from __future__ import annotations

import contextlib
import csv as _csv
import importlib.util
import io
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from hypothesis import event, example, given
from hypothesis import strategies as st

# =====================================================================
# Oracle: load the production comparator. tools/ is not an installable
# package, so the test loads the shipped file directly — the same
# bytes the CLI runs, not a reimplementation.
# =====================================================================

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_tool(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(
        f"f_oracle_{name}", _REPO_ROOT / "tools" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(f"f_oracle_{name}", module)
    spec.loader.exec_module(module)
    return module


parity_compare = _load_tool("parity_compare")

Mutate = Callable[[str, "MiniTable"],
                  "tuple[list[str], list[list[str]]] | object"]
# Mutations return None for "leave this table alone" and DROP to remove
# the file from the tree entirely (the missing-table corruption).
DROP: object = object()


@dataclass(frozen=True)
class MiniTable:
    """A minimal table shaped like one parity_compare knows how to key.

    Cells are pre-formatted strings exactly as a CSV would carry them,
    so a test controls the literal bytes the comparator reads.
    """

    name: str                  # logical table name; must be a KEYS entry
    key_cells: tuple[tuple[str, ...], ...]
    value_cells: tuple[tuple[str, ...], ...]
    value_names: tuple[str, ...]

    @property
    def key_columns(self) -> tuple[str, ...]:
        return parity_compare.KEYS[self.name]

    def header(self) -> list[str]:
        return [*self.key_columns, *self.value_names]

    def rows(self) -> list[list[str]]:
        return [list(k) + list(v)
                for k, v in zip(self.key_cells, self.value_cells,
                                strict=True)]


def stockpiled_table() -> MiniTable:
    return MiniTable(
        name="stockpiled",
        key_cells=(("cohort_a", "1", "2014-03-10"),
                   ("cohort_a", "2", "2014-03-11")),
        value_cells=(("30", "2014-04-09"), ("14", "2014-03-25")),
        value_names=("rxsup", "expiredt"),
    )


def attrition_table() -> MiniTable:
    return MiniTable(
        name="attrition",
        key_cells=(("cohort_a", "1.0"), ("cohort_a", "27.0")),
        value_cells=(("5", "0"), ("2", "3")),
        value_names=("remaining", "excluded"),
    )


def mfu_table() -> MiniTable:
    """Keyed on (group, analysisnum, code): a CODE column in the key."""
    return MiniTable(
        name="mfu",
        key_cells=(("cohort_a", "1", "D00001"), ("cohort_a", "1", "D00002")),
        value_cells=(("3", "2"), ("1", "2")),
        value_names=("codecount", "patcount"),
    )


def baseline_table() -> MiniTable:
    """KEYS['baseline'] is ('group',) — a single-column key."""
    return MiniTable(
        name="baseline",
        key_cells=(("cohort_a",), ("cohort_b",)),
        value_cells=(("1.5", "40"), ("2.5", "41")),
        value_names=("mean_age", "n"),
    )


def tolerance_table() -> MiniTable:
    """First value cell 4.0: the anchor for boundary perturbations."""
    return MiniTable(
        name="baseline",
        key_cells=(("cohort_a",), ("cohort_b",)),
        value_cells=(("4.0", "7"), ("9.0", "11")),
        value_names=("mean_age", "n"),
    )


ALL_MINI_TABLES: tuple[MiniTable, ...] = (
    stockpiled_table(), attrition_table(), mfu_table(), baseline_table())


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = _csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def write_tree(root: Path, tables: list[tuple[str, MiniTable]],
               mutate: Mutate | None = None,
               extra: Callable[[Path], None] | None = None) -> None:
    """Write tables under `root` at `<rel>.csv` parity-dump paths.

    `mutate(rel_name, table)` may replace one table's literal output
    (returning header+rows) or drop the file (returning DROP).
    """
    root.mkdir(parents=True, exist_ok=True)
    for rel_name, table in tables:
        payload: tuple[list[str], list[list[str]]] | object = None
        if mutate is not None:
            payload = mutate(rel_name, table)
        if payload is DROP:
            continue
        if not isinstance(payload, tuple):
            payload = (table.header(), table.rows())
        _write_csv(root / f"{rel_name}.csv", payload[0], payload[1])
    if extra is not None:
        extra(root)


def paired_trees(tmp: Path, tables: list[tuple[str, MiniTable]],
                 sas_mutate: Mutate | None = None,
                 duck_mutate: Mutate | None = None,
                 duck_extra: Callable[[Path], None] | None = None,
                 ) -> tuple[Path, Path]:
    """A reference tree and a second tree carrying only the delta."""
    sas = tmp / "sas"
    duck = tmp / "duck"
    write_tree(sas, tables, mutate=sas_mutate)
    write_tree(duck, tables, mutate=duck_mutate, extra=duck_extra)
    return sas, duck


def run_compare(sas: Path, duck: Path,
                tol: float | None = None) -> tuple[int, str]:
    """Run the real parity_compare.main(); returns (exit code, text)."""
    argv = ["parity_compare.py", str(sas), str(duck)]
    if tol is not None:
        argv += ["--tolerance", repr(tol)]
    out = io.StringIO()
    saved = sys.argv
    try:
        sys.argv = argv
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            try:
                code = parity_compare.main()
            except SystemExit as exc:              # argparse errors
                code = int(exc.code or 0)
    finally:
        sys.argv = saved
    return code, out.getvalue()


def _copy2d(rows: list[list[str]]) -> list[list[str]]:
    return [list(r) for r in rows]


# ---------------------------------------------------------------------
# Corruptions: ONE targeted defect on the duck side; `marker` is the
# report classification the comparator MUST raise, and exit code MUST
# be 1. DETECTION_KINDS order is the evidence order: row-set defects
# (missing table, key set, multiplicity), then schema, then values,
# then a perturbation far outside a WIDE tolerance (the exact boundary
# is pinned by the deterministic tests on tolerance_table).
# ---------------------------------------------------------------------

DETECTION_TOL = 0.5            # only perturb_far needs a wide tolerance


def c_missing_table(target: str) -> Mutate:
    def mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        return DROP                       # remove the file from this tree
    return mutate


def c_extra_row_new_key(target: str) -> Mutate:
    def mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        fresh = _copy2d([rows[0]])[0]
        fresh[0] = "cohort_zz"            # a group the reference lacks
        rows.append(fresh)
        return t.header(), rows
    return mutate


def c_duplicate_row(target: str) -> Mutate:
    """A byte-identical duplicate of one row. The comparator keys rows
    into a dict (which collapses duplicates), so the ONLY signal is the
    row count — the exact defect the calibration mutation had to kill
    (and did: dedupe-before-compare made this case exit 0)."""
    def mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        rows.append(_copy2d([rows[0]])[0])
        return t.header(), rows
    return mutate


def c_missing_column(target: str) -> Mutate:
    def mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        drop = len(t.key_columns)         # first VALUE column
        header = t.header()
        rows = [[c for i, c in enumerate(r) if i != drop] for r in t.rows()]
        return [h for i, h in enumerate(header) if i != drop], rows
    return mutate


def c_changed_string_value(target: str) -> Mutate:
    def mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        rows[0][-1] = f"zzz-{rows[0][-1]}"
        return t.header(), rows
    return mutate


def c_changed_count_value(target: str) -> Mutate:
    def mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        rows[0][len(t.key_columns)] = "999999"
        return t.header(), rows
    return mutate


def c_changed_date_value(target: str) -> Mutate:
    def mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        rows[0][-1] = "2020-12-25"
        return t.header(), rows
    return mutate


def c_changed_code_value(target: str) -> Mutate:
    """A code that is part of the key migrates the row to a new key."""
    def mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        pos = (t.key_columns.index("code")
               if "code" in t.key_columns else 0)
        rows[0][pos] = "D99999"
        return t.header(), rows
    return mutate


def c_perturb_far(target: str) -> Mutate:
    """Numeric perturbation far outside even a WIDE tolerance: the cell
    is TRIPLED and the comparison runs at tolerance 0.5. Tripling always
    beats the relative rule (|3v-v| = 2v > 0.5*3v = 1.5v for v > 0)."""
    def mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        pos = len(t.key_columns)
        rows[0][pos] = f"{float(rows[0][pos]) * 3:.1f}"
        return t.header(), rows
    return mutate


CORRUPTIONS: dict[str, tuple[Callable[[str], Mutate], str]] = {
    "missing_table":       (c_missing_table,       "MISSING TABLE"),
    "extra_row_new_key":   (c_extra_row_new_key,   "KEY MISMATCH"),
    "duplicate_row":       (c_duplicate_row,       "ROW COUNT"),
    "missing_column":      (c_missing_column,      "MISSING COLUMN"),
    "changed_string_value": (c_changed_string_value, "VALUE"),
    "changed_count_value": (c_changed_count_value, "VALUE"),
    "changed_date_value":  (c_changed_date_value,  "VALUE"),
    "changed_code_value":  (c_changed_code_value,  "KEY MISMATCH"),
    "perturb_far":         (c_perturb_far,         "VALUE"),
}

DETECTION_KINDS: tuple[str, ...] = tuple(CORRUPTIONS)

# ---------------------------------------------------------------------
# Equivalences: paired changes that must NOT fail the comparison
# (exit 0). `expect_note` marks documented NOTE-only differences.
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class EquivalenceCase:
    name: str
    sas_mutate: Mutate | None
    duck_mutate: Mutate | None
    duck_extra: Callable[[Path], None] | None = None
    # A note classification the report must still carry, when the
    # difference is documented as a note rather than an issue.
    expect_note: str | None = None


_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def _to_date9(cell: str) -> str:
    y, m, d = cell.split("-")
    return f"{d}{_MONTHS[int(m) - 1]}{y}"


def e_row_permutation(target: str) -> EquivalenceCase:
    def duck_mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        rows.reverse()
        return t.header(), rows
    return EquivalenceCase("row_permutation", None, duck_mutate)


def e_type_canonical(target: str) -> EquivalenceCase:
    """Type-canonical forms the tool documents as equivalent: integral
    counts written as floats ('30.0'), DATE9. dates against ISO."""
    def duck_mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        for r in rows:
            for i, cell in enumerate(r):
                if len(cell) == 10 and cell[4] == "-" and cell[7] == "-":
                    r[i] = _to_date9(cell)
                elif cell.isdigit() and len(cell) <= 4:
                    r[i] = f"{cell}.0"
        return t.header(), rows
    return EquivalenceCase("type_canonical", None, duck_mutate)


def e_missing_sentinels(target: str) -> EquivalenceCase:
    """'', '.', 'NA', 'NULL', 'NaN', 'nan' are ONE canonical missing
    value (norm() maps them all to ''). The reference writes 'NaN',
    the deliverable '.'."""
    def sas_mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        rows[-1][-1] = "NaN"
        return t.header(), rows
    def duck_mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        rows[-1][-1] = "."
        return t.header(), rows
    return EquivalenceCase("missing_sentinels", sas_mutate, duck_mutate)


def e_within_tolerance(target: str) -> EquivalenceCase:
    """Harmless float difference inside the tolerance: the pair from
    the tool's own docstring (SAS's lower-precision real storage
    34.5333251953125 against 34.5333333...), at the default 1e-6."""
    def sas_mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        rows[0][len(t.key_columns)] = "34.5333251953125"
        return t.header(), rows
    def duck_mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        rows[0][len(t.key_columns)] = "34.5333333"
        return t.header(), rows
    return EquivalenceCase("within_tolerance", sas_mutate, duck_mutate)


def e_extra_column(target: str) -> EquivalenceCase:
    """An extra internal column on the deliverable side: documented as
    a NOTE (SAS's table is the contract; DuckDB legitimately carries
    internal columns), so the exit code stays 0."""
    def duck_mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        return [*t.header(), "internal_flag"], [r + ["X"] for r in rows]
    return EquivalenceCase("extra_column", None, duck_mutate,
                           expect_note="EXTRA COLUMN")


def e_extra_table(target: str) -> EquivalenceCase:
    """An extra TABLE on the deliverable side: documented as a note
    (the SAS export skips tables it did not produce)."""
    def extra(root: Path) -> None:
        (root / "only_here.csv").write_text("a,b\n1,2\n")
    return EquivalenceCase("extra_table", None, None, duck_extra=extra,
                           expect_note="EXTRA TABLE")


def e_missing_sentinel_null(target: str) -> EquivalenceCase:
    """'NULL' text against an empty cell — the same missing value."""
    def sas_mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        rows[-1][-1] = "NULL"
        return t.header(), rows
    def duck_mutate(rn: str, t: MiniTable):
        if rn != target:
            return None
        rows = _copy2d(t.rows())
        rows[-1][-1] = ""
        return t.header(), rows
    return EquivalenceCase("missing_sentinel_null", sas_mutate, duck_mutate)


EQUIVALENCES: dict[str, Callable[[str], EquivalenceCase]] = {
    "row_permutation": e_row_permutation,
    "type_canonical": e_type_canonical,
    "missing_sentinels": e_missing_sentinels,
    "within_tolerance": e_within_tolerance,
    "extra_column": e_extra_column,
    "extra_table": e_extra_table,
    "missing_sentinel_null": e_missing_sentinel_null,
}

BOUNDARY_TOL = 0.5
BOUNDARY_CASES: tuple[tuple[str, float, float, bool], ...] = (
    # (label, a, b, same?) — tol 0.5; values exact in float64.
    ("epsilon_below", 4.0, 7.5, True),
    ("exactly_at", 4.0, 8.0, True),
    ("epsilon_above", 4.0, 8.5, False),
    ("equal", 4.0, 4.0, True),
    ("far_above", 4.0, 40.0, False),
)


# =====================================================================
# Tests
# =====================================================================

as_pairs = [(t.name, t) for t in ALL_MINI_TABLES]
TARGETS = st.sampled_from(as_pairs)
KINDS = st.sampled_from(DETECTION_KINDS)
EQUIV_KINDS = st.sampled_from(tuple(EQUIVALENCES))
TARGET_NAMES = st.sampled_from([name for name, _ in as_pairs])


# ---------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------


@given(kind=KINDS, target=TARGETS)
@example(kind="duplicate_row", target=("stockpiled", stockpiled_table()))
@example(kind="missing_table", target=("attrition", attrition_table()))
def test_comparators_detect_targeted_corruption(
        tmp_path_factory: pytest.TempPathFactory,
        kind: str, target: tuple[str, MiniTable]) -> None:
    """Exactly ONE targeted corruption; the comparator must report it
    with its classification and exit nonzero."""
    target_name = target[0]
    maker, marker = CORRUPTIONS[kind]
    tables = list(as_pairs)
    tol = DETECTION_TOL if kind == "perturb_far" else None
    sas, duck = paired_trees(
        tmp_path_factory.mktemp("cmp"), tables,
        duck_mutate=maker(target_name))
    code, text = run_compare(sas, duck, tol=tol)
    event(f"corruption={kind}")
    event(f"target={target_name}")
    assert code == 1, f"{kind} on {target_name} exited 0:\n{text}"
    assert marker in text, (
        f"{kind} on {target_name} exited 1 but never said {marker!r}:\n"
        f"{text}")
    # The report must be actionable: it names the corrupted table.
    assert target_name in text, (
        f"{kind} on {target_name} produced a diff that never names the "
        f"table:\n{text}")


# ---------------------------------------------------------------------
# Acceptance
# ---------------------------------------------------------------------


@given(kind=EQUIV_KINDS, target=TARGET_NAMES)
@example(kind="row_permutation", target="stockpiled")
@example(kind="extra_table", target="baseline")
def test_comparators_accept_equivalent_outputs(
        tmp_path_factory: pytest.TempPathFactory,
        kind: str, target: str) -> None:
    """Documented equivalences must exit 0; note-only differences must
    still be VISIBLE in the report (accepted, not silenced)."""
    case = EQUIVALENCES[kind](target)
    tables = list(as_pairs)
    sas, duck = paired_trees(
        tmp_path_factory.mktemp("eqv"), tables,
        sas_mutate=case.sas_mutate,
        duck_mutate=case.duck_mutate,
        duck_extra=case.duck_extra)
    code, text = run_compare(sas, duck)
    event(f"equivalence={kind}")
    event(f"target={target}")
    assert code == 0, f"{kind} on {target} rejected a documented " \
        f"equivalence (exit {code}):\n{text}"
    if case.expect_note is not None:
        assert case.expect_note in text, (
            f"{kind} on {target} exited 0 but hid the difference; the "
            f"tool documents it as a note:\n{text}")
    else:
        assert "No differences." in text, (
            f"{kind} on {target} exited 0 with unexpected report "
            f"content:\n{text}")


# ---------------------------------------------------------------------
# Tolerance boundary, pinned exactly — unit level and file level.
# ---------------------------------------------------------------------


@pytest.mark.parametrize("label,a,b,same", BOUNDARY_CASES)
def test_same_value_tolerance_boundary_exact(
        label: str, a: float, b: float, same: bool) -> None:
    """|a-b| <= tol*max(1,|a|,|b|) at exactly-at must ACCEPT (rule is
    <=), epsilon-above must REPORT, with values exact in float64."""
    got = parity_compare.same_value(f"{a!r}", f"{b!r}", BOUNDARY_TOL)
    assert got is same, (
        f"{label}: same_value({a!r}, {b!r}, {BOUNDARY_TOL}) = {got}, "
        f"expected {same}")


@pytest.mark.parametrize("duck_cell,expect_exit", [
    ("7.5", 0),    # epsilon-below: accepted
    ("8.0", 0),    # exactly at the boundary: accepted (<=)
    ("8.5", 1),    # epsilon-above: reported
])
def test_compare_table_tolerance_boundary_through_files(
        tmp_path_factory: pytest.TempPathFactory,
        duck_cell: str, expect_exit: int) -> None:
    """The same boundary through the real file path: reference 4.0 vs
    the deliverable cell, tolerance 0.5."""
    tables = [("baseline", tolerance_table())]

    def duck_mutate(rn: str, t: MiniTable):
        rows = [list(r) for r in t.rows()]
        rows[0][len(t.key_columns)] = duck_cell
        return t.header(), rows

    sas, duck = paired_trees(tmp_path_factory.mktemp("bnd"), tables,
                             duck_mutate=duck_mutate)
    code, text = run_compare(sas, duck, tol=BOUNDARY_TOL)
    assert code == expect_exit, (
        f"reference 4.0 vs deliverable {duck_cell} at tol={BOUNDARY_TOL}: "
        f"exit {code}, expected {expect_exit}\n{text}")


# ---------------------------------------------------------------------
# Documented normalization forms, pinned at the unit level so the
# acceptance property above cannot pass by accident.
# ---------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("30.0", "30"),                     # integral float -> integer text
    ("1", "1"),
    ("10MAR2014", "2014-03-10"),        # DATE9.
    ("03/10/2014", "2014-03-10"),       # MMDDYY10.
    ("2014-03-10 00:00:00", "2014-03-10"),   # midnight timestamp
    ("2014-03-10T00:00:00", "2014-03-10"),
    ("NaN", ""),                        # missing sentinels collapse
    ("nan", ""),
    ("NA", ""),
    ("NULL", ""),
    (".", ""),
    ("0636", "0636"),                   # leading-zero code kept verbatim
    ("abc", "abc"),                     # non-numeric text unchanged
    ("", ""),
    (None, ""),
])
def test_norm_canonical_forms(raw: str | None, expected: str) -> None:
    assert parity_compare.norm(raw) == expected


def test_same_value_missing_forms_are_equal() -> None:
    """Missing forms equal each other at the same_value level ('',
    '.', 'NA', 'NULL'). 'NaN'/'nan' are canonicalized by norm() BEFORE
    same_value runs, so their equality holds through the file path —
    same_value alone does not treat them as missing, which the norm
    test above pins."""
    for a, b in (("", "."), ("", "NA"), (".", "NULL"), ("NULL", "")):
        assert parity_compare.same_value(a, b, 1e-6), (a, b)
    # NaN vs empty is equal only after norm(), i.e. through read_csv
    assert parity_compare.norm("NaN") == parity_compare.norm("")
    # numeric strings compare through the tolerance rule
    assert parity_compare.same_value("1.0", "1", 1e-6)
    assert parity_compare.same_value("34.5333251953125",
                                     "34.5333333", 1e-6)
    assert not parity_compare.same_value("1", "2", 1e-6)
