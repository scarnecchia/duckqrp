# Findings — what is broken or open

**PRIVATE — NOT AUTHORIZED FOR PUBLICATION.**

Current state of the qrp-duckdb repository at `discovery` `178f71c`
(2026-11-03). This report lists only items that are broken, unverified,
or waiting on a decision. Everything else passes: the property suite
(441 passed / 13 skipped / 1 xfailed), the original suites (373 passed
/ 3 skipped), and both static gates.

## Broken tests

Two tests in `tests/test_pipeline.py` fail deterministically:

- `test_spilling_is_attributed_to_the_statement_that_caused_it`
- `test_spill_summary_names_the_statement_in_the_log`

Both die with `OutOfMemoryException` ("failed to pin block of size
256.0 KiB") inside the tests' forced ~286 MiB DuckDB memory limit, so
the spill- attribution path they exercise never runs to its assertion.
They fail identically at the pre-campaign baseline, on the operator's
own environment, and in every run since. Fixing them means
investigating DuckDB memory accounting at that limit in production
code, which no campaign or review has been authorized to touch.

Three further tests skip for environmental reasons: one needs a
production input file, two need a larger generated dataset
(`/tmp/qrp_data/2m`).

## Production defects that need an operator ruling first

The charter for all recent work allows changes to tests and mocked
data only. Each item below is a suspected or confirmed production
defect, fully evidenced, with the fix held until the operator rules.
Full briefs with options and recommendations:
`investigation/packages/ARBITRATION.md`.

**DB-2 — CIDA denominators overstate member-days.** For studies with
inclusion conditions, SAS restricts denominator time to the inclusion
windows (and to dose-eligible members under dose limits). This
implementation keeps all enrolled time minus exclusions. Affected
studies overstate member-days; direction certain, magnitude
unmeasured.

**DB-5 — dose-truncated episodes skip the length re-check.** SAS
re-checks the minimum-duration criteria after dose censoring
truncates an episode. This implementation filters before truncation
and never re-checks. Magnitude unmeasured; needs a witness study
before any fix.

**DB-6 — attrition level 1 counts members pov1 silently drops.** The
level-1 sex filter tests a column that is never NULL by construction,
and the missing-demographics check treats blank strings as present
(SAS's `missing()` does not). A member with a blank-string sex is
counted at level 1, then lost between attrition and eligibility with
no record.

**DB-7 — a missing race becomes Multi-racial.** A NULL or absent race
column collapses to 'M' (Multi-racial). SCDM's unknown code is '0'.
Race-conditioned studies and stratified outputs therefore label
missing-race members as Multi-racial.

**DB-8 — a blank Pdx can never match.** Normalize relabels a NULL
Pdx to 'X' before any consumer runs. The care-setting grammar's
explicit "missing Pdx" token and the blank member of the code
distribution expansion are dead code for diagnosis claims. SCDM
requires blank Pdx on ambulatory claims, so real data hits this.

**DB-11 — the benchmark generator emits illegal values.**
`tools/gen_synthetic.py` — the source of the `/tmp/qrp_data/100k`
dataset the original suites run on — swaps the enrollment plantype and
payertype domains, puts a principal-diagnosis flag on ~80% ambulatory
claims, and fabricates a death source code. No currently-tested
behavior is masked, but the benchmark is not SCDM-shaped data. Fixing
it requires regenerating the dataset and re-recording the baselines.

**DB-12 — `qrp doctor` validates on illegal claim shapes.** The
built-in doctor extract puts Pdx 'P' on roughly half its ambulatory
diagnosis rows. A regression that mishandles blank Pdx on ambulatory
claims would pass the doctor run. One-line fix, held for the ruling.

## Open contract questions (no wrong output shown)

**DB-3 — SIGINT mid-run leaves no terminal record.** A Ctrl-C kills
the run with a raw traceback; the JSONL stream and .log end with no
final event. What exit code, diagnostics, and log record a cancelled
run should carry is undefined.

**DB-4 — the failure path emits no RunFinished event.** The CLI and
UI streams are identical for successful runs only; on failure the CLI
carries no terminal event, and a replay tool cannot tell a failed run
from a truncated one.

**DB-9 — `enc_is` has no cited SAS basis.** The pipeline counts
non-acute institutional stays in utilization and baseline output while
the schema note cites SAS counting only AV/OA/IP/ED. Either SAS has a
column the note omits, or this package emits one SAS does not.

**DB-10 — missing-enctype claims: event yes, distribution no.** A
diagnosis claim with no EnType counts as an outcome event under a
wildcard care setting but can never appear in the code-distribution
tables. Both halves quote SAS macros; whether SAS composes the same
way on this input needs one macro read.

## Ruled: documented, no action pending

**B2 — one dispensing inside overlapping enrollment spans counts
twice.** Inherited bug-for-bug from the SAS original. The operator
ruled to keep parity; a validated fix prototype exists in the records
and was deliberately not applied.

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

## Reference ambiguities (not repository defects)

Three points where the SCDM 8.2.2 documentation contradicts itself or
leaves a rule open. Per the standing rule, none became a validation
rule; they are recorded in `investigation/packages/SCDM-ALIGNMENT.md`:

- the Encounter unique-row key (PatID+EncounterID vs EncounterID
  alone — the reference states both);
- the ImputedRace code-3 description (duplicates code 4's text);
- Result_Loc nullability (one line says nulls exist, another says
  none do).
