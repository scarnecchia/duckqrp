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
| B2 | DEFECT — inherited bug-for-bug from the SAS original; parity kept by operator ruling | none (documented; strict-xfail pin kept) | none (prototype validated then removed per ruling) |
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
  finding docs, this file, manifest updates.
- `inv/0f462p/fix-a1`, `inv/0f462p/fix-a2`: shipped fixes, merged.
- `inv/0f462p/fix-b2`: REMOVED per the operator's B2 parity ruling —
  no fix branch for B2. The validated prototype's results (red
  f4d2e6a / green c87dc0b, 287-passed pipeline run) are preserved in
  the manifest passes and summarized in the B2 finding doc's
  not-applied minimal-fix section.
