"""Regression: an extract without dispensing.rxamt must run (investigation
finding A-2, arbitrator fix branch).

INPUTS.md ("Columns actually read") declares dispensing.rxamt OPTIONAL —
"rxamt (dose only)" — and scdm.py lists it in the dispensing spec's
optional columns, so `qrp inspect` reports such an extract as compatible.
10_normalize.sql referenced rxamt unconditionally, so the first stage
died with `BinderException: Referenced column "rxamt" not found`.

The fix mirrors the mechanism every other declared-optional column uses
(pipeline.py resolves optional columns against the actual extract and
falls back to NULL; see the `chart` note there). An extract WITHOUT the
column gets NULL amounts and no amount rule — `rxamt IS NOT NULL AND
rxamt > 0` cannot exclude anything when no amount exists to test. An
extract WITH the column keeps SAS's rule exactly: a true zero or missing
amount is not a dispensing (ms_cidanum.sas:617).

Standalone: builds its own SCDM parquet extract with duckdb and runs the
production pipeline. No campaign infrastructure.
"""

from datetime import date
from pathlib import Path

import duckdb
import pytest

from qrp import load_study_dict, run


# dispensing deliberately carries NO codetype column: the code-type
# resolution treats a missing column as "match any" and the extract
# stays minimal.
SCDM_TABLES = {
    "enrollment": (("patid", "BIGINT"), ("enr_start", "DATE"),
                   ("enr_end", "DATE"), ("medcov", "VARCHAR"),
                   ("drugcov", "VARCHAR")),
    "demographic": (("patid", "BIGINT"), ("birth_date", "DATE"),
                    ("sex", "VARCHAR")),
    "dispensing": None,   # schema varies per test (with/without rxamt)
    "diagnosis": (("patid", "BIGINT"), ("adate", "DATE"),
                  ("dx", "VARCHAR"), ("dx_codetype", "VARCHAR"),
                  ("pdx", "VARCHAR"), ("enctype", "VARCHAR")),
}


def _write_extract(root: Path, dispensing_schema, dispensing_rows) -> Path:
    indata = root / "indata"
    indata.mkdir(parents=True)
    rows = {
        "demographic": [(1, date(1980, 5, 1), "F")],
        "enrollment": [(1, date(2011, 1, 1), date(2011, 12, 31), "Y", "Y")],
        "dispensing": dispensing_rows,
        "diagnosis": [],
    }
    con = duckdb.connect()
    try:
        for table, schema in SCDM_TABLES.items():
            if table == "dispensing":
                schema = dispensing_schema
            cols = ", ".join(f"{name} {typ}" for name, typ in schema)
            con.execute(f"CREATE TABLE t ({cols})")
            if rows.get(table):
                placeholders = ", ".join("?" for _ in schema)
                con.executemany(
                    f"INSERT INTO t VALUES ({placeholders})", rows[table])
            con.execute(
                f"COPY t TO '{indata / f'{table}.parquet'}' (FORMAT PARQUET)")
            con.execute("DROP TABLE t")
    finally:
        con.close()
    return indata


def _study():
    study = load_study_dict({
        "qrp_parameters_scalars": {
            "runid": "regr", "type": 2,
            "startdate": "2011-01-01", "enddate": "2011-12-31",
        },
        "cohortfile": [{"cohortgrp": "c1", "coverage": "MD",
                        "enrolgap": 0, "enrdays": 183}],
        "cohortcodes": [
            {"group": "c1", "indexcriteria": "DEF",
             "code": "E00001", "codecat": "RX"},
        ],
    })
    study.validate()
    return study


def _rows_by_name(con, table):
    cur = con.execute(f"SELECT * FROM {table}")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


BASE_SCHEMA = (("patid", "BIGINT"), ("rxdate", "DATE"), ("rx", "VARCHAR"),
               ("rxsup", "INTEGER"))


def test_optional_rxamt_absent_runs(tmp_path: Path):
    """An extract with every required column but no rxamt must run, not
    die with a BinderException at the first stage."""
    indata = _write_extract(
        tmp_path,
        BASE_SCHEMA,
        [(1, date(2011, 9, 15), "E00001", 30)],
    )
    result = run(_study(), str(indata), verbose=False)
    con = result.engine.con

    disp = _rows_by_name(con, "cdm_dispensing")
    assert len(disp) == 1
    assert disp[0]["rxamt"] is None  # absent column resolves to NULL

    stock = _rows_by_name(con, "stockpiled")
    assert len(stock) == 1
    assert stock[0]["numdispensing"] == 1
    assert stock[0]["rxsup"] == 30


def test_rxamt_present_zero_amount_still_excluded(tmp_path: Path):
    """With the column present, SAS's `rxamt IS NOT NULL AND rxamt > 0`
    rule must keep excluding zero/missing amounts — the fix must not
    relax the rule for extracts that carry amounts."""
    schema = BASE_SCHEMA + (("rxamt", "DOUBLE"),)
    indata = _write_extract(
        tmp_path,
        schema,
        [
            (1, date(2011, 9, 15), "E00001", 30, 0.0),    # zero: excluded
            (1, date(2011, 9, 20), "E00001", 7, 10.0),    # kept
        ],
    )
    result = run(_study(), str(indata), verbose=False)
    con = result.engine.con

    disp = _rows_by_name(con, "cdm_dispensing")
    assert [r["rxsup"] for r in disp] == [7]
    assert disp[0]["rxamt"] == pytest.approx(10.0)

    stock = _rows_by_name(con, "stockpiled")
    assert len(stock) == 1
    assert stock[0]["numdispensing"] == 1
