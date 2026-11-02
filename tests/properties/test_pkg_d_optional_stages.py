"""Package D — each optional/output stage against an independent
aggregation oracle.

One parametrized behavioral property per stage family; every case asserts
REAL computed values (windows, counts, sums, ranks), never a schema
smoke check. Oracles read the stage's INPUT relations and re-derive the
stage's documented semantics in Python (see oracles/d_stages.py); float
aggregations are compared at 9 decimal places, integer counts and dates
exactly.

Clinical-scoring caveat: risk-score WEIGHTS are tested as documented
mappings and arithmetic (max per condition, sum, intercept) only. Whether
any weight set is clinically correct is external to this repository and
is NOT asserted anywhere.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from hypothesis import assume, event, given, strategies as st

from qrp.config import Covariate, RiskScoreCode

from .conftest import expensive_settings
from .harness import StageHarness
from .oracles import d_stages
from .oracles.d_denom import expected_denomcounts
from .oracles.d_fixtures import (
    ALL_CODES,
    ALL_EVENTS,
    CIDA_LEVELS,
    FUTU_LEVELS,
    study_with_features,
)
from .strategies import SEEDS, generate_options
from .synth import (
    Dispensing,
    EnrollmentSpan,
    GenerateOptions,
    Patient,
    SyntheticDataset,
    generate,
)

_START = date(2014, 1, 1)
_END = date(2014, 12, 31)
CODES = ALL_CODES[:8]
EVENTS = ALL_EVENTS[:4]


def _cases() -> dict[str, dict]:
    # DX covariate: every outcome code, 1-year pre-index lookback, so a
    # hit is likely on ordinary draws (diagnoses draw ~40% outcome codes
    # from a 20-code pool). RX covariates: the full exposure pool — the
    # day-0 window must catch each episode's own index fill, whose code
    # is by definition an exposure code, so covariates_long has a row
    # for every master-list episode and the comparison cannot be empty.
    cov_pre_dx = (Covariate(1, "pre_dx", "DX", covfrom=-365, covto=-1, codes=EVENTS),)
    cov_two = cov_pre_dx + (
        Covariate(
            2, "post_rx", "RX", covfrom=1, covto=30, dateonly=True, codes=ALL_CODES
        ),
        Covariate(
            3, "index_rx", "RX", covfrom=0, covto=0, dateonly=True, codes=ALL_CODES
        ),
    )
    util_med_drug = (("", "MED", -30, 30), ("", "DRUG", -60, 0))
    classes_a = ((CODES[0], "classA", "genA"),)
    cci_scores = (
        RiskScoreCode("CCI", "IN", "IN", weight=1.0),
        RiskScoreCode(
            "CCI", "c1", "DX", code=EVENTS[0], weight=2.0, riskfrom=-30, riskto=30
        ),
    )
    return {
        "code_distribution": dict(stage="code distribution"),
        "mfu": dict(
            stage="most frequent use",
            mfu=(
                ("cohort_a", 1, "DX", "CODECOUNT", 5, -90, 30, "09"),
                ("cohort_a", 2, "RX", "PATCOUNT", 3, -60, 0, None),
                (None, 3, "DX", "CODECOUNT", 2, -365, 0, None),
            ),
        ),
        "labs": dict(
            stage="labs",
            labs=True,
            lab_codes=(
                (
                    "cohort_a",
                    "2160-0",
                    "LRO",
                    "02",
                    "N",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    ">",
                    50.0,
                    None,
                ),
                (
                    "cohort_a",
                    "PX1",
                    "LRO",
                    "99",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
                (
                    "cohort_b",
                    "2160-0",
                    "LRO",
                    "02",
                    "N",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    ":",
                    60.0,
                    140.0,
                ),
            ),
        ),
        "utilization": dict(
            stage="utilization",
            utilization=util_med_drug,
            drug_classes=(
                (CODES[0], "classA", "genA"),
                (CODES[1], "classB", "genA"),
                ("N00001", "classA", "genB"),
            ),
        ),
        "geography": dict(stage="geography"),
        "risk_scores": dict(
            stage="risk scores",
            risk_scores=(
                RiskScoreCode("CCI", "IN", "IN", weight=1.0),
                RiskScoreCode(
                    "CCI",
                    "c1",
                    "DX",
                    code=EVENTS[0],
                    weight=2.0,
                    riskfrom=-30,
                    riskto=30,
                ),
                RiskScoreCode(
                    "CCI",
                    "c2",
                    "RX",
                    code=CODES[0],
                    weight=3.0,
                    riskfrom=-60,
                    riskto=-1,
                ),
                RiskScoreCode("FRAIL", "d1", "DM", code="F", weight=2.5),
                RiskScoreCode("FRAIL", "IN", "IN", weight=0.5),
            ),
        ),
        "covariates": dict(stage="covariates", covariates=cov_two),
        "baseline": dict(
            stage="baseline",
            covariates=cov_pre_dx,
            utilization=util_med_drug,
            drug_classes=classes_a,
            risk_scores=cci_scores,
        ),
        "cida_tables": dict(stage="cida tables"),
        "followuptime": dict(stage="follow-up time table"),
        "mstr_wide": dict(stage="master list columns", covariates=cov_pre_dx),
        "attrition_person_time": dict(stage="attrition + denominators"),
    }


_CASE_NAMES = tuple(_cases())


@given(
    patients=st.integers(2, 5),
    seed=SEEDS,
    options=generate_options(defaults=GenerateOptions(include_labs=True)),
)
@expensive_settings()
@pytest.mark.parametrize("case_name", _CASE_NAMES)
def test_optional_stage_matches_reference(
    case_name: str, patients: int, seed: int, options
) -> None:
    """Every optional stage's real output equals the independent
    aggregation oracle over its documented inputs."""
    from dataclasses import replace

    spec = _cases()[case_name]
    if case_name == "labs":
        options = replace(options, include_labs=True)
    dataset = generate(
        patients=patients, seed=seed, start=_START, end=_END, options=options
    )
    study = _study_for(spec, dataset)

    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        if spec["stage"] == "master list columns":
            _check_mstr_wide(harness, dataset, study, spec)
        else:
            harness.run_through(spec["stage"])
            # Anti-vacuity: a draw whose cohorts produced no episodes
            # would compare empty output against an equally empty oracle
            # and "pass" without exercising the stage. Reject such
            # draws so the profile statistics report the fixture's real
            # yield; every surviving example has rows to check.
            assume(_rows(harness, "ptsmasterlist"))
            _check_tables(harness, dataset, study, spec, case_name)
    event(f"{case_name}-ran")


def _study_for(spec: dict, dataset) -> object:
    zip_rows: tuple[
        tuple[str, str | None, str | None, str | None, float | None], ...
    ] = ()
    if spec["stage"] == "geography":
        seen = list({p.postalcode for p in dataset.patients})
        zip_rows = tuple(
            (
                z,
                "MA" if i == 0 else "CA",
                "1" if i % 2 else "2",
                "Northeast" if i % 3 else "other",
                10.0 * i + 5.0,
            )
            for i, z in enumerate(seen)
        )
    # the FULL generated exposure pool: splitting all 40 codes across
    # the two cohorts lets any exposure-code fill found a cohort
    # episode; slicing [:8] starved membership on most draws.
    return study_with_features(
        dataset.exposure_codes,
        EVENTS,
        covariates=spec.get("covariates", ()),
        risk_scores=spec.get("risk_scores", ()),
        utilization=spec.get("utilization", ()),
        drug_classes=spec.get("drug_classes", ()),
        lab_codes=spec.get("lab_codes", ()),
        mfu=spec.get("mfu", ()),
        zipfile=zip_rows,
        strata=spec.get("strata", CIDA_LEVELS + FUTU_LEVELS),
        start=_START,
        end=_END,
    )


def _rows(harness, table: str) -> list[dict]:
    return d_stages.table_dicts(harness.engine.con, table)


def _check_tables(harness, dataset, study, spec, case_name: str) -> None:
    # outcome tables below are guaranteed nonempty once the master list
    # has episodes (verified empirically across the draw space); the
    # assert before each comparison turns a fixture regression into a
    # loud failure instead of an empty-vs-empty "pass".
    if case_name == "code_distribution":
        for table in ("distindexmap", "distindex", "distindex_episodes"):
            assert _rows(harness, table), table
        got = d_stages.expected_distindex(
            _rows(harness, "cohort_final"),
            _rows(harness, "stockpiled"),
            _envdx(harness),
            _envpx(harness),
            _rows(harness, "cdm_dispensing"),
            _rows(harness, "cfg_codes"),
            _rows(harness, "cfg_care_setting"),
        )
        for table in ("distindexmap", "distindex", "distindex_episodes"):
            assert d_stages.canonical(_rows(harness, table)) == d_stages.canonical(
                got[table]
            ), table
    elif case_name == "mfu":
        # mfu emits rows only for analyses with at least one qualifying
        # member claim (78_mfu.sql claims CTE). A draw whose members
        # have no diagnosis in the widest window (-365..0) ranks
        # nothing; reject such draws instead of comparing empty against
        # empty — evidence mirrors the cfg's catch-all DX analysis.
        assume(
            any(
                m["indexdt"] - timedelta(days=365) <= d["adate"] <= m["indexdt"]
                for m in _rows(harness, "ptsmasterlist")
                for d in _rows(harness, "cdm_diagnosis")
                if d["patid"] == m["patid"]
            )
        )
        assert _rows(harness, "mfu")
        assert d_stages.canonical(_rows(harness, "mfu")) == d_stages.canonical(
            d_stages.expected_mfu(
                _rows(harness, "ptsmasterlist"),
                _envdx(harness),
                _rows(harness, "cdm_dispensing"),
                _rows(harness, "cfg_mfu"),
            )
        )
    elif case_name == "labs":
        # the fixture forces include_labs, so raw lab rows exist; the
        # RESULT rows can legitimately be empty (window/result-type
        # misses), so only the raw input is asserted here — the result
        # and summary comparisons stay exact through the oracle.
        assert _rows(harness, "cdm_lab")
        assert d_stages.canonical(_rows(harness, "lab_results")) == d_stages.canonical(
            d_stages.expected_labs(
                _rows(harness, "ptsmasterlist"),
                _rows(harness, "cdm_lab"),
                _rows(harness, "cfg_labcodes"),
            )
        )
        assert d_stages.canonical(_rows(harness, "lab_summary")) == d_stages.canonical(
            d_stages.expected_lab_summary(
                d_stages.expected_labs(
                    _rows(harness, "ptsmasterlist"),
                    _rows(harness, "cdm_lab"),
                    _rows(harness, "cfg_labcodes"),
                )
            )
        )
    elif case_name == "utilization":
        assert _rows(harness, "utilization")
        expected = d_stages.expected_utilization(
            _rows(harness, "ptsmasterlist"),
            _rows(harness, "cdm_encounter")
            if _has(harness, "cdm_encounter")
            else _rows(harness, "encounter"),
            _rows(harness, "cdm_dispensing"),
            _rows(harness, "cfg_utilization"),
            {
                c["cohortgrp"]
                for c in _rows(harness, "cfg_cohort")
                if c["create_baseline"]
            },
            _rows(harness, "cfg_drugclass"),
        )
        assert d_stages.canonical(_rows(harness, "utilization")) == d_stages.canonical(
            expected
        )
        assert d_stages.canonical(
            _rows(harness, "utilization_summary")
        ) == d_stages.canonical(d_stages.expected_utilization_summary(expected))
    elif case_name == "geography":
        assert _rows(harness, "geography")
        assert d_stages.canonical(_rows(harness, "geography")) == d_stages.canonical(
            d_stages.expected_geography(
                _rows(harness, "ptsmasterlist"), _rows(harness, "cfg_zipfile")
            )
        )
    elif case_name == "risk_scores":
        assert _rows(harness, "risk_scores")
        expected = d_stages.expected_risk_scores(
            _rows(harness, "ptsmasterlist"),
            _rows(harness, "cohort_claims"),
            _rows(harness, "cfg_risk_codes"),
        )
        assert d_stages.canonical(_rows(harness, "risk_scores")) == d_stages.canonical(
            expected
        )
        assert d_stages.canonical(
            _rows(harness, "risk_score_summary")
        ) == d_stages.canonical(d_stages.expected_risk_summary(expected))
    elif case_name == "covariates":
        assert _rows(harness, "covariates_long")
        cov_cfg = _rows(harness, "cfg_covariates")
        codes: dict[int, set] = {}
        for r in _rows(harness, "cfg_covariate_codes"):
            codes.setdefault(int(r["covarnum"]), set()).add(
                (str(r["code"]), str(r["codecat"]))
            )
        assert d_stages.canonical(
            _rows(harness, "covariates_long")
        ) == d_stages.canonical(
            d_stages.expected_covariates(
                _rows(harness, "ptsmasterlist"),
                _rows(harness, "cohort_claims"),
                cov_cfg,
                codes,
                _spans(harness, study),
                study.start_date,
            )
        )
    elif case_name == "baseline":
        assert _rows(harness, "baseline_long")
        baseline_grps = {
            c["cohortgrp"] for c in _rows(harness, "cfg_cohort") if c["create_baseline"]
        }
        seen_labels: dict[str, None] = {}
        for c in study.cohorts:
            if c.cohortgrp in baseline_grps:
                for band in c.age_strata.strata:
                    seen_labels[band.label] = None
        age_labels = tuple(seen_labels)
        assert d_stages.canonical(
            _rows(harness, "baseline_long")
        ) == d_stages.canonical(
            d_stages.expected_baseline_long(
                _rows(harness, "cohort_final"),
                _rows(harness, "utilization"),
                _rows(harness, "risk_scores"),
                baseline_grps,
                tuple(
                    sorted(
                        {
                            int(r["covarnum"])
                            for r in _rows(harness, "cfg_covariate_codes")
                        }
                    )
                ),
                _rows(harness, "covariates_long"),
                age_labels,
                study.start_date,
                study.end_date,
            )
        )
    elif case_name == "cida_tables":
        assert _rows(harness, "numcounts")
        assert _rows(harness, "t2_cida")
        num = d_stages.expected_numcounts(
            _rows(harness, "cohort_final"), _rows(harness, "cfg_strata")
        )
        assert d_stages.canonical(_rows(harness, "numcounts")) == d_stages.canonical(
            num
        )
        denom = expected_denomcounts(dataset, study)
        assert d_stages.canonical(_rows(harness, "t2_cida")) == d_stages.canonical(
            d_stages.expected_t2_cida(num, denom)
        )
    elif case_name == "followuptime":
        assert _rows(harness, "followuptime")
        assert d_stages.canonical(_rows(harness, "followuptime")) == d_stages.canonical(
            d_stages.expected_followuptime(
                _rows(harness, "cohort_final"),
                _rows(harness, "episode_flags"),
                _rows(harness, "cfg_strata"),
                {c["cohortgrp"] for c in _rows(harness, "cfg_cohort")},
            )
        )
    elif case_name == "attrition_person_time":
        for table in ("denominators", "censoring", "attrition"):
            assert _rows(harness, table), table
        assert d_stages.canonical(_rows(harness, "denominators")) == d_stages.canonical(
            d_stages.expected_denominators(_rows(harness, "cohort_final"))
        )
        assert d_stages.canonical(_rows(harness, "censoring")) == d_stages.canonical(
            d_stages.expected_censoring(
                _rows(harness, "cohort_final"),
                _rows(harness, "episode_flags"),
                _rows(harness, "cfg_strata"),
                study.end_date,
                study.effective_censor_date,
            )
        )
        assert d_stages.canonical(_rows(harness, "attrition")) == d_stages.canonical(
            d_stages.expected_attrition(
                _rows(harness, "demographics"),
                _rows(harness, "cdm_enrollment"),
                _rows(harness, "_att_spans"),
                _rows(harness, "stockpiled"),
                _rows(harness, "cfg_cohort"),
                _rows(harness, "cfg_age_strata"),
                _rows(harness, "episode_criteria"),
                _rows(harness, "inclusion_excluded")
                if _has(harness, "inclusion_excluded")
                else [],
                _rows(harness, "episode_hoi"),
                _rows(harness, "cohort_final"),
                _rows(harness, "cfg_inclusion"),
                study.start_date,
                study.end_date,
            )
        )


def _check_mstr_wide(harness, dataset, study, spec) -> None:
    """Run to the stage before 62, capture its inputs, then run 62."""
    harness.run_through("baseline" if study.any_covariates else "follow-up + events")
    pre_final = _rows(harness, "cohort_final")
    # anti-vacuity: 62's added columns exist per surviving episode; with
    # no episodes the widened list would "match" an empty expectation.
    assume(pre_final)
    flags = _rows(harness, "episode_flags")
    harness.run_stages(["master list columns"])
    di_rows = (
        _rows(harness, "distindex_episodes")
        if _has(harness, "distindex_episodes")
        else []
    )
    expected = d_stages.expected_mstr_added(pre_final, flags, di_rows, study.end_date)
    post = _rows(harness, "cohort_final")
    keys = ("cohortgrp", "patid", "indexdt")
    added = (
        "year",
        "month",
        "quarter",
        "periodid",
        "indexlookenddt",
        "rawdisp",
        "adjusteddisp",
        "totrxsup",
        "totrxamt",
        "distindexexp",
        "distindexhoi",
        "numevents",
        "fupdays_value_cat",
        "censorcat_sort",
        "fup_elig",
        "fup_dpend",
        "fup_episend",
        "fup_spec",
        "fup_event",
        "cens_elig",
        "cens_dth",
        "cens_qryend",
        "cens_dpend",
    )
    got = [{**{k: r[k] for k in keys}, **{k: r[k] for k in added}} for r in post]
    assert d_stages.canonical(got) == d_stages.canonical(expected)


# ---- small helpers --------------------------------------------------


def _has(harness, table: str) -> bool:
    names = {
        r[0].lower()
        for r in harness.engine.con.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    return table in names


def _envdx(harness) -> list[dict]:
    rows = _rows(harness, "cdm_diagnosis")
    for r in rows:
        r["codecat"] = "DX"
    return rows


def _envpx(harness) -> list[dict]:
    rows = _rows(harness, "cdm_procedure")
    for r in rows:
        r["codecat"] = "PX"
    return rows


def _enveloped(harness) -> list[dict]:
    return _envpx(harness) + _envdx(harness)


def _spans(harness, study) -> dict[str, list]:
    out: dict[str, list] = {}
    for s in _rows(harness, "enrollment_spans"):
        out.setdefault(s["enr_cfg_id"], []).append(
            (s["patid"], s["enr_start"], s["enr_end"])
        )
    return out


# ---- F1 hand pins: the attrition empty-step contract -----------------
#
# ms_attrition_cidacompute.sas:77-88 fills :remaining from count(*) over
# the step's data — 0 for an empty relation — and :112-119 carries the
# previous count forward ONLY on skip rows. 70_outputs.sql implements
# this in its `counted` CTE (a missing count row is 0 at every
# init/excl/final step; blank rows carry). These pins exercise the funnel
# arithmetic around that contract with hand inputs and hand-derived
# exact rows: zero counted stage, skipped-stage carry, final empty
# members.

_PIN_START = date(2014, 1, 1)
_PIN_END = date(2014, 12, 31)


def _pin_member(patid: int) -> dict[str, Any]:
    return {"patid": patid, "birth_date": date(1980, 5, 1), "sex": "F"}


def _pin_demographics(n: int) -> list[dict[str, Any]]:
    return [_pin_member(i) for i in range(1, n + 1)]


def _pin_enrollment(patids: tuple[int, ...]) -> list[dict[str, Any]]:
    return [{"patid": p, "enr_start": _PIN_START, "enr_end": _PIN_END} for p in patids]


def _pin_cohorts(grps: tuple[str, ...]) -> list[dict[str, Any]]:
    return [{"cohortgrp": g, "enr_cfg_id": "E1", "censor_death": False} for g in grps]


def _pin_age_strata(grps: tuple[str, ...]) -> list[dict[str, Any]]:
    return [{"cohortgrp": g, "lo": -200, "hi": 200, "unit": "years"} for g in grps]


def _pin_episode(grp: str, patid: int, indexdt: date, **flags: bool) -> dict[str, Any]:
    row: dict[str, Any] = {"cohortgrp": grp, "patid": patid, "indexdt": indexdt}
    for name in (
        "min_enr_days_met",
        "min_enr_aft_met",
        "min_day_sup_met",
        "min_epi_dur_met",
        "post_blackout_met",
    ):
        row[name] = flags.get(name, False)
    return row


def _pin_funnel(expected: list[dict[str, Any]], cohort: str) -> list[tuple]:
    """(level, remaining, excluded) triples for one cohort's skeleton."""
    return [
        (r["level"], r["remaining"], r["excluded"])
        for r in expected
        if r["group"] == cohort
    ]


