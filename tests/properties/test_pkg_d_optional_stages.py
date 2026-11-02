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

from datetime import date

import pytest
from hypothesis import event, given, strategies as st

from qrp.config import Covariate, RiskScoreCode

from .conftest import expensive_settings
from .harness import StageHarness
from .oracles import d_stages
from .oracles.d_denom import expected_denomcounts
from .oracles.d_fixtures import (
    ALL_CODES, ALL_EVENTS, CIDA_LEVELS, FUTU_LEVELS, study_with_features,
)
from .strategies import SEEDS, generate_options
from .synth import GenerateOptions, generate

_START = date(2014, 1, 1)
_END = date(2014, 12, 31)
CODES = ALL_CODES[:8]
EVENTS = ALL_EVENTS[:4]


def _cases() -> dict[str, dict]:
    cov_pre_dx = (Covariate(1, "pre_dx", "DX", covfrom=-30, covto=-1,
                            codes=(EVENTS[0],)),)
    cov_two = cov_pre_dx + (Covariate(2, "post_rx", "RX", covfrom=1,
                                      covto=30, dateonly=True,
                                      codes=(CODES[0],)),)
    util_med_drug = (("", "MED", -30, 30), ("", "DRUG", -60, 0))
    classes_a = ((CODES[0], "classA", "genA"),)
    cci_scores = (RiskScoreCode("CCI", "IN", "IN", weight=1.0),
                  RiskScoreCode("CCI", "c1", "DX", code=EVENTS[0],
                                weight=2.0, riskfrom=-30, riskto=30))
    return {
        "code_distribution": dict(stage="code distribution"),
        "mfu": dict(stage="most frequent use", mfu=(
            ("cohort_a", 1, "DX", "CODECOUNT", 5, -90, 30, "09"),
            ("cohort_a", 2, "RX", "PATCOUNT", 3, -60, 0, None),
            (None, 3, "DX", "CODECOUNT", 2, -365, 0, None))),
        "labs": dict(stage="labs", labs=True, lab_codes=(
            ("cohort_a", "2160-0", "LRO", "02", "N", None, None, None,
             None, None, None, None, ">", 50.0, None),
            ("cohort_a", "PX1", "LRO", "99", None, None, None, None, None,
             None, None, None, None, None, None),
            ("cohort_b", "2160-0", "LRO", "02", "N", None, None, None,
             None, None, None, None, ":", 60.0, 140.0))),
        "utilization": dict(stage="utilization", utilization=util_med_drug,
                            drug_classes=((CODES[0], "classA", "genA"),
                                          (CODES[1], "classB", "genA"),
                                          ("N00001", "classA", "genB"))),
        "geography": dict(stage="geography"),
        "risk_scores": dict(stage="risk scores", risk_scores=(
            RiskScoreCode("CCI", "IN", "IN", weight=1.0),
            RiskScoreCode("CCI", "c1", "DX", code=EVENTS[0], weight=2.0,
                          riskfrom=-30, riskto=30),
            RiskScoreCode("CCI", "c2", "RX", code=CODES[0], weight=3.0,
                          riskfrom=-60, riskto=-1),
            RiskScoreCode("FRAIL", "d1", "DM", code="F", weight=2.5),
            RiskScoreCode("FRAIL", "IN", "IN", weight=0.5))),
        "covariates": dict(stage="covariates", covariates=cov_two),
        "baseline": dict(stage="baseline", covariates=cov_pre_dx,
                         utilization=util_med_drug,
                         drug_classes=classes_a,
                         risk_scores=cci_scores),
        "cida_tables": dict(stage="cida tables"),
        "followuptime": dict(stage="follow-up time table"),
        "mstr_wide": dict(stage="master list columns",
                          covariates=cov_pre_dx),
        "attrition_person_time": dict(stage="attrition + denominators"),
    }

_CASE_NAMES = tuple(_cases())


@given(patients=st.integers(2, 5), seed=SEEDS,
       options=generate_options(defaults=GenerateOptions(include_labs=True)))
@expensive_settings()
@pytest.mark.parametrize("case_name", _CASE_NAMES)
def test_optional_stage_matches_reference(case_name: str, patients: int,
                                          seed: int, options) -> None:
    """Every optional stage's real output equals the independent
    aggregation oracle over its documented inputs."""
    from dataclasses import replace
    spec = _cases()[case_name]
    if case_name == "labs":
        options = replace(options, include_labs=True)
    dataset = generate(patients=patients, seed=seed, start=_START,
                       end=_END, options=options)
    study = _study_for(spec, dataset)

    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        if spec["stage"] == "master list columns":
            _check_mstr_wide(harness, dataset, study, spec)
        else:
            harness.run_through(spec["stage"])
            _check_tables(harness, dataset, study, spec, case_name)
    event(f"{case_name}-ran")


