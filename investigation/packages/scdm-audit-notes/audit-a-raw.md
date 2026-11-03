# Audit A — Demographic + Enrollment (raw)

Scope: SCDM 8.2.2 alignment of the Demographic (DEM) and Enrollment (ENR) fixture/builder
surface in `tests/properties/` against the pipeline's treatment in `src/qrp/sql/`, on branch
`discovery` tip `7165d77`. READ-ONLY audit; every verdict cites the reference file and the
repo file:line. Reference consulted:
`/home/scarndp/.config/polytoken/skills/howto-work-with-scdm-data/references/{index,demographic,enrollment}.md`.

| table | reference consulted (file + variables) | fixtures/builders checked (file:line) | item | verdict | outcome suggestion |
|---|---|---|---|---|---|
| Demographic | demographic.md:5,11 (`Unique row: PatID`) | synth.py:15-18,496-507 (dup block), 572-574,585-586 (hand_fixture P5/p5_tie); oracles/a_demographics_tie.py:70-94 (D_* fixtures); oracles/b_pkg_fixtures.py:174-180 (dup_demog); findings A-3 | A1 duplicate-demographics fixture vs DEM one-row-per-patient | aligned-with-annotation | add SCDM-rule label to the two synth.py docstrings; one-line DEM unique-row framing for finding A-3 (text below) |
| Demographic | demographic.md:33-41 (`Sex` A/F/M/U, missing not allowed) | synth.py:374 (`_SEXES=("F","M")`), :77 (`Patient.sex` comment already says F/M/U/A); oracles/a_config_semantics.py:68,123,711; sql/10_normalize.sql:38-42 (sex_raw vs collapsed sex); sql/45_pov1.sql:43-44 (cfg_demog join on `sex_raw`); config.py:313 (default `("F","M","U","A")`); sql/70_outputs.sql:29-31,123-127,220-241 | A2 sex pool lacks U/A | misaligned-fixed | `_SEXES` → `("F","M","U","A")` + end-to-end U/A-sex pin test (spec below) |
| Demographic | demographic.md:25-29 (`Birth_Date` Num(4) SAS date, **missing allowed**); index.md:37 (special missing → null in Parquet) | synth.py:76 (`birth_date: date`, non-optional), :426-428; sql/10_normalize.sql:72-75 (`demographics_missing`); sql/45_pov1.sql:42,49; sql/70_outputs.sql:28-31,92-96 (`_att_dem`, level 1) | A3 SCDM allows null Birth_Date; pipeline requires it; no synth path draws it | misaligned-fixed | widen `Patient.birth_date` to `date \| None` + `GenerateOptions.null_birth_dates` knob + pin test: dropped at attrition level 1, excluded from pov1 via demographics_missing (spec below) |
| Demographic | demographic.md:53-69 (`Race` 0-5 **plus `M` = Multi-racial**, missing not allowed) | synth.py:375 (`_RACES=("0".."5")`); sql/10_normalize.sql:43 (absent/null → `'M'`); oracles/a_config_semantics.py:69 (absent race condition defaults to `("0".."5","M")`); sql/45_pov1.sql:45-46; config.py:314 | A4 race pool lacks `M` | misaligned-fixed | `_RACES` → `("0","1","2","3","4","5","M")`; oracle expectations unchanged (config default already admits M); optional follow-on: null-race → `'M'` probe |
| Demographic | demographic.md:43-51 (`Hispanic` N/Y/U) | synth.py:431 (`rng.choice(("Y","N","U"))`); sql/10_normalize.sql:44 (null/absent → `'U'`) | A5 hispanic draws vs domain | aligned | none — exact match incl. the normalize default |
| Enrollment | enrollment.md:41-57 (`MedCov` Y/N/U/A; `DrugCov` Y/N/U) | synth.py:97-98 (stale `# Y / N` comments), :440-443 (spans always Y/Y/Y); oracles/b_pkg_fixtures.py:89-94,71-73 | A6 stale EnrollmentSpan flag comments; Y/Y/Y-only generate(); flag-variety placement | misaligned-fixed | exact docstring fix below (incl. chart optionality misattribution); do NOT add a flag-variety knob to generate() — b_pkg_fixtures owns that coverage |
| Enrollment | enrollment.md:41-57 vs oracles/b_pkg_fixtures.py:94 (`_FLAG_POOL`); commits 744189d+7165d77 | b_pkg_fixtures.py:94 + test_pkg_b_enrollment.py:243,283 (test names verified) | A7 `_FLAG_POOL` legality + pinned boundary tests | aligned | none — every drawn pair is legal; cite the two pinning tests as outcomes |
| Enrollment | enrollment.md:25-37 (`Enr_Start`/`Enr_End` missing not allowed; start ≥ 2000-01-01), :98-102 (collapsing rule, 1-day bridge, PlanType/PayerType overlap) | synth.py:438-443 (span construction), :401-410 (generate() docstring); oracles/b_enrollment_model.py:50-63,96-153; oracles/b_day_sets.py:3-8; sql/10_normalize.sql:28-30; sql/20_enrollment.sql:50-75; test_pkg_b_enrollment.py:201,243 | A8 span date rules + collapsing/overlap forms | aligned-with-annotation | add a generate() docstring note: custom windows < 211 days can emit inverted spans, which `cdm_enrollment` drops (10_normalize.sql:28-30) — SCDM-collapsing is an ETL rule upstream, not a pipeline stage (text below) |
| Enrollment | enrollment.md:61-74 (`Chart` Y/N, missing not allowed) | synth.py:99 (`# Y / N (optional column in SCDM)`); scdm.py:69-75 (chart is optional in the pipeline's read set); sql/10_normalize.sql:26 (null chart → `'N'`); test_pkg_b_enrollment.py:283,331 | A9 chart Y/N handling | aligned-with-annotation | the A6 docstring edit already corrects the "(optional column in SCDM)" misattribution (optional in `qrp.scdm` read set, required in SCDM 8.2.2); behavior itself is pinned by the two chart tests |

## Detail blocks (non-aligned items)

### A1 — aligned-with-annotation: duplicate-demographics fixture is a deliberate DEM-rule violation

Reference: `demographic.md:5` "One record per patient with the most recent birth date, sex,
race, ethnicity, and postal code." and `demographic.md:11` "**Unique row:** PatID". Any
extract with two rows for one PatID therefore violates SCDM DEM and is reachable only as an
upstream ETL defect. The repo's fixtures create such rows on purpose:

- synth.py:15-18: "duplicate demographic rows — same patid and birth_date, different sex —
  which exercise the total-order tie-break in 10_normalize.sql" (framed as tie-break
  exercise, never as an SCDM-rule violation);
