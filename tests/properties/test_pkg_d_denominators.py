"""Package D — 92_cidadenom.sql (denomcounts) against a day-enumeration
oracle.

The oracle enumerates eligible CALENDAR DAYS per member and never shares
an expression with the shipped SQL: enrollment merging, window bounds,
washout/exclusion/event shaving and the stratum algebra are all
re-derived in Python from the documented contract (docs/SAS_PARITY.md,
docs/PARITY_FINDINGS.md "Third fix", docs/OUTPUTS.md OUTPUTDENOM).
Member counts and member-days are checked on the SAME rows but are
different columns with different failure modes; a defect that moved
member-days without moving members (or vice versa) fails this property.

Contamination note (B2): generate() never produces overlapping enrollment
spans, so the stockpile double-count cannot contaminate this family.
Every fixture here uses generate() or non-overlapping hand spans.
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import event, given, strategies as st
import pytest

from qrp.config import InclusionRule

from .conftest import expensive_settings
from .harness import StageHarness, fetch_rows
from .oracles.d_denom import expected_denomcounts
from .oracles.d_fixtures import ALL_CODES, CIDA_LEVELS, make_study2
from .strategies import SEEDS, generate_options
from .synth import (
    Death, Diagnosis, Dispensing, EnrollmentSpan, Patient, SyntheticDataset,
    generate,
)

_START = date(2014, 1, 1)
_END = date(2014, 12, 31)

_WASHOUTS = st.sampled_from([(0, 0), (183, 183), (30, 0), (60, 90)])
_OUTDENS = st.sampled_from([("Y", "Y"), ("Y", "M"), ("N", "Y")])


@given(
    patients=st.integers(1, 5),
    seed=SEEDS,
    options=generate_options(),
    wash=_WASHOUTS,
    outden=_OUTDENS,
)
@expensive_settings()
def test_denominator_matches_day_enumeration(
    patients: int, seed: int, options, wash: tuple[int, int],
    outden: tuple[str, str],
) -> None:
    """denomcounts equals the independent day enumeration, members and
    member-days separately, for every t2cida level and stratum."""
    study = make_study2(
        ALL_CODES[:20], ALL_CODES[20:], ("D00001",),
        strata=CIDA_LEVELS, start=_START, end=_END,
        over_a={"wash_per": wash[0], "output_denom": outden[0]},
        over_b={"wash_per": wash[1], "output_denom": outden[1]},
    )
    dataset = generate(patients=patients, seed=seed, start=_START,
                       end=_END, options=options)
    facts: dict[str, int] = {}
    expected = expected_denomcounts(dataset, study, facts)
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("cida denominators")
        actual = fetch_rows(harness.engine, "denomcounts")
    assert sorted(actual) == sorted(expected)

    # member counts vs member-days move together only when correct;
    # record which axis actually carried data in this example
    if any(r[15] for r in actual):
        event("member-counts-nonzero")
    if any(r[16] for r in actual):
        event("member-days-nonzero")
    event("windows", facts.get("windows", 0) > 0)
    event("washout-shave-hit", facts.get("washout-hit-members", 0) > 0)
    event("exclusion-hit", facts.get("exclusion-hit-members", 0) > 0)
    event("death-clipping", facts.get("death-clipped-spans", 0) > 0)
    event("shaving-split-a-window", facts.get("shaved-windows", 0) > 0)
    event("output-nonempty", facts.get("output-nonempty", 0) > 0)


@pytest.mark.parametrize("death_offset", ["span_end", "denom_start", "start"])
def test_denominator_death_exactly_at_period_edge(death_offset: str) -> None:
    """A death exactly on a period boundary: at the span end the window
    truncates to the death day; at the window start it drops the member;
    at the query start it never opens a window. Hand-built fixture."""
    enr_end = date(2014, 12, 31)
    death = {
        "span_end": enr_end,
        "denom_start": _START + timedelta(days=183),  # exactly denom_start
        "start": _START,
    }[death_offset]
    dataset = _one_patient_fixture(death)
    study = make_study2(ALL_CODES[:20], (), ("D00001",),
                        strata=CIDA_LEVELS, start=_START, end=_END,
                        over_a={"wash_per": 0})
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("cida denominators")
        actual = fetch_rows(harness.engine, "denomcounts")
    expected = expected_denomcounts(dataset, study)
    assert sorted(actual) == sorted(expected)
    # and the window really did react to the boundary
    total_days = sum((r[16] or 0) for r in actual)
    if death_offset == "start":
        assert total_days == 0
    else:
        assert total_days > 0


def _one_patient_fixture(death: date) -> SyntheticDataset:
    p = Patient(1, date(1950, 7, 2), "F", "1", "N", "02108", _START)
    # fill inside the eligible window, well clear of the washout chain
    fill = _START + timedelta(days=200)
    return SyntheticDataset(
        patients=(p,),
        deaths=(Death(1, death),),
        enrollment=(EnrollmentSpan(1, _START, date(2014, 12, 31),
                                   "Y", "Y", "Y"),),
        dispensings=(Dispensing(1, fill, "E00001", "ND", 30, 10.0, 1001),),
        diagnoses=(),
        procedures=(),
        encounters=(),
        labs=(),
        exposure_codes=ALL_CODES[:20],
        event_codes=("D00001",),
        noise_drug_codes=("N00001",),
        noise_dx_codes=("X00001",),
        start=_START,
        end=_END,
    )


def test_denominator_exclusion_exactly_covers_stratum_boundary() -> None:
    """An exclusion window that shaves EXACTLY the first eligible day
    removes the only eligible day before a birthday, so the member
    appears only in the older band — calendar-day allocation, boundary-exact.

    Hand math: born 1950-08-02; query 2014; enrollment 2014-01-01 with
    enr_days 212 so denom_start = 2014-08-01 (age 63); bands '00-63'
    and '64+'; an EXC diagnosis (condfrom=condto=0) dated exactly
    denom_start disqualifies exactly that one day, so the surviving
    segment starts 2014-08-02 — the 64th birthday, band '64+' — and the
    member must appear ONLY there, with 152 member-days
    (2014-08-02..2014-12-31). A wrong clip boundary leaves the day in
    band '00-63' and fails the assertion.
    """
    start = date(2014, 1, 1)
    end = date(2014, 12, 31)
    denom_start = start + timedelta(days=212)
    assert denom_start == date(2014, 8, 1)
    p = Patient(1, date(1950, 8, 2), "F", "1", "N", "02108", start)
    dataset = SyntheticDataset(
        patients=(p,),
        deaths=(),
        enrollment=(EnrollmentSpan(1, start, end, "Y", "Y", "Y"),),
        dispensings=(Dispensing(1, denom_start + timedelta(days=10),
                                "E00001", "ND", 30, 10.0, 1001),),
        # pdx 'S' on an AV noise claim: deliberate out-of-domain value
        # (SCDM Pdx is blank on ambulatory claims); wildcard-neutral.
        diagnoses=(Diagnosis(1, denom_start, "X00001", "09", "S", "AV"),),
        procedures=(),
        encounters=(),
        labs=(),
        exposure_codes=ALL_CODES[:20],
        event_codes=("D00001",),
        noise_drug_codes=("N00001",),
        noise_dx_codes=("X00001",),
        start=start,
        end=end,
    )
    from qrp.config import AgeStrata, StratumLevel
    study = make_study2(
        ALL_CODES[:20], (), (),
        strata=(StratumLevel(table_id="t2cida", level_id="A",
                             levelvars=("agegroup",)),),
        start=start, end=end,
        over_a={"wash_per": 0, "enr_days": 212,
                "age_strata": AgeStrata.parse("00-63 64+")},
        exclusions=(InclusionRule(
            cohortgrp="cohort_a", cond=1, criteria="EXC",
            codecat="DX", condfrom=0, condto=0,
            codes=("X00001",)),),
    )
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("cida denominators")
        actual = fetch_rows(harness.engine, "denomcounts")
    expected = expected_denomcounts(dataset, study)
    assert sorted(actual) == sorted(expected)
    # exactly one stratum row, in the OLDER band, one member, 152 days
    assert [tuple(r) for r in actual] == [
        ("A", "cohort_a", "64+", 2, None, None, None, None,
         None, None, None, None, None, None, None, 1, 152)]


@pytest.mark.parametrize("diff_field", ["enrol_gap", "exclusion"])
def test_denominator_example_pair_differing_one_field(diff_field: str) -> None:
    """Deterministic pair: cohorts differing in exactly one config field
    (enrollment config vs exclusion config) must produce different
    denominators when that field matters, and the oracle must agree with
    the production result in both cases."""
    start, end = _START, _END
    if diff_field == "enrol_gap":
        # distinct enrollment_num, as the cohort file would carry: one
        # enrollment build per number, different gap parameters
        over_a = {"enrol_gap": 0, "enrollment_num": 1, "wash_per": 0}
        over_b = {"enrol_gap": 60, "enrollment_num": 2, "wash_per": 0}
        exclusions: tuple[InclusionRule, ...] = ()
        spans_a = (
            EnrollmentSpan(1, start, date(2014, 5, 31), "Y", "Y", "Y"),
            EnrollmentSpan(1, date(2014, 6, 10), end, "Y", "Y", "Y"),
        )
        dataset = _one_patient_fixture(date(2014, 12, 31))
        dataset = replace_ds_spans(dataset, spans_a)
    else:
        over_a = {"wash_per": 0}
        over_b = {"wash_per": 0}
        exclusions = (InclusionRule(
            cohortgrp="cohort_b", cond=1, criteria="EXC", codecat="DX",
            condfrom=0, condto=0, codes=("X00001",)),)
        dataset = _one_patient_fixture(date(2014, 12, 31))
        dataset = SyntheticDataset(
            patients=dataset.patients,
            deaths=(),
            enrollment=(EnrollmentSpan(1, start, end, "Y", "Y", "Y"),),
            dispensings=dataset.dispensings,
            # pdx 'S' on an AV noise claim: deliberate out-of-domain
            # value (SCDM Pdx is blank on ambulatory claims);
            # wildcard-neutral.
            diagnoses=(Diagnosis(1, start + timedelta(days=250),
                                 "X00001", "09", "S", "AV"),),
            procedures=(), encounters=(), labs=(),
            exposure_codes=dataset.exposure_codes,
            event_codes=dataset.event_codes,
            noise_drug_codes=dataset.noise_drug_codes,
            noise_dx_codes=dataset.noise_dx_codes,
            start=start, end=end,
        )
    study = make_study2(ALL_CODES[:20], ALL_CODES[:20], (),
                        strata=CIDA_LEVELS, start=start, end=end,
                        over_a=over_a, over_b=over_b,
                        exclusions=exclusions)
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("cida denominators")
        actual = fetch_rows(harness.engine, "denomcounts")
    expected = expected_denomcounts(dataset, study)
    assert sorted(actual) == sorted(expected)


def replace_ds_spans(dataset: SyntheticDataset,
                     spans: tuple[EnrollmentSpan, ...]) -> SyntheticDataset:
    return SyntheticDataset(
        patients=dataset.patients, deaths=(), enrollment=spans,
        dispensings=dataset.dispensings, diagnoses=(), procedures=(),
        encounters=(), labs=(), exposure_codes=dataset.exposure_codes,
        event_codes=dataset.event_codes,
        noise_drug_codes=dataset.noise_drug_codes,
        noise_dx_codes=dataset.noise_dx_codes,
        start=dataset.start, end=dataset.end)
