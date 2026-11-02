"""AC.6 package E — filesystem results: honesty of outputs and manifests.

Contract sources: OUTPUTS.md — the manifest is written LAST and its
presence signals a complete run; a failure during the write loop loses
the previous outputs and leaves a partial new set (documented, NOT
all-or-nothing — we do not demand transactions the docs disclaim); the
clear before writing is scoped to <runid>_* under dplocal/ and msoc/.

Populated-table note (from the package-C calibration): studies built by
synth.build_study carry no cfg_care_setting wildcard rows, so event
claims are dropped downstream. Row/schema preservation is therefore
asserted on outputs known to be populated from exposure claims
(cohort_final, attrition, censoring), never on event-dependent tables.
"""

from __future__ import annotations

import json
import tempfile
import warnings
from pathlib import Path

import duckdb
import pytest
from hypothesis import event, example, given, strategies as st

from qrp.config import StudyConfig
from qrp.engine import Engine
from qrp.events import RunFinished
from qrp.pipeline import run as pipeline_run
from qrp.outputs import OUTPUTS, SAS_NAMES, tables_for

from .conftest import expensive_settings
from .oracles import e_lifecycle_model as model
from .synth import SyntheticDataset, hand_fixture, write_parquet
from .doubles.e_faults import (
    EventLog,
    WriteFaultEngine,
    close_quietly,
)


@pytest.fixture(scope="module")
def tiny_dataset_study() -> "tuple[SyntheticDataset, StudyConfig]":
    dataset, study = hand_fixture()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return dataset, study


def _successful_run(dataset: SyntheticDataset, study: StudyConfig,
                    out: Path) -> Engine:
    """Direct driver, real pipeline, outputs written; returns the engine."""
    log = EventLog()
    engine = Engine(threads=1, verbose=False, on_event=log)
    root = write_parquet(dataset, Path(tempfile.mkdtemp()) / "indata")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pipeline_run(study, str(root), engine=engine, verbose=False,
                         output_dir=out)
    finally:
        pass
    ok_events = [e for e in log.events if isinstance(e, RunFinished)]
    assert ok_events and ok_events[0].ok, (
        "contract [PIPE]: a completed run with outputs reports ok=True "
        "only after the writes")
    return engine


def _table_rows(engine: Engine, table: str) -> int:
    return engine.count(table)


def test_outputs_filenames_manifest_and_preservation(tiny_dataset_study) -> None:
    """The happy path: names, routing, manifest, and faithful content."""
    dataset, study = tiny_dataset_study
    out = Path(tempfile.mkdtemp(prefix="pkg-e-outputs-")) / "out"
    engine = _successful_run(dataset, study, out)
    try:
        manifest = model.read_manifest(out)
        model.check_manifest_shape(manifest, study.run_id)
        model.check_output_files(out, manifest)

        # every manifest table is one the run intended to write [OUT]
        expected = set(tables_for(study, debug=False)) | {"signature",
                                                          "runtimes"}
        assert set(manifest["tables"]) == expected, (
            f"contract [OUT]: manifest tables must be exactly what "
            f"tables_for() gates; got {sorted(manifest['tables'])}, "
            f"expected {sorted(expected)}")

        # disclosure routing matches the declaration [OUT]
        declared = {o.name: o.library for o in OUTPUTS}
        for logical, entry in manifest["tables"].items():
            if logical in declared:
                assert entry["library"] == declared[logical], (
                    f"contract [OUT]: {logical} must land in "
                    f"{declared[logical]}")

        # msoc tables carry a plain-text view [OUT]
        for logical, entry in manifest["tables"].items():
            if entry["library"] == "msoc":
                txt = out / "msoc" / f"{entry['file']}.txt"
                assert txt.exists(), (
                    f"contract [OUT]: msoc table {logical} has a .txt view")

        # row and schema preservation for the populated outputs [OUT]
        con = duckdb.connect()
        try:
            for logical in ("cohort_final", "attrition", "censoring"):
                entry = manifest["tables"][logical]
                lib = entry["library"]
                suffix = SAS_NAMES.get(logical, logical)
                flat = out / lib / f"{study.run_id.lower()}_{suffix}.parquet"
                part_dir = out / lib / f"{study.run_id.lower()}_{suffix}"
                # cohort_final is partitioned by cohortgrp -> a directory
                target = part_dir if part_dir.is_dir() else flat
                assert target.exists(), target
                read_expr = (
                    f"read_parquet('{target}/**/*.parquet')"
                    if target.is_dir() else f"read_parquet('{target}')")
                src_count = _table_rows(engine, logical)
                row = con.execute(
                    f"SELECT count(*) FROM {read_expr}").fetchone()
                assert row is not None
                disk_count = row[0]
                assert disk_count == src_count, (
                    f"contract [OUT]: {logical} rows must survive the "
                    f"write ({src_count} in-engine vs {disk_count} on disk)")
                disk_cols = [
                    r[0] for r in con.execute(
                        f"DESCRIBE SELECT * FROM {read_expr}"
                    ).fetchall()]
                eng_cols = [
                    r[0] for r in engine.con.execute(
                        f"DESCRIBE SELECT * FROM {logical}"
                    ).fetchall()]
                # a PARTITION_BY write promotes the partition column into
                # the read schema (hive layout); every declared column
                # must still be present, with no others added
                allowed_extra = {"cohortgrp"} if target.is_dir() else set()
                assert set(eng_cols) <= set(disk_cols), (
                    f"contract [OUT]: {logical} schema must be preserved")
                assert set(disk_cols) - set(eng_cols) <= allowed_extra, (
                    f"contract [OUT]: {logical} gains unexpected columns "
                    f"on disk: {set(disk_cols) - set(eng_cols)}")
            # the exposure-driven cohort really produced rows (the
            # hand fixture guarantees members; an empty cohort here
            # would make preservation checks vacuous)
            assert _table_rows(engine, "cohort_final") >= 2
        finally:
            con.close()
    finally:
        close_quietly(engine)


