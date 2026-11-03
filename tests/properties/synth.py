"""Typed synthetic SCDM data and a valid StudyConfig builder.

Column names and types are cross-checked against `qrp.scdm.SCDM` (the
required/optional sets the pipeline actually reads) and keep the same
table names and file layout as `tools/gen_synthetic.py` (one folder per
table). Serialization here is the ONE shared path: property tests, the
harness self-tests, and any future oracle write rows through
`write_parquet()` so a discrepancy between what a test THINKS it wrote
and what the pipeline reads is impossible by construction.

Deliberate ties
---------------
Real claims data carries exact duplicates and same-day dispensings;
purely random data does not. Mirroring gen_synthetic's tie injection,
this builder can add:

* duplicate demographic rows — same patid and birth_date, different
  sex — which exercise the total-order tie-break in 10_normalize.sql.
  These rows deliberately violate SCDM 8.2.2 DEM's unique-row rule
  ("Unique row: PatID" — one record per patient, demographic.md), so
  they are non-canonical data-defect probes, like package B's
  overlapping enrollment spans, not shapes a compliant extract carries;
* same-patient/same-day/same-supply dispensings with DIFFERENT amounts,
  which exercise every supply/amount tie-break downstream.

Both keep the full payload on the dataclass, so a witness retains the
tie rows themselves and a shrunken failing example stays reproducible.

Supported configurations only
-----------------------------
`build_study()` produces StudyConfig values that pass `validate()`:
no dose restrictions (no code strengths needed), no combinations, no
unsupported table ids. Packages that need richer studies should extend
this builder rather than hand-rolling StudyConfig in a test.

Every event code also carries the loader's care-setting expansion: an
absent CARESETTINGPRINCIPAL means the all-settings wildcard
(`('**', '*')`, qrp.config.CARE_SETTING_ALL), and the event_claims
stage INNER JOINs cfg_care_setting on (cohortgrp, code) — so a study
without those rows would silently lose every outcome claim. test_
property_builder_events.py pins the builder's rows against the same
codes fed through the real loader.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from math import ceil
from pathlib import Path
from typing import Any

import duckdb

from qrp.config import (
    CARE_SETTING_ALL,
    CohortConfig,
    Covariate,
    InclusionRule,
    RiskScoreCode,
    StratumLevel,
    StudyConfig,
)
from qrp.engine import sql_str

# ---------------------------------------------------------------------
# Row types. Frozen dataclasses; tuple fields on the dataset, so a
# dataset is hashable-safe, cheap to repr in a witness, and cannot be
# mutated by a test (no shared mutable fixture state).
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class Patient:
    """One demographic row (SCDM `demographic`)."""

    patid: int
    # None is SCDM-legal (demographic.md Birth_Date: "missing allowed");
    # the pipeline drops such members (demographics_missing, attrition
    # level 1). GenerateOptions.null_birth_dates draws one.
    birth_date: date | None
    sex: str                      # F / M / U / A — raw value, not collapsed
    race: str
    hispanic: str
    postalcode: str
    postalcode_date: date


@dataclass(frozen=True)
class Death:
    patid: int
    deathdt: date


@dataclass(frozen=True)
class EnrollmentSpan:
    """One enrollment span (SCDM `enrollment`)."""

    patid: int
    enr_start: date
    enr_end: date
    medcov: str                   # Y / N / U / A (SCDM 8.2.2 MedCov; 'A'
                                  # = ambulatory-only medical coverage)
    drugcov: str                  # Y / N / U (SCDM 8.2.2 DrugCov)
    chart: str                    # Y / N; the column is optional in the
                                  # pipeline's read set (qrp.scdm) and a
                                  # NULL reads as 'N' — in SCDM 8.2.2 the
                                  # column itself is required


@dataclass(frozen=True)
class Dispensing:
    """One dispensing (SCDM `dispensing`). The code column is `rx`."""

    patid: int
    rxdate: date
    rx: str
    rx_codetype: str
    rxsup: int                    # days of supply, > 0
    rxamt: float                  # amount dispensed, > 0, fractional allowed
    providerid: int               # inert payload: the pipeline never
                                  # reads it (qrp.scdm dispensing spec),
                                  # so the .U/null case SCDM allows is
                                  # unobservable through this pipeline


@dataclass(frozen=True)
class Diagnosis:
    patid: int
    adate: date
    dx: str
    dx_codetype: str
    pdx: str
    enctype: str                  # AV / ED / IP / IS / OA (SCDM domain);
                                  # 'XX' is 10_normalize.sql's fallback
                                  # for a missing/null column, never a
                                  # value a builder emits


@dataclass(frozen=True)
class Procedure:
    patid: int
    adate: date
    px: str
    px_codetype: str
    enctype: str
    encounterid: int | None = None
    providerid: int | None = None
    origpx: str | None = None


@dataclass(frozen=True)
class Encounter:
    """One encounter row. `ddate`/`discharge_status` drive inpatient
    enveloping and encounter-derived deaths in 10_normalize.sql.

    SCDM 8.2.2 (encounter.md): `discharge_status` stays ""/None in this
    generator — the d-package oracles model deaths from the death table
    only, so an 'EX' draw would need oracle-side encounter-death
    modeling first; the EX -> death derivation is pinned in
    tests/test_pipeline.py
    (test_deaths_come_from_the_death_table_and_expired_encounters,
    including lowercase 'ex' and a missing ddate). EncounterID
    uniqueness is UNRESOLVED in the reference (encounter.md:23); the
    patid*10-family ids used here are globally unique, satisfying both
    readings."""

    patid: int
    adate: date
    enctype: str
    encounterid: int
    ddate: date | None = None
    discharge_status: str | None = None


@dataclass(frozen=True)
class LabResult:
    """One lab row. `result_dt`/`order_dt` are SAS numeric dates stored
    as DOUBLE (days since 1960-01-01), NULL in the real extracts seen —
    the same fall-through is reproduced here."""

    patid: int
    lab_dt: date
    result_dt: float | None
    order_dt: float | None
    ms_result_n: float | None
    ms_result_c: str | None
    result_type: str
    loinc: str
    px: str
    ms_test_name: str
    ms_test_sub_category: str
    specimen_source: str
    ms_result_unit: str
    fast_ind: str
    pt_loc: str


@dataclass(frozen=True)
class SyntheticDataset:
    """A complete synthetic SCDM extract, plus the code pools used to
    build it (so a study builder and an oracle share one vocabulary)."""

    patients: tuple[Patient, ...]
    deaths: tuple[Death, ...]
    enrollment: tuple[EnrollmentSpan, ...]
    dispensings: tuple[Dispensing, ...]
    diagnoses: tuple[Diagnosis, ...]
    procedures: tuple[Procedure, ...]
    encounters: tuple[Encounter, ...]
    labs: tuple[LabResult, ...]
    exposure_codes: tuple[str, ...]
    event_codes: tuple[str, ...]
    noise_drug_codes: tuple[str, ...]
    noise_dx_codes: tuple[str, ...]
    start: date
    end: date

    # ---- tie inspection (AC.1 payloads) -----------------------------

    def duplicate_demographic_patids(self) -> tuple[int, ...]:
        """Patids carrying more than one demographic row."""
        seen: set[int] = set()
        dupes: list[int] = []
        for p in self.patients:
            if p.patid in seen and p.patid not in dupes:
                dupes.append(p.patid)
            seen.add(p.patid)
        return tuple(dupes)

    def same_supply_ties(self) -> tuple[tuple[Dispensing, Dispensing], ...]:
        """Same-patient/same-day/same-code/same-supply pairs whose
        amounts differ — the payload a tie-breaking defect would need."""
        by_key: dict[tuple[int, date, str, int], list[Dispensing]] = {}
        for d in self.dispensings:
            by_key.setdefault((d.patid, d.rxdate, d.rx, d.rxsup), []).append(d)
        out: list[tuple[Dispensing, Dispensing]] = []
        for group in by_key.values():
            for i, a in enumerate(group):
                for b in group[i + 1:]:
                    if a.rxamt != b.rxamt:
                        out.append((a, b))
        return tuple(out)


# ---------------------------------------------------------------------
# Exact SQL schemas. Column NAME sets are asserted against scdm.SCDM in
# test_property_builder_schema_and_ties; the types mirror what
# tools/gen_synthetic.py writes and what 10_normalize.sql casts from.
# ---------------------------------------------------------------------

TABLE_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "demographic": (
        ("patid", "BIGINT"),
        ("birth_date", "DATE"),
        ("sex", "VARCHAR"),
        ("race", "VARCHAR"),
        ("hispanic", "VARCHAR"),
        ("postalcode", "VARCHAR"),
        ("postalcode_date", "DATE"),
    ),
    "death": (
        ("patid", "BIGINT"),
        ("deathdt", "DATE"),
    ),
    "enrollment": (
        ("patid", "BIGINT"),
        ("enr_start", "DATE"),
        ("enr_end", "DATE"),
        ("medcov", "VARCHAR"),
        ("drugcov", "VARCHAR"),
        ("chart", "VARCHAR"),
    ),
    "dispensing": (
        ("patid", "BIGINT"),
        ("rxdate", "DATE"),
        ("rx", "VARCHAR"),
        ("rx_codetype", "VARCHAR"),
        ("rxsup", "INTEGER"),
        ("rxamt", "DOUBLE"),
        ("providerid", "BIGINT"),
    ),
    "diagnosis": (
        ("patid", "BIGINT"),
        ("adate", "DATE"),
        ("dx", "VARCHAR"),
        ("dx_codetype", "VARCHAR"),
        ("pdx", "VARCHAR"),
        ("enctype", "VARCHAR"),
    ),
    "procedure": (
        ("patid", "BIGINT"),
        ("adate", "DATE"),
        ("px", "VARCHAR"),
        ("px_codetype", "VARCHAR"),
        ("enctype", "VARCHAR"),
        ("encounterid", "BIGINT"),
        ("providerid", "BIGINT"),
        ("origpx", "VARCHAR"),
    ),
    "encounter": (
        ("patid", "BIGINT"),
        ("adate", "DATE"),
        ("enctype", "VARCHAR"),
        ("encounterid", "BIGINT"),
        ("ddate", "DATE"),
        ("discharge_status", "VARCHAR"),
    ),
    "lab_result": (
        ("patid", "BIGINT"),
        ("lab_dt", "DATE"),
        ("result_dt", "DOUBLE"),
        ("order_dt", "DOUBLE"),
        ("ms_result_n", "DOUBLE"),
        ("ms_result_c", "VARCHAR"),
        ("result_type", "VARCHAR"),
        ("loinc", "VARCHAR"),
        ("px", "VARCHAR"),
        ("ms_test_name", "VARCHAR"),
        ("ms_test_sub_category", "VARCHAR"),
        ("specimen_source", "VARCHAR"),
        ("ms_result_unit", "VARCHAR"),
        ("fast_ind", "VARCHAR"),
        ("pt_loc", "VARCHAR"),
    ),
}

# canonical SCDM table -> the SyntheticDataset field holding its rows
TABLE_ROWS: dict[str, str] = {
    "demographic": "patients",
    "death": "deaths",
    "enrollment": "enrollment",
    "dispensing": "dispensings",
    "diagnosis": "diagnoses",
    "procedure": "procedures",
    "encounter": "encounters",
    "lab_result": "labs",
}

TABLE_ORDER: tuple[str, ...] = (
    "demographic", "enrollment", "dispensing", "diagnosis", "death",
    "procedure", "encounter", "lab_result",
)


def rows_for_table(dataset: SyntheticDataset, table: str) -> tuple[dict[str, Any], ...]:
    """A table's rows as column-name -> value dicts, in schema order.

    THE serializer boundary: oracles read rows through this so they see
    exactly what was written, not a parallel hand-built copy.
    """
    rows: tuple[Any, ...] = getattr(dataset, TABLE_ROWS[table])
    return tuple({name: getattr(r, name) for name, _ in TABLE_COLUMNS[table]}
                 for r in rows)


def schema_sql(table: str) -> str:
    return ", ".join(f"{name} {typ}" for name, typ in TABLE_COLUMNS[table])


def write_parquet(dataset: SyntheticDataset, root: Path) -> Path:
    """Write the dataset as an SCDM parquet root: one folder per table,
    `data.parquet` inside, the layout gen_synthetic.py and
    `qrp.scdm.resolve_table` both expect. Every table is written, even
    when empty, so table resolution never needs fingerprinting."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        for table in TABLE_ORDER:
            (root / table).mkdir(parents=True, exist_ok=True)
            con.execute(f"CREATE OR REPLACE TABLE {table} ({schema_sql(table)})")
            rows = rows_for_table(dataset, table)
            if rows:
                app = con.appender(table)
                # column order matches TABLE_COLUMNS by construction
                app.append_rows([tuple(r.values()) for r in rows])
                app.flush()
                del app
            target = sql_str(str(root / table / "data.parquet"))
            con.execute(
                f"COPY (SELECT * FROM {table}) TO {target} "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            ).fetchall()
    finally:
        con.close()
    return root


