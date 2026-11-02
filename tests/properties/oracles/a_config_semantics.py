# pattern: Functional Core
"""Hand-authored semantic fixtures for the study input-file contract.

Oracle independence
-------------------
Nothing here computes an expectation by calling production translation
code. Every expected value is a LITERAL written by hand from INPUTS.md
(the "Tables consumed today" and "Also read" tables), the SAS-epoch rule
(days since 1960-01-01), and the casing/indirection rules INPUTS.md
documents. The one place arithmetic appears — SAS date integers — is an
independent re-derivation: ``date.toordinal()`` minus the epoch's own
ordinal, with 1960-01-01 spelled out here, never imported from
``qrp.inputfile``.

The cases have two shapes:

* ``SEMANTIC_CASES`` — JSON payloads a study author could really send,
  each with the exact StudyConfig semantics the contract says it means.
  Different spellings of the same meaning (indirection vs literal keys,
  SAS casing, ISO vs SAS-integer dates, list vs string code lists) must
  all land on the SAME hand-written semantics.
* ``BOUNDARY_CASES`` — near-valid variants. Each is pre-classified by
  hand as either ``"documented"`` (the package rejects it with its own
  configuration-error message; the fragment quoted comes from the
  documented rule) or ``"incidental"`` (no documented rule covers it; an
  exception escapes, and its type is characterization evidence, not a
  contract). ``"accepts"`` cases pin that a boundary input is VALID and
  record the exact resulting semantics.

Keep payloads small and boring: this module is an expectation table, not
a study generator.
"""

from __future__ import annotations

from datetime import date
from typing import Any

# INPUTS.md: dates export as "days since 1960-01-01".
_SAS_EPOCH_ORDINAL = date(1960, 1, 1).toordinal()


def sas_day(d: date) -> int:
    """The SAS date integer for ``d``, derived from the documented epoch."""
    return d.toordinal() - _SAS_EPOCH_ORDINAL


# ---------------------------------------------------------------------
# The base study: what the fixture below MEANS, hand-written.
# ---------------------------------------------------------------------

BASE_PERIOD = (date(2016, 1, 1), date(2016, 12, 31), date(2017, 1, 31))

