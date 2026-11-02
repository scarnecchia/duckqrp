# FINAL-RECORDS — campaign record reconciliation against plan-001.md

**Status:** reconciliation complete for the record set; campaign NOT complete
**Branch:** `inv/0f462p/final-records` (from discovery `d5fb14b`)
**Date:** 2026-11-02
**Privacy:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
**Owned files:** `investigation/manifest.json` (updated in place) and this
document. No other file was edited; no production or test code was changed;
no D-owned file was touched.

## What this document is

An honest reconciliation of the campaign manifest and its referenced
evidence against the acceptance criteria in `plan-001.md`. It records
what is proven, what is missing, and what was deliberately not merged.
It does not claim completeness where records do not support it.

Method: git ancestry checks against this branch, reads of every package
report and the AC.9 evidence record, execution of the two cheap
always-on validators, and one targeted single-test run. No heavy suite
was run (the D worker is active separately), no mutation replay was
re-executed, and no validator or test was modified.

## Acceptance-criteria status

| AC | status | basis |
|---|---|---|
| AC.1 | satisfied (recorded) | infra package complete; profiles load; manifest validator green |
| AC.2–AC.7 | satisfied per recorded passes | package reports A–F record the named property families with pass results |
| AC.8 | partially satisfied — gaps listed below | manifest validator green; 6/6 calibrations executed; D second pass incomplete; B second-pass record unmerged |
| AC.9 | satisfied | 7 replay groups over 8 fixed findings, executed red/green evidence, validator green |

### AC.8 — determination and missing evidence

Machine-checked now: `tests/properties/test_campaign_manifest_complete.py`
passes (10 checks) on this branch, covering schema, package branches and
statuses, pass-entry completeness, calibration record schema and
reference resolution, finding documents against the template, witness
path existence, and the ledger tree.

Calibrations: 6 records (`a-1`, `b-1`, `c-1`, `e-1`, `f-1`,
`d-1-pass2-20261102`), one per package A–F. All 6 patch files exist in
`investigation/calibrations/`, all base revisions resolve, all failing
commands name existing test files (validator-enforced). Executed
red→green evidence exists for all 6:

- `a-1`, `b-1`, `c-1`, `e-1`, `f-1` — executed 2026-11-02 in the
  campaign venv; all proved red for the recorded reason, then green
  after revert, worktree removed. The record is prose in
  `investigation/README.md` (Mutation replay section).
- `d-1-pass2-20261102` — executed via `investigation/replay_mutation.py`
  offline by the D worker; verdict JSON at `/tmp/d-pass2-replay.json`
  (present at reconciliation time, not committed); results quoted in the
  manifest calibration record's `red_evidence`/`green_evidence` fields.

Missing / weak evidence, stated plainly:

1. **No committed machine verdicts for the mutation replay.** Unlike
   AC.9 (committed `verdicts.json` + per-revision logs), the AC.8
   replay evidence is README prose plus ephemeral `/tmp` artifacts.
   The opt-in wrapper `test_campaign_mutation_replay.py` has not been
   re-run as a suite over all 6 records on this integrated branch. This
   reconciliation deliberately did not run it (minutes per record with
   fresh venv builds; D worker active).
2. **Two recorded passes per package is not machine-enforced.** The
   validator checks entry completeness, not pass count. Verified by
   reading the reports: A (manifest + `A.md` Pass 2), C (`C.md` Pass 2,
   integrated at `d3b6043`), E (`E.md` Pass2, integrated at `96204bd`),
   F (`F.md` Pass 2, integrated at `6bc4171`) each record a second pass;
   B's second pass is partially merged (below); D's second pass is
   incomplete.
3. **D second pass incomplete.** The D worker is active on
   `inv/0f462p/d-final-check` (`22fad19`, `917e75d`, `af8021a` — none
   merged here). `D.md` records its remaining bounded gaps: mandatory
   nonempty optional-stage fixtures and the gapped-RX piece-per-span
   clip witness were not implemented, and the RX oracle still models
   only the containing enrollment span.
4. **B pass-2 record unmerged** (deliberate; see below).
5. **No deliberate mutation is left integrated**: all records carry
   `reverted: true`, the working tree is clean, and the calibration
   diffs live only under `investigation/calibrations/`.

### AC.9 — determination

