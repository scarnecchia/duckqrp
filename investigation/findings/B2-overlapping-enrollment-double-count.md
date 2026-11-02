# B2: one dispensing inside overlapping enrollment episodes is
# stockpiled twice (inherited from SAS — documented, parity kept)

- **id:** B2
- **proposed issue title:** Overlapping raw enrollment spans double-count
  a dispensing through the stockpile pre-shave/re-shave joins
  (numdispensing=2, supplies 16/46 for one 30-day fill) — inherited
  bug-for-bug from the SAS original; parity kept by operator ruling
- **contract:** The pipeline's outputs are governed by parity with the
  original Sentinel QRP SAS implementation (docs/SAS_PARITY.md is an
  audit against the SAS macros; docs/PARITY_FINDINGS.md measures
  results against SAS outputs). The arithmetic contract a dispensing
  violates — one dispensing is one dispensing: numdispensing counts
  claims, and stockpiled supply cannot exceed what was dispensed — is
  the sane-internal-arithmetic reading the package's own oracle models
  encode; INPUTS.md is silent on enrollment overlap semantics. The
  behavior below was verified bug-for-bug against the ORIGINAL SAS
  source (read-only reference tree /home/scarndp/dev/Sentinel/qrp/,
  package 14.5.0), so the double-count is INHERITED, and fixing the
  Python side would diverge from SAS parity — which is not authorized
  (operator ruling on the B1 decision brief,
  investigation/packages/ARBITRATION.md).
- **trigger:** Raw `cdm_enrollment` rows for one patient where a later
  span ruptures from an inner span while lying inside an outer one
  (A=[2011-01-01..2011-12-31], B=[2011-02-15..2011-06-15],
  C=[2011-09-01..2011-09-30], enrol_gap=0), plus a dispensing dated
  inside both episode outputs. RIPR: reachable whenever a real SCDM
  extract contains overlapping enrollment rows for one patient;
  infection happens at the stockpile pre-shave; propagates through
  stockpiled into index_candidates/episodes; revealed by any output
  carrying rxsup/rxamt/numdispensing.
- **witness/repro command:** `investigation/probes/b_overlap_probe.py` —
  `python investigation/probes/b_overlap_probe.py` (repo root on
  sys.path); independently reproduced by the orchestrator (manifest
  pass `orchestrator-b2-repro-1`). Pinned in the suite as positive
  parity pins — the original strict-xfail witness
  `tests/properties/test_pkg_b_enrollment.py::
  test_enrollment_overlapping_raw_spans_known_divergence` was replaced
  at 88edf91 (commands naming it now fail with pytest exit 4, node not
  found; use the current names):
  `python -m pytest
  'tests/properties/test_pkg_b_enrollment.py::test_enrollment_overlapping_raw_spans_parity'
  'tests/properties/test_pkg_b_stockpile.py::test_stockpile_fanout_parity_on_overlapping_spans'
  --hypothesis-profile=quick` → `2 passed` (pins asserting the
  double-count behavior, including the explicit one-claim / 46-day /
  numdispensing-2 witness asserts).
- **expected vs actual:** Expected (one-dispensing-is-one-dispensing):
  ONE enrollment episode [2011-01-01..2011-12-31]; the single 30-day /
  10.0 fill stockpiles to ONE row, numdispensing=1, rxsup=30, rxamt=10.
  Actual: `enrollment_spans` emits TWO overlapping episodes
  ([01-01..12-31] and [09-01..09-30]); `stockpiled` emits TWO rows for
  the one fill — rxsup 16 / rxamt 5.33 and rxsup 46 / rxamt 15.33 —
  EACH with numdispensing=2; `index_candidates` carries the inflated
  payload (rxsup=46, numdispensing=2) after the longest-supply-per-day
  dedupe.
- **first failing revision:** c3e60db5d08343b1cebbb90d234602f09cb0d70d
  (campaign base; `src/` is byte-identical between c3e60db and
  discovery HEAD 82e4364 — only pyproject.toml dev extras differ).
