# A-3: the "TOTAL order" comment in 10_normalize.sql is false for
# full-tie duplicate demographics — doc-only finding (behavior is SAS
# parity and strictly MORE deterministic than SAS)

- **id:** A-3
- **proposed issue title:** Demographics dedupe comment claims a TOTAL
  order, but rows agreeing on all five order columns resolve by
  insertion order — correct the comment (behavior kept)
- **contract:** The in-file comment (10_normalize.sql:48-51) claims the
  demographics QUALIFY "is a TOTAL order, not just `birth_date`" over
  (birth_date, sex, race, hispanic, postalcode). The order IS total only
  when an order column separates the duplicate rows; rows agreeing on
  ALL five columns (differing only in `postalcode_date`, which is not
  ordered) resolve by first-encountered order. INPUTS.md does not
  specify the tie-break; the comment is the only documented claim, and
  it overstates.
- **trigger:** One patid with two demographic rows identical on
  birth_date, sex, race, hispanic and postalcode but different
  postalcode_date, re-extracted with a different physical row order.
- **witness/repro command:**
  `investigation/witnesses/a-demographics-full-tie-evidence.txt` (24
  battery cases), reproduced by `python -m pytest
  tests/properties/test_pkg_a_tiebreak.py -q` (TestDemographicsFullTie).
- **expected vs actual:** Comment's claim: survivor independent of row
  order. Actual: D_DIFFER_PCD order AB keeps zip_date 2011-01-01, order
  BA keeps 2012-02-29 (insert-order dependent; stable across both
  writers and threads 1/4 at battery scale). Rows separating in ANY
  order column: documented total order decides (smaller postalcode
  survives) — that half of the comment holds.
- **first failing revision:** since c3e60db5d08343b1cebbb90d234602f09cb
  0d70d (campaign base).
- **mechanism:** A window ORDER BY that does not separate the rows
  leaves row_number() among ties to the scan order. SAS comparison
  (read-only reference tree) settles the parity question: SAS dedupes
  demographics `proc sort nodupkey ... by PatId;` ONLY
  (ms_create_enrollment_spans.sas:198-201, out=worktemp.demogs,
  dupout=worktemp.multiDemogs). SAS's default PROC SORT is not stable
  (no EQUALS option), so the SAS survivor among PatId-tied rows — which
  is EVERY duplicate-demographics row — is equally order-dependent.
  DuckDB's five-column ORDER BY is therefore strictly MORE
  deterministic than SAS (any row differing in an order column is
  resolved deterministically here and not in SAS), and the full-tie
  wart is parity. Changing the behavior (e.g. extending the key with
  postalcode_date) would diverge from SAS for no parity gain.
- **severity:** reproducibility (comment/documentation defect only; the
  behavior is contained — `demographics_multi` exclusion held in every
  battery case, so duplicate-demographics patids never reach pov1 and
  cohort outputs are unaffected). **+ unproven impact:** output-side
  consumers of demographics content by patid (the attrition/geography
  join at 70_outputs.sql:109) could surface a different survivor's
  zip_date after a re-extraction — same as SAS.
- **affected paths:** src/qrp/sql/10_normalize.sql (comment only).
- **minimal fix/commit:** APPLIED in wave 3: inv/0f462p/fix3-docs
  commit b0be77e (merged into inv/0f462p/arb3-findings) applies the
  replacement comment below verbatim; comment-only, zero executable
  changes (verified by diff). Proposed replacement comment, verbatim,
  as applied:

  > The ORDER BY is total ONLY when one of these columns separates the
  > duplicate rows: birth_date, sex, race, hispanic, postalcode. Rows
  > agreeing on all five (they may still differ in postalcode_date,
  > which is not ordered) resolve by first-encountered scan order —
  > deliberately kept, because SAS's own dedupe is `proc sort nodupkey
  > by PatId` (ms_create_enrollment_spans.sas:198), whose default
  > unstable sort leaves the survivor among tied rows
  > order-dependent too. See tests/test_determinism.py and the
  > TestDemographicsFullTie battery.
- **red/green commands+results:** N/A (no behavior change proposed).
- **broader validation:** battery stable across registered vs parquet
  writers and threads 1/4 (evidence file); contained from pov1 in all
  24 cases.
- **related findings:** B2 (same conclusion shape: the wart is SAS
  parity; documentation corrected rather than behavior).
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