def test_attrition_zero_counted_stage_counts_zero_not_carried() -> None:
    """A counted step whose relation is empty reports remaining=0 with
    the whole drop as its exclusion; later counted steps report 0. Only
    cohort_a's stockpile relation is empty — cohort_b, sharing the same
    enrollment configuration, keeps its real counts, so the pin shows
    the zero is per-cohort, not a global collapse."""
    dem = _pin_demographics(2)
    enr = _pin_enrollment((1, 2))
    spans = [
        {"enr_cfg_id": "E1", "patid": p, "enr_start": _PIN_START, "enr_end": _PIN_END}
        for p in (1, 2)
    ]
    stockpiled = [
        {"cohortgrp": "cohort_b", "patid": 1, "adate": date(2014, 3, 1)},
        {"cohortgrp": "cohort_b", "patid": 2, "adate": date(2014, 4, 1)},
    ]
    episodes = [
        _pin_episode(
            "cohort_b",
            p,
            date(2014, 3, 1),
            **dict.fromkeys(
                (
                    "min_enr_days_met",
                    "min_enr_aft_met",
                    "min_day_sup_met",
                    "min_epi_dur_met",
                    "post_blackout_met",
                ),
                True,
            ),
        )
        for p in (1, 2)
    ]
    cohort_final = [
        {"cohortgrp": "cohort_b", "patid": 1},
        {"cohortgrp": "cohort_b", "patid": 2},
    ]
    expected = d_stages.expected_attrition(
        dem,
        enr,
        spans,
        stockpiled,
        _pin_cohorts(("cohort_a", "cohort_b")),
        _pin_age_strata(("cohort_a", "cohort_b")),
        episodes,
        [],
        [],
        cohort_final,
        [],
        _PIN_START,
        _PIN_END,
    )

    a = _pin_funnel(expected, "cohort_a")
    # levels 2-5 count 2 members from the shared enrollment spans...
    assert a[0] == (1.0, 2, None)
    assert [t[1] for t in a[1:5]] == [2, 2, 2, 2]
    # ...and level 6's stockpile relation is EMPTY: remaining 0 with the
    # whole drop (2) as the exclusion — not the carried 2 with no drop.
    assert a[5] == (6.0, 0, 2)
    # level 7 (init) reports 0; every later counted step is 0 with a 0
    # drop; blanks carry the 0.
    assert a[6] == (7.0, 0, None)
    assert all(t[1:] == (0, 0) for t in a[9:26])
    # level 27: cohort_a has no final members — the information row
    # reports 0, excluded NULL.
    assert a[26] == (27.0, 0, None)

    # cohort_b's relations are non-empty: its funnel is unchanged by the
    # empty-step contract (n0 = n wherever a count row exists).
    b = _pin_funnel(expected, "cohort_b")
    assert b[5] == (6.0, 2, 0)
    assert b[6] == (7.0, 2, None)
    assert [t[1:] for t in b[7:10]] == [(2, 0), (2, 0), (2, 0)]
    assert b[26] == (27.0, 2, None)


