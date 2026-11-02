# FINDING-REPLAY: AC.9 red/green regression replay for the fixed findings

**Status:** complete
**Branch:** `inv/0f462p/finding-replay` (from discovery `ca93404`)
**Privacy:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
**Evidence:** `investigation/finding-replay-records.json` (machine record) +
`investigation/replays/*.log` (full per-revision pytest outputs)
**Tool:** `investigation/finding_regression_replay.py` (pure model:
`investigation/finding_core.py`)
**Always-on validator:** `tests/properties/test_finding_evidence_complete.py`
(42 tests; cheap: file/git/`pytest --collect-only` only)
**Opt-in replay wrapper:** `tests/properties/test_finding_regression_replay.py`
(skip-only unless selected — a skip is never success evidence)

## What this package proves

Every finding the campaign classified as **fixed** has a standalone
regression test committed on its fix branch, and that regression
FAILS at the recorded red revision for the recorded reason and PASSES
at every later recorded revision (green, and final where the branch
carries a follow-up commit). All revisions are pinned by full 40-hex
immutable SHAs resolved from the finding documents and fix branches
and verified by direct parent checks in git before any replay runs.
The patches are relative to discovery's implementation (`c3e60db`),
not to `main`; nothing here implies the fixes apply independently to
`main`.

**Executed:** 2026-11-02T21:08:04Z, single CLI run, strictly
sequential, from the committed replay tool (refactor-identical rerun;
first run 2026-11-02T21:02:29Z proved the same red/green pairs).

**Immutable record count:** 7 replay groups covering all 8 fixed
findings (A-1, A-2, A-4, E-1, E-2, C-3, F1, F2); 7 non-fixed findings
documented WITHOUT fix evidence (below). 16 per-revision pytest runs:
8 red (all failed for the recorded reason), 8 green/final (all
passed, zero skips).

## Fix-branch minimality

Every red commit adds ONLY its regression test file; every
green/final commit touches at least one `src/` production path; each
branch stays within its recorded commit allowance (2 commits for the
minimal branches; 3 for A-4). The E-1/E-2 pairing is the one
declared exception: both frontend leaks share branch `inv/0f462p/fix3-e1`
and test file `tests/test_regression_e1.py`, justified by the
identical try/finally fix shape; its green commit additionally tweaks
the shared regression by two lines (declared in the record's
minimality allowance and enforced by the validator).

A-4 uses the clean final branch `inv/0f462p/fix3-a4-complete` with red
`8bc521e` — NOT the obsolete diagnosis-only `inv/0f462p/fix2-a4`
(preserved, unreferenced by this replay).

## Executed red/green evidence

Base for every branch: `c3e60db5d08343b1cebbb90d234602f09cb0d70d`.

| group | findings | branch | red sha → result | green sha → result | final sha → result |
|---|---|---|---|---|---|
| a1 | A-1 | `inv/0f462p/fix-a1` | `f971710` red: exit 1, `1 failed, 1 passed` (`DID NOT RAISE`), 0.3s | `5bbc4ff` exit 0, `2 passed`, 0.3s | — (green == head) |
| a2 | A-2 | `inv/0f462p/fix-a2` | `1577f83` red: exit 1, `1 failed, 1 passed` (Binder `Referenced column "rxamt" not found`), 0.9s | `564eb7a` exit 0, `2 passed`, 1.0s | — (green == head) |
| a4 | A-4 | `inv/0f462p/fix3-a4-complete` | `8bc521e` red: exit 1, `21 failed, 3 passed` (Binder family on omitted optional columns), 11.4s | `a6a74e4` exit 0, `24 passed`, 16.0s | `f1dca19` exit 0, `24 passed`, 15.9s |
| e1e2 | E-1, E-2 | `inv/0f462p/fix3-e1` | `323b231` red: exit 1, `2 failed` (BOTH recorded assertions matched: TUI never released the engine; CLI closed engine 0 times), 10.7s | `0a26573` exit 0, `2 passed`, 2.9s | — (green == head) |
| c3 | C-3 | `inv/0f462p/fix3-c3` | `26ddf87` red: exit 1, `1 failed` (`person_days is not floored at 0`), 0.7s | `1a810a2` exit 0, `1 passed`, 0.7s | — (green == head) |
| f1 | F1 | `inv/0f462p/fix2-f1` | `0bcb1a8` red: exit 1, `1 failed, 1 passed` (`assert (2, None) == (0, 2)` — forward-filled members at the first empty counted step), 1.0s | `3001086` exit 0, `2 passed`, 1.0s | — (green == head) |
| f2 | F2 | `inv/0f462p/fix2-f2` | `0e7eeee` red: exit 1, `1 failed` (`denominator must split the window at the age threshold`), 0.8s | `37e3d89` exit 0, `1 passed`, 0.8s | — (green == head) |

