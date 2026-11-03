# SCDM 8.2.2 full-surface alignment review — audit matrix

**PRIVATE — NOT AUTHORIZED FOR PUBLICATION.**

Follow-up to the B2/enrollment review (commits `744189d` + `7165d77`),
extending the same method to every synthetic builder, fixture
generator, and oracle in the repo. Audit date: 2026-11-03. Base
revision (this audit's scope anchor): **`7165d77`** (discovery tip at
audit start; also the AC.2 scope base — see Scope evidence below).

Ground truth: the SCDM skill references at
`/home/scarndp/.config/polytoken/skills/howto-work-with-scdm-data/references/`
(`index.md` routes to per-table files). The standing rules from the
index governed every verdict: byte lengths not display widths; SAS
dates count from 1960-01-01; special missing becomes ordinary null in
Parquet; sort order is not the uniqueness key; named vocabularies are
coding systems, not exhaustive code lists; a stated documentation
ambiguity is unresolved and never becomes a validation rule.

Raw per-audit reports (verbatim, with full detail blocks):
`investigation/packages/scdm-audit-notes/audit-{a,b,c,d}-raw.md`.

## Standing framing: subset extracts

The synthetic extracts contain only pipeline-relevant columns (the
read sets in `src/qrp/scdm.py` `SCDM`), so they are **not** full SCDM
tables — e.g. no EncounterID/ProviderID on synthetic diagnosis rows,
no DtImpute/Source/Confidence on death rows. A "missing required SCDM
column" in a synthetic extract is standing context, never a defect;
the audits verdicted only the columns each builder actually models.

## Audit matrix

Verdicts: `aligned` · `aligned-with-annotation` · `misaligned-fixed`
(test-side change applied in this follow-up) · `probe-needed`
(behavior unknown from reading; probe executed — see Probes below).