def _study_for(spec: dict, dataset) -> object:
    zip_rows: tuple[tuple[str, str | None, str | None, str | None,
                     float | None], ...] = ()
    if spec["stage"] == "geography":
        seen = list({p.postalcode for p in dataset.patients})
        zip_rows = tuple(
            (z, "MA" if i == 0 else "CA", "1" if i % 2 else "2",
             "Northeast" if i % 3 else "other", 10.0 * i + 5.0)
            for i, z in enumerate(seen))
    return study_with_features(
        dataset.exposure_codes[:8], EVENTS,
        covariates=spec.get("covariates", ()),
        risk_scores=spec.get("risk_scores", ()),
        utilization=spec.get("utilization", ()),
        drug_classes=spec.get("drug_classes", ()),
        lab_codes=spec.get("lab_codes", ()),
        mfu=spec.get("mfu", ()),
        zipfile=zip_rows,
        strata=spec.get("strata", CIDA_LEVELS + FUTU_LEVELS),
        start=_START, end=_END)


def _rows(harness, table: str) -> list[dict]:
    return d_stages.table_dicts(harness.engine.con, table)


def _check_tables(harness, dataset, study, spec, case_name: str) -> None:
    if case_name == "code_distribution":
        got = d_stages.expected_distindex(
            _rows(harness, "cohort_final"), _rows(harness, "stockpiled"),
            _envdx(harness), _envpx(harness),
            _rows(harness, "cdm_dispensing"), _rows(harness, "cfg_codes"),
            _rows(harness, "cfg_care_setting"))
        for table in ("distindexmap", "distindex", "distindex_episodes"):
            assert d_stages.canonical(_rows(harness, table)) == \
                d_stages.canonical(got[table]), table
    elif case_name == "mfu":
        assert d_stages.canonical(_rows(harness, "mfu")) == \
            d_stages.canonical(d_stages.expected_mfu(
                _rows(harness, "ptsmasterlist"), _envdx(harness),
                _rows(harness, "cdm_dispensing"),
                _rows(harness, "cfg_mfu")))
    elif case_name == "labs":
        assert d_stages.canonical(_rows(harness, "lab_results")) == \
            d_stages.canonical(d_stages.expected_labs(
                _rows(harness, "ptsmasterlist"), _rows(harness, "cdm_lab"),
                _rows(harness, "cfg_labcodes")))
        assert d_stages.canonical(_rows(harness, "lab_summary")) == \
            d_stages.canonical(d_stages.expected_lab_summary(
                d_stages.expected_labs(
                    _rows(harness, "ptsmasterlist"),
                    _rows(harness, "cdm_lab"),
                    _rows(harness, "cfg_labcodes"))))
    elif case_name == "utilization":
        expected = d_stages.expected_utilization(
            _rows(harness, "ptsmasterlist"), _rows(harness, "cdm_encounter")
            if _has(harness, "cdm_encounter") else _rows(harness, "encounter"),
            _rows(harness, "cdm_dispensing"), _rows(harness, "cfg_utilization"),
            {c["cohortgrp"] for c in _rows(harness, "cfg_cohort")
             if c["create_baseline"]},
            _rows(harness, "cfg_drugclass"))
        assert d_stages.canonical(_rows(harness, "utilization")) == \
            d_stages.canonical(expected)
        assert d_stages.canonical(_rows(harness, "utilization_summary")) == \
            d_stages.canonical(
                d_stages.expected_utilization_summary(expected))
    elif case_name == "geography":
        assert d_stages.canonical(_rows(harness, "geography")) == \
            d_stages.canonical(d_stages.expected_geography(
                _rows(harness, "ptsmasterlist"), _rows(harness, "cfg_zipfile")))
    elif case_name == "risk_scores":
        expected = d_stages.expected_risk_scores(
            _rows(harness, "ptsmasterlist"), _rows(harness, "cohort_claims"),
            _rows(harness, "cfg_risk_codes"))
        assert d_stages.canonical(_rows(harness, "risk_scores")) == \
            d_stages.canonical(expected)
        assert d_stages.canonical(_rows(harness, "risk_score_summary")) == \
            d_stages.canonical(d_stages.expected_risk_summary(expected))
    elif case_name == "covariates":
        cov_cfg = _rows(harness, "cfg_covariates")
        codes: dict[int, set] = {}
        for r in _rows(harness, "cfg_covariate_codes"):
            codes.setdefault(int(r["covarnum"]), set()).add(
                (str(r["code"]), str(r["codecat"])))
        assert d_stages.canonical(_rows(harness, "covariates_long")) == \
            d_stages.canonical(d_stages.expected_covariates(
                _rows(harness, "ptsmasterlist"), _rows(harness, "cohort_claims"),
                cov_cfg, codes, _spans(harness, study), study.start_date))
    elif case_name == "baseline":
        baseline_grps = {c["cohortgrp"] for c in _rows(harness, "cfg_cohort")
                         if c["create_baseline"]}
        seen_labels: dict[str, None] = {}
        for c in study.cohorts:
            if c.cohortgrp in baseline_grps:
                for band in c.age_strata.strata:
                    seen_labels[band.label] = None
        age_labels = tuple(seen_labels)
        assert d_stages.canonical(_rows(harness, "baseline_long")) == \
            d_stages.canonical(d_stages.expected_baseline_long(
                _rows(harness, "cohort_final"), _rows(harness, "utilization"),
                _rows(harness, "risk_scores"),
                baseline_grps,
                tuple(sorted({int(r["covarnum"])
                              for r in _rows(harness, "cfg_covariate_codes")})),
                _rows(harness, "covariates_long"), age_labels,
                study.start_date, study.end_date))
    elif case_name == "cida_tables":
        num = d_stages.expected_numcounts(_rows(harness, "cohort_final"),
                                          _rows(harness, "cfg_strata"))
        assert d_stages.canonical(_rows(harness, "numcounts")) == \
            d_stages.canonical(num)
        denom = expected_denomcounts(dataset, study)
        assert d_stages.canonical(_rows(harness, "t2_cida")) == \
            d_stages.canonical(
                d_stages.expected_t2_cida(num, denom))
    elif case_name == "followuptime":
        assert d_stages.canonical(_rows(harness, "followuptime")) == \
            d_stages.canonical(d_stages.expected_followuptime(
                _rows(harness, "cohort_final"),
                _rows(harness, "episode_flags"),
                _rows(harness, "cfg_strata"),
                {c["cohortgrp"] for c in _rows(harness, "cfg_cohort")}))
    elif case_name == "attrition_person_time":
        assert d_stages.canonical(_rows(harness, "denominators")) == \
            d_stages.canonical(
                d_stages.expected_denominators(_rows(harness, "cohort_final")))
        assert d_stages.canonical(_rows(harness, "censoring")) == \
            d_stages.canonical(d_stages.expected_censoring(
                _rows(harness, "cohort_final"),
                _rows(harness, "episode_flags"), _rows(harness, "cfg_strata"),
                study.end_date, study.effective_censor_date))
        assert d_stages.canonical(_rows(harness, "attrition")) == \
            d_stages.canonical(d_stages.expected_attrition(
                _rows(harness, "demographics"),
                _rows(harness, "cdm_enrollment"),
                _rows(harness, "_att_spans"), _rows(harness, "stockpiled"),
                _rows(harness, "cfg_cohort"),
                _rows(harness, "cfg_age_strata"),
                _rows(harness, "episode_criteria"),
                _rows(harness, "inclusion_excluded")
                if _has(harness, "inclusion_excluded") else [],
                _rows(harness, "episode_hoi"),
                _rows(harness, "cohort_final"),
                _rows(harness, "cfg_inclusion"),
                study.start_date, study.end_date))


