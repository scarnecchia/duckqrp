# Audit B — Dispensing + Diagnosis + Procedure (raw)

Repo `duckqrp` @ `discovery` tip 7165d77. Ground truth: SCDM 8.2.2 references
(`index.md`, `dispensing.md`, `diagnosis.md`, `procedure.md`). Read-only audit;
the only write is this file.

Verdict key: **aligned** · **aligned-with-annotation** · **misaligned-fixed**
(test-side edit proposed, not applied) · **probe-needed** (behavior not
observable through any current fixture; probe specified).

## 1. Verdict table

| table | reference consulted (file + variables) | fixtures/builders checked (file:line) | item | verdict | outcome suggestion |
|---|---|---|---|---|---|
| Diagnosis | diagnosis.md:47-56 (EncType AV/ED/IP/IS/OA), :85 (Pdx conditional) | synth.py:376, 470; 10_normalize.sql:159-169; 30_exposure.sql:486-490; 72_codedistribution.sql:81-84; 74_utilization.sql:45,75 | B1 EncType domain: `_ENCTYPES` lacks IS; `# AV / OA / IP / ED / XX` comment presents pipeline fallback as a value | **misaligned-fixed** | Append `"IS"` to `_ENCTYPES`; replace the enctype comment (exact text below); run the IS-claims probe |
| Diagnosis | diagnosis.md:82-93 (Pdx P/S/X/blank, IP+IS only) | synth.py:468-471 (generate), 609-613 (hand_fixture); c_glue.py:172-174; a_demographics_tie.py:119; 10_normalize.sql:159,166-167; 30_exposure.sql:457,490 | B2 Pdx drawn P/S on ANY enctype in `generate()` | **misaligned-fixed** | Blank pdx for AV/ED/OA draws (exact edit below); hand_fixture enveloping rows stay (annotation); secondary sites listed for the follow-up |
| Dispensing | dispensing.md:57-63 (Rx_CodeType ND/SN/SK/DM/DI/RN/AT), :29-39 (ProviderID .U) | synth.py:455, 695 (build_study), b_pkg_fixtures.py:77-78; d_fixtures.py:69; c_glue.py:68,115; 30_exposure.sql:76-82, 109-115, 130-136, 481-483; pipeline.py:1123,1213; config.py:1477; scdm.py:83-90 | B3 Rx_CodeType always "ND" in data AND study; ProviderID always populated | **probe-needed** (codetype) + annotation (ProviderID) | SN/SN probe specified below; ProviderID annotation text below |
| Dispensing | dispensing.md:41-47 (RxDate), :49-55 (Rx), :67-74 (RxSup), :75-80 (RxAmt) | synth.py:102-113, 447-460, 508-514, 597-608; 10_normalize.sql:81-105 | B4 remaining dispensing fields vs reference | **aligned** | Coverage note: null/zero branches of the cdm_dispensing filters are exercised by test_regression_a2, not by `generate()` |
| Procedure | procedure.md:33-36 (ADate), :47-56 (EncType), :58-61 (PX), :63-85 (PX_CodeType), :86-89 (OrigPX) | a_optional_columns.py:29-37 (only Procedure row in the suite); synth.py:125-134, 540, 626; c_glue.py:188; 10_normalize.sql:200-218 | B5 procedure fields + total absence of procedure draws in `generate()` | **aligned-with-annotation** | Fields of the one probe row are legal; annotate its cross-table ADate mismatch; absence of draws = coverage note (probe suggested for the enveloping path) |
| (fixtures) | — | test_regression_a2.py:98-145; test_regression_c3.py:63-101 | B6 regression fixture blocks under the SCDM lens | **aligned** | a2 zero-rxamt row is a deliberate boundary probe (keep); c3 out-of-domain literals are schema-only (`WHERE 1 = 0`), never data |

Counts: aligned 2 (B4, B6) · aligned-with-annotation 1 (B5) · misaligned-fixed 2
(B1, B2) · probe-needed 1 (B3).

## 2. Detail blocks (non-aligned items)

### B1 — Diagnosis EncType: IS missing from draws; 'XX' comment is stale — misaligned-fixed

