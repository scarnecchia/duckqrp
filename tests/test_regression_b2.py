"""Regression: overlapping raw enrollment spans must not double-count a
dispensing (investigation finding B2, arbitrator fix branch).

One dispensing is one dispensing: numdispensing counts claims and the
stockpiled supply cannot exceed what was dispensed, whatever the
enrollment input looks like. Overlapping raw enrollment spans do produce
overlapping enrollment episodes — that segmentation is documented SAS
parity (docs/SAS_PARITY.md, "Enrollment spans": ENRSTART - lag(ENREND) - 1
> ENROLGAP breaks against the immediately previous span) — but the
stockpile shave joins must shave each fill against exactly ONE containing
span, not once per containing span.

Standalone: builds its own SCDM parquet extract with duckdb and runs the
production pipeline. No campaign infrastructure.
"""

from datetime import date
from pathlib import Path

import duckdb
import pytest

from qrp import load_study_dict, run


SCDM_TABLES = {
    # (column name, SQL type) per table; only what the pipeline reads.
    "enrollment": (("patid", "BIGINT"), ("enr_start", "DATE"),
                   ("enr_end", "DATE"), ("medcov", "VARCHAR"),
                   ("drugcov", "VARCHAR")),
    "demographic": (("patid", "BIGINT"), ("birth_date", "DATE"),
                    ("sex", "VARCHAR")),
    "dispensing": (("patid", "BIGINT"), ("rxdate", "DATE"),
                   ("rx", "VARCHAR"), ("rxsup", "INTEGER"),
                   ("rxamt", "DOUBLE")),
    "diagnosis": (("patid", "BIGINT"), ("adate", "DATE"),
                  ("dx", "VARCHAR"), ("dx_codetype", "VARCHAR"),
                  ("pdx", "VARCHAR"), ("enctype", "VARCHAR")),
}


def _write_extract(root: Path, rows: dict[str, list[tuple]]) -> Path:
    """Write one parquet file per SCDM table, empty tables included."""
    indata = root / "indata"
    indata.mkdir(parents=True)
    con = duckdb.connect()
    try:
        for table, schema in SCDM_TABLES.items():
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
    """One MD cohort over 2011, exposure code E00001, defaults elsewhere."""
    study = load_study_dict({
        "qrp_parameters_scalars": {
            "runid": "regr", "type": 2,
            "startdate": "2011-01-01", "enddate": "2011-12-31",
        },
        "cohortfile": [{"cohortgrp": "c1", "coverage": "MD",
                        "enrolgap": 0, "enrdays": 183}],
        "cohortcodes": [
            {"group": "c1", "indexcriteria": "DEF",
             "code": "E00001", "codecat": "RX", "codetype": "ND"},
        ],
    })
    study.validate()
    return study


def _overlap_dataset_rows() -> dict[str, list[tuple]]:
    """One patient whose raw enrollment spans overlap: A=[Jan 1 .. Dec 31],
    B=[Feb 15 .. Jun 15] nested in A, C=[Sep 1 .. Sep 30] inside A.

    The lag-break segmentation emits TWO overlapping episodes (A+B merged,
    C alone) — documented SAS parity, NOT changed by the fix. The single
    30-day/10.0 fill on Sep 15 sits inside BOTH episode outputs, which is
    where the double-count happened.
    """
    return {
        "demographic": [(1, date(1980, 5, 1), "F")],
        "enrollment": [
            (1, date(2011, 1, 1), date(2011, 12, 31), "Y", "Y"),
            (1, date(2011, 2, 15), date(2011, 6, 15), "Y", "Y"),
            (1, date(2011, 9, 1), date(2011, 9, 30), "Y", "Y"),
        ],
        "dispensing": [(1, date(2011, 9, 15), "E00001", 30, 10.0)],
        "diagnosis": [],
    }


def _rows_by_name(con, table):
    cur = con.execute(f"SELECT * FROM {table}")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def test_overlapping_spans_stockpile_each_dispensing_once(tmp_path: Path):
    indata = _write_extract(tmp_path, _overlap_dataset_rows())
    result = run(_study(), str(indata), verbose=False)
    con = result.engine.con

    # The segmentation itself keeps its documented SAS parity: overlapping
    # raw spans yield the two overlapping episodes (merged A+B, plus C).
    episodes = sorted(
        (r["enr_start"], r["enr_end"]) for r in _rows_by_name(con,
                                                              "enrollment_spans"))
    assert episodes == [
        (date(2011, 1, 1), date(2011, 12, 31)),
        (date(2011, 9, 1), date(2011, 9, 30)),
    ]

    # ONE fill -> ONE stockpiled record, un-prorated (30 days, 10.0),
    # numdispensing counting the one claim.
    stock = _rows_by_name(con, "stockpiled")
    assert len(stock) == 1, f"one dispensing produced {len(stock)} rows"
    row = stock[0]
    assert row["numdispensing"] == 1, (
        f"numdispensing counted {row['numdispensing']} claims for one fill")
    assert row["rxsup"] == 30 and row["rxamt"] == pytest.approx(10.0), (
        f"fill was re-prorated: rxsup={row['rxsup']} rxamt={row['rxamt']}")

    # The inflation must not propagate to index candidates.
    cand = _rows_by_name(con, "index_candidates")
    assert len(cand) == 1
    assert cand[0]["numdispensing"] == 1
    assert cand[0]["rxsup"] == 30
