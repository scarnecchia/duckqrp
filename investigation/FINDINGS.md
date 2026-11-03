# Findings — what is broken or open

**PRIVATE — NOT AUTHORIZED FOR PUBLICATION.**

Current state of the qrp-duckdb repository at `discovery` `178f71c`
(2026-11-03). This report lists only items that are broken, unverified,
or in remediation. Everything else passes: the property suite
(441 passed / 13 skipped / 1 xfailed), the original suites (373 passed
/ 3 skipped), and both static gates.

## Broken tests

Two tests in `tests/test_pipeline.py` fail deterministically:

- `test_spilling_is_attributed_to_the_statement_that_caused_it`
- `test_spill_summary_names_the_statement_in_the_log`

Both die with `OutOfMemoryException` ("failed to pin block of size
256.0 KiB") inside the tests' forced ~286 MiB DuckDB memory limit, so
the spill-attribution path they exercise never runs to its assertion.
They fail identically at the pre-campaign baseline, on the operator's
own environment, and in every run since. Fixing them means
investigating DuckDB memory accounting at that limit in production
code, which no campaign or review has been authorized to touch.

Three further tests skip for environmental reasons: one needs a
production input file, two need a larger generated dataset
(`/tmp/qrp_data/2m`).

## Ruled by the operator (2026-11-03) — fixes authorized

The operator ruled on every open item below. These rulings authorize
production changes for exactly these items (recorded in
`investigation/packages/ARBITRATION.md`, "Operator rulings"). Items
stay listed here until their fix lands and validates.

### Defects

**DB-2 — CIDA denominators overstate member-days.**
For studies with inclusion conditions, SAS restricts denominator time
to the inclusion windows and, under dose limits, to dose-eligible
members. This implementation keeps all enrolled time minus exclusions.
Direction certain; magnitude unmeasured. Studies without inclusion
conditions are exact.
Ruling: (a) — implement both restrictions for parity, with the oracle
and generator extensions.

**DB-5 — dose-truncated episodes skip the length re-check.**
SAS re-checks the minimum-duration criteria after dose censoring
truncates an episode. This implementation filters before truncation
and never re-checks, so short truncated episodes survive that SAS
would delete. Affects only studies with maxcumdose and cumdoseper
both set.
Ruling: (a) — implement the re-check, witness study first.

**DB-6 — attrition level 1 counts members pov1 silently drops.**
The level-1 sex filter tests a column that is never NULL by
construction, and the missing-demographics check treats blank strings
as present (SAS's `missing()` does not). A member with a blank-string
sex is counted at level 1, then lost between attrition and eligibility
with no record. Zero effect on SCDM-legal extracts.
Ruling: (a) — apply the NULL-or-blank predicate and align level 1,
after the ms_attrition source check.

**DB-7 — a missing race becomes Multi-racial.**
A NULL or absent race column collapses to 'M' (Multi-racial); SCDM's
unknown code is '0'. Race-conditioned studies and stratified outputs
label missing-race members as Multi-racial.
Ruling: (a) — read SAS's missing-race handling and match it.

**DB-8 — a blank Pdx can never match a diagnosis claim.**
Normalize relabels a NULL Pdx to 'X' before any consumer runs. The
care-setting grammar's explicit "missing Pdx" token and the blank
member of the code-distribution expansion are therefore dead code for
diagnosis claims — while SCDM requires blank Pdx on ambulatory claims.
Affects only studies that configure Pdx-restricted care-setting
tokens.
Ruling: (a) — read the SAS macros; if SAS matches blank, move the
relabel after matching; if 'X', close as parity.

**DB-11 — the benchmark generator emits illegal values.**
`tools/gen_synthetic.py` — the source of the `/tmp/qrp_data/100k`
dataset the original suites run on — swaps the enrollment plantype and
payertype domains, puts a principal-diagnosis flag on ~80% of
ambulatory claims, and fabricates a death source code. No
currently-tested behavior is masked, but the benchmark is not
SCDM-shaped data.
Ruling: (a) — fix all three, regenerate the dataset, re-record the
baselines.

**DB-12 — `qrp doctor` validates on illegal claim shapes.**
The built-in doctor extract puts Pdx 'P' on roughly half its
ambulatory diagnosis rows, so a regression that mishandles blank Pdx
on ambulatory claims would still pass the doctor run.
Ruling: (a) — Pdx only on IP rows, then a doctor re-run to confirm
liveness.

### Contract questions

**DB-3 — SIGINT mid-run leaves no terminal record.**
A Ctrl-C kills the run with a raw traceback; the JSONL stream and .log
end with no final event, so a cancelled run is indistinguishable from
a truncated one.
Ruling: (b) — catch KeyboardInterrupt in the CLI, emit the cancelled
terminal record, exit 130. Full signal-handler cancellation only if
tooling reports confusion.

**DB-4 — the failure path emits no RunFinished event.**
CLI and UI event streams are identical for successful runs only; on
failure the CLI carries no terminal event, and a replay tool cannot
tell a failed run from a truncated one.
Ruling: (b) — document the split ownership in LOGGING.md and amend the
parity sentence.

**DB-9 — `enc_is` has no cited SAS basis.**
The pipeline counts non-acute institutional stays in utilization and
baseline output while the schema note cites SAS counting only
AV/OA/IP/ED. Either SAS has a column the note omits, or this package
emits one SAS does not.
Ruling: (a) — read ms_computeutilization.sas and correct whichever
comment is wrong.

**DB-10 — missing-enctype claims: event yes, distribution no.**
A diagnosis claim with no EnType counts as an outcome event under a
wildcard care setting but can never appear in the code-distribution
tables. Both halves quote SAS macros; the behavior is pinned by tests
either way.
Ruling: (a) — read ms_codedistribution.sas; "let's at least understand
what SAS does."

## Ruled earlier: documented, no action pending

**B2 — one dispensing inside overlapping enrollment spans counts
twice.** Inherited bug-for-bug from the SAS original; the ruling keeps
parity. The SCDM data model shows compliant extracts cannot trigger
it: the only sanctioned overlap form arrives here as exact duplicates,
which provably collapse to one episode. Firing requires a
non-canonical extract that upstream Sentinel QA is expected to reject.
A validated fix prototype exists in the records and was deliberately
not applied.

## Known limitations (bounded, recorded)

- **Lab mean rounding.** `lab_summary` averages DOUBLEs, so float
  summation order can flip an exact .0005 rounding boundary. A
  decimal-based mean would make it deterministic. Unimplemented; the
  property suite avoids the boundary by construction.
- **Identical-dispensing dedupe.** The covariate stage counts distinct
  dispensing rows before the same-day sum. Whether SAS dedupes
  identical rows is unverified; the suite asserts neither direction.
- **Episode ordering under overlapping spans.** When claims tie on the
  full ordering key, the episode pairing depends on join emission
  order (observed differing by thread count). No ordering policy is
  endorsed; the durable overlap tests assert only order-independent
  invariants.
- **Attrition levels 3-5 are carried, not counted.** Member age, chart,
  and demographic-condition exclusions never reduce the funnel; the
  in-file note marks this as not yet verified against SAS.
- **D integration approval limits.** The optional-stage approval covers
  documented-input pinning only; the DOUBLE mean and the DISTINCT
  question sit outside it.

## Reference ambiguities

Three points where the SCDM 8.2.2 documentation contradicts itself or
leaves a rule open were recorded as unresolved during the audit. Two
are now resolved by operator ruling (2026-11-03, recorded in
ARBITRATION.md):

- **Encounter unique-row key — RESOLVED by ruling: PatID+EncounterID.**
  The synthetic EncounterIDs are globally unique and satisfy it; no
  production code joins on EncounterID.
- **ImputedRace code-3 description** (duplicates code 4's text) —
  still unresolved; outside the pipeline read set; no action.
- **Result_Loc nullability — RESOLVED by ruling: non-nullable.**
  Outside the pipeline read set; records-only.
