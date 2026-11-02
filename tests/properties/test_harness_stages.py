"""Self-tests for the stage harness: trust it before using it.

The harness is only load-bearing if it (a) reproduces hand-calculated
results on an explicit fixture, (b) gives identical results on isolated
repeated runs, (c) cannot drift from STAGES / plan_stages / the shipped
SQL, and (d) agrees with the real end-to-end pipeline (that last one
lives in test_harness_pipeline.py).
"""

from __future__ import annotations

import hashlib
from datetime import date

import pytest

from qrp.config import Covariate, StudyConfig
from qrp.pipeline import STAGES, plan_stages

from .harness import (
    STAGE_BY_NAME,
    STAGE_ORDER,
    STAGE_OUTPUTS,
    STAGE_PREREQUISITES,
    StageHarness,
    fetch_rows,
    stage_closure,
)
from .synth import build_study, generate, hand_fixture


def _digest(rows: tuple[tuple[object, ...], ...]) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(repr(row).encode())
    return h.hexdigest()


# ---------------------------------------------------------------------
# Hand-calculated fixture
# ---------------------------------------------------------------------

def test_harness_hand_fixture_matches_hand_calculation() -> None:
    """Explicit fixture, expectations derivable on paper.

    P1: two E00001 fills (2014-03-10, 2014-04-09) -> one episode
        (the second fill sits inside the 183-day washout of the first
        index date) and one attributed outcome; an ambulatory claim
        inside their 2014-05-01..05 IP stay is enveloped to IP/'X'.
    P2: noise drug only -> no cohort membership; dies 2015-01-01.
    P3: E00001 fill dated before their enrollment starts -> excluded.
    P4: duplicate demographic rows and a same-day/same-supply E00021
        tie pair -> one episode in cohort_b, tie-breaks exercised.

    Events: the builder's studies carry the loader's wildcard care
    settings, so P1's two diagnoses become FOUR event_claims rows (both
    codes, both cohorts; extraction is query-period-bounded, not
    enrollment-bounded), of which exactly ONE attributes — the episode's
    supply ends 2014-05-08, so first_event is D00002 on 2014-05-03 and
    the 2014-06-01 claim lands outside the window. A builder that lost
    the care-setting rows would leave ALL of these empty (the vacuity
    test_property_builder_events.py calibrates against).
    """
    dataset, study = hand_fixture()
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("attrition + denominators")

        # One episode per exposed patient, in the right cohort.
        mstr = harness.engine.con.execute(
            "SELECT cohortgrp, patid, COUNT(*) FROM ptsmasterlist "
            "GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall()
        assert mstr == [("cohort_a", 1, 1), ("cohort_b", 4, 1)]
        # P5 (duplicate demographics) never becomes a member.
        row = harness.engine.con.execute(
            "SELECT count(*) FROM ptsmasterlist WHERE patid = 5"
        ).fetchone()
        assert row is not None and int(row[0]) == 0
        # ...but IS flagged by normalize.
        assert harness.engine.count("demographics_multi") == 1

        # Denominators cover both cohorts after the attrition stage
        # (the table carries one row per cohort per stratum level).
        denom = harness.engine.con.execute(
            "SELECT DISTINCT cohortgrp FROM denominators ORDER BY 1"
        ).fetchall()
        assert denom == [("cohort_a",), ("cohort_b",)]

        # The cohort_claims narrow (glue) is defined after episodes.
        kind = harness.engine.con.execute(
            "SELECT table_type FROM information_schema.tables "
            "WHERE table_name = 'cohort_claims'"
        ).fetchall()
        assert kind == [("VIEW",)]

        # Enveloping is visible at the cdm layer, as in the AC.1 test,
        # so the deeper stages inherited the corrected claim set. The
        # normalized claim code column is `code`, not `dx`.
        env = harness.engine.con.execute(
            "SELECT enctype, pdx FROM cdm_diagnosis WHERE code = 'D00002'"
        ).fetchall()
        assert env == [("IP", "X")]

        # Event attribution is real, not vacuous: "follow-up + events"
        # runs before "attrition + denominators", so both tables exist.
        # Four extracted claims (both codes x both cohorts), one
        # attributed outcome, hand-computed.
        n_claims = harness.engine.con.execute(
            "SELECT count(*) FROM event_claims").fetchone()
        assert n_claims is not None and int(n_claims[0]) == 4
        first = harness.engine.con.execute(
            "SELECT cohortgrp, patid, indexdt, eventdt, numevents "
            "FROM first_event ORDER BY cohortgrp, patid"
        ).fetchall()
        assert first == [("cohort_a", 1, date(2014, 3, 10),
                          date(2014, 5, 3), 1)]


# ---------------------------------------------------------------------
# Isolated repeated runs
# ---------------------------------------------------------------------

@pytest.mark.parametrize("use_generated", [False, True])
def test_harness_repeated_runs_are_identical(use_generated: bool) -> None:
    """Two fresh harnesses over the same fixture produce identical
    tables — no state carries between runs."""
    if use_generated:
        study: StudyConfig = build_study(("E00001", "E00021"))
        dataset = generate(patients=3, seed=7)
    else:
        dataset, study = hand_fixture()

    tables = ("cdm_dispensing", "exposure_claims", "ptsmasterlist")
    digests: list[dict[str, str]] = []
    for _ in range(2):
        with StageHarness(study) as harness:
            harness.attach_dataset(dataset)
            harness.run_through("episodes + masterlist")
            digests.append(
                {t: _digest(fetch_rows(harness.engine, t)) for t in tables})
    assert digests[0] == digests[1]


# ---------------------------------------------------------------------
# Prerequisite model
# ---------------------------------------------------------------------

def test_harness_prerequisite_closure() -> None:
    """Closure is the STAGES prefix through the deepest request;
    unknown or empty requests fail loudly."""
    assert stage_closure(["normalize"]) == ("normalize",)
    assert stage_closure(["index dates"]) == STAGE_ORDER[:4]
    assert stage_closure(
        ["pov1", "enrollment_spans"]) == STAGE_ORDER[: STAGE_ORDER.index("pov1") + 1]

    with pytest.raises(ValueError, match="unknown stage"):
        stage_closure(["not-a-stage"])
    with pytest.raises(ValueError):
        stage_closure([])

    # The prerequisite relation is exactly "all stages before X",
    # derived from the same STAGES tuple run() and plan_stages() use.
    for stage in STAGES:
        index = STAGE_ORDER.index(stage.name)
        assert STAGE_PREREQUISITES[stage.name] == frozenset(
            STAGE_ORDER[:index])


def test_harness_stage_order_cannot_drift() -> None:
    """The harness's order and prerequisite maps are derived from
    STAGES, and executed prefixes equal plan_stages prefixes."""
    assert STAGE_ORDER == tuple(st.name for st in STAGES)

    study = build_study(("E00001", "E00021"), ("D00001",))
    dataset, _ = hand_fixture()
    planned = plan_stages(study)
    for stop in ("enrollment_spans", "pov1", "attrition + denominators"):
        # A fresh harness per stop: the model must hold for any prefix,
        # not just for one engine's accumulated state.
        with StageHarness(study) as harness:
            harness.attach_dataset(dataset)
            executed = harness.run_through(stop)
        # plan_stages() already filters gated stages off, so the
        # prerequisite prefix comes from STAGE_ORDER; executed must
        # equal the gated-in part of BOTH views.
        stop_idx = STAGE_ORDER.index(stop)
        prefix = STAGE_ORDER[: stop_idx + 1]
        gated_off = [s for s in prefix
                     if not STAGE_BY_NAME[s].runs_for(study)]
        assert executed == [s for s in prefix if s not in gated_off]
        assert harness.skipped == gated_off
        assert executed == [
            s for s in planned if STAGE_ORDER.index(s) <= stop_idx]


def test_harness_stage_outputs_derived_from_shipped_sql() -> None:
    """STAGE_OUTPUTS is read out of the shipped SQL files, so the
    relation map cannot disagree with them."""
    assert STAGE_OUTPUTS["normalize"] == (
        "cdm_enrollment", "demographics", "demographics_multi",
        "demographics_missing", "cdm_dispensing", "_ip_days",
        "_cdm_diagnosis_raw", "cdm_diagnosis", "deaths",
        "_cdm_procedure_raw", "cdm_procedure", "cdm_lab",
    )
    for stage in STAGES:
        assert STAGE_OUTPUTS[stage.name], f"{stage.name} produces nothing?"
    # Glue relations belong to run(), not to any stage script.
    for glue in ("covar_source", "cohort_claims", "claim_dose"):
        for name, outs in STAGE_OUTPUTS.items():
            assert glue not in outs, (name, glue)


def test_harness_executes_feature_stages_when_configured() -> None:
    """A study with a covariate enables covariates + baseline + the
    master-list widening; the harness runs the generated glue."""
    study = build_study(
        ("E00001", "E00021"), ("D00001",),
        covariates=(Covariate(
            covarnum=1, covarname="any_dx_noise", codecat="DX",
            covfrom=-365, covto=-1, codes=("X00001",),
        ),),
    )
    dataset, _ = hand_fixture()
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        executed = harness.run_through("master list columns")
        stop_idx = STAGE_ORDER.index("master list columns")
        prefix = STAGE_ORDER[: stop_idx + 1]
        assert executed == [s for s in prefix
                            if STAGE_BY_NAME[s].runs_for(study)]
        assert executed == [
            s for s in plan_stages(study) if STAGE_ORDER.index(s) <= stop_idx]
        # The covariate stage produced its long table (empty is fine on
        # this fixture; the stage itself must have run and materialised
        # the relation).
        cols = harness.engine.con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'covariates_long'"
        ).fetchall()
        assert len(cols) > 0