def test_attrition_blank_rows_carry_previous_count() -> None:
    """Blank rows carry the last counted remaining with excluded=0, and
    an exclusion's previous count comes from the last init/excl step —
    never from a blank row (the lag partition skips them)."""
    dem = _pin_demographics(3)
    enr = _pin_enrollment((1, 2, 3))
    spans = [
        {"enr_cfg_id": "E1", "patid": p, "enr_start": _PIN_START, "enr_end": _PIN_END}
        for p in (1, 2, 3)
    ]
    stockpiled = [
        {"cohortgrp": "cohort_a", "patid": p, "adate": date(2014, 3, p)}
        for p in (1, 2, 3)
    ]
    # staged flags: e1 passes everything; e2 passes enrollment+criteria
    # but fails HOI incidence; e3 fails the pre-index enrollment gate.
    episodes = [
        _pin_episode(
            "cohort_a",
            1,
            date(2014, 3, 1),
            min_enr_days_met=True,
            min_enr_aft_met=True,
            min_day_sup_met=True,
            min_epi_dur_met=True,
            post_blackout_met=True,
        ),
        _pin_episode("cohort_a", 2, date(2014, 4, 1), min_enr_days_met=True),
        _pin_episode("cohort_a", 3, date(2014, 5, 1)),
    ]
    hoi = [
        {
            "cohortgrp": "cohort_a",
            "patid": 1,
            "indexdt": date(2014, 3, 1),
            "hoi_incidence_met": True,
            "hoi_blackout_met": True,
        }
    ]
    cohort_final = [
        {"cohortgrp": "cohort_a", "patid": 1},
        {"cohortgrp": "cohort_a", "patid": 2},
    ]
    expected = d_stages.expected_attrition(
        dem,
        enr,
        spans,
        stockpiled,
        _pin_cohorts(("cohort_a",)),
        _pin_age_strata(("cohort_a",)),
        episodes,
        [],
        hoi,
        cohort_final,
        [],
        _PIN_START,
        _PIN_END,
    )
    got = {
        r["level"]: (r["remaining"], r["excluded"])
        for r in expected
        if r["group"] == "cohort_a"
    }

    # counts: 12=3 episodes, 16=2 pass enrollment, 18=2 pass criteria,
    # 19=1 passes HOI incidence, 20-24=1 (e1 only), 27=2 final members.
    assert got[12.0] == (3, 0)
    assert got[16.0] == (2, 1)
    assert got[18.0] == (2, 0)
    assert got[19.0] == (1, 1)
    assert got[24.0] == (1, 0)
    # blanks 8/9/11 carry 3 with excluded 0...
    assert got[8.0] == got[9.0] == got[11.0] == (3, 0)
    # ...blank 17 carries 16's count (2), and 18's exclusion lags 16's
    # count ACROSS the blank (lag partition init|excl): 2 - 2 = 0.
    assert got[17.0] == (2, 0)
    assert got[18.0][1] == 0
    # blanks 25/26 carry 1; the final information row carries the real
    # member count with excluded NULL.
    assert got[25.0] == got[26.0] == (1, 0)
    assert got[27.0] == (2, None)