BASE_EXPECT: dict[str, Any] = {
    "study_type": 2,
    "start_date": BASE_PERIOD[0],
    "end_date": BASE_PERIOD[1],
    "censor_date": BASE_PERIOD[2],
    "run_id": "wp307like",
    "n_cohorts": 2,
    # cohort_a: every INPUTS.md-listed COHORTFILE/TYPE2FILE field set
    # explicitly; defaults only where the field is genuinely absent.
    "cohort:cohort_a.enrollment_num": 3,
    "cohort:cohort_a.coverage": "MD",          # file says "md"
    "cohort:cohort_a.enrol_gap": 30,
    "cohort:cohort_a.chart_required": True,    # chartres "Y"
    "cohort:cohort_a.enr_days": 200,
    "cohort:cohort_a.sex": ("F", "M"),         # list ["f", " m "]
    "cohort:cohort_a.race": ("0", "1", "2", "3", "4", "5", "M"),  # absent
    "cohort:cohort_a.hispanic": ("N",),        # ["n"]
    "cohort:cohort_a.age_strata": (
        (1, "65+", 65, 99999, "years"),
        (2, "18-44y", 18, 44, "years"),
    ),
    "cohort:cohort_a.create_baseline": False,  # createbaseline "N"
    "cohort:cohort_a.wash_per": 100,           # t2washper
    "cohort:cohort_a.point": False,
    "cohort:cohort_a.episode_gap": 5,
    "cohort:cohort_a.episode_gap_type": "P",   # "p"
    "cohort:cohort_a.exp_ext_per": 7,
    "cohort:cohort_a.min_epis_dur": 2,
    "cohort:cohort_a.max_epis_dur": 400,
    "cohort:cohort_a.min_days_supp": 3,
    "cohort:cohort_a.at_risk_start": 0,
    "cohort:cohort_a.blackout_per": 0,
    "cohort:cohort_a.fup_wash_per": None,      # t2fupwashper absent
    "cohort:cohort_a.event_count": 1,
    "cohort:cohort_a.req_days_aft_ind": 5,
    "cohort:cohort_a.req_days_aft_epi": 2,
    "cohort:cohort_a.censor_death": False,     # censor_dth "N"
    "cohort:cohort_a.output_denom": "M",
    "cohort:cohort_a.code_supply": 30,         # the one DEF code carrying it
    # (code, codecat, codetype, codesupply); codetype upcased, absent -> ""
    "cohort:cohort_a.exposure_codes": (
        ("00700", "RX", "ND", None),
        ("070.10", "RX", "", None),
        ("J1234", "PX", "", 30),
    ),
    # stockgroups of DEF and TRUNK codes, sorted; EVERY code lands in
    # one, "_default" when the file's stockgroup column is absent
    "cohort:cohort_a.exposure_stockgroups": (
        ("00700", "ace"),
        ("070.10", "_default"),
        ("J1234", "_default"),
        ("Z450", "tr"),
    ),
    # caresettingprincipal "IP*" -> enctype "IP", wildcard pdx; the IOC
    # code carries none, so it resolves to the all-settings wildcard
    "cohort:cohort_a.event_care_settings": (
        ("410.1", "IP", "*"),
        ("V700", "**", "*"),
    ),
    # EVENT routing is on fupcriteria='DEF', not indexcriteria
    "cohort:cohort_a.event_codes": (("410.1", "DX", "10", None),),
    "cohort:cohort_a.ioc_codes": (("V700", "DX", "", None),),
    "cohort:cohort_a.trunc_codes": (("Z450", "PX", "", None),),
    # cohort_b: identity plus one code; everything else defaults.
    "cohort:cohort_b.enrollment_num": 1,
    "cohort:cohort_b.coverage": "MD",
    "cohort:cohort_b.enrol_gap": 0,
    "cohort:cohort_b.chart_required": False,
    "cohort:cohort_b.enr_days": 183,
    "cohort:cohort_b.sex": ("F", "M", "U", "A"),
    "cohort:cohort_b.hispanic": ("Y", "N", "U"),
    "cohort:cohort_b.wash_per": None,          # no TYPE2FILE row at all
    "cohort:cohort_b.episode_gap": 0,
    "cohort:cohort_b.min_epis_dur": 1,
    "cohort:cohort_b.censor_death": True,
    "cohort:cohort_b.output_denom": "Y",
    "cohort:cohort_b.exposure_codes": (("N00010", "RX", "ND", None),),
    "cohort:cohort_b.exposure_stockgroups": (("N00010", "_default"),),
}

# The subset a representation-variant must still reproduce verbatim.
CORE_SEMANTICS_KEYS = (
    "study_type", "start_date", "end_date", "censor_date", "run_id",
    "n_cohorts",
    "cohort:cohort_a.coverage", "cohort:cohort_a.enr_days",
    "cohort:cohort_a.wash_per", "cohort:cohort_a.sex",
    "cohort:cohort_a.exposure_codes",
    "cohort:cohort_a.exposure_stockgroups",
    "cohort:cohort_a.event_codes",
    "cohort:cohort_b.exposure_codes",
    "cohort:cohort_b.enr_days",
)

# ---------------------------------------------------------------------
# The base payload, in SAS's own shape (dataset-name keys, PROC JSON
# casing, SAS date integers), exactly as INPUTS.md describes it.
# ---------------------------------------------------------------------

_COHORT_A_NAME = "AnMod_Mpl1r_CohortFile_V3"
_TYPE2_NAME = "AnMod_Mpl1r_Type2_V3"
_CODES_NAME = "AnMod_Mpl1r_CohortCodes_V3"


