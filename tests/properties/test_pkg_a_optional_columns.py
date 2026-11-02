"""Optional-column omissions must run, including the enabled labs stage."""

import tempfile
from pathlib import Path
from typing import Any

import duckdb
import pytest
from hypothesis import event, given
from hypothesis import strategies as st
from hypothesis.strategies import DataObject

from qrp import run
from qrp.scdm import SCDM

from .conftest import expensive_settings
from .oracles import a_optional_columns as OC
from .oracles.a_scdm_layouts import write_variant

OUTPUTS = ("exposure_claims", "cdm_dispensing", "lab_results", "lab_summary")


def _capture(root: Path) -> dict[str, list[tuple[Any, ...]]]:
    dataset = OC.probe_dataset()
    result = run(OC.probe_study(dataset), str(root), threads=1, verbose=False)
    try:
        return {table: result.engine.con.execute(
            f"SELECT * FROM {table} ORDER BY ALL").fetchall() for table in OUTPUTS}
    finally:
        result.engine.close()


@pytest.fixture(scope="module")
def probe() -> dict[str, list[tuple[Any, ...]]]:
    with tempfile.TemporaryDirectory(prefix="qrp-a-opt-baseline-") as tmp:
        root = Path(tmp)
        write_variant(root, OC.probe_dataset(), kind="standard")
        outputs = _capture(root)
    assert outputs["exposure_claims"]
    assert outputs["lab_results"], "labs-enabled probe must extract matching records"
    assert outputs["lab_summary"], "labs-enabled probe must summarize matching records"
    return outputs


def _check_omission(tmp: Path, table: str, columns: tuple[str, ...],
                    probe: dict[str, list[tuple[Any, ...]]]) -> None:
    dataset = OC.probe_dataset()
    absent = tmp / "absent"
    control = tmp / "null"
    write_variant(absent, dataset, kind="standard", drop_by_table={table: columns})
    write_variant(control, dataset, kind="standard")
    path = control / table / "data.parquet"
    with duckdb.connect(config={"threads": 1}) as con:
        con.execute(f"CREATE TABLE control AS SELECT * FROM read_parquet('{path}')")
        con.execute("UPDATE control SET " + ", ".join(f"{col} = NULL" for col in columns))
        con.execute(f"COPY control TO '{path}' (FORMAT PARQUET)")
    actual = _capture(absent)
    expected = _capture(control)
    # rxamt absence disables the amount rule; explicit NULL does not.
    compared = OUTPUTS if table != "dispensing" or "rxamt" not in columns else ()
    if not compared:
        assert len(actual["cdm_dispensing"]) == len(probe["cdm_dispensing"])
        assert all(row[-1] is None for row in actual["cdm_dispensing"])
        assert len(actual["exposure_claims"]) == len(probe["exposure_claims"])
    for output in compared:
        assert actual[output] == expected[output], (table, columns, output)
    assert actual["exposure_claims"], (table, columns)
    if table != "lab_result":
        assert actual["lab_results"] == probe["lab_results"]
        assert actual["lab_summary"] == probe["lab_summary"]
    elif "loinc" in columns or ("ms_result_n" in columns and "result_type" not in columns):
        assert actual["lab_results"] == []
        assert actual["lab_summary"] == []
    elif "ms_result_n" in columns:
        assert [row[:-1] for row in actual["lab_results"]] == [
            row[:-1] for row in probe["lab_results"]]
        assert all(row[-1] is None for row in actual["lab_results"])
        assert actual["lab_summary"] == [("cohort_a", "2160-0", 1, 1, None, None, None)]
    else:
        assert actual["lab_results"] == probe["lab_results"]
        assert actual["lab_summary"] == probe["lab_summary"]


class TestOptionalColumnDeclaration:
    def test_hand_list_matches_declaration(self) -> None:
        declared = {spec.name: set(spec.optional) for spec in SCDM}
        extras = {"encounter": {"ddate", "discharge_status"}}
        assert set(declared) == set(OC.DECLARED_OPTIONAL)
        for table, columns in OC.DECLARED_OPTIONAL.items():
            assert set(columns) == declared[table] | extras.get(table, set())


class TestOptionalColumnCharacterization:
    @pytest.mark.parametrize("table,column", OC.all_declared_columns(),
                             ids=[f"{t}.{c}" for t, c in OC.all_declared_columns()])
    def test_omitted_column(self, table: str, column: str, tmp_path: Path,
                            probe: dict[str, list[tuple[Any, ...]]]) -> None:
        _check_omission(tmp_path, table, (column,), probe)

    @expensive_settings()
    @given(table=st.sampled_from([t for t, cols in OC.DECLARED_OPTIONAL.items() if cols]),
           pick=st.data())
    def test_generated_omission_sets(self, table: str, pick: DataObject,
                                     probe: dict[str, list[tuple[Any, ...]]]) -> None:
        columns = OC.DECLARED_OPTIONAL[table]
        chosen = tuple(pick.draw(st.lists(st.sampled_from(columns), min_size=1,
                                         max_size=len(columns), unique=True)))
        event(f"omission:{table}:{'+'.join(chosen)}")
        with tempfile.TemporaryDirectory(prefix="qrp-a-opt-set-") as tmp:
            _check_omission(Path(tmp), table, chosen, probe)

    def test_present_lab_values(self, probe: dict[str, list[tuple[Any, ...]]]) -> None:
        for row in probe["lab_results"]:
            assert row[3] == "2160-0"
            assert row[5] == 1.2
        for row in probe["lab_summary"]:
            assert row[1] == "2160-0"
            assert row[2] > 0
            assert row[3] > 0
            assert row[4:] == (1.2, 1.2, 1.2)
