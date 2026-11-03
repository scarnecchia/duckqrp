# D2: the 92_cidadenom.sql header claims exclusion shaving is NOT
# implemented — it is (doc error corrected; the inclusion half is the
# D1 parity gap)

- **id:** D2
- **proposed issue title:** Correct 92_cidadenom.sql header: exclusion-
  condition shaving IS implemented; only the inclusion restriction and
  dose-denominator restriction are missing
- **contract:** The header comment (92_cidadenom.sql:31-33 on base)
  said "NOT included: the inclusion/exclusion shaving (`excl_incl = Y`)".
  Demonstrably false on the exclusion half: the stage shaves
  EXCLUSION-condition windows out of eligible time
  (`_denom_unelig` EXC rows; `WHERE r.criteria = 'EXC'` at :309; the
  `_denom_unelig_x` / `_denom_windows_x` shaves), and the SAS original
  confirms exclusion shaving is part of `excl_incl = Y` (gate
  ms_cidadenom.sas:146; %ms_shaveinside on EXC subconditions at
  347-368, 441). The comment was accidentally right only about the
  inclusion half — which is the D1 parity gap (SAS additionally narrows
  eligible time to INCLUSION-condition windows, %ms_shaveoutside
  KeepPartBf=Y over STACK_INC, ms_cidadenom.sas:413-417, and applies
  the dose-denominator restriction, %ms_dose_denom, ms_cidadenom.sas
  :265, :359 — neither implemented; see DB-2).
- **trigger:** Read the file; no runtime input involved.
- **witness/repro command:**
  `investigation/packages/D.md` finding D2 with the verified SAS
  citations above; `git show 34f84e7` (the correction commit).
- **expected vs actual:** Expected: the header states what the stage
  implements and omits, with verified citations. Actual (base): a flat
  "NOT included" claim contradicted by the stage's own WHERE clause.
- **first failing revision:** since c3e60db5d08343b1cebbb90d234602f09cb
  0d70d (campaign base).
- **mechanism:** The comment predates the exclusion-half implementation
  (docs/PARITY_FINDINGS.md "Third fix") and was never updated; it
  misdirects a parity reviewer away from the one half that IS parity-
  correct and toward believing the whole excl_incl branch is missing.
- **severity:** doc-only (no behavioral effect).
- **affected paths:** src/qrp/sql/92_cidadenom.sql (header comment,
  lines 31-33 on base).
- **minimal fix/commit:** inv/0f462p/fix3-docs commit 34f84e7 — the
  header now states: EXC shaving implemented (with the in-file anchors
  and SAS lines); INC restriction and dose restriction NOT implemented
  (with SAS lines 413-417 / 265, 359) and member-days overstated for
  inclusion-criteria studies; see docs/SAS_PARITY.md.
- **red/green commands+results:** N/A (doc-only). Validated by
  `git diff c3e60db fix3-docs -- src/` showing zero non-comment line
  changes; determinism + rewrites green on the merged tree.
- **broader validation:** package D denominators quick suite 7 passed
  on the merged tree (behavior untouched).
- **related findings:** D1 (the implementation gap the corrected
  comment now names), DB-2 (the decision brief for that gap), package D
  D2 (original report).
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
- **SCDM 8.2.2 alignment:** reviewed 2026-11-03 — comment-only
  documentation fix; no SCDM data-model-bearing assumption.
