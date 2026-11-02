# Builder events — the shared synthetic builder's event vacuity trap

Worker: builder-events (`inv/0f462p/builder-events`, base ca93404).
Scope: shared infrastructure only — `tests/properties/synth.py`, the
harness self-tests, and a new guard suite. No production (`src/`)
changes. All work private to this campaign.

## The trap

`build_study()` used to produce cohorts whose `event_care_settings` was
empty. The pipeline materialises `cfg_care_setting` from that field and
nothing else (`pipeline.register_config`), and the event extraction
INNER JOINs that table on (cohortgrp, code) — `30_exposure.sql`,
`event_claims`. The real study loader never has this problem because it
expands EVERY event code's care setting, and an absent
CARESETTINGPRINCIPAL expands to the all-settings wildcard
`('**', '*')` (`config.parse_care_setting` → `CARE_SETTING_ALL`).

Consequence: every study built by `synth.build_study()` silently lost
EVERY outcome claim. `event_claims`, `first_event`, the follow-up
washout and all event columns of `cohort_final` were empty — no error,
no warning. The hand fixture's docstring said P1 had "one event"; the
event existed only in the docstring.

How the campaign absorbed it (all three workarounds are now redundant
but harmless — they re-add rows the builder already carries, and
`dataclasses.replace` with identical tuples is a no-op):

* Package C: `oracles/c_glue.py::cohort_a` builds its own cohorts and
  sets `event_care_settings=(c, "**", "*")` per event code.
* Package D: `oracles/d_fixtures.py::wildcard_events`/`make_cohort`
  apply the same expansion through `replace`.
* Package F: `test_pkg_f_meta_helpers.py::wildcard_study` rewrites any
  builder-built study before running it.
* Package E (documented avoidance): `test_pkg_e_outputs.py` asserts
  row/schema preservation only on outputs known to be populated from
  exposure claims, "never on event-dependent tables".

The harness self-test `test_harness_hand_fixture_matches_hand_calculation`
asserted episodes, denominators and enveloping — but never the event the
fixture exists to demonstrate, so the vacuity survived every green run.

## The fix

`tests/properties/synth.py::build_study` now emits, per cohort:

```python
event_care_settings=tuple((c, *CARE_SETTING_ALL) for c in event_codes)
```

which is exactly what loading the same codes from an input file
produces. Builder studies and loader studies are now interchangeable
with respect to event extraction.

## The guard suite (new): `tests/properties/test_property_builder_events.py`

1. **Exact row representation vs an independently authored loader
   input.** The test authors a raw study payload (INPUTS.md row shapes,
   `fupcriteria: "DEF"` event routing, NO caresettingprincipal) and
   loads it through `config.load_study_dict`. Per cohort, the builder's
   `event_care_settings` tuples must equal the loader's, and the rows
   `register_config` materialises into `cfg_care_setting` must be
   identical for both studies — the representation the pipeline
   actually joins against. A literal pin asserts every row is
   `(cohortgrp, code, "**", "*")`, one per event code per cohort.
2. **Property over arbitrary pools** (hypothesis): any subset of
   `EXPOSURE_CODE_POOL` × `EVENT_CODE_POOL` builds a study whose
   care-setting rows equal the loader expansion; `@example` pins the
   empty-events and single-event/single-cohort edges.
3. **Hand fixture through the shipped stages AND the full pipeline.**
   Hand calculation, on paper:
   * P1's episode: fills 2014-03-10 + 2014-04-09, 30 days each;
     stockpiled supply runs 2014-03-10..2014-05-08 → at-risk window
     [2014-03-10, 2014-05-08] (no FUT/death/dose; `exp_ext_per` 0).
   * Event extraction is query-period-bounded, NOT enrollment-bounded,
     so both P1 diagnoses become event claims in BOTH cohorts (the
     builder gives every cohort the same event codes): 4 rows,
     (cohort_a|cohort_b, 1, 2014-05-03 D00002 | 2014-06-01 D00001,
     codetype "09").
   * Only 2014-05-03 falls inside the at-risk window → `first_event` is
     exactly (cohort_a, 1, 2014-03-10, 2014-05-03, numevents 1);
     the 2014-06-01 claim never attributes.
   * `cohort_final`: P1 feventdt 2014-05-03, event 1, flag "Y",
     numevents 1, followuptime 55 (index → event inclusive),
     exit_reason "event"; P4 (cohort_b) has no events: event 0, flag
     "N", exit_reason "exposure_end", followuptime 60 (supply 30+30
     same-day summed → episode end 2014-05-13).
4. **Calibration — the guard detects the trap.** The same fixture with
   the care settings stripped (reproducing the pre-fix builder via
   `dataclasses.replace`) must produce ZERO event claims, zero
   attributed events, and flat event columns. Guard 3 is therefore not
   vacuous: it fails precisely on the defect it exists for.

## Harness self-test changes

