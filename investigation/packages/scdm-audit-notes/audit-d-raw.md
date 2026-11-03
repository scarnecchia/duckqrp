# Audit D — Existing suites + production generators (raw)

Scope: fixture/data-building blocks in the six main test files, the seven `test_regression_*.py` files,
and the two production mock generators (`tools/gen_synthetic.py`, `src/qrp/selfcheck.py`).
Repo `/home/scarndp/dev/duckqrp`, branch `discovery`, tip `7165d77`. Read-only audit; this file is the only write.

## Method note: DuckDB DOUBLE→INTEGER casts round, not truncate

Several pools are drawn via `1 + CAST(random()*N AS INTEGER)`. Verified empirically against the repo venv
(duckdb 2.0.0): `CAST(0.5 AS INTEGER)=1`, `CAST(1.5 AS INTEGER)=2`, `CAST(1.7 AS INTEGER)=2`;
`CAST(random()*2 AS INTEGER)=2` occurs in ~25% of 100k rows; `CAST(random() AS INTEGER)=1` in ~50%.
Consequences used throughout the verdicts below:

- Every `random()*K` list index in `tools/gen_synthetic.py` reaches **every** element of its pool — full
  coverage, no unreachable codes (an earlier truncation-based reading that would have implied systematic
  off-by-one pool gaps is wrong and is retracted; no such defect exists).
- `src/qrp/selfcheck.py`'s `['IP','AV'][1 + CAST(random() AS INTEGER)]` (selfcheck.py:152) is a genuine
  ~50/50 mix, so its `pdx='P'` lands on ambulatory rows — see PD-4.

Domains used (SCDM 8.2.2 references): Sex {A,F,M,U}; Hispanic/ImputedHispanic {N,Y,U}; Race/ImputedRace
{0,1,2,3,4,5,M}; MedCov {Y,N,U,A}; DrugCov {Y,N,U}; Chart {Y,N}; PlanType {F,H,E,P,S,M,O,U}; PayerType
{CO,MC,MD,OG,OT,UN}; EncType {AV,ED,IP,IS,OA}; PDX {P,S,X,' '} populated only for IP/IS; Dx_Codetype
{09,C9,10,11,SM,OT}; Rx_CodeType {ND,SN,SK,DM,DI,RN,AT}; PX_CodeType {09,10,11,C2,C3,C4,CP,CV,CX,H3,HC,LC,LO,ND,OT,RE,SK};
Discharge_Status {AF,AL,AM,AW,EX,HH,HO,HS,IP,NH,OT,RH,RS,SH,SN,UN}; Death.Source {L,N,S,T,O,M};
Death.DtImpute {B,D,M,N}; Death.Confidence {E,F,P}; Lab Result_Type {N,C,U}; Fast_Ind {F,R,X,U};
MS_Test_Name enumerated (incl. CREATININE/SODIUM/GLUCOSE); Specimen_Source enumerated (incl. SERUM/SR_PLS);
MS_Result_C {BORDERLINE,NEGATIVE,POSITIVE,UNDETERMINED,range} null for Result_Type='N';
MS_Test_Sub_Category enumerated {BHCG,CLC,DDU,DIRECT,EIA,FEU,HCG,IA_RAP,IF,NS,PCR,SEQ,VTC}.

## Findings table

