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
# Known divergence (lead B1): overlapping raw spans whose inner boundary
# ruptures mid-episode. The day-model merges the outer span whole; the
# shipped SQL breaks against the immediately PREVIOUS span (as the SAS
# parity notes document), emitting episodes that OVERLAP each other.
# Everything downstream then fans out per containing span (lead B2: a
# single dispensing is stockpiled twice). xfail(strict) so a future fix
# fails this test and prompts its removal.
# ---------------------------------------------------------------------

@pytest.mark.xfail(
    reason="lead B1/B2: overlapping raw spans yield overlapping "
           "enrollment episodes and a stockpiled double-count; see "
           "investigation/packages/B.md",
    strict=True)
def test_enrollment_overlapping_raw_spans_known_divergence() -> None:
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
    model_rows = _model_rows(dataset, study)
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("enrollment_spans")
        sql = fetch_rows(harness.engine, "enrollment_spans")
    # Day-model: ONE episode (2011-01-01 .. 2011-12-31) — C's start sits
    # inside A's end, so the accumulator merges it; SQL instead breaks C
    # from B (77 blank days from B's end) and emits a second, nested
    # episode.
    assert len(sql) == 1, (
        f"overlapping raw spans produced {len(sql)} episodes: {sql}; "
        f"day-model: {model_rows}")
    assert model_rows == [
        ("MD|0|0", 1, 1, date(2011, 1, 1), date(2011, 12, 31))]


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
