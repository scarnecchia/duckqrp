# pattern: Functional Core
"""The declared-optional SCDM column characterization table.

INPUTS.md ("Columns actually read") and ``qrp.scdm.SCDM`` declare a set
of OPTIONAL columns per table. The contract says an extract may omit
them. Pass 1 pinned the resolution mechanism for the columns the
pipeline resolves (``opt_*``/``dispensing_*``). This module generalizes
that to EVERY declared-optional column: the property omits exactly one
column from an otherwise-complete extract and characterizes TODAY's
outcome per column.

Hand-maintained declaration
---------------------------
``DECLARED_OPTIONAL`` is transcribed from INPUTS.md and scdm.py's
TableSpec.optional tuples (ddate/discharge_status are resolved by the
pipeline but NOT declared in scdm.py, so they enter via INPUTS.md's
encounter row). ``test_optional_set_matches_declaration`` in the test
module cross-checks the transcription against ``qrp.scdm.SCDM`` so the
hand list cannot silently drift.

Outcome map (observed at discovery rev 8f9f603, labs-enabled study,
extracts otherwise complete):

* ``"runs"``   — the pipeline resolves the absence: NULL-resolved
  (``opt_*`` / ``dispensing_*``) or simply never referenced. Extract
  runs; claim extraction unchanged.
* ``"raises"`` — the shipped ``10_normalize.sql`` references the column
  unconditionally in a ``cdm_*`` view, so binding the view dies with a
  BinderException naming the column. The DOCUMENTED contract (INPUTS.md
  declares these optional) says an extract may omit them; the observed
  crash is the violation, pinned as non-strict xfail in the test:
  * diagnosis trio (dx_codetype, pdx, enctype) — the arbitrated A-4;
  * procedure px_codetype/enctype — same family, unconditional in
    ``_cdm_procedure_raw`` (crashes even for an RX-only study, because
    the PX extraction block materializes the view unconditionally);
  * all 13 lab_result optionals — unconditional in ``cdm_lab``,
    surfacing only when a labs-enabled study runs the labs stage (the
    probe study configures one lab code so the characterization is
    real, not vacuous).

``CRASHERS`` is the derived set of raises-columns; the test module
xfails exactly those.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

from ..synth import (
    GenerateOptions,
    LabResult,
    Procedure,
    SyntheticDataset,
    build_study,
    generate,
)

# transcribed from INPUTS.md "Columns actually read" + scdm.py
# TableSpec.optional (see module docstring for ddate/discharge_status)
DECLARED_OPTIONAL: dict[str, tuple[str, ...]] = {
    "enrollment": ("chart",),
    "demographic": ("race", "hispanic", "postalcode", "postalcode_date"),
    "dispensing": ("rxamt", "rx_codetype"),
    "diagnosis": ("dx_codetype", "pdx", "enctype"),
    "procedure": ("px_codetype", "enctype", "encounterid", "providerid",
                  "origpx"),
    "encounter": ("encounterid", "ddate", "discharge_status"),
    "death": (),
    "lab_result": ("result_dt", "order_dt", "ms_result_n", "ms_result_c",
                   "result_type", "loinc", "px", "ms_test_name",
                   "ms_test_sub_category", "specimen_source",
                   "ms_result_unit", "fast_ind", "pt_loc"),
}

# observed TODAY (rev 8f9f603): column -> outcome class
RESOLVES: set[tuple[str, str]] = {
    ("enrollment", "chart"),
    ("demographic", "race"),
    ("demographic", "hispanic"),
    ("demographic", "postalcode"),
    ("demographic", "postalcode_date"),
    ("dispensing", "rxamt"),
    ("dispensing", "rx_codetype"),
    ("procedure", "encounterid"),
    ("procedure", "providerid"),
    ("procedure", "origpx"),
    ("encounter", "encounterid"),
    ("encounter", "ddate"),
    ("encounter", "discharge_status"),
}

CRASHERS: set[tuple[str, str]] = {
    # A-4 trio
    ("diagnosis", "dx_codetype"),
    ("diagnosis", "pdx"),
    ("diagnosis", "enctype"),
    # A-4 family, found by this characterization
    ("procedure", "px_codetype"),
    ("procedure", "enctype"),
    *{("lab_result", c) for c in DECLARED_OPTIONAL["lab_result"]},
}


def all_declared_columns() -> list[tuple[str, str]]:
    return [(t, c) for t, cols in DECLARED_OPTIONAL.items() for c in cols]


def probe_dataset() -> SyntheticDataset:
    """A fixed extract with every table POPULATED, so omitting a column
    omits it from a present table, not from an absent one."""
    base = generate(patients=2, seed=0, options=GenerateOptions(
        duplicate_demographics=True, same_supply_ties=True,
        include_deaths=True, include_encounters=True))
    procedures = (Procedure(1, date(2014, 3, 12), "PX1", "HC", "AV", 10,
                            1001, None),)
    labs = tuple(
        LabResult(p, date(2014, 4, 1), None, None, 1.2, "", "N", "2160-0",
                  "PX1", "CREATININE", "", "SR_PLS", "MG/DL", "X", "O")
        for p in (1, 2)
    )
    return dataclasses.replace(base, procedures=procedures, labs=labs)


def probe_study(dataset: SyntheticDataset) -> Any:
    """Full code pools (non-degenerate extraction) plus one lab code so
    the labs stage runs and the lab_result columns are really bound."""
    return build_study(
        dataset.exposure_codes, dataset.event_codes,
        lab_codes=(("cohort_a", "CREAT", "LRO", "02", "N",
                    "CREATININE", "", "SR_PLS", "MG/DL", "N", "X", "O",
                    ">=", 1.0, None),),
    )


def outcome_class(table: str, column: str) -> str:
    assert (table, column) in RESOLVES or (table, column) in CRASHERS, (
        f"{table}.{column} missing from the outcome map"
    )
    return "runs" if (table, column) in RESOLVES else "raises"
