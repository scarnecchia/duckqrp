"""Package C -- dose restrictions (42_dose.sql over _claim_dose_view.sql)
vs the dose model.

Contract under test (the stage SQL's header + config docs):

* claim_dose: one row per exposure dispensing; cumdose = strength x
  amount; cfdd = strength x amount / days-of-supply (NULL at supply 0);
  claims whose code has no strength entry are dropped by the inner
  join;
* prior cumulative dose sums cumdose over dispensings whose FILL DATE
  lies in [orig_adate - cum_dose_per, orig_adate - 1] -- a closed
  RANGE frame ending the day BEFORE the candidate; the candidate's own
  day never counts;
* comparisons are round-to-integer, half away from zero (SAS's
  round(x, 1) unit semantics), inclusive bounds: excluded iff
  round(prior) < min_cum_dose or round(prior) > max_cum_dose, or any
  same-day claim's rounded cfdd is outside [min_cfdd, max_cfdd] (NULL
  cfdd fails either bound; no same-day claim row at all counts as
  NULL cfdd);
* exclusions remove the candidate index date, keyed by the (possibly
  stockpile-shifted) adate.

Oracles: `oracles/c_models.dose_verdict` re-derives every decision
with plain date arithmetic over the snapshot inputs (exposure_claims
and the pre-dose index_candidates), never transliterating the SQL.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from hypothesis import assume, event, given
from hypothesis import strategies as st

from .conftest import expensive_settings
from .oracles.c_glue import (
    CAND_COLS,
    GRID_ANCHOR,
    cohort_a,
    dose_claims_from_rows,
    grid_dataset,
    two_pass_run,
    with_dose,
)
from .oracles.c_models import DoseCandidate, dose_verdict

_EXCL_COLS = ("cohortgrp", "patid", "adate", "reason")
_STRENGTHS = {"E00001": 1.0}


def _run_dose(
    dataset: Any,
    *,
    wash_per: int,
    cum_dose_per: int,
    min_cum_dose: float | None,
    max_cum_dose: float | None,
    min_cfdd: float | None,
    max_cfdd: float | None,
):
    """Run through 'dose restrictions'; return the pass object plus the
    model verdict per pre-dose candidate."""
    study = with_dose(
        cohort_a(exposure_codes=("E00001",), event_codes=("D00001",),
                 wash_per=wash_per),
        exposure_codes=("E00001",),
        code_strength=[("E00001", 1.0)],
        wash_per=wash_per,
        cum_dose_per=cum_dose_per,
        min_cum_dose=min_cum_dose,
        max_cum_dose=max_cum_dose,
        min_cfdd=min_cfdd,
        max_cfdd=max_cfdd,
    )
    run = two_pass_run(
        study, dataset,
        stop_before="index dates",
        stop_after="dose restrictions",
        input_tables={
            "index_candidates": CAND_COLS,
            "exposure_claims": (
                "cohortgrp", "patid", "adate", "code", "rxsup", "rxamt",
            ),
        },
        output_tables={
            "dose_excluded": _EXCL_COLS,
            "index_candidates": CAND_COLS,
        },
    )
    claims = dose_claims_from_rows(run.inputs["exposure_claims"],
                                   _STRENGTHS)
    verdicts = {}
    for r in run.inputs["index_candidates"]:
        cand = DoseCandidate(
            cohortgrp=str(r["cohortgrp"]), patid=int(r["patid"]),
            adate=r["adate"], orig_adate=r["orig_adate"],
        )
        verdicts[(cand.cohortgrp, cand.patid, cand.adate)] = dose_verdict(
            cand, claims,
            min_cum_dose=min_cum_dose, max_cum_dose=max_cum_dose,
            cum_dose_per=cum_dose_per,
            min_cfdd=min_cfdd, max_cfdd=max_cfdd,
        )
    return run, verdicts


def _assert_dose_matches_model(run: Any, verdicts: dict) -> None:
    excluded_stage = {
        (str(r["cohortgrp"]), int(r["patid"]), r["adate"]):
            str(r["reason"])
        for r in run.outputs["dose_excluded"]
    }
    excluded_model = {
        k: v.reason for k, v in verdicts.items() if v.excluded
    }
    assert excluded_stage == excluded_model, (
        "dose_excluded differs", excluded_stage, excluded_model)
    survivors = {
        (str(r["cohortgrp"]), int(r["patid"]), r["adate"])
        for r in run.outputs["index_candidates"]
    }
    expected = {k for k, v in verdicts.items() if not v.excluded}
    assert survivors == expected, (
        sorted(str(s) for s in survivors ^ expected),
        "surviving index_candidates differ from the model")


# ---------------------------------------------------------------------
# Hand-computed boundary cases
# ---------------------------------------------------------------------


def test_dose_lookback_window_boundaries() -> None:
    """The prior-dose frame [orig - per, orig - 1] is CLOSED at both
    ends: a fill at exactly orig - per counts, one day earlier does
    not, and the index day's own fill never counts.

    wash_per=5 so a fill at -10 (supply 4, expiry -7) leaves the index
    intact; cum_dose_per=10 with min/max_cum_dose at the round(1)
    boundary.
    """
    cases = (
        # (fill offset, amount, knob, expect_excluded)
        (-10, 1.0, ("min_cum_dose", 1.0), False),   # boundary: counts, 1 >= 1
        (-11, 1.0, ("min_cum_dose", 1.0), True),    # just outside: prior 0
        (-10, 2.0, ("max_cum_dose", 1.0), True),    # round 2 > 1
        (-10, 1.0, ("max_cum_dose", 1.0), False),   # 1 <= 1 inclusive
        (0, 2.0, ("max_cum_dose", 1.0), False),     # own day never counts
    )
    for off, amt, (knob, value), expect_excluded in cases:
        dataset = grid_dataset(fills=[(0, "E00001", 30, 2.0),
                                      (off, "E00001", 4, amt)],
                               index_fill=False)
        kwargs: dict[str, float | None] = dict(
            min_cum_dose=None, max_cum_dose=None,
            min_cfdd=None, max_cfdd=None)
        kwargs[knob] = value
        run, verdicts = _run_dose(
            dataset, wash_per=5, cum_dose_per=10, **kwargs)
        _assert_dose_matches_model(run, verdicts)
        index_keys = [k for k in verdicts if k[2] == GRID_ANCHOR]
        assert index_keys, (off, amt, knob, verdicts)
        assert verdicts[index_keys[0]].excluded is expect_excluded, (
            off, amt, knob, value, verdicts)


def test_dose_cfdd_rounds_half_away_to_integer() -> None:
    """cfdd bounds compare round(cfdd) -- integer rounding, halves AWAY
    FROM ZERO. cfdd 0.5 rounds to 1 and passes min_cfdd=1; cfdd 0.25
    rounds to 0 and fails; cfdd 2.0 fails max_cfdd=1.

    The claims inspected are those ON the index day: the index fill
    itself (single fill, wash_per irrelevant).
    """
    cases = (
        (0.5, ("min_cfdd", 1.0), False),   # round(0.5) = 1 >= 1
        (0.25, ("min_cfdd", 1.0), True),   # round(0.25) = 0 < 1
        (0.5, ("max_cfdd", 1.0), False),   # 1 <= 1
        (1.0, ("max_cfdd", 1.0), False),   # round(1.0) = 1 <= 1
        (2.0, ("max_cfdd", 1.0), True),    # 2 > 1
    )
    for cfdd, (knob, value), expect_excluded in cases:
        sup, amt = 4, cfdd * 4
        dataset = grid_dataset(fills=[(0, "E00001", sup, amt)],
                               index_fill=False)
        kwargs: dict[str, float | None] = dict(
            min_cum_dose=None, max_cum_dose=None,
            min_cfdd=None, max_cfdd=None)
        kwargs[knob] = value
        run, verdicts = _run_dose(
            dataset, wash_per=5, cum_dose_per=10, **kwargs)
        _assert_dose_matches_model(run, verdicts)
        index_keys = [k for k in verdicts if k[2] == GRID_ANCHOR]
        assert index_keys, (cfdd, knob, verdicts)
        assert verdicts[index_keys[0]].excluded is expect_excluded, (
            cfdd, knob, value, verdicts)


def test_dose_any_same_day_claim_out_of_bounds_excludes() -> None:
    """Two fills ON the index day: one in bounds, one out -- the
    candidate is excluded, because the exclusion table is DISTINCT
    over the fanned join and the bad row alone satisfies the WHERE.

    Split 2/8 units over 4-day supplies: cfdds 0.5 and 2.0 round to 1
    and 2; max_cfdd=1.5 keeps the first (1 <= 1.5) but the 2 > 1.5 row
    alone excludes. Split 2/4: cfdds 0.5 and 1.0, both inside.
    """
    for amts, expect_excluded in (((2.0, 4.0), False), ((2.0, 8.0), True)):
        dataset = grid_dataset(fills=[(0, "E00001", 4, amts[0]),
                                      (0, "E00001", 4, amts[1])],
                               index_fill=False)
        run, verdicts = _run_dose(
            dataset, wash_per=5, cum_dose_per=10,
            min_cum_dose=None, max_cum_dose=None,
            min_cfdd=None, max_cfdd=1.5)
        _assert_dose_matches_model(run, verdicts)
        index_keys = [k for k in verdicts if k[2] == GRID_ANCHOR]
        assert index_keys, (amts, verdicts)
        assert verdicts[index_keys[0]].excluded is expect_excluded, (
            amts, verdicts)


def test_dose_washout_zero_prior_fill_feeds_second_index() -> None:
    """wash_per=0 keeps every record as a candidate, so the SECOND
    index date's prior-dose window sees the first fill (exposure
    claims are not washout-filtered): the same physical claim is both
    an episode and somebody else's prior dose.

    Fills at day 0 and day 10; cum_dose_per=10; the day-10 candidate's
    window [0, 9] includes the day-0 fill.
    """
    dataset = grid_dataset(fills=[(0, "E00001", 5, 3.0),
                                  (10, "E00001", 5, 1.0)],
                           index_fill=False)
    run, verdicts = _run_dose(
        dataset, wash_per=0, cum_dose_per=10,
        min_cum_dose=2.0, max_cum_dose=None,
        min_cfdd=None, max_cfdd=None)
    _assert_dose_matches_model(run, verdicts)
    keys = sorted(verdicts, key=lambda k: k[2])
    assert len(keys) >= 2, verdicts
    first, second = keys[0], keys[-1]
    assert verdicts[first].excluded, "prior 0 < min 2 must exclude"
    assert not verdicts[second].excluded, (
        "the second index sees the first fill's 3 units >= 2")


# ---------------------------------------------------------------------
# Interaction: same logical exposure, different claim sets
# ---------------------------------------------------------------------


def test_interaction_dose_one30_vs_three10() -> None:
    """MANDATORY interaction: 30 units of exposure delivered as one
    30-day fill vs three 10-day fills vs three same-day fills.

    Prior-dose granularity must not matter: one 30-day fill and three
    10-day fills put the same 30 units inside [index-per, index-1], so
    max_cum_dose=30 keeps both (inclusive) and 29 excludes both.
    Same-day granularity DOES matter for cfdd: three same-day fills of
    15/4/11 units have per-claim cfdds 1.5/0.4/1.1, and min_cfdd=1.0
    excludes on the 0.4 claim while a single 30-unit fill (cfdd 1.0)
    passes -- same total, same day, different decision. The index date
    is held fixed across all variants and the model predicts each
    cell.
    """
    index_fill = (0, "E00001", 4, 2.0)
    one_prior = grid_dataset(fills=[index_fill, (-40, "E00001", 30, 30.0)],
                             index_fill=False)
    three_prior = grid_dataset(
        fills=[index_fill, (-40, "E00001", 10, 10.0),
               (-30, "E00001", 10, 10.0), (-20, "E00001", 10, 10.0)],
        index_fill=False)
    for name, dataset in (("one_prior", one_prior),
                          ("three_prior", three_prior)):
        for max_cum, expect_excluded in ((30.0, False), (29.0, True)):
            run, verdicts = _run_dose(
                dataset, wash_per=5, cum_dose_per=45,
                min_cum_dose=None, max_cum_dose=max_cum,
                min_cfdd=None, max_cfdd=None)
            _assert_dose_matches_model(run, verdicts)
            index_keys = [k for k in verdicts if k[2] == GRID_ANCHOR]
            assert index_keys, (name, verdicts)
            assert verdicts[index_keys[0]].excluded is expect_excluded, (
                name, max_cum, verdicts)

    one_sameday = grid_dataset(fills=[(0, "E00001", 30, 30.0)],
                               index_fill=False)
    three_sameday = grid_dataset(
        fills=[(0, "E00001", 10, 15.0), (0, "E00001", 10, 4.0),
               (0, "E00001", 10, 11.0)],
        index_fill=False)
    for name, dataset, expect_excluded in (
            ("one_sameday", one_sameday, False),
            ("three_sameday", three_sameday, True)):
        run, verdicts = _run_dose(
            dataset, wash_per=5, cum_dose_per=45,
            min_cum_dose=None, max_cum_dose=None,
            min_cfdd=1.0, max_cfdd=None)
        _assert_dose_matches_model(run, verdicts)
        index_keys = [k for k in verdicts if k[2] == GRID_ANCHOR]
        assert index_keys, (name, verdicts)
        assert verdicts[index_keys[0]].excluded is expect_excluded, (
            name, verdicts)


# ---------------------------------------------------------------------
# Generated property
# ---------------------------------------------------------------------

_OFFSETS = st.sampled_from([-45, -31, -30, -20, -10])
_SUPS = st.sampled_from([4, 5, 10])
_AMTS = st.sampled_from([1.0, 2.0, 2.5, 4.0, 8.0])
_CUM_PER = st.sampled_from([10, 30])
_KNOBS = st.tuples(
    st.sampled_from([None, 1.0, 3.0]),    # min_cum_dose
    st.sampled_from([None, 1.0, 3.0]),    # max_cum_dose
    st.sampled_from([None, 1.0]),         # min_cfdd
    st.sampled_from([None, 1.0]),         # max_cfdd
)
_WASH = st.sampled_from([0, 5, 10])


@st.composite
def _dose_scenarios(draw: st.DrawFn):
    """Small dose scenarios on the grid: a handful of prior fills near
    the dose-lookback boundaries plus the knob profile."""
    wash = draw(_WASH)
    n_fills = draw(st.integers(min_value=0, max_value=3))
    offsets = []
    for _ in range(n_fills):
        off = draw(_OFFSETS)
        # A fill at -10 (expiry >= -7) suppresses the index under
        # wash 10; drop it there -- the model reads whatever
        # candidates exist, but keeping the index keeps the scenario
        # interesting.
        if off == -10 and wash == 10:
            off = -20
        offsets.append(off)
    fills = [
        (off, "E00001", draw(_SUPS), draw(_AMTS)) for off in offsets
    ]
    fills.append((0, "E00001", draw(st.sampled_from([4, 30])),
                  draw(_AMTS)))
    if draw(st.booleans()):
        # a second same-day fill: two claim_dose rows on one day
        fills.append((0, "E00001", draw(_SUPS), draw(_AMTS)))
    knobs = draw(_KNOBS)
    assume(any(k is not None for k in knobs))
    return grid_dataset(fills=fills, index_fill=False), wash, knobs


@given(data=_dose_scenarios())
@expensive_settings()
def test_dose_boundaries_match_model(data: Any) -> None:
    """Every generated candidate's exclusion decision and reason
    equals the model; survivors are exactly the kept candidates."""
    dataset, wash, (min_cum, max_cum, min_cfdd, max_cfdd) = data
    cum_per = 30 if min_cum is not None else 10
    run, verdicts = _run_dose(
        dataset, wash_per=wash, cum_dose_per=cum_per,
        min_cum_dose=min_cum, max_cum_dose=max_cum,
        min_cfdd=min_cfdd, max_cfdd=max_cfdd)
    _assert_dose_matches_model(run, verdicts)

    # Coverage instrumentation.
    event("candidates", "some" if verdicts else "none")
    excluded = [v for v in verdicts.values() if v.excluded]
    event("exclusions", "some" if excluded else "none")
    for v in excluded:
        event("reason", str(v.reason))
    lo = GRID_ANCHOR - timedelta(days=cum_per)
    for r in run.inputs["exposure_claims"]:
        if r["adate"] in (lo, GRID_ANCHOR - timedelta(days=1)):
            event("lookback boundary exact hit")
    same_day = [
        r for r in run.inputs["exposure_claims"]
        if r["adate"] == GRID_ANCHOR
    ]
    if len(same_day) > 1:
        event("distinct-day vs row divergence visible")
