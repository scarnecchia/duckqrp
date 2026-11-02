"""Package B: index dates and POV1 match an independent prior-claim scan.

Contract under test (40_index.sql + 45_pov1.sql): an index candidate is
a stockpiled record starting inside one of the patient's enrollment
spans; per patient-day the longest supply wins; a candidate survives the
washout when no PRIOR claim's supply reaches into the wash_per days
before it (wash_per NULL: only the first ever claim; wash_per 0: every
claim). POV1 then applies demographics (duplicate-demographic patids are
excluded entirely), the configured sex/race/hispanic values, enrollment
containment of the index date, and the age stratum, and reports the SAS
yrdif age and the death date.

Oracle: tests/properties/oracles/b_stockpile_model.py — a sequential
scan over one sorted list with a running prior-supply maximum, plus a
from-scratch demographic/age model. The shipped SQL uses a window
running maximum; the derivations differ.
"""

from datetime import timedelta

from hypothesis import event, example, given, settings

from .conftest import expensive_settings
from .harness import StageHarness, fetch_rows
from .oracles.b_pkg_fixtures import (
    START,
    assert_rows_match,
    disp,
    expand_spec,
    mini_dataset,
    patient,
    span,
    spec_strategy,
    two_cohort_study,
)
from .oracles.b_stockpile_model import POV1_COLS, model_pov1, model_stockpiled


def _spec(**over: object) -> dict:
    """A spec dict matching spec_strategy's shape, for @example cases."""
    base: dict = {
        "enrol_gap": 0,
        "wash_per": 183,
        "coverage": "MD",
        "gap_b_differs": False,
        "patients": [{
            "spans": [("adjacent", 100, "YY")],
            "fills": [{"code": "E00001", "day": 10, "sup": 7, "amt": 1.0,
                       "twin": False, "twin_sup_delta": 0,
                       "twin_amt_epsilon": 0.0}],
            "death_day": None,
        }],
        "dup_demog": False,
        "leap_birth": None,
    }
    base.update(over)
    return base


@example(_spec(patients=[
    # death BEFORE, INSIDE and AFTER candidate index/episode windows,
    # one patient each, wash 0 so every fill is an index date
    {
        "spans": [("adjacent", 400, "YY")],
        "fills": [{"code": "E00001", "day": 50, "sup": 7, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": 30,
    },
    {
        "spans": [("adjacent", 400, "YY")],
        "fills": [{"code": "E00001", "day": 50, "sup": 7, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": 53,
    },
    {
        "spans": [("adjacent", 400, "YY")],
        "fills": [{"code": "E00001", "day": 50, "sup": 7, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": 90,
    },
], wash_per=0))
@example(_spec(  # duplicate demographics: excluded from POV1 entirely
    dup_demog=True,
    patients=[{
        "spans": [("adjacent", 100, "YY")],
        "fills": [{"code": "E00001", "day": 10, "sup": 7, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    }],
    wash_per=0,
))
@example(_spec(  # leap-day birthday, index date ON 2012-02-29 (day 422)
    leap_birth="2000-02-29",
    patients=[{
        "spans": [("adjacent", 500, "YY")],
        "fills": [{"code": "E00001", "day": 422, "sup": 7, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    }],
    wash_per=0,
))
@example(_spec(  # washout NULL: only the very first claim is an index
    wash_per=None,
    patients=[{
        "spans": [("adjacent", 400, "YY")],
        "fills": [
            {"code": "E00001", "day": 10, "sup": 7, "amt": 1.0,
             "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
            {"code": "E00001", "day": 200, "sup": 7, "amt": 1.0,
             "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
        ],
        "death_day": None,
    }],
))
@example(_spec(  # a claim dated BEFORE enrollment starts never indexes
    patients=[{
        "spans": [("adjacent", 50, "YY")],
        "fills": [{"code": "E00001", "day": 0, "sup": 7, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    }],
))
@settings(expensive_settings())
@given(spec_strategy)
def test_index_matches_prior_claim_scan(spec: dict) -> None:
    dataset, study = expand_spec(spec)
    deaths = sum(1 for p in spec["patients"] if p["death_day"] is not None)
    event(f"patients with deaths={deaths}")
    event(f"wash_per={spec['wash_per']}")
    if spec["dup_demog"]:
        event("duplicate-demographics case exercised")
    if spec["leap_birth"]:
        event("leap-day birthday case exercised")

    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("pov1")
        sql = fetch_rows(harness.engine, "pov1")
    model = model_pov1(dataset, study, model_stockpiled(dataset, study))
    event(f"nonempty pov1={bool(sql)}")
    assert_rows_match(sql, model, POV1_COLS, "pov1")


# ---------------------------------------------------------------------
# Hand-calculated washout boundary: a prior supply ending exactly
# wash_per days before a candidate blocks it; one day earlier does not.
# ---------------------------------------------------------------------

def test_index_washout_boundary_is_exact() -> None:
    study = two_cohort_study(wash_per=30)
    dataset = mini_dataset(
        patients=(patient(1), patient(2)),
        enrollment=(
            span(1, START, START + timedelta(days=200)),
            span(2, START, START + timedelta(days=200)),
        ),
        dispensings=(
            # Prior supply ends on day 11 (fill day 5 + 7 days).
            # P1: second fill day 41 -> blank gap 29 < wash 30 -> blocked
            disp(1, START + timedelta(days=5), "E00001", 7, 1.0),
            disp(1, START + timedelta(days=41), "E00001", 7, 1.0),
            # P2: second fill day 42 -> blank gap 30 == wash 30 -> kept
            disp(2, START + timedelta(days=5), "E00001", 7, 1.0),
            disp(2, START + timedelta(days=42), "E00001", 7, 1.0),
        ),
    )
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("pov1")
        sql = fetch_rows(harness.engine, "pov1")
    dates = {patid: sorted(r[2] for r in sql if r[1] == patid)
             for patid in {r[1] for r in sql}}
    assert dates == {
        1: [START + timedelta(days=5)],                       # blocked
        2: [START + timedelta(days=5), START + timedelta(days=42)],  # kept
    }, dates
    model = model_pov1(dataset, study, model_stockpiled(dataset, study))
    assert_rows_match(sql, model, POV1_COLS, "pov1")
