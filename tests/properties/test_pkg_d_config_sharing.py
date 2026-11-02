"""Package D — config sharing: cohorts run TOGETHER must equal cohorts
run SEPARATELY.

The denominator machinery keys work per CONFIG (_enr_cfg_id, _denom_cfg_id,
_exclusion_key in src/qrp/pipeline.py) and fans results back out per
cohort; the outcome is deliberately NOT part of the denominator key (event
scopes handle it). A key that omits a parameter it should include merges
two cohorts whose outputs differ — visible as together != separate.

Each dimension below differs the two cohorts in EXACTLY one field, so any
together/separate divergence names that field's key membership. Integer
counts, dates and flags compare EXACTLY; float aggregates compare at 9
decimal places (sums of doubles can associate differently between runs of
different table sizes — that is ordering noise, not semantics; member-days
and episodes themselves are integers and stay exact).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from hypothesis import assume, event, given, strategies as st

from qrp.config import AgeStrata, InclusionRule

from .conftest import expensive_settings
from .harness import full_pipeline_run
from .oracles.d_fixtures import ALL_CODES, CIDA_LEVELS, FUTU_LEVELS, make_study2
from .oracles.d_stages import canonical, table_dicts
from .strategies import SEEDS
from .synth import GenerateOptions, generate

_START = date(2014, 1, 1)
_END = date(2014, 12, 31)

# tables whose rows must be cohort-identical between together and
# separate runs (skipped when a study feature is absent)
_TABLES = (
    "ptsmasterlist", "cohort_final", "distindex", "distindexmap",
    "distindex_episodes", "denominators", "censoring", "attrition",
    "denomcounts", "t2_cida", "numcounts", "followuptime",
    "utilization", "utilization_summary", "risk_scores",
    "risk_score_summary", "covariates_long", "geography", "mfu",
    "lab_results", "lab_summary", "baseline_long",
)


def _dimensions() -> dict[str, dict]:
    """Two-cohort studies differing in exactly one config field."""
    return {
        # enrollment config: different _enr_cfg_id (span merging), with
        # distinct enrollment_num as the cohort file would carry
        "enr": dict(over_a={"enrol_gap": 0, "enrollment_num": 1},
                    over_b={"enrol_gap": 60, "enrollment_num": 2}),
        # washout: same enrollment AND same codes (the codes hash is in
        # the denominator key), different washout — enr_days 30 keeps the
        # eligible window within reach of the washout of fills near the
        # span start (183-day lookback made this dimension vacuous: the
        # calibration mutation survived it)
        "wash": dict(codes_a=ALL_CODES[:20], codes_b=ALL_CODES[:20],
                     over_a={"wash_per": 0, "enr_days": 30},
                     over_b={"wash_per": 25, "enr_days": 30}),
        # exclusion: only cohort_b carries an EXC rule (_exclusion_key)
        "excl": dict(
            over_a={"wash_per": 0}, over_b={"wash_per": 0},
            exclusions=(InclusionRule(cohortgrp="cohort_b", cond=1,
                                      criteria="EXC", codecat="DX",
                                      condfrom=-20, condto=-1,
                                      codes=("X00001",)),)),
        # outcome ONLY: same denom_cfg_id by design; the scope algebra
        # must still reproduce the separate runs exactly
        "outcome": dict(codes_a=ALL_CODES[:20], codes_b=ALL_CODES[20:],
                        events_a=("D00001", "D00002"),
                        events_b=("D00003", "D00004")),
        # OUTPUTDENOM M: member-days NULLed for cohort_b only
        "denomM": dict(over_a={"output_denom": "Y"},
                       over_b={"output_denom": "M"}),
        # strata: a different age band set is part of the denominator key
        "strata": dict(over_a={"age_strata": AgeStrata.parse("00-63 64+")},
                       over_b={}),
        # demographic filter: cohort_b counts women only
        "demog": dict(over_a={}, over_b={"sex": ("F",)}),
    }

_DIM_NAMES = tuple(_dimensions())
_DIMS = _dimensions()


@given(dim=st.sampled_from(list(_DIMS.values())),
       patients=st.integers(3, 5), seed=SEEDS)
@expensive_settings()
@pytest.mark.parametrize("dim_name", _DIM_NAMES)
def test_cohorts_together_equal_separate(dim_name: str, dim: dict,
                                         patients: int, seed: int) -> None:
    """Per-cohort rows are identical whether the pipeline ran the pair
    together or each cohort alone."""
    spec = dict(dim)
    events_a = spec.pop("events_a", ("D00001", "D00002"))
    events_b = spec.pop("events_b", ("D00001", "D00002"))
    codes_a = spec.pop("codes_a", ALL_CODES[:20])
    codes_b = spec.pop("codes_b", ALL_CODES[20:])
    study = make_study2(
        codes_a, codes_b, strata=CIDA_LEVELS + FUTU_LEVELS,
        start=_START, end=_END,
        over_a=spec.get("over_a"), over_b=spec.get("over_b"),
        exclusions=spec.get("exclusions", ()),
    )
    # per-cohort event codes for the outcome dimension: rebuild cohorts
    if events_a != events_b:
        from .oracles.d_fixtures import make_cohort
        study = replace(study, cohorts=(
            make_cohort("cohort_a", codes_a, events_a,
                        **(spec.get("over_a") or {})),
            make_cohort("cohort_b", codes_b, events_b,
                        **(spec.get("over_b") or {}))))
        study.validate()
    dataset = generate(patients=patients, seed=seed, start=_START,
                       end=_END, options=GenerateOptions(include_deaths=True))
    if dim_name == "wash":
        # Focus on examples where the washout shave actually reaches
        # eligible time (enr_days 30): without the shave firing, the
        # dimension cannot observe a washout-key defect (a calibration
        # mutation survived it before this assume was added).
        from .oracles.d_denom import expected_denomcounts as _edc
        facts: dict[str, int] = {}
        _edc(dataset, study, facts)
        assume(facts.get("washout-hit-members", 0) > 0)

    cohort_tables: dict[str, dict[str, list]] = {}
    with full_pipeline_run(dataset, study, write_outputs=False) as both:
        names = _tables(both.engine)
        for t in names:
            for row in table_dicts(both.engine.con, t):
                grp = row.get("cohortgrp") or row.get("group")
                if grp in ("cohort_a", "cohort_b"):
                    cohort_tables.setdefault(grp, {}).setdefault(
                        t, []).append(row)
    for grp in ("cohort_a", "cohort_b"):
        alone = replace(study, cohorts=tuple(
            c for c in study.cohorts if c.cohortgrp == grp))
        with full_pipeline_run(dataset, alone, write_outputs=False) as run:
            for t in _tables(run.engine):
                rows = [r for r in table_dicts(run.engine.con, t)
                        if (r.get("cohortgrp") or r.get("group")) == grp]
                assert canonical(rows) == canonical(
                    cohort_tables.get(grp, {}).get(t, [])), \
                    f"{grp}: table {t} differs together vs separate"
    event(f"dim-{dim_name}-compared")
    event("together-nonempty",
          any(cohort_tables.get("cohort_a", {}).get("denomcounts")
              for _ in (0,)))


def _tables(engine) -> list[str]:
    have = {r[0].lower() for r in engine.con.execute(
        "SELECT table_name FROM information_schema.tables").fetchall()}
    return [t for t in _TABLES if t in have]