- synth.py:572-574: "P5 DUPLICATE demographic rows -> present in demographics_multi and
  excluded from pov1/ptsmasterlist" (labels the pipeline behavior, not the SCDM status);
- a_demographics_tie.py:70-94 builds D_DIFFER_ZIP / D_DIFFER_PCD / D_IDENTICAL pairs;
  b_pkg_fixtures.py:174-180 (`dup_demog`) has no annotation at all.

Compare the B-side precedent, which already carries the SCDM framing:
test_pkg_b_enrollment.py:238-241 ("SCDM 8.2.2 data-model boundaries") and :243-255, whose
docstring explicitly names the sanctioned overlap form. The A-side duplicate fixtures lack
the equivalent label.

**Annotation text — synth.py "Deliberate ties" bullet (synth.py:15-18), old → new:**

```
* duplicate demographic rows — same patid and birth_date, different
  sex — which exercise the total-order tie-break in 10_normalize.sql;
```
→
```
* duplicate demographic rows — same patid and birth_date, different
  sex — which exercise the total-order tie-break in 10_normalize.sql.
  These rows deliberately violate SCDM 8.2.2 DEM's unique-row rule
  ("Unique row: PatID" — one record per patient), so they are
  non-canonical data-defect probes, like package B's overlapping
  enrollment spans, not shapes a compliant extract can carry;
```

**Annotation text — hand_fixture P5 bullet (synth.py:572-574), old → new:**

```
    * P5  DUPLICATE demographic rows -> present in demographics_multi
          and excluded from pov1/ptsmasterlist, which is what the
          pipeline does with ambiguous demographics.
```
→
```
    * P5  DUPLICATE demographic rows (a deliberate violation of SCDM
          DEM "Unique row: PatID"; non-canonical defect probe) ->
          present in demographics_multi and excluded from
          pov1/ptsmasterlist, which is what the pipeline does with
          ambiguous demographics.
```

