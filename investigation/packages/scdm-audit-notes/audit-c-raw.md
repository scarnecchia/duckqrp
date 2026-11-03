# Audit C — Encounter + Death + Laboratory Result (raw)

Repo `/home/scarndp/dev/duckqrp`, branch `discovery`, tip `7165d77`. Read-only audit; references at
`/home/scarndp/.config/polytoken/skills/howto-work-with-scdm-data/references/` (SCDM 8.2.2). All
file:line references below were verified against the working tree.

| table | reference consulted (file + variables) | fixtures/builders checked (file:line) | item | verdict | outcome suggestion |
|---|---|---|---|---|---|
| Encounter | encounter.md (`EncType` L57-66, `DDate` L48-55, `Discharge_Status` L89-111) | synth.py:376, 473-487, 122, 470; 74_utilization.sql:75; 72_codedistribution.sql:81-84; d_stages.py:63,118,1584 | C1 — `IS` missing from synth EnType pool and encounter draws | misaligned-fixed | add `IS` to `_ENCTYPES` + an IS encounter branch; run probe P-C1 |
| Diagnosis/Encounter | encounter.md (`EncType` domain); 10_normalize.sql:160; 30_exposure.sql:486-490; 72_codedistribution.sql:79-96,130,156 | synth.py:122,470 (no XX draws); d_denom.py:271,489; d_stages.py:1719,1643 | C2 — 'XX'-enctype claims under a '**' cohort | aligned-with-annotation | annotate the XX asymmetry (events yes, distindex no) as SAS-parity; confirm with probe P-C2 |
| Encounter | encounter.md (`Discharge_Status` L89-111: 16 values; `EX`=Expired) | 10_normalize.sql:184-195; pipeline.py:1184-1189; test_pipeline.py:8151-8191; synth.py:473-487,614-618; d_denom.py:325-329,383-386; test_ac1_infrastructure.py:190 | C3 — EX discharge_status absent from properties generator | aligned-with-annotation | decision (b): existing-suite coverage suffices; add an annotation, do not add EX draws |
| Encounter | encounter.md L7, L21, L23 (uniqueness caveat) | synth.py:138-147,479,486; a_optional_columns.py:16; oracles (no uniqueness comments found) | C4 — unique-row key ambiguity (PatID+EncounterID vs EncounterID) | aligned-with-annotation | keep the both-readings-compatible id scheme; ambiguity stays UNRESOLVED |
| Death | death.md (all variables L15-62; COD table L9) | scdm.py:98-105; synth.py:85-87,236-239,489-493,618; 10_normalize.sql:177-182 | C5 — Death two-column subset vs death.md | aligned-with-annotation | confirm + subset annotation (COD/DtImpute/Source/Confidence outside read-set) |
| Laboratory Result | laboratory-result.md (`Result_Type` L74-83, `MS_Result_C` L256-267, `MS_Result_N` L269-272, `Fast_Ind` L106-116, `Pt_Loc` L162-173, `Specimen_Source` L118-143, `LOINC` L145-148, `Order_dt`/`Result_dt` L224-244, `MS_Test_Name` L29-72, `MS_Test_Sub_Category` L85-104, `MS_Result_unit` L299-302) | synth.py:150-170,516-532,283-299; 76_labs.sql:88-112; d_stages.py:256-355; a_optional_columns.py:32-36; 10_normalize.sql:220-259 | C6 — lab result_type conditional semantics violated by synth draws | misaligned-fixed | make (result_type, ms_result_n, ms_result_c, test name) triples SCDM-legal |

Verdict counts: aligned 0 · aligned-with-annotation 4 (C2, C3, C4, C5) · misaligned-fixed 2 (C1, C6) · probe-needed 0.

---

## C1 — EncType `IS` missing from the generators (misaligned-fixed)

**All claims confirmed.**