**Reference.** diagnosis.md:47-56 — EncType Char(2), "Missing: never", values
exactly `AV`, `ED`, `IP`, `IS`, `OA`. There is no `XX` in SCDM. IS =
"Non-Acute Institutional Stay (hospice, SNF, rehab, nursing home, residential,
overnight non-hospital dialysis...)".

**Repo.**
- synth.py:376 — `_ENCTYPES = ("AV", "OA", "IP", "ED")` — IS never drawn, so
  every property-test dataset is IS-free although IS is in-domain and
  pipeline-relevant (it appears in the codedistribution expansion and in
  utilization counters).
- synth.py:122 — `enctype: str  # AV / OA / IP / ED / XX`. The `XX` is not a
  builder value (the dataclass always receives a member of `_ENCTYPES`); it is
  the repo's internal fallback for a NULL enctype column:
  10_normalize.sql:160 — `upper(COALESCE(CAST({opt_diagnosis_enctype} AS
  VARCHAR), 'XX')) AS enctype`. A subset-framing note: the synthetic extract
  omits SCDM columns the pipeline doesn't read, but enctype IS read — the
  comment conflates "value this builder emits" with "value the pipeline can
  produce downstream".

**Why IS draws are safe to add (by reading).** Every consumer treats IS like
the other four:
- Event claims: 30_exposure.sql:489 `AND (cs.enctype = '**' OR cs.enctype =
  x.enctype)` — the wildcard builder studies (synth.py:708-709, c_glue.py:70-71,
  d_fixtures.py:55-57 all emit `("**", "*")`) match IS identically.
- Care-setting attribution: same join; a restricted token "ISP" parses to
  `('IS','P')` via config.py:118-130.
- Codedistribution membership: 72_codedistribution.sql:81-84 — `'**'` expands
  to `['IP', 'IS', 'ED', 'AV', 'OA']`; IS is already a first-class entity.
- Utilization (encounter-absent fallback reads diagnosis claims:
  74_utilization.sql:45): 74_utilization.sql:75 counts `enc_is` like the rest.
- Enveloping: 10_normalize.sql:166-167 rewrites any non-IP claim on an IP day
  to IP/'X' — IS claims envelop exactly like AV/ED/OA.

**Proposed edits (test-side; NOT applied).**

