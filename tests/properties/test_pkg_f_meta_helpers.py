"""Package F — metamorphic oracles (shared by the F test families).

CARE-SETTING WILDCARD: build_study()-shaped studies carry no
cfg_care_setting rows, and event_claims INNER JOINs cfg_care_setting on
(cohortgrp, code) — 30_exposure.sql — so events silently vanish. Real
loaders (config.parse_care_setting) emit one ('**', '*') wildcard row
per event code per cohort; wildcard_study() reproduces exactly that.

EQUIVALENCE PROMISES relied on (cited where asserted):

* input row permutation and DuckDB thread count: README.md,
  "Determinism, and a test that was lying" — every window carries a
  total order precisely so results cannot depend on parquet row order
  or thread count.
* input file partitioning: docs/RUNBOOK.md documents "One folder per
  table (if your export is partitioned)" and "Several files per table"
  as supported input layouts.
* cohort batching (together vs separately): NO documented promise
  exists; the batching test characterizes and records, it does not
  assert.

The helpers live in a test_pkg_f_* module (not a subpackage): a
subpackage under a CLI-package directory gives mypy two module names
for one file.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, replace
import math
from pathlib import Path
from typing import Any

import duckdb

from qrp.config import Covariate, RiskScoreCode, StratumLevel

from .synth import SyntheticDataset, write_parquet

# ---------------------------------------------------------------------
# Care-setting wildcards (campaign trap #1)
# ---------------------------------------------------------------------


def wildcard_study(study: Any) -> Any:
    """Add the cfg_care_setting rows a real loader would emit: one
    ('**', '*') wildcard per EVENT code per cohort (CARE_SETTING_ALL in
    qrp.config). Without them event_claims inner-joins to nothing and
    every event-bearing output is silently empty."""
    cohorts = tuple(
        replace(c, event_care_settings=tuple(
            (code, "**", "*") for code, _ct, _vocab, _role in c.event_codes))
        for c in study.cohorts
    )
    return replace(study, cohorts=cohorts)


# ---------------------------------------------------------------------
# Optional-feature registry. Each entry returns study_for() kwargs over
# the dataset's own code vocabulary, sized for <=5 patients. `needs`
# names a dataset property the feature reads.
# ---------------------------------------------------------------------


def f_mfu(dataset: SyntheticDataset) -> dict[str, Any]:
    return {"mfu": (("cohort_a", 1, "DX", "CODECOUNT", 5, -365, -1, ""),
                    ("cohort_b", 1, "DX", "CODECOUNT", 5, -365, -1, ""))}


def f_labs(dataset: SyntheticDataset) -> dict[str, Any]:
    return {"lab_codes": (
        ("cohort_a", "2160-0", "LRO", "", "N", "CREATININE", "",
         "SR_PLS", "MG/DL", "", "X", "O", ">=", 100.0, None),
        ("cohort_b", "2160-0", "LRO", "", "N", "CREATININE", "",
         "SR_PLS", "MG/DL", "", "X", "O", ">=", 100.0, None),
    )}


def f_utilization(dataset: SyntheticDataset) -> dict[str, Any]:
    return {"utilization": (("cohort_a", "MED", -365, -1),
                            ("cohort_b", "DRUG", -365, -1))}


def f_geography(dataset: SyntheticDataset) -> dict[str, Any]:
    zips = tuple(dict.fromkeys(p.postalcode for p in dataset.patients))[:3]
    return {"zipfile_rows": tuple(
        (z, "MA", "1", "Northeast", 5.0) for z in zips)}


def f_risk_scores(dataset: SyntheticDataset) -> dict[str, Any]:
    return {"risk_scores": (
        RiskScoreCode(riskscore="CCI", condid="IN", codecat="IN",
                      code="", weight=1.0),
        RiskScoreCode(riskscore="CCI", condid="C1", codecat="DX",
                      code=dataset.event_codes[0], weight=2.0),
    )}


def f_covariates(dataset: SyntheticDataset) -> dict[str, Any]:
    return {"covariates": (Covariate(
        covarnum=1, covarname="cv_event_dx", codecat="DX",
        covfrom=-365, covto=-1, codes=(dataset.event_codes[0],)),)}


def f_cida(dataset: SyntheticDataset) -> dict[str, Any]:
    return {"strata": (StratumLevel("t2cida", "000", ()),
                       StratumLevel("t2cida", "001", ("agegroup",)),
                       StratumLevel("t2cida", "002", ("agegroup", "sex")))}


def f_followuptime(dataset: SyntheticDataset) -> dict[str, Any]:
    return {"strata": (StratumLevel("t2followuptime", "F00", ()),)}


FEATURES: dict[str, Any] = {
    "mfu": f_mfu,
    "labs": f_labs,
    "utilization": f_utilization,
    "geography": f_geography,
    "risk_scores": f_risk_scores,
    "covariates": f_covariates,
    "cida": f_cida,
    "followuptime": f_followuptime,
}

FEATURE_NAMES: tuple[str, ...] = tuple(FEATURES)

ALL_FEATURE_KWARGS: dict[str, dict[str, Any]] = {}   # filled lazily


def feature_kwargs(dataset: SyntheticDataset,
                   names: tuple[str, ...]) -> dict[str, Any]:
    """Merge the kwargs for the named features onto one study. The two
    strata-driven features (cida, followuptime) share the `strata`
    kwarg and CONCATENATE: the pipeline's cfg_strata carries both
    tables' levels tagged by tableid, so a study may ask for both."""
    kwargs: dict[str, Any] = {}
    for name in names:
        for key, value in FEATURES[name](dataset).items():
            if key == "strata" and key in kwargs:
                kwargs[key] = tuple(kwargs[key]) + tuple(value)
            else:
                kwargs[key] = value
    return kwargs