| table | reference consulted (file + variables) | fixtures/builders checked (file:line) | item | verdict | outcome suggestion |
|---|---|---|---|---|---|
| demographic | demographic.md (Sex, Race, Hispanic, ImputedRace, ImputedHispanic) | tools/gen_synthetic.py:47-58 | sex F/M, race '0'-'5' (never 'M'), hispanic Y/N/U; imputed = copies | aligned | none |
| demographic | demographic.md (unique row PatID; Sex) | tools/gen_synthetic.py:165-178 | deliberate duplicate-PatID tie rows with flipped sex | aligned-with-annotation | comment already labels it a "genuine tie"; add one line that this intentionally violates the PatID unique-row rule so future readers don't copy it as canonical |
| enrollment | enrollment.md (MedCov, DrugCov, Chart, Enr_Start/Enr_End) | tools/gen_synthetic.py:70-79 | medcov 'Y', drugcov Y/N, chart 'Y', spans from 2010 | aligned | none |
| enrollment | enrollment.md (PlanType {F,H,E,P,S,M,O,U}; PayerType {CO,MC,MD,OG,OT,UN}) | tools/gen_synthetic.py:80-81 | plantype='MC' (a PayerType value), payertype='P' (a PlanType value) — domains swapped | probe-needed | PD-1 below; frozen file, decision brief only |
| dispensing | dispensing.md (Rx_CodeType {ND,...}, RxSup, RxAmt positive) | tools/gen_synthetic.py:94-104 | rx_codetype 'ND', rxsup 10-90, rxamt 30-90 | aligned | none |
| dispensing | dispensing.md (unique row PatID,RxDate,Rx_CodeType,Rx,ProviderID) | tools/gen_synthetic.py:154-164 | deliberate duplicate dispensings (same key, rxamt+1) | aligned-with-annotation | comment labels the tie purpose; add one line noting it intentionally violates the dispensing unique-row rule |
| diagnosis | diagnosis.md (Dx_Codetype {09,...}, DX, EncType) | tools/gen_synthetic.py:117-126 | dx_codetype '09', synthetic DX pools, enctype IP/AV | aligned | none |
| diagnosis | diagnosis.md (PDX populated only for IP/IS; blank for AV/ED/OA) | tools/gen_synthetic.py:127-128 | pdx 'P'/'S' on ~80%-AV rows, unlabeled | probe-needed | PD-2 below; frozen file, decision brief only |
| death | death.md (DtImpute {B,D,M,N}; Confidence {E,F,P}; Source {L,N,S,T,O,M}) | tools/gen_synthetic.py:142 | dtimpute 'N', confidence 'E' in-domain; source 'D' outside the six-value domain | probe-needed | PD-3 below; frozen file, decision brief only |
| procedure | procedure.md (PX_CodeType {C4,...}, EncType) | tools/gen_synthetic.py:187-197 | px_codetype 'C4', enctype IP/AV/ED (all reachable), px 'P00001'-'P00019' | aligned | none |
| lab_result | laboratory-result.md (Result_Type, MS_Test_Name, Specimen_Source, Fast_Ind, Pt_Loc, MS_Result_N ≥ 0, LOINC) | tools/gen_synthetic.py:203-217 | result_type N/U/C, CREATININE/SODIUM/GLUCOSE, SR_PLS, X/R/F, MG/DL+MMOL/L, pt_loc 'O', ms_result_n 0-200; result_dt/order_dt NULL deliberate (commented) | aligned | none |
| demographic | demographic.md | src/qrp/selfcheck.py:127-133 | sex F/M (genuine mix per rounding note), hispanic 'U', race '0', postalcode_date NULL | aligned | none |
| enrollment | enrollment.md | src/qrp/selfcheck.py:134-138 | medcov 'Y', drugcov 'Y', chart 'N', 2010-2015 | aligned | none |
| dispensing | dispensing.md | src/qrp/selfcheck.py:139-145 | rx_codetype 'ND', rxsup/rxamt 30 | aligned | no providerid column — subset extract, in-scope per audit rules |
| diagnosis | diagnosis.md (PDX only for IP/IS) | src/qrp/selfcheck.py:152-153 | pdx always 'P' while enctype mixes IP/AV ~50/50 — PDX on ambulatory claims | probe-needed | PD-4 below; frozen file, decision brief only |
| death | death.md | src/qrp/selfcheck.py:155 | empty table (LIMIT 0), no source columns | aligned | subset extract |
| (dataset lineage) | all | tests/test_pipeline.py:30 (DATA=/tmp/qrp_data/100k); tests/test_regression_e1.py:34; tests/test_ui.py | suites run against the generator's dataset, so PD-1..PD-3 values flow through the regression suites as canonical | aligned-with-annotation | note in the decision briefs that fixing the generator changes benchmark content; suites pin behavior on the current (defective) extract |
| diagnosis/procedure | diagnosis.md, procedure.md (PDX flag tokens {P,S,X,''}) | tests/test_pipeline.py:7890-7898 | claim-view rows: AV+pdx '' (canonical), IP+pdx 'X' (canonical), AV+pdx 'S' (not canonical; drives expected id 14 in token expansion) | aligned-with-annotation | annotate the AV+'S' row: flag 'S' exists to exercise the '*'→P S X '' expansion, real AV claims carry blank PDX |
| diagnosis/procedure | diagnosis.md (enveloping keeps own flag) | tests/test_pipeline.py:8024-8032, 8051-8055 | AV rows carry pdx 'P' and are asserted to stay ('AV','P') through the normalize view | aligned-with-annotation | annotate: rows use 'P' to prove flag preservation; real AV claims have blank PDX, so the ('AV','P') output shape is not SCDM-canonical |
| encounter | encounter.md (Discharge_Status 16 values, case) | tests/test_pipeline.py:8176-8179, 8190 | discharge_status 'EX'/'HO' in-domain; 'ex' lowercase expected to count as death — unlabeled case-tolerance probe | aligned-with-annotation | add comment at :8178: deliberate case-insensitivity probe, not a domain example |
| lab_result | laboratory-result.md (LAB01 seven-attribute combination) | tests/test_pipeline.py:3752-3757 | combination values in-domain; specimen_source 'NOPE' explicitly probes rejection (`assert == 0`) | aligned | labeled deliberate probe |
| demographic | demographic.md (denominator strata) | tests/test_pipeline.py:5759 | sex 'F', race '1', hispanic 'Y' in cohort config | aligned | none |
| dispensing (tie shapes) | dispensing.md | tests/test_determinism.py:37-60 | duplicate claims differing in rxamt only; docstring names the real-world shape | aligned | none |
| demographic (tie) | demographic.md (unique row PatID) | tests/test_determinism.py:118-130, 144 | duplicate PatIDs with sex M/F, race '1', hispanic 'N' | aligned-with-annotation | docstring labels the tie; add one line noting it intentionally violates PatID uniqueness |
| (none — rewrite unit fixtures) | n/a | tests/test_rewrites.py:81-175 | generic (patid, day, supply) tables, no SCDM-coded values | aligned | none |
| (none — SAS oracle) | n/a | tests/_stockpile_reference.py | pure Python oracle over (date, rxsup, rxamt) | aligned | none |
| diagnosis | diagnosis.md (PDX only for IP/IS) | tests/test_optional_claim_columns.py:41 | canonical-shaped row (dx '10', pdx 'P', enctype 'AV') presented as the extract | misaligned-fixed | edit below (OC-1) |
| lab_result | laboratory-result.md (Fast_Ind, MS_Test_Sub_Category, MS_Result_C, Result_Type) | tests/test_optional_claim_columns.py:43-44 | padded/lowercase trim probes that normalize into domain (' positive '→POSITIVE, ' creatinine '→CREATININE, ' serum '→SERUM, ' mg/dl ') — but fast_ind 'Y' ∉ {F,R,X,U} and sub_category ' chemistry ' matches no domain value | misaligned-fixed | edits below (OC-2, OC-3); annotation for the trim probes and for result_type 'N' with ms_result_c populated |
| dispensing | dispensing.md (RxAmt positive expected) | tests/test_regression_a2.py:132 | rxamt 0.0 row — deliberate probe of the documented SAS exclusion rule (labeled "# zero: excluded", docstring cites ms_cidanum.sas:617) | aligned-with-annotation | annotation optional; already labeled |
| dispensing | dispensing.md (Rx_CodeType) | tests/test_regression_a2.py:31-33, 98-99 | dispensing carries no codetype column; comment explains match-any fallback | aligned-with-annotation | already labeled in comment |
| diagnosis | diagnosis.md | tests/test_regression_a4.py:133 | canonical-shaped row (dx_codetype '10' valid, pdx 'P', enctype 'AV') asserted to flow through unchanged | misaligned-fixed | edit below (A4-1) |
| diagnosis | diagnosis.md (Dx_Codetype {09,...}, PDX {P,S,X,''}) | tests/test_regression_c3.py:93-100 | zero-row schema placeholder VALUES carry dx_codetype 'ICD10' (∉ domain) and pdx 'N' (∉ {P,S,X,''}) | aligned-with-annotation | values never materialize (`WHERE 1=0`); change literals to '10' and 'P' (or '') as hygiene so they can't be copied into real fixtures |
| enrollment, demographic, dispensing | enrollment.md, demographic.md, dispensing.md | tests/test_regression_f1.py:43-53, tests/test_regression_f2.py (same builder) | sex F/M, medcov/drugcov Y/Y, rxsup 30, rxamt 10.0 | aligned | none |
| (config only) | dispensing.md (Rx_CodeType) | tests/test_regression_a1.py:36 | cohortcodes codetype 'ND' matches Rx_CodeType domain | aligned | no SCDM fixtures in file |
| (none) | n/a | tests/test_regression_e1.py | runs generator dataset / empty root; no inline SCDM values | aligned | none |
| (none) | n/a | tests/test_ui.py:31,229,262,299 | drives TUI against demo study + /tmp/qrp_data/100k; no inline SCDM values | aligned | none |