- **mechanism:** The lag-break segmentation (20_enrollment.sql:67,
  `date_diff('day', prev_end, enr_start) - 1 > enrol_gap`) breaks C
  from B (77 blank days) while C still lies inside A, so
  `enrollment_spans` emits overlapping episodes. The stockpile
  pre-shave (30_exposure.sql, `shaved` CTE) and re-shave (final SELECT)
  join claims to enrollment with `adate BETWEEN e.enr_start AND
  e.enr_end`, matching the claim once per CONTAINING span; the same-day
  collapse sums the copies (sup 16+30 → the pushed record runs to 46
  days) and numdispensing counts them (2); the re-shave fans out again,
  producing the 16/46 pair. The inflated payload flows into
  index_candidates (longest-supply dedupe keeps a 46-day record for one
  physical fill) and episode_rxsup.
- **SAS inheritance (citations, all under
  /home/scarndp/dev/Sentinel/qrp/inputfiles/macros/):**
  1. `ms_create_enrollment_spans.sas:109-117` builds the enrollment
     reference by calling `%MS_EPISODEREC2(INFILE=indata.&ENRTABLE.,
     OUTFILE=worktemp.enr_&RECONTYP.&ENROLGAP., ...)` — the conciliated
     periods that become the shave reference.
  2. `ms_episoderec2.sas:67-77` breaks each span against the
     immediately PREVIOUS span (`&ENRSTART.-lag(&ENREND.)-1 >
     &ENROLGAP.` plus coverage-flag changes; `first.Patid` restarts);
     `:79-97` reconciles min/max per (PatId, episode). Overlapping
     episodes SURVIVE into `worktemp.enr_&num.` — no overlap dedup
     anywhere in the macro.
  3. `ms_shaveoutside.sas` (macro body, `proc sql` "need cartesian
     product first") joins claims × reference spans on `in.PatId=
     ref.PatId and ref.&refstart. <= in.&ToShaveEnd. and
     in.&ToShaveStart. <= ref.&refend.` and shaves EACH pair (KeepPartBf=N
     drops starts before the span; end clipped; RxSup recomputed; RxAmt
     prorated) — fanning out per overlapping span, with no dedupe
     after. (`ms_shaveinside.sas` is the complementary shave-OUT used
     for denominator/inclusion periods, not this chain.)
  4. `ms_cidanum.sas:1299-1308` runs the pre-shave on `_ITDrugs`;
     `ms_stockpiling.sas` (the `proc means` same-day collapse,
     `output out=&outfile. ... N(&CLMSUP.)=NumDispensing;`) SUMS the
     fanned copies of one claim; `ms_cidanum.sas:1531-1541` ("Need to
     reshave because Stockpiling may have pushed claims outside
     enrollment period") re-shaves through the same fanning join.
  Net: SAS itself emits the overlapping episodes and produces exactly
  the 16/5.33 + 46/15.33, numdispensing=2 pair for the probe input —
  the defect is inherited, not introduced by this implementation.
- **severity:** wrong answer / silent numeric inflation of supply,
  amount and dispensing count downstream into index dates and
  episode_rxsup (min_days_supp eligibility). **+ unproven impact:** how
  much real-world cohort output is affected is NOT demonstrated end to
  end; it requires overlapping raw enrollment rows in the extract.
  **Mitigation context:** the Sentinel model QA Suite is expected to
  prevent overlapping enrollment spans upstream, so real-world exposure
  is mitigated — stated as expected process, not verified in this
  campaign. No crash; outputs stay internally consistent.
- **affected paths:** src/qrp/sql/30_exposure.sql (both shave joins —
  the fan-out), src/qrp/sql/20_enrollment.sql (segmentation producing
  the overlapping episodes), src/qrp/sql/50_episodes.sql (`located`
  LEFT JOIN fans out per containing span for the enrollment-change
  break — same input family, not separately witnessed), src/qrp/sql/
  40_index.sql (SEMI JOIN, fan-out immune; consumes the inflated
  payload).
- **minimal fix/commit:** NOT APPLIED (operator ruling: parity kept) — the validated prototype (red f4d2e6a / green c87dc0b, branch since deleted, evidence in manifest passes arb-red-b2-1/arb-green-b2-1): shave each claim
  against exactly ONE containing span: replace both enrollment-spans
  joins in 30_exposure.sql with a lateral one-row pick, e.g. `JOIN
  LATERAL (SELECT e.enr_end FROM enrollment_spans e WHERE
  e.enr_cfg_id = cc.enr_cfg_id AND e.patid = x.patid AND x.adate
  BETWEEN e.enr_start AND e.enr_end ORDER BY e.enr_end DESC,
  e.enr_start DESC LIMIT 1) e ON TRUE` (latest containing enr_end
  keeps every day enrolled under any containing span; deterministic;
  empty lateral preserves the drop semantics in both shaves). This
  restores one-dispensing-is-one-dispensing but DIVERGES from SAS on
  this input family, so per the operator ruling it is deliberately not
  applied. A prototype was built, regression-tested red→green, and full
  -pipeline-validated on branch inv/0f462p/fix-b2 (red f4d2e6a, green
  c87dc0b; tests/test_determinism.py + tests/test_rewrites.py 15
  passed; tests/test_pipeline.py 287 passed with only the two
  pre-existing exempt spill-attribution OOM failures) and then REMOVED
  when the operator ruled for parity — results preserved in manifest
  passes arb-red-b2-1 / arb-green-b2-1.
- **red/green commands+results:** probe/xfail evidence per the ruling —
  no fix branch):**
  - red (defect present):
    `python investigation/probes/b_overlap_probe.py` → enrollment_spans
    2 overlapping episodes; stockpiled 2 rows (16/5.333... and
    46/15.333...), each numdispensing=2; index_candidates rxsup=46,
    numdispensing=2. Reproduced at c3e60db (src identical to discovery
    HEAD).
  - pin (current): `python -m pytest
    'tests/properties/test_pkg_b_enrollment.py::test_enrollment_overlapping_raw_spans_parity'
    'tests/properties/test_pkg_b_stockpile.py::test_stockpile_fanout_parity_on_overlapping_spans'
    --hypothesis-profile=quick` → `2 passed` (positive pins of the
    defect behavior; they fail the day any parity policy changes). The
    arbitration-close record `1 xfailed` referred to the strict-xfail
    witness `test_enrollment_overlapping_raw_spans_known_divergence`,
    removed at 88edf91 — repro commands naming it now exit 4.
- **broader validation:** package B quick suites
  (test_pkg_b_enrollment/stockpile/index/episodes, quick profile) pass
  with the then-current pin in place (7 passed, 1 xfailed at
  arbitration close; the xfail was the strict pin since replaced — see
  above); the
  discovery-profile generators' overlapping-span events (69% of
  enrollment examples) never place a claim in two containing spans,
  which is why the double-count required the hand probe; package A/C
  quick sets and determinism/rewrites pass on the merged arb branch
  (109 + 38 passed) confirming the A-side fixes did not disturb this
  behavior.
- **addendum — episode peer ordering under fan-out is engine-defined
  (doc-only; FINAL-AUDIT §4):** when the `located` fan-out produces
  rows tied on the whole chaining window key (adate, expiredt, rxsup),
  `ORDER BY s.adate, s.expiredt, s.rxsup` (50_episodes.sql) is not a
  total order, and the episode pairing/count depends on join emission
  order: observed (3,6) at threads 1, (3,3,3)/(6,3) at threads 4 on the
  nine-row witness (analysis and probes:
  investigation/packages/B-independent-review.md; in-suite invariant
  tests: tests/properties/test_pkg_b_independent_review.py). SAS `BY`
  keys do not pin this order; tied differently-flagged raw enrollment
  rows meet the same non-total ordering in 20_enrollment.sql. The suite
  therefore asserts only order-agnostic invariants in the overlap
  domain (upstream multiset stability, episode/located supply
  conservation, membership in the enumerated allowed decompositions)
  and asserts exact episode pairing NOWHERE — no observed ordering is
  endorsed. Any production ordering/rejection policy is a precondition
  for a future fix and remains operator-gated; upstream Sentinel QA
  rejection of overlapping spans remains expected-but-unverified.
- **related findings:** B1 — RESOLVED: the segmentation is parity-
  intended (docs/SAS_PARITY.md "Enrollment spans" verified against
  ms_episoderec2.sas:67-77), so the fix location question collapsed
  into the operator's parity ruling; A-3 (same conclusion shape: wart
  inherited from SAS, documented rather than changed).
- **disclosure status:** PRIVATE — NOT AUTHORIZED FOR PUBLICATION
