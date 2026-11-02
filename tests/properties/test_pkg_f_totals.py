"""Package F — totals reconciliation from emitted outputs.

Nonnegative totals are not proof of correctness: a total that is
internally consistent with a MISSING member is exactly the failure mode
this family hunts. Each reconciliation recomputes a headline total from
one artifact and checks it against a DIFFERENT artifact:

* attrition level 1 (initial members) against the normalized
  demographics table (engine);
* funnel monotonicity inside the member block (levels 1-6) and the
  episode block (7-26), with `excluded` never negative — SAS never
  subtracts across a change of unit (docs/OUTPUTS.md);
* cohort_final members ⊆ ptsmasterlist members ⊆ demographics patids;
* t2_cida is a merge of numcounts and denomcounts BY NAME
  (docs/OUTPUTS.md): every numcounts row must appear in t2_cida with
  its own numerator values and the denominator of the same key.

Two further reconciliations are expected-failures (xfail,
strict=False) because the pipeline currently violates them on minimal
fixtures — kept visible on every run rather than dropped; each has a
deterministic pin and a lead entry in investigation/packages/F.md:

* F1 — attrition level 27 ("unique number of members in final
  cohort", the row's own declared meaning) forward-fills the last
  member count through uncounted episode levels, so an empty
  cohort_final coexists with a nonzero final member count;
* F2 — a numerator cell can exceed its denominator (npts >
  dennumpts) when the numerator's age stratum for a member differs
  from the denominator's (a 75+ episode counted against a denominator
  with no 75+ members).

Output files are read through a FRESH duckdb connection — an
independent path from the engine that wrote them.
"""

from __future__ import annotations

import json
from typing import Any
from pathlib import Path

import duckdb
import pytest
from hypothesis import event, given
from hypothesis import strategies as st

from .conftest import expensive_settings
from .harness import full_pipeline_run
from .test_pkg_f_meta_helpers import wildcard_study
from .synth import GenerateOptions, SyntheticDataset, generate, study_for

DATASETS = st.builds(
    generate,
    patients=st.integers(min_value=1, max_value=4),
    seed=st.integers(min_value=0, max_value=2**31 - 1),
    options=st.just(GenerateOptions(include_labs=True)),
)

# Stratify the CIDA outputs so the reconciliation is not only ever the
# unstratified level: overall (000), agegroup (001), agegroup*sex (002).
STRATA = (("t2cida", "000", ()),
          ("t2cida", "001", ("agegroup",)),
          ("t2cida", "002", ("agegroup", "sex")))


def _study(ds: SyntheticDataset) -> object:
    from .test_pkg_f_meta_helpers import feature_kwargs
    return wildcard_study(study_for(ds, **feature_kwargs(ds, ("cida",))))


def _src(out: Path, name: str) -> str:
    """Resolve a manifest table to a read_parquet() argument."""
    manifest = json.loads((out / "manifest.json").read_text())
    entry = manifest["tables"][name]
    base = out / entry["library"] / entry["file"]
    if base.is_dir():
        return f"{base}/**/*.parquet"
    return str(base.with_suffix(".parquet"))


def _scalar(con: duckdb.DuckDBPyConnection, sql: str,
            params: list[object] | None = None) -> object:
    row = con.execute(sql, params or []).fetchone()
    return row[0] if row else None