Verdict counts: aligned 19 · aligned-with-annotation 10 · misaligned-fixed 3 (value pools; 2 files) · probe-needed 4 (all in frozen production files).

## Detail blocks (non-aligned items)

### OC-1 — misaligned-fixed — tests/test_optional_claim_columns.py:41
PDX 'P' on an AV encounter presented as the canonical extract row ("values must flow through unchanged").

```python
# old
"diagnosis": (1, date(2011, 9, 16), "D00001", "10", "P", "AV"),
# new
"diagnosis": (1, date(2011, 9, 16), "D00001", "10", "P", "IP"),
```
Behavior-neutral: the extract has no encounter table, so no enveloping occurs either way; the test's
assertion (omitted column ≡ NULL column) is independent of enctype. SCDM basis: diagnosis.md PDX —
"Populate only for `IP` and `IS` encounters. For `ED`, `AV`, and `OA` encounter types, set as missing/blank."

### OC-2 — misaligned-fixed — tests/test_optional_claim_columns.py:44 (fast_ind)
`'Y'` is not in Fast_Ind {F,R,X,U} and cannot normalize into it.

```python
# old (tail of the lab_result row)
..., " mg/dl ", "Y", "O")
# new
..., " mg/dl ", " X ", "O")
```
Keeps the whitespace/trim probe (the padded ' X ' still exercises normalization) while becoming in-domain.

