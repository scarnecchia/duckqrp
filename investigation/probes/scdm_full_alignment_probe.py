# Manual probe (committed as evidence, not a test): SCDM 8.2.2 alignment of
# the surfaces the 2026-11-03 full-surface audit flagged as probe-needed.
# Seven families, each ending in asserts; SQL output is printed next to the
# expectation so a witness log is self-describing. Run with the campaign
# venv's python from the repo root:
#   python investigation/probes/scdm_full_alignment_probe.py
#
#   1. Sex U/A members end to end: conditionless cfg_demog admits F/M/U/A
#      (the RAW value joins eligibility, 45_pov1.sql sex_raw), the
#      collapsed covariate reports 'O' (10_normalize.sql), and a
#      sex-restricted study drops them at pov1 only — attrition levels 3-5
#      are carried from level 2 (70_outputs.sql:123-127), so the exclusion
#      is invisible in the funnel by design.
#   2. Null Birth_Date (SCDM-legal, demographic.md "missing allowed"):
#      the member is absent from _att_dem (attrition level 1) and excluded
#      from pov1 via demographics_missing.
#   3. IS encounters/claims: counted in enc_is (74_utilization.sql:75),
#      members of the '**' distindex expansion (72_codedistribution.sql),
#      NOT enveloped (enveloping reads IP encounters only,
#      10_normalize.sql:140), and reproduced by the independent d-package
#      oracles.
#   4. Pdx-by-enctype under restricted care settings: ('IS','P') matches
#      only IS/P claims; ('IS','') only blank-pdx IS claims — enveloped
#      IP/'X' rows match neither. SCDM-legal shapes only (diagnosis.md:
#      Pdx populated for IP/IS, blank on AV/ED/OA).
#   5. 'XX'-enctype claims (NULL enctype column value -> the pipeline's
#      internal fallback, 10_normalize.sql:160): the claim fires as an
#      event under a '**' care setting but matches no distindex entity
#      (wildcard expands to the five concrete enctypes and matches
#      exactly) — the SAS-parity asymmetry, execution-confirmed.
#   6. Alternate Rx codetype: an SN-labeled fill is captured when the
#      study's exposure code says SN and excluded when it says ND
#      (30_exposure.sql codetype equality) — behavior the ND/ND diagonal
#      never exercised.
#   7. Procedure enveloping: an AV procedure dated inside an IP stay is
#      re-filed IP/'X'; outside the stay it keeps AV/'' (10_normalize.sql
#      cdm_procedure) — previously zero data coverage anywhere.
"""Probe: full-surface SCDM alignment families, SQL vs pinned expectations."""
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.properties.harness import StageHarness
from tests.properties.oracles import d_stages
from tests.properties.oracles.b_pkg_fixtures import (
    END, EXPOSED_CODES, NOISE_CODE, START, disp, patient, span, two_cohort_study,
)
from tests.properties.oracles.d_fixtures import CIDA_LEVELS, study_with_features
from tests.properties.synth import (
    Death, Diagnosis, Dispensing, Encounter, EnrollmentSpan, LabResult, Patient,
    Procedure, SyntheticDataset,
)

W_START = date(2014, 1, 1)
W_END = date(2014, 12, 31)


def hand_dataset(
    *,
    patients, enrollment=(), dispensings=(), diagnoses=(), procedures=(),
    encounters=(), deaths=(), labs=(),
):
    """A hand extract over the 2014 window (all eight tables settable)."""
    return SyntheticDataset(
        patients=patients, deaths=deaths, enrollment=enrollment,
        dispensings=dispensings, diagnoses=diagnoses, procedures=procedures,
        encounters=encounters, labs=labs,
        exposure_codes=("E00001", "E00002"), event_codes=("D00001",),
        noise_drug_codes=(NOISE_CODE,), noise_dx_codes=("X00001",),
        start=W_START, end=W_END,
    )


def _rows(harness, table):
    return d_stages.table_dicts(harness.engine.con, table)


def _envdx(harness):
    rows = _rows(harness, "cdm_diagnosis")
    for r in rows:
        r["codecat"] = "DX"
    return rows


def _envpx(harness):
    rows = _rows(harness, "cdm_procedure")
    for r in rows:
        r["codecat"] = "PX"
    return rows


def _baseline_grps(harness):
    return {
        c["cohortgrp"] for c in _rows(harness, "cfg_cohort") if c["create_baseline"]
    }


