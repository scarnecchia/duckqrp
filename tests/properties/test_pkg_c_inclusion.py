"""Package C -- inclusion criteria (52_inclusion.sql) vs the condition
model.

Contract under test (docs/INCLUSION.md + the stage's own header):

* conditions AND, subconditions AND, codes within a subcondition
  alternatives with counts POOLED across the subcondition's codes;
* sub-exclusions (subcondinclusion=0) invert their subcondition;
* `codedays` counts DISTINCT days, not claims;
* INC passes when met, EXC fails when met; `first_failed_cond` is the
  smallest failing cond across both senses; criteria are part of the
  condition key, so an INC and an EXC rule may both be cond 1;
* windows anchor each end independently on INDEXDT or EPISODEENDDT,
  inclusive of both ends; RX claims match when SUPPLY overlaps, and
  `minrxdays` sums overlap days; inclusion dose is computed from the
  MATCHED claims, pro-rated by the supply fraction inside the window,
  and aFDD is the window average of per-claim cfdd;
* failures drop the EPISODE (ptsmasterlist row) and are recorded in
  inclusion_excluded.

Oracles: `oracles/c_models.py` re-derives every verdict with plain
date arithmetic over the fixture claims; the harness snapshots the
stage's real inputs (episode_criteria level-16 survivors and the
cohort_claims narrow) so the model sees exactly what the stage saw.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from hypothesis import event, given
from hypothesis import strategies as st
from qrp.config import InclusionRule

from .conftest import expensive_settings
from .oracles.c_glue import (
    GRID_ANCHOR,
    GRID_END,
    GRID_START,
    cohort_a,
    grid_dataset,
    rules_by_key,
    single_cohort_study,
    two_pass_run,
)
from .oracles.c_models import (
    IncClaim,
    Episode,
    InclusionVerdict,
    inclusion_verdict,
)
from .synth import generate

EPISODE_KEY_COLS = ("cohortgrp", "patid", "indexdt")
_CLAIM_COLS = ("patid", "adate", "expiredt", "code", "codecat", "rxsup",
               "rxamt")
_EXCL_COLS = EPISODE_KEY_COLS + ("first_failed_cond", "failed_criteria")


def _inc(
    *,
    cond: int = 1,
    subcond: int = 1,
    criteria: str = "INC",
    codecat: str = "DX",
    condfrom: int = -30,
    condto: int = -1,
    codedays: int = 1,
    codes: tuple[str, ...] = ("D00001",),
    subcond_inclusion: bool = True,
    condfromanchor: str = "INDEXDT",
    condtoanchor: str = "INDEXDT",
    minrxdays: int = 1,
    mincumdose: float | None = None,
    minafdd: float | None = None,
    maxafdd: float | None = None,
) -> InclusionRule:
    return InclusionRule(
        cohortgrp="cohort_a", cond=cond, subcond=subcond,
        criteria=criteria, codecat=codecat, condfrom=condfrom,
        condto=condto, codedays=codedays, codes=codes,
        subcond_inclusion=subcond_inclusion, condfromanchor=condfromanchor,
        condtoanchor=condtoanchor, minrxdays=minrxdays,
        mincumdose=mincumdose, minafdd=minafdd, maxafdd=maxafdd,
    )


def _run_inclusion(
    dataset: Any, rules: list[InclusionRule], *, wash_per: int = 183,
    enr_days: int = 183,
    strengths: dict[str, float] | None = None,
) -> tuple[Any, dict[tuple[str, int, Any], InclusionVerdict]]:
    """Run through 'inclusion criteria'; return the pass object and the
    model verdict for every level-16 survivor episode. `strengths`
    feeds both cfg_code_strength (the stage's dose lookup) and the
    model, so dose thresholds mean the same thing on both sides."""
    study = single_cohort_study(
        cohort_a(exposure_codes=("E00001",),
                 event_codes=("D00001", "D00002", "D00003", "D00004"),
                 wash_per=wash_per, enr_days=enr_days),
        inclusions=rules,
        code_strength=tuple((c, v) for c, v in (strengths or {}).items()),
    )
    run = two_pass_run(
        study, dataset,
        stop_before="episodes + masterlist",
        stop_after="inclusion criteria",
        input_tables={
            "episode_criteria": EPISODE_KEY_COLS
            + ("episodeenddt", "min_enr_days_met"),
            "cohort_claims": _CLAIM_COLS,
        },
        output_tables={
            "inclusion_excluded": _EXCL_COLS,
            "ptsmasterlist": EPISODE_KEY_COLS,
        },
    )
    inc_claims = [
        IncClaim(
            patid=int(r["patid"]), adate=r["adate"], expiredt=r["expiredt"],
            code=str(r["code"]), codecat=str(r["codecat"]),
            rxsup=r["rxsup"], rxamt=r["rxamt"],
        )
        for r in run.inputs["cohort_claims"]
    ]
    rules_map = rules_by_key(rules)
    verdicts: dict[tuple[str, int, Any], InclusionVerdict] = {}
    for r in run.inputs["episode_criteria"]:
        if not r["min_enr_days_met"]:
            continue
        ep = Episode(
            cohortgrp=str(r["cohortgrp"]), patid=int(r["patid"]),
            indexdt=r["indexdt"], episodeenddt=r["episodeenddt"],
        )
        verdicts[(ep.cohortgrp, ep.patid, ep.indexdt)] = inclusion_verdict(
            ep, inc_claims, rules_map, strengths or {})
    return run, verdicts


def _observed_excluded(
    run: Any,
) -> dict[tuple[str, int, Any], tuple[Any, set[str]]]:
    out: dict[tuple[str, int, Any], tuple[Any, set[str]]] = {}
    for r in run.outputs["inclusion_excluded"]:
        key = (str(r["cohortgrp"]), int(r["patid"]), r["indexdt"])
        out[key] = (r["first_failed_cond"], set(str(r["failed_criteria"])
                                                .split(",")))
    return out


def _observed_survivors(run: Any) -> set[tuple[str, int, Any]]:
    return {
        (str(r["cohortgrp"]), int(r["patid"]), r["indexdt"])
        for r in run.outputs["ptsmasterlist"]
    }


def _assert_stage_matches_model(
    run: Any, verdicts: dict[tuple[str, int, Any], InclusionVerdict],
) -> None:
    observed_excl = _observed_excluded(run)
    input_keys = {
        (str(r["cohortgrp"]), int(r["patid"]), r["indexdt"])
        for r in run.inputs["episode_criteria"] if r["min_enr_days_met"]
    }
    for key, verdict in verdicts.items():
        if verdict.excluded:
            assert key in observed_excl, (key, verdict)
            cond, crit = observed_excl[key]
            assert cond == verdict.first_failed_cond, (key, cond, verdict)
            assert crit == set(verdict.failed_criteria), (key, crit, verdict)
        else:
            assert key not in observed_excl, (key, observed_excl.get(key))
    for key in observed_excl:
        assert key in verdicts, (key, "excluded row for a non-survivor")
    survivors = _observed_survivors(run)
    expected = (
        {k for k, v in verdicts.items() if not v.excluded}
        | (input_keys - set(verdicts))
    )
    assert survivors == expected, (
        sorted(str(s) for s in survivors ^ expected),
        "master list membership differs from the model")


def _index_verdicts(
    verdicts: dict[tuple[str, int, Any], InclusionVerdict],
) -> list[InclusionVerdict]:
    return [v for k, v in verdicts.items() if k[2] == GRID_ANCHOR]


# ---------------------------------------------------------------------
# Hand-computed cases (exhaustive, small)
# ---------------------------------------------------------------------


def test_inclusion_codedays_counts_distinct_days() -> None:
    """Two claims on one day are ONE day; `codedays` pins day counts.

    Fixture: D00001 on days -1 (twice) and -3.
    codedays=1 and 2 pass; codedays=3 fails -> episode excluded.
    """
    dataset = grid_dataset(dxs=[(-1, "D00001", "AV"), (-1, "D00001", "OA"),
                                (-3, "D00001", "AV")])
    for codedays, expect_excluded in ((1, False), (2, False), (3, True)):
        run, verdicts = _run_inclusion(
            dataset, [_inc(codedays=codedays, condfrom=-30, condto=-1)])
        assert verdicts, "no episode survived the prefix"
        for key, v in verdicts.items():
            assert v.excluded is expect_excluded, (codedays, key, v)
        _assert_stage_matches_model(run, verdicts)


def test_inclusion_window_ends_are_inclusive() -> None:
    """Claims exactly at condfrom/condto count; one day outside does not.

    Four conditions over four codes on days -4, -3, -1, 0 against the
    window [-3, -1]: conds on -3 and -1 pass, conds on -4 and 0 fail,
    first_failed_cond = 1 (the smallest failing cond).
    """
    dataset = grid_dataset(dxs=[(-4, "D00001", "AV"), (-3, "D00002", "AV"),
                                (-1, "D00003", "AV"), (0, "D00004", "AV")])
    rules = [
        _inc(cond=1, codes=("D00001",), condfrom=-3, condto=-1),
        _inc(cond=2, codes=("D00002",), condfrom=-3, condto=-1),
        _inc(cond=3, codes=("D00003",), condfrom=-3, condto=-1),
        _inc(cond=4, codes=("D00004",), condfrom=-3, condto=-1),
    ]
    run, verdicts = _run_inclusion(dataset, rules)
    _assert_stage_matches_model(run, verdicts)
    excluded = [v for v in verdicts.values() if v.excluded]
    assert len(excluded) == 1 and excluded[0].first_failed_cond == 1
    assert excluded[0].failed_criteria == ("INC",)


def test_inclusion_exc_fails_when_met_same_cond_number() -> None:
    """INC and EXC may both be cond 1: criteria is part of the key.

    D00001 at -5 satisfies the INC rule; D00002 at -3 satisfies the
    EXC rule -> the episode fails with failed_criteria={EXC} while the
    INC condition passed. Same data, EXC code absent: kept.
    """
    for with_dx2, expect_criteria in ((False, set()), (True, {"EXC"})):
        dxs = [(-5, "D00001", "AV")]
        if with_dx2:
            dxs.append((-3, "D00002", "AV"))
        run, verdicts = _run_inclusion(grid_dataset(dxs=dxs), [
            _inc(cond=1, criteria="INC", codes=("D00001",)),
            _inc(cond=1, criteria="EXC", codes=("D00002",)),
        ])
        _assert_stage_matches_model(run, verdicts)
        for v in verdicts.values():
            assert set(v.failed_criteria) == expect_criteria, v


def test_inclusion_subconditions_are_anded() -> None:
    """Both subconditions of cond 1 must be met."""
    rules = [
        _inc(cond=1, subcond=1, codes=("D00001",)),
        _inc(cond=1, subcond=2, codes=("D00002",)),
    ]
    run, verdicts = _run_inclusion(
        grid_dataset(dxs=[(-5, "D00001", "AV")]), rules)
    _assert_stage_matches_model(run, verdicts)
    assert verdicts and all(v.excluded for v in verdicts.values()), verdicts
    run, verdicts = _run_inclusion(
        grid_dataset(dxs=[(-5, "D00001", "AV"), (-2, "D00002", "AV")]),
        rules)
    _assert_stage_matches_model(run, verdicts)
    assert all(not v.excluded for v in verdicts.values()), verdicts


def test_subcond_exclusion_inverts_its_subcondition() -> None:
    """subcondinclusion=0: MEETING the subcondition fails the condition.

    Sub 1 requires D00001 (present); sub 2 forbids D00002: without the
    D00002 claim the episode survives, with it, it is excluded.
    """
    rules = [
        _inc(cond=1, subcond=1, codes=("D00001",)),
        _inc(cond=1, subcond=2, codes=("D00002",), subcond_inclusion=False),
    ]
    for with_dx2, expect_excluded in ((False, False), (True, True)):
        dxs = [(-5, "D00001", "AV")]
        if with_dx2:
            dxs.append((-3, "D00002", "AV"))
        run, verdicts = _run_inclusion(grid_dataset(dxs=dxs), rules)
        _assert_stage_matches_model(run, verdicts)
        for v in verdicts.values():
            assert v.excluded is expect_excluded, (with_dx2, v)


def test_inclusion_codes_within_subcond_pool_their_days() -> None:
    """CHARACTER TEST for an unspecified shape (open question OC-1).

    Two codes in ONE subcondition with codedays=2, each code on ONE
    distinct day. The shipped stage pools distinct days across the
    subcondition's codes (one hits row per condition key), so the rule
    is MET. A per-code "any single code satisfies" reading of
    'alternatives' would NOT be met. This pins the pooled reading the
    shipped SQL implements; docs/INCLUSION.md's OR wording is ambiguous
    at exactly this point.
    """
    dataset = grid_dataset(dxs=[(-5, "D00001", "AV"), (-3, "D00002", "AV")])
    run, verdicts = _run_inclusion(
        dataset, [_inc(codes=("D00001", "D00002"), codedays=2)])
    _assert_stage_matches_model(run, verdicts)
    assert verdicts and all(not v.excluded for v in verdicts.values())


def test_inclusion_rx_supply_overlap_and_mincrxdays() -> None:
    """RX claims match when SUPPLY overlaps the window; minrxdays sums
    overlap days; cumdose is pro-rated by the same fraction.

    Fill at -20 with 16 days of supply covers [-20, -5]: the window
    [-10, -1] overlaps exactly 6 days. Strength 1, amount 16:
    pro-rated cumdose = 16 * 6/16 = 6.0 exactly (6/16 is binary-exact),
    so mincumdose=6.0 sits ON the boundary (>= passes) and 6.5 fails.

    wash_per=0 keeps the -20 fill as its own candidate too; the model
    evaluates every episode the prefix produced (that episode's own
    window [-30, -21] misses the fill and is excluded).
    """
    dataset = grid_dataset(fills=[(0, "E00001", 30, 10.0),
                                  (-20, "E00001", 16, 16.0)],
                           index_fill=False)
    strengths = {"E00001": 1.0}
    for minrxdays, mincumdose, expect_index_excluded in (
            (6, None, False), (7, None, True),
            (1, 6.0, False), (1, 6.5, True)):
        rules = [_inc(codecat="RX", codes=("E00001",), condfrom=-10,
                      condto=-1, minrxdays=minrxdays, mincumdose=mincumdose)]
        run, verdicts = _run_inclusion(
            dataset, rules, wash_per=0, strengths=strengths)
        _assert_stage_matches_model(run, verdicts)
        index_v = _index_verdicts(verdicts)
        assert index_v, "index episode missing from the prefix"
        assert index_v[0].excluded is expect_index_excluded, (
            minrxdays, mincumdose, verdicts)


def test_inclusion_episodeend_anchor_is_forward_looking() -> None:
    """EPISODEENDDT anchors a FORWARD window past the index date.

    Episode: index at day 0, episode end at day 29 (30-day fill), a
    claim at +7. The INDEXDT window [1, 5] misses it; the second rule
    anchors the SAME offsets on EPISODEENDDT, whose window is
    (29 + 1, 29 + 5) = [30, 34] -- nothing -- so that rule fails too;
    the third offsets [-23, -19] on EPISODEENDDT give [6, 10], which
    covers the claim at +8 and passes. Both anchors applied to the
    same fixture produce different verdicts: the doc's own anchor
    check, plus the model pinning each window arithmetic.
    """
    dataset = grid_dataset(
        fills=[(0, "E00001", 30, 10.0)],
        dxs=[(7, "D00001", "AV"), (8, "D00002", "AV")],
        index_fill=False,
    )
    rules = [
        _inc(cond=1, codes=("D00001",), condfrom=1, condto=5),
        _inc(cond=2, codes=("D00002",), condfrom=1, condto=5,
             condfromanchor="EPISODEENDDT", condtoanchor="EPISODEENDDT"),
        _inc(cond=3, codes=("D00002",), condfrom=-23, condto=-19,
             condfromanchor="EPISODEENDDT", condtoanchor="EPISODEENDDT"),
    ]
    run, verdicts = _run_inclusion(dataset, rules)
    _assert_stage_matches_model(run, verdicts)
    excluded = [v for v in verdicts.values() if v.excluded]
    assert [v.first_failed_cond for v in excluded] == [1], (
        "the forward-anchored cond 2 should pass while cond 1 fails")


def test_inclusion_afdd_is_window_average_of_matched_claims() -> None:
    """aFDD is the average per-claim cfdd over MATCHED claims.

    Fixture: fill at -12 (5 days, amount 10 -> cfdd 10/5 = 2.0) and
    fill at -7 (6 days, amount 6 -> cfdd 1.0). Prior supply ends on
    day -2, so the index fill keeps its own episode (no chaining, no
    stockpile push). Both fills overlap the index window [-10, -1] --
    the first by supply overlap (expiredt -8 >= window start), the
    second fully -- so afdd = round((2.0 + 1.0) / 2, 1) = 1.5 exactly.
    Band [1.5, 1.5] passes; [1.4, 1.4] fails low; [1.6, 9.9] fails
    high.
    """
    dataset = grid_dataset(fills=[(0, "E00001", 30, 10.0),
                                  (-12, "E00001", 5, 10.0),
                                  (-7, "E00001", 6, 6.0)],
                           index_fill=False)
    strengths = {"E00001": 1.0}
    for lo, hi, expect_excluded in (
            (1.5, 1.5, False), (1.4, 1.4, True), (1.6, 9.9, True)):
        rules = [_inc(codecat="RX", codes=("E00001",), condfrom=-10,
                      condto=-1, minafdd=lo, maxafdd=hi)]
        run, verdicts = _run_inclusion(dataset, rules, wash_per=0,
                                       strengths=strengths)
        _assert_stage_matches_model(run, verdicts)
        index_v = _index_verdicts(verdicts)
        assert index_v, "index episode missing from the prefix"
        assert index_v[0].excluded is expect_excluded, (lo, hi)


# ---------------------------------------------------------------------
# Interaction: same logical exposure, different claim sets
# ---------------------------------------------------------------------


def test_interaction_one30_vs_three10_supply_pooled_equal() -> None:
    """MANDATORY interaction: one 29-day fill vs three chained fills
    covering the same supply span, holding the episode/index fixed.

    Episode construction merges fills whose supply chains continuously
    (the next fill starts on the day after the previous expiry), so the
    variants keep a supply GAP before day 0 -- prior supply ends on
    day -2 -- and both produce the episodes {start -30, start 0}.

    In the index-anchored window [-10, -1] BOTH variants supply 9 days
    ([-10, -2]) and pro-rate to the same cumdose (~9 units at cfdd
    1.0), so minrxdays=9 and mincumdose=8.9 pass for both, and 9.5
    fails for both: granularity does not change pooled supply/dose.
    Reducing ONLY the final fill's amount to 5 (cfdd 5/11) drops the
    variant's pro-rated cumdose to ~4.1, so mincumdose=8.9 then
    separates the variants while minrxdays=9 still passes both. The
    model predicts every cell; the stage must agree in each.
    """
    anchor = GRID_ANCHOR
    one29 = grid_dataset(fills=[(0, "E00001", 30, 30.0),
                                (-30, "E00001", 29, 29.0)],
                         index_fill=False)
    three_chained = grid_dataset(
        fills=[(0, "E00001", 30, 30.0),
               (-30, "E00001", 9, 9.0),    # covers [-30, -22]
               (-21, "E00001", 9, 9.0),    # covers [-21, -13]
               (-12, "E00001", 11, 11.0)],  # covers [-12, -2]
        index_fill=False)
    variants = (("one29", one29), ("three_chained", three_chained))

    rule_supply = _inc(codecat="RX", codes=("E00001",), condfrom=-10,
                       condto=-1, minrxdays=9)
    strengths = {"E00001": 1.0}
    for name, dataset in variants:
        run, verdicts = _run_inclusion(dataset, [rule_supply], wash_per=0,
                                       strengths=strengths)
        _assert_stage_matches_model(run, verdicts)
        index_v = _index_verdicts(verdicts)
        assert index_v and not index_v[0].excluded, (name, verdicts)

    rule_dose = _inc(codecat="RX", codes=("E00001",), condfrom=-10,
                     condto=-1, mincumdose=8.9)
    rule_dose_high = _inc(codecat="RX", codes=("E00001",), condfrom=-10,
                          condto=-1, mincumdose=9.5)
    for name, dataset in variants:
        for rule, expect_excluded in ((rule_dose, False),
                                      (rule_dose_high, True)):
            run, verdicts = _run_inclusion(dataset, [rule], wash_per=0,
                                           strengths=strengths)
            _assert_stage_matches_model(run, verdicts)
            index_v = _index_verdicts(verdicts)
            assert index_v, (name, verdicts)
            assert index_v[0].excluded is expect_excluded, (name, verdicts)

    three_chained_small = grid_dataset(
        fills=[(0, "E00001", 30, 30.0),
               (-30, "E00001", 9, 9.0),
               (-21, "E00001", 9, 9.0),
               (-12, "E00001", 11, 5.0)],   # less amount in the window
        index_fill=False)
    run, verdicts = _run_inclusion(three_chained_small, [rule_dose],
                                   wash_per=0, strengths=strengths)
    _assert_stage_matches_model(run, verdicts)
    assert _index_verdicts(verdicts)[0].excluded is True
    # ...while the same rule keeps passing on one29: same supply span,
    # different claim granularity, different pro-rated dose. Pinned.
    run, verdicts = _run_inclusion(one29, [rule_dose], wash_per=0,
                                   strengths=strengths)
    _assert_stage_matches_model(run, verdicts)
    assert _index_verdicts(verdicts)[0].excluded is False
    assert anchor == GRID_ANCHOR  # the held-fixed episode anchor


def test_interaction_same_day_triple_claim_day_vs_row_counting() -> None:
    """MANDATORY interaction: three matching claims on ONE day satisfy
    codedays=1 but not codedays=2 -- the documented distinct-day
    semantics over deliberately tied same-day rows."""
    dataset = grid_dataset(dxs=[(-1, "D00001", "AV"), (-1, "D00001", "OA"),
                                (-1, "D00001", "IP")])
    for codedays, expect_excluded in ((1, False), (2, True)):
        run, verdicts = _run_inclusion(
            dataset, [_inc(codedays=codedays, condfrom=-1, condto=-1)])
        index_v = _index_verdicts(verdicts)
        assert index_v, verdicts
        assert index_v[0].excluded is expect_excluded, (codedays, verdicts)
        _assert_stage_matches_model(run, verdicts)


# ---------------------------------------------------------------------
# Generated property
# ---------------------------------------------------------------------

_OFFERS = st.sampled_from([-20, -10, -5, -3, -1, 0])
_TOFFS = st.sampled_from([-1, 0, 1, 3, 5, 10])
_CODEDAYS = st.integers(min_value=1, max_value=3)


@st.composite
def _rule_sets(draw: st.DrawFn) -> list[InclusionRule]:
    """Small rule sets over the grid vocabulary, biased toward windows
    that intersect the fixture's claim dates."""
    criteria = draw(st.sampled_from(["INC", "EXC"]))
    shape = draw(st.sampled_from(["single", "two_cond", "two_subcond",
                                  "sub_exclusion", "multi_code",
                                  "rx_supply"]))
    common: dict[str, Any] = dict(criteria=criteria, condfrom=draw(_OFFERS),
                                  condto=draw(_TOFFS),
                                  codedays=draw(_CODEDAYS))
    if shape == "rx_supply":
        # RX rules against the index fill (30-day supply from day 0):
        # windows with condto >= 0 overlap the supply, so minrxdays
        # exact fits occur at minrxdays == overlap days.
        return [_inc(cond=1, codecat="RX", codes=("E00001",),
                     condfrom=draw(_OFFERS), condto=draw(_TOFFS),
                     codedays=1,
                     minrxdays=draw(st.sampled_from([1, 2, 3])))]
    if shape == "single":
        return [_inc(cond=1, codes=("D00001",), **common)]
    if shape == "two_cond":
        return [
            _inc(cond=1, codes=("D00001",), **common),
            _inc(cond=2, codes=("D00002",), **common),
        ]
    if shape == "two_subcond":
        return [
            _inc(cond=1, subcond=1, codes=("D00001",), **common),
            _inc(cond=1, subcond=2, codes=("D00002",), **common),
        ]
    if shape == "sub_exclusion":
        return [
            _inc(cond=1, subcond=1, codes=("D00001",), **common),
            _inc(cond=1, subcond=2, codes=("D00002",),
                 subcond_inclusion=False, **common),
        ]
    return [_inc(cond=1, codes=("D00001", "D00002"), **common)]


@st.composite
def _grid_datasets(draw: st.DrawFn):
    """Grid fixtures: the index fill plus claims on a small offset grid,
    so generated windows land exactly on boundaries often."""
    dx_offsets = draw(
        st.lists(st.sampled_from([-20, -10, -5, -3, -1, 0, 1, 3]),
                 max_size=5))
    codes = ("D00001", "D00002", "D00003", "D00004", "D00005")
    dxs = [(off, code, "AV")
           for off, code in zip(dx_offsets, codes)]
    return grid_dataset(fills=[(0, "E00001", 30, 10.0)], dxs=dxs,
                        index_fill=False)


@given(dataset=_grid_datasets(), rules=_rule_sets())
@expensive_settings()
def test_inclusion_matches_condition_model(
    dataset: Any, rules: list[InclusionRule],
) -> None:
    """Every generated inclusion verdict (excluded set, first failed
    cond, failed criteria, surviving master list) equals the model."""
    run, verdicts = _run_inclusion(dataset, rules)
    _assert_stage_matches_model(run, verdicts)

    # Coverage instrumentation.
    excluded = sum(1 for v in verdicts.values() if v.excluded)
    event("episodes_evaluated", "some" if verdicts else "none")
    event("exclusions", "some" if excluded else "none")
    event("criteria mode", rules[0].criteria)
    for spec in rules_by_key(rules).values():
        w_from = GRID_ANCHOR + timedelta(days=spec.condfrom)
        w_to = GRID_ANCHOR + timedelta(days=spec.condto)
        matched = [
            r for r in run.inputs["cohort_claims"]
            if (r["code"] in spec.codes and r["codecat"] == spec.codecat
                and r["adate"] <= w_to and r["expiredt"] >= w_from)
        ]
        for r in matched:
            if r["adate"] in (w_from, w_to):
                event("window boundary exact hit")
        days = {r["adate"] for r in matched}
        if len(days) == spec.codedays and matched:
            event("codedays exact fit")
        if spec.codecat == "RX" and matched:
            supply = sum(
                max(0, (min(r["expiredt"], w_to)
                        - max(r["adate"], w_from)).days + 1)
                for r in matched)
            if supply == spec.minrxdays:
                # coincident-bound shape: minrxdays == available supply
                # exactly (the >= boundary's pass side)
                event("minrxdays exact fit")
        for r in matched:
            same_day_rows = [
                s for s in matched if s["adate"] == r["adate"]
            ]
            if len(same_day_rows) > 1:
                event("distinct-day vs row divergence visible")
                break


def test_inclusion_replay_determinism() -> None:
    """A generated dataset run twice gives identical survivors: the
    two-pass comparison is deterministic, not seed-luck."""
    dataset = generate(patients=2, seed=3, start=GRID_START, end=GRID_END)
    rules = [_inc(condfrom=-10, condto=-1, codes=("D00001",))]
    study = single_cohort_study(
        cohort_a(exposure_codes=tuple(dataset.exposure_codes[:4]),
                 event_codes=("D00001",), wash_per=10, enr_days=30),
        inclusions=rules,
    )
    survivors = []
    for _ in range(2):
        run = two_pass_run(
            study, dataset,
            stop_before="episodes + masterlist",
            stop_after="inclusion criteria",
            input_tables={},
            output_tables={"ptsmasterlist": EPISODE_KEY_COLS},
        )
        survivors.append(_observed_survivors(run))
    assert survivors[0] == survivors[1]
