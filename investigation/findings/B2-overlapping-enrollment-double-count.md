# B2: overlapping raw enrollment spans double-count a dispensing through
# the stockpile shaves — SAS parity, not a defect

- **id:** B2
- **proposed issue title:** One dispensing inside overlapping enrollment
  episodes is stockpiled twice (supplies 16/46, numdispensing=2) —
  verified bug-for-bug identical to SAS; optional stricter fix exists
- **contract:** The governing contract is parity with the original SAS
  QRP (docs/SAS_PARITY.md is written entirely against the SAS macros;
  docs/PARITY_FINDINGS.md measures results against SAS outputs). The
  finder's "one dispensing is one dispensing" reading is a
  sane-arithmetic expectation, not the published contract; INPUTS.md
  says nothing about enrollment overlap semantics. Verdict against the
  actual SAS source (read-only reference tree
  /home/scarndp/dev/Sentinel/qrp/, package 14.5.0):
  - `inputfiles/macros/ms_episoderec2.sas:67-77` — the enrollment
    episode break compares each span to the immediately PREVIOUS span
    (`&ENRSTART.-lag(&ENREND.)-1 > &ENROLGAP.`), and `:79-97`
    reconciles min/max per (PatId, episode). Overlapping episodes
    therefore SURVIVE into the shave reference `worktemp.enr_&num.`
    with no overlap dedup anywhere in the macro.
  - `inputfiles/macros/ms_shaveoutside.sas` (macro body, `proc sql`
    "need cartesian product first") joins claims × reference spans on
    `ref.Enr_Start <= claim_end AND claim_start <= ref.Enr_End` and
    shaves EACH pair — the join fans out per overlapping span, and no
    dedupe follows.
  - `inputfiles/macros/ms_cidanum.sas:1299-1308` runs that pre-shave on
    `_ITDrugs`; `inputfiles/macros/ms_stockpiling.sas` (the `proc
    means` same-day collapse, `N(&CLMSUP.)=NumDispensing`) then SUMS
    the fanned copies of one claim; `ms_cidanum.sas:1531-1541` re-shaves
    through the same fanning join.
  Net: SAS itself stockpiles one dispensing sitting in overlapping
  episodes as TWO records — supplies 16 and 46, each with
  numdispensing=2 — exactly what the shipped SQL does.
- **trigger:** Raw `cdm_enrollment` rows for one patient where a later
  span ruptures from an inner span while lying inside an outer one
  (e.g. A=[2011-01-01..2011-12-31], B=[2011-02-15..2011-06-15],
  C=[2011-09-01..2011-09-30], enrol_gap=0), plus a dispensing dated
  inside both episode outputs.
- **witness/repro command:** `investigation/probes/b_overlap_probe.py`
  (`python investigation/probes/b_overlap_probe.py` with repo root on
  sys.path) — independently reproduced by the orchestrator (manifest
  pass `orchestrator-b2-repro-1`). Also pinned as the committed
  property `tests/properties/test_pkg_b_enrollment.py::
  test_enrollment_overlapping_raw_spans_sas_parity` (formerly
  xfail(strict) `test_enrollment_overlapping_raw_spans_known_divergence`,
  rewritten on the arb branch to pin the SAS-parity semantics with the
  citation chain): `python -m pytest
  'tests/properties/test_pkg_b_enrollment.py::
  test_enrollment_overlapping_raw_spans_sas_parity'
  --hypothesis-profile=quick`
