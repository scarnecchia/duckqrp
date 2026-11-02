"""AC.1 acceptance tests for the investigation infrastructure.

These pin the CONTRACT the rest of the campaign builds on:

* profiles load and budgets are wired (test_property_profiles_load);
* generated fixtures are isolated per example
  (test_generated_fixture_isolation);
* the stage harness runs the SHIPPED SQL through the production
  plumbing, and its hand fixture matches hand calculation
  (test_stage_harness_executes_shipped_sql);
* hypothesis lives in the dev extra only, runtime stays at one
  dependency (test_dev_extra_keeps_runtime_minimal);
* the synthetic builder emits exact SCDM schemas and deliberate,
  payload-retaining ties (test_property_builder_schema_and_ties).
"""

from __future__ import annotations

import tempfile
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import duckdb
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from qrp.engine import SQL_DIR
from qrp.events import StageStarted
from qrp.pipeline import plan_stages
from qrp.scdm import SCDM

from . import conftest
from .conftest import EXPENSIVE_BUDGETS, HYPOTHESIS_DB_DIR, PROFILES, expensive_settings
from .harness import (
    STAGE_BY_NAME,
    STAGE_ORDER,
    StageHarness,
    fetch_rows,
    full_pipeline_run,
)
from .strategies import SEEDS, datasets
from .synth import (
    TABLE_COLUMNS,
    TABLE_ORDER,
    GenerateOptions,
    SyntheticDataset,
    generate,
    hand_fixture,
    rows_for_table,
    study_for,
    write_parquet,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------

def test_property_profiles_load() -> None:
    """The three documented profiles exist, are registered with
    Hypothesis, and `expensive_settings()` tracks the active one."""
    assert set(PROFILES) == {"quick", "discovery", "deep"}
    assert PROFILES["quick"]["max_examples"] == 50
    assert PROFILES["discovery"]["max_examples"] == 500
    assert PROFILES["deep"]["max_examples"] == 2000
    # Cheap pure properties keep a deadline in every profile.
    for kwargs in PROFILES.values():
        assert kwargs["deadline"] is not None

    # Registered and loadable, not merely declared.
    for name, kwargs in PROFILES.items():
        loaded = settings.get_profile(name)
        assert loaded.max_examples == kwargs["max_examples"]

    # The active profile is one of ours (CLI --hypothesis-profile or
    # the $HYPOTHESIS_PROFILE default resolved at conftest import).
    current = settings.get_current_profile_name()
    assert current in PROFILES

    # Expensive database-backed budgets scale 10/50/100 by profile and
    # always drop the deadline.
    assert EXPENSIVE_BUDGETS == {"quick": 10, "discovery": 50, "deep": 100}
    exp = expensive_settings()
    assert exp.max_examples == EXPENSIVE_BUDGETS[current]
    assert exp.deadline is None

    # The suite's example database is its own directory, never a
    # repo-root .hypothesis/ (which belongs to the user).
    assert HYPOTHESIS_DB_DIR == Path(conftest.__file__).parent / ".hypothesis-db"
    assert HYPOTHESIS_DB_DIR.resolve() != (_REPO_ROOT / ".hypothesis").resolve()
    from hypothesis.configuration import storage_directory
    assert Path(str(storage_directory().home_directory)).resolve() \
        == HYPOTHESIS_DB_DIR.resolve()


# ---------------------------------------------------------------------
# Fixture isolation
# ---------------------------------------------------------------------

@given(ds=datasets(min_patients=1, max_patients=4))
@expensive_settings()
def test_generated_fixture_isolation(ds: SyntheticDataset) -> None:
    """Two fully independent runs of the same generated fixture give
    the same answer; connections and temp dirs die between examples.

    The context manager owns the temp tree and closes the engine in
    finally; there is no function-scoped fixture state anywhere in the
    path, so Hypothesis examples share nothing but the strategy.
    """
    study = study_for(ds)

    with full_pipeline_run(ds, study, write_outputs=False) as result:
        first_engine = result.engine
        first = fetch_rows(first_engine, "ptsmasterlist")
    # The first example's connection was closed by the context manager.
    with pytest.raises(duckdb.Error):
        first_engine.count("ptsmasterlist")

    # A second, completely fresh run: new connection, new temp dir.
    with full_pipeline_run(ds, study, write_outputs=False) as result:
        second = fetch_rows(result.engine, "ptsmasterlist")

    assert first == second


# ---------------------------------------------------------------------
# Stage harness over shipped SQL
# ---------------------------------------------------------------------

def test_stage_harness_executes_shipped_sql() -> None:
    """The harness runs the shipped SQL through Engine.script_stage,
    and the hand fixture produces the hand-calculated rows."""
    dataset, study = hand_fixture()
    events: list[object] = []
    with StageHarness(study, on_event=events.append) as harness:
        harness.attach_dataset(dataset)
        executed = harness.run_through("episodes + masterlist")

        # Production plumbing: exactly one StageStarted per executed
        # stage, emitted by Engine.script_stage (no local SQL runner).
        started = [e.name for e in events if isinstance(e, StageStarted)]
        assert started == executed

        # The stages of the prefix that this study enables; the gated
        # ones (dose) were skipped, exactly as the STAGES walk arranges.
        # plan_stages() already filters gated stages off, so the prefix
        # must come from STAGE_ORDER.
        prefix = STAGE_ORDER[: STAGE_ORDER.index("episodes + masterlist") + 1]
        gated_off = [s for s in prefix
                     if not STAGE_BY_NAME[s].runs_for(study)]
        assert executed == [s for s in prefix if s not in gated_off]
        assert harness.skipped == gated_off
        assert executed == [
            s for s in plan_stages(study)
            if STAGE_ORDER.index(s) <= STAGE_ORDER.index("episodes + masterlist")
        ]

        # The exposure script really is the shipped file.
        shipped = (SQL_DIR / "30_exposure.sql").read_text()
        assert "exposure_claims" in shipped

        # Hand-calculated rows:
        # 7 dispensings survive normalize (P1 x2, P2 noise, P3
        # uncovered, P4 tie pair x2, P5).
        assert harness.engine.count("cdm_dispensing") == 7
        # Exposure extraction is query-period-bounded, NOT
        # enrollment-bounded: P3's out-of-span claim is extracted, and
        # the enrollment bound applies later, at index detection.
        rows = harness.engine.con.execute(
            "SELECT cohortgrp, patid, count(*) FROM exposure_claims "
            "GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall()
        assert rows == [
            ("cohort_a", 1, 2),
            ("cohort_a", 3, 1),
            ("cohort_b", 4, 2),
            ("cohort_b", 5, 1),
        ]

        # Demographics dedup: 6 rows in, 5 patients out, P5 flagged as
        # ambiguous (duplicate demographic rows).
        assert harness.engine.count("demographics") == 5
        assert harness.engine.count("demographics_multi") == 1
        # Deaths: exactly P2's row survives normalize.
        assert harness.engine.count("deaths") == 1

        # Enveloping: P1's ambulatory D00002 claim inside the IP stay
        # (2014-05-01..2014-05-05) is re-filed to IP / 'X'. The
        # normalized claim code column is `code`, not `dx`.
        env = harness.engine.con.execute(
            "SELECT enctype, pdx FROM cdm_diagnosis WHERE code = 'D00002'"
        ).fetchall()
        assert env == [("IP", "X")]

        # Membership: one episode each for P1 (cohort_a) and P4
        # (cohort_b). P1's second fill (2014-04-09) sits inside the
        # 183-day washout of the 2014-03-10 index and never starts a
        # second episode. P3 has no index date (claim outside their
        # enrollment); P5 is excluded outright for duplicate
        # demographics.
        mstr = harness.engine.con.execute(
            "SELECT cohortgrp, patid FROM ptsmasterlist ORDER BY 1, 2"
        ).fetchall()
        assert mstr == [("cohort_a", 1), ("cohort_b", 4)]
        row = harness.engine.con.execute(
            "SELECT count(*) FROM ptsmasterlist WHERE patid = 5"
        ).fetchone()
        assert row is not None and int(row[0]) == 0


# ---------------------------------------------------------------------
# Dependency hygiene
# ---------------------------------------------------------------------

def test_dev_extra_keeps_runtime_minimal() -> None:
    """hypothesis is dev-only: the runtime surface stays at exactly one
    dependency, and the lock file never gains hypothesis."""
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    assert data["project"]["dependencies"] == ["duckdb>=2.0,<3"]

    extras = data["project"]["optional-dependencies"]
    assert any(req.startswith("hypothesis>=6,<7") for req in extras["dev"])
    for name in ("ui", "serve"):
        assert not any(
            req.lower().startswith("hypothesis") for req in extras[name]
        ), f"hypothesis leaked into the {name} extra"

    lock = (_REPO_ROOT / "requirements.lock").read_text().lower()
    assert "hypothesis" not in lock


# ---------------------------------------------------------------------
# Builder schemas and deliberate ties
# ---------------------------------------------------------------------

_TIED_DATASETS = given(
    ds=st.builds(
        generate,
        patients=st.integers(min_value=1, max_value=4),
        seed=SEEDS,
        options=st.just(GenerateOptions()),
    )
)


@_TIED_DATASETS
@expensive_settings()
def test_property_builder_schema_and_ties(ds: SyntheticDataset) -> None:
    """Generated parquet schemas match scdm.py exactly, row counts
    round-trip, and the deliberate ties are present with their full
    payloads (the evidence a tie-breaking defect would need)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = write_parquet(ds, Path(tmp))
        con = duckdb.connect()
        try:
            for table in TABLE_ORDER:
                path = root / table / "data.parquet"
                describe = con.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('{path}')"
                ).fetchall()
                # Exact column names AND types, not just presence.
                have = {r[0]: str(r[1]).upper() for r in describe}
                want = {name: typ for name, typ in TABLE_COLUMNS[table]}
                assert have == want, (table, have, want)

                # Column sets agree with what scdm.SCDM says the
                # pipeline reads.
                spec = next(s for s in SCDM if s.name == table)
                assert set(spec.required) <= set(have)
                assert set(spec.optional) <= set(have)

                # Round trip: every row written is readable back.
                n = con.execute(
                    f"SELECT count(*) FROM read_parquet('{path}')"
                ).fetchone()
                assert n is not None
                assert int(n[0]) == len(rows_for_table(ds, table))
        finally:
            con.close()

    # Deliberate ties, generator-on: one duplicated demographic patid
    # and at least one same-day/same-supply dispensing pair with
    # different amounts. The payloads are retained on the dataclasses,
    # so a witness reproduces the tie rows themselves.
    assert len(ds.duplicate_demographic_patids()) >= 1
    ties = ds.same_supply_ties()
    assert len(ties) >= 1
    for a, b in ties:
        assert (a.patid, a.rxdate, a.rx, a.rxsup) \
            == (b.patid, b.rxdate, b.rx, b.rxsup)
        assert a.rxamt != b.rxamt


# ---------------------------------------------------------------------
# Witness mechanism
# ---------------------------------------------------------------------

def test_witness_hook_records_falsifying_examples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed Hypothesis example lands in investigation/witnesses/ as
    a self-contained text file; non-falsifying failures write nothing."""
    monkeypatch.setattr(conftest, "WITNESS_DIR", tmp_path)

    def interact(node: object, call: object, report: object) -> None:
        # Duck-typed stand-ins for pytest's hook objects; the hook only
        # reads .failed / .longrepr / .nodeid.
        conftest.pytest_exception_interact(
            cast("pytest.Item", node),
            cast("pytest.CallInfo[Any]", call),
            cast("pytest.TestReport", report),
        )

    node = SimpleNamespace(
        nodeid="tests/properties/test_x.py::test_falsifies")
    falsifying = SimpleNamespace(
        failed=True,
        longrepr="Traceback ...\nFalsifying example: "
                 "test_falsifies(patients=1, seed=0)\n",
    )
    interact(node, None, falsifying)

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "Falsifying example" in text
    assert "test_falsifies" in files[0].name
    assert "reproduce:" in text

    # An ordinary failure (no falsifying example) is not a witness.
    plain = SimpleNamespace(failed=True, longrepr="AssertionError: nope")
    interact(node, None, plain)
    assert len(list(tmp_path.iterdir())) == 1

    # Passing results never write.
    passed = SimpleNamespace(failed=False, longrepr="")
    interact(node, None, passed)
    assert len(list(tmp_path.iterdir())) == 1
