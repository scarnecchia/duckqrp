# pattern: Functional Core
"""Independent day-set oracle for the supported enrollment domain.

Domain restriction (FINAL-AUDIT §4): uniform coverage flags,
nonoverlapping SINGLE-DAY spans. Inside that domain the enrollment
contract is unambiguous — spans separated by at most `enrol_gap` blank
days merge into one episode — so the shipped SQL must match this oracle
EXACTLY (see test_pkg_b_pass2.py).

Independence: episodes are recovered as connected components over the
occupied-day set after filling holes up to `enrol_gap`. This shares no
mechanism with 20_enrollment.sql (lag break + running-sum island
numbering) or with b_enrollment_model.py's lag segmentation: it is a
set operation over days, derived only from the documented merge rule.
"""


def connected_day_bounds(days: frozenset[int], gap: int) -> list[tuple[int, int]]:
    """Fill bounded holes, then recover connected components by day adjacency."""
    assert gap >= 0
    filled = set(days)
    if not days:
        return []
    for day in range(min(days), max(days) + 1):
        if day in days:
            continue
        left = [d for d in days if d < day]
        right = [d for d in days if d > day]
        if left and right and min(right) - max(left) - 1 <= gap:
            filled.add(day)
    starts = sorted(d for d in filled if d - 1 not in filled)
    ends = sorted(d for d in filled if d + 1 not in filled)
    return list(zip(starts, ends, strict=True))
