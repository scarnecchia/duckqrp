# Arbitration — packages A and B findings (arbiter report)

Arbiter: general-purpose subagent, branch `inv/0f462p/arb-findings`.
PRIVATE — NOT AUTHORIZED FOR PUBLICATION.

Findings arbitrated: B2 (with the B1 fix-location question), A-1, A-2,
A-3 (lightweight). All witnesses were re-derived independently from
code + docs and re-reproduced before classification; midway through the
arbitration the operator supplied the ORIGINAL SAS QRP source tree
(read-only, /home/scarndp/dev/Sentinel/qrp/, package 14.5.0), and the
B1/B2 ruling was re-grounded in the SAS source itself, reversing the
initial direction. Finding documents live in investigation/findings/
(B2-overlapping-enrollment-double-count.md,
A-1-duplicate-cohortgrp-guard-unreachable.md,
A-2-optional-rxamt-binder.md, A-3-demographics-full-tie-comment.md).

## Rulings at a glance

| id | classification | shipped fix | fix branch |
|---|---|---|---|
| B2 | DEFECT — inherited bug-for-bug from the SAS original; parity kept by operator ruling | none (documented; the pass-1 strict-xfail pin was later replaced by positive parity pins — see the witness repair note in the outcome section) | none (prototype validated then removed per ruling) |
| B1 (question) | RESOLVED by SAS source, closed by operator ruling: segmentation is parity-intended; the double-count is inherited | — | — (DB-1 below, answered) |
| A-1 | production defect (cohortgrp side); covarnum merge intended | YES | inv/0f462p/fix-a1, red f971710 → green 5bbc4ff |
| A-2 | production defect (published contract violated) | YES | inv/0f462p/fix-a2, red 1577f83 → green 564eb7a |
| A-3 | documentation defect (comment false; behavior is SAS parity) | none (comment wording proposed in the finding doc) | — |

Both shipped fixes are merged into this arb branch together with the
property-suite updates (B2 witness rewritten to pin SAS-parity
semantics; A-1 characterization case now the documented rejection; A-2
xfail removed; campaign harness fmt mirror extended for the two new
normalize placeholders). Validation on the merged arb tree: package A
quick set + determinism + rewrites + both regression tests → 109
passed; package B quick suites → 8 passed (0 xfailed); package C +
harness stages → 38 passed. Full tests/test_pipeline.py in BOTH fix
worktrees → 287 passed, 2 failed (the two pre-existing exempt
spill-attribution OOM tests), 1 skipped.

## Decision brief DB-1 (verbatim-ready for the operator)

**Question B1 (from investigation/packages/B.md):** is the lag-break
enrollment segmentation intended SAS parity, and if so does the B2 fix
belong in the shave joins?

**Answer, per the original SAS source — with a finding that changes the
question:** Yes, the segmentation is intended parity, and the original
SAS pipeline double-counts overlapping-span dispensings too. Neither
candidate fix preserves parity; both are deliberate divergences, so the
choice is a policy decision, not a bug fix.

**Evidence (all paths under the read-only SAS reference tree
/home/scarndp/dev/Sentinel/qrp/, quoting verified by the arbiter):**

1. **SAS emits overlapping enrollment episodes.**
   `inputfiles/macros/ms_episoderec2.sas:67-77`: the episode break is
   `if &ENRSTART.-lag(&ENREND.)-1 > &ENROLGAP. or MedCov ne
   lag(MedCov) ...` — against the immediately PREVIOUS span, exactly
   the shipped 20_enrollment.sql:67 formula (docs/SAS_PARITY.md
   "Enrollment spans" documents this equivalence; now verified at the
   source). Lines 79-97 then reconcile min/max per (PatId, episode).
   On the probe input (A=[Jan 1..Dec 31], B=[Feb 15..Jun 15],
   C=[Sep 1..Sep 30], gap 0) SAS emits episode 1 = [Jan 1..Dec 31] and
   episode 2 = [Sep 1..Sep 30] — overlapping. No overlap dedup exists
   anywhere in the macro, so the shave reference
   `worktemp.enr_&enrollmentnum.` genuinely contains overlapping
   periods. An enrollment-side guard would therefore DIVERGE from SAS.
