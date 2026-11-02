"""Arbiter probe: A-4 — diagnosis optional columns unconditionally
referenced (INPUTS.md:180 declares dx_codetype/pdx/enctype optional).

Builds a minimal SCDM extract whose diagnosis table carries only the
REQUIRED columns (patid, adate, dx) and runs the production pipeline.
Expected if the defect is present: duckdb.BinderException naming one of
the missing columns at the first stage. Works at any revision.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

import duckdb

from qrp import load_study_dict, run

SCDM_TABLES = {
    "enrollment": (("patid", "BIGINT"), ("enr_start", "DATE"),
                   ("enr_end", "DATE"), ("medcov", "VARCHAR"),
                   ("drugcov", "VARCHAR")),
    "demographic": (("patid", "BIGINT"), ("birth_date", "DATE"),
                    ("sex", "VARCHAR")),
    "dispensing": (("patid", "BIGINT"), ("rxdate", "DATE"),
                   ("rx", "VARCHAR"), ("rxsup", "INTEGER"),
                   ("rxamt", "DOUBLE")),
    # diagnosis carries ONLY the required columns: no dx_codetype, pdx,
    # enctype — the declared-optional trio (INPUTS.md:180).
    "diagnosis": (("patid", "BIGINT"), ("adate", "DATE"),
                  ("dx", "VARCHAR")),
}

ROWS = {
    "demographic": [(1, date(1980, 5, 1), "F")],
    "enrollment": [(1, date(2011, 1, 1), date(2011, 12, 31), "Y", "Y")],
    "dispensing": [(1, date(2011, 9, 15), "E00001", 30, 10.0)],
    "diagnosis": [(1, date(2011, 9, 16), "D00001")],
}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        indata = Path(tmp) / "indata"
        indata.mkdir()
        con = duckdb.connect()
        try:
            for table, schema in SCDM_TABLES.items():
                cols = ", ".join(f"{name} {typ}" for name, typ in schema)
                con.execute(f"CREATE TABLE t ({cols})")
                if ROWS.get(table):
                    placeholders = ", ".join("?" for _ in schema)
                    con.executemany(
                        f"INSERT INTO t VALUES ({placeholders})",
                        ROWS[table])
                con.execute(
                    f"COPY t TO '{indata / f'{table}.parquet'}' "
                    "(FORMAT PARQUET)")
                con.execute("DROP TABLE t")
        finally:
            con.close()
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
        try:
            result = run(study, str(indata), verbose=False)
        except duckdb.BinderException as exc:
            print(f"REPRODUCED (BinderException): {exc}")
            return 1
        n = result.engine.count("cdm_diagnosis")
        print(f"OK — pipeline ran; cdm_diagnosis rows={n}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
