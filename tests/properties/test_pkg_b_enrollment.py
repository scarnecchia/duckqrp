"""Package B: SAS lag-segmentation parity characterization.

Contract under test (20_enrollment.sql, docs/SAS_PARITY.md "Enrollment
spans"): continuous enrollment episodes — spans separated by at most
`enrol_gap` blank days merge, coverage-flag changes rupture, a
chart-required configuration drops patients with any non-chart span
overlapping the study window, and spans failing the coverage mode never
qualify.

Oracle: tests/properties/oracles/b_enrollment_model.py — the lag
segmentation (each span ruptures against the immediately PREVIOUS
span's end and flags; episode = min start / max end of member spans),
i.e. the SAME ruled recurrence the shipped SQL encodes. It is a parity
characterization, not an independent day-set proof: restricted
uniform-flag, nonoverlap day-set checks with exact assertions live in
test_pkg_b_pass2.py (independent oracle:
tests/properties/oracles/b_day_sets.py), and the overlap fan-out
invariant surface lives in test_pkg_b_independent_review.py. The old
strict-xfail pin
(test_enrollment_overlapping_raw_spans_known_divergence) was replaced
at 88edf91 by the positive parity pin
test_enrollment_overlapping_raw_spans_parity below.
"""

from datetime import date, timedelta

from hypothesis import event, example, given, settings

from qrp.config import StudyConfig

from .synth import SyntheticDataset
from .conftest import expensive_settings
from .harness import StageHarness, fetch_rows
from .oracles.b_enrollment_model import enrollment_episodes
from .oracles.b_pkg_fixtures import (
    START,
    assert_rows_match,
    disp,
    expand_spec,
    mini_dataset,
    patient,
    spec_strategy,
    span,
    two_cohort_study,
)

ENROLLMENT_COLS = ("enr_cfg_id", "patid", "episode", "enr_start", "enr_end")


def _spec(**over: object) -> dict:
    """A spec dict matching spec_strategy's shape, for @example cases."""
    base: dict = {
        "enrol_gap": 1,
        "wash_per": 183,
        "coverage": "MD",
        "gap_b_differs": False,
        "patients": [{
            "spans": [("adjacent", 10, "YY")],
            "fills": [{"code": "E00001", "day": 6, "sup": 7, "amt": 1.0,
                       "twin": False, "twin_sup_delta": 0,
                       "twin_amt_epsilon": 0.0}],
            "death_day": None,
        }],
        "dup_demog": False,
        "leap_birth": None,
    }
    base.update(over)
    return base


def _model_rows(dataset: SyntheticDataset, study: StudyConfig) -> list[tuple]:
    out: list[tuple] = []
    for (cfg, patid), episodes in enrollment_episodes(dataset, study).items():
        for i, ep in enumerate(episodes, start=1):
            out.append((cfg, patid, i, ep.enr_start, ep.enr_end))
    return out