@given(ds=DATASETS)
@expensive_settings()
def test_totals_reconcile_across_outputs(
        tmp_path_factory: pytest.TempPathFactory,
        ds: SyntheticDataset) -> None:
    """Headline totals recomputed from one artifact must equal the
    membership another artifact shows — per group, per level."""
    event(f"patients:{len(ds.patients)}")
    out = tmp_path_factory.mktemp("totals") / "out"
    with full_pipeline_run(ds, _study(ds), output_dir=out) as result:
        eng = result.engine
        con = duckdb.connect()
        try:
            attrition = _src(out, "attrition")

            # ---- level 1 == normalized demographics members ----------
            demographics = _scalar(eng.con,
                                   "SELECT count(*) FROM demographics")
            l1 = con.execute(
                'SELECT DISTINCT "group", remaining FROM read_parquet(?) '
                "WHERE level = 1.0", [attrition]).fetchall()
            event(f"l1_groups:{len(l1)}")
            assert l1, "attrition has no level-1 rows"
            for group, remaining in l1:
                assert remaining == demographics, (
                    f"attrition level-1 remaining for {group} is "
                    f"{remaining}, demographics has {demographics} "
                    f"members")

            # Level 27 vs cohort_final now ASSERTED below
            # (test_attrition_final_count_matches_cohort_final): fixed
            # by fix2-f1 — a counted step whose relation is empty
            # counts 0 (SAS's count(*) semantics,
            # ms_attrition_cidacompute.sas:77-88), so the funnel can no
            # longer forward-fill member counts into level 27.

            # ---- funnel monotonicity, no negative exclusions ---------
            rows = con.execute(
                'SELECT "group", level, remaining, excluded, claim_level '
                "FROM read_parquet(?) ORDER BY \"group\", level",
                [attrition]).fetchall()
            by_group: dict[str, list[tuple[float, float, str]]] = {}
            for group, level, remaining, excluded, _claim in rows:
                assert excluded is None or excluded >= 0, (
                    f"negative excluded at {group} level {level}")
                if remaining is not None:
                    by_group.setdefault(group, []).append(
                        (float(level), float(remaining),
                         claim_level_of(_claim)))
            for group, levels in by_group.items():
                _assert_monotone(group, levels)

            # ---- membership chain ------------------------------------
            missing_mstr = _scalar(eng.con, """
                SELECT count(*) FROM (
                    SELECT DISTINCT cohortgrp, patid FROM cohort_final
                ) f LEFT JOIN (
                    SELECT DISTINCT cohortgrp, patid FROM ptsmasterlist
                ) m USING (cohortgrp, patid)
                WHERE m.patid IS NULL""")
            assert missing_mstr == 0, (
                f"{missing_mstr} final-cohort members missing from the "
                f"master list — a total internally consistent with a "
                f"missing member")
            missing_demo = _scalar(eng.con, """
                SELECT count(*) FROM (
                    SELECT DISTINCT patid FROM ptsmasterlist
                ) m LEFT JOIN (
                    SELECT DISTINCT patid FROM demographics
                ) d USING (patid)
                WHERE d.patid IS NULL""")
            assert missing_demo == 0, (
                f"{missing_demo} master-list members have no "
                f"demographics row")

            # ---- t2_cida merges numcounts + denomcounts by name ------
            num_src, cida_src = _src(out, "numcounts"), _src(out, "t2_cida")
            # NULL-safe join: agegroup/sex/etc. are NULL at aggregate
            # levels, and a plain equality would drop those rows.
            join = ("n.level = t.level AND n.\"group\" = t.\"group\" "
                    "AND n.agegroup IS NOT DISTINCT FROM t.agegroup "
                    "AND n.agegroupnum IS NOT DISTINCT FROM t.agegroupnum "
                    "AND n.sex IS NOT DISTINCT FROM t.sex "
                    "AND n.race IS NOT DISTINCT FROM t.race "
                    "AND n.hispanic IS NOT DISTINCT FROM t.hispanic "
                    "AND n.index_year IS NOT DISTINCT FROM t.year")
            lost = _scalar(con, f"""
                SELECT count(*) FROM read_parquet(?) n
                LEFT JOIN read_parquet(?) t ON {join}
                WHERE t.npts IS NULL""",
                [num_src, cida_src])
            assert lost == 0, (
                f"{lost} numcounts rows have no t2_cida row at the same "
                f"key — the BY NAME merge lost rows")
            wrong_num = _scalar(con, f"""
                SELECT count(*) FROM read_parquet(?) n
                JOIN read_parquet(?) t ON {join}
                WHERE n.npts IS DISTINCT FROM t.npts""",
                [num_src, cida_src])
            assert wrong_num == 0, (
                f"{wrong_num} t2_cida rows disagree with numcounts on "
                f"npts at the same key")
            # npts <= dennumpts now ASSERTED below
            # (test_numerator_never_exceeds_denominator): fixed by
            # fix2-f2 — the denominator splits each eligible window at
            # the age thresholds (ms_cidadenom.sas:1036-1057), so an
            # episode's index-date band always has its members in the
            # denominator too.
            event("reconciled:true")
        finally:
            con.close()