def _attrition_remaining(harness, level):
    return {
        (r["group"], r["remaining"])
        for r in _rows(harness, "attrition")
        if r["level"] == level
    }


# ---------------------------------------------------------------------
# Family 1: sex U/A end to end.
# ---------------------------------------------------------------------
f1 = hand_dataset(
    patients=(patient(1, sex="U"), patient(2, sex="A"), patient(3, sex="F")),
    enrollment=tuple(span(i, START, END) for i in (1, 2, 3)),
    dispensings=tuple(
        disp(i, START + timedelta(days=200), "E00001", 30, 10.0)
        for i in (1, 2, 3)
    ),
)
print("family 1 (sex U/A):")
with StageHarness(two_cohort_study()) as h:
    h.attach_dataset(f1)
    h.run_through("attrition + denominators")
    dem = h.engine.con.execute(
        "SELECT patid, sex_raw, sex FROM demographics ORDER BY patid").fetchall()
    pov1 = {r[0] for r in h.engine.con.execute(
        "SELECT DISTINCT patid FROM pov1").fetchall()}
    lvl1 = _attrition_remaining(h, 1.0)
print("  demographics (patid, sex_raw, sex):", dem)
print("  pov1 patids (conditionless):", sorted(pov1))
print("  attrition level 1 (group, remaining):", sorted(lvl1))
assert dem == [(1, "U", "O"), (2, "A", "O"), (3, "F", "F")], dem
assert pov1 == {1, 2, 3}, pov1
assert all(n == 3 for _g, n in lvl1) and lvl1, lvl1

with StageHarness(two_cohort_study(cohort_overrides={"sex": ("F", "M")})) as h:
    h.attach_dataset(f1)
    h.run_through("attrition + denominators")
    pov1r = {r[0] for r in h.engine.con.execute(
        "SELECT DISTINCT patid FROM pov1").fetchall()}
    carried = {
        lvl: _attrition_remaining(h, lvl) for lvl in (1.0, 2.0, 5.0)
    }
print("  pov1 patids (sex=F,M):", sorted(pov1r))
for lvl, rows in carried.items():
    print(f"  attrition level {lvl} (group, remaining):", sorted(rows))
assert pov1r == {3}, pov1r
# Levels 3-5 are carried from level 2 (70_outputs.sql:123-127): the
# demographic-condition exclusion never appears in the funnel.
assert all(n == 3 for rows in carried.values() for _g, n in rows), carried
print("  -> raw-value eligibility, 'O' collapse, pov1-only exclusion OK")

# ---------------------------------------------------------------------
# Family 2: null Birth_Date member (SCDM-legal) is dropped at level 1.
# ---------------------------------------------------------------------
f2 = hand_dataset(
    patients=(patient(1), patient(2, None, "F")),
    enrollment=(span(1, START, END), span(2, START, END)),
    dispensings=(
        disp(1, START + timedelta(days=200), "E00001", 30, 10.0),
        disp(2, START + timedelta(days=200), "E00001", 30, 10.0),
    ),
)
print("family 2 (null birth_date):")
with StageHarness(two_cohort_study()) as h:
    h.attach_dataset(f2)
    h.run_through("attrition + denominators")
    missing = {r[0] for r in h.engine.con.execute(
        "SELECT patid FROM demographics_missing").fetchall()}
    pov1 = {r[0] for r in h.engine.con.execute(
        "SELECT DISTINCT patid FROM pov1").fetchall()}
    mstr = {r[0] for r in h.engine.con.execute(
        "SELECT DISTINCT patid FROM ptsmasterlist").fetchall()}
    lvl1 = _attrition_remaining(h, 1.0)
print("  demographics_missing:", sorted(missing))
print("  pov1 patids:", sorted(pov1), " ptsmasterlist:", sorted(mstr))
print("  attrition level 1 (group, remaining):", sorted(lvl1))
assert missing == {2}, missing
assert pov1 == {1} and mstr == {1}, (pov1, mstr)
assert all(n == 1 for _g, n in lvl1) and lvl1, lvl1
print("  -> dropped at attrition level 1 and excluded from pov1 OK")

