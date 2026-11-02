"""Package B: stockpiling matches a sequential claim-at-a-time oracle.

Contract under test (30_exposure.sql, docs/SAS_PARITY.md "Stockpiling
recurrence"): within one (cohort, stockgroup, patient), fills are pushed
forward so supplies never overlap — s(i) = max(a(i), e(i-1) + 1) — with
the SAS same-day collapse (per-group sum/min/max/mean operators), the
SUPRANGE/AMTRANGE exclusions applied to SHAVED values, the PERCENTDAYS
sequence/truncation rule, and the two enrollment shaves around the chain.

Oracle: tests/properties/oracles/b_stockpile_model.py — an explicit
Python loop over dates; the shipped SQL instead solves the recurrence in
closed form with window functions, so the two derivations are distinct.

Dates AND selected claim payloads are compared (adate, expiredt,
orig_adate, rxsup, rxamt, numdispensing), not just row counts.
"""

from dataclasses import replace as dc_replace
from datetime import date, timedelta

from hypothesis import event, example, given, settings
from hypothesis import strategies as st

# tests/ is on sys.path under pytest's prepend import mode (no
# tests/__init__.py), so the audited SAS transliteration imports bare —
# the same way tests/test_pipeline.py reaches it.
from _stockpile_reference import stockpile as reference_stockpile
from .conftest import expensive_settings
from .harness import StageHarness, fetch_rows
from .oracles.b_pkg_fixtures import (
    START,
    assert_rows_match,
    disp,
    mini_dataset,
    patient,
    span,
    two_cohort_study,
)
from .oracles.b_stockpile_model import (
    STOCKPILED_COLS,
    model_stockpiled,
    _sequential_recurrence,
)
from qrp.config import StockpilingRule

# ---------------------------------------------------------------------
# Generated cases: one patient per example keeps SQL runs tiny while the
# rule axes sweep every supported STOCKPILINGFILE setting from
# config.StockpilingRule (sup_op/amt_op sum|min|max|mean, SUPRANGE /
# AMTRANGE pieces, PERCENTDAYS fraction or None = always push).
# ---------------------------------------------------------------------

_RANGE_SPECS = {
    "all": ((0.0, True, float("inf"), False),),
    "1-30": ((1.0, False, 30.0, False),),
    "15-<30": ((15.0, False, 30.0, True),),
}
_AMT_RANGE_SPECS = {
    "all": ((0.0, True, float("inf"), False),),
    "0.4-10": ((0.4, False, 10.0, False),),
    "2-<8": ((2.0, False, 8.0, True),),
}

_FILL_SPEC = st.fixed_dictionaries({
    "code": st.sampled_from(("E00001", "E00002")),
    "day": st.integers(0, 120),
    "sup": st.sampled_from((1, 3, 7, 16, 30)),
    "amt": st.sampled_from((0.5, 1.0, 2.5, 7.5, 10.0, 10.5)),
    "twin": st.booleans(),
    "twin_sup_delta": st.integers(-1, 1),
    "twin_amt_epsilon": st.sampled_from((0.0, 0.125, 1e-9)),
})

rule_spec_strategy = st.fixed_dictionaries({
    "sup_op": st.sampled_from(("sum", "min", "max", "mean")),
    "amt_op": st.sampled_from(("sum", "min", "max", "mean")),
    "suprange": st.sampled_from(tuple(_RANGE_SPECS)),
    "amtrange": st.sampled_from(tuple(_AMT_RANGE_SPECS)),
    "percentdays": st.sampled_from((None, 0.0, 0.25, 0.5, 1.0)),
    "groups": st.sampled_from(("shared", "per_code")),
    "fills": st.lists(_FILL_SPEC, min_size=1, max_size=5),
})


def _spec(**over: object) -> dict:
    base: dict = {
        "sup_op": "sum", "amt_op": "sum", "suprange": "all",
        "amtrange": "all", "percentdays": None, "groups": "shared",
        "fills": [],
    }
    base.update(over)
    return base