def test_attrition_final_empty_members_report_zero() -> None:
    """An empty cohort_final reports 0 members at the final information
    row (not the last episode count carried through)."""
    dem = _pin_demographics(2)
    enr = _pin_enrollment((1, 2))
    spans = [
        {"enr_cfg_id": "E1", "patid": p, "enr_start": _PIN_START, "enr_end": _PIN_END}
        for p in (1, 2)
    ]
    stockpiled = [
        {"cohortgrp": "cohort_a", "patid": p, "adate": date(2014, 3, 1)} for p in (1, 2)
    ]
    episodes = [
        _pin_episode(
            "cohort_a",
            p,
            date(2014, 3, 1),
            **dict.fromkeys(
                (
                    "min_enr_days_met",
                    "min_enr_aft_met",
                    "min_day_sup_met",
                    "min_epi_dur_met",
                    "post_blackout_met",
                ),
                True,
            ),
        )
        for p in (1, 2)
    ]
    hoi = [
        {
            "cohortgrp": "cohort_a",
            "patid": p,
            "indexdt": date(2014, 3, 1),
            "hoi_incidence_met": True,
            "hoi_blackout_met": True,
        }
        for p in (1, 2)
    ]
    expected = d_stages.expected_attrition(
        dem,
        enr,
        spans,
        stockpiled,
        _pin_cohorts(("cohort_a",)),
        _pin_age_strata(("cohort_a",)),
        episodes,
        [],
        hoi,
        [],
        [],
        _PIN_START,
        _PIN_END,
    )
    got = {
        r["level"]: (r["remaining"], r["excluded"])
        for r in expected
        if r["group"] == "cohort_a"
    }

    # the funnel is alive through the last counted episode step...
    assert got[24.0] == (2, 0)
    # ...blanks 25/26 still carry 2...
    assert got[25.0] == got[26.0] == (2, 0)
    # ...and the empty final relation pins level 27 at 0 members, no
    # exclusion (information row) — carrying 2 here is exactly the F1
    # defect this contract exists to prevent.
    assert got[27.0] == (0, None)


