"""Regression: an extract without the declared-optional diagnosis
columns must run (investigation finding A-4, arbitrator fix branch).

INPUTS.md ("Columns actually read") declares the diagnosis table's
`dx_codetype`, `pdx` and `enctype` OPTIONAL — required is only
`patid, adate, dx` — and scdm.py lists all three in the diagnosis spec's
optional tuple, so `qrp inspect` reports such an extract as compatible.
10_normalize.sql's `_cdm_diagnosis_raw` referenced all three
unconditionally, so the first stage died with `Binder Error: Referenced
column "dx_codetype" not found`.

The fix mirrors the mechanism every other declared-optional column uses
(pipeline.py resolves optional columns against the actual extract and
falls back to NULL; see the `chart` note there, and fix-a2's rxamt).
Substituting the column or NULL into the view's own COALESCE wildcards
keeps present-column semantics byte-identical: without `pdx`/`enctype`
a claim carries no principal flag or care setting ('X'/'XX', which the
care-setting parser treats as match-any), and without `dx_codetype` the
exposure join's "NULL source codetype cannot contradict" rule matches
any configured codetype — the same convention as dispensing.

Standalone: builds its own SCDM parquet extract with duckdb and runs the
production pipeline. No campaign infrastructure.
"""

from datetime import date
from pathlib import Path

import duckdb

from qrp import load_study_dict, run


def _write_extract(root: Path, diagnosis_schema, diagnosis_rows) -> Path:
    indata = root / "indata"
    indata.mkdir(parents=True)
    rows = {
        "demographic": [(1, date(1980, 5, 1), "F")],
        "enrollment": [(1, date(2011, 1, 1), date(2011, 12, 31), "Y", "Y")],
        "dispensing": [(1, date(2011, 9, 15), "E00001", 30, 10.0)],
        "diagnosis": diagnosis_rows,
    }
    con = duckdb.connect()
    try:
        for table, schema in (
            ("enrollment", (("patid", "BIGINT"), ("enr_start", "DATE"),
                            ("enr_end", "DATE"), ("medcov", "VARCHAR"),
                            ("drugcov", "VARCHAR"))),
            ("demographic", (("patid", "BIGINT"), ("birth_date", "DATE"),
                             ("sex", "VARCHAR"))),
            ("dispensing", (("patid", "BIGINT"), ("rxdate", "DATE"),
                            ("rx", "VARCHAR"), ("rxsup", "INTEGER"),
                            ("rxamt", "DOUBLE"))),
            ("diagnosis", diagnosis_schema),
        ):
            cols = ", ".join(f"{name} {typ}" for name, typ in schema)
            con.execute(f"CREATE TABLE t ({cols})")
            if rows[table]:
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


# the REQUIRED columns only: no dx_codetype, pdx, enctype
BASE_SCHEMA = (("patid", "BIGINT"), ("adate", "DATE"), ("dx", "VARCHAR"))


def test_optional_diagnosis_columns_absent_runs(tmp_path: Path):
    """An extract whose diagnosis table carries every required column
    but none of the declared-optional trio must run, not die with a
    BinderException at the first stage."""
    indata = _write_extract(
        tmp_path,
        BASE_SCHEMA,
        [(1, date(2011, 9, 16), "D00001")],
    )
    result = run(_study(), str(indata), verbose=False)
    con = result.engine.con

    dx = _rows_by_name(con, "cdm_diagnosis")
    assert len(dx) == 1
    # absent codetype resolves to NULL: the exposure join treats a NULL
    # source codetype as "match any" (30_exposure.sql)
    assert dx[0]["codetype"] is None
    # absent principal flag / care setting resolve to the view's own
    # wildcards: 'X' (any pdx) and 'XX' (any enctype)
    assert dx[0]["pdx"] == "X"
    assert dx[0]["enctype"] == "XX"

    # the episode itself is unaffected: the dispensing still forms it
    stock = _rows_by_name(con, "stockpiled")
    assert len(stock) == 1


def test_diagnosis_columns_present_keep_semantics(tmp_path: Path):
    """With the columns present, values must flow through unchanged —
    the fix resolves absent columns, it must not alter extracts that
    carry them."""
    schema = BASE_SCHEMA + (("dx_codetype", "VARCHAR"),
                            ("pdx", "VARCHAR"), ("enctype", "VARCHAR"))
    indata = _write_extract(
        tmp_path,
        schema,
        # pdx 'P' on an IP claim — SCDM populates Pdx only for IP/IS
        [(1, date(2011, 9, 16), "D00001", "10", "P", "IP")],
    )
    result = run(_study(), str(indata), verbose=False)
    con = result.engine.con

    dx = _rows_by_name(con, "cdm_diagnosis")
    assert len(dx) == 1
    assert dx[0]["codetype"] == "10"
    assert dx[0]["pdx"] == "P"
    assert dx[0]["enctype"] == "IP"
