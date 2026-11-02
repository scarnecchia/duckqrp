"""Package B: episode selection matches an independent chaining model.

Contract under test (50_episodes.sql, docs/SAS_PARITY.md "Episode gap
type P"): for non-point cohorts, stockpiled records chain into episodes
while blank days to the latest supply end stay within `episode_gap`
(fixed) or within `episode_gap * previous record's supply / 100` (type
'P'; NULL gap: every record stands alone), with a record also breaking
when its enrollment span changes. Episode ends extend by exp_ext_per.
Point cohorts emit one episode per index date instead. episode_criteria
joins each POV1 index date to the episode STARTING on it, censors the
end (study censor, enrollment end, death when censoring, max duration),
and evaluates the five master list flags; ptsmasterlist keeps rows where
all five hold.

Oracle: tests/properties/oracles/b_episode_model.py — sequential scans.
Selected claim payloads, eligibility flags and multiplicity are
compared, not just counts.
"""


from hypothesis import event, example, given, settings
from hypothesis import strategies as st

from .conftest import expensive_settings
from .harness import StageHarness, fetch_rows
from .oracles.b_episode_model import (
    CRITERIA_COLS,
    EPISODES_COLS,
    MSTR_COLS,
    model_episode_criteria,
    model_episodes,
)
from .oracles.b_pkg_fixtures import (
    assert_rows_match,
    expand_spec,
    spec_strategy,
)
from .oracles.b_stockpile_model import model_pov1, model_stockpiled

# Every supported cohort episode-selection mode from config.CohortConfig
# (point vs chain; fixed/percent/NULL episode gap; end extension;
# duration caps; supply/duration/observability flags). Applied to both
# cohorts so the whole pipeline runs under the mode.
_MODES: dict[str, dict] = {
    "chain_f0": {"episode_gap": 0},
    "chain_f2": {"episode_gap": 2},
    "chain_p50": {"episode_gap": 50, "episode_gap_type": "P"},
    "chain_null": {"episode_gap": None},
    "chain_ext5": {"episode_gap": 0, "exp_ext_per": 5},
    "chain_maxdur30": {"episode_gap": 0, "max_epis_dur": 30},
    "chain_flags": {"episode_gap": 0, "min_days_supp": 20,
                    "min_epis_dur": 15, "req_days_aft_ind": 5},
    "point_maxdur30": {"point": True, "episode_gap": None,
                       "max_epis_dur": 30},
}

episode_spec_strategy = st.fixed_dictionaries({
    "mode": st.sampled_from(tuple(_MODES)),
    "spec": spec_strategy,
})


def _spec(over: dict) -> dict:
    """Identity kept only so the @example blocks read as _spec({...})."""
    return over


