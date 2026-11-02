"""Package B pass 2: exact checks on the supported nonoverlap domain.

FINAL-AUDIT §4 resolution (policy-free): uniform-flag, nonoverlapping
enrollment lies INSIDE the supported contract, so the shipped SQL must
match an independent day-set oracle EXACTLY there — exact assertions on
exact semantics, no weakening. The oracle
(`oracles/b_day_sets.py`) recovers episodes as gap-filled connected
components over the occupied-day set and shares no mechanism with
20_enrollment.sql's lag/window formulation or with the lag-segmentation
model in `oracles/b_enrollment_model.py`.

`test_nonoverlap_enrollment_exhaustive_day_sets` pins the lag model to
that independent oracle over the full 128-mask x 3-gap domain (384
cases); its SQL twin runs the same 384 cases through the shipped
20_enrollment.sql. Overlap fan-out is characterized with order-agnostic
invariants in test_pkg_b_independent_review.py, not here.
"""

from datetime import timedelta

from hypothesis import event, given, settings
from hypothesis import strategies as st

from .conftest import expensive_settings
from .harness import StageHarness, fetch_rows
from .oracles.b_day_sets import connected_day_bounds
from .oracles.b_enrollment_model import enrollment_episodes
from .oracles.b_pkg_fixtures import (
    START,
    mini_dataset,
    patient,
    span,
    two_cohort_study,
)


@settings(expensive_settings())
@given(st.sets(st.integers(0, 30), max_size=20), st.integers(0, 4))
def test_nonoverlap_enrollment_matches_occupied_days(days: set[int], gap: int) -> None:
    event(f"empty={not days}")
    event(f"gap={gap}")
    dataset = mini_dataset(
        patients=(patient(1),),
        enrollment=tuple(span(1, START + timedelta(days=d), START + timedelta(days=d))
                         for d in sorted(days)),
    )
    study = two_cohort_study(cohort_overrides={"enrol_gap": gap})
    expected = [(START + timedelta(days=a), START + timedelta(days=b))
                for a, b in connected_day_bounds(frozenset(days), gap)]
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("enrollment_spans")
        actual = fetch_rows(harness.engine, "enrollment_spans")
    event(f"nonempty output={bool(expected)}")
    assert sorted((r[3], r[4]) for r in actual) == expected


def test_nonoverlap_enrollment_exhaustive_day_sets() -> None:
    """All 128 seven-day occupancy masks x gaps 0-2: the lag model must
    equal the independent day-set oracle everywhere in the domain."""
    for mask in range(128):
        days = frozenset(d for d in range(7) if mask & (1 << d))
        dataset = mini_dataset(
            patients=(patient(1),),
            enrollment=tuple(span(1, START + timedelta(days=d), START + timedelta(days=d))
                             for d in sorted(days)),
        )
        for gap in range(3):
            study = two_cohort_study(cohort_overrides={"enrol_gap": gap})
            actual = enrollment_episodes(dataset, study)
            bounds = [(ep.enr_start, ep.enr_end) for eps in actual.values() for ep in eps]
            expected = [(START + timedelta(days=a), START + timedelta(days=b))
                        for a, b in connected_day_bounds(days, gap)]
            assert bounds == expected, (mask, gap)


def test_nonoverlap_enrollment_exhaustive_day_sets_sql() -> None:
    """The same 384 exhaustive cases through the SHIPPED 20_enrollment.sql.

    Bounded and cheap (single-day spans, enrollment stage only): this is
    the exact supported-domain evidence the exact assertions rest on —
    SQL vs the independent day-set oracle, no model in between."""
    for mask in range(128):
        days = frozenset(d for d in range(7) if mask & (1 << d))
        dataset = mini_dataset(
            patients=(patient(1),),
            enrollment=tuple(span(1, START + timedelta(days=d), START + timedelta(days=d))
                             for d in sorted(days)),
        )
        expected = [(START + timedelta(days=a), START + timedelta(days=b))
                    for a, b in connected_day_bounds(days, 1)]
        study = two_cohort_study(cohort_overrides={"enrol_gap": 1})
        with StageHarness(study) as harness:
            harness.attach_dataset(dataset)
            harness.run_through("enrollment_spans")
            actual = fetch_rows(harness.engine, "enrollment_spans")
        assert sorted((r[3], r[4]) for r in actual) == expected, mask