# ---- gapped-enrollment RX covariate: piece-per-span clip witness -----
#
# Contract (80_covariates.sql `clipped` CTE lines 117-141 and
# `reclipped` lines 165-174, citing ms_cidacov_codeextraction.sas
# :590-672): a covariate dispensing is clipped to EVERY enrollment span
# it overlaps (one piece per span, supply cut at the span edges),
# same-day supplies sum, pieces chain back-to-back, and the chained
# coverage is clipped to enrollment AGAIN — a continuation landing in a
# gap or past the last span end is dropped. Generated fixtures enroll
# continuously, so this hand witness is the only place the straddle is
# exercised end to end.

_W_START = date(2014, 1, 1)
_W_END = date(2014, 12, 31)


def _enumerate_chain_days(
    spans: list[tuple[date, date]], fills: list[tuple[date, int]]
) -> set[date]:
    """Covered days of the documented chain, enumerated day by day:
    clip each fill to every overlapping span, chain the pieces forward
    one day at a time, then keep only days inside a span (second
    shave)."""
    pieces: list[tuple[date, int]] = []
    for adate, sup in fills:
        raw_end = adate + timedelta(days=sup - 1)
        for sp_start, sp_end in spans:
            if adate <= sp_end and raw_end >= sp_start:
                lo = max(adate, sp_start)
                hi = min(raw_end, sp_end)
                pieces.append((lo, (hi - lo).days + 1))
    covered: set[date] = set()
    next_free: date | None = None
    for lo, sup in sorted(pieces):
        start = lo if next_free is None or lo > next_free else next_free
        for k in range(sup):
            covered.add(start + timedelta(days=k))
        next_free = start + timedelta(days=sup)
    return {
        d for d in covered if any(sp_start <= d <= sp_end for sp_start, sp_end in spans)
    }