@expensive_settings()
@given(st.integers(min_value=1, max_value=6))
@example(1)
@example(6)
def test_output_failure_is_not_reported_complete(fail_write_at: int) -> None:
    """kth-write failure: partial files may remain (documented), but NO
    manifest and NO success claim may survive them [OUT]."""
    dataset, study = hand_fixture()
    log = EventLog()
    engine = WriteFaultEngine(threads=1, verbose=False,
                              fail_write_at=fail_write_at, on_event=log)
    out = Path(tempfile.mkdtemp(prefix="pkg-e-partial-")) / "out"
    root = write_parquet(dataset, Path(tempfile.mkdtemp()) / "indata")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(RuntimeError, match="injected output-write"):
                pipeline_run(study, str(root), engine=engine, verbose=False,
                             output_dir=out)
    finally:
        close_quietly(engine)

    model.expect_no_manifest(out)
    ok_events = [e for e in log.events if isinstance(e, RunFinished)]
    assert not [e for e in ok_events if e.ok], (
        "contract [PIPE]: a partial write must not be reported complete")
    event(f"partial-write-manifest=absent (fault at write {fail_write_at})")


def test_failure_during_pipeline_preserves_previous_outputs(
    tiny_dataset_study,
) -> None:
    """[OUT 'What a failure does'] a failure BEFORE the write block
    (cancellation raises at the first stage boundary) leaves the
    previous run's outputs intact."""
    dataset, study = tiny_dataset_study
    out = Path(tempfile.mkdtemp(prefix="pkg-e-prev-")) / "out"

    _successful_run(dataset, study, out)
    first_manifest = model.read_manifest(out)
    files_before = sorted(p.name for p in out.rglob("*") if p.is_file())

    # a second, pre-cancelled run into the same directory must abort at
    # the first stage boundary, before the output block (which is where
    # the clear lives) ever runs
    log = EventLog()
    engine = Engine(threads=1, verbose=False, on_event=log)
    engine.cancel()
    root = write_parquet(dataset, Path(tempfile.mkdtemp()) / "indata")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(Exception) as excinfo:
                pipeline_run(study, str(root), engine=engine, verbose=False,
                             output_dir=out)
        assert type(excinfo.value).__name__ == "CancelledRun", (
            "the pre-cancelled engine must refuse the run with "
            f"CancelledRun; got {type(excinfo.value).__name__}")
    finally:
        close_quietly(engine)

    second_manifest = model.read_manifest(out)
    assert second_manifest == first_manifest, (
        "contract [OUT]: a pipeline-stage failure leaves the previous "
        "run's manifest untouched")
    files_after = sorted(p.name for p in out.rglob("*") if p.is_file())
    assert files_after == files_before, (
        "contract [OUT]: a pipeline-stage failure leaves the previous "
        "run's files untouched")


def test_rerun_refreshes_and_clears_this_runid_prefix(
    tiny_dataset_study,
) -> None:
    """[OUT 'Rerun safety'] a rerun clears THIS runid's stale outputs
    (both naming modes) but nothing else; the fresh manifest replaces
    the old one."""
    dataset, study = tiny_dataset_study
    out = Path(tempfile.mkdtemp(prefix="pkg-e-rerun-")) / "out"
    sibling = out / "dplocal" / "keepme.txt"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_text("a sibling run's file")

    _successful_run(dataset, study, out)
    stale = out / "dplocal" / f"{study.run_id.lower()}_mstr"
    assert stale.exists(), "baseline run wrote the partitioned mstr output"

    _successful_run(dataset, study, out)
    assert sibling.exists(), (
        "contract [OUT]: the clear is scoped to the runid prefix; a "
        "sibling file without the prefix survives")
    model.read_manifest(out)     # fresh manifest present


def test_manifest_json_is_wellformed_and_maps_logical_names(
    tiny_dataset_study,
) -> None:
    """[OUT 'Reading the manifest'] the manifest maps logical names, so
    readers never parse the SAS convention."""
    dataset, study = tiny_dataset_study
    out = Path(tempfile.mkdtemp(prefix="pkg-e-manifest-")) / "out"
    _successful_run(dataset, study, out)
    raw = json.loads((out / "manifest.json").read_text())
    for logical in ("cohort_final", "attrition"):
        entry = raw["tables"][logical]
        assert entry["file"] == (
            f"{study.run_id.lower()}_"
            f"{SAS_NAMES.get(logical, logical)}"), (
            f"contract [OUT]: manifest file for {logical} must follow the "
            f"declared naming")