def base_payload() -> dict[str, Any]:
    """The base study as SAS's create_json.sas would write it."""
    return {
        "QRP_PARAMETERS": [
            {"parameter": "runid", "run1": "wp307like"},
            {"parameter": "type", "run1": 2},
            {"parameter": "startdate", "run1": sas_day(BASE_PERIOD[0])},
            {"parameter": "enddate", "run1": sas_day(BASE_PERIOD[1])},
            {"parameter": "censordate", "run1": sas_day(BASE_PERIOD[2])},
            # the indirection INPUTS.md documents: logical -> SAS dataset
            {"parameter": "cohortfile", "run1": _COHORT_A_NAME},
            {"parameter": "type2file", "run1": _TYPE2_NAME},
            {"parameter": "cohortcodes", "run1": _CODES_NAME},
        ],
        _COHORT_A_NAME: [
            {
                "CohortGrp": "cohort_a", "EnrollmentNum": 3,
                "Coverage": "md", "EnrolGap": 30, "ChartRes": "Y",
                "EnrDays": 200, "AgeStrat": "65+ 18-44y",
                "Sex": ["f", " m "], "Hispanic": ["n"],
                "ReqDaysAftInd": 5, "CreateBaseline": "N",
            },
            {"CohortGrp": "cohort_b"},
        ],
        _TYPE2_NAME: [
            {
                "Group": "cohort_a", "T2WashPer": 100, "Point": "N",
                "EpisodeGap": 5, "EpisodeGapType": "p", "ExpExtPer": 7,
                "MinEpisDur": 2, "MaxEpisDur": 400, "MinDaysupp": 3,
                "EventCount": 1, "ReqDaysAftEpi": 2, "Censor_Dth": "N",
                "OutputDenom": "M",
            },
        ],
        _CODES_NAME: [
            # exposure (indexcriteria DEF)
            {"Group": "cohort_a", "IndexCriteria": "DEF",
             "Code": "00700", "CodeCat": "rx", "CodeType": "nd",
             "StockGroup": "ace"},
            # no codetype at all: match any code system
            {"Group": "cohort_a", "IndexCriteria": "DEF",
             "Code": "070.10", "CodeCat": "RX"},
            # PX exposure with per-code supply
            {"Group": "cohort_a", "IndexCriteria": "DEF",
             "Code": "J1234", "CodeCat": "PX", "CodeSupply": 30},
            # outcome: routed by FUPCRITERIA, not indexcriteria
            {"Group": "cohort_a", "IndexCriteria": "NOT",
             "FupCriteria": "DEF", "Code": "410.1", "CodeCat": "DX",
             "CodeType": "10", "CareSettingPrincipal": "IP*"},
            # washout-only code
            {"Group": "cohort_a", "IndexCriteria": "NOT",
             "FupCriteria": "IOC", "Code": "V700", "CodeCat": "DX"},
            # truncation code
            {"Group": "cohort_a", "IndexCriteria": "FUT",
             "Code": "Z450", "CodeCat": "PX", "StockGroup": "tr"},
            {"Group": "cohort_b", "IndexCriteria": "DEF",
             "Code": "N00010", "CodeCat": "RX", "CodeType": "ND"},
        ],
    }


# ---------------------------------------------------------------------
# Semantic cases: alternative spellings that must mean the same thing.
# Each entry: (name, payload, lookup-or-None, expected-subset or FULL).
# ---------------------------------------------------------------------

def _sameday_ok_case() -> tuple[str, dict[str, Any], None, dict[str, Any]]:
    """Stockpiling row semantics, hand-written from the INPUTS.md table."""
    payload = base_payload()
    # re-spell the table keys to lowercase and keep the indirection
    # pointing at the renamed keys
    params = payload["QRP_PARAMETERS"]
    pmap = {r["parameter"].strip().lower(): r for r in params}
    for logical, key in (("type2file", "type2file"),
                         ("cohortfile", "cohortfile"),
                         ("cohortcodes", "cohortcodes")):
        old = pmap[logical]["run1"]
        payload[old.lower()] = payload.pop(old)
        pmap[logical]["run1"] = old.lower()
    payload["stockpilingfile"] = [
        {"Group": "cohort_a", "StockGroup": "ace", "SameDay": "nm",
         "SupRange": "1-90", "AmtRange": "0<-HIGH", "PercentDays": 0.25},
    ]
    # sameday letter 1 -> supply op, letter 2 -> amount op (INPUTS.md)
    expect = {
        "stockpiling": ((
            "cohort_a", "ace", "min", "mean",
            ((1.0, False, 90.0, False),),
            ((0.0, True, float("inf"), False),),
            0.25,
        ),),
    }
    return "stockpiling_ops_and_ranges", payload, None, expect