# ---------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------

EXPOSURE_CODE_POOL = tuple(f"E{i:05d}" for i in range(1, 41))
EVENT_CODE_POOL = tuple(f"D{i:05d}" for i in range(1, 21))
NOISE_DRUG_POOL = tuple(f"N{i:05d}" for i in range(1, 21))
NOISE_DX_POOL = tuple(f"X{i:05d}" for i in range(1, 41))

_SUPPLIES = (7, 14, 30, 60, 90)
# SCDM 8.2.2 domains (demographic.md / encounter.md / diagnosis.md):
# Sex A/F/M/U; Race 0-5 plus M = Multi-racial; EncType AV/ED/IP/IS/OA.
# The pools match the conditionless-study defaults (qrp.config
# CohortConfig.sex / .race), so every drawn value passes cfg_demog
# eligibility; 'U'/'A' sex still collapses to 'O' for the covariate
# (10_normalize.sql). "IS" is appended last so existing seeds' draw
# indices are unchanged.
_SEXES = ("F", "M", "U", "A")
_RACES = ("0", "1", "2", "3", "4", "5", "M")
_ENCTYPES = ("AV", "OA", "IP", "ED", "IS")


def _pick(rng: random.Random, pool: Sequence[Any]) -> Any:
    return pool[rng.randrange(len(pool))]


