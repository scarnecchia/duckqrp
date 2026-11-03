# C-3: cohort_final.person_days has no floor and reads negative when
# at_risk_start outruns the episode

- **id:** C-3
- **proposed issue title:** Floor person_days at 0 like followuptime —
  negative person-time is summed into the denominators and inflates
  event rates
- **contract:** followuptime on the same table is floored exactly as
  SAS defines it — `Max(0, Min(EpisodeEndDt,Enr_End,FEventDt) - IndexDt
  - BLACKOUTPER - ATRISKSTART + 1)` (ms_finalizeptsmasterlist.sas:305)
  — and 60_followup.sql reproduces that floor with `greatest(0, ...)`.
  person_days (60_followup.sql:325-326 on base) spans the SAME at-risk
  window from atriskindexdt with NO floor. SAS never produces negative
  person-time anywhere in the Type 2 path: the at-risk span quantity it
  carries is floored (above), episodes with no at-risk day are deleted
  outright (ms_createptsmasterlist.sas:175-176), and there is a
  defensive `if IndexDt > EpisodeEndDt then delete` (:189). There is no
  SAS counterpart column named person_days — it is a documented
  tool-side construct ("This implementation's `denominators` table sums
  exposed person-days", docs/SAS_PARITY.md:349-356) — but the span
  semantics it mirrors are floored in SAS, and a negative person-days
  value is incoherent regardless of parity: zero days at risk is the
  correct reading.
- **trigger:** A cohort sets t2atriskstart (SAS &ATRISKSTART) larger
  than the remaining episode span — e.g. at_risk_start 60 on an episode
  that ends 30 days after the index dispensing. atriskindexdt =
  indexdt + at_risk_start (50_episodes.sql:222-223) lands past
  episodeenddt and span_days goes negative.
- **witness/repro command:**
  `investigation/witnesses/c3-person-days-negative-evidence.txt`;
  `tests/test_regression_c3.py` (inline SCDM parquet, real pipeline,
  no /tmp/qrp_data dependency).
- **expected vs actual:** Expected: person_days 0 (zero days at risk),
  like followuptime on the same row. Actual (base c3e60db): the witness
  row reads atriskindexdt 2014-07-31, episodeenddt 2014-06-30,
  person_days −30 with followuptime 0. Package C had pinned this as
  observed behavior (person_days == −1, open question OC-3).
- **first failing revision:** since c3e60db5d08343b1cebbb90d234602f09cb
  0d70d (campaign base).
- **mechanism:** `span_days(d_start, d_end)` = date_diff + 1
  (00_macros.sql:14-16); with d_start > d_end it is negative. The
  negative rows are not filtered anywhere: 70_outputs.sql:213 sums
  person_days into the denominators table and :216 divides event
  counts by it for rate_per_1000_py — negative person-time shrinks the
  denominator and inflates the reported rate. 90_cidatables deliberately
  uses followuptime (not person_days) for the SAS-named columns, so the
  corruption is confined to the denominators/msoc rate table.
- **severity:** wrong answer (denominators table: understated
  person_days, overstated rate_per_1000_py for affected strata; cohort
  membership and episode counts unaffected). Reachability: requires a
  study shape with at_risk_start > 0 AND episodes shorter than the
  offset — not the demo defaults (t2atriskstart 0 everywhere in
  study/*.json), which is why the existing
  test_pipeline.py::test_person_time_is_positive never caught it.
- **affected paths:** src/qrp/sql/60_followup.sql (cohort_final
  person_days).
- **minimal fix/commit:** inv/0f462p/fix3-c3 red 26ddf87 → green
  1a810a2, merged into inv/0f462p/arb3-findings. `greatest(0, ...)`
  wraps the span, mirroring the adjacent followuptime floor; nothing
  else changes. The package C witness that pinned the old behavior was
  updated on the arb branch (test_pkg_c_followup.py
  ::test_followup_zero_length_followup_via_at_risk_start now pins
  person_days == 0 and closes OC-3; the c_models oracle models the
  floor).
- **red/green commands+results:**
  - red: `.venv/bin/python -m pytest tests/test_regression_c3.py` →
    `person_days is not floored at 0 ... [(2014-07-31, 2014-06-30,
    -30)]`
  - green: same command → 1 passed in 0.45s
- **broader validation:** tests/test_determinism.py +
  tests/test_rewrites.py 15 passed (fix worktree); full
  tests/test_pipeline.py in the fix worktree (recorded below in
  ARBITRATION.md; the two pre-existing exempt spill-attribution OOM
  failures excluded); package C quick suite green on the merged tree
  after the pin/oracle update (9 passed); package D denominators quick
  7 passed.
- **related findings:** OC-3 (package C open question — RESOLVED by
  this ruling), C-1 (same stage family; different mechanism).
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
- **SCDM 8.2.2 alignment:** reviewed 2026-11-03 — person-time
  arithmetic; no SCDM data-model-bearing assumption.