def _check_mstr_wide(harness, dataset, study, spec) -> None:
    """Run to the stage before 62, capture its inputs, then run 62."""
    harness.run_through("baseline" if study.any_covariates
                        else "follow-up + events")
    pre_final = _rows(harness, "cohort_final")
    flags = _rows(harness, "episode_flags")
    harness.run_stages(["master list columns"])
    di_rows = _rows(harness, "distindex_episodes") \
        if _has(harness, "distindex_episodes") else []
    expected = d_stages.expected_mstr_added(pre_final, flags, di_rows,
                                            study.end_date)
    post = _rows(harness, "cohort_final")
    keys = ("cohortgrp", "patid", "indexdt")
    added = ("year", "month", "quarter", "periodid", "indexlookenddt",
             "rawdisp", "adjusteddisp", "totrxsup", "totrxamt",
             "distindexexp", "distindexhoi", "numevents",
             "fupdays_value_cat", "censorcat_sort",
             "fup_elig", "fup_dpend", "fup_episend", "fup_spec",
             "fup_event", "cens_elig", "cens_dth", "cens_qryend",
             "cens_dpend")
    got = [{**{k: r[k] for k in keys},
            **{k: r[k] for k in added}} for r in post]
    assert d_stages.canonical(got) == d_stages.canonical(expected)


# ---- small helpers --------------------------------------------------

def _has(harness, table: str) -> bool:
    names = {r[0].lower() for r in harness.engine.con.execute(
        "SELECT table_name FROM information_schema.tables").fetchall()}
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
            (s["patid"], s["enr_start"], s["enr_end"]))
    return out