def _study_for(spec: dict):
    study = two_cohort_study()
    stockgroups: tuple[tuple[str, str], ...] = ()
    if spec["groups"] == "per_code":
        stockgroups = (("E00001", "sg1"), ("E00002", "sg2"))
    groups = ("_default",) if spec["groups"] == "shared" else ("sg1", "sg2")
    cohort = next(c for c in study.cohorts if c.cohortgrp == "cohort_a")
    cohort = dc_replace(cohort, exposure_stockgroups=stockgroups)
    others = tuple(c for c in study.cohorts if c.cohortgrp != "cohort_a")
    study = dc_replace(study, cohorts=(cohort, *others))
    study = dc_replace(study, stockpiling=tuple(
        StockpilingRule(
            cohortgrp="cohort_a", stockgroup=sg,
            sup_op=spec["sup_op"], amt_op=spec["amt_op"],
            suprange=_RANGE_SPECS[spec["suprange"]],
            amtrange=_AMT_RANGE_SPECS[spec["amtrange"]],
            percentdays=spec["percentdays"],
        )
        for sg in groups))
    study.validate()
    return study


def _dataset_for(spec: dict):
    fills = []
    for f in spec["fills"]:
        day = START + timedelta(days=f["day"])
        fills.append(disp(1, day, f["code"], f["sup"], f["amt"]))
        if f["twin"]:
            sup = min(30, max(1, f["sup"] + f["twin_sup_delta"]))
            fills.append(disp(1, day, f["code"], sup,
                              f["amt"] + f["twin_amt_epsilon"]))
    return mini_dataset(
        patients=(patient(1),),
        enrollment=(span(1, START, START + timedelta(days=300)),),
        dispensings=tuple(fills),
    )


