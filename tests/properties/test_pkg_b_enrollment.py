"""Package B: enrollment episodes match an independent day-model oracle.

Contract under test (20_enrollment.sql, docs/SAS_PARITY.md "Enrollment
spans"): continuous enrollment episodes — spans separated by at most
`enrol_gap` blank days merge, coverage-flag changes rupture, a
chart-required configuration drops patients with any non-chart span
overlapping the study window, and spans failing the coverage mode never
qualify.

Oracle: tests/properties/oracles/b_enrollment_model.py — a sorted
interval scan with a running episode accumulator. The known divergence
on overlapping raw spans (lag-break vs episode-accumulator derivations)
is documented separately in
test_enrollment_overlapping_raw_spans_known_divergence.
"""

from datetime import date, timedelta

import pytest
from hypothesis import event, example, given, settings

from qrp.config import StudyConfig

from .synth import SyntheticDataset
from .conftest import expensive_settings
from .harness import StageHarness, fetch_rows
from .oracles.b_enrollment_model import enrollment_episodes
from .oracles.b_pkg_fixtures import (
    START,
    assert_rows_match,
    disp,
    expand_spec,
    mini_dataset,
    patient,
    spec_strategy,
    span,
    two_cohort_study,
)

ENROLLMENT_COLS = ("enr_cfg_id", "patid", "episode", "enr_start", "enr_end")


def _spec(**over: object) -> dict:
    """A spec dict matching spec_strategy's shape, for @example cases."""
    base: dict = {
        "enrol_gap": 1,
        "wash_per": 183,
        "coverage": "MD",
        "gap_b_differs": False,
        "patients": [{
            "spans": [("adjacent", 10, "YY")],
            "fills": [{"code": "E00001", "day": 6, "sup": 7, "amt": 1.0,
                       "twin": False, "twin_sup_delta": 0,
                       "twin_amt_epsilon": 0.0}],
            "death_day": None,
        }],
        "dup_demog": False,
        "leap_birth": None,
    }
    base.update(over)
    return base


def _model_rows(dataset: SyntheticDataset, study: StudyConfig) -> list[tuple]:
    out: list[tuple] = []
    for (cfg, patid), episodes in enrollment_episodes(dataset, study).items():
        for i, ep in enumerate(episodes, start=1):
            out.append((cfg, patid, i, ep.enr_start, ep.enr_end))
    return out


