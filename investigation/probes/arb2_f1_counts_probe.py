"""Arbiter probe 2: dump _att_counts and the carried windows for F1."""

from __future__ import annotations

import sys

sys.path.insert(0, "tests")

from properties.harness import full_pipeline_run  # noqa: E402
from properties.test_pkg_f_meta_helpers import (  # noqa: E402
    feature_kwargs,
    wildcard_study,
)
from properties.synth import GenerateOptions, generate, study_for  # noqa: E402


def main() -> None:
    ds = generate(patients=1, seed=0,
                  options=GenerateOptions(include_labs=True))
    study = wildcard_study(study_for(ds, **feature_kwargs(ds, ("cida",))))
    with full_pipeline_run(ds, study, output_dir=None) as result:
        con = result.engine.con
        print("=== _att_counts raw rows ===")
        for r in con.execute(
                "SELECT cohortgrp, level, n FROM _att_counts "
                "ORDER BY cohortgrp, level").fetchall():
            print(f"  {r[0]} L{r[1]:<5} n={r[2]!r}")
        print("=== _att_spans ===")
        for r in con.execute(
                "SELECT enr_cfg_id, count(DISTINCT patid) FROM _att_spans "
                "GROUP BY 1").fetchall():
            print(f"  enr_cfg_id={r[0]} members={r[1]}")
        print("=== _att_dem/enrollment L1 ===")
        print(" ", con.execute(
            "SELECT count(*) FROM _att_dem m SEMI JOIN cdm_enrollment e "
            "ON e.patid = m.patid").fetchone())
        print("=== grid carried for cohort_a (kind partition TRUE) ===")
        for r in con.execute("""
                WITH skel(level, kind) AS (VALUES
                    (1.0,'init'),(2.0,'excl'),(3.0,'excl'),(4.0,'excl'),
                    (5.0,'excl'),(6.0,'excl'),(7.0,'init')),
                grid AS (
                    SELECT c.cohortgrp, k.level, k.kind, n.n
                    FROM cfg_cohort c CROSS JOIN skel k
                    LEFT JOIN _att_counts n
                      ON n.cohortgrp = c.cohortgrp AND n.level = k.level)
                SELECT level, kind, n,
                       lag(n) OVER (PARTITION BY cohortgrp,
                                    (kind IN ('init','excl'))
                                    ORDER BY level) AS prev_n,
                       coalesce(n, last_value(n IGNORE NULLS) OVER (
                           PARTITION BY cohortgrp ORDER BY level
                           ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
                       ) AS remaining
                FROM grid WHERE cohortgrp = (SELECT min(cohortgrp) FROM cfg_cohort) ORDER BY level""").fetchall():
            print(f"  L{r[0]:<4} {r[1]:<5} n={r[2]!r} prev_n={r[3]!r} "
                  f"remaining={r[4]!r}")


if __name__ == "__main__":
    main()
