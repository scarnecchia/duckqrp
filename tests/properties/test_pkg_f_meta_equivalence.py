"""Package F — cross-stage metamorphic equivalence of the real pipeline.

The promises under test (cited at point of use):

* input ROW PERMUTATION and DUCKDB THREADS 1 vs 4: README.md
  "Determinism, and a test that was lying" — every window carries a
  total order precisely so results cannot depend on parquet row order
  or thread count ("runs the same input under 1 and 4 threads").
  ASSERTED.
* input FILE PARTITIONING: docs/RUNBOOK.md documents "One folder per
  table (if your export is partitioned)" and "Several files per table"
  as supported input layouts. ASSERTED.
* COHORT BATCHING (cohorts together vs separately): no documented
  promise exists — docs/PERFORMANCE.md's cohort-config claim is a
  within-run dedup statement, not an across-runs equivalence. The
  batching test CHARACTERIZES and prints, it does not assert.

Every run uses care-setting WILDCARD studies (see
f_meta_oracle.wildcard_study): without the ('**','*') cfg_care_setting
rows a real loader emits, event_claims inner-joins to nothing and the
equivalences would compare empty against empty (campaign trap #1).
Datasets never create overlapping enrollment spans (finding B2 would
contaminate the stockpile joins).

The assertion is FULL output membership equality: every engine table
and every manifest output file, rows normalized and sorted, with
tie-payload examples pinned deterministically in their own tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from hypothesis import assume, event, example, given
from hypothesis import strategies as st

from qrp import run

from .conftest import expensive_settings
from .harness import full_pipeline_run
from .test_pkg_f_meta_helpers import (
    FEATURE_NAMES,
    all_pairs,
    assert_same_outputs,
    feature_kwargs,
    membership_diff,
    permute_dataset,
    read_engine_membership,
    read_file_membership,
    sharded_root,
    study_for_features,
    wildcard_study,
)
from .synth import GenerateOptions, SyntheticDataset, generate, hand_fixture

PAIRS = st.sampled_from(all_pairs())
DATASETS = st.builds(
    generate,
    patients=st.integers(min_value=1, max_value=4),
    seed=st.integers(min_value=0, max_value=2**31 - 1),
    # labs ON: the labs feature reads lab_result rows; without them the
    # labs stage runs but has nothing to read.
    options=st.just(GenerateOptions(include_labs=True)),
)

_HAND_DATASET, _HAND_STUDY = hand_fixture()
_HAND_FEATURES = ("mfu", "cida")


def _compare(base: Any, variant: Any, base_out: Path,
             variant_out: Path, context: str, features: str) -> None:
    """Full output membership equality between two completed runs."""
    problems = membership_diff(
        read_engine_membership(base.engine),
        read_engine_membership(variant.engine),
        f"{context}:base-engine", f"{context}:{context}-engine")
    problems += membership_diff(
        read_file_membership(base_out),
        read_file_membership(variant_out),
        f"{context}:base-files", f"{context}:{context}-files")
    if problems:
        raise AssertionError(
            f"{context} changed outputs (features={features}) —\n  "
            + "\n  ".join(problems))


def _direct_run(dataset: SyntheticDataset, study: Any, tmp: Path,
                *, threads: int = 1, sharded: bool = False) -> Any:
    """The production run() over a root this test prepared, mirroring
    full_pipeline_run (fresh tree, outputs kept, engine closed by the
    caller). Needed because the SHARDED variant must point run() at a
    pre-partitioned root, which the harness cannot express. The data
    itself is still written once, through write_parquet() (inside
    sharded_root)."""
    from .synth import write_parquet
    if sharded:
        root = sharded_root(dataset, tmp)
    else:
        root = write_parquet(dataset, tmp / "indata")
    return run(study, str(root), threads=threads, verbose=False,
               output_dir=tmp / "out")


@given(ds=DATASETS, pair=PAIRS, perm_seed=st.integers(0, 2**31 - 1))
@example(
    ds=generate(patients=1, seed=0,
                options=GenerateOptions(include_labs=True)),
    pair=("mfu", "cida"), perm_seed=0)
@expensive_settings()
def test_pipeline_metamorphic_equivalence(
        tmp_path_factory: pytest.TempPathFactory,
        ds: SyntheticDataset, pair: tuple[str, str], perm_seed: int) -> None:
    """One wildcard study, four runs: baseline; permuted input rows;
    sharded input root; threads=4 (bounded excursion, then back to 1).
    All four must produce identical output membership — engine tables
    AND manifest files, rows and tie payloads included."""
    event(f"features={pair[0]}+{pair[1]}")
    study = study_for_features(ds, pair)

    # ---- baseline: input order as generated, single files, threads=1
    base_dir = tmp_path_factory.mktemp("base")
    with full_pipeline_run(ds, study, output_dir=base_dir / "out") as base:
        # Care-setting trap guard: events must exist for the wildcarded
        # study, or every equivalence below would be empty-vs-empty.
        # Some generate() draws legitimately carry no matching exposure
        # (all-noise codes) — REJECT those as vacuous rather than fail.
        exposure_rows = base.engine.count("exposure_claims")
        event(f"exposure_rows_ge_1:{exposure_rows >= 1}")
        assume(exposure_rows > 0)
        event(f"episodes_empty:{base.engine.count('cohort_final') == 0}")

        # ---- input row permutation (README determinism promise) ----
        perm_dir = tmp_path_factory.mktemp("perm")
        with full_pipeline_run(permute_dataset(ds, perm_seed), study,
                               output_dir=perm_dir / "out") as permuted:
            problems = membership_diff(
                read_engine_membership(base.engine),
                read_engine_membership(permuted.engine),
                "base-engine", "permuted-engine")
            problems += membership_diff(
                read_file_membership(base_dir / "out"),
                read_file_membership(perm_dir / "out"),
                "base-files", "permuted-files")
            event(f"permuted_diverged:{bool(problems)}")
            assert not problems, (
                f"input row permutation changed outputs "
                f"(features={pair}) —\n  " + "\n  ".join(problems))

        # ---- input file partitioning (RUNBOOK partitioned layout) --
        shard_dir = tmp_path_factory.mktemp("shard")
        sharded = _direct_run(ds, study, shard_dir, sharded=True)
        try:
            problems = membership_diff(
                read_engine_membership(base.engine),
                read_engine_membership(sharded.engine),
                "base-engine", "sharded-engine")
            problems += membership_diff(
                read_file_membership(base_dir / "out"),
                read_file_membership(shard_dir / "out"),
                "base-files", "sharded-files")
            event(f"sharded_diverged:{bool(problems)}")
            assert not problems, (
                f"sharded input changed outputs (features={pair}) —\n  "
                + "\n  ".join(problems))
        finally:
            sharded.engine.close()

        # ---- threads 1 -> 4 -> 1 (README determinism promise) ------
        t4_dir = tmp_path_factory.mktemp("t4")
        with full_pipeline_run(ds, study, threads=4,
                               output_dir=t4_dir / "out") as fast:
            problems = membership_diff(
                read_engine_membership(base.engine),
                read_engine_membership(fast.engine),
                "base-engine", "threads4-engine")
            problems += membership_diff(
                read_file_membership(base_dir / "out"),
                read_file_membership(t4_dir / "out"),
                "base-files", "threads4-files")
            event(f"threads4_diverged:{bool(problems)}")
            assert not problems, (
                f"threads=4 changed outputs (features={pair}) —\n  "
                + "\n  ".join(problems))

        back_dir = tmp_path_factory.mktemp("back1")
        with full_pipeline_run(ds, study, threads=1,
                               output_dir=back_dir / "out") as back:
            problems = membership_diff(
                read_engine_membership(base.engine),
                read_engine_membership(back.engine),
                "base-engine", "back-to-1-engine")
            event(f"back_to_1_diverged:{bool(problems)}")
            assert not problems, (
                f"returning to threads=1 changed outputs "
                f"(features={pair}) —\n  " + "\n  ".join(problems))


# ---------------------------------------------------------------------
# Deterministic pins on the hand fixture (known ties, known members).
# ---------------------------------------------------------------------


def test_tie_payloads_identical_across_permutation_and_threads(
        tmp_path: Path) -> None:
    """The P4 tie (two same-day/same-supply dispensings, amounts 5.0 and
    7.5) must produce the same SELECTED payload in every variant: the
    stockpile sums the tied dispensings, so stockpiled and
    index_candidates carry rxsup 30+30=60, rxamt 5.0+7.5=12.5,
    numdispensing=2. Payload asserted, not just stability."""
    study = wildcard_study(_HAND_STUDY)
    expected = {"stockpiled": (60, 12.5, 2),
                "index_candidates": (60, 12.5, 2)}

    def payload(result: Any, table: str) -> tuple[Any, ...]:
        rows = result.engine.con.execute(
            f"SELECT rxsup, rxamt, numdispensing FROM {table} "
            "WHERE patid = 4").fetchall()
        assert len(rows) == 1, f"{table} should carry one P4 row: {rows}"
        return rows[0]

    base_dir = tmp_path / "base"
    with full_pipeline_run(_HAND_DATASET, study,
                           output_dir=base_dir / "out") as base:
        with full_pipeline_run(permute_dataset(_HAND_DATASET, 7), study,
                               output_dir=tmp_path / "perm" / "out") as perm:
            with full_pipeline_run(_HAND_DATASET, study, threads=4,
                                   output_dir=tmp_path / "t4" / "out") as t4:
                for table, want in expected.items():
                    got_base = payload(base, table)
                    assert got_base == want, (
                        f"tie payload wrong in {table} (baseline): "
                        f"{got_base} != {want}")
                    assert payload(perm, table) == want, (
                        f"tie payload changed under row permutation "
                        f"({table})")
                    assert payload(t4, table) == want, (
                        f"tie payload changed under threads=4 ({table})")


def test_dense_feature_combination_equivalence(tmp_path: Path) -> None:
    """ALL optional features at once (plus labs-bearing data): a dense
    combination selected for its interactions — covariates make
    cohort_claims materialise, utilization/risk-scores feed baseline,
    both strata features share cfg_strata."""
    from .synth import study_for
    ds = generate(patients=3, seed=2,
                  options=GenerateOptions(include_labs=True))
    study = wildcard_study(
        study_for(ds, **feature_kwargs(ds, FEATURE_NAMES)))
    base_dir = tmp_path / "base"
    with full_pipeline_run(ds, study, output_dir=base_dir / "out") as base:
        with full_pipeline_run(permute_dataset(ds, 11), study,
                               output_dir=tmp_path / "perm" / "out") as perm:
            assert_same_outputs(base, base_dir / "out",
                                perm, tmp_path / "perm" / "out",
                                "dense-permutation")
        with full_pipeline_run(ds, study, threads=4,
                               output_dir=tmp_path / "t4" / "out") as fast:
            assert_same_outputs(base, base_dir / "out",
                                fast, tmp_path / "t4" / "out",
                                "dense-threads4")


def test_sharded_input_equivalence_hand_fixture(tmp_path: Path) -> None:
    """Sharded 'one folder per table' layout against single files, on
    the hand fixture (deterministic companion to the hypothesis
    sharding dimension)."""
    study = wildcard_study(_HAND_STUDY)
    base_dir = tmp_path / "base"
    with full_pipeline_run(_HAND_DATASET, study,
                           output_dir=base_dir / "out") as base:
        root = sharded_root(_HAND_DATASET, tmp_path / "shard")
        sharded = run(study, str(root), threads=1, verbose=False,
                      output_dir=tmp_path / "shard" / "out")
        try:
            assert_same_outputs(base, base_dir / "out",
                                sharded, tmp_path / "shard" / "out",
                                "sharded-hand")
        finally:
            sharded.engine.close()


# ---------------------------------------------------------------------
# Cohort batching: CHARACTERIZATION ONLY (no documented promise).
# ---------------------------------------------------------------------


def test_cohort_batching_characterization(tmp_path: Path) -> None:
    """Cohorts run TOGETHER vs SEPARATELY. No documented promise covers
    this (see module docstring), so this test CHARACTERIZES: it asserts
    only that the separate runs complete and carry per-cohort tables,
    computes the per-table agreement, and PRINTS the divergence rate
    for the campaign ledger. A divergence printed here is a LEAD, not a
    demonstrated contract violation."""
    ds = generate(patients=3, seed=3,
                  options=GenerateOptions(include_labs=True))
    study = wildcard_study(_batching_study(ds))

    together_dir = tmp_path / "together"
    with full_pipeline_run(ds, study, output_dir=together_dir / "out") as tog:
        tables = sorted(read_engine_membership(tog.engine))
        agree, differ, not_comparable = [], [], []
        for cohort in ("cohort_a", "cohort_b"):
            solo_study = wildcard_study(replace_cohorts(
                _batching_study(ds), cohort))
            solo_dir = tmp_path / f"solo-{cohort}"
            solo = run(solo_study, str(_write_root(ds, solo_dir)),
                       threads=1, verbose=False,
                       output_dir=solo_dir / "out")
            try:
                solo_rows = read_engine_membership(solo.engine)
                for table in tables:
                    if table in ("runtimes", "signature"):
                        continue
                    tog_rows = _filter_cohort(tog.engine, table, cohort)
                    if tog_rows is None:
                        not_comparable.append(table)
                        continue
                    if tog_rows == solo_rows.get(table):
                        agree.append(f"{cohort}:{table}")
                    else:
                        differ.append(f"{cohort}:{table}")
            finally:
                solo.engine.close()
    print(f"[batching characterization] agree={len(agree)} "
          f"differ={len(differ)} not_cohort_keyed={len(set(not_comparable))}")
    if differ:
        print(f"[batching characterization] DIVERGENT tables: {differ}")
    else:
        print("[batching characterization] together vs separately "
              "identical on every cohort-keyed table in this fixture")
    # Structural assertions only (not the equivalence itself).
    assert tables, "together run produced no tables"
    assert agree or differ, (
        "no cohort-keyed table was comparable; the characterization "
        "ran on nothing")


def _batching_study(ds: SyntheticDataset) -> Any:
    from .synth import study_for
    return study_for(ds, **feature_kwargs(ds, ("mfu", "cida")))


def _write_root(ds: SyntheticDataset, base: Path) -> Path:  # noqa: ARG001
    from .synth import write_parquet
    return write_parquet(ds, base / "indata")


def replace_cohorts(study: Any, cohortgrp: str) -> Any:
    from dataclasses import replace
    keep = tuple(c for c in study.cohorts if c.cohortgrp == cohortgrp)
    assert keep, f"{cohortgrp} not in study"
    return replace(study, cohorts=keep)


def _filter_cohort(engine: Any, table: str,
                   cohortgrp: str) -> list[str] | None:
    """Rows of `table` for one cohort, normalized; None when the table
    has no cohortgrp column (not cohort-keyed)."""
    from .test_pkg_f_meta_helpers import _norm_rows
    cols = [str(d[0]).lower() for d in engine.con.execute(
        f"SELECT * FROM {table} LIMIT 0").description]
    if "cohortgrp" not in cols:
        return None
    rows = engine.con.execute(
        f"SELECT * FROM {table} WHERE cohortgrp = ?", (cohortgrp,)
    ).fetchall()
    return _norm_rows(rows)