@dataclass(frozen=True)
class GenerateOptions:
    """Knobs for dataset generation. Booleans default to True so ties
    and encounters are PRESENT unless a test asks them away — absent
    ties make tie-break tests vacuous, which is the failure mode this
    suite exists to avoid."""

    duplicate_demographics: bool = True
    same_supply_ties: bool = True
    include_deaths: bool = True
    include_encounters: bool = True
    include_labs: bool = False
    # Null the LAST patient row's birth date (SCDM-legal missing; the
    # pipeline drops the member — demographics_missing, attrition
    # level 1). Off by default: only the SCDM boundary tests ask for it.
    null_birth_dates: bool = False


def generate(
    patients: int = 3,
    seed: int = 0,
    *,
    start: date = date(2011, 1, 1),
    end: date = date(2015, 6, 30),
    options: GenerateOptions | None = None,
) -> SyntheticDataset:
    """Generate a deterministic synthetic extract.

    Constructive, not rejective: enrollment spans are built FIRST and
    claim dates are drawn from inside a span, so dispensings are always
    plausibly covered. Same (patients, seed, options) -> same dataset.

    Enrollment flags are uniform Y/Y/Y here; coverage-flag variety
    (SCDM MedCov Y/N/U/A x DrugCov Y/N/U pairs) is package B's surface
    (oracles/b_pkg_fixtures._FLAG_POOL). Spans assume
    `end - start >= 211` days: with a shorter custom window the drawn
    offsets can invert a span (enr_end < enr_start), which
    cdm_enrollment drops (10_normalize.sql WHERE enr_end >= enr_start).
    """
    opt = options or GenerateOptions()
    if patients < 1:
        raise ValueError("patients must be >= 1")
    rng = random.Random(seed)

    pts: list[Patient] = []
    spans: list[EnrollmentSpan] = []
    disp: list[Dispensing] = []
    dx: list[Diagnosis] = []
    encs: list[Encounter] = []
    dths: list[Death] = []
    window_days = max((end - start).days, 30)

    for patid in range(1, patients + 1):
        birth = date(1935, 1, 1) + timedelta(days=rng.randrange(14000))
        patient = Patient(
            patid=patid,
            birth_date=birth,
            sex=_pick(rng, _SEXES),
            race=_pick(rng, _RACES),
            hispanic=rng.choice(("Y", "N", "U")),
            postalcode=f"{rng.randrange(100000):05d}",
            postalcode_date=start,
        )
        pts.append(patient)
        # Enrollment covers most of the window; a patient occasionally
        # gets a late start so uncovered claims occur without a filter.
        span_start = start + timedelta(days=rng.choice((0, 0, 0, 60, 180)))
        span_end = end - timedelta(days=rng.choice((0, 0, 30)))
        spans.append(EnrollmentSpan(
            patid=patid, enr_start=span_start, enr_end=span_end,
            medcov="Y", drugcov="Y", chart="Y",
        ))

        # 1-4 dispensings, later ones strictly after the first, drawn
        # inside the span. ~60% exposure codes, rest noise.
        n_disp = 1 + rng.randrange(4)
        fill = span_start + timedelta(days=rng.randrange(max(
            (span_end - span_start).days, 1)))
        for _ in range(n_disp):
            code = _pick(rng, EXPOSURE_CODE_POOL) if rng.random() < 0.6 \
                else _pick(rng, NOISE_DRUG_POOL)
            supply = _pick(rng, _SUPPLIES)
            disp.append(Dispensing(
                patid=patid, rxdate=fill, rx=code, rx_codetype="ND",
                rxsup=supply, rxamt=1.0 + rng.random() * 59.0,
                providerid=1000 + patid,
            ))
            fill = min(fill + timedelta(days=30 + rng.randrange(370)),
                       span_end)

        # 1-3 diagnoses, ~40% outcome codes.
        for _ in range(1 + rng.randrange(3)):
            adate = span_start + timedelta(days=rng.randrange(max(
                (span_end - span_start).days, 1)))
            code = _pick(rng, EVENT_CODE_POOL) if rng.random() < 0.4 \
                else _pick(rng, NOISE_DX_POOL)
            # Pdx exists only on IP/IS claims (diagnosis.md: "Populate
            # only for IP and IS encounters; for ED, AV, and OA
            # encounter types, set as missing/blank").
            enctype = _pick(rng, _ENCTYPES)
            dx.append(Diagnosis(
                patid=patid, adate=adate, dx=code, dx_codetype="09",
                pdx=rng.choice(("P", "S")) if enctype in ("IP", "IS") else "",
                enctype=enctype,
            ))

        if opt.include_encounters and rng.random() < 0.5:
            adate = span_start + timedelta(days=rng.randrange(max(
                (span_end - span_start).days, 1)))
            kind = rng.random()
            if kind < 0.4:
                encs.append(Encounter(
                    patid=patid, adate=adate, enctype="IP",
                    encounterid=patid * 10,
                    ddate=adate + timedelta(days=rng.randrange(1, 6)),
                    discharge_status="",
                ))
            elif kind < 0.7:
                # IS stays are SCDM-legal only with a discharge date (or
                # the .S still-in-facility stand-in, null in Parquet) —
                # encounter.md DDate. IS days are NOT enveloping days:
                # 10_normalize.sql reads IP encounters only.
                encs.append(Encounter(
                    patid=patid, adate=adate, enctype="IS",
                    encounterid=patid * 10 + 2,
                    ddate=adate + timedelta(days=rng.randrange(1, 6)),
                    discharge_status="",
                ))
            else:
                encs.append(Encounter(
                    patid=patid, adate=adate, enctype="AV",
                    encounterid=patid * 10 + 1,
                ))

        if opt.include_deaths and rng.random() < 0.25:
            dths.append(Death(
                patid=patid,
                deathdt=span_start + timedelta(days=rng.randrange(window_days)),
            ))

    # Deliberate ties, mirroring gen_synthetic.py's injection pattern.
    if opt.duplicate_demographics:
        victim = pts[0]
        pts.append(Patient(
            patid=victim.patid,
            birth_date=victim.birth_date,
            # a genuine tie: same patid, same birth date, different sex
            sex="M" if victim.sex == "F" else "F",
            race=victim.race,
            hispanic=victim.hispanic,
            postalcode=victim.postalcode,
            postalcode_date=victim.postalcode_date,
        ))
    if opt.same_supply_ties and disp:
        donor = disp[0]
        disp.append(Dispensing(
            patid=donor.patid, rxdate=donor.rxdate, rx=donor.rx,
            rx_codetype=donor.rx_codetype, rxsup=donor.rxsup,
            rxamt=donor.rxamt + 2.5, providerid=donor.providerid,
        ))
    if opt.null_birth_dates and len(pts) > 1:
        # SCDM-legal missing birth date (demographic.md) on the last
        # patient row; at least one dated patient stays so index-age
        # stages remain exercisable. The pipeline drops the member
        # (demographics_missing / attrition level 1).
        pts[-1] = replace(pts[-1], birth_date=None)

    labs: tuple[LabResult, ...] = ()
    if opt.include_labs:
        # SCDM-legal result shapes (laboratory-result.md): 'N' carries a
        # numeric value with null text and a unit; 'C' carries enumerated
        # text with null numeric; 'U' is reserved for UNMAPPED rows,
        # which this generator never emits, so it is never drawn.
        # Numeric values draw ONE decimal: with the per-lab member counts
        # this suite generates (<= 5), a one-decimal mean never lands on
        # an exact .0005 boundary, so the recorded production DOUBLE-mean
        # summation-order limitation (FINAL-VALIDATION.md, D lab mean
        # rounding) cannot flake the oracle comparison. Two decimals
        # manufacture such boundaries (observed: mean 107.2975).
        labs = tuple(
            LabResult(
                patid=p.patid,
                lab_dt=start + timedelta(days=rng.randrange(window_days)),
                result_dt=None, order_dt=None,
                ms_result_n=round(rng.random() * 200, 1) if rt == "N" else None,
                ms_result_c=rng.choice(("POSITIVE", "NEGATIVE", "BORDERLINE",
                                        "UNDETERMINED")) if rt == "C" else None,
                result_type=rt,
                loinc="2160-0", px="PX1",
                ms_test_name="CREATININE", ms_test_sub_category="",
                specimen_source="SR_PLS",
                ms_result_unit="MG/DL" if rt == "N" else None,
                fast_ind="X", pt_loc="O",
            )
            for p in pts[: patients]  # one per patient, deterministic
            for rt in (rng.choice(("N", "C")),)
        )

    return SyntheticDataset(
        patients=tuple(pts),
        deaths=tuple(dths),
        enrollment=tuple(spans),
        dispensings=tuple(disp),
        diagnoses=tuple(dx),
        procedures=(),
        encounters=tuple(encs),
        labs=labs,
        exposure_codes=EXPOSURE_CODE_POOL,
        event_codes=EVENT_CODE_POOL,
        noise_drug_codes=NOISE_DRUG_POOL,
        noise_dx_codes=NOISE_DX_POOL,
        start=start,
        end=end,
    )