def claim_level_of(claim: object) -> str:
    return str(claim or "")


def _assert_monotone(group: str,
                     levels: list[tuple[float, float, str]]) -> None:
    """remaining never increases within the member block (levels 1-6)
    or the episode block (7-26). The level-27 member count is a
    different unit again and is reconciled separately."""
    member = [(lv, rem) for lv, rem, claim in levels
              if claim == "Member" and 1.0 <= lv <= 6.0]
    episode = [(lv, rem) for lv, rem, claim in levels
               if claim == "Episode"]
    for block_name, block in (("member", member), ("episode", episode)):
        ordered = sorted(block)
        for (lv_a, rem_a), (lv_b, rem_b) in zip(ordered, ordered[1:],
                                                strict=False):
            assert rem_b <= rem_a, (
                f"attrition funnel for {group} increases inside the "
                f"{block_name} block: level {lv_a} has {rem_a} but "
                f"level {lv_b} has {rem_b}")


@given(ds=DATASETS)
@expensive_settings()
def test_denominator_memberdays_cover_members(
        tmp_path_factory: pytest.TempPathFactory,
        ds: SyntheticDataset) -> None:
    """A member contributes at least one day: dennumpts <=
    dennummemdays wherever the member-days metric was computed (it is
    NULL, not zero, for OUTPUTDENOM='M' cohorts — docs/OUTPUTS.md)."""
    out = tmp_path_factory.mktemp("denom") / "out"
    with full_pipeline_run(ds, _study(ds), output_dir=out):
        con = duckdb.connect()
        try:
            bad = _scalar(con, """
                SELECT count(*) FROM read_parquet(?)
                WHERE dennumpts IS NOT NULL AND dennummemdays IS NOT NULL
                  AND dennummemdays < dennumpts""",
                [_src(out, "denomcounts")])
            assert bad == 0, (
                f"{bad} denominator rows report fewer member-days than "
                f"members")
            event("denom_days_ok:true")
        finally:
            con.close()


# ---------------------------------------------------------------------
# Leads F1 / F2: documented contracts the pipeline currently violates
# on minimal fixtures. xfail(strict=False): the failure stays visible
# in every run; a fix turns these into XPASS rather than into silence.
# ---------------------------------------------------------------------


def _l27_vs_final(out: Path, eng: Any) -> list[str]:
    con = duckdb.connect()
    try:
        problems = []
        attrition = _src(out, "attrition")
        for group, remaining in con.execute(
                'SELECT "group", remaining FROM read_parquet(?) '
                "WHERE level = 27.0", [attrition]).fetchall():
            final_n = _scalar(
                eng.con,
                "SELECT count(DISTINCT patid) FROM cohort_final "
                "WHERE cohortgrp = ?", [group])
            if remaining != final_n:
                problems.append(
                    f"{group}: attrition level-27 remaining={remaining!r} "
                    f"but cohort_final holds {final_n} distinct members")
        return problems
    finally:
        con.close()


@given(ds=DATASETS)
@expensive_settings()
def test_attrition_final_count_matches_cohort_final(
        tmp_path_factory: pytest.TempPathFactory,
        ds: SyntheticDataset) -> None:
    """attrition level 27's own descr DECLARES the contract: 'Unique
    number of members in final cohort'. Reconcile it against
    cohort_final membership. FIXED (fix2-f1): a counted step whose
    relation is empty counts 0 (SAS's count(*) semantics,
    ms_attrition_cidacompute.sas:77-88), so the funnel cannot
    forward-fill member counts into the final row."""
    out = tmp_path_factory.mktemp("l27") / "out"
    with full_pipeline_run(ds, _study(ds), output_dir=out) as result:
        problems = _l27_vs_final(out, result.engine)
        event(f"l27_problems:{len(problems)}")
        assert not problems, "\n".join(problems)


