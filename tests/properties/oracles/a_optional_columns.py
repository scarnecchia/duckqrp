# pattern: Functional Core
"""Declared optional columns and a populated, labs-enabled synthetic probe."""

import dataclasses
from datetime import date
from typing import Any

from ..synth import LabResult, Procedure, SyntheticDataset, build_study, hand_fixture

DECLARED_OPTIONAL: dict[str, tuple[str, ...]] = {
    "enrollment": ("chart",),
    "demographic": ("race", "hispanic", "postalcode", "postalcode_date"),
    "dispensing": ("rxamt", "rx_codetype"),
    "diagnosis": ("dx_codetype", "pdx", "enctype"),
    "procedure": ("px_codetype", "enctype", "encounterid", "providerid", "origpx"),
    "encounter": ("encounterid", "ddate", "discharge_status"),
    "death": (),
    "lab_result": ("result_dt", "order_dt", "ms_result_n", "ms_result_c",
                   "result_type", "loinc", "px", "ms_test_name",
                   "ms_test_sub_category", "specimen_source", "ms_result_unit",
                   "fast_ind", "pt_loc"),
}


def all_declared_columns() -> list[tuple[str, str]]:
    return [(t, c) for t, cols in DECLARED_OPTIONAL.items() for c in cols]


def probe_dataset() -> SyntheticDataset:
    base, _ = hand_fixture()
    procedures = (Procedure(1, date(2014, 3, 12), "PX1", "HC", "AV", 10, 1001, None),)
    labs = tuple(
        LabResult(p, date(2013, 12, 1), None, None, 1.2, "", "N", "2160-0",
                  "PX1", "CREATININE", "", "SR_PLS", "MG/DL", "X", "O")
        for p in (1, 2)
    )
    return dataclasses.replace(base, procedures=procedures, labs=labs)


def probe_study(dataset: SyntheticDataset) -> Any:
    return build_study(
        dataset.exposure_codes, dataset.event_codes,
        lab_codes=(("cohort_a", "2160-0", "LRO", "02", "N", "CREATININE",
                    "", "SR_PLS", "MG/DL", "N", "X", "O", ">=", 1.0, None),),
    )