**One-line DEM unique-row framing for finding A-3's alignment note:**

> SCDM 8.2.2 DEM admits exactly one row per PatID ("Unique row: PatID",
> demographic.md:11); every duplicate-demographics fixture in this repo
> (synth.py:496-507, hand_fixture p5/p5_tie at synth.py:585-588,
> a_demographics_tie.py D_* fixtures, b_pkg_fixtures.py dup_demog) is
> therefore a deliberate non-canonical data-defect probe exercising the
> pipeline's dedupe / demographics_multi / pov1-exclusion defense
> (10_normalize.sql:57-70, 45_pov1.sql:41-49) — not a shape a compliant
> extract can carry.

### A2 — misaligned-fixed: `_SEXES` lacks SCDM-legal `U` and `A`

Reference: `demographic.md:33-41` — `Sex` Char(1), missing not allowed, values `A`
(Ambiguous) / `F` / `M` / `U` (Unknown).

Confirmed pool: synth.py:374 `_SEXES = ("F", "M")` — `U`/`A` never drawn. Note the dataclass
already documents the full domain: synth.py:77 `sex: str  # F / M / U / A — raw value, not
collapsed`.

Where sex flows (all read, not edited):

- Config conditions: a_config_semantics.py:68 pins a study-declared `Sex: ["f", " m "]` →
  `("F","M")`; :123 pins the ABSENT condition default `("F","M","U","A")` (payload
  `{"CohortGrp": "cohort_b"}` at :179); :711 pins empty-string sex → same default. The oracle
  treats a condition as a plain allow-list: values outside it (`U`, `A` under `("F","M")`)
  simply fail the match — no special modeling.
- Conditionless studies: `cfg_demog` is registered from each cohort's `c.sex`/`c.race`/
  `c.hispanic` lists (src/qrp/pipeline.py:670-683); the `CohortConfig` defaults
  (src/qrp/config.py:313-315) are `sex=("F","M","U","A")`, `race=("0".."5","M")`,
  `hispanic=("Y","N","U")` — the same tuples the input-file loader uses (config.py:1616-1618).
  **A conditionless study therefore admits every SCDM-legal value: F/M/U/A sex, 0-5+M race,
  Y/N/U hispanic.** All synth studies built by `build_study()` are conditionless (synth.py:656-732
  never sets sex/race/hispanic), so U/A-sex members pass demographic eligibility in every
  synth-driven test.
- Normalize: 10_normalize.sql:40 keeps `sex_raw = upper(sex)`; :41-42 collapses anything
  outside F/M (including NULL, `U`, `A`) to `'O'` — "SAS collapses anything outside F/M to 'O'
  for the sex covariate but keeps the raw value for the eligibility filter."
- Eligibility: 45_pov1.sql:43-44 joins `cfg_demog` on `ds.value = dm.sex_raw` — the RAW value
  decides.
- Attrition: 70_outputs.sql:29-31 `_att_dem` counts `demographics` rows with
  `birth_date IS NOT NULL AND sex IS NOT NULL`; levels 3-5 (age/chart/demographic conditions)
  are CARRIED from level 2, not counted (70_outputs.sql:123-127). So a U/A-sex member counts
  at level 1 regardless of any sex condition, and the demographic-condition exclusion never
  appears in attrition.
- Stratified outputs: `denominators` and `censoring` group on `c.sex` — the COLLAPSED value —
  so U and A both surface as `'O'` (70_outputs.sql:224, :267, :302; the `'othersex'` output
  shell knob acknowledges this: config.py:1170).

**Proposed edit (synth.py:374), old → new:**

```
_SEXES = ("F", "M")
```
→
```
# SCDM 8.2.2 Sex domain (demographic.md): A/F/M/U, missing not allowed.
# Draws all four so the raw-value eligibility join (45_pov1.sql sex_raw)
# and the non-F/M -> 'O' collapse (10_normalize.sql) are both exercised;
# matches the conditionless-study default (qrp.config CohortConfig.sex).
_SEXES = ("F", "M", "U", "A")
```

Follow-up caution before applying: nothing outside synth.py references `_SEXES` (repo grep),
but changing the pool length changes every subsequent draw in `generate()` for a given seed
(`_pick` consumes one `randrange(len(pool))` per patient). Hypothesis-driven property tests
assert invariants, not golden draws, so risk is low — but run the property suite once after
the edit.