`test_harness_stages.py::test_harness_hand_fixture_matches_hand_calculation`
now also asserts the event layer it previously ignored: `event_claims`
has exactly 4 rows and `first_event` is exactly the hand-computed row
(stage "follow-up + events" runs before "attrition + denominators", so
both tables exist at the existing `run_through` point). Its docstring
now states the exact event facts. No other expectations were touched.

## Expectation audit (what changed and why — no blind updates)

* `numevents` on an EVENT-FREE episode is NULL, not 0, through the FULL
  pipeline: `62_mstr_wide.sql` applies `nullif(f.numevents, 0)` —
  documented SAS parity ("SAS leaves numevents blank, not 0"). The
  stage harness stops before that stage, so it observes 0; the full
  pipeline observes NULL. Both expectations are written down where they
  are asserted.
* The first two draft runs of the loader-input helper failed on payload
  shape (`qrp_parameters` row lists read only their FIRST row; the flat
  dict shape belongs under `qrp_parameters_scalars`). Loader semantics,
  not builder semantics — documented in the helper's comment.
* All other existing expectations held unchanged: episodes/masterlist,
  denominators, enveloping, digests across repeated runs.

## Verification (actual results, nothing masked)

* `uv run pytest tests/properties/test_property_builder_events.py -q`
  → 5 passed.
* `uv run pytest tests/properties/test_harness_stages.py
  tests/properties/test_harness_pipeline.py tests/properties/test_ac1_infrastructure.py -q`
  → 17 passed.
* Full `tests/properties` suite, serial, hypothesis profile `quick`:
  see the run note appended below (run AFTER the fix; failures listed
  as observed, none masked).

## Changed behaviour for consumers of the shared builder

* Any test that runs `build_study()`/`study_for()`/`hand_fixture()`
  studies through event-dependent stages now sees real events where it
  previously saw none. Concretely: event-dependent outputs
  (`event_claims`, `washout_claims`, `episode_hoi`, `first_event`,
  `cohort_final` event columns, `92_cidadenom` event counts) can be
  nonempty from builder studies for the first time.
* Packages C/D/F keep their helpers; they are idempotent no-ops now.
  Package E's avoidance note is obsolete but its assertions were
  already event-conservative, so nothing breaks.
* Fixture digests that include event-dependent tables would change;
  the harness self-tests digest only `cdm_dispensing`,
  `exposure_claims`, `ptsmasterlist` (pre-event tables), which is why
  they still agree.

## Integration risks

1. The D worker's oracle (`oracles/d_denom.py`) reads
   `cohort.event_care_settings`; where D tests pass builder studies,
   the model and the pipeline now both see the wildcards — consistent,
   but D should re-run its suite on its own worktree after merging this
   (its committed tests pass here at ca93404 + this fix; see run note).
2. `hand_fixture()`'s study now attributes an event to P1's episode.
   Any FUTURE test copied from E's "avoid event-dependent tables"
   pattern is free to assert events, but a test that (unknown to its
   author) relied on empty event columns via builder studies would
   surface a real diff — that surface is the point of the fix.
3. `cfg_care_setting` rows exist for builder studies now; a test
   asserting an exact `cfg_care_setting` row set for a builder study
   with `()` would fail. None exists in the tree at ca93404 (grep:
   only production-side unit tests register their own config rows).
4. No production file changed; loader and SQL semantics are untouched.
   The fix cannot alter any real-study result — only what TEST
   fixtures mean.

## Run note (full properties, serial, profile quick)

Command: `uv run python -m pytest tests/properties -q
--hypothesis-profile=quick` (serial, harness engines threads=1),
with this fix in place:

* **356 passed, 1 failed, 6 skipped, 1 xfailed** in ~5m15s.
* The ONE failure is `test_pkg_d_optional_stages.py::
  test_optional_stage_matches_reference[attrition_person_time]` —
  a package-D-owned test. It fails IDENTICALLY at base ca93404
  (verified by extracting the base tree with `git archive` and running
  it there: same assertion, same diff
  `('', '', 0, 'cohort_a', 11.0, 0) != ('', '', 0, 'cohort_a', 11.0, 2)`),
  so it is PRE-EXISTING and unrelated to this fix. It compares the
  production `attrition` table against D's committed reference model;
  resolving it belongs to the D worker (its own worktree), and its
  test file was not touched here.

## Post-run incident note (no shared state harmed)

While verifying the pre-existence of the D failure, a `git stash pop`
in this worktree popped a PRE-EXISTING foreign stash (`WIP on Testing:
2a5ef9c Remove stray root copies...`) that carried changes to
`src/qrp/sql/00_macros.sql` and `10_normalize.sql`. Those two files
were immediately restored to HEAD (`git checkout HEAD --`) and the
stash entry itself remains intact in `git stash list`. This worktree
carries ONLY this task's changes; no other worker's files were
modified.
