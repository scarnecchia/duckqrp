"""Arbiter probe: detailed evidence for F1 (attrition L27 forward-fill)
and F2 (npts > dennumpts). Read-only over the synth fixtures; prints
tables needed for arbitration."""

from __future__ import annotations

import sys

sys.path.insert(0, "tests")

import duckdb  # noqa: E402

from properties.conftest import expensive_settings  # noqa: F401  (marker)
from properties.harness import full_pipeline_run  # noqa: E402
from properties.test_pkg_f_meta_helpers import (  # noqa: E402
    feature_kwargs,
    wildcard_study,
)
from properties.synth import GenerateOptions, generate, study_for  # noqa: E402


def f1() -> None:
    ds = generate(patients=1, seed=0,
                  options=GenerateOptions(include_labs=True))
    study = wildcard_study(study_for(ds, **feature_kwargs(ds, ("cida",))))
    with full_pipeline_run(ds, study, output_dir=None) as result:
        eng = result.engine
        print("=== F1: generate(patients=1, seed=0), wildcard+cida ===")
        for t in ("cohort_final", "ptsmasterlist", "_att_ep", "_att_gi"):
            try:
                print(f"{t}: {eng.count(t)} rows")
            except Exception as exc:  # noqa: BLE001
                print(f"{t}: unavailable ({exc})")
        rows = eng.con.execute(
            'SELECT "group", level, claim_level, remaining, excluded, descr '
            "FROM attrition ORDER BY \"group\", level").fetchall()
        for r in rows:
            print(f"  {r[0]} L{r[1]:<5} {str(r[2]):<7} rem={r[3]!s:<5} "
                  f"exc={r[4]!s:<5} {str(r[5])[:60]}")


def f2() -> None:
    ds = generate(patients=4, seed=0,
                  options=GenerateOptions(include_labs=True))
    study = wildcard_study(study_for(ds, **feature_kwargs(ds, ("cida",))))
    with full_pipeline_run(ds, study, output_dir=None) as result:
        eng = result.engine
        print("=== F2: generate(patients=4, seed=0), wildcard+cida ===")
        con = eng.con
        print("-- denomcounts (level 001/002, agegroup rows):")
        for r in con.execute("""
                SELECT level, "group", agegroup, count(*) AS n,
                       sum(dennumpts) AS pts
                FROM denomcounts GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""").fetchall():
            print(f"  L{r[0]} {r[1]} {r[2]!s:<8} rows={r[3]} pts={r[4]}")
        print("-- numcounts 75+ cells:")
        for r in con.execute("""
                SELECT level, "group", agegroup, sex, npts
                FROM numcounts WHERE agegroup = '75+' ORDER BY 1, 2""").fetchall():
            print(f"  L{r[0]} {r[1]} {r[2]!s:<8} sex={r[3]!s:<5} npts={r[4]}")
        print("-- t2_cida cells with npts > dennumpts:")
        for r in con.execute("""
                SELECT n.level, n."group", n.agegroup, n.sex, n.npts,
                       t.dennumpts
                FROM numcounts n JOIN t2_cida t
                  ON n.level = t.level AND n."group" = t."group"
                 AND n.agegroup IS NOT DISTINCT FROM t.agegroup
                 AND n.agegroupnum IS NOT DISTINCT FROM t.agegroupnum
                 AND n.sex IS NOT DISTINCT FROM t.sex
                 AND n.race IS NOT DISTINCT FROM t.race
                 AND n.hispanic IS NOT DISTINCT FROM t.hispanic
                 AND n.index_year IS NOT DISTINCT FROM t.year
                WHERE n.npts > t.dennumpts""").fetchall():
            print(f"  L{r[0]} {r[1]} {r[2]!s:<8} sex={r[3]!s:<5} "
                  f"npts={r[4]} den={r[5]}")
        print("-- member-level: cohort_final episodes vs denom windows:")
        for r in con.execute("""
                SELECT c.cohortgrp, c.patid,
                       (SELECT max(birth_date) FROM demographics d
                         WHERE d.patid = c.patid) AS birth,
                       min(c.indexdt) AS first_index,
                       c.agegroup
                FROM cohort_final c GROUP BY 1, 2, 5 ORDER BY 1, 2""").fetchall():
            print(f"  {r[0]} {r[1]} birth={r[2]} first_index={r[3]} "
                  f"num_agegroup={r[4]}")
        try:
            print("-- denom window rows for those members:")
            for r in con.execute("""
                    SELECT w.patid, w.denom_start, w.denom_end, w.memberdays
                    FROM _denom_windows w
                    WHERE w.patid IN (SELECT DISTINCT patid FROM cohort_final)
                    ORDER BY 1, 2""").fetchall():
                print(f"  {r[0]} window=[{r[1]} .. {r[2]}] days={r[3]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  _denom_windows unavailable: {exc}")
        print("-- denomcounts agegroup assignment source check:")
        for r in con.execute("""
                SELECT d.patid, d.denom_start, a.label, a.lo, a.hi,
                       age_in_unit((SELECT max(birth_date) FROM demographics x
                                    WHERE x.patid = d.patid),
                                   d.denom_start, a.unit) AS age_at_start
                FROM _denom_demog d
                JOIN cfg_denom_strata a
                  ON a.denom_cfg_id = d.denom_cfg_id
                 AND age_in_unit((SELECT max(birth_date) FROM demographics x
                                  WHERE x.patid = d.patid),
                                 d.denom_start, a.unit) BETWEEN a.lo AND a.hi
                WHERE d.patid IN (SELECT DISTINCT patid FROM cohort_final)
                ORDER BY 1""").fetchall():
            print(f"  {r[0]} denom_start={r[1]} band={r[2]} "
                  f"[{r[3]},{r[4]}] age_at_start={r[5]}")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which in ("f1", "both"):
        f1()
    if which in ("f2", "both"):
        f2()
