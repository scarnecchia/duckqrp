# EVIDENCE-STRICT — strict AC.8 completion validation

- **Status:** validator implemented; campaign record deliberately NOT complete (B/D pass-2 records outstanding)
- **Branch:** `inv/0f462p/evidence-strict` (worktree `evidence-strict`, from integrated `132fa87`)
- **Date:** 2026-11-02
- **Privacy:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
- **Owned files:** `tests/properties/test_campaign_manifest_complete.py`,
  `tests/properties/test_campaign_mutation_replay.py`,
  `investigation/replay_core.py` (additive only), this document. No manifest
  or other shared file was edited.

## What this closes

The validator-looseness gaps recorded in `FINAL-RECORDS.md`
(§ Validator looseness observations, items 1, 2, 3, 6):

1. **Two recorded passes per package A–F is now machine-enforced.**
   `replay_core.completion_problems()` requires, per package A–F: a pass-1
   and a pass-2 run with result `pass` in `passes[]`; a settled status
   consistent with the recorded passes (a package with pass-2 records may
   not still claim `pass1-complete`; `pending`/`in_progress` never pass);
   one deliberate-mutation calibration record; and the package report
   (`investigation/packages/<P>.md`) referenced by name in a pass-2
   record's `detail` or in the package entry's `notes`. Pass-2 records
   must name a resolvable commit in `revision` and real test files in
   `command`.
2. **Mutation-replay execution evidence is machine-checked.**
   `investigation/audit-replays/replay-verdicts.json` (committed by
   FINAL-AUDIT at `132fa87`) must cover exactly the manifest's calibration
   id set, and every verdict must be genuine: `ok: true`, red exit code 1
   (a real assertion-level failure — exit 0/2/4/5 would be a
   no-failure/collection/usage/empty mask), the recorded failure pattern
   matched, green exit code 0, worktree removed, the full 8-step replay
   sequence ok, and evidence tails carrying a real failure (FAILED /
   AssertionError) and a real recovery (`passed`, no `failed`/` skipped`
   /`no tests ran` markers).
3. **No skips, no collection masking.** The old
   `pytest.skip("still in flight …")` path in
   `test_package_branches_and_statuses` is gone — in-flight packages fail.
   The validator fails closed: an unreadable or malformed manifest raises
   `ManifestUnreadable` in every check, and
   `test_campaign_mutation_replay` now carries a sentinel test
   (`test_manifest_must_be_replayable`) that fails in every run when the
   manifest cannot be trusted, instead of parametrizing zero replay tests
   silently.
4. **Negative coverage for the validators themselves.** Synthetic-manifest
   self-tests in `test_campaign_manifest_complete.py` prove the predicates
   reject absent fields (every required pass-entry field, one at a time),
   fabricated values (`result: "PASS"`, unknown packages, `campaign_pass`
   of 0/3/`"2"`/`True`), masked verdicts (ok=false, wrong exit codes,
   reordered steps, skip-marked green tails), and stale/missing coverage
   (missing, duplicate, and unknown verdict ids).

## Record conventions codified (no new schema required)

- **Pass number:** an entry belongs to pass 2 when its id contains
  `pass2` or it carries an explicit integer `campaign_pass` field (1 or 2;
  the explicit field wins). Everything else is pass 1. The optional
  `campaign_pass` field is the escape hatch for ids that do not fit the
  convention — no backfill of existing entries is required.
- **Evidence reference:** every package A–F must name its report path
  `investigation/packages/<P>.md` in a pass-2 record's `detail` or in its
  `packages[]` entry's `notes` (A already satisfies this via `notes`;
  C/E/F via their pass-2 `detail`s).
- **Machine verdicts:** any NEW calibration record must come with an
  executed verdict appended to `investigation/audit-replays/`
  `replay-verdicts.json` (run `investigation/replay_mutation.py --id <id>`
  and commit the JSON). Coverage must stay exact — verdicts for removed
  records are stale evidence and fail the validator.

## Exact remaining record requirements (parent integration list)

The strict completion test currently fails with precisely these problems:

1. **Package B:** no recorded pass-2 run with result `pass` in
   `passes[]`. Add an entry whose id marks it pass 2 (e.g. id containing
   `pass2`, or explicit `campaign_pass: 2`) with a command naming the B
   property test files, a `revision` naming a resolvable commit, and
   `investigation/packages/B.md` referenced in its `detail`; then set
   `packages[B].status` to `pass2-complete`.
2. **Package D:** same as B, referencing `investigation/packages/D.md`,
   then `packages[D].status` → `pass2-complete`. (Today D's report path is
   also referenced nowhere — the D pass-2 record's `detail` referencing it
   satisfies both problems at once.)
3. **Only if a new calibration record is added** (likely for D pass 2):
   append its genuine machine verdict to
   `investigation/audit-replays/replay-verdicts.json`; the six existing
   verdicts (a-1, b-1, c-1, e-1, f-1, d-1-pass2-20261102) already validate
   clean and must not be disturbed.

Nothing else is missing: verdict coverage is exact, all six verdicts are
genuine, all A–F reports exist and are referenced (D excepted, above), all
pass-2 revisions resolve, and every package has its calibration record.

## Expected current test state (do not "fix" by inventing evidence)

- `test_campaign_record_is_strictly_complete` — **FAILS** with the B/D
  messages above. This is the legitimate standing failure; it goes green
  only when the parent integrates the records.
- `test_calibration_replays_have_committed_genuine_verdicts` — passes.
- All always-on validity tests, the negative self-tests, the fail-closed
  loader tests, and the replay wrapper sentinel — pass.
- The six replay executions in the wrapper remain opt-in skips
  (`-m mutation_replay`), as designed; the audit already executed them 6/6
  with committed verdicts, so no replay was re-run here (cheap validator
  runs only).

## Honesty limits of this validator

It is a cheap file/git validator. It proves the record set is complete,
self-consistent, and carries executed-evidence artifacts; it cannot
re-derive the recorded runs, re-verify log-content authenticity, or assert
the integrated suite is green (the D attrition-oracle divergence remains
executable-red on this branch by decision, owned by the D pass-2 worker).
Those limits are the same ones `FINAL-RECORDS.md` records for the AC.9
validator.