Complete. `investigation/finding-replay-records.json` records 7 replay
groups (`a1`, `a2`, `a4`, `e1e2`, `c3`, `f1`, `f2`) covering all 8
fixed findings (A-1, A-2, A-4, E-1, E-2, C-3, F1, F2), each with
immutable 40-hex red/green(/final) SHAs verified by ancestry, branch
minimality allowances, and execution blocks from the actual
2026-11-02T21:08:04Z replay run (16 per-revision pytest runs; 8 red all
failed for the recorded reason, 8 green/final all passed with zero
skips; worktrees removed). Full logs are committed under
`investigation/replays/` with `verdicts.json`. The 7 findings without
fix evidence (B2, A-3, D2, C-1, E-3, E-4, D1) are documented separately
and enforced as non-evidence. The always-on validator
`test_finding_evidence_complete.py` passes on this branch — 42 checks
(12 test functions; 5 parametrized over the 7 groups), executed by this
reconciliation together with the manifest validator: **52 passed**.

## Package status reconciliation (manifest updated accordingly)

| pkg | was | now | evidence |
|---|---|---|---|
| A | pass2-complete | pass2-complete (unchanged) | `A.md` Pass 2; manifest pass records `a-pass2-*` |
| B | pass1-complete | pass1-complete + integrated pass-2 oracle verification | see below |
| C | pass1-complete | pass2-complete | `C.md` Pass 2: discovery 35 passed 1 xfailed; deep 2 passed (2000/property); quick 42 passed 1 xfailed + identical replay — integrated `d3b6043` |
| D | pass1-complete | pass1-complete; pass-2 underway (separate worker) | `D.md`; worker on `inv/0f462p/d-final-check` |
| E | pass1-complete | pass2-complete | `E.md` Pass2: 5 commands, all exit 0 — integrated `96204bd` |
| F | pass1-complete | pass2-complete | `F.md` Pass 2: discovery 74 passed 4 xfailed; deep 48 passed; quick 78 passed 4 xfailed twice — integrated `6bc4171` |

Manifest changes made (append-only; no existing record removed or
rewritten): C/E/F statuses and notes updated with pass-2 records; 11 new
`passes[]` entries (`pkg-c-pass2-*`, `pkg-e-pass2-*`, `pkg-f-pass2-*`)
quoting the report-recorded runs; B and D notes extended; lead L-B3 and
the D attrition oracle divergence added to `follow_up_leads` and
`open_questions`; a `reconciliation` block added pointing here.

## B: what is merged, what is not, and why

Merged (in this branch, via `88edf91`): the pass-2 oracle corrections —
enrollment segmentation rewritten to the arbitrated SAS lag break, the
chaining window's span-major tie order modeled as observed behavior,
the strict B2 xfail replaced by positive parity pins; full quick set
16 passed twice (`B.md`, Pass 2 verification correction).

NOT merged, deliberately:

- `d63a8fb` (inv/0f462p/b-tie-review): the pass-2 coverage record and
  the preserved unresolved chaining witness.
- `0f76539`: the independent episode tie review
  (`B-independent-review.md` +
  `tests/properties/test_pkg_b_independent_review.py`, 294 lines).

Reason (operator decision, honored here without re-litigating): the
overlap-tie behavior is genuinely nondeterministic — lead **L-B3**: the
`50_episodes.sql` chaining window `ORDER BY (adate, expiredt, rxsup)`
is not a total order when the located fan-out emits copies tied on all
three columns, so episode pairing depends on DuckDB's join emission
order (span-major observed on threads=1) — and the proposed oracle
overspecifies that observed tie order. A fix would be a
SAS-parity-sensitive tie-break extension and is flagged for
arbitration, not applied.

## Open decision briefs (unchanged, all unresolved)

- **DB-2 / D1** — CIDA denominators omit SAS's INCLUSION-subcondition
  window restriction and dose restriction under `excl_incl=Y`
  (clinical/parity contract choice).
- **DB-3 / E-3** — CLI SIGINT mid-run: unhandled, exit −2, JSONL
  without terminal record, no manifest (runtime contract choice).
- **DB-4 / E-4** — direct `pipeline.run()` failures emit no RunFinished
  (runtime contract choice; safe direction asserted).