- SCDM domain: encounter.md:62-66 defines exactly five EnTypes: `AV`, `ED`, `IP`, `IS` ("Non-Acute Institutional Stay — includes hospice, skilled nursing facility (SNF), rehab center, nursing home, residential, overnight non-hospital dialysis, and other non-hospital stays"), `OA`.
- Pool omits IS: synth.py:376 `_ENCTYPES = ("AV", "OA", "IP", "ED")`.
- Encounter block omits IS: synth.py:476-487 draws only `enctype="IP"` (with `ddate`, `discharge_status=""`) or `enctype="AV"` (no ddate/discharge_status). Diagnosis claims draw from `_ENCTYPES` (synth.py:470), so diagnosis IS claims are also never generated; the dataclass comment (synth.py:122) documents the pool as "AV / OA / IP / ED / XX".
- Production counts IS: 74_utilization.sql:75 `count(*) FILTER (WHERE enctype = 'IS') AS enc_is`; header cites SAS's default list `AV OA IP IS ED` (74_utilization.sql:7-9).
- Codedistribution wildcard includes IS: 72_codedistribution.sql:81-84 `'**'` expands to `['IP', 'IS', 'ED', 'AV', 'OA']`.
- The oracles model IS while the generator never emits it — the coverage is dead on arrival: d_stages.py:63 `_ENC_COLS = ("AV", "OA", "IP", "IS", "ED")`, d_stages.py:118 `"enc_is": enc_counts["IS"]`, d_stages.py:1584 `_EXP_SETTINGS = ("IP", "IS", "ED", "AV", "OA")`. Every `enc_is` comparison therefore runs against an always-zero column, and the `IS` distindex entities are never matched by any generated claim.

**ENC-legal dates for an IS draw** (encounter.md:48-55): `ADate` is never missing; `DDate` — "Populate for all Inpatient Hospital Stay (`IP`) and Non-Acute Institutional Stay (`IS`) encounter types. … If `EncType` is `IP` or `IS`, the only missing value allowed is the special missing `.S` — actual discharge dates remain valid; use `.S` for patients still in the facility." So the IS draw must carry a populated `ddate` (a `None` ddate is legal only as the `.S` still-in-facility stand-in; keep the draw populated to stay unambiguous). `Discharge_Status` is legal on IS ("Populate for Inpatient Hospital Stay (`IP`) and Non-Acute Institutional Stay (`IS`) encounter types", encounter.md:111); `""` keeps the generator minimal and is how the existing IP draw already models "not drawn". Note: production enveloping reads IP rows only (10_normalize.sql:140 `WHERE upper(trim(CAST(enctype AS VARCHAR))) = 'IP'`), so an IS encounter contributes no `_ip_days` — that is correct SAS parity, mirrored by d_denom.py:247.

**Proposed edit 1 — synth.py:376**

old:
```python
_ENCTYPES = ("AV", "OA", "IP", "ED")
```
new:
```python
_ENCTYPES = ("AV", "OA", "IP", "IS", "ED")
```

**Proposed edit 2 — synth.py:473-487** (adds an IS encounter branch with ENC-legal dates; the free `encounterid` slot `patid * 10 + 2` keeps the existing both-readings-compatible uniqueness scheme from C4)

old:
```python
        if opt.include_encounters and rng.random() < 0.5:
            adate = span_start + timedelta(days=rng.randrange(max(
                (span_end - span_start).days, 1)))
            if rng.random() < 0.5:
                encs.append(Encounter(
                    patid=patid, adate=adate, enctype="IP",
                    encounterid=patid * 10,
                    ddate=adate + timedelta(days=rng.randrange(1, 6)),
                    discharge_status="",
                ))
            else:
                encs.append(Encounter(
                    patid=patid, adate=adate, enctype="AV",
                    encounterid=patid * 10 + 1,
                ))
```
new:
```python
        if opt.include_encounters and rng.random() < 0.5:
            adate = span_start + timedelta(days=rng.randrange(max(
                (span_end - span_start).days, 1)))
            kind = rng.random()
            if kind < 0.4:
                encs.append(Encounter(
                    patid=patid, adate=adate, enctype="IP",
                    encounterid=patid * 10,
                    ddate=adate + timedelta(days=rng.randrange(1, 6)),
                    discharge_status="",
                ))
            elif kind < 0.7:
                # IS stays are SCDM-legal only with a discharge date (or the
                # .S still-in-facility stand-in); see encounter.md DDate.
                encs.append(Encounter(
                    patid=patid, adate=adate, enctype="IS",
                    encounterid=patid * 10 + 2,
                    ddate=adate + timedelta(days=rng.randrange(1, 6)),
                    discharge_status="",
                ))
            else:
                encs.append(Encounter(
                    patid=patid, adate=adate, enctype="AV",
                    encounterid=patid * 10 + 1,
                ))
```
Determinism note: both edits change the number/order of `rng` draws, so every dataset from a given
`(patients, seed, options)` shifts. That is acceptable — the properties suite is oracle-derived, not
golden-file — but the follow-up must run the whole properties battery after applying, since shrinkers
and tie-injection victims move too.