Full outputs: `investigation/replays/<group>-<role>.log` (paths and
durations are embedded per revision in `finding-replay-records.json`
`execution` blocks). Machine verdicts of the recorded run:
`investigation/replays/verdicts.json`; human progress:
`investigation/replays/cli-progress.log`.

Red acceptance is three-layered: nonzero exit, pytest summary showing
at least one real failed test, and EVERY recorded `expected_pattern`
matched in the output. A dependency/import/collection failure
(ImportError/ModuleNotFoundError/INTERNALERROR/collection error) is
refused as the red reason even if a pattern somehow matched. Green
acceptance refuses skip-only exits: at least one real pass, zero
skips, zero failures — so the e1e2 `/tmp/qrp_data/100k` fixture
dependency cannot masquerade as success. That dependency is recorded
in the record (`fixture_dependency`): synthetic input built by
`tools/gen_synthetic.py --out /tmp/qrp_data/100k` (seed 42, README
recipe); the module `pytest.skip()`s without it and the replay then
fails loudly. No clinical dataset is accessed anywhere in this
package.

## Replay mechanics

Per group: resolve + verify ancestry (`base` is the direct parent of
`red`; `red` of `green`; `green` of `final`) → create a DISPOSABLE
detached worktree `fr-<group>` (only if that path does not already
exist; never touched otherwise) → fresh `uv` venv from `.[dev]` → run
red → checkout next revision, DELETE and rebuild the venv (fresh
isolated environment per revision) → run green/final → remove the
worktree in a `finally` block, success or failure. Nothing outside
`fr-<group>` is ever written; preexisting worktrees, branches, and
stashes are untouched (verified: 31 worktrees, 1 stash, branch set
unchanged before/after; zero `fr-*` leftovers).

There is NO blanket campaign-branch guard in this tool (unlike the
AC.8 mutation replay): it may run from any checkout, including
discovery, because it only ever writes its own uniquely-named
disposable worktrees and collides hard on existing paths.

Timeouts: git 120s, venv bootstrap 600s, pytest 900s per revision;
exit status 0 (all proved), 1 (some step failed, tails printed), 2
(invalid records). Groups run strictly sequentially; the replay adds
no parallelism. The regressions use the production DuckDB thread
default (they were not rewritten for this validation, matching the
A-4 validation precedent); DuckDB `threads=1` is an Engine-level
setting that the standalone regressions do not set, so "threads=1
where feasible" is satisfied at the harness level (one group, one
pytest process at a time), not inside the tests.

Hypothesis is not needed by any red regression (all are standalone
deterministic tests); the replay env is the campaign dev extra
(`.[dev]`: pytest, pandas, textual, hypothesis, mypy, ruff) — textual
is required by the E-1/E-2 regression.

## How to run

    # cheap, always-on evidence validation (normal suite includes it)
    python -m pytest tests/properties/test_finding_evidence_complete.py

    # expensive red/green replay (never part of normal runs)
    python investigation/finding_regression_replay.py            # all groups
    python investigation/finding_regression_replay.py --group a4 --json
    python -m pytest tests/properties/test_finding_regression_replay.py -m mutation_replay
    QRP_FINDING_REPLAY=1 python -m pytest \
        tests/properties/test_finding_regression_replay.py

