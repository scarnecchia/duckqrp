# A-1: duplicate cohortgrp rows silently resolved (documented rejection
# unreachable from files) — cohortgrp side FIXED; covarnum side intended

- **id:** A-1
- **proposed issue title:** load_study_dict folds duplicate COHORTFILE
  rows last-wins, so validate()'s documented "duplicate cohortgrp"
  rejection can never fire from an input file
- **contract:** `StudyConfig.validate` (src/qrp/config.py:770-774)
  documents duplicate cohortgrp as an error: `raise ValueError(f"
  duplicate cohortgrp {c.cohortgrp!r}")`. The loader bypassed it:
  load_study_dict folded `cohortfile` rows into a dict keyed by
  cohortgrp (src/qrp/config.py:1582 pre-fix), so the LAST duplicate row
  silently won. INPUTS.md — the published input contract — is silent on
  duplicates, so the code's own guards are the contract source, and
  they agree elsewhere: the stockpilingfile loader already rejects
  duplicate keyed rows (config.py:1929, "stockpilingfile: duplicate row
  for group ..."). SAS comparison (read-only reference tree): SAS does
  NOT reject duplicate COHORTFILE rows — ms_processinputfiles.sas
  builds `_coverage` by inner join (both duplicate rows ride along,
  :1300-1310), dedupes only distinct PARAMETER SETS for enrollment
  builds (:1310-1318), and distributes per-group parameters through
  macro variables whose overwrites make conflicting duplicates resolve
  arbitrarily rather than raise. The shipped repo's validate guard and
  stockpilingfile precedent are therefore a deliberate strictness
  choice beyond SAS, consistent with the repo's stated philosophy that
  a silently-different answer is worse than an error (see the
  query-period comment in `_query_period`, config.py:1531-1537).
- **trigger:** An input file (or dict payload) whose `cohortfile`
  carries two rows with the same `cohortgrp` and conflicting parameters
  (e.g. enrdays 30 vs 200).
- **witness/repro command:** regression test
  `tests/test_regression_a1.py::test_duplicate_cohortgrp_rows_are_rejected`
  (standalone, no campaign infrastructure):
  `.venv/bin/python -m pytest tests/test_regression_a1.py -q`.
  Characterization evidence pre-fix: oracle cases
  `duplicate_cohortgrp_rows_collapse_to_last` and
  `duplicate_covarnum_rows_merge` in
  tests/properties/oracles/a_config_semantics.py:441-520, exercised by
  `TestInvalidConfigBoundaries::test_boundary`.
- **expected vs actual:** Expected (validate's documented rule): the
  duplicate is rejected with the "duplicate cohortgrp" error. Actual
  pre-fix: no error; `study.cohorts[g1].enr_days == 200` — the last row
  won silently.
- **first failing revision:** since c3e60db5d08343b1cebbb90d234602f09cb
  0d70d (campaign base) — the dict fold and the guards coexist there.
- **mechanism:** A dict comprehension keeps one value per key, so the
  fold collapses duplicates before validate() sees them; the guard is
  reachable only from programmatically constructed configs. Ruled a
  production defect for the cohortgrp side (validation promised by the
  code's own convention, not enforced on the file path). The covarnum
  side is DIFFERENT BY DESIGN and NOT a defect: COVARIATECODES
  legitimately carries ONE ROW PER CODE with covarnum repeated (in-code
  comment config.py:1804-1813, verified against a real 4,385-row file),
  so rows are merged per covarnum (first row's windows, union of
  codes); the `duplicate covarnum` validate guard (config.py:856-858)
  is vestigial from an earlier parser shape and rejecting duplicates
  from files would reject every real study. SAS confirms the shape:
  ms_processinputfiles.sas keeps one row per code with CovarNum
  repeated through the covariate processing (the `data
  _&COVARIATECODES.` step reading `infolder.&COVARIATECODES.`).
- **severity:** wrong answer (a doubled cohort row with conflicting
  parameters silently resolved instead of rejected; SAS resolves it
  ambiguously, not identically). **+ unproven impact:** silent
  enrollment-parameter divergence per affected cohort; no real shipped
  study file is affected (all study/*.json scanned: no duplicate
  cohortgrp rows).
- **affected paths:** src/qrp/config.py (load_study_dict fold;
  validate() guards).
- **minimal fix/commit:** branch `inv/0f462p/fix-a1` (from c3e60db):
  red commit f971710 (standalone regression test), green commit 5bbc4ff
  — detect the duplicate while folding and raise
  `ValueError(f"duplicate cohortgrp {grp!r}")`, the same message the
  validate guard uses. The covariates merge is untouched.
- **red/green commands+results:**
  - red: `.venv/bin/python -m pytest tests/test_regression_a1.py -q`
    at c3e60db+f971710 → `Failed: DID NOT RAISE ValueError` (1 failed,
    1 passed — the companion distinct-groups case already passed).
  - green: same at 5bbc4ff → `2 passed`.
- **broader validation:** tests/test_determinism.py +
  tests/test_rewrites.py in the fix worktree → `15 passed`; every
  shipped study/*.json scanned for duplicate cohortgrp rows → none, so
  no shipped file can newly fail; on the merged arb branch the
  characterization case became the documented rejection
  (`kind="documented", fragment="duplicate cohortgrp"`) and the full
  package A quick set passes (109 passed incl. determinism, rewrites,
  both regression tests).
- **related findings:** none sharing the mechanism (A-2 is the
  optional-column class).
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
