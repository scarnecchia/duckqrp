# pattern: Mixed (unavoidable) -- harness driving and fixture building for
# the package C oracles; the models themselves stay pure in c_models.py.
"""Harness glue and fixture builders for package C.

* study builders that extend `synth.build_study` with the dose and
  inclusion fields that builder deliberately leaves unset;
* deterministic grid fixtures whose claim dates sit on a small offset
  grid, so rule windows and dose lookbacks land exactly on boundaries;
* the two-pass stage harness runner: snapshot the upstream relations a
  stage consumes, run the stage, fetch its outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

from qrp.config import CohortConfig, InclusionRule, StudyConfig

from ..harness import StageHarness
from .c_models import DoseClaim, RuleKey, RuleSpec
from ..synth import (
    Death,
    Diagnosis,
    Dispensing,
    EnrollmentSpan,
    Patient,
    SyntheticDataset,
    build_study,
)

GRID_START = date(2014, 1, 1)
GRID_END = date(2014, 4, 30)
GRID_ANCHOR = date(2014, 2, 15)
ENR_START = date(2013, 6, 1)
ENR_END = date(2014, 12, 31)


# ---------------------------------------------------------------------
# Study builders
# ---------------------------------------------------------------------


def cohort_a(
    *,
    exposure_codes: Sequence[str],
    event_codes: Sequence[str] = (),
    wash_per: int = 183,
    enr_days: int = 183,
    episode_gap: int = 0,
    **overrides: Any,
) -> CohortConfig:
    """One cohort named cohort_a over RX/ND exposure and DX/09 events.

    Every event code gets the wildcard care-setting pair ('**', '*'),
    which is exactly what the study loader emits for a code with no
    CARESETTINGPRINCIPAL (parse_care_setting on an empty value); the
    event extraction inner-joins cfg_care_setting, so a cohort without
    these rows would silently lose every outcome claim."""
    base = CohortConfig(
        cohortgrp="cohort_a",
        coverage="MD",
        enrol_gap=0,
        wash_per=wash_per,
        enr_days=enr_days,
        episode_gap=episode_gap,
        exposure_codes=tuple((c, "RX", "ND", None) for c in exposure_codes),
        event_codes=tuple((c, "DX", "09", None) for c in event_codes),
        event_care_settings=tuple(
            (c, "**", "*") for c in event_codes),
    )
    if overrides:
        base = replace(base, **overrides)
    return base


def single_cohort_study(
    cohort: CohortConfig,
    *,
    inclusions: Sequence[InclusionRule] = (),
    code_strength: Sequence[tuple[str, float]] = (),
    start: date = GRID_START,
    end: date = GRID_END,
) -> StudyConfig:
    """A valid one-cohort study; validates like every shipped study."""
    study = StudyConfig(
        study_type=2,
        start_date=start,
        end_date=end,
        cohorts=(cohort,),
        inclusions=tuple(inclusions),
        code_strength=tuple(code_strength),
    )
    study.validate()
    return study


def with_dose(
    cohort: CohortConfig,
    *,
    exposure_codes: Sequence[str],
    code_strength: Sequence[tuple[str, float]],
    min_cum_dose: float | None = None,
    max_cum_dose: float | None = None,
    cum_dose_per: int | None = None,
    min_cfdd: float | None = None,
    max_cfdd: float | None = None,
    wash_per: int = 183,
    inclusions: Sequence[InclusionRule] = (),
) -> StudyConfig:
    """A one-cohort study carrying the 42/55 dose knobs."""
    c = replace(
        cohort,
        exposure_codes=tuple((x, "RX", "ND", None) for x in exposure_codes),
        wash_per=wash_per,
        min_cum_dose=min_cum_dose,
        max_cum_dose=max_cum_dose,
        cum_dose_per=cum_dose_per,
        min_cfdd=min_cfdd,
        max_cfdd=max_cfdd,
    )
    return single_cohort_study(
        c, inclusions=inclusions, code_strength=code_strength)


# ---------------------------------------------------------------------
# Deterministic grid fixture
# ---------------------------------------------------------------------


def grid_dataset(
    *,
    patid: int = 1,
    fills: Sequence[tuple[int, str, int, float]] = (),
    dxs: Sequence[tuple[int, str, str]] = (),
    death_day: int | None = None,
    duplicate_demographics: bool = False,
    second_patient: bool = False,
    index_fill: bool = True,
) -> SyntheticDataset:
    """A hand-sized dataset on the grid anchor + day offsets.

    fills: (day offset from GRID_ANCHOR, code, days supply, amount)
    dxs:   (day offset, code, enctype)
    Dates stay inside [GRID_START, GRID_END]; enrollment spans
    [ENR_START, ENR_END] so enr_days=183 membership holds for any grid
    index date. `index_fill` prepends the canonical index dispensing
    (day 0, E00001, 30 days, amount 10) -- every grid episode anchors
    there; callers that supply their own day-0 fill pass False.
    """
    def d(off: int) -> date:
        return GRID_ANCHOR + timedelta(days=off)

    patients = [Patient(patid, date(1950, 1, 1), "F", "1", "N", "00000",
                        GRID_START)]
    if second_patient:
        patients.append(Patient(patid + 1, date(1960, 1, 1), "M", "2", "N",
                                "00000", GRID_START))
    if duplicate_demographics:
        patients.append(Patient(patid, date(1950, 1, 1), "M", "1", "N",
                                "00000", GRID_START))
    enrollment = tuple(
        EnrollmentSpan(p.patid, ENR_START, ENR_END, "Y", "Y", "Y")
        for p in patients
    )
    all_fills = ([(0, "E00001", 30, 10.0)] if index_fill else []) + list(fills)
    dispensings = tuple(
        Dispensing(patid, d(off), code, "ND", sup, amt, 1000 + patid)
        for off, code, sup, amt in all_fills
    )
    diagnoses = tuple(
        # pdx 'P' rides whatever enctype the caller passes; builder
        # studies use the ('**','*') wildcard, so pdx never gates a
        # claim here. SCDM populates Pdx only on IP/IS claims — callers
        # passing ambulatory enctypes carry a deliberate out-of-domain
        # value (harmless under the wildcard).
        Diagnosis(patid, d(off), code, "09", "P", enctype)
        for off, code, enctype in dxs
    )
    deaths = tuple(
        Death(patid, d(death_day)) for _ in (0,) if death_day is not None
    )
    codes = sorted({f[1] for f in fills} | {x[1] for x in dxs})
    exposure = tuple(c for c in codes if c.startswith("E"))
    events = tuple(c for c in codes if c.startswith("D"))
    return SyntheticDataset(
        patients=tuple(patients),
        deaths=deaths,
        enrollment=enrollment,
        dispensings=dispensings,
        diagnoses=diagnoses,
        procedures=(),
        encounters=(),
        labs=(),
        exposure_codes=exposure,
        event_codes=events,
        noise_drug_codes=(),
        noise_dx_codes=(),
        start=GRID_START,
        end=GRID_END,
    )


# ---------------------------------------------------------------------
# Harness plumbing: snapshot inputs, run the stage, fetch outputs
# ---------------------------------------------------------------------

# Column lists per relation. Explicit lists, because 52 and 55 rebuild
# ptsmasterlist with EXCLUDE/reordered columns.
DOSE_INPUT_COLS = (
    "cohortgrp", "stockgroup", "patid", "adate", "code", "rxsup",
    "rxamt", "codecat",
)
CAND_COLS = (
    "cohortgrp", "patid", "adate", "orig_adate",
)
EPISODE_COLS = (
    "cohortgrp", "patid", "indexdt", "episodeenddt", "enr_end", "deathdt",
    "atriskindexdt",
)


def fetch_dicts(
    harness: StageHarness, table: str,
    columns: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    """All rows of `table` restricted to `columns`, as dicts, ordered."""
    collist = ", ".join(f'"{c}"' for c in columns)
    cur = harness.engine.con.execute(
        f"SELECT {collist} FROM {table} ORDER BY ALL")
    names = [str(d[0]) for d in cur.description]
    return tuple(dict(zip(names, row)) for row in cur.fetchall())


@dataclass(frozen=True)
class TwoPass:
    """Input snapshots taken before a stage ran, and outputs after."""

    inputs: dict[str, tuple[dict[str, Any], ...]]
    outputs: dict[str, tuple[dict[str, Any], ...]]
    executed: tuple[str, ...]


def two_pass_run(
    study: StudyConfig,
    dataset: SyntheticDataset,
    *,
    stop_before: str,
    stop_after: str,
    input_tables: Mapping[str, Sequence[str]],
    output_tables: Mapping[str, Sequence[str]],
) -> TwoPass:
    """Run the pipeline twice on one harness: first to `stop_before`,
    snapshotting the stage inputs; then through `stop_after`, fetching
    the stage outputs. Stage execution is deterministic CREATE OR
    REPLACE, so the second pass rebuilds identical inputs."""
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through(stop_before)
        inputs = {
            name: fetch_dicts(harness, name, cols)
            for name, cols in input_tables.items()
        }
        executed = harness.run_through(stop_after)
        outputs = {
            name: fetch_dicts(harness, name, cols)
            for name, cols in output_tables.items()
        }
        return TwoPass(inputs, outputs, tuple(executed))


def dose_claims_from_rows(
    rows: Iterable[Mapping[str, Any]],
    strengths: Mapping[str, float],
) -> list[DoseClaim]:
    """claim_dose rows (exposure_claims joined to strengths) as model
    DoseClaim values, via the shared serializer boundary's own output."""
    out: list[DoseClaim] = []
    for r in rows:
        strength: float | None = strengths.get(str(r["code"]))
        if strength is None:
            continue    # the view's inner join drops strengthless codes
        out.append(DoseClaim(
            adate=r["adate"], rxsup=int(r["rxsup"]),
            rxamt=float(r["rxamt"]), strength=strength))
    return out