**Probe P-C1 (execution confirms IS is counted and expands correctly):**

1. Hand-build a dataset: one IS encounter (`adate=D`, `ddate=D+3`, `discharge_status=""`, `encounterid`
   unique) and one IS diagnosis claim (`D00001`, `09`, `pdx="P"`, `enctype="IS"`, dated after the
   index), patient enrolled, exposure code with an index date before `D`. Study: `make_study2`/
   `study_with_features` with a MED utilization spec (any window covering `D`), event `D00001` with the
   loader wildcard `('**','*')` care setting (d_fixtures.wildcard_events), wash/enrol small so the
   patient reaches ptsmasterlist.
2. Run stages through `utilization` and `codedistribution` in the StageHarness.
3. Assert:
   - `utilization` row: `enc_is = 1` (the IS encounter), `enc_total` includes it, `enc_av/enc_oa/enc_ip/enc_ed` unchanged; `utilization_summary.mean_*` consistent.
   - IS does NOT envelop: a second AV-coded diagnosis claim dated inside the IS stay keeps `enctype='AV'`
     in `cdm_diagnosis` (no `_ip_days` hit), and d_denom's `_enveloped_claims` agrees.
   - `distindexmap` contains the hoi entity `('DX','09','IS','P','D00001')` with the id produced by the
     five-way expansion (d_stages.py:1584 expansion order), and NOT a literal `'**'` row.
   - With the IS claim as the first event: `distindex_episodes.distindexhoi` carries that id, and
     `distindex` groups the episode under the list containing it.
   - d_denom.expected_denomcounts and d_stages.expected_utilization / expected_distindex reproduce the
     engine rows byte-for-byte via `canonical()` — i.e. IS is provably counted on BOTH sides, not just
     present in output.

## C2 — 'XX'-enctype diagnosis claims (aligned-with-annotation)

**What the code says (fully determined by reading):**

- Origin: 10_normalize.sql:160 `upper(COALESCE(CAST({opt_diagnosis_enctype} AS VARCHAR), 'XX')) AS enctype`
  — 'XX' when the diagnosis extract lacks the column entirely (pipeline.py:1193 resolves
  `{opt_diagnosis_enctype}` to literal `NULL` when absent) or when a row's value is NULL.
- Event attribution (30_exposure.sql:486-490): `event_claims` INNER JOINs `cfg_care_setting` with
  `AND (cs.enctype = '**' OR cs.enctype = x.enctype)`. The config side keeps `'**'` literal, so under a
  wildcard ('**') cohort an 'XX' claim DOES match and IS counted as an outcome event.
- Codedistribution membership (72_codedistribution.sql:81-84, 130, 156): the wildcard is EXPANDED into
  `['IP','IS','ED','AV','OA']` entities and matched afterwards by exact equality (`i.enctype = m.enctype`).
  'XX' equals none of the five, so an 'XX' claim contributes NO id to `distindexexp`/`distindexhoi`, and
  `distindexmap` has no 'XX' row. A cohort whose only events are 'XX' claims therefore shows
  `numevents > 0` while its `distindexhoi` is the blank placeholder row (72_codedistribution.sql:169-177, 205-209).