@example(_spec(patients=[
    # gap threshold, all three sides in ONE run: pair 1 merges with one
    # blank day less than the threshold, pair 2 exactly at it, and the
    # chain between patients 2 and 3 covers the one-day-over rupture.
    {
        "spans": [("adjacent", 10, "YY"), ("gap_minus", 10, "YY")],
        "fills": [{"code": "E00001", "day": 8, "sup": 3, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    },
    {
        "spans": [("adjacent", 10, "YY"), ("gap_exact", 10, "YY"),
                  ("gap_plus", 10, "YY")],
        "fills": [{"code": "E00001", "day": 8, "sup": 3, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    },
]))
@example(_spec(  # coverage-flag rupture and the A-flag boundary
    coverage="MD",
    patients=[{
        "spans": [("adjacent", 5, "YY"), ("adjacent", 5, "AY"),
                  ("gap_exact", 5, "NY")],
        "fills": [{"code": "E00001", "day": 8, "sup": 3, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    }],
))
@example(_spec(  # drug-only coverage: medcov-only spans never qualify
    coverage="D",
    patients=[{
        "spans": [("adjacent", 30, "YN"), ("adjacent", 30, "YY")],
        "fills": [{"code": "E00001", "day": 20, "sup": 7, "amt": 2.5,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    }],
))
@example(_spec(  # nested spans merge into one episode
    patients=[{
        "spans": [("adjacent", 40, "YY"), ("overlap", 5, "YY")],
        "fills": [{"code": "E00002", "day": 20, "sup": 7, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    }],
))
@example(_spec(  # leap February inside an enrollment window
    patients=[{
        "spans": [("adjacent", 200, "YY")],
        "fills": [{"code": "E00001", "day": 420, "sup": 7, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    }],
    leap_birth="2000-02-29",
))
@settings(expensive_settings())
@given(spec_strategy)
def test_enrollment_matches_day_model(spec: dict) -> None:
    dataset, study = expand_spec(spec)
    shapes = [s[0] for p in spec["patients"] for s in p["spans"]]
    event(f"enrol_gap={spec['enrol_gap']}")
    event(f"coverage={spec['coverage']}")
    event(f"wash_per={spec['wash_per']}")
    if "gap_exact" in shapes:
        event("gap-threshold boundary exercised")
    if "overlap" in shapes:
        event("overlapping raw spans present")
    flags = [s[2] for p in spec["patients"] for s in p["spans"]]
    if len(set(flags)) > 1:
        event("coverage-flag variety present")

    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("enrollment_spans")
        sql = fetch_rows(harness.engine, "enrollment_spans")
    event(f"nonempty output={bool(sql)}")
    assert_rows_match(sql, _model_rows(dataset, study), ENROLLMENT_COLS,
                      "enrollment_spans")


# ---------------------------------------------------------------------
# Overlapping raw spans (findings B1/B2 — RESOLVED as SAS parity).
# Verified against the original SAS source (read-only reference tree):
#
#   * ms_episoderec2.sas:67-77 breaks each span against the immediately
#     PREVIOUS span (`&ENRSTART.-lag(&ENREND.)-1 > &ENROLGAP.`) and
#     :79-97 reconciles min/max per (PatId, episode) — overlapping
#     episodes SURVIVE into the shave reference (worktemp.enr_&num),
#     with no overlap dedup anywhere.
#   * ms_shaveoutside (macro body, `proc sql` cartesian join
#     `ref.Enr_Start <= claim_end and claim_start <= ref.Enr_End`)
#     fans the claim out per overlapping span and shaves EACH pair.
#   * ms_cidanum.sas:1299-1308 runs that pre-shave, ms_stockpiling
#     then same-day-sums the fanned rows (ms_stockpiling.sas proc
#     means, `N(&CLMSUP.)=NumDispensing`), and ms_cidanum.sas:1531-1541
#     re-shaves through the same fanning join.
#
# So SAS itself, on the input below, produces TWO stockpiled records
# for the one dispensing — supplies 16 and 46, each numdispensing=2 —
# exactly what the shipped SQL does. The day-model accumulator (one
# merged episode, one count) is a sane-arithmetic reading, not the
# project's SAS-parity contract; the divergence is real but it is a
# divergence from the SANE model, not from SAS. Pinning the SAS-parity
# semantics here (was xfail(strict) before that reading was settled).
# ---------------------------------------------------------------------

def test_enrollment_overlapping_raw_spans_sas_parity() -> None:
    dataset = mini_dataset(
        patients=(patient(1),),
        enrollment=(
            span(1, date(2011, 1, 1), date(2011, 12, 31)),
            span(1, date(2011, 2, 15), date(2011, 6, 15)),
            span(1, date(2011, 9, 1), date(2011, 9, 30)),
        ),
        dispensings=(disp(1, date(2011, 9, 15), "E00001", 30, 10.0),),
    )
    study = two_cohort_study()
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("episodes + masterlist")
        sql = fetch_rows(harness.engine, "enrollment_spans")
        stock = fetch_rows(harness.engine, "stockpiled")
        cand = fetch_rows(harness.engine, "index_candidates")
    # C ruptures from B (77 blank days) while sitting inside A, so the
    # two episodes overlap — same as SAS's reconciled output.
    assert sorted((r[3], r[4]) for r in sql) == [
        (date(2011, 1, 1), date(2011, 12, 31)),
        (date(2011, 9, 1), date(2011, 9, 30)),
    ]
    # The fill lands in both episodes; each shaved copy stockpiles, and
    # the same-day sum (sup 16+30) pushes one record out to 46 days.
    # numdispensing=2 on BOTH rows is SAS's own `N(RxSup)` count of the
    # two fanned copies of one physical claim.
    assert len(stock) == 2
    by_sup = {r[6]: r for r in stock}
    assert sorted(by_sup) == [16, 46]
    short, long = by_sup[16], by_sup[46]
    assert (short[3], short[4], short[8]) == (
        date(2011, 9, 15), date(2011, 9, 30), 2), f"short row: {short}"
    assert short[7] == pytest.approx(10.0 * 16 / 30), (
        f"short amount not prorated: {short}")
    assert (long[3], long[4], long[8]) == (
        date(2011, 9, 15), date(2011, 10, 30), 2), f"long row: {long}"
    # the pipeline sums the two prorated copies (16/30 + 30/30 of the
    # amount) before the re-shave prorates again, so compare loosely
    assert long[7] == pytest.approx(10.0 * 46 / 30), (
        f"long amount not prorated: {long}")
    # The inflated payload reaches the index candidates (longest supply
    # wins the per-day dedupe), again matching SAS's washout input.
    assert len(cand) == 1
    assert (cand[0][5], cand[0][7]) == (46, 2), (
        f"index_candidates row: {cand}")


# ---------------------------------------------------------------------
# Chart-required configurations
# ---------------------------------------------------------------------

def test_enrollment_chart_required_drops_nonchart_patients() -> None:
    """chart_required=Y drops any patient with a non-chart span
    overlapping the window — entirely, for that configuration."""
    dataset = mini_dataset(
        patients=(patient(1), patient(2)),
        enrollment=(
            span(1, START, START + timedelta(days=60), chart="N"),
            span(2, START, START + timedelta(days=60), chart="Y"),
        ),
        dispensings=(
            disp(1, START + timedelta(days=10), "E00001", 7, 1.0),
            disp(2, START + timedelta(days=10), "E00001", 7, 1.0),
        ),
    )
    study = two_cohort_study(cohort_overrides={"chart_required": True})
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("enrollment_spans")
        sql = fetch_rows(harness.engine, "enrollment_spans")
    patids = {r[1] for r in sql}
    assert patids == {2}, sql
    model = enrollment_episodes(dataset, study)
    assert {patid for (_cfg, patid) in model} == {2}
