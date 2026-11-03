# Manual probe (not committed as a test): SCDM 8.2.2 alignment of the B2
# overlap domain. Two families the SCDM Enrollment definition sanctions or
# anticipates that the generated B strategies never drew:
#   1. exact duplicate enrollment rows — the representable residue of the
#      SCDM "Collapsing enrollment spans" rule (the sanctioned overlap form
#      differs only in PlanType/PayerType, columns this pipeline does not
#      read; INPUTS.md enrollment = patid, enr_start, enr_end, medcov,
#      drugcov [, chart]);
#   2. 'U' (Unknown) coverage flags — SCDM MedCov Y/N/U/A, DrugCov Y/N/U.
# Run with the campaign venv's python.
"""Probe: duplicate-row collapse and 'U'-flag coverage semantics, SQL vs
independent model."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.properties.harness import StageHarness, fetch_rows
from tests.properties.synth import SyntheticDataset
from tests.properties.oracles.b_enrollment_model import enrollment_episodes
from tests.properties.oracles.b_pkg_fixtures import (
    START, disp, mini_dataset, patient, span, two_cohort_study,
)

# ---------------------------------------------------------------------
# Family 1: exact duplicate rows (SCDM collapsing residue) + one fill.
# Expectation: ONE episode (no flag change, blank gap -1 <= enrol_gap),
# and NO B2 fan-out — one stockpiled row, numdispensing=1, supply 30.
# ---------------------------------------------------------------------
dup_dataset = mini_dataset(
    patients=(patient(1),),
    enrollment=(
        span(1, date(2011, 1, 1), date(2011, 12, 31)),
        span(1, date(2011, 1, 1), date(2011, 12, 31)),
    ),
    dispensings=(disp(1, date(2011, 9, 15), "E00001", 30, 10.0),),
)
dup_study = two_cohort_study()
with StageHarness(dup_study) as harness:
    harness.attach_dataset(dup_dataset)
    harness.run_through("exposure + stockpiling")
    spans_sql = fetch_rows(harness.engine, "enrollment_spans")
    stock_sql = fetch_rows(harness.engine, "stockpiled")
print("family 1 (duplicate identical rows):")
print("  enrollment_spans:", spans_sql)
print("  stockpiled      :", stock_sql)
model_eps = enrollment_episodes(dup_dataset, dup_study)
print("  model episodes  :", model_eps)
assert len(spans_sql) == 1, spans_sql
assert len(model_eps) == 1 and len(next(iter(model_eps.values()))) == 1
assert len(stock_sql) == 1, stock_sql
assert stock_sql[0][6] == 30, stock_sql
assert stock_sql[0][8] == 1, stock_sql
print("  -> one episode, one stockpiled row, sup=30, numdispensing=1 OK")

# ---------------------------------------------------------------------
# Family 2: 'U' coverage flags (SCDM-legal) — filter, segmentation input,
# and chart interaction. Patient 1: qualifying YY span PLUS an overlapping
# non-chart UU span. Patient 2: clean YY, chart Y. Patient 3: only (U,Y)
# spans. Under coverage=MD + chart_required, expect patids == {2}: the UU
# span never qualifies for coverage but still drops patient 1 via chart.
# ---------------------------------------------------------------------
u_dataset = mini_dataset(
    patients=(patient(1), patient(2), patient(3)),
    enrollment=(
        span(1, START, START + timedelta(days=60), "Y", "Y"),
        span(1, START + timedelta(days=10), START + timedelta(days=20),
             "U", "U", chart="N"),
        span(2, START, START + timedelta(days=60), "Y", "Y"),
        span(3, START, START + timedelta(days=60), "U", "Y"),
    ),
    dispensings=(
        disp(1, START + timedelta(days=10), "E00001", 7, 1.0),
        disp(2, START + timedelta(days=10), "E00001", 7, 1.0),
        disp(3, START + timedelta(days=10), "E00001", 7, 1.0),
    ),
)
u_study = two_cohort_study(
    coverage="MD", cohort_overrides={"chart_required": True})
with StageHarness(u_study) as harness:
    harness.attach_dataset(u_dataset)
    harness.run_through("enrollment_spans")
    u_sql = fetch_rows(harness.engine, "enrollment_spans")
u_model = enrollment_episodes(u_dataset, u_study)
print("family 2 ('U' flags, MD coverage, chart_required):")
print("  enrollment_spans:", u_sql)
print("  model episodes  :", u_model)
sql_patids = {r[1] for r in u_sql}
model_patids = {patid for (_cfg, patid) in u_model}
print(f"  -> SQL patids {sql_patids} / model patids {model_patids}")
assert sql_patids == model_patids == {2}, (sql_patids, model_patids)
print("  -> 'U' never qualifies; non-chart 'U' span still drops the "
      "patient; (U,Y) excluded under MD; SQL == model OK")
