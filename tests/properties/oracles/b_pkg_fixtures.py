"""Fixtures and comparators shared by the package-B properties.

Everything here is fixture plumbing (input construction and output
comparison), not semantics: semantics live in the b_*_model oracles and
shared serialization in synth.rows_for_table.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from hypothesis import strategies as st

from qrp.config import StudyConfig

from ..synth import (
    Death,
    Dispensing,
    EnrollmentSpan,
    Patient,
    SyntheticDataset,
    build_study,
)

START = date(2011, 1, 3)
END = date(2012, 6, 30)
EXPOSED_CODES = ("E00001", "E00002")
NOISE_CODE = "N00001"


def two_cohort_study(**overrides: Any) -> StudyConfig:
    """A valid study with cohorts a/b over E00001/E00002. Overrides go to
    build_study; `cohort_overrides` (a dict) is applied to BOTH cohorts
    afterwards via dataclasses.replace, then revalidated."""
    cohort_overrides = overrides.pop("cohort_overrides", {}) or {}
    study = build_study(EXPOSED_CODES, (), start=START, end=END,
                        **overrides)
    if cohort_overrides:
        cohorts = tuple(replace(c, **cohort_overrides) for c in study.cohorts)
        study = replace(study, cohorts=cohorts)
        study.validate()
    return study


def mini_dataset(
    *,
    patients: tuple[Patient, ...],
    enrollment: tuple[EnrollmentSpan, ...] = (),
    dispensings: tuple[Dispensing, ...] = (),
    deaths: tuple[Death, ...] = (),
) -> SyntheticDataset:
    """A minimal synthetic extract: RX dispensings only, no diagnoses,
    procedures, encounters or labs — exactly what the enrollment,
    stockpiling, index and episode stages read."""
    return SyntheticDataset(
        patients=patients, deaths=deaths, enrollment=enrollment,
        dispensings=dispensings, diagnoses=(), procedures=(), encounters=(),
        labs=(), exposure_codes=EXPOSED_CODES, event_codes=(),
        noise_drug_codes=(NOISE_CODE,), noise_dx_codes=(),
        start=START, end=END,
    )


def patient(pid: int, birth: date = date(1980, 5, 1), sex: str = "F",
            race: str = "1", hispanic: str = "N", postal: str = "02108",
            postal_date: date = START) -> Patient:
    return Patient(pid, birth, sex, race, hispanic, postal, postal_date)


def span(pid: int, start: date, end: date, medcov: str = "Y",
         drugcov: str = "Y", chart: str = "Y") -> EnrollmentSpan:
    return EnrollmentSpan(pid, start, end, medcov, drugcov, chart)


def disp(pid: int, day: date, code: str, sup: int, amt: float,
         codetype: str = "ND") -> Dispensing:
    return Dispensing(pid, day, code, codetype, sup, amt, 1000 + pid)


# ---------------------------------------------------------------------
# Generated inputs: enrollment shapes relative to the config's gap
# threshold, dispensings with deliberate same-day ties, coverage-flag
# variety. Expanded deterministically from the drawn spec.
# ---------------------------------------------------------------------

_SPAN_SHAPES = ("overlap", "adjacent", "gap_minus", "gap_exact",
                "gap_plus", "far")
_FLAG_POOL = ("YY", "AY", "YN", "NY", "AA")
_SUPS = (1, 3, 7, 16, 30)
_AMTS = (0.5, 1.0, 2.5, 7.5, 10.0, 10.5)
_DAY_JITTER = (-1, 0, 1, 2, 3)

spec_strategy = st.fixed_dictionaries({
    "enrol_gap": st.integers(0, 3),
    "wash_per": st.sampled_from((0, 30, 183, None)),
    "coverage": st.sampled_from(("MD", "M", "D")),
    "gap_b_differs": st.booleans(),
    "patients": st.lists(
        st.fixed_dictionaries({
            "spans": st.lists(
                st.tuples(st.sampled_from(_SPAN_SHAPES),
                          st.integers(1, 12),
                          st.sampled_from(_FLAG_POOL)),
                min_size=1, max_size=3),
            "fills": st.lists(
                st.fixed_dictionaries({
                    "code": st.sampled_from(("E00001", "E00002", NOISE_CODE)),
                    "day": st.integers(0, 40),
                    "sup": st.sampled_from(_SUPS),
                    "amt": st.sampled_from(_AMTS),
                    "twin": st.booleans(),
                    "twin_sup_delta": st.integers(-1, 1),
                    "twin_amt_epsilon": st.sampled_from(
                        (0.0, 0.125, 1e-9)),
                }),
                min_size=1, max_size=4),
            "death_day": st.none() | st.integers(0, 500),
        }),
        min_size=1, max_size=4),
    "dup_demog": st.booleans(),
    "leap_birth": st.sampled_from((None, "2000-02-29", "2004-02-29")),
})


def expand_spec(spec: dict, extra_cohort_overrides: dict | None = None,
) -> tuple[SyntheticDataset, StudyConfig]:
    """Deterministically turn a drawn spec into (dataset, study)."""
    gap = spec["enrol_gap"]
    overrides = {
        # cohort_b usually carries its own gap -> a second enrollment
        # config; both paths of the cfg dedup get exercised.
        "enrol_gap": gap + (1 if spec["gap_b_differs"] else 0),
    }
    if extra_cohort_overrides:
        overrides.update(extra_cohort_overrides)
    study = two_cohort_study(
        coverage=spec["coverage"], enrol_gap=gap,
        wash_per=spec["wash_per"],
        cohort_overrides=overrides)
    patients: list[Patient] = []
    enrollment: list[EnrollmentSpan] = []
    dispensings: list[Dispensing] = []
    deaths: list[Death] = []
    for i, pspec in enumerate(spec["patients"], start=1):
        birth = date(1955 + i, 3, 15)
        if i == 1 and spec["leap_birth"]:
            y, m, d = (int(x) for x in spec["leap_birth"].split("-"))
            birth = date(y, m, d)
        patients.append(patient(i, birth=birth))
        cursor = START + timedelta(days=5)
        for j, (shape, length, flags) in enumerate(pspec["spans"]):
            s_start = (cursor + timedelta(days=_shape_offset(shape, gap) + 1)
                       if j > 0 else cursor)
            s_end = s_start + timedelta(days=length - 1)
            enrollment.append(span(i, s_start, s_end,
                                   medcov=flags[0], drugcov=flags[1]))
            cursor = s_end
        for fspec in pspec["fills"]:
            base = START + timedelta(days=fspec["day"])
            dispensings.append(disp(i, base, fspec["code"],
                                    fspec["sup"], fspec["amt"]))
            if fspec["twin"]:
                sup = min(30, max(1, fspec["sup"] + fspec["twin_sup_delta"]))
                amt = fspec["amt"] + fspec["twin_amt_epsilon"]
                dispensings.append(disp(i, base, fspec["code"], sup, amt))
        if pspec["death_day"] is not None:
            deaths.append(Death(i, START + timedelta(days=pspec["death_day"])))
    if spec["dup_demog"]:
        victims = patients[0]
        patients.append(Patient(
            victims.patid, victims.birth_date,
            "M" if victims.sex == "F" else "F",
            victims.race, victims.hispanic, victims.postalcode,
            victims.postalcode_date))
    return mini_dataset(patients=tuple(patients),
                        enrollment=tuple(enrollment),
                        dispensings=tuple(dispensings),
                        deaths=tuple(deaths)), study


def _shape_offset(shape: str, gap: int) -> int:
    """Blank days between the previous span's end and this span's start.
    'far' ruptures for any gap in (0..3); the other shapes sit at and
    around the threshold."""
    return {
        "overlap": -2,
        "adjacent": 0,
        "gap_minus": gap - 1,
        "gap_exact": gap,
        "gap_plus": gap + 1,
        "far": gap + 4,
    }[shape]


# ---------------------------------------------------------------------
# Output comparison: canonical row dicts, float-tolerant field compare,
# mismatch messages that show both sides.
# ---------------------------------------------------------------------

def _key(row: dict, cols: tuple[str, ...]) -> tuple:
    return tuple(
        round(row[c], 6) if isinstance(row[c], float) else row[c]
        for c in cols)


def assert_rows_match(sql_rows: Sequence[tuple], model_rows: Sequence[tuple],
                      cols: tuple[str, ...], label: str) -> None:
    sql = sorted([dict(zip(cols, r)) for r in sql_rows], key=lambda r: _key(r, cols))
    model = sorted([dict(zip(cols, r)) for r in model_rows],
                   key=lambda r: _key(r, cols))
    if len(sql) != len(model):
        raise AssertionError(
            f"{label}: SQL returned {len(sql)} rows, model {len(model)}\n"
            f"sql:   {sql}\nmodel: {model}")
    for k, (a, b) in enumerate(zip(sql, model)):
        for c in cols:
            va, vb = a[c], b[c]
            if isinstance(va, float) or isinstance(vb, float):
                ok = (va is not None and vb is not None
                      and abs(va - vb) <= 1e-9 * max(1.0, abs(va), abs(vb)))
            else:
                ok = va == vb
            if not ok:
                raise AssertionError(
                    f"{label}: row {k} field {c!r}: SQL {va!r} "
                    f"!= model {vb!r}\nsql row:   {a}\nmodel row: {b}")


__all__ = [
    "END",
    "EXPOSED_CODES",
    "NOISE_CODE",
    "START",
    "assert_rows_match",
    "expand_spec",
    "mini_dataset",
    "patient",
    "spec_strategy",
    "two_cohort_study",
]