### OC-3 — misaligned-fixed — tests/test_optional_claim_columns.py:44 (ms_test_sub_category)
`' chemistry '` normalizes to 'CHEMISTRY', which matches no MS_Test_Sub_Category value
{BHCG,CLC,DDU,DIRECT,EIA,FEU,HCG,IA_RAP,IF,NS,PCR,SEQ,VTC}; the reference also conditions sub-categories on
select MS_Test_Name values only.

```python
# old (middle of the lab_result row)
..., " creatinine ", " chemistry ", " serum ", ...
# new
..., " creatinine ", "", " serum ", ...
```
Matches the production generator's own convention (`'' AS ms_test_sub_category`, gen_synthetic.py:212).
The trim probe remains covered by ms_result_c/ms_test_name/specimen_source/ms_result_unit.

### A4-1 — misaligned-fixed — tests/test_regression_a4.py:133
Same defect shape as OC-1: `(1, date(2011, 9, 16), "D00001", "10", "P", "AV")` asserted to pass through
`cdm_diagnosis` unchanged, presenting AV+pdx 'P' as canonical.

```python
# old
[(1, date(2011, 9, 16), "D00001", "10", "P", "AV")],
# new
[(1, date(2011, 9, 16), "D00001", "10", "P", "IP")],
```
Behavior-neutral for the same reason as OC-1 (no encounter table → no enveloping; assertion is about
column resolution, not care setting).