def rules_by_key(
    rules: Sequence[InclusionRule],
) -> dict[RuleKey, RuleSpec]:
    """Map cfg_inclusion rows to the model's per-key threshold specs.

    Config rows sharing (criteria, cond, subcond) must agree on every
    threshold (SAS expects exactly one value per subcondlevel; the
    shipped stage would otherwise any_value one arbitrarily), and their
    code sets merge -- one row per code. minrxdays is reset to 1 for
    non-RX codes exactly as register_config does.
    """
    out: dict[RuleKey, RuleSpec] = {}
    for r in rules:
        key = RuleKey(criteria=r.criteria, cond=r.cond, subcond=r.subcond)
        spec = RuleSpec(
            codes=frozenset(r.codes),
            codecat=r.codecat,
            condfrom=r.condfrom,
            condto=r.condto,
            condfromanchor=r.condfromanchor,
            condtoanchor=r.condtoanchor,
            codedays=r.codedays,
            minrxdays=r.minrxdays if r.codecat == "RX" else 1,
            mincumdose=r.mincumdose,
            minafdd=r.minafdd,
            maxafdd=r.maxafdd,
            subcond_inclusion=r.subcond_inclusion,
        )
        prev = out.get(key)
        if prev is None:
            out[key] = spec
            continue
        for field in ("codecat", "condfrom", "condto", "condfromanchor",
                      "condtoanchor", "codedays", "minrxdays",
                      "mincumdose", "minafdd", "maxafdd",
                      "subcond_inclusion"):
            if getattr(prev, field) != getattr(spec, field):
                raise AssertionError(
                    f"inconsistent {field} within condition key {key}: "
                    f"the shipped stage would pick one arbitrarily")
        out[key] = replace(prev, codes=prev.codes | spec.codes)
    return out


def build_study_compat(
    exposure_codes: Sequence[str],
    event_codes: Sequence[str] = (),
    **kwargs: Any,
) -> StudyConfig:
    """Re-export of synth.build_study for callers that want the shared
    builder (kept here so test modules import glue only)."""
    return build_study(exposure_codes, event_codes, **kwargs)


__all__ = [
    "CAND_COLS",
    "DOSE_INPUT_COLS",
    "ENR_END",
    "ENR_START",
    "EPISODE_COLS",
    "GRID_ANCHOR",
    "GRID_END",
    "GRID_START",
    "TwoPass",
    "build_study_compat",
    "cohort_a",
    "dose_claims_from_rows",
    "fetch_dicts",
    "grid_dataset",
    "single_cohort_study",
    "two_pass_run",
    "with_dose",
]
