"""Regression: cohort_final.person_days must not go negative.

Investigation finding C-3: 60_followup.sql computes person-time as
span_days(atriskindexdt, least(event, episodeend)) with NO
greatest(0, ...) floor — unlike followuptime on the very same table,
which SAS defines as Max(0, ...) (ms_finalizeptsmasterlist.sas:305)
and which the SQL reproduces with greatest(0, ...). When a cohort sets
t2atriskstart larger than the remaining episode (atriskindexdt is past
episodeenddt), the span is negative, and those negative rows are summed
into the denominators table (70_outputs.sql sum(person_days)), shrinking
person-time and inflating rate_per_1000_py.

SAS never produces negative person-time anywhere in the Type 2 path:
the at-risk span it carries (followuptime under ATRISKSTART > 0) is
floored at 0, episodes with no at-risk day are deleted outright
(ms_createptsmasterlist.sas:175-176), and there is a defensive
IndexDt > EpisodeEndDt delete (:189).

Standalone: builds a two-table-patient SCDM root as parquet inline (no
campaign infrastructure, no /tmp/qrp_data dependency) and runs the real
pipeline. The witness episode: one 30-day dispensing, at_risk_start 60
— the at-risk window starts 30 days after the episode is over.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from qrp import Engine, load_study_dict, run


def _study():
    return load_study_dict({
        "qrp_parameters": [{
            "type": 2, "runid": "c3regr",
            "startdate": "2014-01-01", "enddate": "2015-06-30",
            "censordate": "2015-06-30",
        }],
        "cohortfile": [{
            "cohortgrp": "g1", "enrollmentnum": 1, "coverage": "MD",
            "enrolgap": 45, "chartres": "N", "enrdays": 183,
            "agestrat": "18-64", "reqdaysaftind": 0,
        }],
        "type2file": [{
            "group": "g1", "t2washper": 183, "point": "N",
            "episodegap": 15, "episodegaptype": "F", "expextper": 0,
            "minepisdur": 1, "maxepisdur": 365, "t2fupwashper": 183,
            "eventcount": 1, "censor_dth": "Y",
            "t2atriskstart": 60, "blackoutper": 0, "reqdaysaftepi": 0,
        }],
        "cohortcodes": [
            {"group": "g1", "indexcriteria": "DEF",
             "code": "E00001", "codecat": "RX"},
            {"group": "g1", "indexcriteria": "EVENT",
             "code": "D00001", "codecat": "DX"},
        ],
    })


def _write_scdm(root: Path) -> None:
    """A minimal SCDM root: one patient whose only episode ends 30 days
    after the index dispensing, while the at-risk window starts 60 days
    after it. No outcome events, so person-time runs to the episode end."""
    con = duckdb.connect()
    con.execute(f"""
        COPY (
            SELECT * FROM (VALUES
                (1, DATE '2013-01-01', DATE '2015-01-31', 'Y', 'Y')
            ) t(patid, enr_start, enr_end, medcov, drugcov)
        ) TO '{root / "enrollment.parquet"}' (FORMAT PARQUET)
    """)
    con.execute(f"""
        COPY (
            SELECT * FROM (VALUES
                (1, DATE '1980-06-15', 'F')
            ) t(patid, birth_date, sex)
        ) TO '{root / "demographic.parquet"}' (FORMAT PARQUET)
    """)
    con.execute(f"""
        COPY (
            SELECT * FROM (VALUES
                (1, DATE '2014-06-01', 'E00001', 30, 2.0)
            ) t(patid, rxdate, rx, rxsup, rxamt)
        ) TO '{root / "dispensing.parquet"}' (FORMAT PARQUET)
    """)
    # The diagnosis optionals (dx_codetype/pdx/enctype) are declared
    # optional in scdm.py but referenced unconditionally in normalize
    # (a recorded production lead, not this branch's scope), so the
    # fixture carries them the way real extracts do.
    con.execute(f"""
        COPY (
            SELECT * FROM (VALUES
                (1, DATE '2014-01-01', 'X999', 'ICD10', 'N', 'OA')
            ) t(patid, adate, dx, dx_codetype, pdx, enctype)
            WHERE 1 = 0
        ) TO '{root / "diagnosis.parquet"}' (FORMAT PARQUET)
    """)
    con.close()


def test_person_days_never_goes_negative(tmp_path):
    """at_risk_start past the episode end means zero days at risk — the
    row must carry person_days 0 (like followuptime), not a negative
    span that the denominators table then sums."""
    import warnings

    _write_scdm(tmp_path)
    study = _study()

    eng = Engine(verbose=False)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            run(study, str(tmp_path), engine=eng, verbose=False)
        rows = eng.con.execute("""
            SELECT atriskindexdt, episodeenddt, followuptime, person_days
            FROM cohort_final
        """).fetchall()
    finally:
        eng.close()

    assert rows, "witness episode did not survive to cohort_final"
    # Witness shape: the at-risk window really does start after the
    # episode is over (otherwise this fixture proves nothing).
    assert all(arsk > end for arsk, end, _, _ in rows), rows
    # followuptime is already floored at 0 (SAS parity); the same span
    # expressed as person_days must be too.
    assert all(fup == 0 for _, _, fup, _ in rows), rows
    bad = [(arsk, end, pd) for arsk, end, _, pd in rows if pd != 0]
    assert not bad, (
        f"person_days is not floored at 0 for episodes whose at-risk "
        f"window starts after the episode end: {bad}"
    )
