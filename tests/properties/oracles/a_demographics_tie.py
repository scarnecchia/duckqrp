# pattern: Functional Core
"""The demographics full-tie experiment (investigation lead).

Contract background (pinned by infra): ``10_normalize.sql`` keeps one
demographic row per patid via

    QUALIFY row_number() OVER (PARTITION BY patid
        ORDER BY birth_date, sex, race, hispanic, postalcode) = 1

Two duplicate rows that AGREE on all five of those columns leave the
choice between them undefined. This module builds the minimal fixtures
and runs the SHIPPED SQL through the production paths, recording what
actually happens. It deliberately asserts nothing about WHICH row
survives in the undefined cases — that would invent a spec. It does
assert the documented contracts: one row per patid survives, the patid
lands in ``demographics_multi``, and (per the pinned infra contract)
``pov1`` excludes it entirely.

Fixtures
--------
* ``D_DIFFER_ZIP``  — the two rows differ in ``postalcode``, which IS
  in the order: the documented total order must decide (zip 60601 wins,
  the smaller string).
* ``D_DIFFER_PCD``  — the rows differ ONLY in ``postalcode_date``,
  which is NOT in the order: survivor undefined.
* ``D_IDENTICAL``   — the rows are equal in every column: survivor
  trivially value-identical.

Each fixture runs under: both insert orders, threads 1 and 4, and two
entry paths (registered fixture tables through ``StageHarness``, and
parquet files through the real ``qrp.run``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..synth import (
    Diagnosis,
    Dispensing,
    EnrollmentSpan,
    Patient,
    SyntheticDataset,
    build_study,
)


@dataclass(frozen=True)
class TieFixture:
    name: str
    row_a: Patient
    row_b: Patient
    differ_in_order_key: bool     # does ANY order column separate them?


def _patient(patid: int, sex: str, zip_: str, pcd: date) -> Patient:
    return Patient(patid=patid, birth_date=date(1980, 5, 1), sex=sex,
                   race="4", hispanic="N", postalcode=zip_,
                   postalcode_date=pcd)


def _fixture(
    name: str, row_a: Patient, row_b: Patient,
    differ_in_order_key: bool,
) -> TieFixture:
    return TieFixture(name, row_a, row_b, differ_in_order_key)


def fixture_differ_zip() -> TieFixture:
    return _fixture(
        "D_DIFFER_ZIP",
        _patient(7, "F", "60601", date(2011, 1, 1)),
        _patient(7, "F", "60602", date(2011, 1, 1)),
        differ_in_order_key=True,
    )


def fixture_differ_pcd() -> TieFixture:
    return _fixture(
        "D_DIFFER_PCD",
        _patient(7, "F", "60601", date(2011, 1, 1)),
        _patient(7, "F", "60601", date(2012, 2, 29)),
        differ_in_order_key=False,
    )


def fixture_identical() -> TieFixture:
    return _fixture(
        "D_IDENTICAL",
        _patient(7, "F", "60601", date(2011, 1, 1)),
        _patient(7, "F", "60601", date(2011, 1, 1)),
        differ_in_order_key=False,
    )


def dataset_for(fixture: TieFixture, order: str) -> SyntheticDataset:
    """A minimal extract: the tie pair in the requested order, plus one
    clean control patient with an index-producing fill."""
    rows = [fixture.row_a, fixture.row_b]
    if order == "BA":
        rows.reverse()
    patients = tuple(rows) + (
        _patient(8, "M", "60605", date(2011, 1, 1)),
    )
    span = EnrollmentSpan(7, date(2013, 1, 1), date(2015, 6, 30),
                          "Y", "Y", "Y")
    span8 = EnrollmentSpan(8, date(2013, 1, 1), date(2015, 6, 30),
                           "Y", "Y", "Y")
    disp7 = Dispensing(7, date(2014, 3, 10), "E00001", "ND", 30, 10.0,
                       1007)
    disp8 = Dispensing(8, date(2014, 3, 10), "E00001", "ND", 30, 10.0,
                       1008)
    return SyntheticDataset(
        patients=patients,
        deaths=(),
        enrollment=(span, span8),
        dispensings=(disp7, disp8),
        diagnoses=(Diagnosis(8, date(2014, 4, 1), "D00001", "09", "P", "AV"),),
        procedures=(),
        encounters=(),
        labs=(),
        exposure_codes=("E00001",),
        event_codes=("D00001",),
        noise_drug_codes=(),
        noise_dx_codes=(),
        start=date(2011, 1, 1),
        end=date(2015, 6, 30),
    )


def study_for_fixture():
    return build_study(("E00001",), ("D00001",))


def demographics_survivor(engine, patid: int = 7) -> tuple | None:
    rows = engine.con.execute(
        "SELECT * FROM demographics WHERE patid = ?", [patid]
    ).fetchall()
    return rows[0] if rows else None


def experiment_record(engine, fixture: TieFixture, order: str,
                      writer: str, threads: int) -> dict:
    """Observation only — the survivor's identifying values plus the
    documented contract checks' raw outcomes."""
    surv = demographics_survivor(engine)
    multi = {r[0] for r in engine.con.execute(
        "SELECT patid FROM demographics_multi").fetchall()}
    pov1 = {r[0] for r in engine.con.execute(
        "SELECT patid FROM pov1").fetchall()}
    return {
        "fixture": fixture.name,
        "order": order,
        "writer": writer,
        "threads": threads,
        "survivor": surv,
        "in_multi": 7 in multi,
        "pov1_has_7": 7 in pov1,
        "pov1_has_8": 8 in pov1,
    }


def format_evidence(records: list[dict]) -> str:
    lines = [
        "demographics full-tie experiment — observed behavior of the",
        "shipped 10_normalize.sql QUALIFY (row_number OVER (PARTITION BY",
        "patid ORDER BY birth_date, sex, race, hispanic, postalcode)).",
        "",
        "D_DIFFER_ZIP separates in an ORDER-BY column: the documented",
        "total order must and does decide. D_DIFFER_PCD and D_IDENTICAL",
        "agree on ALL five order columns: the shipped SQL leaves the",
        "choice undefined; the survivor values below are OBSERVED, not",
        "contracted.",
        "",
        f"{'fixture':<14}{'order':<7}{'writer':<10}{'thr':>4}  "
        f"survivor (patid, birth, sex_raw, sex, race, hisp, zip, zipdt)",
        "-" * 110,
    ]
    for r in records:
        lines.append(
            f"{r['fixture']:<14}{r['order']:<7}{r['writer']:<10}"
            f"{r['threads']:>4}  {r['survivor']}"
        )
    lines.append("")
    lines.append("contract checks (documented, asserted in the test):")
    bad = [r for r in records
           if not r["in_multi"] or r["pov1_has_7"] or not r["pov1_has_8"]]
    lines.append(
        f"  all cases: one demographics row per patid, patid in "
        f"demographics_multi, pov1 excludes patid 7 and includes "
        f"control 8: {'ALL OK' if not bad else f'VIOLATED in {bad}'}"
    )
    return "\n".join(lines) + "\n"
