"""Scratch: check the F1 regression scenario at base (c3e60db)."""

from datetime import date
from pathlib import Path
import tempfile

import duckdb

from qrp import load_study_dict, run


def build(indata: Path) -> None:
    rows = {
        "demographic": [(1, date(1980, 5, 1), "F"),
                        (2, date(1955, 3, 3), "M")],
        "enrollment": [(1, date(2011, 1, 1), date(2011, 12, 31), "Y", "Y"),
                       (2, date(2011, 1, 1), date(2011, 12, 31), "Y", "Y")],
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


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        indata = Path(tmp) / "indata"
        indata.mkdir()
        build(indata)
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
                {"group": "c2", "indexcriteria": "DEF",
                 "code": "Z99999", "codecat": "RX"},
            ],
        })
        study.validate()
        result = run(study, str(indata), verbose=False)
        con = result.engine.con
        for t in ("cohort_final", "ptsmasterlist", "stockpiled"):
            print(t, con.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
        print("attrition:")
        for r in con.execute(
                'SELECT "group", level, remaining, excluded FROM attrition '
                'WHERE level IN (1.0, 2.0, 6.0, 7.0, 12.0, 16.0, 27.0) '
                'ORDER BY "group", level').fetchall():
            print(f"  {r[0]} L{r[1]:<5} rem={r[2]!r} exc={r[3]!r}")


if __name__ == "__main__":
    main()
