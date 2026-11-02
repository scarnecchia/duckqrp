"""Regression: attrition must count empty relations as 0, not carry the
last member count forward (investigation finding F1, arbitrator fix
branch).

The attrition funnel's own level descriptions declare the contract:
level 27 is "Information - Unique number of members in final cohort",
and every "Exclusion -" level counts the rows surviving that step.
SAS's %ms_attrition_cidacompute computes each counted step's remaining
with `proc sql select count(*) ... into :remaining` over the step's
data — an EMPTY relation counts 0, never missing — and only SKIP rows
carry the previous count forward with excluded=0
(ms_attrition_cidacompute.sas:77-88, :112-119).

The SQL funnel instead computed each counted level with a GROUP BY over
the step's relation: an empty relation produced NO row, the count came
back NULL, and `coalesce(n, last_value(n IGNORE NULLS))` — meant for
the blank carry rows — forward-filled the last MEMBER count through the
whole episode block and into level 27. A cohort whose members have no
cohort-identifying claims reported a nonzero final member count against
an empty cohort_final, and none of its episode steps recorded an
exclusion.

The fix counts a missing count row as 0 at every counted step (SAS
parity): the first step whose relation is empty absorbs the whole drop
as its exclusion, later steps report 0, and level 27 reports the actual
final cohort size. Extracts where every relation is non-empty are
unchanged.

Standalone: builds its own SCDM parquet extract with duckdb and runs
the production pipeline. No campaign infrastructure.
"""

from datetime import date
from pathlib import Path

import duckdb

from qrp import load_study_dict, run


def _write_extract(indata: Path) -> None:
    indata.mkdir(parents=True)
    rows = {
        "demographic": [(1, date(1980, 5, 1), "F"),
                        (2, date(1955, 3, 3), "M")],
        # both members enrolled all year under both cohorts' coverage
        "enrollment": [(1, date(2011, 1, 1), date(2011, 12, 31), "Y", "Y"),
                       (2, date(2011, 1, 1), date(2011, 12, 31), "Y", "Y")],
        # only cohort c1's index code is ever dispensed, and only for
        # member 1; c2's code never appears, so c2 has no claims at all
        "dispensing": [(1, date(2011, 9, 15), "E00001", 30, 10.0)],
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
        "cohortfile": [
            {"cohortgrp": "c1", "coverage": "MD",
             "enrolgap": 0, "enrdays": 183},
            {"cohortgrp": "c2", "coverage": "MD",
             "enrolgap": 0, "enrdays": 183},
        ],
        "cohortcodes": [
            {"group": "c1", "indexcriteria": "DEF",
             "code": "E00001", "codecat": "RX"},
            # never dispensed: cohort c2 counts members, then no claims
            {"group": "c2", "indexcriteria": "DEF",
             "code": "Z99999", "codecat": "RX"},
        ],
    })
    study.validate()
    return study


def _scalar(con: duckdb.DuckDBPyConnection, sql: str):
    row = con.execute(sql).fetchone()
    return row[0] if row else None


def _attrition(con, group: str, level: float):
    return con.execute(
        'SELECT remaining, excluded FROM attrition '
        'WHERE "group" = ? AND level = ?', [group, level]).fetchone()


def test_cohort_with_no_claims_counts_zero_not_carried(tmp_path: Path):
    """Cohort c2 has members but no cohort-identifying claims: every
    counted step after the member levels must report 0 — the funnel
    must not forward-fill member counts through the episode block, and
    level 27 must not report members that do not exist."""
    indata = tmp_path / "indata"
    _write_extract(indata)
    result = run(_study(), str(indata), verbose=False)
    con = result.engine.con

    final_c2 = _scalar(con, "SELECT count(DISTINCT patid) FROM cohort_final "
                            "WHERE cohortgrp = 'c2'")
    assert final_c2 == 0, "c2 has no claims; nothing can reach cohort_final"

    # the member-count steps survive (2 enrolled members)
    assert _attrition(con, "c2", 1.0) == (2, None)
    assert _attrition(con, "c2", 2.0) == (2, 0)
    # level 6 (at least one claim with cohort-identifying codes) is the
    # first counted step over an empty relation: remaining 0, and the
    # whole drop is its exclusion — SAS's count(*) semantics
    assert _attrition(con, "c2", 6.0) == (0, 2)
    # level 7 (initial episode count) counts the same empty relation
    assert _attrition(con, "c2", 7.0) == (0, None)
    # later episode steps count 0 with nothing left to exclude
    assert _attrition(con, "c2", 12.0) == (0, 0)
    assert _attrition(con, "c2", 16.0) == (0, 0)
    # level 27: unique members in the FINAL cohort — none
    assert _attrition(con, "c2", 27.0) == (0, None)


def test_cohort_with_surviving_episode_counts_real_values(tmp_path: Path):
    """Cohort c1 has one dispensing that forms a surviving episode: its
    counted steps must keep reporting the real counts — the fix changes
    empty-relation counts only."""
    indata = tmp_path / "indata"
    _write_extract(indata)
    result = run(_study(), str(indata), verbose=False)
    con = result.engine.con

    final_c1 = _scalar(con, "SELECT count(DISTINCT patid) FROM cohort_final "
                            "WHERE cohortgrp = 'c1'")
    assert final_c1 == 1
    # member 2 has no c1 claims and is excluded at level 6
    assert _attrition(con, "c1", 6.0) == (1, 1)
    assert _attrition(con, "c1", 7.0) == (1, None)
    # the episode passes the counted steps and reaches the final cohort
    assert _attrition(con, "c1", 12.0) == (1, 0)
    assert _attrition(con, "c1", 27.0) == (1, None)