@example(_spec(patients=[
    # gap threshold, all three sides in ONE run: pair 1 merges with one
    # blank day less than the threshold, pair 2 exactly at it, and the
    # chain between patients 2 and 3 covers the one-day-over rupture.
    {
        "spans": [("adjacent", 10, "YY"), ("gap_minus", 10, "YY")],
        "fills": [{"code": "E00001", "day": 8, "sup": 3, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    },
    {
        "spans": [("adjacent", 10, "YY"), ("gap_exact", 10, "YY"),
                  ("gap_plus", 10, "YY")],
        "fills": [{"code": "E00001", "day": 8, "sup": 3, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    },
]))
@example(_spec(  # coverage-flag rupture and the A-flag boundary
    coverage="MD",
    patients=[{
        "spans": [("adjacent", 5, "YY"), ("adjacent", 5, "AY"),
                  ("gap_exact", 5, "NY")],
        "fills": [{"code": "E00001", "day": 8, "sup": 3, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    }],
))
@example(_spec(  # drug-only coverage: medcov-only spans never qualify
    coverage="D",
    patients=[{
        "spans": [("adjacent", 30, "YN"), ("adjacent", 30, "YY")],
        "fills": [{"code": "E00001", "day": 20, "sup": 7, "amt": 2.5,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    }],
))
@example(_spec(  # nested spans merge into one episode
    patients=[{
        "spans": [("adjacent", 40, "YY"), ("overlap", 5, "YY")],
        "fills": [{"code": "E00002", "day": 20, "sup": 7, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    }],
))
@example(_spec(  # SCDM-legal 'U' flags: non-qualifying spans are filtered
    # BEFORE segmentation, so the Y Y spans on either side of the Y/U span
    # see each other and merge — segmenting first would rupture on the
    # flag change and emit two episodes.
    coverage="MD",
    patients=[{
        "spans": [("adjacent", 5, "YY"), ("adjacent", 5, "YU"),
                  ("adjacent", 5, "YY")],
        "fills": [{"code": "E00001", "day": 8, "sup": 3, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    }],
))
@example(_spec(  # leap February inside an enrollment window
    patients=[{
        "spans": [("adjacent", 200, "YY")],
        "fills": [{"code": "E00001", "day": 420, "sup": 7, "amt": 1.0,
                   "twin": False, "twin_sup_delta": 0,
                   "twin_amt_epsilon": 0.0}],
        "death_day": None,
    }],
    leap_birth="2000-02-29",
))
@settings(expensive_settings())
@given(spec_strategy)
def test_enrollment_matches_day_model(spec: dict) -> None:
    dataset, study = expand_spec(spec)
    shapes = [s[0] for p in spec["patients"] for s in p["spans"]]
    event(f"enrol_gap={spec['enrol_gap']}")
    event(f"coverage={spec['coverage']}")
    event(f"wash_per={spec['wash_per']}")
    if "gap_exact" in shapes:
        event("gap-threshold boundary exercised")
    if "overlap" in shapes:
        event("overlapping raw spans present")
    flags = [s[2] for p in spec["patients"] for s in p["spans"]]
    if len(set(flags)) > 1:
        event("coverage-flag variety present")
    if any("U" in f for f in flags):
        event("U-flag span present (SCDM unknown coverage)")

    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("enrollment_spans")
        sql = fetch_rows(harness.engine, "enrollment_spans")
    event(f"nonempty output={bool(sql)}")
    assert_rows_match(sql, _model_rows(dataset, study), ENROLLMENT_COLS,
                      "enrollment_spans")


# ---------------------------------------------------------------------
# B1/B2 parity pin (OPERATOR RULED: inherited defect, parity kept — see
# investigation/findings/B2-overlapping-enrollment-double-count.md).
# Overlapping raw spans whose inner boundary ruptures mid-episode make
# the shipped SQL emit OVERLAPPING episodes (the lag break compares
# each span with the immediately PREVIOUS span's end), and everything
# downstream fans out per containing span (the single dispensing below
# is stockpiled twice, numdispensing=2, supplies 16/46). Verified
# bug-for-bug against the ORIGINAL SAS source (read-only reference
# tree): ms_create_enrollment_spans.sas:109-117 builds the shave
# reference via %MS_EPISODEREC2, whose lag-break (:67-77) and min/max
# reconciliation (:79-97) emit the same overlapping episodes;
# ms_shaveoutside (cartesian claims×spans join) shaves per containing
# span; ms_cidanum.sas:1299-1308 pre-shaves, ms_stockpiling
# same-day-sums the copies (N()=NumDispensing), and ms_cidanum.sas:
# 1531-1541 re-shaves through the same join. So the behavior is
# INHERITED from SAS; the oracle models it (lag segmentation +
# containing-span fan-out) and this test pins it — it fails the day
# any parity policy changes, prompting an oracle revisit.
# ---------------------------------------------------------------------

def test_enrollment_overlapping_raw_spans_parity() -> None:
    dataset = mini_dataset(
        patients=(patient(1),),
        enrollment=(
            span(1, date(2011, 1, 1), date(2011, 12, 31)),
            span(1, date(2011, 2, 15), date(2011, 6, 15)),
            span(1, date(2011, 9, 1), date(2011, 9, 30)),
        ),
        dispensings=(disp(1, date(2011, 9, 15), "E00001", 30, 10.0),),
    )
    study = two_cohort_study()
    model_rows = _model_rows(dataset, study)
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("exposure + stockpiling")
        sql = fetch_rows(harness.engine, "enrollment_spans")
        stock = fetch_rows(harness.engine, "stockpiled")
    expected = [
        ("MD|0|0", 1, 1, date(2011, 1, 1), date(2011, 12, 31)),
        ("MD|0|0", 1, 2, date(2011, 9, 1), date(2011, 9, 30)),
    ]
    assert sorted(sql) == sorted(expected), sql
    assert model_rows == expected, model_rows
    # Parity fan-out: the 09-15 fill sits inside BOTH episodes; the
    # shave joins emit TWO stockpiled rows for ONE dispensing, each
    # with numdispensing=2 (same-day-summed copies): supply 16 (trimmed
    # at C's end) and 46 (pushed expiry Oct 30 inside A — untrimmed).
    assert len(stock) == 2, stock
    assert sorted(r[6] for r in stock) == [16, 46]
    assert {r[8] for r in stock} == {2}
    # B2 invariant-violation witness, not a correctness endorsement:
    # a single 30-day claim becomes a 46-day row with count two.
    assert len(dataset.dispensings) == 1
    assert max(r[6] for r in stock) > 30
    assert max(r[8] for r in stock) > len(dataset.dispensings)


# ---------------------------------------------------------------------
# SCDM 8.2.2 data-model boundaries (post-campaign alignment review;
# probe: investigation/probes/b_scdm_alignment_probe.py).
# ---------------------------------------------------------------------

def test_enrollment_duplicate_identical_spans_collapse() -> None:
    """Exact duplicate enrollment rows — the representable residue of the
    SCDM "Collapsing enrollment spans" rule — collapse to ONE episode and
    do NOT trigger the B2 fan-out: one fill stockpiles once.

    SCDM 8.2.2 Enrollment sanctions exactly one overlap form: spans
    identical on (PatID, Enr_Start, Enr_End, MedCov, DrugCov, Chart)
    differing only in PlanType/PayerType, which partners are told to
    collapse. This pipeline reads neither column (INPUTS.md enrollment:
    patid, enr_start, enr_end, medcov, drugcov [, chart]), so the
    sanctioned overlap arrives here as exact duplicate rows. Their
    lag-break is a no-op (blank gap -1, no flag change), the island
    min/max reconciles, and the claim matches one containing span."""
    dataset = mini_dataset(
        patients=(patient(1),),
        enrollment=(
            span(1, date(2011, 1, 1), date(2011, 12, 31)),
            span(1, date(2011, 1, 1), date(2011, 12, 31)),
        ),
        dispensings=(disp(1, date(2011, 9, 15), "E00001", 30, 10.0),),
    )
    study = two_cohort_study()
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("exposure + stockpiling")
        sql = fetch_rows(harness.engine, "enrollment_spans")
        stock = fetch_rows(harness.engine, "stockpiled")
    expected = [
        ("MD|0|0", 1, 1, date(2011, 1, 1), date(2011, 12, 31)),
    ]
    assert sorted(sql) == sorted(expected), sql
    assert _model_rows(dataset, study) == expected
    # No B2 fan-out on duplicates: one stockpiled row for the one fill,
    # full supply (nothing shaved), numdispensing=1 (row indices as in
    # the parity pin above: r[6]=supply, r[8]=numdispensing).
    assert len(stock) == 1, stock
    assert stock[0][6] == 30, stock
    assert stock[0][8] == 1, stock


def test_enrollment_unknown_flags_and_chart_interaction() -> None:
    """SCDM-legal 'U' (Unknown) coverage flags: MedCov Y/N/U/A, DrugCov
    Y/N/U. 'U' never qualifies for coverage (00_macros has_coverage and
    the oracle agree), BUT the chart filter runs over ALL raw spans — a
    non-chart 'U' span still drops the whole patient under chart_required,
    and a (U,Y) span qualifies under drug-only coverage."""
    dataset = mini_dataset(
        patients=(patient(1), patient(2), patient(3)),
        enrollment=(
            span(1, START, START + timedelta(days=60), "Y", "Y"),
            span(1, START + timedelta(days=10), START + timedelta(days=20),
                 "U", "U", chart="N"),
            span(2, START, START + timedelta(days=60), "Y", "Y"),
            span(3, START, START + timedelta(days=60), "U", "Y"),
        ),
        dispensings=(
            disp(1, START + timedelta(days=10), "E00001", 7, 1.0),
            disp(2, START + timedelta(days=10), "E00001", 7, 1.0),
            disp(3, START + timedelta(days=10), "E00001", 7, 1.0),
        ),
    )
    md_study = two_cohort_study(
        coverage="MD", cohort_overrides={"chart_required": True})
    with StageHarness(md_study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("enrollment_spans")
        sql = fetch_rows(harness.engine, "enrollment_spans")
    model = enrollment_episodes(dataset, md_study)
    assert {r[1] for r in sql} == {2}, sql
    assert {patid for (_cfg, patid) in model} == {2}

    d_study = two_cohort_study(coverage="D")
    with StageHarness(d_study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("enrollment_spans")
        d_sql = fetch_rows(harness.engine, "enrollment_spans")
    d_model = enrollment_episodes(dataset, d_study)
    # Drug-only coverage, chart NOT required (a different enr_cfg_id, so
    # the MD chart drop does not carry over): (U,Y) qualifies; (Y,U) and
    # (U,U) never do; patient 1's (Y,Y) span qualifies again.
    assert {r[1] for r in d_sql} == {1, 2, 3}, d_sql
    assert {patid for (_cfg, patid) in d_model} == {1, 2, 3}


# ---------------------------------------------------------------------
# Chart-required configurations
# ---------------------------------------------------------------------

def test_enrollment_chart_required_drops_nonchart_patients() -> None:
    """chart_required=Y drops any patient with a non-chart span
    overlapping the window — entirely, for that configuration."""
    dataset = mini_dataset(
        patients=(patient(1), patient(2)),
        enrollment=(
            span(1, START, START + timedelta(days=60), chart="N"),
            span(2, START, START + timedelta(days=60), chart="Y"),
        ),
        dispensings=(
            disp(1, START + timedelta(days=10), "E00001", 7, 1.0),
            disp(2, START + timedelta(days=10), "E00001", 7, 1.0),
        ),
    )
    study = two_cohort_study(cohort_overrides={"chart_required": True})
    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("enrollment_spans")
        sql = fetch_rows(harness.engine, "enrollment_spans")
    patids = {r[1] for r in sql}
    assert patids == {2}, sql
    model = enrollment_episodes(dataset, study)
    assert {patid for (_cfg, patid) in model} == {2}