2. **SAS's own shave fans out per overlapping span.**
   `inputfiles/macros/ms_shaveoutside.sas` (macro body): "need
   cartesian product first" — `proc sql ... from &ToShaveFile. as in,
   &reffile. as ref where in.PatId=ref.PatId and ref.&refstart. <=
   in.&ToShaveEnd. and in.&ToShaveStart. <= ref.&refend.;` then the
   data step shaves EACH pair (KeepPartBf=N drops starts before the
   span; end clipped; `if RxSup >=1 then RxSup=&ToShaveEnd.-
   &ToShaveStart.+1;` and the amount prorated). Called from
   `ms_cidanum.sas:1299-1308` (pre-shave) and again `ms_cidanum.sas:
   1531-1541` ("Need to reshave because Stockpiling may have pushed
   claims outside enrollment period") — both against the same
   overlapping reference, with NO dedupe between shave, stockpiling,
   and re-shave.
3. **SAS sums the fanned copies.** `ms_stockpiling.sas` (proc means
   same-day collapse, `output out=&outfile. ... &SAMEDAY_SUPP_OPER.
   (&CLMSUP.)= ... N(&CLMSUP.)=NumDispensing;`) collapses the two
   shaved copies of one claim into sup 46 / amt 15.33 / numdispensing=2
   before the push loop; the re-shave then fans out again to 16/5.33 +
   46/15.33. That is EXACTLY the shipped SQL's pre-fix output on the
   probe (reproduced by investigation/probes/b_overlap_probe.py and
   pinned as
   test_enrollment_overlapping_raw_spans_sas_parity).

**Consequences for the two candidate fixes:**

- Enrollment-side guard (merge overlapping spans in 20_enrollment.sql):
  diverges from SAS at the segmentation — rejected.
- Shave-join fix (one containing span per claim): preserves the
  segmentation but diverges at the shave — SAS's fan-out + same-day sum
  IS the double-count, so "fixing" it moves results away from SAS on
  every study whose extract has overlapping raw enrollment rows. Built
  and validated on branch `inv/0f462p/fix-b2` (standalone regression
  red f4d2e6a / green c87dc0b; determinism+rewrites 15 passed; full
  test_pipeline 287 passed with only the 2 known exempt spill
  failures) but NOT merged: with the original SAS tree available, the
  shipped behavior is parity and the branch is a policy option, not a
  repair.

**Outcome (operator ruling, final):** B2 is documented as a DEFECT —
inherited bug-for-bug from the SAS original. No fix branch; the
Python-side fix would diverge from SAS parity, which is not authorized.
The Sentinel model QA Suite is expected to prevent overlapping
enrollment spans upstream (mitigation context, unverified in this
campaign). The strict-xfail witness
`test_enrollment_overlapping_raw_spans_known_divergence` remains in
`tests/properties/test_pkg_b_enrollment.py` as the pin documenting
current behavior. The full defect report is
investigation/findings/B2-overlapping-enrollment-double-count.md. The
prototype divergence branch inv/0f462p/fix-b2 was validated
(red f4d2e6a / green c87dc0b; determinism+rewrites 15 passed;
tests/test_pipeline.py 287 passed with only the two pre-existing exempt
spill failures — manifest passes arb-red-b2-1 / arb-green-b2-1) and
then removed per this ruling; its results are the record of what the
join-side fix would look like.

**Witness repair (closing records, 2026-11-02):** the strict-xfail
witness named above (`test_enrollment_overlapping_raw_spans_known_divergence`)
no longer exists. It was replaced at B pass 2 (oracle-only, integrated
at 88edf91; final work on `inv/0f462p/b-final`, head 4891dfa, integrated
into the closing base 3709ab2) by the positive parity pins
`test_enrollment_overlapping_raw_spans_parity`
(tests/properties/test_pkg_b_enrollment.py — explicit B2 witness
asserts: one dispensing, supply 46 > 30, numdispensing 2 > 1; witness,
not endorsement) and `test_stockpile_fanout_parity_on_overlapping_spans`
(tests/properties/test_pkg_b_stockpile.py). The ruling and its
classification are unchanged; the current pins are recorded in the
manifest findings[] B2 witness and in B.md's Properties table.

**Evidence the ruling rests on (all paths under the read-only SAS
reference tree /home/scarndp/dev/Sentinel/qrp/, quoting verified by the
arbiter):**

## Surprises found during independent review

1. **The finder's B2 oracle premise was inverted (the big one).** The
   day-model oracle (one merged episode, one count) is sane arithmetic
   but NOT the project contract; the strict-xfail "known divergence"
   documented a divergence from SAS that does not exist. This was
   settled only when the operator supplied the original SAS source;
   before that, the arbiter had accepted the parity-of-segmentation
   argument and built the shave-join fix (branch kept, unmerged).
2. **New defect-class lead (unfixed, out of scope):** diagnosis
   optionals `dx_codetype`, `pdx`, `enctype` are declared optional in
   INPUTS.md:180 but referenced unconditionally in 10_normalize.sql
   (`_cdm_diagnosis_raw`) — the same class as A-2. Found because the
   standalone B2 regression extract crashed on them before reaching
   the B2 mechanism. FIXED in wave 2 as A-4 (see below).
3. **A.md's claim "Real files have produced doubled rows before (per
   code comments)" could not be substantiated** — no such comment
   exists in the repo (grep across src/ and docs/). The A-1 fix stands
   on the code's own validate guard + stockpilingfile precedent, not
   on that claim.
4. **The A-1 covarnum half is intended by design** (one row per code
   with covarnum repeated is the real file shape, confirmed both by
   the in-code comment citing a 4,385-row real file and by SAS's
   covariatecodes processing); only the cohortgrp half is a defect.
5. **DuckDB's demographics tie-break is strictly more deterministic
   than SAS's** (five ordered columns vs SAS's `by PatId` unstable
   sort) — A-3's wart is parity, and the SAS comparison turned a
   would-be bug into a documentation correction.
6. **SAS requires rxamt** on the Type 2 path (ms_shaveoutside
   shaverx=Y references RxAmt; ms_stockpiling collapses on it), so
   INPUTS.md's "rxamt (dose only)" optional declaration is a deliberate
   tool-level divergence from SAS's accepted inputs — the A-2 fix
   honors the tool's published promise, not SAS's stricter requirement,
   and the finding doc records the nuance.

## Wave 2 — arbitration of F1, F2, and the A-4 lead (arb2 report)

Arbiter: second general-purpose subagent, branch
`inv/0f462p/arb2-findings`. All three findings were re-derived
independently from code + docs + the read-only SAS reference, and the
witnesses were re-reproduced (F pins xfail at HEAD; A-4's
BinderException reproduced at base c3e60db AND discovery HEAD 2a71a0c
via a detached throwaway worktree) before classification. All three
classified as production defects. F1/F2 and the diagnosis subset of A-4
have local fixes validated. The expanded A-4 procedure/lab family is now
also fixed and validated, including a labs-enabled property suite. No fixes
were published. The parent has not merged arb2. Current complete-family
commands and AC9 collection evidence: [A4-validation.md](A4-validation.md).

### Rulings at a glance

| id | classification | fix branch (from c3e60db) | red / green |
|---|---|---|---|
| F1 | production defect — SAS parity + the funnel's own declared contract | inv/0f462p/fix2-f1 | 0bcb1a8 / 3001086 |
| F2 | production defect — undocumented SAS parity divergence in the denominator banding | inv/0f462p/fix2-f2 | 0e7eeee / 37e3d89 (amended) |
| A-4 | production defect — complete 18-column family fixed | inv/0f462p/fix3-a4-complete | 8bc521e / a6a74e4; final f1dca19 |

### F1 — attrition forward-fill (finder claimed HIGH; confirmed)

The funnel's counted steps produced their counts with GROUP BYs over
each step's relation; an EMPTY relation yielded no row, `n` came back
NULL, and the blank-row carry
`coalesce(n, last_value(n IGNORE NULLS))` — correct for the skeleton's
blank rows — forward-filled the last member count through the episode
block into level 27, whose own descr declares "Unique number of
members in final cohort". SAS never leaves a counted step without a
number: %ms_attrition_cidacompute fills `:remaining` from
`count(*)` over the step's data — 0 on empty
(ms_attrition_cidacompute.sas:77-88) — and ONLY skip rows carry
forward (:112-119); the initial-episode and final rows are information
rows carrying the actual count (ms_attrition.sas:291-297, 1008-1015).
The fix counts a missing count row as 0 at every
init/excl/final step: the first empty step absorbs the drop as its
exclusion, later steps report 0, level 27 reports the real final
cohort size; blank rows keep carrying; non-empty extracts are
unchanged. Regression (standalone, inline two-cohort extract): a
claims-free cohort reads (0, 2) at level 6 — red on c3e60db as (2,
None) — plus a surviving-episode pin. Both F1 xfail pins flipped to
passing on this branch.

### F2 — npts > dennumpts in stratified cells (finder claimed MEDIUM; confirmed)

The numerator bands episodes at the index date (45_pov1.sql:78, SAS
ms_createpov1.sas:102-116); the denominator banded each eligible
window ONCE at denom_start (92_cidadenom.sql, "earliest date an index
could occur in it"). SAS's denominator instead SPLITS the window at
the age thresholds — ms_cidadenom.sas:1036-1057 walks the bands
oldest-first, outputs one span per overlapped band with its own
member-days, shrinking the window to threshdate-1 — so a member counts
in every band their eligible time touches, and num ≤ den holds per
cell by construction. The tool's single-anchor banding is therefore an
UNDOCUMENTED parity divergence (SAS_PARITY.md's denominator
verification reconciled level margins only, never per-band cells), not
an intended semantic. The fix emits one span per overlapped band
(entry = birth + lo units; end = next band's entry − 1 day; member-days
per span; index_year at span start). SAS's merge behavior (num-only
rows get DenNumPts=0, ms_cidatables.sas:272-285) was already correct
in t2_cida. Regression: a threshold-crossing member (born 1936-06-15,
query 2011, index at 75) is banded 65-74-only on c3e60db (red);
post-fix both bands exist with a 165/200 member-day split.

**Nondeterminism caught during fix validation:** the first green used
`lead() ... ORDER BY ordinal` to find each band's end — but
`_denom_windows` holds multiple shave segments per (member,
eligepisode), so the tie-ordered lead() paired rows arbitrarily and
the emitted spans depended on physical row order (test
test_denominator_is_shaved_around_outcome_events flapped: beta_blocker
member-days moved between two IDENTICAL runs; proven on a frozen stage
input — 5 runs, 5 distinct digests — while the base view under the
same harness showed 0 diffs). The shipped green replaces the window
function with per-band interval offsets (bounds are pure band data),
5 runs → 1 digest. Residual, documented: the denominator still does
not split at year boundaries (SAS's period splitting) — pre-existing,
outside this finding's scope.

### A-4 — diagnosis optionals (wave-1 lead; fixed as predicted)

INPUTS.md:180 declares `dx_codetype`/`pdx`/`enctype` optional;
`_cdm_diagnosis_raw` referenced all three unconditionally. Reproduced
at c3e60db AND HEAD (`Binder Error: Referenced column "dx_codetype"
not found`) with an extract carrying rxamt (so A-2's fixed path does
not mask it). The fix mirrors fix-a2's mechanism exactly: the
`_optional` loop resolves the trio (column or NULL) and the view reads
`{opt_dx_codetype}`/`{opt_pdx}`/`{opt_enctype}` — NULL codetype is the
exposure join's match-any convention (30_exposure.sql:76-82); NULL
pdx/enctype fall to the view's own COALESCE wildcards ('X'/'XX').
Consumers were verified before the initial diagnosis-only fix. Historically,
package A's pass-2 sweep at `f5a2a85` extended the same defect to two
procedure and thirteen lab columns. All 18 are now resolved by the clean
`inv/0f462p/fix3-a4-complete` branch: red `8bc521e`, green `a6a74e4`,
final typing correction `f1dca19`. The original `fix2-a4` is superseded,
not deleted. Compatibility changes to existing tests and the campaign
harness remain arb2-only. See [A4-validation.md](A4-validation.md).

### Validation on the merged arb2 tree

Merges: fix2-a4 + fix2-f1 + fix2-f2 (clean; campaign harness fmt
mirror extended with the diagnosis keys — the same mirror fix-a2
needed; all four F xfail pins flipped to passing assertions). Runs:
tests/properties/test_pkg_f_totals.py + the three standalone
regression suites + determinism + rewrites → 26 passed, 0 xfailed;
the other F families + package A quick set → 67 passed; package B
quick suite → 2 passed, 1 xfailed (the B2 strict parity pin,
unchanged). Full tests/test_pipeline.py on the merged tree → 287 passed,
1 skipped, 2 failed in 403.09s. Both failures are the pre-existing exempt
spill-attribution tests (`test_spilling_is_attributed_to_the_statement_that_caused_it`
and `test_spill_summary_names_the_statement_in_the_log`). The shell job
returned exit 0, but pytest itself reported these two failures; this is
not an all-green pipeline result.

### Branch inventory (wave 2)

- `inv/0f462p/arb2-findings` (this branch): the three fix merges,
  harness mirror extension, xfail flips, finding docs
  (F1-attrition-empty-step-forward-fill.md,
  F2-denominator-age-band-split.md, A-4-optional-diagnosis-columns.md),
  manifest + this file.
- `inv/0f462p/fix2-a4`, `inv/0f462p/fix2-f1`, `inv/0f462p/fix2-f2`:
  shipped fixes, merged.
- `wt/arb2-throwaway`: detached c3e60db worktree used for the A-4 base
  reproduction.

## Surprises found during independent review (wave 2)

1. **The F2 fix's first version was itself nondeterministic** — a
   defect the finder's property suite could not see, because the F
   totals properties compare artifacts WITHIN one run, never across
   repeated identical runs. A shave test that asserts cross-run
   equality caught it. Worth remembering for future SQL fixes that add
   window functions over non-unique partition keys.
2. **`_cdm_procedure_raw` has A-4's exact defect class** (optional
   table, optional columns `px_codetype`/`enctype` referenced
   unconditionally) — discovered while scoping A-4, left as a lead.
3. **SAS's L7 excluded is NULL by design** (information row, `excluded`
   parameter defaults to `.`) — the tool's init rows already match,
   so only the empty-relation counts needed fixing, not the excluded
   semantics.
4. **DuckDB 2.0's `DATE + INTERVAL` is TIMESTAMP** — the band-entry
   expressions need explicit DATE casts (the codebase already does
   this shape in age_yrdif); a naive port of SAS's intnx arithmetic
   binds but poisons downstream date arithmetic.

## Branch inventory

- `inv/0f462p/arb-findings` (this branch): B2 strict-xfail pin restored
  with the ruling context, A-1/A-2 fix merges, property-suite updates,
  finding docs, this file, manifest updates. (Historical note, closing
  records 2026-11-02: the strict-xfail pin restored here was later
  replaced by the positive parity pins at B pass 2 — see the witness
  repair note in the B2 outcome section.)
- `inv/0f462p/fix-a1`, `inv/0f462p/fix-a2`: shipped fixes, merged.
- `inv/0f462p/fix-b2`: REMOVED per the operator's B2 parity ruling —
  no fix branch for B2. The validated prototype's results (red
  f4d2e6a / green c87dc0b, 287-passed pipeline run) are preserved in
  the manifest passes and summarized in the B2 finding doc's
  not-applied minimal-fix section.

---

# Arbitration wave 3 — packages E, C leads, D doc errors (arbiter 3 report)

Arbiter: third findings arbiter (general-purpose subagent), branch
`inv/0f462p/arb3-findings`. PRIVATE — NOT AUTHORIZED FOR PUBLICATION.

Findings arbitrated: E-1/E-2/E-3/E-4 (runtime lifecycle leads from
package E), the C-1 comment (verified against the SAS original and
reclassified as a parity-gap lead), C-3/OC-3 (person_days floor,
resolved against the SAS original), D2 (demonstrated doc error,
corrected), and A-3's proposed comment wording (applied verbatim).
D1's implementation question is assembled as decision brief DB-2
(no code). Every SAS citation below was re-verified by this arbiter in
the read-only reference tree /home/scarndp/dev/Sentinel/qrp/ (package
14.5.0); every fix was re-derived from code + docs, reproduced red, and
validated green before merging. Finding documents live in
investigation/findings/ (E-1, E-2, C-1, C-3, D2, E-3-E4).

## Rulings at a glance

| id | classification | shipped fix | fix branch |
|---|---|---|---|
| E-1 | DEFECT — resource release: the TUI is the RunHandle's caller (close is caller-owned) and a long-lived multi-run session is its designed usage; every completed run leaked one live engine holding its in-memory database | YES | inv/0f462p/fix3-e1, red 323b231 → green 0a26573 |
| E-2 | DEFECT (low) — same release gap on the CLI failure path; process exit bounds it for subprocess use, but main() is library-callable and in-process callers accumulate | YES (same branch — the fix shape is the same try/finally, per the wave-3 charter) | inv/0f462p/fix3-e1, red 323b231 → green 0a26573 |
| E-3 | CONTRACT CHOICE — SIGINT mid-run leaves a truncated log with no terminal record; no contract exists for exit code/diagnostics/terminal record | none (decision brief DB-3) | — |
| E-4 | CONTRACT CHOICE — pipeline.run emits no failure-path RunFinished; only RunHandle synthesizes it; LOGGING.md's "identical stream" parity holds for successes only | none (decision brief DB-4) | — |
| C-1 | PARITY-GAP LEAD — SAS enforces length criteria on the post-truncation episode (ordering inside ms_createptsmasterlist.sas:124→156/160/162/165→185); this implementation filters in 50 on the pre-truncation end and 55 re-checks nothing. The comment claiming the re-check was FALSE — corrected | comment only (dff222c); behavior = DB-5 | inv/0f462p/fix3-docs |
| C-3 (OC-3) | DEFECT — person_days unfloored reads negative when at_risk_start outruns the episode; SAS floors the same at-risk span at 0 (Max(0,...), ms_finalizeptsmasterlist.sas:305) and deletes zero-at-risk episodes (ms_createptsmasterlist.sas:175-176); negative rows sum into the denominators and inflate rate_per_1000_py | YES | inv/0f462p/fix3-c3, red 26ddf87 → green 1a810a2 |
| D2 | DOC ERROR — header claims exclusion shaving is NOT implemented; it is (92:309 `WHERE r.criteria = 'EXC'`; SAS ms_cidadenom.sas:146, 347-368, 441) | YES (comment) | inv/0f462p/fix3-docs, 34f84e7 |
| A-3 | DOC ERROR (wave 1) — proposed replacement wording applied verbatim | YES (comment) | inv/0f462p/fix3-docs, b0be77e |
| D1 | PARITY GAP (confirmed by D against the SAS original; re-verified by this arbiter at ms_cidadenom.sas:146/413-417/265+359) | none (decision brief DB-2) | — |

## Validation record (merged arb3 tree, HEAD at commit of this file)

- tests/test_regression_e1.py + tests/test_regression_c3.py +
  tests/test_determinism.py + tests/test_rewrites.py +
  tests/properties/test_pkg_a_tiebreak.py → **43 passed**.
- tests/test_ui.py → **35 passed, 2 skipped** (the pre-existing
  /tmp/qrp_data/2m skips).
- Package E quick set (lifecycle/faults/outputs/cli_tui,
  --hypothesis-profile=quick) → **21 passed**.
- Package C quick suites (inclusion/dose/followup, quick) →
  **31 passed** after the OC-3 pin and oracle were updated to the
  floored semantics (see Surprises 1).
- Package D denominators quick → **7 passed** (92 comment-only change
  confirmed inert).
- Full tests/test_pipeline.py in the fix3-c3 worktree → **287 passed,
  2 failed** (the two PRE-EXISTING EXEMPT spill-attribution OOM tests,
  same two as wave 1's record), 1 skipped, 7m32s.
- mypy on the changed Python files clean; ruff --select E4,E7,E9,F
  clean on changed files; fix3-docs diff verified to contain ZERO
  non-comment SQL line changes.

## Decision brief DB-2 (verbatim-ready for the operator)

**Question D1 (from investigation/packages/D.md):** the Python
implementation omits SAS's INCLUSION-subcondition window restriction
and dose-denominator restriction in the CIDA denominators under
`excl_incl = Y`. Implement, document, or partially implement?

**Evidence (all re-verified in the read-only SAS tree by this
arbiter):** `ms_setnumloopmacrovars.sas:653-681` sets `excl_incl = Y`
whenever a study has inclusion codes (default N at :653-654), so the
branch is active for ANY study with INCLUSIONCODES. Inside it
(ms_cidadenom.sas:146 gate): INCLUSION subconditions build claim-index
windows (`EligStart = ADate - CondTo`, `EligEnd = Expiredt/ADate -
CondFrom`) and RESTRICT eligible time to them via `%ms_shaveoutside`
KeepPartBf=Y over `_Denom&Group._STACK_INC` (ms_cidadenom.sas:413-417;
windows read at :197-212 per D); EXCLUSION subconditions shave OUT via
`%ms_shaveinside` (:347-368, merged STACK_EXCL shaved at :441) — that
half IS implemented (92_cidadenom.sql:309 `WHERE r.criteria = 'EXC'`).
Dose thresholds additionally restrict eligibility through
`%ms_dose_denom` (:265, :359) — not implemented.

**Impact, honestly stated:** affected shape = any study whose cohorts
configure INCLUSIONCODES (conditions). For those, member-days are
OVERSTATED — SAS narrows eligibility to the inclusion windows and
(dose configured) to dose-eligible members; this implementation keeps
all enrolled time minus exclusions. Direction is certain (overstated);
magnitude depends on how restrictive the study's inclusion windows and
dose thresholds are relative to enrollment. Unaffected: studies without
inclusion codes (excl_incl stays N; the EXC-half parity is exact —
package D's day-enumeration oracle agrees with production on every
generated case including exclusion shaving, deaths, washouts, OUTPUTDENOM
M/N, gapped enrollment). Consistent with docs/PARITY_FINDINGS.md
"Third fix", which implemented only the exclusion half.

**Options:**

- **(a) Implement both for parity.** Feature work, NOT a minimal fix.
  Scope estimate (this arbiter's): the 92 stage needs a per-config
  build of INC-subcondition windows from the inclusion config (the
  episode-side 52 machinery builds different windows — denominator
  windows are claim-index anchored, not episode anchored), the
  %ms_dose_denom analogue over `claim_dose`, reuse of the existing
  merge/shave plumbing (`_denom_unelig` merge + `_denom_windows_x` for
  the EXC half gives the shape), then oracle + property work:
  d_denom's day-enumeration oracle must model inclusion windows and
  dose restriction, and test_pkg_d_denominators' generator must emit
  INC-condition studies (it currently emits none). Realistic effort:
  days of focused work plus a discovery-style validation pass; risk
  concentrated in the window-build semantics (CondFrom/CondTo,
  codedays, dateonly) where 52 already encodes the episode-side
  reading that DB-2's implementation would have to mirror for the
  denominator.
- **(b) Document as a known divergence.** One paragraph in
  docs/SAS_PARITY.md (shape: affected studies, direction, the D2
  header already points there since 34f84e7) + the CIDA denominator
  section of README. Honest and cheap; leaves INC-condition studies
  overstating member-days with no tool-side warning.
- **(c) Implement only one restriction.** Not recommended as an end
  state: the two restrictions interact (dose restriction applies to
  members, windows to time; implementing windows alone still
  overstates for dose-restricted studies and vice versa), so a partial
  implementation is a smaller divergence, not a correct subset — it
  only makes sense as a staged step toward (a).

**Recommendation the brief is written to support (not a ruling):** if
INC-condition studies are in production scope, (a) with the D-style
oracle extension; if not, (b) now, (a) when the first INC-condition
study arrives.

## Decision brief DB-3 (verbatim-ready for the operator)

**Question E-3:** what SHOULD `qrp run` do on SIGINT mid-run — exit
code, diagnostics, and what terminal record the logs carry? (Current:
KeyboardInterrupt is a BaseException, `except Exception` cannot catch
it; the process dies with a raw traceback, exit −2/SIGINT, the JSONL
stream ends with no terminal record (last observed line
StageFinished(normalize)), no FAILED line reaches the .log, and no
manifest survives.)

**Evidence:** errors.py's charter — "The runbook promises actionable
errors; that promise has to hold wherever the failure happens" — is
currently unmet on this path. ARCHITECTURE.md fully specifies IN-APP
cancellation (flag + boundary → CancelledRun →
RunFinished(cancelled=True), error stays empty, no ERROR LogMessage —
model M4) but says nothing about process signals. LOGGING.md frames the
.log as "what you attach to a parity report or a bug": a SIGINT-killed
run is indistinguishable from a truncated log. OUTPUTS.md's
manifest-last rule means the absence of a manifest correctly marks the
run incomplete — the one part of today's behavior any option must
preserve. Resource note: since the E-2 fix, the CLI's finally DOES
release the engine on SIGINT (finally unwinds for BaseException); what
remains open is purely the contract.

**Options:**

- **(a) Keep the current behavior, document it.** Cheap. Defensible
  for a local tool whose outputs are manifest-guarded; a shell that
  reports exit 130 for signal deaths is the user's existing mental
  model. Cost: the JSONL stays terminal-incomplete and the .log reads
  truncated — "which stage got slower" tooling (LOGGING.md's compare
  runs) silently loses the run.
- **(b) Catch KeyboardInterrupt in cli.main around the run block; write
  a terminal record; exit 130.** Smallest honest change: the cancelled
  semantics already exist end to end (M4) — reuse them: a
  RunFinished(cancelled=True) into the JSONL (preserving the
  CLI/UI-parity promise of LOGGING.md:81-91), a "CANCELLED" line in
  the .log where FAILED goes today, a one-line actionable diagnostic on
  stderr, exit 130 (128+SIGINT convention). Caveats: the catch must
  wrap from before Engine construction (a SIGINT during construction or
  output writes must not traceback either); do NOT emit ok=False
  (cancelled is its own state, and the empty-error convention is
  tested).
- **(c) Signal-handler cancellation: install a SIGINT handler that
  requests engine.cancel() and lets the run unwind as CancelledRun.**
  Most faithful to ARCHITECTURE.md's cancellation story (the query
  actually interrupts; stages stop at boundaries) and gives the TUI-
  identical event stream — but the CLI drives pipeline.run directly
  (no RunHandle), so this adds signal plumbing to the CLI and changes
  timing behavior (second SIGINT must still hard-kill; partial-stage
  cancellation latency ~0.3s measured in ARCHITECTURE.md). Most work;
  most uniform.

**Recommendation the brief is written to support (not a ruling):** (b),
with (c) as the follow-up if operators report long-running SIGINTs
leaving confusing partial JSONLs; (a) only if SIGINT is considered
operator error.

## Decision brief DB-4 (verbatim-ready for the operator)

**Question E-4:** who owns the FAILURE-path terminal event?
`pipeline.run` emits RunStarted/RunFinished around the happy path only;
on failure the exception propagates — the UI path
(RunHandle._work, runner.py:128-142) synthesizes
RunFinished(ok=False, error=explain(exc)) into the same stream, but the
CLI/JSONL stream carries no terminal event at all. LOGGING.md:81-91
promises "Both now come from pipeline.run, so the CLI and the UI
observe an identical stream" (pinned by
test_cli_and_ui_emit_the_same_events) — true for successes, silently
false for failures.

**Evidence:** LOGGING.md's parity section exists precisely because the
two paths DRIFTED once before (the "bug this found" section); the
failure path is the same drift shape. The safe direction is asserted by
package E (nothing claims ok on failure — outputs/manifest honesty is
intact; the .log does get the CLI's "FAILED:" line via runlog.write).
What is missing is machine-readable: a JSONL consumer replaying runs
(LOGGING.md's compare-runs use case) cannot tell a failed run from a
truncated one.

**Options:**

- **(a) pipeline.run owns the failure terminal too:** wrap the stage
  sequence, emit RunFinished(ok=False) on exception, re-raise. The
  parity promise becomes unconditionally true; the CLI JSONL gains the
  terminal record with zero CLI code. Cost — the coupling that makes
  this NOT a minimal fix: RunHandle._work currently synthesizes the
  same event and would double-emit for run() failures; its synthesis
  also covers failures BEFORE run() is reached (Engine construction,
  cancel-during-startup), so ownership can't just move — _work needs a
  guard (emit only if run() didn't), which re-splits ownership across
  two files and re-opens the drift LOGGING.md warns about. Also needs
  a decision on cancelled runs (CancelledRun through run(): emit
  cancelled=True there, and _work's guard must respect it).
- **(b) Document the split ownership:** pipeline.run owns terminal
  events for completed runs; drivers own failure synthesis. One
  paragraph in LOGGING.md + amend the parity sentence to "identical for
  successes; on failure the driver synthesizes the terminal event".
  Honest, cheap, keeps the asserted safe direction as the contract.
- **(c) Invert: drivers own ALL terminal events** (UI unchanged; CLI
  gains synthesis around run()). Fails LOGGING.md's own history — the
  original bug WAS drivers-only emission; re-centralizing on drivers
  re-creates the [3/0] header problem for any third driver that
  forgets.

**Recommendation the brief is written to support (not a ruling):** (b)
now (documentation-only, zero risk); (a) only as deliberate feature
work with the _work guard and the CancelledRun interaction designed,
not slipped in.

## Decision brief DB-5 (verbatim-ready for the operator)

**Question C-1:** SAS re-checks the length-dependent criteria on the
POST-truncation episode (ms_createptsmasterlist.sas:124 truncation →
MinEnrolMet3 :156, MinObsEnrolMet2/4 :160/:162, MinEpiDurMet :165 →
subsetting IF :185); this implementation filters in 50 on the
pre-truncation end and 55's truncation re-checks nothing. Implement the
re-check, or document the divergence?

**Evidence:** investigation/witnesses/c1-dose-censor-recheck-sas-evidence.txt
(SAS line walk); the false comment claiming the re-check was corrected
in fix3-docs dff222c. Affected shape: studies with maxcumdose AND
cumdoseper set where the dose censor pushes an episode below minepisdur
or below the required observable days. Magnitude: UNMEASURED — no
witness study exists yet; this is the same evidence-first gap the B2
ruling insisted on closing before classifying.

**Options:**

- **(a) Implement the SAS-ordered re-check.** Not a minimal fix: 55
  must recompute the end-dependent flags (min_epi_dur_met; the
  reqdaysaftepi/obs-window equivalents that read EpisodeEndDt) on the
  truncated end and remove failing episodes — from ptsmasterlist,
  which every later stage reads, so the change ripples through
  episode_hoi, cohort_final, attrition, and every C/D oracle that
  models the current single-pass order. Requires the B2-standard
  evidence first: a witness study with maxcumdose tipped on a
  short-episode patient, quantifying how many episodes SAS deletes
  that this implementation keeps. Realistic effort: comparable to
  DB-2 (a).
- **(b) Document as a known divergence + pin.** A strict-xfail-style
  witness (B2 precedent) plus a SAS_PARITY.md paragraph. Cheap, honest,
  and the magnitude evidence it records feeds (a) if ever warranted.
- Note: there is no option "fix the comment only" — dff222c already
  did that; the question is purely behavioral.

**Recommendation the brief is written to support (not a ruling):** (b)
with the witness study, unless a production maxcumdose study is in
scope — then (a) with the oracle work budgeted up front.

## Surprises found during independent review

1. **Package C's OC-3 pin was load-bearing in the opposite direction:**
   the property suite had pinned `person_days == -1` as observed
   behavior, so the C-3 fix turned a suite test red. The pin and the
   c_models oracle were updated to the floored semantics on the arb
   branch (wave-1 precedent: the B2 witness rewrite), and OC-3 is
   closed by the ruling.
2. **The wave-3 assignment pointer for C-1 was imprecise:** the
   dose-truncation path is NOT in ms_episoderec2.sas (no maxcumdose
   logic there at all) — it is ms_createpov4.sas:178-201 (compute) →
   ms_createptsmasterlist.sas:124 (apply) → same-step flags
   (:156-:185, AFTER the truncation). The re-classification of C-1
   from "correct the comment" to "parity-gap lead + correct the
   comment" rests on that corrected trail.
3. **The C-3 regression fixture tripped two pre-existing input
   landmines** worth recording: SCDM patid must be INTEGER
   (10_normalize casts `patid AS BIGINT`), and the diagnosis optionals
   dx_codetype/pdx/enctype are declared optional in scdm.py but
   referenced unconditionally in normalize — the same unfixed lead the
   first arbiter recorded (ARBITRATION.md wave-1 Surprises 2). A
   conformant-looking minimal extract that omits them cannot run at
   all.
4. **The E-1 fix has a cancellation race to avoid and the ordering
   matters:** closing the engine BEFORE flipping `running` False would
   let a Cancel landing at the completion instant hit a CLOSED engine
   (`con.interrupt()` raises ConnectionException) via action_cancel.
   The fix flips `running` first — a cancel in the gap reaches an
   open, idle engine, which ARCHITECTURE.md documents as a silent
   no-op.
5. **E-2's fix quietly improves the E-3 path:** the CLI finally now
   releases the engine even on KeyboardInterrupt (finally unwinds for
   BaseException), so DB-3's options inherit a clean resource story
   and are purely about contract.
6. **test_pkg_a_tiebreak.py lives under tests/properties/, not tests/**
   (the wave-3 charter's validation list said "package quick sets" —
   first validation run collected nothing because of the wrong path;
   re-run green).
7. **Backticks in shell-quoted commit messages get command-substituted**
   — the first fix3-docs commit message lost its `proc sort nodupkey`
   quote to bash; repaired by amend with a message file. Recorded so
   the next arbiter writes -F files from the start.

## Branch inventory

- `inv/0f462p/arb3-findings` (this branch): merges of fix3-e1 /
  fix3-c3 / fix3-docs, the OC-3 pin + oracle update, finding docs
  (E-1, E-2, C-1, C-3, D2, E-3-E4), witness files, manifest updates,
  and this wave-3 section.
- `inv/0f462p/fix3-e1`: red 323b231 / green 0a26573 (E-1 + E-2).
- `inv/0f462p/fix3-c3`: red 26ddf87 / green 1a810a2 (C-3 floor).
- `inv/0f462p/fix3-docs`: b0be77e (A-3 verbatim), 34f84e7 (D2 header),
  dff222c (C-1 comment) — comment-only commits, zero executable
  changes verified by diff.
- Decision briefs DB-2..DB-5 above are verbatim-ready; DB-1 (wave 1)
  stands answered by the operator's B2 ruling.

## Closing records pointer (2026-11-02)

B and D pass-2 final batteries are recorded in the manifest as
pkg-b-pass2-* and pkg-d-pass2-* (B from B.md pass records 1-6 at
`inv/0f462p/b-final` head 4891dfa, integrated into base 3709ab2; D from
D.md run records at cc4945d on `inv/0f462p/d-final-check`, battery
recorded at e5d4dd4 — that branch is NOT merged at recording time and
independent review is pending, which the records state explicitly).
Stale pass-1 strict-xfail B2 witness references in this file and the
manifest were repaired to the current positive parity pins (see the
witness repair note in the B2 outcome section). Narrative and the full
record inventory: [CLOSING-RECORDS.md](CLOSING-RECORDS.md).

**Post-integration update (2026-11-02, same day):** the parent
independently reviewed D final e5d4dd4 with verdict **APPROVE** and
merged it into discovery at 5d0ac0a; B final 4891dfa had been reviewed
with its M1 doc-accuracy repair applied; the campaign's last three mypy
errors were fixed by 14a54f1 (investigation/packages/TYPES-FINAL.md).
The "NOT merged / review pending" phrasing above and in the pass
records is historical (accurate at recording time). The D approval's
limits are recorded in the manifest's D package entry. Final validation
subsequently completed at discovery 31846dc with known baseline
residue (properties: 424 passed / 13 skipped / 1 xfailed, exit 0;
original suites: 373 passed / 2 failed / 3 skipped — the 2 failures
exactly the pre-existing baseline spill-attribution OOM tests; the full
state lives in [FINAL-VALIDATION.md](FINAL-VALIDATION.md)).
