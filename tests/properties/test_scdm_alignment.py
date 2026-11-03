"""SCDM 8.2.2 alignment boundary tests (full-surface audit follow-up).

Each test pins pipeline behavior on an SCDM-legal input shape the
generators previously never drew; the SCDM reference citation is in the
test docstring. Audit trail: investigation/packages/SCDM-ALIGNMENT.md
(items A2, A3, B1, B2, B3, B5, C1, C2); executed probe evidence:
investigation/probes/scdm_full_alignment_probe.py and
investigation/witnesses/scdm-full-alignment-probe.txt.

The conditionless-study default admits every SCDM-legal demographic
value (qrp.config CohortConfig: sex F/M/U/A, race 0-5+M, hispanic
Y/N/U), so the drawn shapes flow through cfg_demog eligibility — the
misalignment was purely generator-side, fixed in synth.py.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from tests.properties.harness import StageHarness
from tests.properties.oracles import d_stages
from tests.properties.oracles.b_pkg_fixtures import (
    END, NOISE_CODE, START, disp, patient, span, two_cohort_study,
)
from tests.properties.oracles.d_fixtures import CIDA_LEVELS, study_with_features
from tests.properties.synth import (
    Diagnosis, Encounter, Procedure, SyntheticDataset,
)

W_START = date(2014, 1, 1)
W_END = date(2014, 12, 31)


def _hand_dataset(
    *,
    patients,
    enrollment=(), dispensings=(), diagnoses=(), procedures=(),
    encounters=(), deaths=(), labs=(),
) -> SyntheticDataset:
    """A hand extract over the 2014 window (all eight tables settable)."""
    return SyntheticDataset(
        patients=patients, deaths=deaths, enrollment=enrollment,
        dispensings=dispensings, diagnoses=diagnoses, procedures=procedures,
        encounters=encounters, labs=labs,
        exposure_codes=("E00001", "E00002"), event_codes=("D00001",),
        noise_drug_codes=(NOISE_CODE,), noise_dx_codes=("X00001",),
        start=W_START, end=W_END,
    )


def _rows(harness: StageHarness, table: str) -> list[dict[str, Any]]:
    return d_stages.table_dicts(harness.engine.con, table)


def _envdx(harness: StageHarness) -> list[dict[str, Any]]:
    rows = _rows(harness, "cdm_diagnosis")
    for r in rows:
        r["codecat"] = "DX"
    return rows


def _envpx(harness: StageHarness) -> list[dict[str, Any]]:
    rows = _rows(harness, "cdm_procedure")
    for r in rows:
        r["codecat"] = "PX"
    return rows


def _attrition_remaining(harness: StageHarness, level: float) -> set[tuple[str, int]]:
    return {
        (r["group"], r["remaining"])
        for r in _rows(harness, "attrition")
        if r["level"] == level
    }


def _sex_domain_dataset() -> SyntheticDataset:
    """Three members with SCDM-legal sex values (demographic.md: Sex
    domain A/F/M/U), one fill each, fully enrolled."""
    return _hand_dataset(
        patients=(patient(1, sex="U"), patient(2, sex="A"), patient(3, sex="F")),
        enrollment=tuple(span(i, START, END) for i in (1, 2, 3)),
        dispensings=tuple(
            disp(i, START + timedelta(days=200), "E00001", 30, 10.0)
            for i in (1, 2, 3)
        ),
    )


def test_sex_u_a_flow_through_conditionless_study_and_collapse_to_o(
        tmp_path, monkeypatch) -> None:
    """SCDM 8.2.2 demographic.md Sex = A/F/M/U (audit A2).

    A conditionless study admits F/M/U/A (cfg_demog joins the RAW
    value, 45_pov1.sql); the covariate collapses non-F/M to 'O'
    (10_normalize.sql). Attrition level 1 counts all three."""
    ds = _sex_domain_dataset()
    with StageHarness(two_cohort_study()) as h:
        h.attach_dataset(ds)
        h.run_through("attrition + denominators")
        dem = h.engine.con.execute(
            "SELECT patid, sex_raw, sex FROM demographics ORDER BY patid"
        ).fetchall()
        pov1 = {r[0] for r in h.engine.con.execute(
            "SELECT DISTINCT patid FROM pov1").fetchall()}
        lvl1 = _attrition_remaining(h, 1.0)
    assert dem == [(1, "U", "O"), (2, "A", "O"), (3, "F", "F")], dem
    assert pov1 == {1, 2, 3}, pov1
    assert lvl1 and all(n == 3 for _g, n in lvl1), lvl1


def test_sex_restriction_excludes_u_a_at_pov1_and_attrition_is_unmoved() -> None:
    """SCDM 8.2.2 demographic.md Sex = A/F/M/U (audit A2).

    Under sex=("F","M") the U/A members fail the cfg_demog raw-value
    join and vanish from pov1 — while attrition levels 1-5 still count
    them, because levels 3-5 are CARRIED from level 2 (70_outputs.sql:
    123-127, documented in-file as SAS-unverified). The exclusion is
    invisible in the funnel by design."""
    ds = _sex_domain_dataset()
    with StageHarness(two_cohort_study(
            cohort_overrides={"sex": ("F", "M")})) as h:
        h.attach_dataset(ds)
        h.run_through("attrition + denominators")
        pov1 = {r[0] for r in h.engine.con.execute(
            "SELECT DISTINCT patid FROM pov1").fetchall()}
        carried = {lvl: _attrition_remaining(h, lvl)
                   for lvl in (1.0, 2.0, 5.0)}
    assert pov1 == {3}, pov1
    assert all(n == 3 for rows in carried.values() for _g, n in rows), carried


def test_null_birth_date_member_dropped_at_attrition_level_1() -> None:
    """SCDM 8.2.2 demographic.md Birth_Date is "missing allowed" (audit
    A3); the pipeline requires it (SAS parity).

    A null-birth-date member is absent from _att_dem (attrition level
    1, 70_outputs.sql:29-31) and excluded from pov1 via
    demographics_missing (10_normalize.sql:72-75, 45_pov1.sql)."""
    ds = _hand_dataset(
        patients=(patient(1), patient(2, None, "F")),
        enrollment=(span(1, START, END), span(2, START, END)),
        dispensings=(
            disp(1, START + timedelta(days=200), "E00001", 30, 10.0),
            disp(2, START + timedelta(days=200), "E00001", 30, 10.0),
        ),
    )
    with StageHarness(two_cohort_study()) as h:
        h.attach_dataset(ds)
        h.run_through("attrition + denominators")
        missing = {r[0] for r in h.engine.con.execute(
            "SELECT patid FROM demographics_missing").fetchall()}
        pov1 = {r[0] for r in h.engine.con.execute(
            "SELECT DISTINCT patid FROM pov1").fetchall()}
        mstr = {r[0] for r in h.engine.con.execute(
            "SELECT DISTINCT patid FROM ptsmasterlist").fetchall()}
        lvl1 = _attrition_remaining(h, 1.0)
    assert missing == {2}, missing
    assert pov1 == {1} and mstr == {1}, (pov1, mstr)
    assert lvl1 and all(n == 1 for _g, n in lvl1), lvl1


def _is_dataset() -> SyntheticDataset:
    """One member; 30-day episode from a 2014-03-01 fill; IS and IP
    stays; four claims all inside the episode window (follow-up events
    must fall between atriskindexdt and episodeenddt, 60_followup.sql).

      (a) D00001 IS  pdx P  on 3/12 — first event under '**'
      (b) X00001 IS  pdx '' on 3/20 — noise, inside the IS stay
      (c) D00001 AV  pdx '' on 3/25 — inside the IP stay -> enveloped
      (d) D00001 IS  pdx '' on 3/19
    """
    return _hand_dataset(
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


def _is_study():
    return study_with_features(
        ("E00001",), ("D00001",),
        utilization=(("cohort_a", "MED", -30, 365),),
        strata=CIDA_LEVELS, start=W_START, end=W_END,
    )


def test_is_encounters_counted_and_is_claims_not_enveloped() -> None:
    """SCDM 8.2.2 encounter.md/diagnosis.md EncType IS = Non-Acute
    Institutional Stay (audits B1 + C1).

    IS is a first-class setting: counted in enc_is (74_utilization.sql
    :75), a member of the '**' distindex expansion
    (72_codedistribution.sql:81-84), and NOT an enveloping day —
    10_normalize.sql reads IP encounters only (:140), so an IS-dated
    ambulatory claim keeps its own setting. Independent oracles
    (d_stages) reproduce every table compared here."""
    ds = _is_dataset()
    with StageHarness(_is_study()) as h:
        h.attach_dataset(ds)
        h.run_through("utilization")
        dx = h.engine.con.execute(
            "SELECT adate, code, pdx, enctype FROM cdm_diagnosis"
            " ORDER BY adate").fetchall()
        util = h.engine.con.execute(
            "SELECT enc_av, enc_oa, enc_ip, enc_is, enc_ed, enc_total,"
            " enc_days FROM utilization").fetchall()
        dimap = h.engine.con.execute(
            "SELECT DISTINCT enctype, pdx, code FROM distindexmap"
            " WHERE codecat = 'DX' ORDER BY 1, 2, 3").fetchall()
        is_p_id = h.engine.con.execute(
            "SELECT distindexid FROM distindexmap"
            " WHERE codecat = 'DX' AND enctype = 'IS' AND pdx = 'P'"
            " AND code = 'D00001'").fetchone()
        hoi = h.engine.con.execute(
            "SELECT distindexhoi FROM distindex_episodes").fetchall()
        expected_util = d_stages.expected_utilization(
            _rows(h, "ptsmasterlist"), _rows(h, "encounter"),
            _rows(h, "cdm_dispensing"), _rows(h, "cfg_utilization"),
            {c["cohortgrp"] for c in _rows(h, "cfg_cohort")
             if c["create_baseline"]},
            _rows(h, "cfg_drugclass"),
        )
        expected_di = d_stages.expected_distindex(
            _rows(h, "cohort_final"), _rows(h, "stockpiled"),
            _envdx(h), _envpx(h), _rows(h, "cdm_dispensing"),
            _rows(h, "cfg_codes"), _rows(h, "cfg_care_setting"),
        )
        util_rows = _rows(h, "utilization")
        util_summary = _rows(h, "utilization_summary")
        di_rows = {t: _rows(h, t) for t in
                   ("distindexmap", "distindex", "distindex_episodes")}
    assert dx == [
        (date(2014, 3, 12), "D00001", "P", "IS"),   # IS: not enveloped
        (date(2014, 3, 19), "D00001", "", "IS"),
        (date(2014, 3, 20), "X00001", "", "IS"),
        (date(2014, 3, 25), "D00001", "X", "IP"),   # AV on IP day: enveloped
    ], dx
    assert util == [(0, 0, 1, 1, 0, 2, 2)], util
    assert ("IS", "P", "D00001") in dimap and ("IS", "", "D00001") in dimap
    assert all(e in ("IP", "IS", "ED", "AV", "OA") for e, _p, _c in dimap)
    assert is_p_id and hoi == [(str(is_p_id[0]),)], (is_p_id, hoi)
    assert d_stages.canonical(util_rows) == d_stages.canonical(expected_util)
    assert d_stages.canonical(util_summary) == d_stages.canonical(
        d_stages.expected_utilization_summary(expected_util))
    for t in ("distindexmap", "distindex", "distindex_episodes"):
        assert d_stages.canonical(di_rows[t]) == d_stages.canonical(
            expected_di[t]), t


def test_pdx_restricted_care_settings_match_only_legal_shapes() -> None:
    """SCDM 8.2.2 diagnosis.md Pdx: P/S/X/blank, populated only for IP
    and IS (audit B2).

    A restricted token ('IS','P') captures only IS claims carrying P;
    ('IS','') only blank-pdx IS claims. An enveloped claim (rewritten
    to IP/'X') matches neither — every input shape here is
    SCDM-legal."""
    import pytest

    ds = _is_dataset()
    base = _is_study()
    for token, expect in ((("IS", "P"), date(2014, 3, 12)),
                          (("IS", ""), date(2014, 3, 19))):
        study = replace(
            base,
            cohorts=tuple(
                replace(c, event_care_settings=(("D00001",) + token,))
                for c in base.cohorts
            ),
        )
        study.validate()
        with StageHarness(study) as h:
            h.attach_dataset(ds)
            h.run_through("exposure + stockpiling")
            ev = h.engine.con.execute(
                "SELECT adate FROM event_claims ORDER BY adate").fetchall()
        assert ev == [(expect,)], (token, ev)


def test_xx_enctype_claim_counts_as_event_but_matches_no_distindex_entity() -> None:
    """SCDM 8.2.2 diagnosis.md EncType domain has no 'XX' (audit C2) —
    'XX' is this pipeline's fallback for a missing/null column
    (10_normalize.sql:160).

    Under a '**' care setting the claim still fires as an event
    (30_exposure.sql keeps the literal wildcard), but matches no
    distindex entity — the wildcard expands to the five concrete
    enctypes and compares exactly (72_codedistribution.sql:81-84). SAS
    parity per the quoted ms_caresettingprincipal /
    ms_codedistribution semantics; the oracle agrees on both sides."""
    ds = _hand_dataset(
        patients=(patient(1),),
        enrollment=(span(1, W_START, W_END),),
        dispensings=(disp(1, date(2014, 3, 1), "E00001", 30, 10.0),),
        diagnoses=(Diagnosis(1, date(2014, 3, 15), "D00001", "09", "P", None),),
    )
    with StageHarness(study_with_features(
            ("E00001",), ("D00001",), strata=CIDA_LEVELS,
            start=W_START, end=W_END)) as h:
        h.attach_dataset(ds)
        h.run_through("code distribution")
        dx = h.engine.con.execute(
            "SELECT adate, code, pdx, enctype FROM cdm_diagnosis").fetchall()
        fin = h.engine.con.execute(
            "SELECT patid, feventdt, numevents FROM cohort_final").fetchall()
        xx = h.engine.con.execute(
            "SELECT count(*) FROM distindexmap WHERE enctype = 'XX'").fetchone()
        hoi = h.engine.con.execute(
            "SELECT distindexhoi FROM distindex_episodes").fetchall()
        expected_di = d_stages.expected_distindex(
            _rows(h, "cohort_final"), _rows(h, "stockpiled"),
            _envdx(h), _envpx(h), _rows(h, "cdm_dispensing"),
            _rows(h, "cfg_codes"), _rows(h, "cfg_care_setting"),
        )
        di_rows = {t: _rows(h, t) for t in
                   ("distindexmap", "distindex", "distindex_episodes")}
    assert dx == [(date(2014, 3, 15), "D00001", "P", "XX")], dx
    assert fin and fin[0][1] == date(2014, 3, 15) and fin[0][2] >= 1, fin
    assert xx[0] == 0, xx
    assert all(r[0] is None for r in hoi), hoi
    for t in ("distindexmap", "distindex", "distindex_episodes"):
        assert d_stages.canonical(di_rows[t]) == d_stages.canonical(
            expected_di[t]), t


def test_alternate_rx_codetype_captures_only_the_matching_fill() -> None:
    """SCDM 8.2.2 dispensing.md Rx_CodeType domain
    ND/SN/SK/DM/DI/RN/AT (audit B3).

    Codetype is matched by equality (30_exposure.sql:76-82): an
    SN-labeled fill is captured when the study says SN and silently
    excluded when it says ND — the ND/ND diagonal never exercised
    this."""
    ds = _hand_dataset(
        patients=(patient(1),),
        enrollment=(span(1, W_START, W_END),),
        dispensings=(
            disp(1, date(2014, 3, 1), "E00001", 30, 10.0, codetype="SN"),
            disp(1, date(2014, 4, 10), "E00001", 30, 10.0, codetype="ND"),
        ),
    )
    for ct, expect_day in (("SN", date(2014, 3, 1)),
                           ("ND", date(2014, 4, 10))):
        base = study_with_features(
            ("E00001",), (), strata=CIDA_LEVELS, start=W_START, end=W_END)
        study = replace(
            base,
            cohorts=(replace(
                base.cohorts[0],
                exposure_codes=(("E00001", "RX", ct, None),),
            ),),
        )
        study.validate()
        with StageHarness(study) as h:
            h.attach_dataset(ds)
            h.run_through("index dates")
            ec = h.engine.con.execute(
                "SELECT adate FROM exposure_claims ORDER BY adate").fetchall()
            idx = h.engine.con.execute(
                "SELECT DISTINCT adate FROM index_candidates").fetchall()
        assert ec == [(expect_day,)], (ct, ec)
        assert idx == [(expect_day,)], (ct, idx)


def test_procedure_claims_enveloped_inside_ip_stay_only() -> None:
    """SCDM 8.2.2 procedure.md EncType carries the same five-value
    domain (audit B5); cdm_procedure's enveloping rewrite
    (10_normalize.sql:211-218) previously had zero data coverage.

    An AV procedure dated inside an IP stay is re-filed IP/'X'; outside
    the stay it keeps AV/''."""
    ds = _hand_dataset(
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
    with StageHarness(study_with_features(
            ("E00001",), (), strata=CIDA_LEVELS,
            start=W_START, end=W_END)) as h:
        h.attach_dataset(ds)
        h.run_through("normalize")
        px = h.engine.con.execute(
            "SELECT adate, code, enctype, pdx FROM cdm_procedure"
            " ORDER BY adate").fetchall()
    assert px == [
        (date(2014, 7, 1), "PX2", "AV", ""),    # outside stay: untouched
        (date(2014, 8, 3), "PX1", "IP", "X"),   # inside stay: re-filed
    ], px
