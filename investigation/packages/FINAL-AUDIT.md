# FINAL-AUDIT — independent acceptance audit and AC.8 mutation replay

- **Auditor:** independent final-audit subagent (not a reviewer of its own oracle/fix; no shared
  source/test/manifest edits made — proposed repairs listed, ownership unchanged).
- **Audited revision:** `d5fb14b` (branch `inv/0f462p/final-audit`, ancestry rooted in discovery
  `c3e60db`; worktree clean before audit).
- **Date:** 2026-11-02 (local). **Plan:** sessions `plan-001.md` (AC.1–AC.9).
- **Scope honored:** one heavy worker (this audit), no full-suite run, no push/PR/issues, no SAS
  execution, no clinical data, no services, no stash/apply/drop, no clean/reset, no preexisting
  worktrees touched. Replay cleanup limited to its own disposable `replay-*` worktrees.

## Verdict summary

Campaign machinery is **verified, not merely reported**: AC.8 mutation replay was independently
re-executed (6/6 red→green, real exits captured, zero mutations left), the AC.9 finding-replay
validator and manifest validators pass at HEAD, the recorded "42 passed, 1 xfailed" parent C-suite
claim reproduces exactly, the B2 open-finding witness reproduces at HEAD, and all seven fix
branches satisfy minimal-fix boundaries. Three record-drift gaps (one MEDIUM, two LOW) and two
integration-state completion limits are classified below. No HIGH-severity defect found in the
machinery or evidence chain.

## 1. AC.8 mutation replay — independently executed

`investigation/replay_mutation.py` was inspected for safety **before** execution: branch guard
(`inv/0f462p/*` only), fail-fast record validation, diff-path containment under the repo root,
command restriction to the replay venv interpreter, disposable detached `replay-<id>` worktrees
removed in `finally` on every path, and red acceptance requiring nonzero exit **plus** a recorded
assertion-level pattern. All six calibration patches were read: every mutation targets a
production path (`sqlsplit.py`, `20_enrollment.sql`, `60_followup.sql`, `pipeline.py` ×2,
`tools/parity_compare.py`); none is test-only.

Run: sequential, single invocation, `--json` verdicts captured. Result per record:

| record | red exit | red pattern matched | green exit | worktree removed | red evidence (tail) |
|---|---|---|---|---|---|
| a-1 | 1 | yes | 0 | yes | `AssertionError` at `test_pkg_a_sqlsplit.py:105` (`assert [] == [None]` family) |
| b-1 | 1 | yes | 0 | yes | `AssertionError: enrollment_spans: SQL returned 4 rows, model 3` |
| c-1 | 1 | yes | 0 | yes | `cohort_final membership differs from the model` (assert) |
| e-1 | 1 | yes | 0 | yes | `Failed: DID NOT RAISE RuntimeError` + two FAILED lines |
| f-1 | 1 | yes | 0 | yes | `duplicate_row on stockpiled exited 0` (assert) |
| d-1-pass2-20261102 | 1 | yes | 0 | yes | FAILED `test_cohorts_together_equal_separate[wash]` |

- Script exit: **0**. All six reds are genuine assertion failures (pytest exit 1 + `FAILED`/`AssertionError`
  markers); **no collection errors (exit 2), usage errors (exit 4), or skip-masked greens were observed**.
- After replay: `git worktree list` contains no `replay-*` entries (the only "replay" match is the
  preexisting `inv/0f462p/finding-replay` branch worktree — untouched); current worktree tracked
  state clean; no mutation applied anywhere.
- Evidence: `investigation/audit-replays/replay-verdicts.json`, `replay-run.log` (committed with
  this report).

## 2. Machinery claims re-verified at HEAD (not trusted from reports)

- **Validators (always-on):** `test_campaign_manifest_complete`, `test_finding_evidence_complete`,
  `test_campaign_mutation_replay`, `test_finding_regression_replay` → **52 passed, 13 skipped**;
  the 13 skips are the deliberate opt-in replay executions (documented skip messages:
  `-m mutation_replay` / `QRP_FINDING_REPLAY=1`), not missing coverage. Exit 0.