SEMANTIC_CASES: list[tuple[str, dict[str, Any], dict[str, Any] | None,
                           dict[str, Any]]] = [
    (
        "sas_indirect",
        base_payload(),
        None,
        BASE_EXPECT,
    ),
    (
        "hand_written_literal_keys_leap_days",
        {
            # hand-written files still carry qrp_parameters as a row
            # list (PROC JSON's shape); the dates are the ISO spellings
            "qrp_parameters": [
                {"parameter": "runid", "run1": "demo"},
                {"parameter": "type", "run1": 2},
                {"parameter": "startdate", "run1": "2000-02-29"},
                {"parameter": "enddate", "run1": "2012-02-29"},
            ],
            "cohortfile": [
                {"cohortgrp": "cohort_a"},
                {"cohortgrp": "cohort_b"},
            ],
            "cohortcodes": [
                {"group": "cohort_a", "indexcriteria": "DEF",
                 "code": "00700", "codecat": "RX", "codetype": "ND"},
                {"group": "cohort_b", "indexcriteria": "DEF",
                 "code": "N00010", "codecat": "RX", "codetype": "ND"},
            ],
        },
        None,
        {
            "start_date": date(2000, 2, 29),
            "end_date": date(2012, 2, 29),
            # no censordate -> None; the effective censor is the end date
            "censor_date": None,
            "run_id": "demo",
            "n_cohorts": 2,
            "cohort:cohort_a.exposure_codes": (("00700", "RX", "ND", None),),
            "cohort:cohort_b.exposure_codes": (("N00010", "RX", "ND", None),),
        },
    ),
    (
        "sas_integer_leap_days",
        {
            "qrp_parameters": [
                {"parameter": "runid", "run1": "leapint"},
                {"parameter": "type", "run1": 2},
                {"parameter": "startdate", "run1": sas_day(date(2000, 2, 29))},
                {"parameter": "enddate", "run1": sas_day(date(2012, 2, 29))},
            ],
            "cohortfile": [
                {"cohortgrp": "cohort_a"},
                {"cohortgrp": "cohort_b"},
            ],
            "cohortcodes": [
                {"group": "cohort_a", "indexcriteria": "DEF",
                 "code": "00700", "codecat": "RX", "codetype": "ND"},
                {"group": "cohort_b", "indexcriteria": "DEF",
                 "code": "N00010", "codecat": "RX", "codetype": "ND"},
            ],
        },
        None,
        {
            "start_date": date(2000, 2, 29),
            "end_date": date(2012, 2, 29),
            "run_id": "leapint",
        },
    ),
    (
        "lookup_file_resolves_missing_tables",
        # The study JSON carries only the params table; the lookup file
        # supplies the rest (the CLI's --lookup path).
        {
            "qrp_parameters": [
                {"parameter": "runid", "run1": "lookuprun"},
                {"parameter": "type", "run1": 2},
                {"parameter": "startdate", "run1": sas_day(BASE_PERIOD[0])},
                {"parameter": "enddate", "run1": sas_day(BASE_PERIOD[1])},
                {"parameter": "cohortfile", "run1": "lookup_cohortfile"},
                {"parameter": "cohortcodes", "run1": "lookup_cohortcodes"},
            ],
        },
        {
            "lookup_cohortfile": [
                {"cohortgrp": "cohort_a"}, {"cohortgrp": "cohort_b"},
            ],
            "lookup_cohortcodes": [
                {"group": "cohort_a", "indexcriteria": "DEF",
                 "code": "00700", "codecat": "RX", "codetype": "ND"},
                {"group": "cohort_b", "indexcriteria": "DEF",
                 "code": "N00010", "codecat": "RX", "codetype": "ND"},
            ],
        },
        {
            "start_date": BASE_PERIOD[0],
            "end_date": BASE_PERIOD[1],
            "run_id": "lookuprun",
            "n_cohorts": 2,
            "cohort:cohort_a.exposure_codes": (("00700", "RX", "ND", None),),
            "cohort:cohort_b.exposure_codes": (("N00010", "RX", "ND", None),),
        },
    ),
    (
        "unicode_group_and_special_path",
        # Unicode cohort name and run id must survive translation
        # verbatim; the FILE may live at a quoted, spaced, Unicode path.
        {
            "qrp_parameters": [
                {"parameter": "runid", "run1": "héllo wörld[1]"},
                {"parameter": "type", "run1": 2},
                {"parameter": "startdate", "run1": "2016-01-01"},
                {"parameter": "enddate", "run1": "2016-12-31"},
            ],
            "cohortfile": [{"cohortgrp": "kohorte_ü"}],
            "cohortcodes": [
                {"group": "kohorte_ü", "indexcriteria": "DEF",
                 "code": "00700", "codecat": "RX", "codetype": "ND"},
            ],
        },
        None,
        {
            "run_id": "h_llo_w_rld_1",   # non-[A-Za-z0-9_-] -> underscore
            "n_cohorts": 1,
            "cohort:kohorte_ü.exposure_codes": (("00700", "RX", "ND", None),),
        },
    ),
    _sameday_ok_case(),
]