### aligned-with-annotation items — proposed annotations
1. **tests/test_pipeline.py:7897** (AV+pdx 'S' claim-view row): add
   `# flag 'S' on AV is not SCDM-canonical (PDX is blank on ambulatory claims); it exists to exercise the '*' -> P S X '' token expansion`.
2. **tests/test_pipeline.py:8025-8026 / 8052**: add near the fixture:
   `# AV rows carry 'P' to pin flag preservation; real AV claims have blank PDX, so ('AV', 'P') output is not SCDM-canonical shape.`
3. **tests/test_pipeline.py:8178** (`discharge_status: "ex"`): add
   `# lowercase on purpose: probes case-insensitive EX matching; not a domain example.`
4. **tests/test_determinism.py:118-130**: docstring already labels the tie; append
   "the duplicate PatIDs intentionally violate the Demographic unique-row rule (PatID)".
5. **tools/gen_synthetic.py:165-178 and 154-164**: comments label the tie purpose; a one-line addition
   noting the intentional violation of Demographic/Dispensing unique-row rules would prevent the shapes
   being copied as canonical (suggestion only — file is frozen; fold into PD briefs if undesired).
6. **tests/test_regression_c3.py:96**: annotation `# schema-only; literals are placeholders, not data`
   (plus the optional literal hygiene fix 'ICD10'→'10', 'N'→'P').
7. **tests/test_optional_claim_columns.py:43-44**: annotation near the lab row:
   `# padded/lowercase on purpose: pins trim+upper normalization; ' positive '->POSITIVE etc.` plus
   `# result_type 'N' with ms_result_c populated is not SCDM-canonical (MS_Result_C must be null for numeric results) — trim probe only`.

### probe-needed items (production files frozen — exact probe specs)
- **PD-1 / PD-2 / PD-3**: see production-defect section; probe = re-run `qrp doctor` and one
  `test_pipeline.py` smoke run after an operator-approved generator fix, asserting (a) enrollment
  plantype ∈ {F,H,E,P,S,M,O,U} and payertype ∈ {CO,MC,MD,OG,OT,UN}, (b) `SELECT count(*) FROM cdm_diagnosis WHERE enctype NOT IN ('IP','IS') AND pdx <> ''` == 0, (c) death source ∈ {L,N,S,T,O,M}. No probe should
  assert plantype/payertype pipeline behavior until the operator decides whether the pipeline reads them.
- **PD-4**: probe = `SELECT enctype, count(*) FROM (diagnosis rows of a doctor run) GROUP BY 1` before and
  after fixing selfcheck's pdx column (e.g. `CASE WHEN enctype='IP' THEN 'P' ELSE '' END`), confirming the
  doctor path tolerates blank PDX on AV claims.

## UNRESOLVED ambiguities

None affecting these verdicts. The references' own caveats (Race/ImputedRace code-3 label contradiction;
Encounter unique-row EncounterID-alone-vs-PatID+EncounterID tension; Result_Loc "no null values" vs
missing-allowed; Px/PX spelling) were read and none is load-bearing for any fixture verdict above; per the
standing rules they stay unresolved rather than becoming validation rules.

## Suspected PRODUCTION defects (decision briefs, never fixes)

- **PD-1 — tools/gen_synthetic.py:80-81.** `'MC' AS plantype, 'P' AS payertype` — the two columns' values
  are swapped: 'MC' (Medicare) is a PayerType value, 'P' (PPO) is a PlanType value. The reference dataset
  for the whole repo (`/tmp/qrp_data/100k`) therefore presents both enrollment columns out-of-domain as
  canonical, and `test_pipeline.py`'s baseline runs inherit it. Masking risk is currently low only because
  the pipeline appears not to key on plantype/payertype — which is itself unverifiable from fixtures that
  have never carried legal values.