The wrapper reuses the registered `mutation_replay` marker (no new
marker registration, no shared-source edits) or the explicit env
opt-in; unselected runs skip with a reason naming both commands.

## Findings WITHOUT fix evidence (documented separately on purpose)

These are NOT counted as fix evidence anywhere in the records or in
this report; the validator enforces the separation (fixed vs
non-fixed sets must match the manifest exactly, with no overlap):

- **B2 — parity ruling.** Operator-ruled inherited defect: one
  dispensing inside overlapping enrollment episodes is stockpiled
  twice, bug-for-bug from the SAS original (ms_create_enrollment_spans
  → ms_episoderec2 → ms_shaveoutside → ms_stockpiling chain). SAS
  parity kept, no fix branch; a validated prototype fix
  (`inv/0f462p/fix-b2`, red `f4d2e6a` / green `c87dc0b`) was REMOVED
  per the ruling (results remain in manifest passes
  `arb-red-b2-1`/`arb-green-b2-1`). Evidence: strict-xfail pin
  `test_enrollment_overlapping_raw_spans_known_divergence` + probe
  `investigation/probes/b_overlap_probe.py`.
- **A-3 — comment-only.** demographics full-tie comment corrected
  (`fix3-docs` `b0be77e`); behavior kept (SAS parity, strictly more
  deterministic than SAS). Zero executable changes → no red/green
  pair exists or applies.
- **D2 — comment-only.** `92_cidadenom.sql` header corrected
  (`fix3-docs` `34f84e7`); the comment contradicted the stage's own
  `WHERE criteria='EXC'` and the SAS original. Zero executable
  changes.
- **C-1 — pending (DB-5).** Silent divergence from SAS for
  maxcumdose+cumdoseper studies whose censor pushes an episode below
  a length criterion; magnitude unmeasured; only the false comment
  half was corrected. Behavior change awaits the operator.
- **E-3 — pending (DB-3).** CLI SIGINT mid-run contract unspecified
  (exit −2, JSONL without terminal record, no manifest); no fix by
  charter. Resource half clean since the E-2 fix.
- **E-4 — pending (DB-4).** Direct `pipeline.run()` failures emit no
  RunFinished; ownership of the failure terminal event unspecified;
  safe direction asserted (nothing claims ok on failure); no fix by
  charter.
- **D1 — pending (DB-2).** CIDA denominators omit SAS's
  INCLUSION-subcondition window restriction and dose restriction
  under `excl_incl=Y` (parity gap confirmed against
  ms_cidadenom.sas:146, 413-417, 265/359). Behavior change awaits the
  operator.

## Remaining issues / limits

1. **Zero-defect case not applicable:** the campaign HAS confirmed
   defects; the records are non-empty and each carries execution
   evidence. The AC.9 zero-defect wording is moot here.
2. **Red-reason specificity depends on the recorded patterns.** They
   are anchored on assertion text or DuckDB Binder messages observed
   in these runs and in the finding documents. A future engine change
   that rewords an assertion will fail the pattern match loudly —
   that is the intended failure mode (update the record deliberately,
   never silently).
3. **The replay mutates nothing it does not own, but it does require
   `uv` on PATH and disk headroom for one worktree + venv at a time
   (~300 MB, removed immediately).**
4. **F2's green is the amended head** `37e3d89` (deterministic
   offset-join rewrite after a lead()-tie nondeterminism was proven);
   the superseded intermediate green `80208b1` is noted in the record
   but not replayed.
5. **Unproven beyond these revisions:** replay proves red/green at
   the recorded commits only; it is not a claim that the fixes hold
   for all inputs (that is the property suites' job, already recorded
   in the campaign manifest).