@example(_spec(fills=[
    # duplicate same-day claims, same supply, amounts differing: the
    # sameday collapse must add BOTH amounts and count BOTH dispensings
    {"code": "E00001", "day": 10, "sup": 16, "amt": 10.0, "twin": True,
     "twin_sup_delta": 0, "twin_amt_epsilon": 2.5},
    # a follow-up inside the first supply: pushed to expiry + 1
    {"code": "E00001", "day": 20, "sup": 16, "amt": 5.0, "twin": False,
     "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
]))
@example(_spec(  # near-tie: supplies differing by one day on one date
    fills=[
        {"code": "E00001", "day": 5, "sup": 16, "amt": 1.0, "twin": True,
         "twin_sup_delta": 1, "twin_amt_epsilon": 0.0},
        {"code": "E00001", "day": 22, "sup": 7, "amt": 1.0, "twin": False,
         "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
    ],
))
@example(_spec(  # zero washout configuration does not disturb the chain
    fills=[
        {"code": "E00001", "day": 1, "sup": 30, "amt": 4.0, "twin": False,
         "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
        {"code": "E00001", "day": 40, "sup": 30, "amt": 4.0, "twin": False,
         "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
    ],
))
@example(_spec(  # PERCENTDAYS boundary: overlap share exactly at the
    # fraction pushes; a hair above it starts a sequence and truncates
    percentdays=0.5,
    fills=[
        {"code": "E00001", "day": 1, "sup": 30, "amt": 4.0, "twin": False,
         "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
        {"code": "E00001", "day": 16, "sup": 30, "amt": 4.0, "twin": False,
         "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
    ],
))
@example(_spec(  # stockgroups push independently: same two dates, two
    # different codes in two groups, each chained within its own group
    groups="per_code",
    fills=[
        {"code": "E00001", "day": 10, "sup": 30, "amt": 1.0, "twin": False,
         "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
        {"code": "E00002", "day": 10, "sup": 7, "amt": 2.0, "twin": False,
         "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
        {"code": "E00001", "day": 15, "sup": 7, "amt": 1.0, "twin": False,
         "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
        {"code": "E00002", "day": 15, "sup": 30, "amt": 2.0, "twin": False,
         "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
    ],
))
@example(_spec(  # mean supply rounds half away from zero: 16 + 7 -> 12
    sup_op="mean", amt_op="min",
    fills=[
        {"code": "E00001", "day": 10, "sup": 16, "amt": 3.0, "twin": True,
         "twin_sup_delta": -9, "twin_amt_epsilon": 1.0},
    ],
))
@settings(expensive_settings())
@given(rule_spec_strategy)
def test_stockpile_matches_sequential_model(spec: dict) -> None:
    dataset = _dataset_for(spec)
    study = _study_for(spec)
    event(f"sup_op={spec['sup_op']}")
    event(f"amt_op={spec['amt_op']}")
    event(f"percentdays={spec['percentdays']}")
    event(f"ranges={spec['suprange']}/{spec['amtrange']}")
    event(f"stockgroups={spec['groups']}")
    twins = sum(1 for f in spec["fills"] if f["twin"])
    event(f"same-day twin pairs={twins}")

    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("exposure + stockpiling")
        sql = fetch_rows(harness.engine, "stockpiled")
    model = model_stockpiled(dataset, study)
    event(f"nonempty output={bool(sql)}")
    assert_rows_match(sql, model, STOCKPILED_COLS, "stockpiled")


# ---------------------------------------------------------------------
# Oracle cross-check: my sequential recurrence against the audited SAS
# transliteration tests/_stockpile_reference.py (independent derivation:
# that module transliterates ms_stockpiling.sas line by line, the SQL
# solves the recurrence in closed form, and my oracle re-derives the
# loop from the documented recurrence). Pure and cheap.
# ---------------------------------------------------------------------

@st.composite
def _fill_lists(draw: st.DrawFn) -> list[tuple[date, int, float]]:
    return [
        (draw(st.dates(min_value=date(2011, 1, 1),
                       max_value=date(2011, 3, 31))),
         draw(st.sampled_from((1, 3, 7, 16, 30))),
         draw(st.sampled_from((0.5, 1.0, 2.5, 5.0, 7.5, 10.0))))
        for _ in range(draw(st.integers(1, 10)))
    ]


@example([(date(2011, 1, 10), 30, 4.0), (date(2011, 1, 25), 30, 4.0)])
@example([(date(2011, 1, 10), 7, 4.0), (date(2011, 1, 10), 7, 4.0)])
@given(_fill_lists())
def test_stockpile_sequential_recurrence_matches_sas_reference(
    fills: list[tuple[date, int, float]],
) -> None:
    """The oracle's recurrence and the SAS transliteration agree on every
    push decision and expiry for always-push stockpiling."""
    event(f"overlapping fill pairs present={_has_overlap(fills)}")
    mine = _reference_recs(_sequential_recurrence(_collapse(fills), None))
    ref_recs, _excluded = reference_stockpile(fills)
    theirs = {r["orig"]: (r["date"], r["exp"], r["sup"], r["amt"], r["n"])
              for r in ref_recs}
    assert mine == theirs, (fills, mine, theirs)


def _collapse(fills: list[tuple[date, int, float]]):
    days: dict[date, list[tuple[int, float]]] = {}
    for d, sup, amt in fills:
        days.setdefault(d, []).append((sup, amt))
    return [(d, float(sum(s for s, _ in v)), sum(a for _, a in v), len(v))
            for d, v in sorted(days.items())]


def _reference_recs(recs) -> dict:
    return {r.orig: (r.start, r.end, r.sup, r.amt, r.n) for r in recs}


def _has_overlap(fills: list[tuple[date, int, float]]) -> bool:
    days = sorted({d for d, _, _ in fills})
    supply_end: date | None = None
    for d in days:
        total = sum(sup for dd, sup, _ in fills if dd == d)
        if supply_end is not None and d <= supply_end:
            return True
        end = d + timedelta(days=total - 1)
        supply_end = end if supply_end is None else max(supply_end, end)
    return False
