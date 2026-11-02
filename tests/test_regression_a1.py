"""Regression: duplicate cohortgrp rows in cohortfile must be rejected
(investigation finding A-1, arbitrator fix branch).

`StudyConfig.validate` documents the rule — it raises
"duplicate cohortgrp ..." for a study whose cohorts repeat a group — but
`load_study_dict` folded cohortfile rows into a dict keyed by cohortgrp,
so the LAST duplicate row silently won and the guard was unreachable
from any input file. A doubled cohort row with conflicting parameters
(enrdays 30 vs 200) was silently resolved instead of rejected.

The loader rejects duplicates the same way everywhere else it keys rows
by a group id: stockpilingfile raises "duplicate row for group ...,
stockgroup ...". The covariates loader is different BY DESIGN —
COVARIATECODES legitimately carries one row per code with covarnum
repeated, and rows are merged per covarnum (first row's windows, union
of the codes) — so duplicate covarnum rows remain accepted here.

Standalone: pure config translation, no database, no campaign
infrastructure.
"""

import pytest

from qrp import load_study_dict


def _study(cohortfile_rows):
    return load_study_dict({
        "qrp_parameters_scalars": {
            "runid": "regr", "type": 2,
            "startdate": "2016-01-01", "enddate": "2016-12-31",
        },
        "cohortfile": cohortfile_rows,
        "cohortcodes": [
            {"group": "g1", "indexcriteria": "DEF",
             "code": "00700", "codecat": "RX", "codetype": "ND"},
        ],
    })


def test_duplicate_cohortgrp_rows_are_rejected():
    """Two cohortfile rows naming the same group must raise the
    documented duplicate-cohortgrp error, not silently keep the last
    row's parameters."""
    with pytest.raises(ValueError, match="duplicate cohortgrp"):
        _study([
            {"cohortgrp": "g1", "enrdays": 30},
            {"cohortgrp": "g1", "enrdays": 200},
        ])


def test_distinct_cohortgrp_rows_still_accepted():
    """The rejection must not fire for a normal multi-cohort file."""
    study = _study([
        {"cohortgrp": "g1", "enrdays": 30},
        {"cohortgrp": "g2", "enrdays": 200},
    ])
    assert [c.cohortgrp for c in study.cohorts] == ["g1", "g2"]
    assert study.cohorts[0].enr_days == 30
    assert study.cohorts[1].enr_days == 200
