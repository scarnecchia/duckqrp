"""Builder studies must carry the loader's event care-setting rows.

The trap this file guards, in one line: `event_claims` INNER JOINs
`cfg_care_setting` on (cohortgrp, code) — 30_exposure.sql — and
`register_config` materialises that table from `CohortConfig.
event_care_settings` and nothing else. The study loader therefore always
emits at least one care-setting row per EVENT code per cohort (an absent
CARESETTINGPRINCIPAL expands to the all-settings wildcard
`('**', '*')`, qrp.config.parse_care_setting), but `synth.build_study()`
used to emit none — so every builder-built study silently lost EVERY
outcome claim, and the hand fixture's supposed event existed nowhere
except in its docstring. Packages C, D and F each re-added the expansion
by hand around the gap; this file pins the builder to the loader's
semantics so no future fixture has to.

Four guards, each earning its keep:

1. EXACT ROW REPRESENTATION — the builder's cohorts and an
   independently authored loader input over the same codes must
   produce byte-identical care-setting tuples AND identical rows in
   the registered `cfg_care_setting` table (the representation the
   pipeline actually joins against).
2. A PROPERTY over arbitrary code pools — same equivalence for any
   subset of the builder's vocabularies, both cohort shapes.
3. HAND FIXTURE, SHIPPED PIPELINE — the hand fixture's events must
   actually exist: `event_claims` nonempty with exactly the
   hand-computed rows, `first_event` carrying the hand-computed
   attributed outcome, through the stage harness AND the real
   end-to-end pipeline.
4. CALIBRATION — the same fixture with the care settings stripped
   (the pre-fix builder behaviour, reproduced deliberately) must
   produce ZERO events, proving guard 3 detects exactly this vacuity
   and is not vacuous itself.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

from hypothesis import example, given
from hypothesis import strategies as st

from qrp.config import CohortConfig, StudyConfig, load_study_dict
from qrp.engine import Engine
from qrp.pipeline import register_config

from .harness import StageHarness, full_pipeline_run
from .synth import (
    EVENT_CODE_POOL,
    EXPOSURE_CODE_POOL,
    build_study,
    hand_fixture,
)

# ---------------------------------------------------------------------
# Independently authored loader input. The payload mirrors INPUTS.md's
# row shapes (lowercase literal keys, the hand-written spelling the
# config-semantics oracle also uses) — NOT a call into the loader's
# parse helpers. Its cohortcodes rows carry NO caresettingprincipal, so
# the loader must expand every EVENT code to the all-settings wildcard,
# which is precisely the semantics the builder has to reproduce.
# ---------------------------------------------------------------------

_RUNID = "builder-events-equivalence"


def _exposure_rows(grp: str, codes: tuple[str, ...]) -> list[dict[str, Any]]:
    return [{"group": grp, "indexcriteria": "DEF",
             "code": c, "codecat": "RX", "codetype": "ND"}
            for c in codes]


def _event_rows(grp: str, codes: tuple[str, ...]) -> list[dict[str, Any]]:
    # Outcome codes route on FUPCRITERIA ('DEF'), per ms_cidanum.sas:1684.
    # No CareSettingPrincipal key: the loader must fall through to the
    # wildcard expansion.
    return [{"group": grp, "indexcriteria": "NOT", "fupcriteria": "DEF",
             "code": c, "codecat": "DX", "codetype": "09"}
            for c in codes]


def _split_codes(codes: tuple[str, ...]) -> tuple[tuple[str, ...],
                                                  tuple[str, ...]]:
    """The demo_type2 split the builder documents: first half to
    cohort_a, second to cohort_b, one cohort when fewer than two codes."""
    if len(codes) < 2:
        return codes, ()
    mid = -(-len(codes) // 2)
    return codes[:mid], codes[mid:]


def loader_equivalent_study(
    exposure_codes: tuple[str, ...],
    event_codes: tuple[str, ...],
    *,
    start: date = date(2011, 1, 1),
    end: date = date(2015, 6, 30),
) -> StudyConfig:
    """The same study, expressed as an input file and LOADED.

    Cohort shapes mirror build_study's demo_type2 shape so the two
    configs are comparable cohort-for-cohort; the period matches the
    builder's default. Everything else stays at loader defaults.
    """
    first, second = _split_codes(exposure_codes)
    groups = ["cohort_a"] + (["cohort_b"] if second else [])
    payload: dict[str, Any] = {
        # The pre-resolved scalar shape (config.py reads it before the
        # parameter-row-list fallback, whose first-row rule would drop
        # everything else).
        "qrp_parameters_scalars": {
            "runid": _RUNID, "type": 2,
            "startdate": start.isoformat(), "enddate": end.isoformat(),
        },
        "cohortfile": [{"cohortgrp": g} for g in groups],
        "cohortcodes": (
            _exposure_rows("cohort_a", first)
            + _exposure_rows("cohort_b", second)
            + _event_rows("cohort_a", event_codes)
            + _event_rows("cohort_b", event_codes)
        ),
    }
    return load_study_dict(payload)


def _by_group(study: StudyConfig) -> dict[str, CohortConfig]:
    return {c.cohortgrp: c for c in study.cohorts}


def loader_event_settings(study: StudyConfig, grp: str) -> tuple:
    return _by_group(study)[grp].event_care_settings


def builder_care_setting_tuples(study: StudyConfig) -> tuple:
    rows = [(c.cohortgrp, code, enctype, pdx)
            for c in study.cohorts
            for code, enctype, pdx in c.event_care_settings]
    return tuple(rows)


def cfg_care_setting_rows(study: StudyConfig) -> tuple[tuple[Any, ...], ...]:
    """The care-setting rows `register_config` materialises for a study,
    total-ordered — the exact representation event_claims joins against."""
    engine = Engine(threads=1, verbose=False)
    try:
        register_config(engine, study)
        return tuple(engine.con.execute(
            "SELECT cohortgrp, code, enctype, pdx FROM cfg_care_setting "
            "ORDER BY cohortgrp, code, enctype, pdx").fetchall())
    finally:
        engine.close()


# ---------------------------------------------------------------------
# 1. Exact row representation, builder vs independently authored loader
#    input, over the hand fixture's own vocabulary.
# ---------------------------------------------------------------------

def test_builder_care_settings_match_loader_exact() -> None:
    exposure = ("E00001", "E00021")
    events = ("D00001", "D00002")
    builder = build_study(exposure, events)
    loader = loader_equivalent_study(exposure, events)

    assert _by_group(builder).keys() == _by_group(loader).keys()
    for grp, bc in _by_group(builder).items():
        # The tuples, in order — loader file-row order equals builder
        # event-code order for the same codes.
        assert bc.event_care_settings == loader_event_settings(loader, grp)
        # And the codes they name are exactly the cohort's event codes.
        assert [code for code, _e, _p in bc.event_care_settings] \
            == [code for code, _cc, _ct, _sup in bc.event_codes]

    # The literal wildcard pin: every row is the all-settings pair, and
    # there is exactly one row per event code per cohort.
    assert builder_care_setting_tuples(builder) == (
        ("cohort_a", "D00001", "**", "*"),
        ("cohort_a", "D00002", "**", "*"),
        ("cohort_b", "D00001", "**", "*"),
        ("cohort_b", "D00002", "**", "*"),
    )
    # The registered cfg_care_setting table is IDENTICAL for both
    # studies — the rows the pipeline will actually inner-join.
    assert cfg_care_setting_rows(builder) == cfg_care_setting_rows(loader)


# ---------------------------------------------------------------------
# 2. Property: the equivalence holds for arbitrary code pools.
# ---------------------------------------------------------------------

_exposure_strategy = st.lists(
    st.sampled_from(EXPOSURE_CODE_POOL), min_size=1, max_size=8, unique=True)
_event_strategy = st.lists(
    st.sampled_from(EVENT_CODE_POOL), max_size=6, unique=True)


@given(exposure=_exposure_strategy, events=_event_strategy)
@example(exposure=["E00001"], events=[])            # no events at all
@example(exposure=["E00001"], events=["D00001"])    # one cohort, one event
@example(exposure=["E00001", "E00021"], events=["D00001", "D00002"])
def test_builder_matches_loader_for_arbitrary_pools(
    exposure: list[str], events: list[str],
) -> None:
    builder = build_study(tuple(exposure), tuple(events))
    loader = loader_equivalent_study(tuple(exposure), tuple(events))
    assert _by_group(builder).keys() == _by_group(loader).keys()
    for grp, bc in _by_group(builder).items():
        expected = tuple((code, "**", "*") for code in events)
        assert bc.event_care_settings == expected, grp
        assert bc.event_care_settings \
            == loader_event_settings(loader, grp), grp


# ---------------------------------------------------------------------
# 3. Hand fixture: nonempty, hand-computed event attribution through the
#    shipped stages and the full pipeline.
#
#    Hand calculation, for review:
#    * P1's episode: fills 2014-03-10 and 2014-04-09 (30 days each);
#      stockpiled supply runs 2014-03-10..2014-05-08, so the at-risk
#      window is [2014-03-10, 2014-05-08] (no FUT, no death, no dose).
#    * Event extraction is enrollment-blind (query-period bounded), so
#      BOTH of P1's diagnoses become event claims in EACH cohort:
#      D00002 on 2014-05-03 and D00001 on 2014-06-01 — four rows.
#    * Only 2014-05-03 falls inside the at-risk window, so first_event
#      carries exactly (cohort_a, P1, index 2014-03-10, event
#      2014-05-03, numevents 1). cohort_b's member is P4, who has no
#      event claims, so cohort_b attributes nothing.
# ---------------------------------------------------------------------

# (cohortgrp, patid, adate, code, codetype) — event_claims' column order.
_HAND_EVENT_CLAIMS = (
    ("cohort_a", 1, date(2014, 5, 3), "D00002", "09"),
    ("cohort_a", 1, date(2014, 6, 1), "D00001", "09"),
    ("cohort_b", 1, date(2014, 5, 3), "D00002", "09"),
    ("cohort_b", 1, date(2014, 6, 1), "D00001", "09"),
)
# (cohortgrp, patid, indexdt, eventdt, numevents) — first_event's order.
_HAND_FIRST_EVENT = (("cohort_a", 1, date(2014, 3, 10), date(2014, 5, 3), 1),)


def test_hand_fixture_event_attribution_through_stages() -> None:
    dataset, study = hand_fixture()
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("follow-up + events")

        # The care-setting rows the builder now emits, as registered.
        n_settings = harness.engine.con.execute(
            "SELECT count(*) FROM cfg_care_setting").fetchone()
        assert n_settings is not None and int(n_settings[0]) == 4

        claims = harness.engine.con.execute(
            "SELECT cohortgrp, patid, adate, code, codetype "
            "FROM event_claims ORDER BY cohortgrp, patid, adate, code"
        ).fetchall()
        assert tuple(claims) == _HAND_EVENT_CLAIMS

        first = harness.engine.con.execute(
            "SELECT cohortgrp, patid, indexdt, eventdt, numevents "
            "FROM first_event ORDER BY cohortgrp, patid"
        ).fetchall()
        assert tuple(first) == _HAND_FIRST_EVENT

        # The outcome columns the deliverable carries, hand-computed:
        # P1's followuptime runs index -> event inclusive (55 days);
        # P4's episode ends at supply exhaustion 2014-05-13 with no
        # event (60 days of person-time, exit 'exposure_end').
        final = harness.engine.con.execute(
            'SELECT "group", patid, feventdt, "event", event_flag, '
            "numevents, followuptime, exit_reason "
            'FROM cohort_final ORDER BY "group", patid'
        ).fetchall()
        assert tuple(final) == (
            ("cohort_a", 1, date(2014, 5, 3), 1, "Y", 1, 55, "event"),
            ("cohort_b", 4, None, 0, "N", 0, 60, "exposure_end"),
        )


def test_hand_fixture_event_attribution_through_full_pipeline() -> None:
    """The same hand-computed facts through qrp.run() itself — no
    harness glue between the fixture and the shipped pipeline."""
    dataset, study = hand_fixture()
    with full_pipeline_run(dataset, study, write_outputs=False) as result:
        con = result.engine.con
        claims = con.execute(
            "SELECT cohortgrp, patid, adate, code, codetype "
            "FROM event_claims ORDER BY cohortgrp, patid, adate, code"
        ).fetchall()
        assert tuple(claims) == _HAND_EVENT_CLAIMS
        first = con.execute(
            "SELECT cohortgrp, patid, indexdt, eventdt, numevents "
            "FROM first_event ORDER BY cohortgrp, patid"
        ).fetchall()
        assert tuple(first) == _HAND_FIRST_EVENT
        final = con.execute(
            'SELECT "group", patid, feventdt, "event", event_flag, '
            "numevents, exit_reason FROM cohort_final ORDER BY \"group\", patid"
        ).fetchall()
        # numevents is NULL, not 0, on an event-free episode through the
        # FULL pipeline: 62_mstr_wide.sql applies nullif(numevents, 0) —
        # documented SAS parity — and the stage harness above stops
        # before that stage, which is why it sees 0 there.
        assert tuple(final) == (
            ("cohort_a", 1, date(2014, 5, 3), 1, "Y", 1, "event"),
            ("cohort_b", 4, None, 0, "N", None, "exposure_end"),
        )


# ---------------------------------------------------------------------
# 4. Calibration: the guard detects the trap it exists for. Stripping
#    the care-setting rows reproduces the PRE-FIX builder exactly; the
#    same fixture through the same stages must then attribute nothing.
# ---------------------------------------------------------------------

def test_stripped_care_settings_reproduce_the_vacuity() -> None:
    _, study = hand_fixture()
    stripped = replace(
        study,
        cohorts=tuple(
            replace(c, event_care_settings=()) for c in study.cohorts),
    )
    assert all(c.event_care_settings == () for c in stripped.cohorts)
    dataset, _ = hand_fixture()
    with StageHarness(stripped) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("follow-up + events")
        assert harness.engine.count("event_claims") == 0
        assert harness.engine.count("first_event") == 0
        # And the deliverable's outcome columns go flat with them.
        events = harness.engine.con.execute(
            'SELECT "event", numevents FROM cohort_final').fetchall()
        assert set(events) == {(0, 0)}