- **DB-5 / C-1** — dose-censor post-truncation re-check: Python filters
  before truncation and does not recheck; SAS checks after (silent
  parity divergence, magnitude unmeasured).
- **B2 policy** — doc-only, inherited bug-for-bug from SAS, QA Suite
  mitigation expected (unverified). No new policy authorized; none
  applied.

## Known red on the integrated branch (verified, not fixed here)

`tests/properties/test_pkg_d_optional_stages.py::test_optional_stage_matches_reference[attrition_person_time]`
fails deterministically at `d5fb14b` (this reconciliation re-ran it:
1 failed in 16.36s). The D optional-stage oracle expects pre-F1
semantics (2 person-time members on an empty counted step); production
and the F1 fix count 0. This is the preserved divergence documented in
`D.md`; reconciling that oracle belongs to the D pass-2 worker. The
full integrated suite is therefore **not green** until that lands.

## Validator looseness observations (report-only; no tests modified)

1. `test_package_branches_and_statuses` enforces only the
   pending-status-plus-passes contradiction. A settled-but-stale status
   (e.g. `pass1-complete` after a merged pass 2) is undetectable —
   exactly the staleness fixed manually in this reconciliation.
2. AC.8's "two recorded passes" and behavioral-coverage requirements
   are not machine-enforced anywhere; detection required reading the
   package reports.
3. Mutation-replay execution evidence has no committed
   machine-verdict requirement, unlike AC.9; the validator cannot
   distinguish executed from prose-recorded evidence.
4. The AC.9 validator checks execution-record fields and log presence;
   it cannot re-verify log content authenticity (acceptable for a
   cheap file/git validator, worth stating).
5. `d-1-pass2-20261102` quotes an ephemeral `/tmp` verdict artifact in
   its record fields; the artifact is not under version control.
6. `test_campaign_mutation_replay._calibration_ids()` parametrizes over
   zero tests if the manifest is momentarily unreadable (documented in
   code; the manifest validator fails loudly for that case in normal
   runs, so the exposure is narrow).

## Evidence inventory verified at reconciliation time

- `investigation/calibrations/`: 6 patches present (a-1, b-1, c-1, d-1,
  e-1, f-1).
- `investigation/replays/`: 16 per-revision AC.9 logs + `verdicts.json`
  + `cli-progress.log` present.
- `investigation/witnesses/`, `investigation/findings/` (14 docs +
  template): present; all witness references resolve (validator-enforced).
- `/tmp/f-pass2-*.log` (F pass-2 raw logs) present but ephemeral —
  flagged here so the loss of `/tmp` is not mistaken for loss of the
  record (the `F.md` tables are the durable record).
- `/tmp/d-pass2-replay.json` present but ephemeral (same caveat).

## Checks performed by this reconciliation

1. Ancestry: `git merge-base --is-ancestor` for d63a8fb, 0f76539,
   d3b6043, 6fa3761, 96204bd, 6bc4171, aa23381, c5154fd, adff2f8,
   d5fb14b, af8021a, 22fad19, 917e75d, 88edf91 against this HEAD.
2. Environment: fresh `uv venv .venv --python 3.13` +
   `uv pip install -e '.[dev]'` in this worktree (standard campaign
   recipe; warm cache).
3. `python -m pytest tests/properties/test_campaign_manifest_complete.py
   tests/properties/test_finding_evidence_complete.py -q` → **52
   passed** (pre-change); manifest validator re-run after the manifest
   edits → **10 passed**.
4. Single targeted run: the D attrition divergence test → failed as
   documented (see above).
5. Reads: plan-001.md; manifest.json (full); finding-replay-records.json
   (full); FINDING-REPLAY.md; A/B/C/D/E/F.md pass sections; README
   mutation-replay section; both validator modules.

## Not claimed

- The campaign is **not** complete: D's second pass is outstanding, B's
  pass-2 record and independent review are unmerged by decision, and
  four decision briefs await the operator.
- No heavy suite, discovery/deep profile run, or mutation replay was
  re-executed here; recorded results were verified for internal
  consistency and evidence presence, not re-derived.
- Fixes are relative to discovery's implementation (`c3e60db`), not to
  `main`; nothing here implies they apply independently to `main`.
- Zero network egress, no push, no SAS execution, no clinical dataset
  access. All work stayed inside this worktree and its `.venv`.