def study_for_features(dataset: SyntheticDataset,
                       names: tuple[str, ...]) -> Any:
    """A validated wildcard study with the named optional features on."""
    from .synth import study_for
    return wildcard_study(study_for(dataset, **feature_kwargs(dataset,
                                                              names)))


def all_pairs() -> list[tuple[str, str]]:
    """Every unordered PAIR of optional features."""
    names = FEATURE_NAMES
    return [(names[i], names[j])
            for i in range(len(names)) for j in range(i + 1, len(names))]


# ---------------------------------------------------------------------
# Input transformations
# ---------------------------------------------------------------------


def permute_dataset(dataset: SyntheticDataset,
                    seed: int) -> SyntheticDataset:
    """Shuffle the rows WITHIN each table (never across tables), keeping
    every row's payload intact. This is the input-order metamorphosis:
    parquet row order is not part of any contract, and the README
    determinism section promises results do not depend on it."""
    rng = random.Random(seed)
    patients, deaths, enrollment = (tuple(_shuffled(rng, dataset.patients)),
                                    tuple(_shuffled(rng, dataset.deaths)),
                                    tuple(_shuffled(rng, dataset.enrollment)))
    dispensings = tuple(_shuffled(rng, dataset.dispensings))
    diagnoses = tuple(_shuffled(rng, dataset.diagnoses))
    procedures = tuple(_shuffled(rng, dataset.procedures))
    encounters = tuple(_shuffled(rng, dataset.encounters))
    labs = tuple(_shuffled(rng, dataset.labs))
    return replace(dataset, patients=patients, deaths=deaths,
                   enrollment=enrollment, dispensings=dispensings,
                   diagnoses=diagnoses, procedures=procedures,
                   encounters=encounters, labs=labs)


def _shuffled(rng: random.Random,
              rows: tuple[Any, ...]) -> list[Any]:
    out = list(rows)
    rng.shuffle(out)
    return out


def sharded_root(dataset: SyntheticDataset, base: Path) -> Path:
    """Write the dataset, then split every table's single parquet file
    into a folder of parts — the "one folder per table (partitioned)"
    layout docs/RUNBOOK.md documents. The DATA is written exactly once,
    through write_parquet(); the split re-partitions those same bytes
    so the input content, not its serialization, is what varies."""
    root = write_parquet(dataset, base / "indata")
    con = duckdb.connect()
    try:
        for table_dir in sorted(root.iterdir()):
            src = table_dir / "data.parquet"
            if not src.exists():
                continue
            total = con.execute(
                f"SELECT count(*) FROM read_parquet('{src}')").fetchone()
            n = int(total[0]) if total else 0
            half = n // 2
            for part, rng in (("part-0.parquet", f"LIMIT {half}"),
                              ("part-1.parquet", f"OFFSET {half}")):
                con.execute(
                    f"COPY (SELECT * FROM read_parquet('{src}') {rng}) "
                    f"TO '{table_dir / part}' "
                    "(FORMAT PARQUET, COMPRESSION ZSTD)").fetchall()
            src.unlink()
    finally:
        con.close()
    return root


# ---------------------------------------------------------------------
# Output membership comparison
# ---------------------------------------------------------------------

# Timing/identity tables: present in every run, values legitimately
# differ run to run (stage seconds, host info). Membership compared,
# content exempted.
NONDETERMINISTIC_TABLES: frozenset[str] = frozenset(
    {"runtimes", "signature"})

# repr-normalization per column type is handled by _norm_cell.


def _norm_cell(v: Any) -> str:
    if v is None:
        return "<null>"
    if hasattr(v, "isoformat"):
        return f"<d:{v.isoformat()}>"
    return repr(v)


def _norm_rows(rows: list[tuple[Any, ...]]) -> list[str]:
    return sorted(repr(tuple(_norm_cell(c) for c in row)) for row in rows)


# DEPLOYMENT.md:220-221 permits last-binary-digit aggregate differences
# below 4e-15 relative, while counts, dates and flags remain exact.
# No absolute floor: zero versus a nonzero value must still differ.
AGGREGATE_RTOL = 4e-15


@dataclass(frozen=True)
class AggregateTable:
    schema: tuple[tuple[str, str], ...]
    rows: tuple[tuple[Any, ...], ...]


Membership = dict[str, Any]


def snapshot(name: str, cursor: Any) -> Any:
    schema = tuple((str(d[0]), str(d[1])) for d in cursor.description)
    rows = cursor.fetchall()
    if name in {"baseline", "baseline_long"}:
        return AggregateTable(schema, tuple(rows))
    return _norm_rows(rows)