# ---------------------------------------------------------------------
# Families 3+4 share one dataset: IS encounter + IS/IP/AV claims, all
# dated INSIDE the 30-day episode (follow-up events must fall between
# atriskindexdt and episodeenddt, 60_followup.sql _event_candidates).
#   (a) D00001 IS  pdx P  on 3/12 (first event under '**')
#   (b) X00001 IS  pdx '' on 3/20 (noise, inside the IS stay; IS days
#       are NOT enveloping days — 10_normalize.sql reads IP only)
#   (c) D00001 AV  pdx '' on 3/25 (inside the IP stay -> enveloped IP/X)
#   (d) D00001 IS  pdx '' on 3/19
# ---------------------------------------------------------------------
f34 = hand_dataset(
    patients=(patient(1),),
    enrollment=(span(1, W_START, W_END),),
    dispensings=(disp(1, date(2014, 3, 1), "E00001", 30, 10.0),),
    diagnoses=(
        Diagnosis(1, date(2014, 3, 12), "D00001", "09", "P", "IS"),
        Diagnosis(1, date(2014, 3, 19), "D00001", "09", "", "IS"),
        Diagnosis(1, date(2014, 3, 20), "X00001", "09", "", "IS"),
        Diagnosis(1, date(2014, 3, 25), "D00001", "09", "", "AV"),
    ),
    encounters=(
        Encounter(1, date(2014, 3, 18), "IS", 12,
                  ddate=date(2014, 3, 21), discharge_status=""),
        Encounter(1, date(2014, 3, 22), "IP", 10,
                  ddate=date(2014, 3, 26), discharge_status=""),
    ),
)
base34 = study_with_features(
    ("E00001",), ("D00001",),
    utilization=(("cohort_a", "MED", -30, 365),),
    strata=CIDA_LEVELS, start=W_START, end=W_END,
)
print("families 3+4 (IS encounter/claims, pdx x enctype):")
with StageHarness(base34) as h:
    h.attach_dataset(f34)
    h.run_through("utilization")
    dx = h.engine.con.execute(
        "SELECT adate, code, pdx, enctype FROM cdm_diagnosis ORDER BY adate"
    ).fetchall()
    dbg_mstr = h.engine.con.execute(
        "SELECT cohortgrp, patid, indexdt FROM ptsmasterlist").fetchall()
    dbg_idx = h.engine.con.execute(
        "SELECT cohortgrp, patid, adate FROM index_candidates").fetchall()
    dbg_codes = h.engine.con.execute(
        "SELECT role, codecat, codetype, code FROM cfg_codes"
        " ORDER BY role, codecat, code").fetchall()
    dbg_util_cfg = h.engine.con.execute(
        "SELECT * FROM cfg_utilization").fetchall()
    dbg_ev = h.engine.con.execute(
        "SELECT adate, code FROM event_claims ORDER BY adate").fetchall()
    dbg_map = h.engine.con.execute(
        "SELECT * FROM distindexmap ORDER BY distindexid NULLS LAST").fetchall()
    dbg_fin = h.engine.con.execute(
        "SELECT cohortgrp, patid, indexdt, feventdt FROM cohort_final").fetchall()
    util = h.engine.con.execute(
        "SELECT enc_av, enc_oa, enc_ip, enc_is, enc_ed, enc_total, enc_days"
        " FROM utilization").fetchall()
    dimap = h.engine.con.execute(
        "SELECT DISTINCT enctype, pdx, code FROM distindexmap"
        " WHERE codecat = 'DX' ORDER BY 1, 2, 3").fetchall()
    expected_util = d_stages.expected_utilization(
        _rows(h, "ptsmasterlist"),
        _rows(h, "encounter"),
        _rows(h, "cdm_dispensing"),
        _rows(h, "cfg_utilization"),
        _baseline_grps(h),
        _rows(h, "cfg_drugclass"),
    )
    util_ok = d_stages.canonical(_rows(h, "utilization")) == d_stages.canonical(
        expected_util
    ) and d_stages.canonical(
        _rows(h, "utilization_summary")
    ) == d_stages.canonical(
        d_stages.expected_utilization_summary(expected_util)
    )
    expected_di = d_stages.expected_distindex(
        _rows(h, "cohort_final"), _rows(h, "stockpiled"),
        _envdx(h), _envpx(h), _rows(h, "cdm_dispensing"),
        _rows(h, "cfg_codes"), _rows(h, "cfg_care_setting"),
    )
    di_ok = all(
        d_stages.canonical(_rows(h, t)) == d_stages.canonical(expected_di[t])
        for t in ("distindexmap", "distindex", "distindex_episodes")
    )
    # The first event is the IS/P claim, so the episode's hoi list must
    # carry the IS/P entity id from the five-way expansion.
    is_p_id = h.engine.con.execute(
        "SELECT distindexid FROM distindexmap"
        " WHERE codecat = 'DX' AND enctype = 'IS' AND pdx = 'P'"
        " AND code = 'D00001'").fetchone()
    hoi_lists = h.engine.con.execute(
        "SELECT distindexhoi FROM distindex_episodes").fetchall()
