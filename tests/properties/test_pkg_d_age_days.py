# pattern: Imperative Shell
"""F2-dependent comparisons; old segment-start discovery tests are unchanged."""
from datetime import date, timedelta

import pytest
from hypothesis import event, given, strategies as st

from qrp.config import AgeStrata, InclusionRule, StratumLevel

from .conftest import expensive_settings
from .harness import StageHarness, fetch_rows
from .oracles.d_age_days import BandCount, MemberDay, attained_years, count_age_days
from .oracles.d_denom import expected_denomcounts
from .oracles.d_fixtures import make_study2
from .synth import Death, Diagnosis, EnrollmentSpan, Patient, SyntheticDataset

BANDS = AgeStrata.parse("00-63 64+")
DAY = timedelta(days=1)


def compare_window(birth: date, start: date, end: date, death: date | None,
                   excluded: date | None, patients: int = 1,
                   bands: AgeStrata = BANDS) -> tuple[BandCount, ...]:
    dataset = SyntheticDataset(
        patients=tuple(Patient(p, birth, "F", "1", "N", "02108", start)
                       for p in range(1, patients + 1)),
        deaths=tuple(Death(p, death) for p in range(1, patients + 1)) if death else (),
        enrollment=tuple(EnrollmentSpan(p, start, end, "Y", "Y", "Y")
                         for p in range(1, patients + 1)),
        dispensings=(),
        # pdx 'S' on AV claims: deliberate out-of-domain values (SCDM
        # Pdx is blank on ambulatory claims); behavior-neutral under the
        # wildcard care settings these noise rows flow through.
        diagnoses=tuple(Diagnosis(p, excluded, "X00001", "09", "S", "AV")
                        for p in range(1, patients + 1) for _ in range(2)) if excluded else (),
        procedures=(), encounters=(), labs=(), exposure_codes=("E00001",),
        event_codes=(), noise_drug_codes=(), noise_dx_codes=("X00001",),
        start=start, end=end,
    )
    study = make_study2(
        ("E00001",), strata=(StratumLevel("t2cida", "A", ("agegroup",)),),
        start=start, end=end,
        over_a={"wash_per": 0, "enr_days": 0, "age_strata": bands},
        exclusions=(InclusionRule(cohortgrp="cohort_a", cond=1, criteria="EXC",
                                  codecat="DX", condfrom=0, condto=0,
                                  codes=("X00001",)),),
    )
    eligible = tuple(MemberDay(p, birth, start + DAY * offset)
                     for p in range(1, patients + 1)
                     for offset in range((end - start).days + 1)
                     if (death is None or start + DAY * offset <= death)
                     and start + DAY * offset != excluded)
    expected = count_age_days(eligible, bands)
    with StageHarness(study, threads=1) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("cida denominators")
        actual = fetch_rows(harness.engine, "denomcounts")
    expected_rows = [("A", "cohort_a", b.label, b.ordinal,
                      None, None, None, None, None, None, None, None, None, None, None,
                      b.members, b.days) for b in expected]
    assert sorted(actual) == sorted(expected_rows)
    facts: dict[str, int] = {}
    expected_denomcounts(dataset, study, facts)
    assert facts.get("exclusion-hit-members", 0) == (
        patients if excluded is not None and start <= excluded <= min(end, death or end) else 0)
    return expected


@pytest.mark.parametrize("birth,birthday", [
    (date(1950, 6, 15), date(2014, 6, 15)),
    (date(1952, 2, 29), date(2016, 2, 29)),
])
@pytest.mark.parametrize("boundary", ["none", "death", "exclusion", "both"])
def test_pinned_age_boundary(birth: date, birthday: date, boundary: str) -> None:
    death = birthday if boundary in ("death", "both") else None
    exclusion = birthday if boundary in ("exclusion", "both") else None
    result = compare_window(birth, birthday - DAY, birthday + DAY, death, exclusion, 2)
    older_days = {"none": 4, "death": 2, "exclusion": 2, "both": 0}[boundary]
    assert result == (BandCount("00-63", 1, 2, 2),) + (
        (BandCount("64+", 2, 2, older_days),) if older_days else ())


@given(before=st.integers(1, 100), after=st.integers(0, 100),
       death_offset=st.one_of(st.none(), st.integers(-2, 2)),
       exclusion_offset=st.one_of(st.none(), st.integers(-2, 2)), patients=st.integers(1, 3))
@expensive_settings()
def test_generated_age_day_comparison(before: int, after: int, death_offset: int | None,
                                      exclusion_offset: int | None, patients: int) -> None:
    birthday = date(2014, 6, 15)
    result = compare_window(date(1950, 6, 15), birthday - DAY * before, birthday + DAY * after,
                   birthday + DAY * death_offset if death_offset is not None else None,
                   birthday + DAY * exclusion_offset if exclusion_offset is not None else None,
                   patients)
    event("nonempty", bool(result))
    event("crossed-band", len(result) == 2)


def test_nonleap_february_anniversary() -> None:
    assert compare_window(date(1952, 2, 29), date(2017, 2, 27), date(2017, 3, 1),
                          None, None, bands=AgeStrata.parse("00-64 65+")) == (
                              BandCount("00-64", 1, 1, 1), BandCount("65+", 2, 1, 2))


@pytest.mark.parametrize("day,age", [(date(2015, 2, 27), 62), (date(2015, 2, 28), 63),
                                    (date(2016, 2, 28), 63), (date(2016, 2, 29), 64)])
def test_leap_anniversary_pin(day: date, age: int) -> None:
    assert attained_years(date(1952, 2, 29), day) == age


@given(before=st.integers(1, 300), after=st.integers(0, 300), copies=st.integers(1, 5))
def test_day_model_independent_counts(before: int, after: int, copies: int) -> None:
    birthday = date(2014, 6, 15)
    days = tuple(MemberDay(1, date(1950, 6, 15), birthday + DAY * offset)
                 for offset in range(-before, after + 1)) * copies
    assert count_age_days(days, BANDS) == (
        BandCount("00-63", 1, 1, before * copies),
        BandCount("64+", 2, 1, (after + 1) * copies))
    assert count_age_days(tuple(reversed(days)), BANDS) == count_age_days(days, BANDS)


def test_literal_gap_and_finite_top_are_model_only() -> None:
    """Not a SAS parity claim: parser acceptance does not settle denominator semantics."""
    days = tuple(MemberDay(1, date(1950, 6, 15), date(year, 6, 15))
                 for year in (2012, 2013, 2014, 2015))
    assert count_age_days(days, AgeStrata.parse("00-62 64-64")) == (
        BandCount("00-62", 1, 1, 1), BandCount("64-64", 2, 1, 1))
