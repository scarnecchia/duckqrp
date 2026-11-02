# Manual probe (not committed as a test): overlapping raw enrollment spans.
"""Probe: do overlapping raw spans yield overlapping enrollment episodes and
a duplicated stockpiled dispensing? Run with .venv/bin/python."""
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.properties.harness import StageHarness, fetch_rows
from tests.properties.synth import (
    Dispensing, EnrollmentSpan, Patient, SyntheticDataset, build_study,
)

start, end = date(2011, 1, 1), date(2011, 12, 31)
# Sorted by (start, end): A, B, C. SQL lag-break: C breaks from B
# (gap 77 days) even though B's days are inside A's span.
spans = (
    EnrollmentSpan(1, date(2011, 1, 1), date(2011, 12, 31), "Y", "Y", "Y"),   # A
    EnrollmentSpan(1, date(2011, 2, 15), date(2011, 6, 15), "Y", "Y", "Y"),   # B nested
    EnrollmentSpan(1, date(2011, 9, 1), date(2011, 9, 30), "Y", "Y", "Y"),    # C inside A
)
disp = (
    # 2011-09-15 is inside BOTH episode outputs if the anomaly holds:
    # a 30-day fill would be shaved/counted twice.
    Dispensing(1, date(2011, 9, 15), "E00001", "ND", 30, 10.0, 1001),
)
dataset = SyntheticDataset(
    patients=(Patient(1, date(1980, 5, 1), "F", "1", "N", "02108", start),),
    deaths=(), enrollment=spans, dispensings=disp, diagnoses=(), procedures=(),
    encounters=(), labs=(), exposure_codes=("E00001",), event_codes=(),
    noise_drug_codes=(), noise_dx_codes=(), start=start, end=end,
)
study = build_study(("E00001",))
with StageHarness(study) as harness:
    harness.attach_dataset(dataset)
    harness.run_through("episodes + masterlist")
    print("enrollment_spans:")
    for r in fetch_rows(harness.engine, "enrollment_spans"):
        print("  ", r)
    print("stockpiled:")
    for r in fetch_rows(harness.engine, "stockpiled"):
        print("  ", r)
    print("index_candidates:")
    for r in fetch_rows(harness.engine, "index_candidates"):
        print("  ", r)