Edit 1 — synth.py:376 (append, so existing seeds' draw indices are unchanged):
```
old: _ENCTYPES = ("AV", "OA", "IP", "ED")
new: _ENCTYPES = ("AV", "OA", "IP", "ED", "IS")
```

Edit 2 — synth.py:122 (stale comment; exact new text):
```
old:     enctype: str                  # AV / OA / IP / ED / XX
new:     enctype: str                  # AV / ED / IP / IS / OA (SCDM domain);
                                       # 'XX' is the pipeline's fallback for a
                                       # missing column, never a builder value
```
(If the follow-up prefers a single line: `# AV / ED / IP / IS / OA — 'XX' is
10_normalize's null-column fallback, not a value this builder emits.`)

**Probe spec (diagnosis-claim IS rows; the encounter-table IS probe belongs to
another audit).** Build a dataset where one patient has: (a) an IS diagnosis
claim on a day inside an IP encounter stay, (b) an IS claim outside any stay,
(c) one AV claim inside the same stay as a control. Run two studies:
1. wildcard care setting (`("**", "*")`) — expect (a) enveloped to
   enctype `IP`/pdx `X` (10_normalize.sql:166-167), (b) flows to event_claims
   as IS, (c) appears in the distindex DX entity whose enctype expansion is
   `IS` (72_codedistribution.sql:81-84), and, with the encounter table absent,
   raises `enc_is` (74_utilization.sql:45,75);
2. restricted care setting token `ISP` (config.py:118-130 → `('IS','P')`) —
   expect claim (b) to become an event only when its pdx is `P`, and claims
   (a)/(c) (enveloped/non-matching) to be excluded.
Assert IS behaves identically to an identically-placed OA control pair. If
(2) surprises, the divergence is in care-setting attribution, not the
generator.

### B2 — Diagnosis Pdx drawn on non-IP/IS claims — misaligned-fixed (align the generator)

**Reference.** diagnosis.md:85 — "Principal discharge diagnosis flag. Populate
only for `IP` and `IS` encounters. For `ED`, `AV`, and `OA` encounter types,
set as missing/blank." Values P/S/X/blank (diagnosis.md:87-93).

**Repo.**
- synth.py:468-471 — `generate()` draws `pdx=rng.choice(("P", "S"))`
  unconditionally while `enctype=_pick(rng, _ENCTYPES)` may be AV/ED/OA:
  out-of-domain rows presented as canonical data. Nothing in synth.py labels
  this deliberate (contrast the explicitly-labeled "Deliberate ties" section,
  synth.py:11-23, 495-514), and the module docstring (synth.py:1-9) frames the
  builder as typed SCDM data cross-checked against the schema.
- Pipeline pdx usage (grep src/qrp): pdx is only ever MATCHED, never validated
  — care-setting joins 30_exposure.sql:490 and 85_riskscores.sql:72
  (`cs.pdx = '*' OR cs.pdx = claim.pdx`), distribution labeling
  72_codedistribution.sql:49,89-94, and the normalize fallback/enveloping
  10_normalize.sql:159,166-167. Under the wildcard `("*", "*")` every builder
  study emits, pdx values never change any outcome — so the edit below is
  behavior-neutral for all current tests while removing wrong-shaped data that
  would mask a future defect if any stage began keying off Pdx.
- COALESCE-to-'X' coverage is NOT lost by blanking draws: the 'X' fallback
  (10_normalize.sql:159) is exercised by extracts lacking the pdx column
  (pipeline.py:1193 optional-column resolution; test_regression_a2's
  column-absent pattern), not by random P/S draws.
- Carve-out (annotation, never a change): synth.py:610-612 — the hand_fixture
  AV claims with pdx "S"/"P" are the IP-stay enveloping probes consumed by
  10_normalize.sql:164-169 (re-filing non-IP claims on IP days to IP/'X');
  the harness docstring (synth.py:560-565) pins the expected enveloped result.
  They stay exactly as-is.

**Proposed edit (test-side; NOT applied)** — synth.py:462-471:
```
old:         # 1-3 diagnoses, ~40% outcome codes.
         for _ in range(1 + rng.randrange(3)):
             adate = span_start + timedelta(days=rng.randrange(max(
                 (span_end - span_start).days, 1)))
             code = _pick(rng, EVENT_CODE_POOL) if rng.random() < 0.4 \
                 else _pick(rng, NOISE_DX_POOL)
             dx.append(Diagnosis(
                 patid=patid, adate=adate, dx=code, dx_codetype="09",
                 pdx=rng.choice(("P", "S")), enctype=_pick(rng, _ENCTYPES),
             ))

new:         # 1-3 diagnoses, ~40% outcome codes. Pdx exists only on IP/IS
         # claims (diagnosis.md: "Populate only for IP and IS encounters;
         # for ED, AV, and OA encounter types, set as missing/blank").
         for _ in range(1 + rng.randrange(3)):
             adate = span_start + timedelta(days=rng.randrange(max(
                 (span_end - span_start).days, 1)))
             code = _pick(rng, EVENT_CODE_POOL) if rng.random() < 0.4 \
                 else _pick(rng, NOISE_DX_POOL)
             enctype = _pick(rng, _ENCTYPES)
             pdx = rng.choice(("P", "S")) if enctype in ("IP", "IS") else ""
             dx.append(Diagnosis(
                 patid=patid, adate=adate, dx=code, dx_codetype="09",
                 pdx=pdx, enctype=enctype,
             ))
```

**Annotation for the carved-out rows** (goes as a comment above
synth.py:611-612): `# Deliberate out-of-SCDM Pdx values: these AV claims carry
P/S to probe IP-stay enveloping (10_normalize.sql rewrites non-IP claims on IP
days to enctype IP / pdx 'X'). Real SCDM AV claims carry blank Pdx
(diagnosis.md:85); do not copy these values as canonical.`

**Secondary sites with the same out-of-domain pattern** (for the follow-up to
treat consistently — same fix or the same annotation; none is load-bearing
today because all builder studies use wildcard care settings):
c_glue.py:173 (`Diagnosis(patid, d(off), code, "09", "P", enctype)` — P on any
enctype the caller passes); a_demographics_tie.py:119 (pdx "P" on an AV claim);
selfcheck.py:152-153 (production smoke: pdx 'P' for both IP and AV draws);
test_pkg_d_age_days.py:31, test_pkg_d_denominators.py:165,232 (pdx "S" on AV
noise claims).

### B3 — Dispensing Rx_CodeType + ProviderID — probe-needed / annotation

**Codetype (probe).** Reference: dispensing.md:57-63 — Rx_CodeType Char(2),
domain `ND, SN, SK, DM, DI, RN, AT`; missing not allowed. Repo:
- synth.py:455 always `rx_codetype="ND"`; hand_fixture 597-608 all "ND";
  b_pkg_fixtures.py:77-78 parameterizes (`codetype: str = "ND"`) but callers
  leave the default; every study builder hardcodes ("RX","ND"):
  synth.py:695, c_glue.py:68,115, d_fixtures.py:69. So the suite never leaves
  the ND/ND diagonal.
- Matching is an equality comparison with match-any fallbacks:
  30_exposure.sql:76-82 (`k.codetype = '' OR k.codetype IS NULL OR d.codetype
  IS NULL OR upper(d.codetype) = k.codetype`), repeated at 109-115 (PX),
  130-136 (DX exposure), 481-483 (event claims). Config side uppercases at
  load: config.py:1477 (`str(r.get("codetype") or "").strip().upper()`); there
  is NO whitelist of codetype values in validation, so a study may configure
  "SN". The source column is detected as the first of
  ("rx_codetype","codetype","ndc_codetype") (pipeline.py:1123) and a missing
  column becomes a NULL literal (pipeline.py:1213), which matches anything.
- Consequence: an SN-labeled draw IS observable — SN in data + SN in study is
  captured; SN in data + ND in study is silently excluded (that exclusion
  branch is the never-exercised behavior).

**Probe spec.** One patient, two E00001 fills (day 0 and day 40), first
`rx_codetype='SN'`, second `'ND'`; study exposure code `("E00001","RX","SN",
None)` (build the cohort directly with `dataclasses.replace`, as
d_fixtures.make_cohort does, since synth.build_study hardcodes ND). Expect:
exposure_claims contains exactly the SN fill (30_exposure.sql:82 drops the ND
fill), one index date sourced from day 0, and the ND fill absent from
stockpiled/distindex. Control: flip the study to ND and assert the complement.
(The match-any NULL-source branch is already exercised by
test_regression_a2.py's codetype-less extract.)

**ProviderID (annotation).** Reference: dispensing.md:31-35 — ProviderID
ordinary missing not allowed in SAS7BDAT, `.U` allowed → ordinary null in
Parquet (index.md:37). Repo: synth.py:112 `providerid: int` always populated
(457 `providerid=1000+patid`; 598-607 `1001..1005`); the pipeline never reads
it — scdm.py:83-90 dispensing TableSpec required=`(patid, rxdate, rx, rxsup)`,
optional=`(rxamt, rx_codetype)`, no providerid anywhere, and cdm_dispensing
(10_normalize.sql:81-105) never selects it. Annotation text (goes beside
synth.py:112): `# ProviderID is inert here: the pipeline never reads it
(scdm.py dispensing spec), so the .U/null case SCDM allows is unexercised and
unobservable through this pipeline.`

### B5 — Procedure — aligned-with-annotation

**Reference.** procedure.md: PX Char(#) missing never (:58-61); PX_CodeType
Char(2) domain incl. `HC` = HCPCS Level II (:78); ADate Num(4) missing never
AND "Must match `ADate` in the Encounter table for the same
`PatID`/`EncounterID` combination" (:33-36); EncType same five-value domain as
diagnosis (:47-56); OrigPX missing allowed (:86-89); spelling caveat Px/PX —
"pick one spelling and apply it consistently" (:13).

**Repo — the only Procedure row in the entire suite** is
a_optional_columns.py:31: `Procedure(1, date(2014, 3, 12), "PX1", "HC", "AV",
10, 1001, None)`. Field by field:
- px `"PX1"` non-empty — aligned (missing never).
- px_codetype `"HC"` — legal domain value — aligned.
- adate present — aligned on nullability; BUT the row carries
  encounterid 10, and hand_fixture's only encounter for patid 1 is
  `Encounter(1, date(2014, 5, 1), "IP", 10, ...)` (synth.py:615-616) —
  procedure ADate 2014-03-12 ≠ encounter ADate 2014-05-01, violating the
  cross-table rule at procedure.md:36. Annotation (goes above
  a_optional_columns.py:31): `# NOTE: this probe row violates SCDM's
  cross-table ADate rule (procedure.md:36 — procedure ADate must match the
  Encounter ADate for the same PatID/EncounterID; hand_fixture encounter 10 is
  2014-05-01). Unobservable here: cdm_procedure never joins the encounter
  table on EncounterID (10_normalize.sql:200-218 envelops on patid+adate).`
- enctype `"AV"` — legal; and because 2014-03-12 is outside the IP stay, the
  enveloping rewrite (10_normalize.sql:211-218) correctly leaves it untouched.
- origpx `None` — aligned (missing allowed).
- encounterid/providerid columns — subset framing: present-and-populated here;
  the SQL never reads them (see annotation above). Not defects.
- `generate()` emits no procedures at all (synth.py:540 `procedures=()`;
  hand_fixture synth.py:626; c_glue.py:188) — this is a COVERAGE note, not a
  misalignment: `generate()`'s contract is dispensing/diagnosis traffic, and
  procedure is an optional table (scdm.py:106-113 `optional_table=True`).
  Consequence worth an operator's attention: cdm_procedure's enveloping
  rewrite (10_normalize.sql:211-218, procedure claims on IP days → enctype
  `IP`/pdx `X`) has ZERO data coverage anywhere in the suite. Suggested probe
  row (test-side, optional): a Procedure on an IP-stay day in
  a_optional_columns.probe_dataset, expecting view output enctype `IP`, pdx
  `X`, and the outside-stay row keeping pdx `''` (10_normalize.sql:215-216).

### B6 — Regression fixture blocks — aligned

- test_regression_a2.py:98-99 — dispensing schema `patid, rxdate, rx, rxsup`
  (codetype/rxamt deliberately absent; the point of the regression); rows
  `(1, 2011-09-15, "E00001", 30)` :108 and `(1, 2011-09-15, "E00001", 30,
  0.0)` + `(1, 2011-09-20, "E00001", 7, 10.0)` :132-133. The zero-rxamt row is
  a deliberate boundary probe of SAS's `rxamt > 0` rule (10_normalize.sql:95-105)
  with the nonzero fill kept — correctly labeled by its docstring :124-126.
  No out-of-domain value is presented as canonical. Aligned.
- test_regression_c3.py:93-100 — diagnosis VALUES `(1, '2014-01-01', 'X999',
  'ICD10', 'N', 'OA')` contain two out-of-domain literals (`'N'` as pdx —
  Pdx domain is P/S/X/blank; `'ICD10'` as dx_codetype — SCDM writes `10` for
  ICD-10-CM), but the INSERT carries `WHERE 1 = 0`: the table is created
  schema-only and zero rows exist. Nothing materialized, nothing canonical —
  cosmetic only. If touched for other reasons, change to `'09'`/`'S'` and
  drop nothing else. The dispensing row :85 `(1, '2014-06-01', 'E00001', 30,
  2.0)` is in-domain. Aligned.

## 3. UNRESOLVED ambiguities

1. procedure.md:13 spelling caveat — "the description and unique-row text
   spell the code variables `Px` and `Px_CodeType`, while the variable table
   uses `PX` and `PX_CodeType`. Pick one spelling and apply it consistently."
   The repo consistently uses lowercase `px`/`px_codetype` (synth.py:265-274,
   scdm.py:109, 10_normalize.sql:204-205) and DuckDB is case-insensitive
   (scdm.py:10-15), so this is resolved-by-consistency — recorded here only to
   show it was considered, not as an open question.
2. NumIS provenance: scdm.py:114-115 quotes SAS counting "NumAV, NumOA, NumIP,
   NumED" from the encounter table (ms_computeutilization.sas:139), yet the
   pipeline also computes `enc_is`/`NumIS` (74_utilization.sql:75,
   pipeline.py:220, 96_baseline.sql:113). Whether SAS has a NumIS output the
   comment merely omits cannot be resolved from this repo — carried to the
   production briefs below.

No reference-stated ambiguity in dispensing.md/diagnosis.md/procedure.md was
found that the repo resolves by invention.

## 4. Suspected PRODUCTION defects (operator decision briefs — never fixes)

1. **Blank Pdx is unreachable for diagnosis claims; the missing-Pdx care
   setting can never match.** 10_normalize.sql:159 relabels a NULL (i.e.
   blank) Pdx to `'X'` (`upper(COALESCE(..., 'X'))`) before any consumer. But
   SCDM REQUIRES blank Pdx on AV/ED/OA claims (diagnosis.md:85), and the
   caresettingprincipal grammar explicitly encodes "missing PDX" as `_` →
   `''` (config.py:126-127), matched against claims at 30_exposure.sql:490
   (`cs.pdx = '*' OR cs.pdx = COALESCE(x.pdx, '')`) and 85_riskscores.sql:72;
   the DX wildcard expansion likewise carries a `''` member
   (72_codedistribution.sql:89). Post-normalize, cdm_diagnosis.pdx is never
   NULL and never `''`, so for DX claims the `''`/missing-Pdx branches are
   dead: a study token like `AV_` that SAS reads as "AV, blank Pdx" matches
   nothing here, and blank-Pdx AV claims are attributed to the `X` entity in
   distindex where SAS would file them under the blank entity. Either the 'X'
   COALESCE needs SAS-macro ground truth (does ms_caresettingprincipal /
   ms_codedistribution see blank or 'X'?), or the fallback belongs after
   care-setting matching. Evidence: src/qrp/sql/10_normalize.sql:159,
   164-169; src/qrp/config.py:126-130; src/qrp/sql/30_exposure.sql:457,489-490;
   src/qrp/sql/72_codedistribution.sql:49,89-94.
2. **NumIS emitted without cited SAS basis** (the unresolved item above):
   src/qrp/scdm.py:114-115 vs src/qrp/pipeline.py:219-221 +
   src/qrp/sql/74_utilization.sql:75 + src/qrp/sql/96_baseline.sql:112-113.
   If SAS produces no NumIS, baseline/utilization output carries a column SAS
   does not; if it does, the scdm.py comment under-documents. Needs the SAS
   macro, not a repo-local decision.

(No production defect found in the dispensing path: the cdm_dispensing
filters at 10_normalize.sql:93-105 match dispensing.md's expectations and the
SAS rule they cite, and the rxamt-optional resolution is regression-pinned by
test_regression_a2.py.)

## 5. End-state alignment summary

**Dispensing.** After the B3 probe and ProviderID annotation, the synthetic
dispensing domain is a faithful positive-value subset of dispensing.md:
PatID/RxDate/Rx/Rx_CodeType/RxSup/RxAmt all satisfy their reference rules
(missing-never fields always populated, positive supplies 7-90 days and
amounts 1.0-60.0), and the cdm_dispensing filters (rxdate non-null, rxsup>0,
SAS's rxamt>0 rule with the column-absent resolution) are exactly the
reference semantics, with the zero/absent branches pinned by
test_regression_a2. The only unexercised behavior is codetype equality/
mismatch, which the specified SN/SN probe closes; ProviderID remains inert
payload the pipeline never reads.

**Diagnosis.** After the B1+B2 edits, `generate()` draws the full SCDM EncType
domain (AV/ED/IP/IS/OA) and populates Pdx only on IP/IS claims, matching
diagnosis.md exactly; the hand_fixture enveloping probes keep their
deliberate out-of-domain Pdx values behind an explicit annotation, and the
'XX' fallback is documented as pipeline-internal rather than a data value.
The IS-claims probe then verifies end-to-end what reading already supports:
IS flows through event claims, care-setting attribution, codedistribution
membership, utilization counting, and enveloping identically to the other
four settings.

**Procedure.** Field-wise the suite's single Procedure row is legal against
procedure.md (non-empty PX, legal HC codetype, legal AV setting, optional
OrigPX null), with one cross-table ADate mismatch annotated as unobservable
through this pipeline. The remaining gap is coverage, not alignment:
`generate()` never emits procedures, so cdm_procedure's enveloping rewrite
has no data coverage — a one-row on-stay/off-stay probe in
a_optional_columns closes it without changing any builder contract.
