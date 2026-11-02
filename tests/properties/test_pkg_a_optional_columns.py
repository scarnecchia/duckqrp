"""Package A pass 2 — every DECLARED-OPTIONAL SCDM column, omitted,
characterized (generalizes the A-2/A-4 failure family).

Contract (INPUTS.md "Columns actually read" + ``qrp.scdm.SCDM``): an
extract may omit any column declared optional. Pass 1 pinned the
resolved set (``opt_*``/``dispensing_*``) and the single fixed rxamt
case. This property sweeps ALL declared-optional columns: omit exactly
one (or a generated pair from one table) from an otherwise-complete
extract with every table populated, and characterize today's outcome.

* ``runs``  — extract completes; claim extraction unchanged (and for
  dispensing.rxamt, the amount rule is resolved OUT: rows are kept with
  NULL amounts, the fix-a2 semantics).
* ``raises`` — the shipped SQL binds a ``cdm_*`` view that references
  the column unconditionally: BinderException naming the column. The
  DOCUMENTED contract says these columns are optional, so each crasher
  is pinned ``xfail(strict=False)`` asserting the contract — green now
  (the crash is the expected failure), and every pin flips to XPASS as
  arbitration lands fixes. The diagnosis trio is the arbitrated A-4;
  procedure px_codetype/enctype and all 13 lab_result optionals were
  FOUND by this sweep (same family; lab crashes surface only under a
  labs-enabled study, which the probe configures).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from hypothesis import event, given
from hypothesis import strategies as st

from qrp import run
from qrp.scdm import SCDM

from .conftest import expensive_settings
from .oracles import a_optional_columns as OC
from .oracles.a_scdm_layouts import write_variant


def _scalar(result, sql: str) -> int:
    """First column of a one-row query, None-checked for the checker."""
    row = result.engine.con.execute(sql).fetchone()
    assert row is not None, f"query returned no row: {sql}"
    return int(row[0])


@pytest.fixture(scope="module")
def probe() -> dict[str, int]:
    """Baseline counts for the fixed probe extract (immutable, computed
    once): full schema, labs-enabled study."""
    dataset = OC.probe_dataset()
    study = OC.probe_study(dataset)
    with tempfile.TemporaryDirectory(prefix="qrp-a-optcol-") as tmp:
        write_variant(Path(tmp) / "indata", dataset, kind="standard")
        result = run(study, str(Path(tmp) / "indata"), threads=1,
                     verbose=False)
        counts = {
            "exposure_claims": _scalar(
                result, "SELECT count(*) FROM exposure_claims"),
            "cdm_dispensing": _scalar(
                result, "SELECT count(*) FROM cdm_dispensing"),
        }
        result.engine.close()
    assert counts["exposure_claims"] > 0, "probe extract is degenerate"
    return counts


def _run_omitting(tmp: Path, table: str, columns: tuple[str, ...],
                  dataset, study) -> dict[str, int]:
    write_variant(tmp / "indata", dataset, kind="standard",
                  drop_by_table={table: columns})
    result = run(study, str(tmp / "indata"), threads=1, verbose=False)
    try:
        return {
            "exposure_claims": _scalar(
                result, "SELECT count(*) FROM exposure_claims"),
            "cdm_dispensing": _scalar(
                result, "SELECT count(*) FROM cdm_dispensing"),
        }
    finally:
        result.engine.close()


class TestOptionalColumnDeclaration:
    def test_hand_list_matches_declaration(self) -> None:
        """The hand-transcribed list cannot drift from the production
        declaration: every scdm.py optional is in the hand list, and the
        hand list adds only what INPUTS.md declares beyond scdm.py
        (encounter ddate/discharge_status)."""
        declared = {s.name: set(s.optional) for s in SCDM}
        for table, cols in OC.DECLARED_OPTIONAL.items():
            hand = set(cols)
            prod = declared.get(table, set())
            assert prod <= hand, (
                f"{table}: production declares {prod - hand} not in the "
                f"hand list"
            )
        extras = {
            ("encounter", "ddate"), ("encounter", "discharge_status"),
        }
        for table, cols in OC.DECLARED_OPTIONAL.items():
            overflow = set(cols) - declared.get(table, set())
            assert overflow <= {c for t, c in extras if t == table}, (
                f"{table}: hand list invents {overflow}"
            )


def _params() -> list:
    params = []
    for table, column in OC.all_declared_columns():
        if OC.outcome_class(table, column) == "raises":
            params.append(pytest.param(
                table, column,
                marks=pytest.mark.xfail(
                    strict=False,
                    reason=(
                        f"A-4 family: INPUTS.md declares {table}.{column} "
                        f"optional, but the shipped cdm_* view binds it "
                        f"unconditionally -> BinderException "
                        f"(observed at rev 8f9f603)"),
                ),
                id=f"{table}.{column}",
            ))
        else:
            params.append(pytest.param(
                table, column, id=f"{table}.{column}"))
    return params


class TestOptionalColumnCharacterization:
    @pytest.mark.parametrize("table,column", _params())
    def test_omitted_column(
        self, table: str, column: str, probe: dict[str, int],
        tmp_path: Path,
    ) -> None:
        """The DOCUMENTED contract: an extract omitting this declared
        optional runs and extracts the same claims. Crashers are pinned
        as expected failures (non-strict xfail above)."""
        dataset = OC.probe_dataset()
        study = OC.probe_study(dataset)
        counts = _run_omitting(tmp_path, table, (column,), dataset, study)
        assert counts["exposure_claims"] == probe["exposure_claims"], (
            f"omitting declared-optional {table}.{column} changed "
            f"claim extraction"
        )
        if (table, column) == ("dispensing", "rxamt"):
            # fix-a2 semantics: the SAS amount rule is resolved OUT for
            # extracts without the column — rows kept, amounts NULL.
            assert counts["cdm_dispensing"] == probe["cdm_dispensing"], (
                "rxamt-less extract lost dispensings; the amount rule "
                "must not apply when there is no amount column"
            )

    @expensive_settings()
    @given(
        table=st.sampled_from(
            [t for t, cols in OC.DECLARED_OPTIONAL.items() if len(cols) >= 1
             and t != "death"]),
        pick=st.data(),
    )
    def test_generated_omission_sets(
        self, table: str, pick, probe: dict[str, int]
    ) -> None:
        """Omit a generated 1-2 column set from ONE table: the outcome
        class is the union of the members' classes — a set containing
        any crasher still crashes; an all-resolved set still runs with
        unchanged extraction."""
        cols = OC.DECLARED_OPTIONAL[table]
        chosen = tuple(pick.draw(
            st.lists(st.sampled_from(cols), min_size=1,
                     max_size=min(2, len(cols)), unique=True)))
        classes = {OC.outcome_class(table, c) for c in chosen}
        event(f"omission:{table}:{'+'.join(chosen)}:{sorted(classes)}")
        dataset = OC.probe_dataset()
        study = OC.probe_study(dataset)
        with tempfile.TemporaryDirectory(prefix="qrp-a-opt-") as tmp:
            if "raises" in classes:
                with pytest.raises(Exception) as excinfo:
                    _run_omitting(Path(tmp), table, chosen, dataset, study)
                event(f"crash-surface:{type(excinfo.value).__name__}")
            else:
                counts = _run_omitting(Path(tmp), table, chosen,
                                       dataset, study)
                assert (counts["exposure_claims"]
                        == probe["exposure_claims"])
