"""Regression: the CIDA denominator must split an eligible window at age
thresholds like SAS, not band the whole window at its start (finding F2,
arbitrator fix branch).

SAS's denominator (%ms_cidadenom, ms_cidadenom.sas:1036-1057) walks the
age thresholds oldest-first and OUTPUTS one span per band the eligible
window overlaps, shrinking the window at each threshold — a member
counts in EVERY age band their eligible time touches, with member-days
split accordingly. The numerator bands each episode at its index date
(ms_createpov1.sas:102-116 → %ms_agestrat, STARTDT=birth_date,
ENDDT=ADate→IndexDt). Because an index date always lies inside the
eligible window, its band is one of the bands the denominator splits
out, so npts <= dennumpts holds per cell in SAS — and ms_cidatables'
merge (ms_cidatables.sas:272-285) writes DenNumPts=0 for a numerator row
whose denominator cell is missing.

Band the whole window at denom_start instead, and a member whose window
crosses their birthday into the next band is counted ONLY in the earlier
band: an index at 78 then lands in a 75+ numerator cell whose
denominator holds nobody, and t2_cida reports npts > dennumpts.

The fixture: one member born 1936-06-15, enrolled 2009-01-01..2011-12-31,
dispensing on 2011-10-01 (age 75 → 75+). Query period 2011 (so the
eligible window starts at 2011-01-01, age 74 → 65-74) spans the 75th
birthday 2011-06-15. Study strata "65-74 75+", t2cida level on agegroup.

Standalone: builds its own SCDM parquet extract with duckdb and runs the
production pipeline. No campaign infrastructure.
"""

from datetime import date
from pathlib import Path

import duckdb

from qrp import load_study_dict, run


def _write_extract(indata: Path) -> None:
    indata.mkdir(parents=True)
    rows = {
        "demographic": [(1, date(1936, 6, 15), "F")],
        "enrollment": [(1, date(2009, 1, 1), date(2011, 12, 31), "Y", "Y")],
        "dispensing": [(1, date(2011, 10, 1), "E00001", 30, 10.0)],
        "diagnosis": [],
    }
    schemas = {
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
    con = duckdb.connect()
    try:
        for table, schema in schemas.items():
            cols = ", ".join(f"{n} {t}" for n, t in schema)
            con.execute(f"CREATE TABLE t ({cols})")
            if rows[table]:
                ph = ", ".join("?" for _ in schema)
                con.executemany(f"INSERT INTO t VALUES ({ph})", rows[table])
            con.execute(
                f"COPY t TO '{indata / f'{table}.parquet'}' (FORMAT PARQUET)")
            con.execute("DROP TABLE t")
    finally:
        con.close()


def _study():
    study = load_study_dict({
        "qrp_parameters_scalars": {
            "runid": "regr", "type": 2,
            "startdate": "2011-01-01", "enddate": "2011-12-31",
        },
        "cohortfile": [{"cohortgrp": "c1", "coverage": "MD",
                        "enrolgap": 0, "enrdays": 183,
                        "agestrat": "65-74 75+"}],
        "cohortcodes": [
            {"group": "c1", "indexcriteria": "DEF",
             "code": "E00001", "codecat": "RX"},
        ],
        "userstrata": [
            {"tableid": "t2cida", "levelid": "1", "levelvars": "agegroup"},
        ],
    })
    study.validate()
    return study


def _rows(con, sql):
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def test_denominator_covers_every_band_the_window_spans(tmp_path: Path):
    """npts <= dennumpts at every t2_cida cell, and the denominator
    holds the threshold-crossing member in BOTH bands with member-days
    split at the birthday — SAS's per-band span split."""
    indata = tmp_path / "indata"
    _write_extract(indata)
    result = run(_study(), str(indata), verbose=False)
    con = result.engine.con

    # the episode survived, banded at its index date (age 75 -> 75+)
    num = _rows(con, """
        SELECT agegroup, npts FROM numcounts
        WHERE level = '1' AND "group" = 'c1'""")
    assert {r["agegroup"]: r["npts"] for r in num} == {"75+": 1}

    # the eligible window (clipped to the query period: 2011-01-01,
    # age 74) crosses the 75th birthday 2011-06-15: the denominator
    # must emit BOTH bands
    den = _rows(con, """
        SELECT agegroup, dennumpts, dennummemdays FROM denomcounts
        WHERE level = '1' AND "group" = 'c1' ORDER BY agegroup""")
    den_by_band = {r["agegroup"]: r for r in den}
    assert set(den_by_band) == {"65-74", "75+"}, (
        f"denominator must split the window at the age threshold, "
        f"got bands {sorted(den_by_band)}")
    assert den_by_band["65-74"]["dennumpts"] == 1
    assert den_by_band["75+"]["dennumpts"] == 1
    # the split conserves member-days: 2011-01-01..2011-06-14 is 165
    # days in 65-74; 2011-06-15..2011-12-31 is 200 days in 75+
    assert den_by_band["65-74"]["dennummemdays"] == 165
    assert den_by_band["75+"]["dennummemdays"] == 200

    # the study deliverable: npts <= dennumpts at every cell
    for row in _rows(con, """
            SELECT npts, dennumpts, agegroup FROM t2_cida
            WHERE level = '1' AND "group" = 'c1'"""):
        assert row["npts"] <= row["dennumpts"], (
            f"numerator exceeds denominator in the {row['agegroup']} "
            f"cell: npts={row['npts']} dennumpts={row['dennumpts']}")
