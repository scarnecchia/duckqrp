"""Harnesses that run the SHIPPED SQL through the production plumbing.

Two harnesses, one rule: no SQL is copied here.

* `StageHarness` registers fixture tables in a DuckDB connection, then
  executes the same shipped `.sql` scripts, in the same order, through
  `Engine.script_stage()` — the exact production path `pipeline.run()`
  uses. `read_*` parameters point at registered fixture tables instead
  of parquet globs, which is the ONLY divergence from `run()`: a value
  in the `fmt` mapping is a FROM-expression, and a table name is as
  valid a FROM-expression as `read_parquet('...')`.

* `full_pipeline_run()` writes a synthetic dataset to a temporary SCDM
  parquet root and calls `qrp.run()` — the real pipeline, end to end,
  into a temporary output directory.

The stage sequence and the glue between stages (covar_source, the
claim-dose view, cohort_claims, the covariate/baseline block) mirror
`pipeline.run()` step for step. That mirroring is pinned by tests:
`STAGE_ORDER` is asserted equal to `plan_stages()`'s order for an
all-features study, so the harness cannot drift from `STAGES`.

Prerequisites: the pipeline is linear, so the prerequisite relation of
stage X is "every stage before X" (`STAGE_PREREQUISITES`, derived from
STAGES at import and test-verified). `stage_closure()` turns a
requested subset into the prefix that must run. Which relations each
stage CREATES is derived from the shipped SQL itself
(`STAGE_OUTPUTS`), so that map cannot drift either.

Every run uses DuckDB `threads=1` by default: generated runs stay
polite on shared CPU, and single-threaded execution makes results
reproducible across examples.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from qrp import run
from qrp.engine import SQL_DIR, Engine, fetch_one
from qrp.pipeline import (
    COVAR_SOURCE_VIEW,
    STAGES,
    Stage,
    _check_exposure,
    _combo_sql,
    _covar_strata_sql,
    _empty_relation,
    _mstr_extra_columns,
    register_config,
)
from qrp.scdm import SCDM
from qrp.sqlsplit import split_statements

from .synth import SyntheticDataset, rows_for_table, schema_sql, write_parquet

__all__ = [
    "STAGE_ORDER",
    "STAGE_OUTPUTS",
    "STAGE_PREREQUISITES",
    "StageHarness",
    "fetch_rows",
    "full_pipeline_run",
    "stage_closure",
    "stage_creates",
]

STAGE_BY_NAME: dict[str, Stage] = {st.name: st for st in STAGES}
STAGE_ORDER: tuple[str, ...] = tuple(st.name for st in STAGES)

# The pipeline is linear: stage X consumes tables produced by the
# stages before it (plus the SCDM inputs), so its prerequisite set is
# exactly the stages preceding it. Derived from STAGES at import, and
# test-asserted against plan_stages(), so it cannot go stale.
STAGE_PREREQUISITES: dict[str, frozenset[str]] = {
    st.name: frozenset(STAGE_ORDER[: i])
    for i, st in enumerate(STAGES)
}


def stage_closure(requested: Iterable[str]) -> tuple[str, ...]:
    """The ordered prefix of stages that must run for `requested`.

    Raises on an unknown stage name — a typo'd stage must fail loudly,
    not silently run everything.
    """
    names = tuple(requested)
    if not names:
        raise ValueError("requested stage set is empty")
    unknown = [n for n in names if n not in STAGE_BY_NAME]
    if unknown:
        raise ValueError(
            f"unknown stage(s) {unknown}; valid stages: {list(STAGE_ORDER)}")
    last = max(STAGE_ORDER.index(n) for n in names)
    return STAGE_ORDER[: last + 1]


_CREATE_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def stage_creates(script: str) -> tuple[str, ...]:
    """Relation names a shipped script creates, read from the file."""
    sql = (SQL_DIR / script).read_text()
    seen: list[str] = []
    for m in _CREATE_RE.finditer(sql):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
    return tuple(seen)


# Which relations each stage materialises — derived from the shipped SQL
# at import time, so this map cannot disagree with the files.
STAGE_OUTPUTS: dict[str, tuple[str, ...]] = {
    st.name: stage_creates(st.script) for st in STAGES
}


def fetch_rows(
    engine: Engine, table: str, order_by: str = "ALL"
) -> tuple[tuple[Any, ...], ...]:
    """All rows of a relation, deterministically ordered, as tuples.

    The shared oracle accessor: `ORDER BY ALL` sorts by every selected
    column in order, which is a total order over a SELECT *.
    """
    cur = engine.con.execute(f"SELECT * FROM {table} ORDER BY {order_by}")
    return tuple(cur.fetchall())


class StageHarness:
    """Runs shipped pipeline SQL against registered fixture tables.

    Usage:
        harness = StageHarness(study)
        harness.attach_dataset(dataset)      # or register_table() by hand
        harness.run_through("exposure + stockpiling")
        rows = fetch_rows(harness.engine, "exposure_claims")

    Every example creates its OWN harness (own Engine, own connection);
    there is no shared mutable state, and `close()` (or the context
    manager) closes the connection in the caller's finally path.
    """

    def __init__(
        self,
        study: Any,
        *,
        threads: int = 1,
        verbose: bool = False,
        on_event: Any = None,
    ) -> None:
        self.study = study
        self.engine = Engine(
            threads=threads, verbose=verbose, on_event=on_event)
        # Production config plumbing: cfg_* tables registered exactly
        # the way run() registers them.
        register_config(self.engine, study)
        self.executed: list[str] = []
        self.skipped: list[str] = []
        self._scdm_tables: dict[str, str] = {}
        self._fmt: dict[str, Any] | None = None

    # ---- context management ----------------------------------------

    def close(self) -> None:
        self.engine.close()

    def __enter__(self) -> StageHarness:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- fixture tables ---------------------------------------------

    def register_table(
        self, name: str,
        rows: Sequence[dict[str, Any]],
        schema: str,
    ) -> None:
        """Materialise a fixture table via the production `register()`.

        Same appender path pipeline.register_config uses, so fixture
        tables and config tables go through one code path.
        """
        self.engine.register(name, rows, schema)
        # The fmt's read_* expressions point AT these tables; remember
        # the mapping so _prepare_fmt resolves them instead of falling
        # back to the typed empty relation.
        self._scdm_tables[name] = name
        self._fmt = None          # fmt must be rebuilt from the catalog

    def attach_dataset(self, dataset: SyntheticDataset) -> None:
        """Register a synthetic dataset as every SCDM input table."""
        for table in _SCDM_TABLE_KEYS:
            self.register_table(
                table, list(rows_for_table(dataset, table)), schema_sql(table))

    # ---- fmt / reads -------------------------------------------------

    def _columns(self, table: str) -> set[str]:
        # `.description` is how production code introspects a relation
        # too (see pipeline._mstr_extra_columns); DESCRIBE-in-subquery
        # does not project a `name` column on DuckDB 2.
        cur = self.engine.con.execute(f"SELECT * FROM {table} LIMIT 0")
        return {str(d[0]).lower() for d in cur.description}

    def _prepare_fmt(self) -> dict[str, Any]:
        if self._fmt is not None:
            return self._fmt
        fmt: dict[str, Any] = {}
        for spec in SCDM:
            if not spec.used:
                continue
            table = self._scdm_tables.get(spec.name)
            fmt[f"read_{spec.name}"] = (
                table if table is not None else _empty_relation(spec))

        # The dispensed-code / codetype columns, resolved from the
        # fixture schema exactly as run() resolves them from the parquet
        # schema (pipeline.run's `next(...)` chains).
        disp = fmt.get("read_dispensing", "")
        disp_cols: set[str] = set()
        if disp and not disp.startswith("("):
            disp_cols = self._columns(disp)
        code_col = next(
            (c for c in ("rx", "ndc", "rxcode", "code") if c in disp_cols),
            "rx")
        ct_col = next(
            (c for c in ("rx_codetype", "codetype", "ndc_codetype")
             if c in disp_cols), None)
        # mirror of the production rxamt resolution (10_normalize reads
        # {dispensing_rxamt} / {dispensing_amt_rule} since fix-a2)
        rxamt_col = "rxamt" if "rxamt" in disp_cols else None

        def _optional(read_key: str, *names: str) -> dict[str, str]:
            src = fmt.get(read_key, "")
            have: set[str] = set()
            if src and not src.startswith("("):
                have = self._columns(src)
            return {n: (n if n in have else "NULL") for n in names}

        optional_cols: dict[str, str] = {}
        for key, cols in (
            ("read_enrollment", ("chart",)),
            ("read_demographic", ("race", "hispanic",
                                  "postalcode", "postalcode_date")),
            ("read_encounter", ("ddate", "discharge_status")),
        ):
            optional_cols.update(_optional(key, *cols))
        for table, cols in (
            ("diagnosis", ("dx_codetype", "pdx", "enctype")),
            ("procedure", ("px_codetype", "enctype")),
            ("lab_result", (
                "result_dt", "order_dt", "ms_result_n", "ms_result_c",
                "result_type", "loinc", "px", "ms_test_name",
                "ms_test_sub_category", "specimen_source", "ms_result_unit",
                "fast_ind", "pt_loc",
            )),
        ):
            optional_cols.update({
                f"{table}_{name}": value
                for name, value in _optional(f"read_{table}", *cols).items()
            })

        study = self.study
        fmt.update({
            "dispensing_code": code_col,
            "dispensing_codetype": ct_col or "NULL",
            "dispensing_rxamt": rxamt_col or "NULL",
            "dispensing_amt_rule": (
                f"({rxamt_col}) IS NOT NULL AND ({rxamt_col}) > 0"
                if rxamt_col else "TRUE"
            ),
            "enrollment_chart": optional_cols["chart"],
            **{f"opt_{k}": v for k, v in optional_cols.items()},
            # No files on disk here; only warning text ever sees it.
            "indata": "<stage-harness>",
            "start_date": study.start_date.isoformat(),
            "run_envelope": int(study.run_envelope),
            "di_rel": "distindex_episodes" if study.any_code_distribution
            else "(SELECT NULL::VARCHAR AS cohortgrp, NULL::BIGINT AS patid, "
                 "NULL::DATE AS indexdt, NULL::VARCHAR AS distindexexp, "
                 "NULL::VARCHAR AS distindexhoi WHERE FALSE)",
            "incl_rel": "inclusion_excluded" if study.any_inclusions
            else "(SELECT NULL::VARCHAR AS cohortgrp, NULL::BIGINT AS patid, "
                 "NULL::DATE AS indexdt, NULL::INTEGER AS first_failed_cond, "
                 "NULL::VARCHAR AS failed_criteria WHERE FALSE)",
            "dose_rel": "dose_censor" if study.any_dose_censoring
            else "(SELECT NULL::VARCHAR AS cohortgrp, NULL::BIGINT AS patid, "
                 "NULL::DATE AS indexdt, "
                 "NULL::DATE AS censordate_maxdose WHERE FALSE)",
            "end_date": study.end_date.isoformat(),
            "censor_date": study.effective_censor_date.isoformat(),
            "claims_from": (
                (study.start_date - timedelta(days=study.widest_lookback_days))
                .isoformat()
                if study.widest_lookback_days is not None
                else date.min.isoformat()
            ),
            "claims_to": study.effective_censor_date.isoformat(),
        })
        self._fmt = fmt
        return fmt

    @property
    def fmt(self) -> dict[str, Any]:
        return self._prepare_fmt()

    def _stage_params(self, name: str) -> dict[str, Any]:
        """Extra SQL parameters, mirroring pipeline.run._stage_params."""
        if name == "cida tables":
            cbase, csel, cgrp, cfin = _covar_strata_sql(self.study)
            return {**self.fmt, "covar_base": cbase, "covar_select": csel,
                    "covar_group": cgrp, "covar_final": cfin}
        return self.fmt

    # ---- execution ---------------------------------------------------

    def run_stages(self, requested: Iterable[str]) -> list[str]:
        """Run the stage prefix that a requested subset requires.

        The executed sequence is STAGES order, gated by the study (a
        gated-off stage is skipped exactly as run() skips it), with the
        inter-stage glue applied at its anchor points.
        """
        plan = stage_closure(requested)
        last = STAGE_ORDER.index(plan[-1])
        return self._execute(last)

    def run_through(self, last: str) -> list[str]:
        """Run every stage up to and including `last`."""
        return self.run_stages([last])

    def _in_plan(self, name: str, last: int) -> bool:
        return STAGE_ORDER.index(name) <= last

    def _run_stage(self, name: str) -> bool:
        st = STAGE_BY_NAME[name]
        if not st.runs_for(self.study):
            self.skipped.append(name)
            return False
        self.engine.script_stage(name, st.script, **self._stage_params(name))
        self.executed.append(name)
        return True

    def _define_cohort_claims(self) -> None:
        """The cohort_claims narrow, mirroring pipeline.run verbatim
        (including its materialise-or-view decision)."""
        study = self.study
        heavy_readers = (len(study.covariates) + len(study.inclusions)
                         + len(study.risk_scores))
        materialise = False
        if heavy_readers:
            self.engine.con.execute("""
                CREATE OR REPLACE TABLE _cohort_patids AS
                SELECT DISTINCT patid FROM ptsmasterlist
            """)
            row = fetch_one(self.engine.con.execute("""
                SELECT (SELECT count(*) FROM _cohort_patids)::DOUBLE
                     / nullif((SELECT count(DISTINCT patid) FROM demographics), 0)
            """))
            share = (row[0] if row and row[0] is not None else 1.0)
            materialise = share < 0.5
        if materialise:
            self.engine.con.execute("""
                CREATE OR REPLACE TABLE cohort_claims AS
                SELECT s.* FROM covar_source s
                SEMI JOIN _cohort_patids c ON c.patid = s.patid
            """)
        else:
            self.engine.con.execute(
                "CREATE OR REPLACE VIEW cohort_claims AS "
                "SELECT * FROM covar_source")
        if heavy_readers:
            self.engine.con.execute("DROP TABLE _cohort_patids")

    def _run_baseline(self) -> None:
        """The baseline stage with its empty-relation fallbacks,
        mirroring pipeline.run."""
        have = {r[0] for r in self.engine.con.execute(
            "SELECT table_name FROM duckdb_tables()").fetchall()}
        self.engine.script_stage(
            "baseline", "96_baseline.sql",
            util_rel="utilization" if "utilization" in have else
            "(SELECT NULL::VARCHAR AS cohortgrp, NULL::BIGINT AS patid, "
            "NULL::DATE AS indexdt, NULL::DOUBLE AS enc_av, "
            "NULL::DOUBLE AS enc_oa, NULL::DOUBLE AS enc_ip, "
            "NULL::DOUBLE AS enc_is, NULL::DOUBLE AS enc_ed, "
            "NULL::DOUBLE AS numgeneric, NULL::DOUBLE AS numclass, "
            "NULL::DOUBLE AS numrx WHERE FALSE)",
            risk_rel="risk_scores" if "risk_scores" in have else
            "(SELECT NULL::VARCHAR AS cohortgrp, NULL::BIGINT AS patid, "
            "NULL::DATE AS indexdt, NULL::VARCHAR AS riskscore, "
            "NULL::DOUBLE AS score WHERE FALSE)",
            **self.fmt)

    def _execute(self, last: int) -> list[str]:
        """Walk STAGES order through `last`, glue at the anchors.

        Step-for-step mirror of pipeline.run's stage sequence; the
        stages-and-order tests pin this against plan_stages().
        """
        self.executed = []
        self.skipped = []
        fmt = self.fmt

        if self._in_plan("normalize", last):
            self._run_stage("normalize")
            # covar_source: defined unconditionally, as run() does.
            self.engine.con.execute(COVAR_SOURCE_VIEW.format(**fmt))
        if self._in_plan("enrollment_spans", last):
            self._run_stage("enrollment_spans")
        if self._in_plan("exposure + stockpiling", last):
            self._run_stage("exposure + stockpiling")
            # claim_dose: a VIEW over exposure_claims, as run() defines
            # it right after this stage.
            self.engine.con.execute(
                self.engine.read_sql("_claim_dose_view.sql").format(**fmt))
            _check_exposure(self.engine, self.study, fmt["indata"])
        if self._in_plan("index dates", last):
            self._run_stage("index dates")
        if self._in_plan("dose restrictions", last):
            self._run_stage("dose restrictions")
        if self._in_plan("pov1", last):
            self._run_stage("pov1")
        if self._in_plan("episodes + masterlist", last):
            self._run_stage("episodes + masterlist")
            self._define_cohort_claims()
        if self._in_plan("inclusion criteria", last):
            self._run_stage("inclusion criteria")
        if self._in_plan("dose censoring", last):
            self._run_stage("dose censoring")
        if self._in_plan("follow-up + events", last):
            self._run_stage("follow-up + events")
        if self._in_plan("attrition + denominators", last):
            self._run_stage("attrition + denominators")
        if self._in_plan("code distribution", last):
            self._run_stage("code distribution")
        if self._in_plan("most frequent use", last):
            self._run_stage("most frequent use")
        if self._in_plan("labs", last):
            self._run_stage("labs")
        if self._in_plan("utilization", last):
            self._run_stage("utilization")
        if self._in_plan("geography", last):
            self._run_stage("geography")
        if self._in_plan("risk scores", last):
            self._run_stage("risk scores")
        if self._in_plan("covariates", last) and self.study.any_covariates:
            self.engine.script_stage("covariates", "80_covariates.sql",
                                     **fmt)
            self.executed.append("covariates")
            if self.study.any_combo_covariates:
                for stmt in split_statements(_combo_sql(self.study)):
                    self.engine.con.execute(stmt.sql)
        if self._in_plan("baseline", last) and self.study.any_covariates:
            self._run_baseline()
            self.executed.append("baseline")
        if self._in_plan("master list columns", last):
            mstr_extra, mstr_joins = _mstr_extra_columns(self.engine,
                                                         self.study)
            self.engine.script_stage(
                "master list columns", "62_mstr_wide.sql",
                mstr_extra=mstr_extra, mstr_joins=mstr_joins, **fmt)
            self.executed.append("master list columns")
        if self._in_plan("cida denominators", last):
            self._run_stage("cida denominators")
        if self._in_plan("cida tables", last):
            self._run_stage("cida tables")
        if self._in_plan("follow-up time table", last):
            self._run_stage("follow-up time table")
        return list(self.executed)


# The canonical SCDM table names the dataset registers, in scdm.SCDM
# order, skipping tables this implementation does not read.
_SCDM_TABLE_KEYS: tuple[str, ...] = tuple(
    spec.name for spec in SCDM if spec.used)


@contextmanager
def full_pipeline_run(
    dataset: SyntheticDataset,
    study: Any,
    *,
    threads: int = 1,
    write_outputs: bool = True,
    output_dir: Path | None = None,
) -> Iterator[Any]:
    """Run the REAL pipeline end to end over a synthetic parquet root.

    Writes the dataset to a temp directory (its own per-call temp dir,
    so examples share nothing), calls qrp.run() with the production
    path, yields the RunResult, and closes the engine in finally. The
    temp tree dies with the context; nothing touches the repo.
    `output_dir` overrides where results are written (caller-owned and
    NOT cleaned up); by default they go to the temp tree and vanish.
    """
    with tempfile.TemporaryDirectory(prefix="qrp-properties-") as tmp:
        root = write_parquet(dataset, Path(tmp) / "indata")
        if output_dir is not None:
            out: Path | None = Path(output_dir)
        elif write_outputs:
            out = Path(tmp) / "out"
        else:
            out = None
        result = run(
            study, str(root),
            threads=threads, verbose=False, output_dir=out,
        )
        try:
            yield result
        finally:
            result.engine.close()