def test_rx_covariate_gapped_enrollment_piece_per_span() -> None:
    """A 60-day fill straddling a 31-day enrollment gap covers its
    clipped first-span piece (06-20..06-30), stops at the gap, resumes
    on the second span (08-01..08-18), and the second fill chains
    inside that span (09-01..09-10). The hole window inside the gap
    must NOT hit: under the naive single-span chain (full supply on the
    containing span, no second shave) the coverage would run through
    08-18 and the hole window would find days — this witness pins the
    piece-per-span semantics exactly."""
    # 31-day gap (enrol_gap 0): the enrollment stage keeps two spans.
    p1 = Patient(1, date(1980, 5, 1), "F", "1", "N", "02108", _W_START)
    spans = (
        EnrollmentSpan(1, date(2014, 1, 1), date(2014, 6, 30), "Y", "Y", "Y"),
        EnrollmentSpan(1, date(2014, 8, 1), _W_END, "Y", "Y", "Y"),
    )
    dispensings = (
        # the index fill: cohort_a episode, index 2014-03-01
        Dispensing(1, date(2014, 3, 1), "E00001", "ND", 30, 10.0, 1001),
        # straddles the gap: 11 days on span 1, 18 on span 2
        Dispensing(1, date(2014, 6, 20), "N00001", "ND", 60, 5.0, 1001),
        # inside span 2, chains the coverage to 09-10
        Dispensing(1, date(2014, 9, 1), "N00001", "ND", 10, 5.0, 1001),
    )
    dataset = SyntheticDataset(
        patients=(p1,),
        deaths=(),
        enrollment=spans,
        dispensings=dispensings,
        diagnoses=(),
        procedures=(),
        encounters=(),
        labs=(),
        exposure_codes=("E00001",),
        event_codes=("D00001",),
        noise_drug_codes=("N00001",),
        noise_dx_codes=("X00001",),
        start=_W_START,
        end=_W_END,
    )
    index_dt = date(2014, 3, 1)
    covariates = (
        Covariate(1, "gap_all", "RX", covfrom=-300, covto=200, codes=("N00001",)),
        Covariate(2, "gap_hole", "RX", covfrom=126, covto=141, codes=("N00001",)),
        Covariate(3, "gap_tail", "RX", covfrom=192, covto=193, codes=("N00001",)),
        Covariate(4, "gap_over", "RX", covfrom=194, covto=203, codes=("N00001",)),
    )
    study = study_with_features(
        ("E00001",), covariates=covariates, start=_W_START, end=_W_END
    )

    # hand-derived chain output: the three clipped, chained,
    # re-clipped coverage intervals and their 39 covered days.
    hand_intervals = (
        (date(2014, 6, 20), date(2014, 6, 30)),
        (date(2014, 8, 1), date(2014, 8, 18)),
        (date(2014, 9, 1), date(2014, 9, 10)),
    )
    hand_days = {
        lo + timedelta(days=k)
        for lo, hi in hand_intervals
        for k in range((hi - lo).days + 1)
    }
    assert len(hand_days) == 39
    enum_spans = [(s.enr_start, s.enr_end) for s in spans]
    enum_fills = [(d.rxdate, d.rxsup) for d in dispensings[1:]]
    assert _enumerate_chain_days(enum_spans, enum_fills) == hand_days

    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("covariates")
        master = d_stages.table_dicts(harness.engine.con, "ptsmasterlist")
        assert [(m["cohortgrp"], m["patid"], m["indexdt"]) for m in master] == [
            ("cohort_a", 1, index_dt)
        ]
        rows = d_stages.table_dicts(harness.engine.con, "covariates_long")
        got = {
            (r["cohortgrp"], r["patid"], r["indexdt"], r["covarnum"], r["covarname"])
            for r in rows
        }
        # exact expectations: the hole window (inside the gap) has NO
        # row — a containing-span-only chain without the second shave
        # would cover gap days and hit; the over window (beyond the
        # chain end) has NO row — keeping FULL supply on every span
        # piece (the wp307 double-count) would push chained coverage
        # past 09-10 and hit; the tail covariate pins the exact final
        # piece end.
        assert got == {
            ("cohort_a", 1, index_dt, 1, "gap_all"),
            ("cohort_a", 1, index_dt, 3, "gap_tail"),
        }
        # each window's outcome agrees with the enumerated covered days
        # (non-dateonly: a piece hits iff its covered days meet the
        # window).
        for cov in covariates:
            ws = index_dt + timedelta(days=cov.covfrom)
            we = index_dt + timedelta(days=cov.covto)
            expected_hit = any(ws <= d <= we for d in hand_days)
            hit = any(r[3] == cov.covarnum for r in got)
            assert hit == expected_hit, cov.covarname