- SAS parity: the in-repo citations quote both SAS behaviors — the caresettingprincipal match
  `(EncType = '**' OR EncType = claim.enctype)` (30_exposure.sql:401-405, ms_caresettingprincipal.sas) and
  the distindex expansion "'**' into IP IS ED AV OA" with exact matching after (72_codedistribution.sql:22-26,
  ms_codedistribution.sas). The asymmetry is the SAS behaviors composed, not a porting slip.
- Oracle parity: the oracles model exactly this divergence — d_denom.py:489 passes any enctype under a
  `'**'` care setting (`ce == "**" or ce == enctype`); d_stages.py:1719 `match_med` requires exact
  `et != claim["enctype"]` against the expanded entities (d_stages.py:1643). So an XX event yields a
  first event with an empty hoi list on BOTH sides — the suite stays green, by construction.
- The generator never draws 'XX' (synth.py:470 draws only from `_ENCTYPES`; the `'XX'` comment at
  synth.py:122 documents the fallback, not a draw), so this whole behavior is exercised today only via
  the optional-column omission probes (a_optional_columns.py:14 declares diagnosis `enctype` droppable).

**Annotation text (proposed placement: a comment on the care-setting filter in
tests/properties/oracles/d_denom.py near line 489, mirrored in d_stages.py near match_med 1719):**

> An 'XX'-enctype claim (missing/null enctype column, 10_normalize.sql:160) counts as an event under a
> '**' care setting (30_exposure.sql:489 keeps the literal wildcard) but matches no distindex entity,
> which expands '**' to the five concrete enctypes and matches exactly (72_codedistribution.sql:81-84,
> 130, 156). SAS-parity per the quoted ms_caresettingprincipal.sas / ms_codedistribution.sas semantics;
> the generator never draws XX, so this is production-only behavior.

**Probe P-C2 (confirms by execution):** hand dataset with patient enrolled + indexed, one event
`D00001` claim dated at the first-event date, and the diagnosis table written WITHOUT the `enctype`
column (the a_optional_columns omission path). Study with wildcard `('**','*')` care setting.
Run through `episodes + masterlist` and `codedistribution`; assert: (1) `event_claims` contains the
claim (normalized `cdm_diagnosis.enctype = 'XX'` — check the view row directly); (2) `cohort_final.feventdt`
is set (the event fired); (3) `distindexmap` has no 'XX' hoi entity and `distindex_episodes.distindexhoi`
is NULL while `numevents`/follow-up counting includes the event; (4) d_denom.expected_denomcounts and
d_stages.expected_distindex agree with the engine (both sides must predict the empty hoi list). This
pins that the asymmetry is real end-to-end, not just static.

## C3 — Discharge_Status EX in the properties generator (aligned-with-annotation — decision (b))

**Decision: (b) — record that existing-suite coverage suffices and the properties generator stays
minimal.** Grounding:

- The full reference domain (encounter.md:92-109, Char(2), 16 values): `AF` Adult Foster Home, `AL`
  Assisted Living Facility, `AM` Against Medical Advice, `AW` Absent without leave, `EX` Expired, `HH`
  Home Health, `HO` Home / Self Care, `HS` Hospice, `IP` Other Acute Inpatient Hospital, `NH` Nursing
  Home (includes ICF), `OT` Other, `RH` Rehabilitation Facility, `RS` Residential Facility, `SH` Still
  In Facility, `SN` Skilled Nursing Facility, `UN` Unknown. "Populate for `IP` and `IS` … May be
  populated for `ED` … Should be missing for `AV` or `OA`" (encounter.md:111) — so the generator's
  `""` on IP and `None` on AV is domain-conform.