print("  cdm_diagnosis (adate, code, pdx, enctype):", dx)
print("  [dbg] ptsmasterlist:", dbg_mstr)
print("  [dbg] index_candidates:", dbg_idx)
print("  [dbg] cfg_codes:", dbg_codes)
print("  [dbg] cfg_utilization:", dbg_util_cfg)
print("  [dbg] event_claims:", dbg_ev)
print("  [dbg] cohort_final:", dbg_fin)
print("  [dbg] distindexmap (full):", dbg_map[:8],
      "... total", len(dbg_map))
print("  utilization:", util)
print("  distindex DX entities (enctype, pdx, code):", dimap)
print("  utilization == oracle:", util_ok, " distindex == oracle:", di_ok)
assert dx == [
    (date(2014, 3, 12), "D00001", "P", "IS"),   # IS claim: NOT enveloped
    (date(2014, 3, 19), "D00001", "", "IS"),
    (date(2014, 3, 20), "X00001", "", "IS"),
    (date(2014, 3, 25), "D00001", "X", "IP"),   # AV on IP day: enveloped
], dx
assert util == [(0, 0, 1, 1, 0, 2, 2)], util
assert ("IS", "P", "D00001") in dimap and ("IS", "", "D00001") in dimap, dimap
assert all(e in ("IP", "IS", "ED", "AV", "OA") for e, _p, _c in dimap), dimap
assert util_ok and di_ok
print("  IS/P entity id:", is_p_id, " distindex_episodes hoi:", hoi_lists)
assert is_p_id and hoi_lists == [(str(is_p_id[0]),)], (is_p_id, hoi_lists)

for token, expect_adate in ((("IS", "P"), date(2014, 3, 12)),
                            (("IS", ""), date(2014, 3, 19))):
    study_tok = replace(
        base34,
        cohorts=tuple(
            replace(c, event_care_settings=(("D00001",) + token,))
            for c in base34.cohorts
        ),
    )
    study_tok.validate()
    with StageHarness(study_tok) as h:
        h.attach_dataset(f34)
        h.run_through("exposure + stockpiling")
        ev = h.engine.con.execute(
            "SELECT adate FROM event_claims ORDER BY adate").fetchall()
    print(f"  event_claims under care setting {token}:", ev)
    assert ev == [(expect_adate,)], (token, ev)
print("  -> enc_is counted, IS in distindex, IS not enveloped, "
      "pdx x enctype matching OK")

# ---------------------------------------------------------------------
# Family 5: NULL-enctype claim -> 'XX': event yes, distindex entity no.
# ---------------------------------------------------------------------
f5 = hand_dataset(
    patients=(patient(1),),
    enrollment=(span(1, W_START, W_END),),
    dispensings=(disp(1, date(2014, 3, 1), "E00001", 30, 10.0),),
    diagnoses=(Diagnosis(1, date(2014, 3, 15), "D00001", "09", "P", None),),
)
print("family 5 ('XX' fallback claim):")
with StageHarness(study_with_features(
        ("E00001",), ("D00001",), strata=CIDA_LEVELS,
        start=W_START, end=W_END)) as h:
    h.attach_dataset(f5)
    h.run_through("code distribution")
    dx = h.engine.con.execute(
        "SELECT adate, code, pdx, enctype FROM cdm_diagnosis").fetchall()
    fin = h.engine.con.execute(
        "SELECT patid, feventdt, numevents FROM cohort_final").fetchall()
    xx_entities = h.engine.con.execute(
        "SELECT count(*) FROM distindexmap WHERE enctype = 'XX'").fetchone()
    hoi = h.engine.con.execute(
        "SELECT distindexhoi FROM distindex_episodes").fetchall()
    expected_di = d_stages.expected_distindex(
        _rows(h, "cohort_final"), _rows(h, "stockpiled"),
        _envdx(h), _envpx(h), _rows(h, "cdm_dispensing"),
        _rows(h, "cfg_codes"), _rows(h, "cfg_care_setting"),
    )
    di_ok = all(
        d_stages.canonical(_rows(h, t)) == d_stages.canonical(expected_di[t])
        for t in ("distindexmap", "distindex", "distindex_episodes")
    )
