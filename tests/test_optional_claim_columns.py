"""Declared-optional claim columns behave like explicit NULLs when absent."""

from datetime import date
from pathlib import Path

import duckdb
import pytest

from qrp import load_study_dict, run

OPTIONALS = {
    "diagnosis": ("dx_codetype", "pdx", "enctype"),
    "procedure": ("px_codetype", "enctype"),
    "lab_result": (
        "result_dt", "order_dt", "ms_result_n", "ms_result_c", "result_type",
        "loinc", "px", "ms_test_name", "ms_test_sub_category", "specimen_source",
        "ms_result_unit", "fast_ind", "pt_loc",
    ),
}


def _normalized(root: Path, table: str, omitted: tuple[str, ...], nulls: bool) -> tuple:
    root.mkdir()
    schemas = {
        "demographic": "patid BIGINT, birth_date DATE, sex VARCHAR",
        "enrollment": "patid BIGINT, enr_start DATE, enr_end DATE, medcov VARCHAR, drugcov VARCHAR",
        "dispensing": "patid BIGINT, rxdate DATE, rx VARCHAR, rxsup INTEGER, rxamt DOUBLE",
        "diagnosis": "patid BIGINT, adate DATE, dx VARCHAR, dx_codetype VARCHAR, pdx VARCHAR, enctype VARCHAR",
        "procedure": "patid BIGINT, adate DATE, px VARCHAR, px_codetype VARCHAR, enctype VARCHAR",
        "lab_result": (
            "patid BIGINT, lab_dt DATE, result_dt DOUBLE, order_dt DOUBLE, ms_result_n DOUBLE, "
            "ms_result_c VARCHAR, result_type VARCHAR, loinc VARCHAR, px VARCHAR, "
            "ms_test_name VARCHAR, ms_test_sub_category VARCHAR, specimen_source VARCHAR, "
            "ms_result_unit VARCHAR, fast_ind VARCHAR, pt_loc VARCHAR"
        ),
    }
    rows = {
        "demographic": (1, date(1980, 5, 1), "F"),
        "enrollment": (1, date(2011, 1, 1), date(2011, 12, 31), "Y", "Y"),
        "dispensing": (1, date(2011, 9, 15), "E00001", 30, 10.0),
        # SCDM note: pdx 'P' rides an IP claim (diagnosis.md populates Pdx
        # only for IP/IS); the lab row's padded/lowercase values pin
        # trim+upper normalization (' positive '->POSITIVE etc.), and its
        # result_type 'N' with ms_result_c populated is a trim probe only —
        # SCDM requires null text on numeric results.
        "diagnosis": (1, date(2011, 9, 16), "D00001", "10", "P", "IP"),
        "procedure": (1, date(2011, 9, 16), "PX1", "HC", "IP"),
        "lab_result": (1, date(2011, 9, 16), 18000.0, 18001.0, 1.2, " positive ", "N",
                       "2160-0", "PX1", " creatinine ", "", " serum ", " mg/dl ", " X ", "O"),
    }
    with duckdb.connect(config={"threads": 1}) as con:
        for name, schema in schemas.items():
            con.execute(f"CREATE TABLE t ({schema})")
            con.execute(f"INSERT INTO t VALUES ({', '.join('?' for _ in rows[name])})", rows[name])
            if name == table:
                for column in omitted:
                    if nulls:
                        con.execute(f"UPDATE t SET {column} = NULL")
                    else:
                        con.execute(f"ALTER TABLE t DROP COLUMN {column}")
            con.execute(f"COPY t TO '{root / (name + '.parquet')}' (FORMAT PARQUET)")
            con.execute("DROP TABLE t")
    study = load_study_dict({
        "qrp_parameters_scalars": {
            "runid": "optional", "type": 2,
            "startdate": "2011-01-01", "enddate": "2011-12-31",
        },
        "cohortfile": [{"cohortgrp": "c1", "coverage": "MD", "enrolgap": 0, "enrdays": 183}],
        "cohortcodes": [{"group": "c1", "indexcriteria": "DEF", "code": "E00001", "codecat": "RX"}],
    })
    result = run(study, str(root), verbose=False, threads=1)
    try:
        view = "cdm_lab" if table == "lab_result" else f"cdm_{table}"
        records = result.engine.con.execute(f"SELECT * FROM {view}").fetchall()
        assert len(records) == 1
        assert result.engine.con.execute("SELECT count(*) FROM stockpiled").fetchone() == (1,)
        return records[0]
    finally:
        result.engine.con.close()


@pytest.mark.parametrize("table,column", [(t, c) for t, cols in OPTIONALS.items() for c in cols])
def test_each_optional_column_matches_null(tmp_path: Path, table: str, column: str) -> None:
    expected = _normalized(tmp_path / "null", table, (column,), True)
    assert _normalized(tmp_path / "omitted", table, (column,), False) == expected


@pytest.mark.parametrize("table", OPTIONALS)
def test_all_optional_columns_match_null(tmp_path: Path, table: str) -> None:
    expected = _normalized(tmp_path / "null", table, OPTIONALS[table], True)
    assert _normalized(tmp_path / "omitted", table, OPTIONALS[table], False) == expected


@pytest.mark.parametrize("table,expected", [
    ("diagnosis", (1, date(2011, 9, 16), "D00001", "10", "P", "IP")),
    ("procedure", (1, date(2011, 9, 16), "PX1", "HC", "IP", "")),
    ("lab_result", (1, date(2011, 9, 16), date(2009, 4, 13), date(2009, 4, 14),
                    1.2, "POSITIVE", "N", "2160-0", "PX1", "CREATININE", "",
                    "SERUM", "MG/DL", "X", "O")),
])
def test_present_columns_keep_values(tmp_path: Path, table: str, expected: tuple) -> None:
    assert _normalized(tmp_path / "present", table, (), False) == expected