@example(_spec({"mode": "chain_f0", "spec": {
    # regression pin (a085f8b replay, failure 1): lag-break segmentation
    # emits overlapping episodes; the in-both-episodes fill fans out per
    # containing span and the span-change break splits the copies into
    # FOUR chain episodes
    "enrol_gap": 0, "wash_per": 0, "coverage": "MD",
    "gap_b_differs": False,
    "patients": [
        {"spans": [("overlap", 1, "YY")],
         "fills": [{"code": "E00001", "day": 0, "sup": 1, "amt": 0.5,
                    "twin": False, "twin_sup_delta": 0,
                    "twin_amt_epsilon": 0.0}],
         "death_day": None},
        {"spans": [("overlap", 1, "YY"), ("overlap", 5, "YY"),
                   ("overlap", 1, "YY")],
         "fills": [
             {"code": "E00001", "day": 0, "sup": 1, "amt": 0.5,
              "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
             {"code": "E00001", "day": 0, "sup": 1, "amt": 0.5,
              "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
             {"code": "E00001", "day": 0, "sup": 1, "amt": 0.5,
              "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
             {"code": "E00001", "day": 7, "sup": 1, "amt": 0.5,
              "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
         ],
         "death_day": None},
    ],
    "dup_demog": False, "leap_birth": None,
}}))
@example(_spec({"mode": "chain_f0", "spec": {
    # regression pin (a085f8b replay, failure 2): the lag-break
    # segmentation ([span2 ruptured against the inner span's short end])
    # makes POV1's index-carrying span end earlier than a running-max
    # merge would, which flows into dataavail_dt
    "enrol_gap": 0, "wash_per": 0, "coverage": "MD",
    "gap_b_differs": False,
    "patients": [
        {"spans": [("overlap", 1, "YY")],
         "fills": [{"code": "E00001", "day": 0, "sup": 1, "amt": 0.5,
                    "twin": False, "twin_sup_delta": 0,
                    "twin_amt_epsilon": 0.0}],
         "death_day": None},
        {"spans": [("overlap", 1, "YY"), ("overlap", 3, "YY"),
                   ("adjacent", 1, "YY")],
         "fills": [
             {"code": "E00001", "day": 0, "sup": 1, "amt": 0.5,
              "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
             {"code": "E00001", "day": 0, "sup": 1, "amt": 0.5,
              "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
             {"code": "E00001", "day": 0, "sup": 1, "amt": 0.5,
              "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
             {"code": "E00001", "day": 4, "sup": 1, "amt": 0.5,
              "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
         ],
         "death_day": None},
    ],
    "dup_demog": False, "leap_birth": None,
}}))
@example(_spec({"mode": "chain_f0", "spec": {
    # regression pin (a085f8b replay, failure 3): two output episodes
    # with different starts both contain the same-day-summed fill; the
    # fan-out copies tie on (adate, expiredt, rxsup) and the chaining
    # window's span-major tie order pairs them into TWO episodes of
    # rxsup 4 (not four of rxsup 2)
    "enrol_gap": 0, "wash_per": 0, "coverage": "MD",
    "gap_b_differs": False,
    "patients": [
        {"spans": [("overlap", 1, "YY")],
         "fills": [{"code": "E00001", "day": 0, "sup": 1, "amt": 0.5,
                    "twin": False, "twin_sup_delta": 0,
                    "twin_amt_epsilon": 0.0}],
         "death_day": None},
        {"spans": [("overlap", 1, "YY"), ("overlap", 1, "YY"),
                   ("overlap", 3, "AY")],
         "fills": [
             {"code": "E00001", "day": 0, "sup": 1, "amt": 0.5,
              "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
             {"code": "E00001", "day": 0, "sup": 1, "amt": 0.5,
              "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
             {"code": "E00001", "day": 0, "sup": 1, "amt": 0.5,
              "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
             {"code": "E00001", "day": 4, "sup": 1, "amt": 0.5,
              "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
         ],
         "death_day": None},
    ],
    "dup_demog": False, "leap_birth": None,
}}))
@example(_spec({"mode": "chain_f0", "spec": {
    # same-day pair (supplies 16 and 30 sum to 46) chaining a fill 56
    # days later: correct chaining keeps ONE episode; a tie-break defect
    # that dropped the 16-day claim would break the chain at gap 16 > 10
    "enrol_gap": 0, "wash_per": 183, "coverage": "MD",
    "gap_b_differs": False,
    "patients": [{
        "spans": [("adjacent", 500, "YY")],
        "fills": [
            {"code": "E00001", "day": 10, "sup": 16, "amt": 1.0,
             "twin": True, "twin_sup_delta": 14, "twin_amt_epsilon": 2.5},
            {"code": "E00001", "day": 56, "sup": 7, "amt": 1.0,
             "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
        ],
        "death_day": None,
    }],
    "dup_demog": False, "leap_birth": None,
}}))
@example(_spec({"mode": "chain_f2", "spec": {
    # chaining gap at threshold vs one day over, in one patient:
    # runout chains at blank gap 0..2, breaks at 3
    "enrol_gap": 0, "wash_per": 183, "coverage": "MD",
    "gap_b_differs": False,
    "patients": [{
        "spans": [("adjacent", 500, "YY")],
        "fills": [
            {"code": "E00001", "day": 10, "sup": 16, "amt": 1.0,
             "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
            # day 23: blank 0 -> chained
            {"code": "E00001", "day": 23, "sup": 16, "amt": 1.0,
             "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
            # day 46: blank 7 -> break (episode 2)
            {"code": "E00001", "day": 46, "sup": 16, "amt": 1.0,
             "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
            # day 60: blank -2 -> chained to episode 2
            {"code": "E00001", "day": 60, "sup": 16, "amt": 1.0,
             "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
        ],
        "death_day": None,
    }],
    "dup_demog": False, "leap_birth": None,
}}))
@example(_spec({"mode": "chain_f0", "spec": {
    # a disenrol/re-enrol patient: two enrollment spans, fills in each;
    # the span change must break the episode EVEN with no supply gap
    "enrol_gap": 0, "wash_per": 0, "coverage": "MD",
    "gap_b_differs": False,
    "patients": [{
        "spans": [("adjacent", 50, "YY"), ("gap_plus", 40, "YY")],
        "fills": [
            {"code": "E00001", "day": 10, "sup": 45, "amt": 1.0,
             "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
            {"code": "E00001", "day": 60, "sup": 7, "amt": 1.0,
             "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
        ],
        "death_day": None,
    }],
    "dup_demog": False, "leap_birth": None,
}}))
@example(_spec({"mode": "chain_f0", "spec": {
    # deaths before / inside / after the candidate index and episode
    # windows, one patient each
    "enrol_gap": 0, "wash_per": 0, "coverage": "MD",
    "gap_b_differs": False,
    "patients": [
        {"spans": [("adjacent", 400, "YY")],
         "fills": [{"code": "E00001", "day": 60, "sup": 7, "amt": 1.0,
                    "twin": False, "twin_sup_delta": 0,
                    "twin_amt_epsilon": 0.0}],
         "death_day": 40},
        {"spans": [("adjacent", 400, "YY")],
         "fills": [{"code": "E00001", "day": 60, "sup": 7, "amt": 1.0,
                    "twin": False, "twin_sup_delta": 0,
                    "twin_amt_epsilon": 0.0}],
         "death_day": 63},
        {"spans": [("adjacent", 400, "YY")],
         "fills": [{"code": "E00001", "day": 60, "sup": 7, "amt": 1.0,
                    "twin": False, "twin_sup_delta": 0,
                    "twin_amt_epsilon": 0.0}],
         "death_day": 90},
    ],
    "dup_demog": False, "leap_birth": None,
}}))
@example(_spec({"mode": "point_maxdur30", "spec": {
    # point cohort: one episode per index date, capped at 30 days
    "enrol_gap": 0, "wash_per": 0, "coverage": "MD",
    "gap_b_differs": False,
    "patients": [{
        "spans": [("adjacent", 400, "YY")],
        "fills": [
            {"code": "E00001", "day": 60, "sup": 45, "amt": 1.0,
             "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
            {"code": "E00001", "day": 150, "sup": 7, "amt": 1.0,
             "twin": False, "twin_sup_delta": 0, "twin_amt_epsilon": 0.0},
        ],
        "death_day": None,
    }],
    "dup_demog": False, "leap_birth": None,
}}))
@example(_spec({"mode": "chain_ext5", "spec": {
    # exp_ext_per extends the observed end; leap day 2012-02-29 inside
    "enrol_gap": 0, "wash_per": 0, "coverage": "MD",
    "gap_b_differs": False,
    "patients": [{
        "spans": [("adjacent", 500, "YY")],
        "fills": [{"code": "E00001", "day": 422, "sup": 7, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    }],
    "dup_demog": False, "leap_birth": None,
}}))
@settings(expensive_settings())
@given(episode_spec_strategy)
def test_episode_selection_matches_model(case: dict) -> None:
    mode_name, spec = case["mode"], case["spec"]
    dataset, study = expand_spec(spec, extra_cohort_overrides=_MODES[mode_name])
    event(f"episode mode={mode_name}")
    event(f"wash_per={spec['wash_per']}")
    if any(p["death_day"] is not None for p in spec["patients"]):
        event("death interaction present")
    fills = [f for p in spec["patients"] for f in p["fills"] if f["twin"]]
    event(f"same-day tie pairs={len(fills)}")

    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("episodes + masterlist")
        sql_episodes = fetch_rows(harness.engine, "episodes")
        sql_criteria = fetch_rows(harness.engine, "episode_criteria")
        sql_master = fetch_rows(harness.engine, "ptsmasterlist")

    stockpiled = model_stockpiled(dataset, study)
    pov1 = model_pov1(dataset, study, stockpiled)
    episodes = model_episodes(dataset, study, stockpiled, pov1)
    criteria, master = model_episode_criteria(dataset, study, episodes, pov1)
    event(f"nonempty episodes={bool(sql_episodes)}")
    assert_rows_match(sql_episodes, episodes, EPISODES_COLS, "episodes")
    assert_rows_match(sql_criteria, criteria, CRITERIA_COLS,
                      "episode_criteria")
    assert_rows_match(sql_master, master, MSTR_COLS, "ptsmasterlist")
