"""Package C -- follow-up, blackout, and censoring (60_followup.sql,
after 55_dose_censor.sql) vs the day model.

Contracts under test (the stage SQL's header comments + 50_episodes):

* follow-up washout: an outcome (or IOC) claim strictly BEFORE the
  index date disqualifies the episode when it lies in
  [indexdt - fup_wash, indexdt - 1]; fup_wash <= 0 disables the
  washout; fup_wash NULL means ANY prior event disqualifies (SAS's
  99999 sentinel);
* blackout: any outcome claim in [indexdt, indexdt + blackout - 1]
  drops the episode outright -- day 1 is the index day itself;
* both flags are evaluated on the level-16 survivors
  (episode_criteria WHERE min_enr_days_met), and cohort_final keeps
  only episodes passing both;
* candidate outcome events run over [atrisindexdt, episodeenddt]
  (episodeenddt already dose-censor truncated); IEV/EEV rules drop
  individual EVENT dates (per distinct patid/eventdt), never the
  episode; first_event is the earliest surviving event, numevents
  counts surviving event CLAIM rows;
* followuptime = max(0, days(index -> min(episodeenddt, enr_end,
  FEventDt)) - blackout_per - at_risk_start + 1); episodelength =
  days(index -> min(episodeenddt, enr_end)) + 1; episodeenddt_censor
  = min(enr_end, death, query end, censor end) with timetocensor one
  more; person_days spans [atrisindexdt, min(FEventDt or end, end)]
  with NO floor (can go negative when at_risk_start exceeds the
  episode -- observed behavior, open question OC-3);
* exit_reason precedence is the stage's CASE order: event > death >
  disenrollment > study_end > exposure_end.

Oracles: `oracles/c_models` re-derives every value with plain date
arithmetic; nothing here transliterates the shipped SQL.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from datetime import date, timedelta
from typing import Any

from hypothesis import event, given
from hypothesis import strategies as st

from qrp.config import InclusionRule

from .conftest import expensive_settings
from .oracles.c_glue import (
    GRID_ANCHOR,
    GRID_END,
    cohort_a,
    grid_dataset,
    rules_by_key,
    single_cohort_study,
    two_pass_run,
)
from .oracles.c_models import (
    Episode,
    EpisodeOutcome,
    FollowUpConfig,
    episode_outcome,
)
from .strategies import SEEDS, generate_options
from .synth import generate

_EPI_COLS = (
    "cohortgrp", "patid", "indexdt", "episodeenddt", "enr_end", "deathdt",
    "atriskindexdt",
)
_EVENT_COLS = ("cohortgrp", "patid", "adate", "code", "codetype")
_CLAIM_COLS = (
    "patid", "adate", "expiredt", "code", "codecat", "rxsup", "rxamt",
)
_FINAL_COLS = (
    "cohortgrp", "patid", "indexdt", "feventdt", "event", "event_flag",
    "episodelength", "episodeenddt_censor", "timetocensor", "followuptime",
    "has_event", "numevents", "person_days", "exit_reason",
)
_HOI_COLS = (
    "cohortgrp", "patid", "indexdt", "hoi_incidence_met",
    "hoi_blackout_met",
)
_FLAG_COLS = (
    "cohortgrp", "patid", "indexdt", "fup_elig", "fup_dth", "fup_qryend",
    "fup_dpend", "fup_episend", "fup_spec", "fup_event", "cens_elig",
    "cens_dth", "cens_qryend", "cens_dpend",
)
_FIRST_COLS = ("cohortgrp", "patid", "indexdt", "eventdt", "numevents")


def _inc(
    *, criteria: str = "IEV", cond: int = 1, subcond: int = 1,
    codecat: str = "DX", condfrom: int = -1, condto: int = 1,
    codedays: int = 1, codes: tuple[str, ...] = ("D00002",),
) -> InclusionRule:
    return InclusionRule(
        cohortgrp="cohort_a", cond=cond, subcond=subcond,
        criteria=criteria, codecat=codecat, condfrom=condfrom,
        condto=condto, codedays=codedays, codes=codes,
    )


def _run_followup(
    dataset: Any,
    *,
    fup_wash_per: int | None = 0,
    blackout_per: int = 0,
    at_risk_start: int = 0,
    event_count: int = 0,
    max_cum_dose: float | None = None,
    cum_dose_per: int | None = None,
    ie_rules: list[InclusionRule] | None = None,
    ioc_codes: tuple = (),
) -> tuple[Any, dict[tuple[str, int, date], EpisodeOutcome]]:
    """Run through 'follow-up + events'; return the pass object plus
    the model outcome per post-dose-censor episode."""
    cohort = cohort_a(
        exposure_codes=("E00001",), event_codes=("D00001",),
        wash_per=10, enr_days=30,
        fup_wash_per=fup_wash_per,
        blackout_per=blackout_per,
        at_risk_start=at_risk_start,
        event_count=event_count,
        ioc_codes=ioc_codes,
    )
    if max_cum_dose is not None:
        cohort = dc_replace(cohort, max_cum_dose=max_cum_dose,
                            cum_dose_per=cum_dose_per)
    study = single_cohort_study(
        cohort, inclusions=tuple(ie_rules or ()),
        code_strength=(("E00001", 1.0),),
    )
    input_tables: dict[str, tuple[str, ...]] = {
        "ptsmasterlist": _EPI_COLS,
        "event_claims": _EVENT_COLS,
        "cohort_claims": _CLAIM_COLS,
    }
    if ioc_codes:
        # The washout's IOC branch reads the cdm tables directly; the
        # model gets the same rows through the snapshot.
        input_tables["cdm_diagnosis"] = (
            "patid", "adate", "code", "codetype")
    run = two_pass_run(
        study, dataset,
        stop_before="dose censoring",
        stop_after="follow-up + events",
        input_tables=input_tables,
        output_tables={
            "episode_hoi": _HOI_COLS,
            "episode_flags": _FLAG_COLS,
            "first_event": _FIRST_COLS,
            "cohort_final": _FINAL_COLS,
        },
    )
    ioc_dx = {c for c, cat, _ct, _sup in ioc_codes if cat == "DX"}
    ioc_cfg = {c: ct for c, _cat, ct, _sup in ioc_codes if c in ioc_dx}
    ioc_by: dict[tuple[str, int], list[date]] = {}
    for r in run.inputs.get("cdm_diagnosis", ()):
        code = str(r["code"])
        if code not in ioc_dx:
            continue
        ct = ioc_cfg.get(code, "")
        src_ct = r["codetype"]
        if ct == "" or ct is None or src_ct is None \
                or str(src_ct).upper() == str(ct).upper():
            ioc_by.setdefault(("cohort_a", int(r["patid"])),
                              []).append(r["adate"])
    return run, _model_outcomes(run.inputs, fup_wash_per, blackout_per,
                                at_risk_start, ie_rules, ioc_by=ioc_by)


def _model_outcomes(
    inputs: dict[str, tuple[dict[str, Any], ...]],
    fup_wash: int | None,
    blackout: int,
    at_risk_start: int,
    ie_rules: list[InclusionRule] | None,
    *,
    end_date: date = GRID_END,
    strengths: dict[str, float] | None = None,
    ioc_by: dict[tuple[str, int], list[date]] | None = None,
) -> dict[tuple[str, int, date], EpisodeOutcome]:
    """The model side of the comparison, over the snapshotted inputs."""
    from .oracles.c_models import IncClaim

    claims = [
        IncClaim(int(r["patid"]), r["adate"], r["expiredt"],
                 str(r["code"]), str(r["codecat"]), r["rxsup"], r["rxamt"])
        for r in inputs["cohort_claims"]
    ]
    rules_map = rules_by_key(ie_rules) if ie_rules else None
    cfg = FollowUpConfig(
        fup_wash_per=fup_wash, blackout_per=blackout,
        at_risk_start=at_risk_start, end_date=end_date, censor_date=end_date,
    )
    events_by: dict[tuple[str, int], list[date]] = {}
    for r in inputs["event_claims"]:
        events_by.setdefault((str(r["cohortgrp"]), int(r["patid"])),
                             []).append(r["adate"])
    outcomes: dict[tuple[str, int, date], EpisodeOutcome] = {}
    for r in inputs["ptsmasterlist"]:
        ep = Episode(str(r["cohortgrp"]), int(r["patid"]), r["indexdt"],
                     r["episodeenddt"])
        key = (ep.cohortgrp, ep.patid)
        outcomes[(ep.cohortgrp, ep.patid, ep.indexdt)] = episode_outcome(
            ep, enr_end=r["enr_end"], deathdt=r["deathdt"],
            atriskindexdt=r["atriskindexdt"],
            events=events_by.get(key, []),
            ioc_dates=(ioc_by or {}).get(key, []),
            cfg=cfg, ie_rules=rules_map,
            inc_claims=claims, strengths=strengths or {},
        )
    return outcomes


def _assert_followup_matches_model(run: Any, outcomes: dict) -> None:
    """cohort_final membership and columns, and first_event rows."""
    final = {
        (str(r["cohortgrp"]), int(r["patid"]), r["indexdt"]): r
        for r in run.outputs["cohort_final"]
    }
    expected_kept = {k for k, o in outcomes.items() if o.kept}
    assert set(final) == expected_kept, (
        sorted(str(s) for s in set(final) ^ expected_kept),
        "cohort_final membership differs from the model")
    for key, r in final.items():
        o = outcomes[key]
        assert r["feventdt"] == o.feventdt, (key, r["feventdt"], o)
        assert r["numevents"] == o.numevents, (key, r["numevents"], o)
        assert bool(r["event"]) is (o.feventdt is not None), (key, o)
        assert r["followuptime"] == o.followuptime, (key, o)
        assert r["episodelength"] == o.episodelength, (key, o)
        assert r["episodeenddt_censor"] == o.episodeenddt_censor, (key, o)
        assert r["timetocensor"] == o.timetocensor, (key, o)
        assert r["person_days"] == o.person_days, (key, o)
        assert r["exit_reason"] == o.exit_reason, (
            key, r["exit_reason"], o)

    fe = {
        (str(r["cohortgrp"]), int(r["patid"]), r["indexdt"]):
            (r["eventdt"], r["numevents"])
        for r in run.outputs["first_event"]
    }
    expected_fe = {
        k: (o.feventdt, o.numevents) for k, o in outcomes.items()
        if o.feventdt is not None
    }
    assert fe == expected_fe, (fe, expected_fe)


# ---------------------------------------------------------------------
# Hand-computed cases
# ---------------------------------------------------------------------


def test_followup_washout_boundaries() -> None:
    """The washout window [indexdt - fup_wash, indexdt - 1] is CLOSED:
    a prior event exactly at the horizon disqualifies, one day before
    it does not. fup_wash=0 disables the washout; NULL means ANY prior
    event disqualifies.

    Fixture: outcome event at day -7, index fill at day 0.
    """
    dataset = grid_dataset(dxs=[(-7, "D00001", "AV")])
    cases = (
        (None, False),   # any prior event disqualifies
        (0, True),       # washout disabled
        (7, False),      # horizon -7: event AT the horizon disqualifies
        (8, False),      # horizon -8: event still inside
        (6, True),       # horizon -6: event one day BEFORE the horizon
    )
    for wash, expect_kept in cases:
        run, outcomes = _run_followup(dataset, fup_wash_per=wash)
        _assert_followup_matches_model(run, outcomes)
        index_keys = [k for k in outcomes if k[2] == GRID_ANCHOR]
        assert index_keys, (wash, outcomes)
        assert outcomes[index_keys[0]].kept is expect_kept, (
            wash, outcomes[index_keys[0]])


def test_followup_washout_prior_event_inside_window() -> None:
    """A prior event strictly inside the washout window but not at the
    boundary (-6 against wash 8, horizon -8) disqualifies."""
    dataset = grid_dataset(dxs=[(-6, "D00001", "AV")])
    run, outcomes = _run_followup(dataset, fup_wash_per=8)
    _assert_followup_matches_model(run, outcomes)
    index_keys = [k for k in outcomes if k[2] == GRID_ANCHOR]
    assert index_keys and not outcomes[index_keys[0]].kept, outcomes


def test_followup_blackout_includes_the_index_day() -> None:
    """Blackout window [indexdt, indexdt + blackout - 1]: with
    blackout_per=1 an event ON the index day drops the episode;
    with blackout_per=2 an event at +1 drops it and one at +2 (just
    past the window) does not; blackout_per=0 disables entirely."""
    cases = (
        (1, [(0, "D00001", "AV")], False),
        (2, [(1, "D00001", "AV")], False),
        (2, [(2, "D00001", "AV")], True),
        (0, [(0, "D00001", "AV")], True),
    )
    for blackout, dxs, expect_kept in cases:
        dataset = grid_dataset(dxs=dxs)
        run, outcomes = _run_followup(dataset, blackout_per=blackout)
        _assert_followup_matches_model(run, outcomes)
        index_keys = [k for k in outcomes if k[2] == GRID_ANCHOR]
        assert index_keys, (blackout, dxs, outcomes)
        assert outcomes[index_keys[0]].kept is expect_kept, (
            blackout, dxs, outcomes[index_keys[0]])
        if expect_kept and dxs[0][0] >= 2:
            # an event past the blackout is still attributed
            o = outcomes[index_keys[0]]
            assert o.feventdt is not None and o.exit_reason == "event", o


def test_followup_zero_length_followup_via_at_risk_start() -> None:
    """followuptime floors at 0: at_risk_start=2 on a one-day episode
    gives (0 - 0 - 2 + 1) = -1 -> 0. person_days is floored at 0 too,
    since the C-3 fix (inv/0f462p/fix3-c3): zero days at risk, not -1 —
    matching SAS, whose at-risk span quantity is Max(0, ...)
    (ms_finalizeptsmasterlist.sas:305). Resolves open question OC-3."""
    dataset = grid_dataset(fills=[(0, "E00001", 1, 2.0)], index_fill=False)
    run, outcomes = _run_followup(dataset, at_risk_start=2)
    _assert_followup_matches_model(run, outcomes)
    index_keys = [k for k in outcomes if k[2] == GRID_ANCHOR]
    assert index_keys, outcomes
    o = outcomes[index_keys[0]]
    assert o.kept and o.feventdt is None, o
    assert o.followuptime == 0 and o.exit_reason == "exposure_end", o
    assert o.episodelength == 1 and o.person_days == 0, o


def test_followup_same_day_competing_reasons() -> None:
    """Competing censor reasons on the SAME day resolve by the
    documented CASE order: event beats death; death beats
    disenrollment and study_end; a dose-censor truncation that equals
    no recognized end falls through to 'exposure_end'.

    The fup_* / cens_* flags are INDEPENDENT equality tests; a
    mid-episode death sets both death flags and neither elig flag.
    """
    # event and death on the same day (+5)
    ds = grid_dataset(fills=[(0, "E00001", 30, 2.0)],
                      dxs=[(5, "D00001", "AV")],
                      death_day=5, index_fill=False)
    run, outcomes = _run_followup(ds)
    _assert_followup_matches_model(run, outcomes)
    o = outcomes[("cohort_a", 1, GRID_ANCHOR)]
    assert o.exit_reason == "event", o

    # death ON the episode end (30-day fill -> end at +29), no event
    ds = grid_dataset(fills=[(0, "E00001", 30, 2.0)], death_day=29,
                      index_fill=False)
    run, outcomes = _run_followup(ds)
    _assert_followup_matches_model(run, outcomes)
    o = outcomes[("cohort_a", 1, GRID_ANCHOR)]
    assert o.exit_reason == "death", o
    flags = [r for r in run.outputs["episode_flags"]
             if r["indexdt"] == GRID_ANCHOR]
    assert flags, "episode_flags missing an episode"
    row = flags[0]
    assert row["fup_dth"] == 1 and row["cens_dth"] == 1, row
    assert row["fup_elig"] == 0 and row["cens_elig"] == 0, row
    assert row["fup_qryend"] == 0 and row["cens_qryend"] == 0, row

    # dose-censor truncation BEFORE death: the truncated end matches
    # no recognized end -> 'exposure_end' (documented CASE fall-through)
    ds = grid_dataset(fills=[(0, "E00001", 30, 2.0),
                             (2, "E00001", 5, 200.0)],
                      death_day=10, index_fill=False)
    run, outcomes = _run_followup(ds, max_cum_dose=100.0, cum_dose_per=15)
    _assert_followup_matches_model(run, outcomes)
    o = outcomes[("cohort_a", 1, GRID_ANCHOR)]
    assert o.exit_reason == "exposure_end", o
    assert o.feventdt is None, o
    # the censor end stops at DEATH (2014-02-25), not at the query end:
    # min(enr_end, death, query end, censor end)
    assert o.episodeenddt_censor == GRID_ANCHOR + timedelta(days=10), o
    assert o.timetocensor == 11 and o.episodelength == 7, o


def test_followup_iev_drops_event_not_episode() -> None:
    """IEV removes one EVENT, not the episode: with events at +3 and
    +7 and an IEV rule requiring D00002 within [-1, +1] of the event
    (D00002 sits at +4 only), the +7 event is dropped; feventdt stays
    +3 and the episode survives with numevents=1. The EEV mirror
    excludes the +3 event and attributes +7 instead."""
    event3 = GRID_ANCHOR + timedelta(days=3)
    event7 = GRID_ANCHOR + timedelta(days=7)
    ds = grid_dataset(fills=[(0, "E00001", 30, 2.0)],
                      dxs=[(3, "D00001", "AV"), (7, "D00001", "AV"),
                           (4, "D00002", "AV")],
                      index_fill=False)
    run, outcomes = _run_followup(ds)
    _assert_followup_matches_model(run, outcomes)
    o = outcomes[("cohort_a", 1, GRID_ANCHOR)]
    assert o.kept and o.feventdt == event3 and o.numevents == 2, o

    rule = _inc(criteria="IEV", codes=("D00002",), condfrom=-1, condto=1)
    run, outcomes = _run_followup(ds, ie_rules=[rule])
    _assert_followup_matches_model(run, outcomes)
    o = outcomes[("cohort_a", 1, GRID_ANCHOR)]
    assert o.kept, "IEV must not drop the episode"
    assert o.feventdt == event3 and o.numevents == 1, o

    eev = _inc(criteria="EEV", codes=("D00002",), condfrom=-1, condto=1)
    run, outcomes = _run_followup(ds, ie_rules=[eev])
    _assert_followup_matches_model(run, outcomes)
    o = outcomes[("cohort_a", 1, GRID_ANCHOR)]
    assert o.kept and o.numevents == 1, o
    assert o.feventdt == event7, o


def test_followup_ioc_claim_disqualifies_like_an_event() -> None:
    """IOC codes never define events; they only disqualify through the
    follow-up washout. An X-code diagnosis inside the wash window
    drops the episode exactly as an outcome code would."""
    ioc = (("X00001", "DX", "09", None),)
    ds = grid_dataset(dxs=[(-3, "X00001", "AV")])
    run, outcomes = _run_followup(ds, fup_wash_per=7, ioc_codes=ioc)
    _assert_followup_matches_model(run, outcomes)
    index_keys = [k for k in outcomes if k[2] == GRID_ANCHOR]
    assert index_keys and not outcomes[index_keys[0]].kept, outcomes

    # washout disabled: the IOC claim does not disqualify
    run, outcomes = _run_followup(ds, fup_wash_per=0, ioc_codes=ioc)
    _assert_followup_matches_model(run, outcomes)
    index_keys = [k for k in outcomes if k[2] == GRID_ANCHOR]
    assert index_keys and outcomes[index_keys[0]].kept, outcomes


# ---------------------------------------------------------------------
# Interaction: same logical event, different claim sets
# ---------------------------------------------------------------------


def test_followup_same_event_different_claim_counts() -> None:
    """MANDATORY interaction: the same event day reached by one claim
    vs three same-day claims. feventdt and followuptime are identical
    in every variant; numevents tracks the claim count under
    event_count=0 and collapses to the day under event_count=2. The
    model predicts each cell."""
    one = grid_dataset(fills=[(0, "E00001", 30, 2.0)],
                       dxs=[(5, "D00001", "AV")], index_fill=False)
    three = grid_dataset(fills=[(0, "E00001", 30, 2.0)],
                         dxs=[(5, "D00001", "AV"), (5, "D00001", "OA"),
                              (5, "D00001", "IP")],
                         index_fill=False)
    for dataset, count, expect_num in ((one, 0, 1), (three, 0, 3),
                                       (one, 2, 1), (three, 2, 1)):
        run, outcomes = _run_followup(dataset, event_count=count)
        _assert_followup_matches_model(run, outcomes)
        o = outcomes[("cohort_a", 1, GRID_ANCHOR)]
        assert o.numevents == expect_num, (count, o)
        assert o.feventdt is not None and o.followuptime == 6, (count, o)


# ---------------------------------------------------------------------
# Generated property
# ---------------------------------------------------------------------

_FUP_WASH = st.sampled_from([None, 0, 3, 10])
_BLACKOUT = st.sampled_from([0, 1, 3])


@st.composite
def _followup_scenarios(draw: st.DrawFn):
    """Small generated studies: half grid fixtures (whose outcome
    events sit near the index fill by construction, so attribution is
    exercised densely) and half arbitrary generated datasets (breadth
    over patient mixes, ties, and random deaths)."""
    if draw(st.booleans()):
        dx_offsets = draw(st.lists(
            st.sampled_from([-9, -4, -2, 1, 2, 3, 5, 8, 20]), max_size=4))
        codes = ("D00001", "D00002")
        dxs = [(off, codes[i % 2], "AV")
               for i, off in enumerate(dx_offsets)]
        dataset = grid_dataset(
            fills=[(0, "E00001", 30, 2.0)], dxs=dxs, index_fill=False)
    else:
        dataset = draw(st.builds(
            generate,
            patients=st.integers(min_value=1, max_value=3),
            seed=SEEDS,
            start=st.just(date(2014, 1, 1)),
            end=st.just(date(2014, 4, 30)),
            options=generate_options(),
        ))
    fup_wash = draw(_FUP_WASH)
    blackout = draw(_BLACKOUT)
    rule = draw(st.sampled_from([None, "IEV", "EEV"]))
    max_cum = draw(st.sampled_from([None, 5.0]))
    ie_rules = (
        [_inc(criteria=rule, codes=("D00001",), condfrom=-2, condto=1)]
        if rule else [])
    return dataset, fup_wash, blackout, ie_rules, max_cum


@given(data=_followup_scenarios())
@expensive_settings()
def test_followup_and_censoring_match_day_model(data: Any) -> None:
    """Every generated follow-up decision (hoi flags, cohort_final
    membership, first event, follow-up time, person time, exit reason)
    equals the day model."""
    dataset, fup_wash, blackout, ie_rules, max_cum = data
    # All 40 generated exposure codes, so the generator's ~60%-exposure
    # fills all become exposure claims; with a 4-code cohort only ~6%
    # of fills qualified and 90% of examples had no episode at all.
    codes = tuple(dataset.exposure_codes)
    cohort = cohort_a(
        exposure_codes=codes,
        event_codes=tuple(dataset.event_codes),
        wash_per=10, enr_days=30,
        fup_wash_per=fup_wash, blackout_per=blackout,
        event_count=2,
    )
    if max_cum is not None:
        cohort = dc_replace(cohort, max_cum_dose=max_cum, cum_dose_per=15)
    study = single_cohort_study(
        cohort, inclusions=tuple(ie_rules),
        code_strength=tuple((c, 1.0) for c in codes),
    )
    run = two_pass_run(
        study, dataset,
        stop_before="dose censoring",
        stop_after="follow-up + events",
        input_tables={
            "ptsmasterlist": _EPI_COLS,
            "event_claims": _EVENT_COLS,
            "cohort_claims": _CLAIM_COLS,
        },
        output_tables={
            "episode_hoi": _HOI_COLS,
            "first_event": _FIRST_COLS,
            "cohort_final": _FINAL_COLS,
        },
    )
    outcomes = _model_outcomes(
        run.inputs, fup_wash, blackout, 0, ie_rules,
        end_date=study.end_date,
        strengths={c: 1.0 for c in codes},
    )
    _assert_followup_matches_model(run, outcomes)

    # Coverage instrumentation.
    kept = [o for o in outcomes.values() if o.kept]
    event("episodes", "some" if outcomes else "none")
    event("kept", "some" if kept else "none")
    dropped = [o for o in outcomes.values() if not o.kept]
    event("washout-or-blackout drops", "some" if dropped else "none")
    with_event = [o for o in kept if o.feventdt is not None]
    event("event attribution", "some" if with_event else "none")
    reasons = [o.exit_reason for o in kept]
    for reason in ("death", "disenrollment", "study_end"):
        if reason in reasons:
            event("competing censor reason exercised", reason)
    if fup_wash is None:
        event("washout mode", "null")
    elif fup_wash == 0:
        event("washout mode", "disabled")
    else:
        event("washout mode", "windowed")