# ---------------------------------------------------------------------
# Coincident bounds: min==max shapes pass 1 never generated. Each knob
# drives one documented knob to a boundary where the two ends of a
# range meet (or a code list repeats a code). Semantics are hand
# expectations, per knob.
# ---------------------------------------------------------------------

COINCIDENT_KNOBS = ("agestrat", "wash_eq_enr", "covar_point", "lab_eq",
                    "sup_eq", "dup_code")


def apply_coincident(payload: dict[str, Any],
                     knobs: tuple[str, ...]) -> dict[str, Any]:
    """Re-spell the base payload with the given coincident bounds.

    Deterministic in (payload, knobs); only the named knobs are
    touched.
    """
    import copy

    out = copy.deepcopy(payload)
    cohortcodes = out[_CODES_NAME]
    if "agestrat" in knobs:
        out[_COHORT_A_NAME][0]["AgeStrat"] = "65-65 00-01"
    if "wash_eq_enr" in knobs:
        out[_TYPE2_NAME][0]["T2WashPer"] = 200     # == EnrDays 200
    if "covar_point" in knobs:
        out["covariatecodes"] = [
            {"CovarNum": 1, "Code": "410.1", "CodeCat": "DX",
             "CovFrom": -30, "CovTo": -30},        # a one-day window
        ]
    if "lab_eq" in knobs:
        out["labcodes"] = [
            {"Group": "cohort_a", "Code": "CREAT", "CodeType": "02N",
             "LabResult": "7:7",                   # lo == hi
             "Ms_Test_Name": "creatinine",         # upcased on load
             "Ms_Test_Sub_Category": "",
             "Specimen_Source": "sr_pls", "Ms_Result_Unit": "mg/dl",
             "Result_Type": "n", "Fast_Ind": "x", "Pt_Loc": "o"},
        ]
    if "sup_eq" in knobs:
        out["stockpilingfile"] = [
            {"Group": "cohort_a", "StockGroup": "ace", "SupRange": "5-5"},
        ]
    if "dup_code" in knobs:
        cohortcodes.append(dict(cohortcodes[0]))   # 00700 twice
    return out