- **"parent42pass" claim:** located at `investigation/packages/C.md:182` (quick C set +
  `test_harness_stages.py`). Re-ran the exact recorded command at d5fb14b: **42 passed, 1 xfailed,
  26.6s** — reproduced.
- **AC.9 finding-replay evidence:** `investigation/replays/verdicts.json` — 7 groups, all `ok`,
  all worktrees removed; committed per-revision logs present; spot-checked `a1-red.log` shows a
  genuine `FAILED ... 1 failed, 1 passed`. FINDING-REPLAY.md's three-layer red acceptance
  (nonzero exit + real failed test + every expected pattern; import/collection failures refused)
  and skip-refusing green acceptance match the code paths.
- **B2 open finding:** `investigation/probes/b_overlap_probe.py` re-run at HEAD → exit 0,
  double-count reproduced (one 30-day fill → stockpiled rows supply 16 and 46,
  numdispensing=2). The inherited-defect behavior is still present, as the ruling records.
- **B suite at HEAD:** enrollment/stockpile/index/episodes quick → **9 passed** (green; the B2 pin
  is a passing characterization — see finding AUDIT-1 below).
- **Harness fidelity/reachability:** `tests/properties/harness.py` imports `SQL_DIR`/`STAGES` from
  production `qrp.engine`, reads the shipped scripts via `SQL_DIR / script`, executes in production
  stage order with derived stage closure — all **25 shipped SQL files** reachable; this is
  shipped-SQL execution, not copied snippets. Independent day-enumeration/recurrence oracles do
  not import production SQL logic (only `StudyConfig`/`CohortConfig` and the `_enr_cfg_id`
  key helper itself, which package D deliberately tests as the sharing contract).
- **Nonvacuity:** D denominators carries explicit anti-vacuity events (`member-counts-nonzero`,
  `member-days-nonzero`, `windows`, `washout-shave-hit`, `exclusion-hit`, `death-clipping`,
  `shaving-split-a-window`, `output-nonempty`); 24 package test modules use `event()`; C recorded
  statistics show boundary/tie/invalid rates. Optional-stages module currently emits only a
  per-case `-ran` event — the anti-vacuity/oracle work is **in flight by the D final worker** and
  was not duplicated by this audit.

## 3. Findings (severity-classified)