print("  cdm_diagnosis:", dx)
print("  cohort_final (patid, feventdt, numevents):", fin)
print("  distindex 'XX' entities:", xx_entities[0],
      " distindex_episodes hoi:", hoi)
print("  distindex == oracle:", di_ok)
assert dx == [(date(2014, 3, 15), "D00001", "P", "XX")], dx
assert fin and fin[0][1] == date(2014, 3, 15) and fin[0][2] >= 1, fin
assert xx_entities[0] == 0, xx_entities
assert all(r[0] is None for r in hoi), hoi
assert di_ok
print("  -> event fires under '**', no distindex entity, oracle agrees OK")

# ---------------------------------------------------------------------
# Family 6: alternate Rx codetype (SN) capture vs exclusion.
# ---------------------------------------------------------------------
f6 = hand_dataset(
    patients=(patient(1),),
    enrollment=(span(1, W_START, W_END),),
    dispensings=(
        disp(1, date(2014, 3, 1), "E00001", 30, 10.0, codetype="SN"),
        disp(1, date(2014, 4, 10), "E00001", 30, 10.0, codetype="ND"),
    ),
)
print("family 6 (Rx codetype SN vs ND):")
for ct, expect_day in (("SN", date(2014, 3, 1)), ("ND", date(2014, 4, 10))):
    study_ct = replace(
        study_with_features(("E00001",), (), strata=CIDA_LEVELS,
                            start=W_START, end=W_END),
        cohorts=(
            replace(
                study_with_features(("E00001",), (), strata=CIDA_LEVELS,
                                    start=W_START, end=W_END).cohorts[0],
                exposure_codes=(("E00001", "RX", ct, None),),
            ),
        ),
    )
    study_ct.validate()
    with StageHarness(study_ct) as h:
        h.attach_dataset(f6)
        h.run_through("index dates")
        ec = h.engine.con.execute(
            "SELECT adate FROM exposure_claims ORDER BY adate").fetchall()
        idx = h.engine.con.execute(
            "SELECT DISTINCT adate FROM index_candidates").fetchall()
    print(f"  study codetype {ct}: exposure_claims {ec}, index {idx}")
    assert ec == [(expect_day,)], (ct, ec)
    assert idx == [(expect_day,)], (ct, idx)
print("  -> codetype equality filters fills symmetrically OK")

# ---------------------------------------------------------------------
# Family 7: procedure enveloping inside/outside an IP stay.
# ---------------------------------------------------------------------
f7 = hand_dataset(
    patients=(patient(1),),
    enrollment=(span(1, W_START, W_END),),
    dispensings=(disp(1, date(2014, 3, 1), "E00001", 30, 10.0),),
    procedures=(
        Procedure(1, date(2014, 8, 3), "PX1", "HC", "AV", 10, 1001, None),
        Procedure(1, date(2014, 7, 1), "PX2", "HC", "AV", 11, 1001, None),
    ),
    encounters=(
        Encounter(1, date(2014, 8, 1), "IP", 10,
                  ddate=date(2014, 8, 5), discharge_status=""),
    ),
)
print("family 7 (procedure enveloping):")
with StageHarness(study_with_features(
        ("E00001",), (), strata=CIDA_LEVELS,
        start=W_START, end=W_END)) as h:
    h.attach_dataset(f7)
    h.run_through("normalize")
    px = h.engine.con.execute(
        "SELECT adate, code, enctype, pdx FROM cdm_procedure"
        " ORDER BY adate").fetchall()
print("  cdm_procedure (adate, code, enctype, pdx):", px)
assert px == [
    (date(2014, 7, 1), "PX2", "AV", ""),    # outside stay: untouched
    (date(2014, 8, 3), "PX1", "IP", "X"),   # inside stay: re-filed
], px
print("  -> procedure re-filed IP/'X' inside the stay only OK")
print("all families passed")