def hand_fixture() -> tuple[SyntheticDataset, StudyConfig]:
    """An explicit hand-calculated fixture with known expected results.

    Built constructively (no randomness) so every expectation in
    test_harness_stages.py is derivable by hand:

    * P1  enrolled, two E00001 fills 2014-03-10 / 2014-04-09 (the
          second continues the episode; it sits inside the first
          index's washout so only one episode results), two event
          claims in EACH cohort — D00002 on 2014-05-03 (enveloped
          to IP/'X' inside the stay) and D00001 on 2014-06-01 — of
          which exactly ONE attributes to the episode: the episode's
          supply runs out 2014-05-08, so first_event is D00002 on
          2014-05-03 and D00001 stays unattributed;
    * P2  enrolled but only a noise drug, dies 2015-01-01;
    * P3  an E00001 fill dated BEFORE enrollment starts — the claim
          enters exposure_claims (extraction is query-period-bounded)
          but no index date (index detection is enrollment-bounded);
    * P4  two same-day/same-supply E00021 fills with different amounts
          (the tie payload) -> one beta-cohort episode;
    * P5  DUPLICATE demographic rows — a deliberate violation of SCDM
          DEM's "Unique row: PatID" rule (non-canonical defect probe,
          like the module-level ties) -> present in demographics_multi
          and excluded from pov1/ptsmasterlist, which is what the
          pipeline does with ambiguous demographics.

    Study: cohort_a = {E00001}, cohort_b = {E00021}, events {D00001,
    D00002}, window 2011-01-01..2015-06-30, enr_days = wash = 183.
    """
    start = date(2011, 1, 1)
    end = date(2015, 6, 30)
    p1 = Patient(1, date(1980, 5, 1), "F", "1", "N", "02108", start)
    p2 = Patient(2, date(1955, 3, 3), "M", "2", "U", "10001", start)
    p3 = Patient(3, date(1970, 7, 7), "F", "0", "N", "94105", start)
    p4 = Patient(4, date(1975, 11, 20), "M", "4", "N", "60601", start)
    p5 = Patient(5, date(1975, 11, 20), "F", "4", "N", "60601", start)
    p5_tie = Patient(5, date(1975, 11, 20), "M", "4", "N", "60601", start)

    patients = (p1, p2, p3, p4, p5, p5_tie)
    enrollment = (
        EnrollmentSpan(1, date(2013, 1, 1), end, "Y", "Y", "Y"),
        EnrollmentSpan(2, date(2013, 1, 1), end, "Y", "Y", "Y"),
        # P3: enrollment starts AFTER their dispensing
        EnrollmentSpan(3, date(2014, 6, 1), end, "Y", "Y", "Y"),
        EnrollmentSpan(4, date(2013, 1, 1), end, "Y", "Y", "Y"),
        EnrollmentSpan(5, date(2013, 1, 1), end, "Y", "Y", "Y"),
    )
    dispensings = (
        Dispensing(1, date(2014, 3, 10), "E00001", "ND", 30, 10.0, 1001),
        Dispensing(1, date(2014, 4, 9), "E00001", "ND", 30, 12.0, 1001),
        Dispensing(2, date(2014, 2, 1), "N00001", "ND", 30, 5.0, 1002),
        # P3: inside the study window but outside their enrollment
        Dispensing(3, date(2014, 5, 1), "E00001", "ND", 30, 8.0, 1003),
        # P4: same day, same supply, different amounts — a real tie
        Dispensing(4, date(2014, 3, 15), "E00021", "ND", 30, 5.0, 1004),
        Dispensing(4, date(2014, 3, 15), "E00021", "ND", 30, 7.5, 1004),
        # P5: exposure that never becomes membership (dup demographics)
        Dispensing(5, date(2014, 3, 15), "E00021", "ND", 30, 9.0, 1005),
    )
    diagnoses = (
        # Deliberate out-of-SCDM-domain Pdx values: these AV claims carry
        # P/S to probe IP-stay enveloping (10_normalize.sql re-files
        # non-IP claims on IP days to enctype IP / pdx 'X'). The 5/3 row
        # sits inside P1's IP stay and is enveloped to IP/'X'; the 6/1
        # row is outside every stay and stays AV. Real SCDM AV claims
        # carry blank Pdx (diagnosis.md).
        Diagnosis(1, date(2014, 5, 3), "D00002", "09", "S", "AV"),
        Diagnosis(1, date(2014, 6, 1), "D00001", "09", "P", "AV"),
    )
    encounters = (
        Encounter(1, date(2014, 5, 1), "IP", 10,
                  ddate=date(2014, 5, 5), discharge_status=""),
    )
    deaths = (Death(2, date(2015, 1, 1)),)

    dataset = SyntheticDataset(
        patients=patients,
        deaths=deaths,
        enrollment=enrollment,
        dispensings=dispensings,
        diagnoses=diagnoses,
        procedures=(),
        encounters=encounters,
        labs=(),
        exposure_codes=("E00001", "E00021"),
        event_codes=("D00001", "D00002"),
        noise_drug_codes=("N00001",),
        noise_dx_codes=("X00001",),
        start=start,
        end=end,
    )
    study = build_study(
        exposure_codes=("E00001", "E00021"),
        event_codes=("D00001", "D00002"),
    )
    return dataset, study


