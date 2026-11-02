"""Study/dataset fixture builders for package D.

The synth builder's studies carry NO care-setting rows, and the
production event_claims stage inner-joins cfg_care_setting — so a
plainly-built study silently has NO outcome events at all. The study-file
loader (config.py) expands a missing caresettingprincipal to the
wildcard ('**', '*') per EVENT code (parse_care_setting, CARE_SETTING_ALL);
every study built here applies the same expansion so fixtures model the
loader's semantics, not the builder gap.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

from qrp.config import (
    CohortConfig,
    Covariate,
    InclusionRule,
    RiskScoreCode,
    StratumLevel,
    StudyConfig,
)

from ..synth import build_study

CIDA_LEVELS: tuple[StratumLevel, ...] = (
    StratumLevel(table_id="t2cida", level_id="1", levelvars=()),
    StratumLevel(table_id="t2cida", level_id="2",
                 levelvars=("agegroup", "sex")),
    StratumLevel(table_id="t2cida", level_id="3",
                 levelvars=("agegroup", "sex", "year")),
)
# a follow-up-time level per exact days and one by category
FUTU_LEVELS: tuple[StratumLevel, ...] = (
    StratumLevel(table_id="t2followuptime", level_id="F1",
                 levelvars=("censdays_value",)),
    StratumLevel(table_id="t2followuptime", level_id="F2",
                 levelvars=("censdays_value_cat",)),
)

ALL_CODES: tuple[str, ...] = tuple(
    f"E{i:05d}" for i in range(1, 41))
ALL_EVENTS: tuple[str, ...] = tuple(f"D{i:05d}" for i in range(1, 21))


def wildcard_events(c: CohortConfig) -> CohortConfig:
    """The loader's care-setting expansion: one ('**','*') row per EVENT
    code (config.py parse_care_setting -> CARE_SETTING_ALL)."""
    return replace(c, event_care_settings=tuple(
        (code, "**", "*") for code, _cc, _ct, _sup in c.event_codes))


def make_cohort(grp: str, codes: tuple[str, ...],
                events: tuple[str, ...] = (), **over: Any) -> CohortConfig:
    """A cohort with the demo_type2 code shape plus loader care settings.
    Field overrides (wash_per, enrol_gap, output_denom, ...) go through
    dataclasses.replace so exactly one field can differ between cohorts."""
    c = CohortConfig(
        cohortgrp=grp,
        exposure_codes=tuple((code, "RX", "ND", None) for code in codes),
        event_codes=tuple((code, "DX", "09", None) for code in events),
    )
    c = wildcard_events(c)
    if over:
        c = replace(c, **over)
    return c


def make_study2(
    codes_a: tuple[str, ...],
    codes_b: tuple[str, ...] = (),
    event_codes: tuple[str, ...] = (),
    *,
    strata: tuple[StratumLevel, ...] = CIDA_LEVELS,
    start: date = date(2014, 1, 1),
    end: date = date(2014, 12, 31),
    over_a: dict[str, Any] | None = None,
    over_b: dict[str, Any] | None = None,
    exclusions: tuple[InclusionRule, ...] = (),
    **shared: Any,
) -> StudyConfig:
    """A study whose two cohorts may differ in EXACTLY one field via
    over_a / over_b (passed to make_cohort); other kwargs are shared."""
    cohorts = [
        make_cohort("cohort_a", codes_a, event_codes,
                    **(over_a or {}), **shared)]
    if codes_b:
        cohorts.append(
            make_cohort("cohort_b", codes_b, event_codes,
                        **(over_b or {}), **shared))
    study = StudyConfig(
        study_type=2, start_date=start, end_date=end,
        cohorts=tuple(cohorts),
        inclusions=tuple(exclusions),
        strata=tuple(strata),
    )
    study.validate()
    return study


def study_with_features(
    dataset_codes: tuple[str, ...],
    event_codes: tuple[str, ...] = ALL_EVENTS[:4],
    *,
    covariates: tuple[Covariate, ...] = (),
    risk_scores: tuple[RiskScoreCode, ...] = (),
    utilization: tuple[tuple[str, str, int, int], ...] = (),
    drug_classes: tuple[tuple[str, str, str], ...] = (),
    lab_codes: tuple[tuple[Any, ...], ...] = (),
    mfu: tuple[tuple[Any, ...], ...] = (),
    zipfile: tuple[tuple[str, str | None, str | None, str | None,
                         float | None], ...] = (),
    strata: tuple[StratumLevel, ...] = CIDA_LEVELS + FUTU_LEVELS,
    exclusions: tuple[InclusionRule, ...] = (),
    start: date = date(2014, 1, 1),
    end: date = date(2014, 12, 31),
) -> StudyConfig:
    """build_study plus the feature blocks, with loader care settings.

    wash_per/enr_days are 30, not the 183 defaults: the generator's
    dispensing supplies are 7-90 days, so an episode can only chain
    >=183 enrolled days in rare draws and ptsmasterlist comes out empty
    for most examples — every stage comparison would then pass against
    an equally empty oracle (the vacuity this suite exists to avoid).
    30 keeps the min-enrollment gate discriminating (supplies 7/14 still
    fail it) while yielding members on ordinary draws. wash_per must
    stay <= enr_days (config.py validation).
    """
    s = build_study(
        dataset_codes, event_codes, start=start, end=end,
        covariates=covariates, inclusions=exclusions,
        risk_scores=risk_scores, strata=strata, zipfile_rows=zipfile,
        utilization=utilization, drug_classes=drug_classes,
        lab_codes=lab_codes, mfu=mfu, wash_per=30)
    short_days = tuple(replace(c, enr_days=30) for c in s.cohorts)
    return replace(s, cohorts=tuple(wildcard_events(c) for c in short_days))


def split_codes(codes: tuple[str, ...]) -> tuple[tuple[str, ...],
                                                 tuple[str, ...]]:
    from math import ceil
    if len(codes) < 2:
        return codes, ()
    mid = ceil(len(codes) / 2)
    return codes[:mid], codes[mid:]
