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
| B2 | SAS parity — NOT a production defect (sane-arithmetic divergence confirmed) | none (no parity-preserving fix exists) | inv/0f462p/fix-b2 built as an OPTIONAL divergence; not merged |
| B1 (question) | RESOLVED by SAS source: SAS sees overlapping spans AND double-counts | — | — (decision brief DB-1 below) |
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

**What the operator is asked to decide (verbatim):**

> On extracts with overlapping raw enrollment rows, qrp-duckdb
> currently reproduces SAS exactly, including counting one dispensing
> twice (numdispensing=2, supplies 16 and 46 for a single 30-day fill)
> whenever its fill date sits in overlapping enrollment episodes. This
> is verified bug-for-bug against the SAS source (ms_episoderec2 /
> ms_shaveoutside / ms_stockpiling chain, citations in
> investigation/findings/B2-overlapping-enrollment-double-count.md).
> Options:
> (a) keep bug-for-bug parity — no code change (current state; the
>     property suite now pins the behavior with the SAS citations);
> (b) merge inv/0f462p/fix-b2 — shave each claim against exactly one
>     containing span (latest enr_end), a deliberate, documented,
>     regression-tested divergence from SAS in the direction of sane
>     arithmetic;
> (c) keep parity but add a data-quality WARNING when a dispensing is
>     shaved against more than one containing span, so affected
>     studies are told their numbers carry SAS's own double-count.
> The arbiter's recommendation is (c) if parity is the governing
> contract, (b) if internal-arithmetic sanity outranks parity for
> messy extracts; (a) is the default and matches SAS either way.

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
   the B2 mechanism.
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

## Branch inventory

- `inv/0f462p/arb-findings` (this branch): revert of the fix-b2 merge,
  SAS-parity witness, A-1/A-2 fix merges, property-suite updates,
  finding docs, this file, manifest updates.
- `inv/0f462p/fix-b2`: OPTIONAL one-containing-span shave divergence
  (not merged; decision brief DB-1).
- `inv/0f462p/fix-a1`, `inv/0f462p/fix-a2`: shipped fixes, merged.
