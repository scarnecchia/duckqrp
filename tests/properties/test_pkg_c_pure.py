# pattern: Functional Core
"""Independent day enumeration checks for the package C models."""
from datetime import timedelta

from hypothesis import event, given
from hypothesis import strategies as st

from .oracles.c_glue import GRID_ANCHOR
from .oracles.c_models import Episode, FollowUpConfig, episode_outcome, overlap_days


@given(length=st.integers(1, 120), offset=st.integers(0, 125))
def test_person_days_matches_enumerated_at_risk_days(length: int, offset: int) -> None:
    end = GRID_ANCHOR + timedelta(days=length - 1)
    start = GRID_ANCHOR + timedelta(days=offset)
    result = episode_outcome(
        Episode("cohort_a", 1, GRID_ANCHOR, end),
        enr_end=end, deathdt=None, atriskindexdt=start,
        events=(), ioc_dates=(),
        cfg=FollowUpConfig(0, 0, offset, end, end),
        ie_rules=None, inc_claims=(), strengths={},
    )
    expected = sum(GRID_ANCHOR + timedelta(days=i) >= start for i in range(length))
    assert result.person_days == expected
    event("negative pre-floor span" if offset > length else "nonnegative pre-floor span")


@given(a=st.integers(-30, 30), b=st.integers(-30, 30),
       n=st.integers(1, 30), m=st.integers(1, 30))
def test_overlap_matches_day_sets(a: int, b: int, n: int, m: int) -> None:
    expected = len(set(range(a, a + n)) & set(range(b, b + m)))
    actual = overlap_days(
        GRID_ANCHOR + timedelta(days=a), GRID_ANCHOR + timedelta(days=a+n-1),
        GRID_ANCHOR + timedelta(days=b), GRID_ANCHOR + timedelta(days=b+m-1),
    )
    assert actual == expected
    event("coincident starts" if a == b else "different starts")