### MEDIUM — AUDIT-1: B2 evidence records name a witness test that no longer exists at HEAD
The B2 finding doc (`investigation/findings/B2-overlapping-enrollment-double-count.md` lines
36–41, 130–145), the manifest (`findings[B2].witness`, `packages[B].notes`) and the wave-1
arbitration ruling text all cite the strict-xfail pin
`tests/properties/test_pkg_b_enrollment.py::test_enrollment_overlapping_raw_spans_known_divergence`
("remains … as the pin"; repro commands recorded as "→ xfailed"). At d5fb14b that test does not
exist: merged commit `88edf91` replaced it with the passing characterization
`test_enrollment_overlapping_raw_spans_parity` (plus `test_stockpile_fanout_parity_on_overlapping_spans`)
and removed the strict xfail. The behavioral pin survives and is arguably stronger (positive
assertion of the double-count), and the probe reproduces — but every recorded B2 repro command
that selects the old node now fails (pytest exit 4, node not found), and the ruling's witness
sentence is factually stale. Related drift in the same records: `packages[B].status` is
`pass1-complete` / `head 028ebfb` while `B.md:336` already carries the pass-2 correction section.
**Proposed repair (final-records worker's lane; not made by this audit):** update the three
records to name the current characterization tests and the probe, correct `packages[B]`
status/head, and mark the pass-2 evidence location (unmerged branch, see AUDIT-2).

### LOW — AUDIT-2: B pass-2 evidence not integrated; manifest has no B pass-2 pass record
`d63a8fb` ("Record B pass2 coverage and preserve unresolved chaining witness") and `0f76539`
(`B-independent-review.md`) are unmerged. The integrated manifest's `passes[]` contains no B
pass-2 record; plan AC.8 requires two recorded passes per package. This is an integration-state
limit, not an evidence defect — the tie nondeterminism (see §4) is why both branches stopped.
See completion limits.

### LOW — AUDIT-3: stale docstring reference in `test_pkg_b_enrollment.py`
Module docstring still points to `test_enrollment_overlapping_raw_spans_known_divergence` for the
"documented separately" divergence. Cosmetic but misleads navigators; fix alongside AUDIT-1.

### No HIGH findings
No machinery falsehoods detected: replay results, validator claims, red/green logs, fix-branch
minimality, and privacy markers all checked out under independent re-execution or direct
inspection.

## 4. Adversarial second opinion — B tie-oracle scope (requested; no changes made to B)

**Question:** was the suppressed operator decision prompt about B overlap-tie oracle scope
actually required, and does the accepted B2 doc-only ruling permit a complete sound resolution
without new policy?

**Answer: an operator decision is not required for campaign completion; a sound, complete,
policy-free resolution exists within the accepted B2 ruling.** Reasoning:

1. The escalated questions (is overlap outside the contract with mandatory upstream rejection?
   which span is authoritative? what deterministic peer order?) are **production-contract
   policy** questions. No production change is proposed or permitted: the B2 ruling already
   forbids diverging production fixes absent operator authorization, and `b-tie-review` made no
   production change. Test-oracle design is worker authority under the plan (workers own oracle
   repair; only *consequential* contract ambiguities go to the operator).
2. Neither unmerged branch is a sound end state as-is:
   - `d63a8fb` models *observed* span-major ordering with an exact `@example` pin — the review's
     own probes showed thread/optimizer-sensitive decompositions ((3,6) at threads 1;
     (3,3,3)/(6,3) at threads 4), so the pin is flaky. Correctly unmerged.
   - `0f76539` deliberately keeps the broad generated comparison **permanently red**. Keeping a
     standing red test at closure conflicts with an all-green, all-failures-classified suite;
     B2 is classified, but the *tie-order* failure is a distinct, unclassified standing failure.
3. The sound resolution, needing no new policy:
   - **Supported domain** (nonoverlapping spans): exact sequential-oracle comparison — already
     implemented on `b-tie-review` (`test_nonoverlap_enrollment_matches_occupied_days`,
     exhaustive day-set test).
   - **Overlap domain** (outside the supported contract per B2): replace the exact-tie assertion
     with order-agnostic assertions that hold under *every* legal engine ordering — upstream
     multiset equality (enrollment_spans/stockpiled/located vs model), conservation invariants
     (total supply preserved; the numdispensing inflation witness), optionally membership in the
     review's enumerated allowed-set of run decompositions. This pins the invariant surface
     without encoding any arbitrary engine order and conceals no decision — the report's own
     84-words analysis is exactly this characterization.
   - **Documentation:** add the tie-ordering ambiguity as an addendum to the B2 defect doc
     (doc-only, same accepted class as the A-3/D2 comment corrections): episode peer ordering
     under fan-out is engine-defined; SAS `BY` keys do not pin it; any production ordering
     policy is a **precondition for a future fix and remains operator-gated**.
4. What genuinely stays operator-gated (re-issue as FYI, non-blocking): verifying that upstream
   Sentinel QA actually rejects overlapping spans (external system, unverifiable in this
   campaign — correctly recorded as unverified), and any future production ordering/rejection
   policy. Neither blocks audit or campaign closure.

**Bottom line:** the suppressed prompt should not block; it can be re-issued as a non-blocking
FYI. Do not merge `d63a8fb` (flaky pin) or `0f76539`'s standing red as-is; port the invariant-based
resolution instead.

## 5. DB-2..DB-5 — genuine blockers or classified leads?

All four are **genuine decision items** (every option either changes behavior or amends a
published contract document, i.e. policy), correctly parked verbatim-ready. **None blocks campaign
closure** — AC.9 requires classification, which exists; they are decision-pending leads, not
silent gaps. Fixes for all four are correctly absent from the tree ("DB2-5 not approved").

- **DB-2 (D1, CIDA denominators under `excl_incl=Y`):** the high-stakes one — member-days
  **overstated** for any study with inclusion codes; direction certain (SAS-verified at
  ms_cidadenom.sas:146/413-417/265+359), magnitude unmeasured; option (a) is days-scale feature
  work. Genuine operator decision before any fix; documented lead until then.
- **DB-3 (E-3, SIGINT contract):** runtime/observability contract choice; safe direction asserted
  and tested (nothing claims ok on failure; manifest absence marks incompleteness; resource
  release already clean since the E-2 fix). Clearly classified lead; doc-only option still amends
  LOGGING.md's parity promise, hence operator-gated by convention.
- **DB-4 (E-4, failure-path terminal event ownership):** same class; machine-readable gap
  (JSONL consumer can't distinguish failed from truncated), no honesty violation. Classified lead.
- **DB-5 (C-1, post-truncation criteria re-check):** parity-gap lead with **unmeasured magnitude**
  (no witness study yet); the false comment was corrected (dff222c); behavioral question parked
  pending B2-standard evidence-first work. Correctly not a defect claim yet.

## 6. Minimal-fix boundaries — verified

`git diff --stat c3e60db <branch>` for each fix branch: exactly one regression test file plus
production `src/` paths; `tools/` untouched in all seven; A-4 uses the declared 3-commit
allowance (`fix3-a4-complete`); E-1/E-2 share `fix3-e1` as the one declared exception. Doc-only
commits (`fix3-docs`) carry no executable changes per the arbitration record.

## 7. Privacy and clinical-contract evidence — verified

All 14 finding docs carry `**disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION`;
all test data is synthetic (builders/local fixtures); the SAS tree is referenced read-only
(`investigation/witnesses/c1-dose-censor-recheck-sas-evidence.txt`, B2 citations) with no SAS
execution; no external clinical datasets, services, pushes, PRs, or issues. Findings state
external clinical validation as unproven (e.g. B2 "Sentinel QA expected upstream (unverified)",
F2/C-1 parity-scoped claims). No scope waivers were granted or invented by this audit.

## 8. Exact completion limits (what this audit does NOT certify)

1. **B pass-2 integration** (AUDIT-2): the invariant-based tie resolution (§4) must replace the
   flaky `d63a8fb` pin and the standing red of `0f76539`; then a B pass-2 execution record belongs
   in the manifest. Until then B stands at pass-1 evidence + B.md pass-2 correction section.
2. **Manifest/record reconciliation** (AUDIT-1): owned by the final-records worker; this audit
   deliberately did not edit shared records.
3. **D optional attrition/covariate oracle + anti-vacuity events:** in flight by the D final
   worker; this audit did not duplicate or gate on it.
4. **Full-suite validation:** not run (mandate: one heavy worker, no full-suite run); targeted
   quick-profile runs and both replays only. Full-suite integration remains the orchestrator's
   step; the two pre-existing exempt spill-attribution OOM failures recorded in earlier passes
   are known.
5. **AC.9 zero-defect clause:** not applicable — 8 confirmed fixed findings + 7 replay groups.
6. **External limits (standing, per plan):** SAS parity beyond documented mappings, upstream
   Sentinel QA behavior, and clinical validity remain unverified; finite randomized testing does
   not prove universal correctness.

## 9. Evidence index (committed with this report)

- `investigation/audit-replays/replay-verdicts.json` — machine verdicts, all six records.
- `investigation/audit-replays/replay-run.log` — replay progress + final exit code 0.
- `investigation/audit-replays/validator-rerun.log` — validator + C-suite re-run (87 passed,
  13 opt-in skips, 1 xfailed; then exact-command C re-run 42 passed, 1 xfailed).
- Recorded (pre-existing, re-checked): `investigation/replays/*` (AC.9 logs + verdicts.json),
  `investigation/calibrations/*.patch`, `investigation/witnesses/*`,
  `investigation/packages/ARBITRATION.md`, `investigation/packages/FINDING-REPLAY.md`.

— Auditor sign-off: d5fb14b; replay exit 0; no mutations left; no shared files modified.