**End-to-end U/A-sex pin test (the probe):**
Construct via `write_parquet()` a dataset with three patients — sex `'U'`, sex `'A'`, sex
`'F'` (control) — all with birth dates, one qualifying fill each, under
`build_study(...)` (conditionless ⇒ `cfg_demog` admits F/M/U/A) and again under
`two_cohort_study(cohort_overrides={"sex": ("F","M")})`. Observe:
1. `demographics`: `sex_raw` = `U`/`A`/`F`; collapsed `sex` = `'O'`,`'O'`,`F`.
2. Conditionless run: all three in `pov1` (cfg_demog raw-value join admits U and A);
   attrition level 1 counts all three.
3. `sex=("F","M")` run: U and A absent from `pov1` pass-1 output (`_pov1_demog` join
   fails), control present; attrition levels 1-5 UNCHANGED from the conditionless run
   (levels 3-5 are carried, 70_outputs.sql:123-127) — the exclusion is invisible in
   attrition by design.
4. `denominators` rows: the surviving members' `sex` column shows the COLLAPSED value —
   `U`/`A` members would report as `'O'` if they had survived.
Defect vs intended parity: a *defect* would be U/A appearing in `pov1` under the
`("F","M")` condition (raw join not applied) or the collapsed `'O'` leaking into
eligibility (join on collapsed `sex` instead of `sex_raw`). The SQL as read does neither;
the test pins that.

### A3 — misaligned-fixed: null `Birth_Date` is SCDM-legal but never exercised

Reference half: `demographic.md:25-29` — `Birth_Date` "Num(4), SAS date · **missing
allowed**". Stronger: index.md:37 — SAS7BDAT special missing (`.U`, `.S`) become ordinary
nulls in Parquet, so "a variable permitting special missing can contain null in Parquet even
when ordinary SAS missing is disallowed"; for Birth_Date ordinary missing is already allowed,
so null birth dates are a fully legal extract shape.

Pipeline half (verified both):
- 10_normalize.sql:72-75 — `demographics_missing`: `WHERE birth_date IS NULL OR sex IS NULL`.
- 45_pov1.sql:42,49 — pov1 pass 1 `LEFT JOIN demographics_missing ms ... WHERE ms.patid IS
  NULL` → null-birth-date members are excluded from pov1.
- 70_outputs.sql:28-31 — `_att_dem`: `SELECT DISTINCT patid FROM demographics WHERE
  birth_date IS NOT NULL AND sex IS NOT NULL` ("Members with a birth date and sex (SAS's
  level-1 population)", :28); the level-1 count (:92-96) counts `_att_dem` SEMI JOINed to
  `cdm_enrollment` over the query period; `_att_spans` (:39-47) JOINs `_att_dem`. A
  null-birth-date member is absent from `_att_dem` → not counted at level 1 → contributes no
  spans at level 2. The expected pin (dropped at level 1, excluded from pov1 via
  demographics_missing) is exactly what the SQL prescribes.

No synth path produces it: synth.py:76 types `birth_date: date` (non-optional) and
generate() always constructs `birth=date(1935,1,1)+timedelta(...)` (synth.py:425-428);
every other builder (`patient()` helpers, hand_fixture, a_demographics_tie `_patient`)
passes literal dates.

**Proposed edits (test side only):**

1. synth.py:76, old → new:
```
    patid: int
    birth_date: date
```
→
```
    patid: int
    birth_date: date | None       # None legal: SCDM 8.2.2 Birth_Date is
                                  # "missing allowed" (demographic.md); the
                                  # pipeline drops such members (attrition
                                  # level 1, demographics_missing)
```
2. synth.py `GenerateOptions` (synth.py:383-394), add knob:
```
    null_birth_dates: bool = False
```
with implementation in `generate()` after the patient loop: when set, `replace(pts[1],
birth_date=None)` (dataclasses.replace — keep at least one patient with a birth date so
index-age stages stay exercisable).
3. Pinning test (new, e.g. tests/properties/test_pkg_a_demographics_missing.py): dataset via
`write_parquet()` with one null-birth-date member + one control, run through the real
stages; assert (a) `demographics_missing` contains the patid, (b) `pov1` and
`ptsmasterlist` exclude it while including the control, (c) `attrition` level 1 count
excludes it (absent from `_att_dem`, 70_outputs.sql:29-31) and level 2 consequently drops
it (`_att_spans` JOIN `_att_dem`, :44).
Distinguishing defect vs intended parity: the member appearing in `_att_dem`/pov1 would be
a defect; exclusion at both points is the intended SAS-parity behavior the test freezes.