- **expected vs actual:** Expected per the sane-arithmetic reading (the
  finder's day model): ONE merged episode [2011-01-01..2011-12-31]; the
  single 30-day/10.0 fill stockpiles to ONE row, numdispensing=1,
  rxsup=30. Actual (shipped SQL, = SAS): TWO overlapping episodes
  [01-01..12-31] and [09-01..09-30]; stockpiled emits TWO rows for the
  one fill — rxsup 16 / rxamt 5.33 and rxsup 46 / rxamt 15.33 — EACH
  with numdispensing=2; index_candidates carries the inflated payload
  (rxsup=46, numdispensing=2) after the longest-supply-per-day dedupe.
- **first failing revision:** N/A — behavior verified identical to SAS
  at c3e60db5d08343b1cebbb90d234602f09cb0d70d (campaign base); `src/`
  is byte-identical between c3e60db and discovery HEAD 82e4364 (only
  pyproject.toml dev extras differ).
- **mechanism:** The lag-break segmentation (20_enrollment.sql:67,
  `date_diff('day', prev_end, enr_start) - 1 > enrol_gap`) breaks C
  from B (77 blank days) while C still lies inside A, emitting
  overlapping episodes — documented SAS parity (docs/SAS_PARITY.md
  "Enrollment spans", now verified against the SAS source itself). The
  stockpile pre-shave and re-shave joins (30_exposure.sql, `x.adate
  BETWEEN e.enr_start AND e.enr_end`) then match the claim once per
  containing episode; the same-day collapse sums the copies and
  numdispensing counts them. This is the same cartesian-join +
  same-day-sum structure SAS runs, so the "double count" is SAS parity
  of a data-quality wart (messy SCDM extracts do contain overlapping
  enrollment rows), not an implementation divergence.
- **severity:** silent divergence from SAS: NONE (parity confirmed).
  Divergence from a sane single-count reading: yes, confirmed end to
  end. **+ unproven impact:** the inflated rxsup/numdispensing flows
  into index_candidates and episode_rxsup (min_days_supp eligibility),
  so cohorts with overlapping raw enrollment could over-credit supply
  — but SAS produces the same inflation, so any "fix" changes results
  AWAY from the SAS baseline. Bounded for the probe by the per-day
  dedupe keeping only the longest (still inflated) record.
- **affected paths:** src/qrp/sql/20_enrollment.sql (segmentation,
  parity), src/qrp/sql/30_exposure.sql (both shave joins),
  src/qrp/sql/50_episodes.sql (`located` LEFT JOIN fans out per
  containing span for the enrollment-change break — same input family,
  lead B-family, not separately witnessed), src/qrp/sql/40_index.sql
  (SEMI JOIN, fan-out immune).
- **minimal fix/commit:** NONE shipped (no parity-preserving fix
  exists; both candidate fixes diverge from SAS). An optional
  deliberate divergence — shave each claim against exactly ONE
  containing span (the one running latest), via `JOIN LATERAL ... LIMIT
  1` in both shaves — is built, validated and available on branch
  `inv/0f462p/fix-b2` (red f4d2e6a, green c87dc0b). Decision is the
  operator's; see investigation/packages/ARBITRATION.md (decision
  brief DB-1).
- **red/green commands+results:** for the OPTIONAL divergence branch
  only (`inv/0f462p/fix-b2`, from c3e60db):
  - red: `.venv/bin/python -m pytest tests/test_regression_b2.py -q`
    at c3e60db+f4d2e6a → `AssertionError: one dispensing produced 2
    rows; assert 2 == 1`
  - green: same at c87dc0b → `1 passed`; tests/test_determinism.py +
    tests/test_rewrites.py → `15 passed`; tests/test_pipeline.py →
    `287 passed, 2 failed` (the two pre-existing exempt
    spill-attribution OOM tests), `1 skipped`, 6:53.
- **broader validation:** on the arb branch WITHOUT the fix: package B
  quick suites (`test_pkg_b_enrollment/stockpile/index/episodes`,
  quick profile) → `8 passed, 0 xfailed` after the witness was
  rewritten to parity semantics; package A quick set + determinism +
  rewrites + both standalone regression tests → `109 passed`; package
  C + harness stages → `38 passed`. The discovery-profile overlapping
  -span generators (69% overlapping-pair events, B.md pass record 2)
  pass unchanged, confirming the generated overlap shapes never place
  a claim in two containing spans, which is why the double-count
  needed the hand probe.
- **related findings:** B1 (the fix-location question — resolved, see
  ARBITRATION.md DB-1); A-3 shares the pattern " wart is SAS parity".
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