def coincident_expect(knobs: tuple[str, ...]) -> dict[str, Any]:
    """The hand expectations for the knobs that are ON."""
    expect: dict[str, Any] = {}
    if "agestrat" in knobs:
        expect["cohort:cohort_a.age_strata"] = (
            (1, "65-65", 65, 65, "years"),
            (2, "00-01", 0, 1, "years"),
        )
    if "wash_eq_enr" in knobs:
        # wash == enr_days is the accepted edge of the documented rule
        # (the rule rejects only wash_per > enr_days)
        expect["cohort:cohort_a.wash_per"] = 200
    if "covar_point" in knobs:
        expect["covariates"] = "covar1:from=-30,to=-30,codes=1"
    if "lab_eq" in knobs:
        expect["lab_codes"] = ((
            "cohort_a", "CREAT", "LRO", "02", "N",
            "CREATININE", "", "SR_PLS", "MG/DL", "N", "X", "O",
            ":", 7.0, 7.0,
        ),)
    if "sup_eq" in knobs:
        expect["stockpiling"] = ((
            "cohort_a", "ace", "sum", "sum",
            ((5.0, False, 5.0, False),),
            ((0.0, True, float("inf"), False),),
            None,
        ),)
    if "dup_code" in knobs:
        # duplicate code rows are preserved verbatim in order — the
        # translation does not dedupe code lists (identity, not set)
        expect["cohort:cohort_a.exposure_codes"] = (
            ("00700", "RX", "ND", None),
            ("070.10", "RX", "", None),
            ("J1234", "PX", "", 30),
            ("00700", "RX", "ND", None),
        )
    return expect


# The all-knobs authored case rides the same parametrization as the
# other semantic cases; knobs defined above are appended here.
SEMANTIC_CASES.append((
    "coincident_bounds_all",
    apply_coincident(base_payload(), COINCIDENT_KNOBS),
    None,
    coincident_expect(COINCIDENT_KNOBS),
))

# ---------------------------------------------------------------------
# Boundary cases.
#   kind "documented": the package's own configuration error; `fragment`
#       is quoted from the documented rule it enforces.
#   kind "incidental": no documented rule; an exception escapes. The
#       expected type is characterization evidence, not a contract.
#   kind "accepts": the boundary input is VALID; semantics recorded.
# ---------------------------------------------------------------------


def _dict_study(**over: Any) -> dict[str, Any]:
    """A minimal valid study as a load_study_dict payload, overridden."""
    study: dict[str, Any] = {
        "qrp_parameters_scalars": {
            "runid": "bnd", "type": 2,
            "startdate": "2016-01-01", "enddate": "2016-12-31",
        },
        "cohortfile": [{"cohortgrp": "g1"}],
        "cohortcodes": [
            {"group": "g1", "indexcriteria": "DEF",
             "code": "00700", "codecat": "RX", "codetype": "ND"},
        ],
    }
    study.update(over)
    return study


def _t2(**over: Any) -> list[dict[str, Any]]:
    row: dict[str, Any] = {"group": "g1"}
    row.update(over)
    return [row]


