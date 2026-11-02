"""Self-tests for the Parquet full-pipeline harness.

Before the harness is trusted it must (a) run the real end-to-end
pipeline over a SHIPPED study file into a real output tree, (b) agree
table-for-table with the stage harness on the same fixture, and
(c) stay isolated and closed across repeated runs.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pytest

from qrp import load_study

from .harness import StageHarness, fetch_rows, full_pipeline_run
from .synth import hand_fixture

_STUDY = Path(__file__).resolve().parents[2] / "study" / "demo_type2.json"


def _digest(rows: tuple[tuple[object, ...], ...]) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(repr(row).encode())
    return h.hexdigest()


def test_parquet_harness_runs_shipped_study_end_to_end(tmp_path: Path) -> None:
    """The hand fixture through load_study(demo_type2.json): the real
    study file, a real SCDM parquet root, real output layout."""
    dataset, _ = hand_fixture()
    study = load_study(_STUDY)
    out = tmp_path / "results"

    with full_pipeline_run(dataset, study, output_dir=out) as result:
        assert result.seconds > 0
        # Output split layout, as documented in the README.
        assert (out / "dplocal").is_dir()
        assert (out / "msoc").is_dir()
        # Membership: P1 is a lisinopril member (E00001), P4 a
        # beta_blocker member (E00021); P2/P3 are not members.
        mstr = result.engine.con.execute(
            "SELECT cohortgrp, patid, COUNT(*) FROM ptsmasterlist "
            "GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall()
        assert mstr == [("beta_blocker", 4, 1), ("lisinopril", 1, 1)]
    # The engine was closed by the harness context.
    with pytest.raises(duckdb.Error):
        result.engine.count("ptsmasterlist")


def test_stage_harness_matches_full_pipeline() -> None:
    """Fidelity cross-check: the stage harness and the real pipeline
    must agree table-for-table on the same fixture and study.

    This is the test that earns the stage harness its trust: any
    divergence between the harness's fmt/glue mirror and run() shows up
    here before it can hide inside a property.
    """
    dataset, study = hand_fixture()
    tables = (
        "demographics", "deaths", "cdm_dispensing", "cdm_diagnosis",
        "exposure_claims", "ptsmasterlist", "attrition", "denominators",
    )

    with StageHarness(study) as harness:
        harness.attach_dataset(dataset)
        harness.run_through("attrition + denominators")
        harness_rows = {
            t: _digest(fetch_rows(harness.engine, t)) for t in tables}

    with full_pipeline_run(dataset, study, write_outputs=False) as result:
        pipeline_rows = {
            t: _digest(fetch_rows(result.engine, t)) for t in tables}

    assert harness_rows == pipeline_rows


def test_full_pipeline_repeated_runs_are_identical() -> None:
    """Two isolated full-pipeline runs of the same fixture agree; both
    engines are closed by the context manager."""
    dataset, study = hand_fixture()

    digests: list[str] = []
    engines: list[object] = []
    for _ in range(2):
        with full_pipeline_run(dataset, study, write_outputs=False) as result:
            engines.append(result.engine)
            digests.append(_digest(fetch_rows(result.engine, "ptsmasterlist")))
    assert digests[0] == digests[1]
    for engine in engines:
        with pytest.raises(duckdb.Error):
            engine.count("ptsmasterlist")  # type: ignore[attr-defined]


def test_full_pipeline_handles_empty_claims(tmp_path: Path) -> None:
    """A fixture with no matching exposure at all runs to completion
    with empty outputs — the loud-empty path, not an error."""
    dataset, study = hand_fixture()
    stripped = type(dataset)(
        patients=dataset.patients,
        deaths=dataset.deaths,
        enrollment=dataset.enrollment,
        dispensings=(),
        diagnoses=dataset.diagnoses,
        procedures=dataset.procedures,
        encounters=dataset.encounters,
        labs=dataset.labs,
        exposure_codes=dataset.exposure_codes,
        event_codes=dataset.event_codes,
        noise_drug_codes=dataset.noise_drug_codes,
        noise_dx_codes=dataset.noise_dx_codes,
        start=dataset.start,
        end=dataset.end,
    )
    with full_pipeline_run(stripped, study, output_dir=tmp_path / "out") as r:
        assert r.engine.count("exposure_claims") == 0
        assert r.engine.count("ptsmasterlist") == 0