- **PD-2 — tools/gen_synthetic.py:127-128.** `CASE WHEN random() < 0.3 THEN 'P' ELSE 'S' END AS pdx` with
  `enctype` 'IP' 20% / 'AV' 80%: ~4 in 5 diagnosis rows carry a principal/secondary PDX flag on an
  ambulatory encounter, which diagnosis.md restricts to IP/IS. The benchmark's care-setting and
  codedistribution paths are exercised predominantly by claim shapes real extracts cannot produce.
- **PD-3 — tools/gen_synthetic.py:142.** `'D' AS source` — Death.Source has six values {L,N,S,T,O,M}; 'D'
  is fabricated. Every death row in the benchmark carries an out-of-domain source as canonical.
- **PD-4 — src/qrp/selfcheck.py:152-153.** `'P' AS pdx` alongside `['IP','AV'][1 + CAST(random() AS INTEGER)]`
  (a genuine ~50/50 mix under DuckDB rounding): about half the doctor study's diagnosis rows are AV with a
  principal flag. `qrp doctor` — the first thing a data partner runs — validates the pipeline on claim
  shapes SCDM forbids, so an AV-with-blank-PDX regression would not be caught by it.

Non-domain observations (out of lens, one line each, no action proposed): demographic race pool never
draws 'M' (gen_synthetic.py:48); the death tables carry no DtImpute/Source/Confidence columns in
selfcheck's extract (subset, in-scope per audit rules).

## Per-file summary

- **tools/gen_synthetic.py** — Mostly aligned: demographic, dispensing, procedure and lab_result pools are
  fully in-domain and (per the rounding note) fully covered; both tie injections are labeled. Three
  out-of-domain value pools presented as canonical: enrollment plantype/payertype domain swap (PD-1),
  PDX on ~80%-AV diagnosis rows (PD-2), death source 'D' (PD-3). This file is the repo's reference
  dataset, so all three propagate into test_pipeline/test_ui/test_regression_e1 baselines.
- **src/qrp/selfcheck.py** — Demographic, enrollment, dispensing and the empty death table are aligned
  subset extracts. One defect: pdx 'P' on ~half-AV diagnosis rows (PD-4) in the doctor study's inline
  data. Study-config tables (`cohortfile`/`type2file`/`cohortcodes`) are not SCDM and out of lens.
- **tests/test_pipeline.py** — Inline fixtures are unit-level claim/config tables, mostly aligned; three
  annotation-worthy unlabeled probes (AV+'S' token expansion, AV+'P' flag preservation, lowercase 'ex'
  discharge_status); the lab LAB01/'NOPE' probes are labeled and fine. Its baseline data inherits PD-1..3.
- **tests/test_ui.py** — No inline SCDM fixtures; aligned.
- **tests/test_rewrites.py** — Generic rewrite fixtures, no SCDM-coded values; aligned.
- **tests/test_determinism.py** — Labeled tie fixtures with in-domain values; demographic tie duplicates
  PatID by design (annotation suggested); aligned-with-annotation.
- **tests/_stockpile_reference.py** — Pure SAS-transliteration oracle, no SCDM rows; aligned.
- **tests/test_optional_claim_columns.py** — Two misaligned-fixed items (AV+pdx 'P' diagnosis row;
  fast_ind 'Y' and sub_category ' chemistry ' in the lab row); the remaining padded probes normalize into
  domain and only need an annotation; result_type 'N' + ms_result_c populated noted as non-canonical.
- **tests/test_regression_a1.py** — Config-only; codetype 'ND' matches Rx_CodeType; aligned.
- **tests/test_regression_a2.py** — In-domain minimal extract; the zero-amount row and missing
  rx_codetype are labeled deliberate probes; aligned-with-annotation.
- **tests/test_regression_a4.py** — One misaligned-fixed item: pdx 'P' on enctype 'AV' asserted to flow
  through unchanged as canonical (A4-1).
- **tests/test_regression_c3.py** — Values written are in-domain; the zero-row schema placeholder carries
  out-of-domain example literals ('ICD10', pdx 'N') — aligned-with-annotation with hygiene fix.
- **tests/test_regression_f1.py / f2.py** — Minimal in-domain extracts; aligned.
- **tests/test_regression_e1.py** — No SCDM fixtures; aligned.