| table | reference consulted | fixtures/builders checked | item | verdict | outcome (test id or probe path) |
|---|---|---|---|---|---|
| demographic | demographic.md (Unique row: PatID) | synth.py dup-demog block + hand_fixture p5/p5_tie; oracles/a_demographics_tie.py D_*; oracles/b_pkg_fixtures.py dup_demog | A1 duplicate-demographics fixtures unlabeled as SCDM-rule violations | aligned-with-annotation | docstring labels in synth.py (5a6fadd); note in finding A-3 |
| demographic | demographic.md (Sex A/F/M/U) | synth.py `_SEXES`; oracles/a_config_semantics.py; sql/10_normalize.sql sex collapse; sql/45_pov1.sql cfg_demog join | A2 `_SEXES` draws only F/M | misaligned-fixed | `_SEXES` widened (5a6fadd); tests/properties/test_scdm_alignment.py::test_sex_u_a_flow_through_conditionless_study_and_collapse_to_o and ::test_sex_restriction_excludes_u_a_at_pov1_and_attrition_is_unmoved |
| demographic | demographic.md (Birth_Date missing allowed) | synth.py `Patient.birth_date`; sql/10_normalize.sql demographics_missing; sql/70_outputs.sql `_att_dem`; sql/45_pov1.sql | A3 null Birth_Date never drawn; pipeline drops it | misaligned-fixed | type widened + `GenerateOptions.null_birth_dates` (5a6fadd); test_scdm_alignment.py::test_null_birth_date_member_dropped_at_attrition_level_1 |
| demographic | demographic.md (Race 0-5 plus M) | synth.py `_RACES`; sql/10_normalize.sql race default; oracles/a_config_semantics.py | A4 `_RACES` lacks M (Multi-racial) | misaligned-fixed | `_RACES` widened (5a6fadd); M-race eligibility pinned end to end by test_scdm_alignment.py::test_sex_u_a_flow_through_conditionless_study_and_collapse_to_o (member 2 carries race 'M' through cfg_demog into pov1) |
| demographic | demographic.md (Hispanic N/Y/U) | synth.py hispanic draw; sql/10_normalize.sql default 'U' | A5 hispanic draws match domain exactly | aligned | none needed |
| enrollment | enrollment.md (MedCov Y/N/U/A; DrugCov Y/N/U) | synth.py EnrollmentSpan docstrings + generate(); oracles/b_pkg_fixtures.py `_FLAG_POOL` | A6 stale `# Y / N` comments; flag variety placement | misaligned-fixed | docstrings corrected in synth.py (5a6fadd); flag variety deliberately stays in tests/properties/oracles/b_pkg_fixtures.py `_FLAG_POOL`, pinned by tests/properties/test_pkg_b_enrollment.py::test_enrollment_unknown_flags_and_chart_interaction |
| enrollment | enrollment.md (MedCov/DrugCov domains) | oracles/b_pkg_fixtures.py `_FLAG_POOL` (7 pairs) | A7 end-state legality after 744189d | aligned | pinned by test_pkg_b_enrollment.py::test_enrollment_duplicate_identical_spans_collapse and ::test_enrollment_unknown_flags_and_chart_interaction (744189d/7165d77) |
| enrollment | enrollment.md (Enr_Start/End rules; collapsing rule) | synth.py span construction; oracles/b_enrollment_model.py; sql/10_normalize.sql:28-30 | A8 short custom windows can invert spans (dropped by cdm_enrollment) | aligned-with-annotation | generate() docstring note (5a6fadd) |
| enrollment | enrollment.md (Chart Y/N, required in SCDM) | synth.py chart comment; src/qrp/scdm.py read set | A9 "optional column in SCDM" misattribution | aligned-with-annotation | corrected in the A6 docstring edit (5a6fadd) |
| diagnosis | diagnosis.md (EncType AV/ED/IP/IS/OA — no XX) | synth.py `_ENCTYPES` + `Diagnosis.enctype` comment; sql/10_normalize.sql:160; sql/72_codedistribution.sql:81; sql/74_utilization.sql:75 | B1 IS never drawn; comment presents the pipeline's 'XX' fallback as a value | misaligned-fixed | IS appended to `_ENCTYPES` + comment fixed (5a6fadd); test_scdm_alignment.py::test_is_encounters_counted_and_is_claims_not_enveloped |
| diagnosis | diagnosis.md (Pdx P/S/X/blank, IP/IS only) | synth.py generate pdx draw + hand_fixture AV rows; sql/10_normalize.sql:159,166; sql/30_exposure.sql:490 | B2 pdx drawn P/S on any enctype | misaligned-fixed | conditional pdx in generate(); hand_fixture enveloping probes kept + labeled (5a6fadd); test_scdm_alignment.py::test_pdx_restricted_care_settings_match_only_legal_shapes |
| dispensing | dispensing.md (Rx_CodeType ND/SN/SK/DM/DI/RN/AT; ProviderID .U) | synth.py rx_codetype="ND"; sql/30_exposure.sql:76-82; src/qrp/scdm.py read set | B3 codetype equality never exercised off the ND/ND diagonal (ProviderID always populated — inert column, annotation) | probe-needed | probe family 6 → test_scdm_alignment.py::test_alternate_rx_codetype_captures_only_the_matching_fill; providerid annotation in synth.py (5a6fadd) |
| dispensing | dispensing.md (RxDate/Rx/RxSup/RxAmt) | synth.py dispensing block; sql/10_normalize.sql:81-105 | B4 remaining dispensing fields | aligned | zero/absent branches pinned by tests/test_regression_a2.py |
| procedure | procedure.md (ADate cross-table rule; EncType; PX_CodeType; OrigPX) | oracles/a_optional_columns.py:31 (suite's only Procedure row); synth.py generate procedures=() | B5 legal fields; cross-table ADate mismatch unlabeled; cdm_procedure enveloping has zero data coverage | aligned-with-annotation | ADate violation labeled (5a594c1); enveloping pinned by test_scdm_alignment.py::test_procedure_claims_enveloped_inside_ip_stay_only |
| diagnosis | diagnosis.md (Pdx; Dx_Codetype domain) | tests/test_regression_a2.py; tests/test_regression_c3.py fixture blocks | B6 regression fixtures under the SCDM lens | aligned | c3 schema-only literals made in-domain as hygiene (ce178d8) |
| encounter | encounter.md (EncType IS; DDate rules) | synth.py `_ENCTYPES` + encounter block; sql/74_utilization.sql:75; sql/72_codedistribution.sql:81-84; oracles/d_stages.py:63,118,1584 | C1 IS absent from draws while production counts it and both oracles model it (dead coverage) | misaligned-fixed | IS encounter branch with ddate (5a6fadd); test_scdm_alignment.py::test_is_encounters_counted_and_is_claims_not_enveloped (SQL == oracle) |
| diagnosis | diagnosis.md (EncType domain) vs 30_exposure.sql:486-490 + 72_codedistribution.sql:79-96 | sql; oracles/d_denom.py:489; oracles/d_stages.py:1719 | C2 'XX'-fallback claims: events yes, distindex entities no (SAS parity) | aligned-with-annotation | annotated in d_denom + d_stages (5a594c1); pinned by test_scdm_alignment.py::test_xx_enctype_claim_counts_as_event_but_matches_no_distindex_entity |
| encounter | encounter.md (Discharge_Status 16 values; EX) | synth.py encounter draws; sql/10_normalize.sql:184-195; tests/test_pipeline.py:8151-8191 | C3 EX discharge absent from the properties generator | aligned-with-annotation | decision (b): existing-suite coverage suffices (lowercase 'ex', missing ddate, precedence all pinned); Encounter docstring note (5a6fadd) |
| encounter | encounter.md:7/21/23 (unique-row caveat) | synth.py EncounterID scheme; oracle comments | C4 uniqueness key tension in the reference itself | aligned-with-annotation | UNRESOLVED (quoted in audit-c-raw.md); docstring note; patid*10 ids satisfy both readings (5a6fadd) |
| death | death.md (full column list; COD separate table) | src/qrp/scdm.py death spec; synth.py Death rows; sql/10_normalize.sql:177-182 | C5 two-column subset relationship | aligned-with-annotation | subset framing recorded here (report-level); rules the subset can express are satisfied and re-guarded by 10_normalize |
| lab_result | laboratory-result.md (Result_Type conditional semantics; MS_Result_C/N; MS_Result_unit) | synth.py labs block; sql/76_labs.sql; oracles/d_stages.py labs | C6 'U' drawn on mapped CREATININE; non-null numeric on 'C' rows; unit always populated | misaligned-fixed | legal N/C triples in tests/properties/synth.py; the reference ties 'U' to UNMAPPED rows (laboratory-result.md:77) without stating the converse, so the generator conservatively never draws 'U'; ms_result_c widened to `str \| None` (5a6fadd) |
| enrollment | enrollment.md (PlanType {F,H,E,P,S,M,O,U}; PayerType {CO,MC,MD,OG,OT,UN}) | tools/gen_synthetic.py:80-81 (production generator, frozen) | D plantype='MC'/payertype='P' — the two columns' values are swapped; flows into the /tmp/qrp_data/100k benchmark | probe-needed | DB-11 decision brief (investigation/packages/ARBITRATION.md); no pipeline change without operator ruling |
| diagnosis | diagnosis.md (Pdx IP/IS only) | tools/gen_synthetic.py:127-128 (frozen) | D pdx P/S on ~80%-AV rows presented as canonical benchmark data | probe-needed | DB-11 decision brief |
| death | death.md (Source {L,N,S,T,O,M}) | tools/gen_synthetic.py:142 (frozen) | D death source 'D' is fabricated | probe-needed | DB-11 decision brief |
| diagnosis | diagnosis.md (Pdx IP/IS only) | src/qrp/selfcheck.py:152-153 (frozen) | D doctor extract: pdx 'P' on ~50% AV rows | probe-needed | DB-12 decision brief |
| demographic | demographic.md | tools/gen_synthetic.py:47-58,165-178; src/qrp/selfcheck.py:127-133; tests/test_pipeline.py:5759; tests/test_regression_f1.py:43-53 | D demographic pools and labeled tie injections | aligned | tie rows carry a "genuine tie" label; the unique-row framing for the generator comments is recorded in investigation/packages/scdm-audit-notes/audit-d-raw.md (annotation suggestions) |
| enrollment | enrollment.md | tools/gen_synthetic.py:70-79; src/qrp/selfcheck.py:134-138 | D enrollment pools (aside from the swapped columns above) | aligned | none needed |
| dispensing | dispensing.md | tools/gen_synthetic.py:94-104,154-164; src/qrp/selfcheck.py:139-145; tests/test_regression_a2.py:31-33,98-99,132-133 | D dispensing pools; labeled duplicate/zero-amount/no-codetype probes | aligned | deliberate probes already labeled in-file |
| diagnosis | diagnosis.md | tools/gen_synthetic.py:117-126 | D diagnosis codetype/code/enctype pools | aligned | none needed |
| procedure | procedure.md | tools/gen_synthetic.py:187-197 | D procedure pools (C4 codetype, IP/AV/ED enctypes) | aligned | none needed |
| lab_result | laboratory-result.md | tools/gen_synthetic.py:203-217 | D lab pools (mapped tests, N/U/C, SR_PLS, X/R/F, MG/DL+MMOL/L) | aligned | result-conditional shapes in the production generator were checked against the same reference lines as C6; the generator's 'U' rows ride mapped test names — recorded under DB-11 (same file as the other generator defects) |
| diagnosis | diagnosis.md (Pdx) | tests/test_pipeline.py:7890-7898, 8024-8055 | D claim-view fixtures: AV+pdx S/P rows (token expansion, flag preservation) | aligned-with-annotation | labeled as deliberate out-of-domain probes (ce178d8) |
| encounter | encounter.md (Discharge_Status case) | tests/test_pipeline.py:8176-8179,8190 | D lowercase 'ex' row | aligned-with-annotation | labeled as a case-insensitivity probe (ce178d8) |
| lab_result | laboratory-result.md (LAB01 combination) | tests/test_pipeline.py:3752-3757 | D 'NOPE' specimen rejection probe | aligned | already labeled in-file |
| demographic | demographic.md (unique row) | tests/test_determinism.py:118-130 | D duplicate-PatID tie fixture | aligned-with-annotation | docstring names the DEM unique-row violation (ce178d8) |
| lab_result | laboratory-result.md (Fast_Ind; MS_Test_Sub_Category) | tests/test_optional_claim_columns.py:43-44 | D fast_ind 'Y' and sub_category ' chemistry ' out of domain | misaligned-fixed | fixed to padded in-domain ' X ' (trim probe kept) and '' in tests/test_optional_claim_columns.py (ce178d8) |
| diagnosis | diagnosis.md (Pdx) | tests/test_optional_claim_columns.py:41; tests/test_regression_a4.py:133 | D AV+pdx 'P' presented as the canonical extract row | misaligned-fixed | enctype AV -> IP in tests/test_optional_claim_columns.py and tests/test_regression_a4.py, behavior-neutral (ce178d8) |
| diagnosis | diagnosis.md (Dx_Codetype; Pdx) | tests/test_regression_c3.py:93-100 | D zero-row schema literals out of domain | aligned-with-annotation | literals made in-domain + note (ce178d8); rows never materialize (WHERE 1 = 0) |
| (cross-table) | config.py utilfile loader (cohortgrp required) vs 74_utilization.sql equality join | tests/properties/test_pkg_d_optional_stages.py `util_med_drug` ("", "MED", ...) | follow-up: utilization/baseline fixtures carried cohortgrp '' — matches no cohort in SQL, oracle, or loader, so the counts compared zeros against zeros (vacuous) | misaligned-fixed | real per-cohort windows in tests/properties/test_pkg_d_optional_stages.py + four oracle repairs in tests/properties/oracles/ (5a594c1); the IS boundary test test_scdm_alignment.py::test_is_encounters_counted_and_is_claims_not_enveloped exercises the now-live counts |

Counts: 39 items — aligned 11 · aligned-with-annotation 12 ·
misaligned-fixed 11 · probe-needed 5 (all five resolve to decision
briefs DB-11/DB-12 — frozen production files — or to executed tests;
none remains open).

## Probes executed

All probes ran in the campaign venv against the working tree at this
review's commits; the standalone script and its output are committed:

- `investigation/probes/scdm_full_alignment_probe.py`
- `investigation/witnesses/scdm-full-alignment-probe.txt`

| probe family | question | outcome |
|---|---|---|
| 1 — sex U/A end to end | where do U/A-sex members land? | conditionless cfg_demog admits them (raw-value join); covariate collapses to 'O'; restricted study drops them at pov1 only — attrition levels 3-5 carried (documented SAS-unverified behavior) → pinned as 2 tests |
| 2 — null Birth_Date | normalized? dropped where? | dropped at attrition level 1 (`_att_dem`), excluded from pov1 via demographics_missing → pinned as 1 test |
| 3 — IS encounter + claims | counted? enveloped? distindex? | enc_is=1, enc_ip=1; IS stays are NOT enveloping days; IS/P is a first-class distindex entity; SQL == d-package oracles on utilization + all three distindex tables → pinned as 1 test |
| 4 — pdx x enctype under restricted tokens | does (IS,P)/(IS,'') match only legal shapes? | exact match confirmed; enveloped IP/'X' rows match neither → pinned inside the pdx test |
| 5 — 'XX' fallback claim | event? distindex? | event fires under '**'; zero distindex entities; hoi list NULL; oracle agrees on both sides → pinned as 1 test + parity annotation |
| 6 — alternate Rx codetype (SN) | observable? | SN fill captured under SN study, excluded under ND (and vice versa) → pinned as 1 test |
| 7 — procedure enveloping | covered anywhere? | inside-stay procedure re-filed IP/'X', outside stays AV/'' → pinned as 1 test |

Bonus discovery during probe construction: the utilization fixture
vacuity (see matrix last row) — the probe could not make `enc_is`
nonzero until the cohortgrp bug was fixed, which is what exposed it.

## Unresolved documentation ambiguities (recorded, not resolved)

1. **Encounter unique-row key** — encounter.md:7 ("keyed by PatID and
   EncounterID") vs :21 ("Unique row: EncounterID"); the reference
   itself flags the tension (:23). No repo rule picks a side; the
   generator's globally-unique ids satisfy both readings.
2. **ImputedRace code-3 parenthetical** — demographic.md:93 preserves
   a contradiction in the source documentation (code 3's parenthetical
   duplicates code 4's text). Outside the pipeline read set; no action.
3. **Result_Loc** — laboratory-result.md:180 states both "Missing:
   yes" and "There are not any null values". Not in the pipeline read
   set; no action.

## Suspected production defects → decision briefs

No production file was touched. The following are filed as operator
decision briefs in `investigation/packages/ARBITRATION.md` (same
format as DB-2..DB-5):

- **DB-6** — `_att_dem`'s `sex IS NOT NULL` is vacuous (the collapsed
  column never yields NULL) and `demographics_missing` checks
  NULL-only where SAS's `missing()` is NULL-or-blank: blank-string-raw
  sex members are counted at attrition level 1 yet silently lost from
  pov1 (70_outputs.sql:29-31, 10_normalize.sql:72-75; the correct
  `is_missing` macro exists at 00_macros.sql:94-99 and is not applied).
- **DB-7** — NULL/absent race collapses to 'M' (Multi-racial), not
  SCDM's unknown code '0' (10_normalize.sql:43); race-conditioned
  studies and stratified outputs evaluate missing-race members as
  Multi-racial.
- **DB-8** — blank Pdx is unreachable for DX claims: 10_normalize.sql:159
  relabels NULL Pdx to 'X' before care-setting matching, so the
  grammar's explicit missing-Pdx token (`_` -> '') and the '' member
  of the DX expansion can never match; SCDM requires blank Pdx on
  AV/ED/OA claims. Needs SAS-macro ground truth before any change.
- **DB-9** — `enc_is`/NumIS is emitted without cited SAS basis
  (src/qrp/scdm.py:114-115 vs 74_utilization.sql:75, 96_baseline.sql).
- **DB-10** — the XX event/distindex asymmetry (C2) as a parity
  question for the operator to confirm against ms_codedistribution.sas.
- **DB-11** — tools/gen_synthetic.py canonical-domain defects: swapped
  plantype/payertype values, pdx P/S on ~80%-AV rows, fabricated death
  source 'D'; all flow into the /tmp/qrp_data/100k benchmark the
  original suites pin behavior on (fixing the generator changes
  benchmark content — operator call).
- **DB-12** — src/qrp/selfcheck.py doctor extract: pdx 'P' on ~half-AV
  diagnosis rows, so `qrp doctor` validates on claim shapes SCDM
  forbids.

## Scope evidence (AC.2)

Actual command output at the time of this review (the completeness
test re-runs the same command against the base recorded above; the
final-revision refresh lives in FINAL-VALIDATION.md's SCDM section):

```
$ git diff --name-only 7165d77..HEAD
investigation/packages/scdm-audit-notes/audit-a-raw.md
investigation/packages/scdm-audit-notes/audit-b-raw.md
investigation/packages/scdm-audit-notes/audit-c-raw.md
investigation/packages/scdm-audit-notes/audit-d-raw.md
investigation/probes/scdm_full_alignment_probe.py
investigation/witnesses/scdm-full-alignment-probe.txt
tests/properties/oracles/a_demographics_tie.py
tests/properties/oracles/a_optional_columns.py
tests/properties/oracles/c_glue.py
tests/properties/oracles/d_denom.py
tests/properties/oracles/d_stages.py
tests/properties/synth.py
tests/properties/test_pkg_d_age_days.py
tests/properties/test_pkg_d_denominators.py
tests/properties/test_pkg_d_optional_stages.py
tests/properties/test_scdm_alignment.py
tests/test_determinism.py
tests/test_optional_claim_columns.py
tests/test_pipeline.py
tests/test_regression_a4.py
tests/test_regression_c3.py
```

Every path is under `tests/` or `investigation/`; no production
`src/` or `tools/` file appears. The machine check lives in
tests/properties/test_scdm_alignment_complete.py::
test_scdm_audit_scope_is_tests_and_investigation_only, which pins the
same command to the review's closed range (7165d77..844a1da; the
operator rulings of 2026-11-03 authorize production changes beyond
that range and are recorded in ARBITRATION.md).

## Limitations

- Subset extracts (standing framing above) mean several reference
  rules are untestable through this suite by construction; the audits
  verdicted only modeled columns.
- The three documented ambiguities stay unresolved; no synthetic
  validation rule was created from any of them.
- The production-generator briefs (DB-11/DB-12) are unprobed beyond
  reading: their probes require operator-approved generator changes by
  definition.
- Parity is untouched: no SAS source was re-read for this review
  beyond the in-repo citations the audits verified, and no parity
  claim is added or removed.