**Related production nuance (see briefs #1):** `_att_dem`'s `sex IS NOT NULL` is vacuous —
`demographics.sex` is the COLLAPSED column (10_normalize.sql:41-42), which never yields NULL
(the CASE falls through to `'O'` even for NULL raw sex). Only the birth-date half of the
level-1 filter does real work; null/blank raw **sex** members are counted at level 1 yet
still excluded from pov1 via `demographics_missing`.

### A4 — misaligned-fixed: `_RACES` lacks SCDM-legal `M` (Multi-racial)

Reference: `demographic.md:53-69` — `Race` Char(1), missing not allowed, values `0`=Unknown,
`1`-`5`, **`M` = Multi-racial**.

Confirmed pool: synth.py:375 `_RACES = ("0","1","2","3","4","5")` — `M` never drawn.

Flow: 10_normalize.sql:43 `race = upper(COALESCE({opt_race}, 'M'))` — a NULL value (or an
absent column, resolved by `qrp.scdm`) becomes `'M'`. Config side: the conditionless default
already includes M — a_config_semantics.py:69 pins absent race → `("0","1","2","3","4","5","M")`;
config.py:314. Eligibility joins on the collapsed `dm.race` (45_pov1.sql:45-46).

Does adding an explicit `race='M'` draw change oracle expectations? No. The config oracle's
race expectation list already contains `M` (a_config_semantics.py:69); no output-level race
expectation exists outside USERSTRATA-tagged tables (`censoring` stratifies by `c.race` only
when a level declares `has_race`, 70_outputs.sql:303), and synth `build_study()` passes no
strata by default. The existing absent-column-defaults-to-`'M'` behavior is a different code
path (NULL value / absent column via COALESCE) and remains unpinned by this change — an
explicit `'M'` draw exercises the DOMAIN VALUE through eligibility and stratification, which
no current fixture does.

**Proposed edit (synth.py:375), old → new:**

```
_RACES = ("0", "1", "2", "3", "4", "5")
```
→
```
# SCDM 8.2.2 Race domain (demographic.md): 0-5 plus M = Multi-racial;
# matches the conditionless-study default (qrp.config CohortConfig.race).
# (A NULL race value collapses to 'M' in 10_normalize.sql — a separate
# path; see audit brief on the 'M' default.)
_RACES = ("0", "1", "2", "3", "4", "5", "M")
```

Same draw-sequence caution as A2 (pool length change shifts per-seed draws; repo grep shows
nothing else references `_RACES`). Optional follow-on probe (not required for this verdict):
a NULL-race member to pin the COALESCE→`'M'` default through cfg_demog and denominators.

### A5 — aligned: `Hispanic` draws match the domain exactly

Reference: `demographic.md:43-51` — `Hispanic` Char(1), missing not allowed, values
`N`/`Y`/`U`. synth.py:431: `hispanic=rng.choice(("Y","N","U"))` — exact match, all three
legal values drawn, nothing out of domain. Normalize default: 10_normalize.sql:44
`upper(COALESCE({opt_hispanic}, 'U'))` — null/absent → `'U'`, which is itself a member of
the domain (semantically consistent, unlike the race case). Conditionless default admits all
three (config.py:315, a_config_semantics.py:124). No annotation needed.

### A6 — misaligned-fixed: stale `EnrollmentSpan` flag comments; flag variety belongs to b_pkg_fixtures

Reference: `enrollment.md:41-47` — `MedCov` values `Y`/`N`/`U`/`A` (`A` = Ambulatory only);
`:51-57` — `DrugCov` values `Y`/`N`/`U`. Confirmed leftovers:
- synth.py:440-443 — `generate()` emits `medcov="Y", drugcov="Y", chart="Y"` for every span
  (all-`Y` is SCDM-legal, so the DATA is aligned; only the documentation is stale);
- synth.py:97-98 — `medcov: str  # Y / N` and `drugcov: str  # Y / N` are both wrong now
  that A/U are known-legal and actively drawn in b_pkg_fixtures.py:94;
- synth.py:99 — `chart: str  # Y / N (optional column in SCDM)`: Chart is REQUIRED in
  SCDM 8.2.2 (enrollment.md:63, "Char(1) · missing not allowed"); it is optional in the
  PIPELINE's read set (src/qrp/scdm.py:69-75, `optional=("chart",)`), where NULL → `'N'`
  (10_normalize.sql:26).

**Proposed edit (synth.py:94-99), old → new:**

```
    patid: int
    enr_start: date
    enr_end: date
    medcov: str                   # Y / N
    drugcov: str                  # Y / N
    chart: str                    # Y / N (optional column in SCDM)
```
→
```
    patid: int
    enr_start: date
    enr_end: date
    medcov: str                   # Y / N / U / A (SCDM 8.2.2 MedCov;
                                  # 'A' = ambulatory-only medical)
    drugcov: str                  # Y / N / U   (SCDM 8.2.2 DrugCov)
    chart: str                    # Y / N; the COLUMN is optional in the
                                  # pipeline's read set (qrp.scdm) and
                                  # NULL reads as 'N' — not optional in
                                  # SCDM 8.2.2, where Chart is required
```

**Flag-variety in `generate()` — recommend ONE option: do NOT add it.** Reasoning:
b_pkg_fixtures.py already owns this coverage with a hypothesis strategy over all
representative flag pairs (`_FLAG_POOL`, :94) across span shapes at the gap threshold
(`_SPAN_SHAPES`/`_shape_offset`, :87-97, :187-198), and the behavior is pinned end to end
(test_pkg_b_enrollment.py:155, :201, :243, :283, :331). `generate()`'s enrollment rows are
scaffolding for claim plausibility ("Constructive, not rejective", synth.py:407-409), not the
object of study; a knob there would (a) duplicate b_pkg_fixtures' coverage, and (b) shift
every per-seed draw sequence in generate()-based property tests. The docstring fix above plus
a pointer is enough — optionally append to the `generate()` docstring: "enrollment flags are
uniform Y/Y/Y here; flag variety (Y/N/U/A pairs) is package B's surface:
tests/properties/oracles/b_pkg_fixtures.py `_FLAG_POOL`."

### A7 — aligned: `_FLAG_POOL` end state is exactly legal; boundary tests verified

Reference domains: MedCov ∈ {Y,N,U,A} (enrollment.md:47), DrugCov ∈ {Y,N,U}
(enrollment.md:57) — 12 legal (medcov,drugcov) pairs.

End state check of oracles/b_pkg_fixtures.py:94 `_FLAG_POOL = ("YY", "AY", "YN", "NY",
"UU", "UY", "YU")` (read as flags[0]=medcov, flags[1]=drugcov per expand_spec :161-162):
`YY` ✓, `AY` ✓ (A legal for MedCov only), `YN` ✓, `NY` ✓, `UU` ✓, `UY` ✓, `YU` ✓ — **every
drawn pair is legal; no `drugcov='A'` anywhere.** The in-file comment (:89-93) states the
domains correctly and records the removal of the out-of-domain `drugcov='A'`. Legal pairs
not drawn: NN, AN, AA, UA, UN, NU — the pool is a deliberate subset (medcov=`A` only ever
pairs with drugcov=`Y`); noted, not a defect. `span()` defaults (b_pkg_fixtures.py:71-73)
`medcov="Y", drugcov="Y", chart="Y"` are legal; `patient()` defaults (:65-68)
sex=F/race=1/hispanic=N all in-domain.

Pinning tests verified present (outcomes of commits 744189d + 7165d77 — cited, not
re-reported):
- `test_enrollment_duplicate_identical_spans_collapse` — tests/properties/test_pkg_b_enrollment.py:243;
  pins that the SCDM-sanctioned overlap form (identical spans differing only in
  PlanType/PayerType, which the pipeline does not read) collapses to ONE episode with NO
  B2 fan-out (one stockpiled row, full supply, numdispensing=1, :270-280).
- `test_enrollment_unknown_flags_and_chart_interaction` — :283; pins that `U` flags never
  qualify under MD but `(U,Y)` qualifies under drug-only coverage, and that a non-chart `U`
  span still drops the whole patient under `chart_required` (:311-324).
Also present and aligned: `test_enrollment_overlapping_raw_spans_parity` (:201, B2 lag/
fan-out parity witness) and `test_enrollment_chart_required_drops_nonchart_patients` (:331).

### A8 — aligned-with-annotation: span date rules, SCDM collapsing rule, overlap forms

Reference: enrollment.md:27,35 (`Enr_Start`/`Enr_End` missing not allowed), :29 ("Enr_Start
should not be before January 1, 2000"), :98-102 (collapsing rule: adjacent/overlapping
periods with identical PatID/Enr_Start/Enr_End/MedCov/DrugCov/Chart should be collapsed;
>1-day gaps not bridged — 1/31 bridges with 2/1, not with 2/2; PlanType/PayerType overlap →
set Other and collapse).

What the pipeline does (all read): `cdm_enrollment` drops rows with NULL dates or
`enr_end < enr_start` (10_normalize.sql:28-30) — inverted spans are not a legal SCDM shape
and are silently discarded. Episode building is a STUDY-parameter rule (enrol_gap blank-day
merge + flag-change rupture, 20_enrollment.sql:50-75), NOT the SCDM ETL collapsing rule —
correctly so: the SCDM rule governs extract construction upstream; the pipeline must survive
extracts that were not collapsed. The models carry the domain faithfully:
b_enrollment_model.py:50-63 mirrors normalize (flags upcased, NULL chart → 'N', null/inverted
dates dropped) and :66-87 mirrors has_coverage/rupture; b_day_sets.py:3-8 restricts its
independent oracle to uniform flags + nonoverlapping single-day spans and derives episodes as
connected day components. The SCDM-sanctioned overlap form (identical duplicates) is pinned
by test_pkg_b_enrollment.py:243 (see A7); the B2 lag/fan-out form by :201.

Annotation: synth.py:438-443 computes `span_start = start + choice((0,0,0,60,180))` and
`span_end = end - choice((0,0,30))`. On the default window (2011-01-01..2015-06-30) these are
always ordered; a CUSTOM window shorter than ~211 days can produce `span_end < span_start`,
which `cdm_enrollment` silently drops — a legal-per-pipeline outcome (the span never
existed) but surprising in a generator.

**Proposed annotation — generate() docstring (synth.py:405-410), append one sentence to the
"Constructive, not rejective" paragraph:**

> Enrollment spans assume `end - start >= 211` days: with a shorter custom
> window the drawn offsets can invert a span (enr_end < enr_start), which
> `cdm_enrollment` drops (10_normalize.sql WHERE enr_end >= enr_start).

No code change needed; behavior is already mirrored on both sides (SQL and
b_enrollment_model.normalize_spans:53-54).

### A9 — aligned-with-annotation: Chart Y/N

Reference: enrollment.md:61-74 — `Chart` Char(1), missing not allowed, values `Y`/`N`,
with Medicare/ASO→`N` guidance. Pipeline: chart is an optional column in the read set
(scdm.py:72), NULL → `'N'` (10_normalize.sql:26), `chart <> 'Y'` spans disqualify the whole
patient when `chart_required` (20_enrollment.sql:21-31; modeled at
b_enrollment_model.py:107-114). Behavior pinned by test_pkg_b_enrollment.py:283 and :331.
The only misalignment is the synth.py:99 comment's "(optional column in SCDM)" claim —
corrected by the A6 docstring edit; no further action.

## UNRESOLVED ambiguities

1. `demographic.md:93` (ImputedRace): "Caveat: as documented, code 3's parenthetical
   describes Pacific Islander origins — the same text as code 4 — while Race code 3 defines
   Black or African American as origins in the black racial groups of Africa. The
   contradiction is in the source documentation; the label is preserved as written."
   **UNRESOLVED** per the standing rule (a stated documentation ambiguity is never resolved
   into a validation rule). No action: `ImputedRace` is not in the pipeline's read set
   (scdm.py:76-82) and synth does not model it.
2. None other. `enrollment.md:29` "Enr_Start should not be before January 1, 2000" is a
   guideline, not an ambiguity; synth default windows (2011+) comply and no verdict depends
   on it.

## Suspected PRODUCTION defects (operator decision briefs — never fixes)

1. **`_att_dem`'s sex filter is vacuous; level 1 can count members pov1 excludes.**
   src/qrp/sql/70_outputs.sql:29-31 filters on `sex IS NOT NULL`, but `demographics.sex` is
   the COLLAPSED column (10_normalize.sql:41-42) whose CASE never yields NULL — even a NULL
   raw sex becomes `'O'`. A member with NULL (or blank-string) raw `Sex` and a birth date is
   COUNTED at attrition level 1 (whose description claims "non-missing birth date/sex",
   70_outputs.sql:133) yet EXCLUDED from pov1 via `demographics_missing` (10_normalize.sql:75,
   45_pov1.sql:42,49). Worse, `demographics_missing` uses `sex IS NULL` only, while SAS's
   `missing()` on character is NULL-or-blank — the repo even ships the correct macro
   (`is_missing`, 00_macros.sql:94-99, written for exactly this real-extract '' vs NULL
   distinction) and does not apply it here. A blank-string-sex member passes
   `demographics_missing`, then fails the `cfg_demog` raw-value join under the
   conditionless default (F/M/U/A), i.e. silently lost between attrition and eligibility.
2. **NULL/absent Race collapses to `'M'` (Multi-racial).** src/qrp/sql/10_normalize.sql:43
   maps a missing race to a SPECIFIC race value; SCDM's Unknown code is `'0'`
   (demographic.md:61). Under a race-conditioned study, a missing-race member is evaluated
   as Multi-racial, and stratified outputs label them Multi-racial. Presumably SAS parity
   (verify before touching); operator decision whether NULL→`'0'` or an explicit
   missing-category is the intended semantic.
3. **Attrition levels 3-5 are carried, not counted** (70_outputs.sql:123-127, in-file note):
   the age-range, chart and demographic-condition exclusions never reduce the attrition
   funnel, so an A2-style U/A-sex exclusion (or any sex/race/hispanic restriction) is
   invisible in attrition. Documented in-file as "not yet verified against SAS"; listed here
   because it directly shapes how the A2/A3 probes must be interpreted.

## End-state alignment summaries

**Demographic.** The synthetic DEM surface is domain-correct except for two generator pools:
`_SEXES` draws 2 of 4 legal values (U/A missing — A2, misaligned-fixed with exact edit) and
`_RACES` draws 6 of 7 (M missing — A4, exact edit; oracle expectations unaffected because the
conditionless cfg_demog default already admits both). Hispanic draws are exact (A5, aligned).
Birth_Date is SCDM-nullable and the pipeline's non-missing requirement is deliberate SAS
parity, but no fixture exercises a null birth date (A3, misaligned-fixed: type widening +
knob + pinning test; the expected drop-at-level-1/exclude-from-pov1 behavior is confirmed by
reading the SQL). The duplicate-demographics fixtures are deliberate violations of DEM's
"Unique row: PatID" rule that currently lack the SCDM-rule label the B-side fixtures carry
(A1, aligned-with-annotation: two docstring edits + the one-line framing for finding A-3).
One documentation-internal contradiction (ImputedRace code 3) stays UNRESOLVED. Production
briefs: vacuous `_att_dem` sex filter + NULL-vs-blank `demographics_missing` (brief 1) and
NULL-race→`'M'` conflation (brief 2).

**Enrollment.** This table is the aligned end state the background describes: `_FLAG_POOL`
contains only legal (MedCov, DrugCov) pairs with no `drugcov='A'` (A7, aligned — all 7 pairs
checked against enrollment.md's 12-pair domain), the stale `EnrollmentSpan` flag comments are
the only textual defect (A6, exact docstring fix including the chart-optionality
misattribution), and flag-variety coverage correctly lives in b_pkg_fixtures rather than
`generate()` (recommendation: no generator knob). Span construction obeys the required
non-missing dates and the SCDM collapsing/overlap forms are handled at the right layer —
upstream ETL guidance vs the pipeline's study-parameter episode rule — with the sanctioned
duplicate-overlap form and the U-flag/chart interaction pinned by
test_enrollment_duplicate_identical_spans_collapse (test_pkg_b_enrollment.py:243) and
test_enrollment_unknown_flags_and_chart_interaction (:283), cited as outcomes of commits
744189d + 7165d77 (A7). One generator annotation proposed: short custom windows can emit
inverted spans that `cdm_enrollment` drops (A8).