BOUNDARY_CASES: list[dict[str, Any]] = [
    # ---- documented cross-parameter rules --------------------------
    dict(
        name="wash_per_above_enr_days", payload=_dict_study(
            type2file=_t2(t2washper=400)),  # enr_days default 183
        kind="documented",
        fragment="wash_per (400) > enr_days (183)",
    ),
    dict(
        name="coverage_not_MD_M_D", payload=_dict_study(
            cohortfile=[{"cohortgrp": "g1", "coverage": "XY"}]),
        kind="documented", fragment="must be MD, M or D",
    ),
    dict(
        name="type_4_unimplemented", payload=_dict_study(
            qrp_parameters_scalars={
                "runid": "bnd", "type": 4,
                "startdate": "2016-01-01", "enddate": "2016-12-31"}),
        kind="documented", fragment="covers Type 2 only",
    ),
    dict(
        name="start_after_end", payload=_dict_study(
            qrp_parameters_scalars={
                "runid": "bnd", "type": 2,
                "startdate": "2016-12-31", "enddate": "2016-01-01"}),
        kind="documented", fragment="start_date is after end_date",
    ),
    dict(
        # DOCUMENTED rejection (fixed by inv/0f462p/fix-a1): duplicate
        # cohortfile rows for one cohortgrp used to fold into a dict
        # (LAST row silently winning) and never reached the validate
        # guard. The loader now raises the documented error at the fold.
        name="duplicate_cohortgrp_rejected_at_load", payload=_dict_study(
            cohortfile=[
                {"cohortgrp": "g1", "enrdays": 30},
                {"cohortgrp": "g1", "enrdays": 200},
            ]),
        kind="documented", fragment="duplicate cohortgrp",
    ),
    dict(
        name="no_cohorts", payload=_dict_study(cohortfile=[]),
        kind="documented", fragment="study defines no cohorts",
    ),
    dict(
        name="same_enrollmentnum_contradictory_params", payload=_dict_study(
            cohortfile=[
                {"cohortgrp": "g1", "enrollmentnum": 1, "enrolgap": 0},
                {"cohortgrp": "g2", "enrollmentnum": 1, "enrolgap": 30},
            ],
            cohortcodes=[
                {"group": "g1", "indexcriteria": "DEF",
                 "code": "00700", "codecat": "RX"},
                {"group": "g2", "indexcriteria": "DEF",
                 "code": "00701", "codecat": "RX"},
            ]),
        kind="documented", fragment="share enrollmentnum=1",
    ),
    dict(
        name="point_forbids_episode_gap", payload=_dict_study(
            type2file=_t2(point="Y", episodegap=5)),
        kind="documented", fragment="point=Y forbids",
    ),
    dict(
        name="prior_dose_inside_washout", payload=_dict_study(
            # wash_per None here (no t2washper), so the rule must NOT
            # fire; this case arms it with an explicit washout.
            type2file=_t2(mincumdose=10, t2cumdoseper=30, t2washper=100)),
        kind="documented",
        fragment="excludes every index date",
    ),
    dict(
        name="codesupply_with_cfdd", payload=_dict_study(
            type2file=_t2(mincfdd=1.5),
            cohortcodes=[
                {"group": "g1", "indexcriteria": "DEF", "code": "00700",
                 "codecat": "RX", "codesupply": 30},
            ]),
        kind="documented", fragment="CODESUPPLY must be unset",
    ),
    dict(
        name="covfrom_after_covto", payload=_dict_study(
            covariatecodes=[
                {"covarnum": 1, "code": "410.1", "codecat": "DX",
                 "covfrom": -10, "covto": -90},
            ]),
        kind="documented", fragment="is after covto",
    ),
    dict(
        # CHARACTERIZED: duplicate covarnum rows cannot reach the
        # "duplicate covarnum" guard either — COVARIATECODES legitimately
        # carries one row per code (covarnum repeated), so the parser
        # MERGES rows by covarnum: the first row's windows, the union of
        # the codes. The validation is unreachable from input files.
        name="duplicate_covarnum_rows_merge", payload=_dict_study(
            covariatecodes=[
                {"covarnum": 1, "code": "410.1", "codecat": "DX",
                 "covfrom": -90, "covto": -30},
                {"covarnum": 1, "code": "410.2", "codecat": "DX",
                 "covfrom": -10, "covto": -5},
            ]),
        kind="accepts",
        expect={
            "covariates": "covar1:from=-90,to=-30,codes=2",   # first row's window
        },
    ),
    dict(
        name="combo_unknown_reference", payload=_dict_study(
            covariatecodes=[
                {"covarnum": 1, "code": "3 or 99", "codecat": "CC"},
            ]),
        kind="documented", fragment="references unknown covariate",
    ),
    dict(
        name="combo_unparseable", payload=_dict_study(
            covariatecodes=[
                {"covarnum": 1, "code": "3 xor 4", "codecat": "CC"},
                {"covarnum": 2, "code": "410.1", "codecat": "DX"},
            ]),
        kind="documented", fragment="cannot parse at",
    ),
    dict(
        name="query_period_unstated", payload={
            "qrp_parameters_scalars": {"runid": "bnd", "type": 2},
            "cohortfile": [{"cohortgrp": "g1"}],
            "cohortcodes": [
                {"group": "g1", "indexcriteria": "DEF",
                 "code": "00700", "codecat": "RX"}],
        },
        kind="documented", fragment="does not state a query period",
    ),
    dict(
        name="labresult_hyphen_range", payload=_dict_study(
            labcodes=[
                {"group": "g1", "code": "CREAT", "codetype": "01N",
                 "labresult": "5-10"},   # hyphen is SAS's minus, not a range
            ]),
        kind="documented", fragment="labresult '5-10'",
    ),
    dict(
        name="labresult_inverted_range", payload=_dict_study(
            labcodes=[
                {"group": "g1", "code": "CREAT", "codetype": "01N",
                 "labresult": "10:5"},
            ]),
        kind="documented", fragment="inverted range",
    ),
    dict(
        name="stockpiling_bad_range", payload=_dict_study(
            stockpilingfile=[
                {"group": "g1", "stockgroup": "_default",
                 "suprange": "banana"},
            ]),
        kind="documented", fragment="cannot read 'BANANA'",
    ),
    dict(
        name="stockpiling_unknown_stockgroup", payload=_dict_study(
            stockpilingfile=[
                {"group": "g1", "stockgroup": "nosuch"},
            ]),
        kind="documented", fragment="is not used by cohort",
    ),
    # ---- documented ACCEPT boundaries -------------------------------
    dict(
        name="labresult_numeric_zero_is_real", payload=_dict_study(
            labcodes=[
                {"group": "g1", "code": "CREAT", "codetype": "01N",
                 "labresult": 0},   # 0 is a criterion, not an absent one
            ]),
        kind="accepts",
        expect={"lab_codes": ((
            "g1", "CREAT", "LRO", "01", "N",
            "", "", "", "", "", "", "",
            "=", 0.0, None,
        ),)},
    ),
    dict(
        name="minrxdays_on_dx_warns_not_rejects", payload=_dict_study(
            inclusioncodes=[
                {"group": "g1", "indexcriteria": "INC", "condlevel": "1",
                 "code": "410.1", "codecat": "DX", "minrxdays": 5},
            ]),
        kind="accepts",
        warn_fragment="resets it to 1",
        expect={},
    ),
    dict(
        name="zero_enrdays_is_zero_not_default", payload=_dict_study(
            cohortfile=[{"cohortgrp": "g1", "enrdays": 0}]),
        kind="accepts",
        expect={"cohort:g1.enr_days": 0},   # 0 supplied stays 0
    ),
    dict(
        name="empty_string_fields_are_absent", payload=_dict_study(
            cohortfile=[{"cohortgrp": "g1", "enrdays": "", "coverage": "",
                         "sex": ""}],
            type2file=[{"group": "g1", "t2washper": ""}]),
        kind="accepts",
        expect={
            "cohort:g1.enr_days": 183,           # default returns
            "cohort:g1.coverage": "MD",
            "cohort:g1.sex": ("F", "M", "U", "A"),
            "cohort:g1.wash_per": None,          # "" behaves as absent
        },
    ),
    # ---- incidental (characterized, not contracted) ------------------
    dict(
        name="cohort_row_missing_cohortgrp", payload=_dict_study(
            cohortfile=[{"enrdays": 30}]),
        kind="incidental", exc_type=KeyError,
    ),
    dict(
        name="agestrat_unparseable", payload=_dict_study(
            cohortfile=[{"cohortgrp": "g1", "agestrat": "garbage"}]),
        kind="incidental", exc_type=ValueError,
    ),
    dict(
        name="enrdays_not_a_number", payload=_dict_study(
            cohortfile=[{"cohortgrp": "g1", "enrdays": "twelve"}]),
        kind="incidental", exc_type=ValueError,
    ),
    dict(
        name="startdate_not_a_date", payload=_dict_study(
            qrp_parameters_scalars={
                "runid": "bnd", "type": 2,
                "startdate": "not-a-date", "enddate": "2016-12-31"}),
        kind="incidental", exc_type=ValueError,
    ),
    dict(
        name="sas_date_before_gregorian_ordinal", payload=_dict_study(
            qrp_parameters_scalars={
                "runid": "bnd", "type": 2,
                "startdate": -999999999, "enddate": "2016-12-31"}),
        kind="incidental", exc_type=(ValueError, OverflowError),
    ),
    dict(
        name="type_not_a_number", payload=_dict_study(
            qrp_parameters_scalars={
                "runid": "bnd", "type": "three",
                "startdate": "2016-01-01", "enddate": "2016-12-31"}),
        kind="incidental", exc_type=ValueError,
    ),
]