# ---------------------------------------------------------------------
# StudyConfig builder — supported configurations only
# ---------------------------------------------------------------------

def _split_codes(codes: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split a code pool across two cohorts, as demo_type2 does
    (lisinopril E00001-20, beta_blocker E00021-40)."""
    if len(codes) < 2:
        return (tuple(codes), ())
    mid = ceil(len(codes) / 2)
    return tuple(codes[:mid]), tuple(codes[mid:])


def build_study(
    exposure_codes: Sequence[str],
    event_codes: Sequence[str] = (),
    *,
    start: date = date(2011, 1, 1),
    end: date = date(2015, 6, 30),
    coverage: str = "MD",
    enrol_gap: int = 0,
    wash_per: int = 183,
    episode_gap: int = 0,
    covariates: Sequence[Covariate] = (),
    inclusions: Sequence[InclusionRule] = (),
    risk_scores: Sequence[RiskScoreCode] = (),
    strata: Sequence[StratumLevel] = (),
    zipfile_rows: Sequence[tuple[str, str | None, str | None, str | None,
                                 float | None]] = (),
    utilization: Sequence[tuple[str, str, int, int]] = (),
    drug_classes: Sequence[tuple[str, str, str]] = (),
    lab_codes: Sequence[tuple[Any, ...]] = (),
    mfu: Sequence[tuple[Any, ...]] = (),
) -> StudyConfig:
    """Build a VALID StudyConfig over the given code pools.

    Two cohorts, each with half the codes, RX codetype "ND", DX events
    codetype "09" — the demo_type2 shape. Extra feature inputs are
    passed through unchanged; anything this builder does not model
    (dose limits, combos, unsupported table ids) is deliberately absent
    so every produced study validates and runs.
    """
    first, second = _split_codes(exposure_codes)

    def cohort(grp: str, codes: tuple[str, ...]) -> CohortConfig:
        return CohortConfig(
            cohortgrp=grp,
            coverage=coverage,
            enrol_gap=enrol_gap,
            wash_per=wash_per,
            episode_gap=episode_gap,
            exposure_codes=tuple(
                (c, "RX", "ND", None) for c in codes),
            event_codes=tuple((c, "DX", "09", None) for c in event_codes),
            # Loader-equivalent care settings. The study loader expands
            # EVERY event code's caresettingprincipal, and an absent one
            # expands to the all-settings wildcard CARE_SETTING_ALL
            # (qrp.config.parse_care_setting on an empty value). The
            # event_claims stage INNER JOINs cfg_care_setting on
            # (cohortgrp, code), so a cohort whose event codes carry no
            # care-setting row silently loses every outcome claim — the
            # vacuity this emitted the hard way until packages C/D/F each
            # re-added the expansion by hand. One row per code here makes
            # a builder study carry exactly what loading the same codes
            # from an input file would.
            event_care_settings=tuple(
                (c, *CARE_SETTING_ALL) for c in event_codes),
        )

    cohorts = [cohort("cohort_a", first)]
    if second:
        cohorts.append(cohort("cohort_b", second))

    study = StudyConfig(
        study_type=2,
        start_date=start,
        end_date=end,
        cohorts=tuple(cohorts),
        covariates=tuple(covariates),
        inclusions=tuple(inclusions),
        risk_scores=tuple(risk_scores),
        strata=tuple(strata),
        zipfile=tuple(zipfile_rows),
        utilization=tuple(utilization),
        drug_classes=tuple(drug_classes),
        lab_codes=tuple(lab_codes),
        mfu=tuple(mfu),
    )
    study.validate()
    return study


# Convenience: a StudyConfig over a dataset's own code vocabulary.
def study_for(dataset: SyntheticDataset, **kwargs: Any) -> StudyConfig:
    return build_study(dataset.exposure_codes, dataset.event_codes, **kwargs)


__all__ = [
    "TABLE_COLUMNS",
    "TABLE_ORDER",
    "TABLE_ROWS",
    "Death",
    "Diagnosis",
    "Dispensing",
    "Encounter",
    "EnrollmentSpan",
    "GenerateOptions",
    "LabResult",
    "Patient",
    "Procedure",
    "SyntheticDataset",
    "build_study",
    "generate",
    "hand_fixture",
    "rows_for_table",
    "schema_sql",
    "study_for",
    "write_parquet",
]