def test_attrition_final_count_pin_f1(tmp_path: Path) -> None:
    """Deterministic pin for the FIXED F1 semantics: one patient, seed 0
    — the funnel ends at the actual cohort_final size (0 members: both
    index candidates die downstream and their exclusion lands on the
    first empty counted step), not at a forward-filled member count.
    Was xfail before fix2-f1."""
    ds = generate(patients=1, seed=0,
                  options=GenerateOptions(include_labs=True))
    out = tmp_path / "out"
    with full_pipeline_run(ds, _study(ds), output_dir=out) as result:
        eng = result.engine
        assert eng.count("cohort_final") == 0
        assert eng.count("ptsmasterlist") == 0
        problems = _l27_vs_final(out, eng)
        assert not problems, (
            "attrition level 27 contradicts cohort_final —\n  "
            + "\n  ".join(problems))


def _num_gt_den_rows(out: Path) -> list[tuple[Any, ...]]:
    con = duckdb.connect()
    try:
        n, t = _src(out, "numcounts"), _src(out, "t2_cida")
        join = ('n.level = t.level AND n."group" = t."group" '
                "AND n.agegroup IS NOT DISTINCT FROM t.agegroup "
                "AND n.agegroupnum IS NOT DISTINCT FROM t.agegroupnum "
                "AND n.sex IS NOT DISTINCT FROM t.sex "
                "AND n.race IS NOT DISTINCT FROM t.race "
                "AND n.hispanic IS NOT DISTINCT FROM t.hispanic "
                "AND n.index_year IS NOT DISTINCT FROM t.year")
        return con.execute(f"""
            SELECT n.level, n."group", n.agegroup, n.sex, n.index_year,
                   n.npts, t.dennumpts
            FROM read_parquet('{n}') n
            JOIN read_parquet('{t}') t ON {join}
            WHERE t.dennumpts IS NOT NULL AND n.npts > t.dennumpts"""
        ).fetchall()
    finally:
        con.close()


@given(ds=DATASETS)
@expensive_settings()
def test_numerator_never_exceeds_denominator(
        tmp_path_factory: pytest.TempPathFactory,
        ds: SyntheticDataset) -> None:
    """A numerator counts members of the population the denominator
    counts: npts <= dennumpts at every shared key. FIXED (fix2-f2): the
    denominator splits each eligible window at the age thresholds
    (ms_cidadenom.sas:1036-1057), so an episode's index-date band is
    always one of the bands the member contributes to."""
    out = tmp_path_factory.mktemp("numden") / "out"
    with full_pipeline_run(ds, _study(ds), output_dir=out):
        rows = _num_gt_den_rows(out)
        event(f"num_gt_den_rows:{len(rows)}")
        assert not rows, (
            f"npts > dennumpts at {len(rows)} key(s), e.g. "
            f"{rows[:3]}")


def test_numerator_denominator_pin_f2(tmp_path: Path) -> None:
    """Deterministic pin for the FIXED F2 semantics: four patients,
    seed 0 — every numerator cell is backed by its denominator (the
    threshold-crossing member contributes to BOTH bands, so the 75+
    cell exists with dennumpts >= 1). Was xfail before fix2-f2."""
    ds = generate(patients=4, seed=0,
                  options=GenerateOptions(include_labs=True))
    out = tmp_path / "out"
    with full_pipeline_run(ds, _study(ds), output_dir=out):
        rows = _num_gt_den_rows(out)
        assert not rows, (
            "npts exceeds dennumpts —\n  "
            + "\n  ".join(str(r) for r in rows))
