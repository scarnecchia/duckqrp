"""Package A — the demographics full-tie lead (investigation README,
open question 1).

``10_normalize.sql``'s demographics QUALIFY orders by ``birth_date,
sex, race, hispanic, postalcode``. Two duplicate rows agreeing on ALL
five leave the survivor arbitrary. This battery runs the SHIPPED SQL
through the production paths and records what actually happens:

* is the survivor stable across insert orders?
* across DuckDB thread counts 1 vs 4?
* does it affect ``demographics_multi`` exclusion?

The test asserts ONLY documented contracts — one survivor row per
patid, the patid in ``demographics_multi``, ``pov1`` exclusion — plus
the documented total-order outcome where the rows DO separate in an
order column. Which row survives the undefined cases is recorded as
evidence, never asserted: that would invent a spec.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from .conftest import WITNESS_DIR
from .harness import StageHarness
from .oracles import a_demographics_tie as T
from .synth import rows_for_table, schema_sql, write_parquet

FIXTURES = (
    ("differ_zip", T.fixture_differ_zip),
    ("differ_pcd", T.fixture_differ_pcd),
    ("identical", T.fixture_identical),
)


def _run_harness(fixture: T.TieFixture, order: str, threads: int) -> dict:
    dataset = T.dataset_for(fixture, order)
    study = T.study_for_fixture()
    harness = StageHarness(study, threads=threads)
    try:
        for table in ("demographic", "enrollment", "dispensing",
                      "diagnosis", "death", "encounter"):
            harness.register_table(
                table, list(rows_for_table(dataset, table)),
                schema_sql(table))
        harness.run_through("pov1")
        return T.experiment_record(harness.engine, fixture, order,
                                   "registered", threads)
    finally:
        harness.close()


def _run_parquet(fixture: T.TieFixture, order: str, threads: int) -> dict:
    dataset = T.dataset_for(fixture, order)
    study = T.study_for_fixture()
    with tempfile.TemporaryDirectory(prefix="qrp-a-tie-") as tmp:
        write_parquet(dataset, Path(tmp) / "indata")
        from .oracles.a_scdm_layouts import run_at
        with run_at(Path(tmp) / "indata", study, threads=threads) as result:
            return T.experiment_record(result.engine, fixture, order,
                                       "parquet", threads)


def _assert_documented(record: dict, fixture: T.TieFixture) -> None:
    assert record["survivor"] is not None, "no demographics row survived"
    assert record["in_multi"], "duplicate patid missing from demographics_multi"
    assert not record["pov1_has_7"], (
        "duplicate-demographics patid reached pov1"
    )
    assert record["pov1_has_8"], "control patient lost from pov1"
    surv = record["survivor"]
    sources = {fixture.row_a, fixture.row_b}
    match = any(
        surv[1:] == (p.birth_date, p.sex.upper(), p.sex.upper(),
                     p.race, p.hispanic, p.postalcode, p.postalcode_date)
        for p in sources
    )
    assert match, f"survivor {surv} matches neither input row"


class TestDemographicsFullTie:
    @pytest.mark.parametrize("fixture_name,factory", FIXTURES)
    @pytest.mark.parametrize("order", ["AB", "BA"])
    @pytest.mark.parametrize("threads", [1, 4])
    def test_registered_tables(self, fixture_name, factory, order,
                               threads) -> None:
        fixture = factory()
        record = _run_harness(fixture, order, threads)
        self._check(record, fixture)

    @pytest.mark.parametrize("fixture_name,factory", FIXTURES)
    @pytest.mark.parametrize("order", ["AB", "BA"])
    @pytest.mark.parametrize("threads", [1, 4])
    def test_parquet_path(self, fixture_name, factory, order,
                          threads) -> None:
        fixture = factory()
        record = _run_parquet(fixture, order, threads)
        self._check(record, fixture)

    # ---- shared checks ----------------------------------------------

    def _check(self, record: dict, fixture: T.TieFixture) -> None:
        _ev(f"tie:{record['fixture']}:{record['order']}:"
            f"{record['writer']}:{record['threads']}threads")
        _assert_documented(record, fixture)
        if fixture.differ_in_order_key:
            # the documented TOTAL order decides: smaller postalcode
            assert record["survivor"][6] == "60601", (
                "documented total-order tie-break not honored: "
                f"survivor {record['survivor']}"
            )

    def test_evidence_file_written(self) -> None:
        """Run the whole battery again and persist the observations to
        the committed evidence file (a- prefix, campaign convention)."""
        records: list[dict] = []
        for _, factory in FIXTURES:
            fixture = factory()
            for order in ("AB", "BA"):
                for threads in (1, 4):
                    records.append(_run_harness(fixture, order, threads))
                    records.append(_run_parquet(fixture, order, threads))
        WITNESS_DIR.mkdir(parents=True, exist_ok=True)
        path = WITNESS_DIR / "a-demographics-full-tie-evidence.txt"
        path.write_text(T.format_evidence(records), encoding="utf-8")
        assert path.exists()


def _ev(label: str) -> None:
    try:
        from hypothesis import event
        event(label)
    except Exception:
        pass
