# F2: CIDA numerator exceeds denominator in stratified cells — FIXED by
# splitting eligible windows at age thresholds (SAS parity)

- **id:** F2
- **proposed issue title:** t2_cida reports npts > dennumpts in age
  cells: the denominator bands a member's whole eligible window at its
  start while the numerator bands episodes at the index date, so an
  episode's cell can have no denominator members at all
- **contract:** Per-cell consistency of the CIDA deliverable: the
  numerator counts members of the population the denominator counts.
  In the SAS original this holds per cell by construction: the
  denominator (%ms_cidadenom) splits each eligible window at the age
  thresholds — the do-loop walks the bands oldest-first, outputs one
  span per band the window overlaps with its own member-days, and
  shrinks the window to `threshdate-1`
  (ms_cidadenom.sas:1036-1057) — so a member is counted in EVERY band
  their eligible time touches. The numerator bands each episode at its
  index date (ms_createpov1.sas:102-116 routes episodes through
  %ms_agestrat with STARTDT=birth_date, ENDDT=ADate→IndexDt), and an
  index date always lies inside the eligible window, so its band is
  one of the bands the denominator split out. The merge writes
  DenNumPts=0 for a numerator row whose denominator cell is missing
  (ms_cidatables.sas:272-285) — the tool's t2_cida already reproduces
  the merge side; the defect is in the denominator's banding.
- **trigger:** Any member whose eligible denominator window crosses an
  age-band threshold (a birthday in years bands) AND has an episode in
  the later band. The denominator window is clipped to the query
  period (denom_start = greatest(enr_start + enrdays, query start)),
  so the threshold must fall inside the window.
- **witness/repro command:**
  investigation/witnesses/pkg-f-lead-f2-num-gt-den.txt (finder's
  witness, reproduced by the arbiter's probe
  investigation/probes/arb2_f1_f2_probe.py: member cohort_b/3, born
  1936-09-25, index 2014-10-04 aged 78, denominator window banded 65-74
  only); xfail pins
  `tests/properties/test_pkg_f_totals.py::test_numerator_denominator_pin_f2`
  and `::test_numerator_never_exceeds_denominator`. Regression:
  `tests/test_regression_f2.py` (standalone):
  `.venv/bin/python -m pytest tests/test_regression_f2.py -q`.
- **expected vs actual:** On `generate(patients=4, seed=0)`:
  `('001','cohort_b','75+') npts=1 dennumpts=0` and
  `('002','cohort_b','75+','F') npts=1 dennumpts=0` — denomcounts has
  NO 75+ row at all (the member is banded 65-74 from denom_start and
  the HAVING n_pts > 0 drops the empty cell), while t2_cida keeps the
  numerator's 75+ row. Expected (SAS): the member's window splits at
  the 75th birthday, the 75+ cell exists with dennumpts ≥ 1 and the
  member-days split across both bands; npts ≤ dennumpts per cell.
- **first failing revision:** c3e60db5d08343b1cebbb90d234602f09cb0d70d
  (campaign base; the single-anchor banding predates the campaign).
- **mechanism:** `_denom_strat` joined each eligible window to ONE band
  via `age_in_unit(birth_date, denom_start, unit) BETWEEN lo AND hi` —
  a banding the docs never claimed as parity (the in-code rationale
  "earliest date an index could occur in it" describes a heuristic, not
  SAS's rule, and docs/SAS_PARITY.md's denominator verification
  reconciled LEVEL margins only, never per-band cells). The numerator
  (45_pov1.sql:78) bands at indexdt per SAS. A window crossing a
  birthday therefore contributes its member-days entirely to the
  earlier band and the later band's episode gets a numerator cell whose
  denominator holds nobody. Member-days were also mis-banded: SAS
  splits them at the threshold, the tool put them all in the start
  band.
- **severity:** wrong answer in stratified CIDA reporting (the finder's
  MEDIUM guess stands; the unstratified level reconciles, stratified
  cells do not). **+ unproven impact:** rate-per-cell reporting
  (incidence against enrolled member-time) divides by a denominator
  cell that understates or misses the band entirely; the merged
  DenNumPts=0 presentation matches SAS's shape but the underlying
  counts diverge from SAS for every threshold-crossing window.
- **affected paths:** src/qrp/sql/92_cidadenom.sql (_denom_strat).
- **minimal fix/commit:** branch `inv/0f462p/fix2-f2` (from c3e60db):
  red commit 0e7eeee (standalone regression), green commit 80208b1
  (span split; amended with the deterministic rewrite — see below).
  `_denom_strat` now emits one row per band the window overlaps: entry
  = birth + lo units (threshdate), span end = the next band's entry -
  1 day, member-days per span, index_year at the span start; the top
  band runs to the window end. Dates are cast because DATE + INTERVAL
  is TIMESTAMP in DuckDB.
- **red/green commands+results:**
  - red: `.venv/bin/python -m pytest tests/test_regression_f2.py -q`
    at c3e60db+0e7eeee → 1 failed (denominator emits only 65-74; the
    75+ cell is missing entirely).
  - green: same after the rewrite → 1 passed (both bands emitted,
    165/200 member-day split at the fixture's 2011-06-15 birthday,
    npts ≤ dennumpts in t2_cida); regression + determinism + rewrites
    + ui → 51 passed, 2 skipped; the shave test
    test_denominator_is_shaved_around_outcome_events passes; full
    tests/test_pipeline.py → see ARBITRATION.md wave-2.
- **broader validation:** a first green used `lead()` over
  `(denom_cfg_id, scope, patid, eligepisode) ORDER BY ordinal` to find
  each band's end — and turned out NONDETERMINISTIC: `_denom_windows`
  holds multiple shave segments per (member, eligepisode), so the
  tie-ordered lead() paired rows arbitrarily and which segment's
  top-band row landed last decided whether its span survived. Proven
  on a frozen stage input (5 runs → 5 distinct output digests; the
  base view under the same harness → 0 diffs). The shipped green
  replaces the window function with per-band interval offsets
  (next-band bounds are pure band data), 5 runs → 1 digest. This also
  caught a determinism gap: the campaign's property suites never
  re-ran denomcounts cell-level across repeated identical runs.
- **related findings:** F1 (same package; independent mechanism).
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
- **SCDM 8.2.2 alignment:** reviewed against demographic.md
  (Birth_Date: SAS date, days from 1960-01-01) on 2026-11-03 —
  age-band computation operates on normalized DATEs; no
  data-model-bearing assumption beyond the date semantics
  10_normalize.sql already implements.