def aggregate_tables_equal(name: str, a: AggregateTable,
                           b: AggregateTable) -> bool:
    if a.schema != b.schema or len(a.rows) != len(b.rows):
        return False
    columns = [c.lower() for c, _ in a.schema]

    def row_equal(left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
        for i, (x, y) in enumerate(zip(left, right, strict=True)):
            permitted = name == "baseline" and columns[i].startswith(("mean_", "std_"))
            if name == "baseline_long" and columns[i] == "v":
                permitted = (left[columns.index("kind")] == "stat"
                             and right[columns.index("kind")] == "stat"
                             and str(left[columns.index("stat")]).startswith(("mean_", "std_")))
            if type(x) is not type(y):
                return False
            if isinstance(x, float) and isinstance(y, float):
                if math.isnan(x) or math.isnan(y):
                    if not (math.isnan(x) and math.isnan(y)):
                        return False
                    continue
                if permitted and math.isfinite(x) and math.isfinite(y):
                    if not math.isclose(x, y, rel_tol=AGGREGATE_RTOL, abs_tol=0.0):
                        return False
                    continue
            if x != y:
                return False
        return True

    # Perfect bipartite matching, not a set or greedy deduplication:
    # approximate equality is not transitive, and duplicates consume rows.
    matches: dict[int, int] = {}

    def augment(i: int, seen: set[int]) -> bool:
        for j, right in enumerate(b.rows):
            if j in seen or not row_equal(a.rows[i], right):
                continue
            seen.add(j)
            if j not in matches or augment(matches[j], seen):
                matches[j] = i
                return True
        return False

    return all(augment(i, set()) for i in range(len(a.rows)))


def read_engine_membership(engine: Any) -> Membership:
    """Every TABLE in the engine (not views), content-normalized."""
    names = [r[0] for r in engine.con.execute(
        "SELECT table_name FROM duckdb_tables() "
        "WHERE database_name = current_database()").fetchall()]
    out: Membership = {}
    for name in sorted(names):
        out[name] = snapshot(name, engine.con.execute(f"SELECT * FROM {name}"))
    return out


def read_file_membership(out: Path) -> dict[str, list[str]]:
    """Every manifest table, read from parquet through a FRESH duckdb
    connection (an independent path from the engine that wrote them)."""
    manifest = json.loads((out / "manifest.json").read_text())
    con = duckdb.connect()
    try:
        files: dict[str, list[str]] = {}
        for name, entry in sorted(manifest["tables"].items()):
            base = out / entry["library"] / entry["file"]
            if base.is_dir():
                found = sorted(base.rglob("*.parquet"))
                if not found:
                    # A directory entry with no parquet parts: the table
                    # is legitimately EMPTY (e.g. a cohort with no
                    # members), not missing.
                    files[name] = []
                    continue
                src = f"{base}/**/*.parquet"
            else:
                src = str(base) + ("" if base.suffix else ".parquet")
            files[name] = snapshot(name, con.execute(
                f"SELECT * FROM read_parquet('{src}')"))
        return files
    finally:
        con.close()


def membership_diff(a: Membership, b: Membership,
                    label_a: str, label_b: str) -> list[str]:
    """Human-readable mismatches between two memberships; content of
    NONDETERMINISTIC_TABLES is exempted (presence still required)."""
    problems: list[str] = []
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    if only_a:
        problems.append(f"tables only in {label_a}: {only_a}")
    if only_b:
        problems.append(f"tables only in {label_b}: {only_b}")
    for name in sorted(set(a) & set(b)):
        if name in NONDETERMINISTIC_TABLES:
            continue
        if isinstance(a[name], AggregateTable) and isinstance(b[name], AggregateTable):
            if not aggregate_tables_equal(name, a[name], b[name]):
                problems.append(f"{name}: aggregate rows/schema differ: {a[name]!r} vs {b[name]!r}")
            continue
        if a[name] != b[name]:
            sa, sb = set(a[name]), set(b[name])
            detail = (f"{len(sa - sb)} rows missing, {len(sb - sa)} extra"
                      f"; e.g. missing {sorted(sa - sb)[:2]} "
                      f"extra {sorted(sb - sa)[:2]}")
            problems.append(f"{name}: rows differ ({detail})")
    return problems


def assert_same_outputs(run_a: Any, out_a: Path, run_b: Any, out_b: Path,
                        context: str) -> None:
    """Full output membership equality, engine tables AND files."""
    eng_a = read_engine_membership(run_a.engine)
    eng_b = read_engine_membership(run_b.engine)
    problems = membership_diff(eng_a, eng_b, f"{context}:engine-a",
                               f"{context}:engine-b")
    if out_a.exists() and out_b.exists():
        problems += membership_diff(read_file_membership(out_a),
                                    read_file_membership(out_b),
                                    f"{context}:files-a",
                                    f"{context}:files-b")
    if problems:
        raise AssertionError(
            f"{context}: outputs diverged —\n  " + "\n  ".join(problems))
