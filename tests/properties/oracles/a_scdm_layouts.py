# pattern: Functional Core
"""SCDM physical-layout variants and a thin production runner.

Oracle independence
-------------------
Row VALUES come only from ``synth.rows_for_table`` — the shared
serializer boundary. What this module varies is PHYSICAL FORM only:
which file a row lands in, what the file is called, what the folders
look like, which optional columns exist, and what the dispensing code
column is named. No pipeline semantics are re-derived here; equivalence
is asserted in the test by comparing real end-to-end runs.

Layout contracts exercised (INPUTS.md "SCDM data"):

* ``standard``  — one folder per table (what synth.write_parquet does)
* ``sharded``   — ``{name}_001.parquet`` + ``{name}_002.parquet``
* ``hive``      — nested directories under the table folder
* ``alias``     — the documented alias names (``enr``, ``demog``, ...)
* ``fingerprint`` — names nothing like SCDM; found by required columns
* ``mixed``     — a different shape per table

Column forms (INPUTS.md "Things that are NOT a problem"):

* ``optional``  — optional columns present, absent, or present-but-NULL
  (absent and NULL must be equivalent: the pipeline resolves a missing
  optional column to NULL)
* ``code_column`` — ``rx`` (SCDM) or ``ndc`` (accepted alternative)

The thin runner exists because ``harness.full_pipeline_run`` fixes the
layout to ``synth.write_parquet``; these variants need ``qrp.run``
against a different root. It is the same call, not a re-implementation:
production ``run()``, temp root, engine closed in finally, no outputs
written.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from qrp import run

from ..synth import (
    TABLE_COLUMNS,
    SyntheticDataset,
    rows_for_table,
)

# canonical -> documented alias (qrp.scdm SCDM aliases)
ALIAS_NAMES = {
    "enrollment": "enr",
    "demographic": "demog",
    "dispensing": "dis",
    "diagnosis": "dx",
    "death": "dth",
}

# names that match NO SCDM table or alias: found by column fingerprint
FINGERPRINT_NAMES = {
    "enrollment": "DP042_ELIG_2024Q1.parquet",
    "demographic": "DP042_PT_MASTER_V3.parquet",
    "dispensing": "SENTINEL_RX_EXTRACT.parquet",
    "diagnosis": "MSPD_DX_FEED.parquet",
    "death": "WP307_DTH.parquet",
}

# optional columns per table (INPUTS.md "Columns actually read")
OPTIONAL_COLUMNS = {
    "demographic": ("race", "hispanic", "postalcode", "postalcode_date"),
    "enrollment": ("chart",),
    "dispensing": ("rx_codetype",),
}

# tables whose full-run outputs the equivalence property compares
OUTPUT_TABLES = (
    "demographics",
    "demographics_multi",
    "enrollment_spans",
    "exposure_claims",
    "pov1",
    "episodes",
    "ptsmasterlist",
)

# All eight canonical SCDM tables the writer materialises (matching
# synth.write_parquet, which writes every table even when empty, so
# table resolution never needs fingerprinting). The first five carry
# the layout-variant rotation; procedure/encounter/lab_result stay
# canonically named — name-matched under every kind — and exist so the
# per-column characterization can omit columns from a PRESENT table
# rather than omitting the whole table.
LAYOUT_TABLES = ("enrollment", "demographic", "dispensing", "diagnosis",
                 "death", "procedure", "encounter", "lab_result")


@contextmanager
def run_at(
    root: Path,
    study: Any,
    *,
    threads: int = 1,
    table_map: dict[str, str] | None = None,
) -> Iterator[Any]:
    """``qrp.run`` against a caller-built root; engine closed in finally."""
    result = run(study, str(root), threads=threads, verbose=False,
                 table_map=table_map)
    try:
        yield result
    finally:
        result.engine.close()


def capture_outputs(result: Any) -> dict[str, tuple[tuple, ...]]:
    """The compared outputs, deterministically ordered."""
    out: dict[str, tuple[tuple, ...]] = {}
    for table in OUTPUT_TABLES:
        cur = result.engine.con.execute(
            f"SELECT * FROM {table} ORDER BY ALL")
        out[table] = tuple(cur.fetchall())
    return out


def _write_table_files(
    root: Path,
    table: str,
    files: list[tuple[str, list[dict[str, Any]]]],
    cols: tuple[tuple[str, str], ...],
    *,
    code_column: str,
) -> None:
    """Write one table's shard files with exactly ``cols`` as schema.

    Row values come from the rows dicts (``rows_for_table`` output,
    optionally column-dropped); the rename maps the written ``rx``
    column onto the file's ``code_column`` name.
    """
    con = duckdb.connect()
    try:
        for relpath, rows in files:
            path = root / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            schema_cols = [(code_column if (table == "dispensing"
                                            and n == "rx") else n, t)
                           for n, t in cols]
            con.execute(
                f"CREATE OR REPLACE TABLE _w "
                f"({', '.join(f'{n} {t}' for n, t in schema_cols)})")
            if rows:
                projected = [tuple(row[n] for n, _ in cols) for row in rows]
                app = con.appender("_w")
                app.append_rows(projected)
                app.flush()
                del app
            con.execute(
                f"COPY (SELECT * FROM _w) TO "
                f"'{path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            ).fetchall()
    finally:
        con.close()


def _transform_rows(
    dataset: SyntheticDataset,
    table: str,
    *,
    optionals: str,          # "present" | "null"
    rng: random.Random | None,
) -> list[dict[str, Any]]:
    rows = [dict(r) for r in rows_for_table(dataset, table)]
    if optionals == "null":
        for r in rows:
            for col in OPTIONAL_COLUMNS.get(table, ()):
                r[col] = None
    if rng is not None:
        rng.shuffle(rows)
    return rows


def write_variant(
    root: Path,
    dataset: SyntheticDataset,
    *,
    kind: str,
    optionals: str = "present",
    code_column: str = "rx",
    permute_seed: int | None = None,
    drop: tuple[str, ...] = (),
    drop_by_table: dict[str, tuple[str, ...]] | None = None,
) -> None:
    """Materialise ``dataset`` under a physical layout variant.

    ``optionals="absent"`` drops the optional columns from the files;
    ``"null"`` keeps them with NULL values (the matched control);
    ``"present"`` leaves the dataset as generated. ``drop`` removes
    extra named columns everywhere they occur (used to demonstrate the
    rxamt contract gap); ``drop_by_table`` removes named columns from
    ONE table only (the per-column characterization surface).
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(permute_seed) if permute_seed is not None else None
    table_kinds: dict[str, str] = {}
    for i, table in enumerate(LAYOUT_TABLES):
        if kind == "mixed":
            table_kinds[table] = ("standard", "sharded", "hive", "alias",
                                  "fingerprint")[i % 5]
        else:
            table_kinds[table] = kind

    for table in LAYOUT_TABLES:
        rows = _transform_rows(dataset, table, optionals=optionals,
                               rng=rng)
        tkind = table_kinds[table]
        if tkind == "alias":
            name = ALIAS_NAMES.get(table, table)
        elif tkind == "fingerprint":
            name = FINGERPRINT_NAMES.get(table, table)
        else:
            name = table
        if tkind == "sharded":
            half = (len(rows) + 1) // 2
            files = [(f"{name}_001.parquet", rows[:half]),
                     (f"{name}_002.parquet", rows[half:])]
        elif tkind == "hive":
            half = (len(rows) + 1) // 2
            files = [(f"{name}/part=1/data.parquet", rows[:half]),
                     (f"{name}/part=2/data.parquet", rows[half:])]
        elif tkind == "fingerprint":
            files = [(name, rows)]
        else:                       # standard, alias
            files = [(f"{name}/data.parquet", rows)]

        cols = TABLE_COLUMNS[table]
        drop_set: set[str] = set(drop)
        drop_set |= set((drop_by_table or {}).get(table, ()))
        if optionals == "absent":
            drop_set |= set(OPTIONAL_COLUMNS.get(table, ()))
        if drop_set:
            cols = tuple((n, t) for n, t in cols if n not in drop_set)
        _write_table_files(root, table, files, cols, code_column=code_column)