- The EX-derived death path is pinned at unit level on the REAL shipped statement:
  test_pipeline.py:8151-8191 executes the `deaths` statement from 10_normalize.sql itself and asserts
  discharge-date deaths (`EX` with ddate), death-table precedence when both exist (kept "even if
  later"), lowercase `'ex'` with NULL ddate (death at admission), a non-EX `'HO'` row producing no
  death, and the column-absent variant (`{opt_discharge_status}` → `NULL`) yielding no encounter
  deaths. That is stronger than anything a properties draw would add: the properties oracles could not
  check this path without growing their own death models.
- The properties oracles deliberately model deaths from the death table ONLY: d_denom.py:325-329 builds
  `deaths` from `rows_for_table(dataset, "death")` alone and clips windows at that date
  (d_denom.py:383-386); b_stockpile_model.py:417-421 (POV1) likewise. Production instead censors at the
  union table including encounter-derived deaths (10_normalize.sql:184-195). If `generate()` started
  drawing `EX`, every such fixture could diverge from the oracles and look like a pipeline defect —
  option (a) therefore requires extending d_denom AND b_stockpile_model (and any other death consumer)
  before it adds value, which is oracle-scope growth, not under-coverage of a pinned behavior.
- Harness expectations for hand_fixture pin nothing discharge-status-shaped beyond
  `deaths == 1` (test_ac1_infrastructure.py:190; the only encounter is `IP` with `discharge_status=""`,
  synth.py:615-616) and the normalize relation-name list including `deaths`
  (test_harness_stages.py:200-204). No properties assertion would newly discriminate an EX draw.

**Annotation text (proposed placement: extend the `Encounter` docstring, synth.py:139-140):**

> `discharge_status` stays `""`/`None` here: the d-package oracles model deaths from the death table
> only (d_denom.py, b_stockpile_model.py), so an `EX` draw would need oracle-side encounter-death
> modeling first; the EX derivation itself is pinned in
> test_pipeline.py::test_deaths_come_from_the_death_table_and_expired_encounters (incl. lowercase `ex`,
> missing ddate, death-table precedence).

## C4 — Encounter unique-row caveat (aligned-with-annotation)

Exact reference text, encounter.md:

- L7: "Each encounter has a single record, keyed by `PatID` and `EncounterID`."
- L21: "**Unique row:** `EncounterID`."
- L23: "> Caveat: the table description identifies each record by `PatID` and `EncounterID`, while the unique-row rule names `EncounterID` alone. This tension is present in the source documentation."

The tension is explicitly stated in the source documentation → **UNRESOLVED** (standing rule: never
invent a validation rule to resolve it).

Repo side: no synth or oracle comment claims either side. The `Encounter` docstring (synth.py:139-140)
speaks only of ddate/discharge_status semantics. The generator's id scheme — `encounterid=patid * 10`
and `patid * 10 + 1` (synth.py:479, 486) — is globally unique across patients by construction, so it
satisfies BOTH readings simultaneously; the optional-column probes even drop `encounterid` entirely
(a_optional_columns.py:16), compatible with both. Production never joins or dedupes encounters on
EncounterID at all (`_ip_days` dedupes `(patid, adate)`-ish at 10_normalize.sql:136; utilization dedupes
`(patid, adate, enctype)` at 74_utilization.sql:35), so no code path could violate either reading.

**Annotation:** "Encounter uniqueness is UNRESOLVED in SCDM 8.2.2 (encounter.md:23); the synthetic
generator's `patid*10`-family EncounterIDs are globally unique, satisfying both readings — keep it
that way, and do not add a uniqueness assertion that would silently pick a side." Placement: the
`Encounter` docstring or the a_scdm_layouts oracle header.

## C5 — Death vs death.md (aligned-with-annotation)

- death.md full column list: `PatID` (Num(#), missing not allowed, L17-25), `DeathDt` (Num(4) SAS date,
  missing not allowed, L27-33), `DtImpute` (Char(1), missing not allowed, values B/D/M/N, L35-43),
  `Source` (Char(1), missing not allowed, values L/N/S/T/O/M, L45-53), `Confidence` (Char(1), missing
  not allowed, values E/F/P, L55-62). Row: "One record per PatID" (L7); "**Unique row:** `PatID`" (L13).
- L9: "death data is captured in 2 tables: Death … and Cause of Death … The 2 tables are linked by
  PatID" — cause-of-death lives in the separate COD table, so nothing about causes can be violated by
  a Death-table subset.
- Pipeline read-set: scdm.py:98-105 `TableSpec("death", ("patid", "deathdt"), (), …, optional_table=True)`
  — exactly the two columns; synth `Death` rows (synth.py:85-87) and the parquet schema
  (synth.py:236-239) match one-to-one; generation (synth.py:489-493) and hand_fixture (synth.py:618)
  always populate a real date.
- Subset framing: `DtImpute`/`Source`/`Confidence` are absent from the extract because the pipeline
  doesn't read them — standing context, not a defect. The only SCDM rules expressible in the
  two-column subset — DeathDt present, one row per PatID — are satisfied by construction, and
  production defensively re-guards them anyway (10_normalize.sql:179-182: `WHERE deathdt IS NOT NULL`,
  `min(deathdt) … GROUP BY 1` collapses any duplicate).

**Annotation (report-level; no code change):** "Synth death = (patid, deathdt) is exactly the pipeline
read-set (scdm.py:100). death.md's remaining columns (DtImpute, Source, Confidence) and the
cause-of-death table are outside the read-set by subset framing; the subset cannot violate their
not-missing rules, and the one-row-per-PatID + non-null-date rules it CAN express are satisfied by the
generator and re-enforced by 10_normalize.sql:179-182."

## C6 — Labs vs laboratory-result.md (misaligned-fixed)

Field-by-field verdicts (synth draw at synth.py:516-532; schema synth.py:283-299):

| synth field | draw | reference | verdict |
|---|---|---|---|
| `result_type` | `rng.choice(("N","U","C"))` on a fixed `CREATININE` row | L77: "Unknown value is populated when `MS_Test_Name` = `UNMAPPED`." | **misaligned for 'U'**: CREATININE is a mapped value (L46), so 'U' is illegal there |
| `ms_result_n` | always `round(rng.random()*200, 2)` (non-null) | L272: MS_Result_N "Missing: yes, unless `Result_Type` = `N` … null for text/character results (`Result_Type` = `C`)" | **misaligned for 'C'**: non-null numeric on a C row |
| `ms_result_c` | always `""` | L257: "populated only for text or character results (`Result_Type` = `C`)" with enumerated values BORDERLINE/NEGATIVE/POSITIVE/UNDETERMINED/range (L261-267); L270: "null for numeric results" | **misaligned for 'C'** (empty string, not an enumerated value) and cosmetically off for 'N'/'U' (should be null; pipeline-insensitive — 10_normalize.sql:247 collapses `''`/NULL identically, and 76_labs.sql never reads result_char) |
| `fast_ind` | `"X"` | L106-116: `X` = not applicable, legal for any test where fasting status isn't considered; only `U` is reserved for UNMAPPED | aligned |
| `pt_loc` | `"O"` | L162-173: `O` = Outpatient | aligned |
| `specimen_source` | `"SR_PLS"` | L140: `SR_PLS` — serum/plasma; Char(7) fits | aligned |
| `loinc` | `"2160-0"` | L145-148: Char(10), missing allowed, must include the check-digit hyphen; per index standing rule LOINC identifies the coding system, not an exhaustive list | aligned (format-checked only — the specific code's mapping is unverifiable from this reference) |
| `result_dt`/`order_dt` | `None` as DOUBLE | L224-244: Num(4) SAS dates, missing allowed; "One or more of these dates is populated for each record" — `lab_dt` is always populated | aligned; days-since-1960 DOUBLE matches the index standing rule and production (10_normalize.sql:241-245) |
| `ms_test_name` | `"CREATININE"` | L46 (enumerated), Char(20), missing never | aligned |
| `ms_test_sub_category` | `""` | L85-104: sub-categories apply only to select tests (CHOL_LDL, D_DIMER+N, PG); none applies to CREATININE | aligned (empty ≈ null) |
| `ms_result_unit` | `"MG/DL"` | L299-302: "Missing: yes, unless `Result_Type` = `N`"; populated is right for N rows; exact unit strings are Workgroup guidance not enumerated here | aligned with annotation (format-legal, value unverifiable) |
| `lab_dt` | populated DATE | L229-232: Num(4) SAS date, missing yes | aligned |
| `labid` (absent) | not written | L24-27 (never missing in full SCDM) | subset framing — pipeline doesn't read it (scdm.py:136-140); context, not a defect |

Production cross-check (76_labs.sql): extraction matches the seven-attribute LAB01 combination or
LOINC or PX (76_labs.sql:76-87) and the numeric criterion is bypassed for any non-'N' record
(76_labs.sql:99-101 `resulttyp <> 'N' OR result_type <> 'N'`, mirrored by d_stages.py:338). Because
every generated row is fixed `CREATININE`/`SR_PLS`/`MG/DL`/`X`/`O`, a `result_type='C'/'U'` row fails
the seven-attribute match (config `map_result_type='N'`) and is simply never extracted — so the
violations cause no oracle failures, but they (a) make the generator emit SCDM-illegal rows and
(b) leave the non-numeric passthrough branch of 76_labs.sql:100-101 exercised only trivially.

**Proposed edit — synth.py:516-532** (SCDM-legal (result_type, ms_result_n, ms_result_c) triples;
'U' moved onto the only row shape where the reference allows it, UNMAPPED):

old:
```python
        labs = tuple(
            LabResult(
                patid=p.patid,
                lab_dt=start + timedelta(days=rng.randrange(window_days)),
                result_dt=None, order_dt=None,
                ms_result_n=round(rng.random() * 200, 2),
                ms_result_c="",
                result_type=rng.choice(("N", "U", "C")),
                loinc="2160-0", px="PX1",
                ms_test_name="CREATININE", ms_test_sub_category="",
                specimen_source="SR_PLS", ms_result_unit="MG/DL",
                fast_ind="X", pt_loc="O",
            )
            for p in pts[: patients]  # one per patient, deterministic
        )
```
new:
```python
        labs = tuple(
            LabResult(
                patid=p.patid,
                lab_dt=start + timedelta(days=rng.randrange(window_days)),
                result_dt=None, order_dt=None,
                # SCDM-legal result shapes (laboratory-result.md): 'N'
                # carries a numeric value and null text; 'C' carries
                # enumerated text and null numeric; 'U' exists only on
                # UNMAPPED rows, where ms_result_n/_c are null too.
                ms_result_n=round(rng.random() * 200, 2) if rt == "N" else None,
                ms_result_c=rng.choice(("POSITIVE", "NEGATIVE",
                                        "BORDERLINE", "UNDETERMINED"))
                             if rt == "C" else None,
                result_type=rt,
                loinc="2160-0" if rt != "U" else None,
                px="PX1",
                ms_test_name="CREATININE" if rt != "U" else "UNMAPPED",
                ms_test_sub_category="",
                specimen_source="SR_PLS" if rt != "U" else "UNK",
                ms_result_unit="MG/DL" if rt == "N" else None,
                fast_ind="X" if rt != "U" else "U",
                pt_loc="O" if rt != "U" else "U",
            )
            for p in pts[: patients]  # one per patient, deterministic
            for rt in (rng.choice(("N", "N", "C", "U")),)
        )
```
Companion edits required by the same change:
1. synth.py:161 `ms_result_c: str` → `ms_result_c: str | None` (write_parquet already handles NULLs in
   a VARCHAR column; TABLE_COLUMNS unchanged).
2. Call sites passing `ms_result_c` positionally keep working: a_optional_columns.py:33-34 passes
   `""` for an `'N'` row — legal to keep, or update to `None` for consistency.
3. Determinism note as in C1: the draw order changes; rerun the properties battery.

Minimal fallback if the richer triple is rejected: restrict the pool to `"N"` only
(`result_type="N"`) — then every row is legal as-is except `ms_result_c=""`-vs-null, which is
pipeline-insensitive (10_normalize.sql:247). But that drops all coverage of the non-numeric branches.

Coverage note for the follow-up: with the triple construction, a study can additionally map a
`map_result_type='C'` LAB01 combination to exercise the `result_type <> 'N'` passthrough of
76_labs.sql:100-101 and d_stages.py:338 with a matching row — today that branch runs only on
non-matching rows.

---

## UNRESOLVED ambiguities

1. Encounter uniqueness key: "keyed by `PatID` and `EncounterID`" (encounter.md:7) vs "**Unique row:**
   `EncounterID`" (encounter.md:21); the reference itself flags the tension (encounter.md:23). Left
   UNRESOLVED; no synth/oracle comment claims a side; the generator satisfies both readings.
2. (Outside this audit's table set but encountered while reading laboratory-result.md, recorded for the
   lab-package owner: `Result_Loc` "Missing: yes" vs its definition's "There are not any null values"
   — laboratory-result.md:180 preserves both statements. `Result_Loc` is not in the pipeline read-set.)

## Suspected PRODUCTION defects (operator decision briefs — never fixes)

1. **XX-event / distindex asymmetry** — src/qrp/sql/30_exposure.sql:486-490 vs
   src/qrp/sql/72_codedistribution.sql:81-84,130,156 (fallback at src/qrp/sql/10_normalize.sql:160).
   Why: a diagnosis claim whose enctype normalizes to 'XX' (missing/null column or NULL value) counts
   as an outcome event under a '**' care setting, but can never appear in distindexexp/distindexhoi or
   distindexmap; a cohort whose events are all XX gets `numevents > 0` with the blank hoi placeholder
   row. The in-repo SAS citations (30_exposure.sql:401-405; 72_codedistribution.sql:22-26) indicate
   both halves are SAS-faithful, so this is presented as a parity question for the operator to confirm
   against ms_codedistribution.sas — not a proposed fix.
2. (Observation, not a defect: the encounter-death derivation at src/qrp/sql/10_normalize.sql:184-190
   does not filter by EnType, so an `ED` encounter with `discharge_status='EX'` also yields a death —
   legal per encounter.md:111 ("May be populated for Emergency Department (`ED`) encounters"); AV/OA
   rows should never carry EX per the same line, so no legal input is mishandled.)

## End-state alignment summaries

**Encounter.** The synthetic generator covers only IP (ddate + empty status) and AV; IS is absent from
both the EnType pool and the encounter draws while production counts IS in utilization
(74_utilization.sql:75), expands '**' to include IS in codedistribution (72_codedistribution.sql:81-84),
and both d-package oracles model IS (d_stages.py:63,118,1584) — so C1's fix makes the dead coverage
live and the proposed probe proves enc_is counting and wildcard expansion end-to-end. Discharge_status
stays minimal by design (C3 decision (b)): the EX→death derivation is pinned at unit level on the
shipped SQL including lowercase and missing-ddate cases, and the properties oracles model deaths from
the death table only, so an EX draw would first require oracle-side encounter-death modeling. The
EncounterID uniqueness ambiguity is UNRESOLVED in the reference and honored as such: the generator's
globally-unique id scheme satisfies both readings and nothing in the pipeline keys on EncounterID.

**Death.** Fully aligned. Synth and the pipeline read exactly (patid, deathdt) (scdm.py:98-105,
synth.py:85-87); death.md's other columns (DtImpute, Source, Confidence) and the separate cause-of-death
table are outside the read-set by subset framing and cannot be violated by the subset; the one-row-per-
PatID and non-null-date rules the subset CAN express are satisfied by construction and re-enforced by
10_normalize.sql:177-182, whose death-table-precedence and encounter-EX union semantics are pinned by
test_pipeline.py:8151-8191.

**Laboratory Result.** Domain values are individually legal — CREATININE (mapped), SR_PLS, X, O,
format-legal LOINC with check-digit hyphen, null SAS-numeric order/result dates with lab_dt populated
satisfying the "one or more dates" rule — but the conditional result_type semantics are violated: 'U'
is drawn on a mapped CREATININE row where the reference reserves it for UNMAPPED, and 'C' rows carry a
non-null numeric result with an empty instead of enumerated text value (C6). The proposed triple
construction makes every generated row SCDM-legal ('N' numeric + null text; 'C' enumerated text + null
numeric; 'U' only on UNMAPPED rows with null results and unknown indicators) and opens real coverage of
the non-numeric passthrough branch in 76_labs.sql:100-101; the minimal fallback (draw 'N' only) is
recorded if the richer shape is rejected.
