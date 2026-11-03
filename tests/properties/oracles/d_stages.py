"""Independent per-stage oracles for the optional/output stages (package D).

Each oracle re-derives ONE stage's output from that stage's documented
semantics (the SAS line references quoted in the shipped SQL's comments and
docs/OUTPUTS.md), reading the stage's INPUT relations rather than its
output. Upstream stage semantics are therefore trusted to their own
packages; everything THIS stage computes — windows, aggregations,
multiplicity, ranking, rounding, bookkeeping — is recomputed here in
Python and must match exactly (floats: documented tolerance).

Oracle independence: no shipped SQL text is executed or copied. Date
windows are evaluated by per-day/per-claim iteration in Python, ranking
by explicit sort keys, rounding by SAS's round-half-away-from-zero.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

DAY = timedelta(days=1)


def canonical(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """Rows as sorted tuples with floats rounded to 9dp, for set compare.
    The sort key is None-safe: placeholder rows mix NULLs with values."""

    def cell(v: Any) -> Any:
        return round(v, 9) if isinstance(v, float) else v

    def sortkey(t: tuple[Any, ...]) -> tuple:
        return tuple((v is None, str(type(v)), str(v)) for v in t)

    out = [tuple(cell(r[c]) for c in sorted(r, key=str.lower)) for r in rows]
    return sorted(out, key=sortkey)


def table_dicts(con: Any, table: str) -> list[dict[str, Any]]:
    cur = con.execute(f"SELECT * FROM {table}")
    names = [str(d[0]).lower() for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def round_half_away(x: float, n: int) -> float:
    """DuckDB/SAS round: half away from zero, unlike Python's banker's
    round. Used for every rounded mean the summaries report."""
    from decimal import ROUND_HALF_UP, Decimal

    q = Decimal(1).scaleb(-n)
    return float(Decimal(repr(x)).quantize(q, rounding=ROUND_HALF_UP))


def _win(indexdt: date, lo: int, hi: int) -> tuple[date, date]:
    return indexdt + timedelta(days=lo), indexdt + timedelta(days=hi)


# ---------------------------------------------------------------------
# 74_utilization.sql
# ---------------------------------------------------------------------

_ENC_COLS = ("AV", "OA", "IP", "IS", "ED")


def expected_utilization(
    master: list[dict[str, Any]],
    encounters: list[dict[str, Any]],
    dispensings: list[dict[str, Any]],
    util_cfg: list[dict[str, Any]],
    baseline_cohorts: set[str],
    drugclass: list[dict[str, Any]],
    diagnoses: list[dict[str, Any]] | tuple[()] = (),
) -> list[dict[str, Any]]:
    """One row per master-list episode; counts of DISTINCT
    (adate, enctype) encounters in the window and of dispensings joined to
    the drug-class file; zeros when absent (LEFT join contract).

    When the encounter input is EMPTY, visits fall back to DISTINCT
    (patid, adate, enctype) diagnosis claims — 74_utilization.sql's
    documented fallback for a study/site with no encounter data (an
    empty encounter table fires it, same as an absent one)."""
    if encounters:
        enc = {
            (e["patid"], e["adate"], str(e["enctype"]).upper().strip())
            for e in encounters
            if e["adate"] is not None
        }
    else:
        enc = {
            (d["patid"], d["adate"], str(d["enctype"]).upper().strip())
            for d in diagnoses
            if d["adate"] is not None
        }
    cls = {(d["code"], d["generic"], d["classname"]) for d in drugclass}
    rows = []
    for m in master:
        grp = m["cohortgrp"]
        cfg = [u for u in util_cfg if u["cohortgrp"] == grp]
        med_cfg = [u for u in cfg if u["utiltype"] == "MED"]
        drug_cfg = [u for u in cfg if u["utiltype"] == "DRUG"]
        enc_counts: dict[str, int] = defaultdict(int)
        enc_days: set[date] = set()
        for a, b in (_win(m["indexdt"], u["utilfrom"], u["utilto"]) for u in med_cfg):
            for pid, adate, enctype in enc:
                if pid == m["patid"] and a <= adate <= b:
                    enc_counts[enctype] += 1
                    enc_days.add(adate)
        # SAS (ms_computeutilization.sas:396-421): dispensings in the
        # window INNER-joined to the drug-class file on NDC; numrx counts
        # the joined rows, the distinct counts are breadth measures.
        numrx = 0
        seen_g: set[str] = set()
        seen_c: set[str] = set()
        for a, b in (_win(m["indexdt"], u["utilfrom"], u["utilto"]) for u in drug_cfg):
            for d in dispensings:
                # cdm_dispensing is the NORMALIZED view (10_normalize):
                # adate/code, not the raw rxdate/rx — the raw keys were
                # latent dead code until real cohortgrps made this
                # branch reachable (SCDM audit vacuity fix).
                if d["patid"] == m["patid"] and a <= d["adate"] <= b:
                    hits = {(g, cl) for c, g, cl in cls if c == str(d["code"])}
                    if hits:
                        numrx += 1
                        seen_g |= {g for g, _ in hits}
                        seen_c |= {cl for _, cl in hits}
        rows.append(
            {
                "cohortgrp": grp,
                "patid": m["patid"],
                "indexdt": m["indexdt"],
                "enc_av": enc_counts["AV"],
                "enc_oa": enc_counts["OA"],
                "enc_ip": enc_counts["IP"],
                "enc_is": enc_counts["IS"],
                "enc_ed": enc_counts["ED"],
                "enc_total": sum(enc_counts.values()),
                "enc_days": len(enc_days),
                "numrx": numrx,
                "numgeneric": len(seen_g),
                "numclass": len(seen_c),
                "util_computed": grp in baseline_cohorts,
            }
        )
    return rows


def expected_utilization_summary(util: list[dict[str, Any]]) -> list[dict[str, Any]]:
    per: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in util:
        per[r["cohortgrp"]].append(r)
    rows = []
    for grp, rs in per.items():
        rows.append(
            {
                "cohortgrp": grp,
                "episodes": len(rs),
                "mean_encounters": round_half_away(
                    sum(r["enc_total"] for r in rs) / len(rs), 2
                ),
                "mean_inpatient": round_half_away(
                    sum(r["enc_ip"] for r in rs) / len(rs), 3
                ),
                "mean_ed": round_half_away(sum(r["enc_ed"] for r in rs) / len(rs), 3),
                "mean_dispensings": round_half_away(
                    sum(r["numrx"] for r in rs) / len(rs), 2
                ),
                "mean_generics": round_half_away(
                    sum(r["numgeneric"] for r in rs) / len(rs), 2
                ),
                "mean_classes": round_half_away(
                    sum(r["numclass"] for r in rs) / len(rs), 2
                ),
            }
        )
    return rows


# ---------------------------------------------------------------------
# 78_mfu.sql
# ---------------------------------------------------------------------


def expected_mfu(
    master: list[dict[str, Any]],
    diagnoses: list[dict[str, Any]],
    dispensings: list[dict[str, Any]],
    mfu_cfg: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rank codes by the configured measure; ties broken by codecount then
    code; top topxx kept. codecount counts CLAIM ROWS (a code appearing
    twice for one patient counts twice), patcount distinct patients."""
    claims: list[tuple[str, int, str, str, str, int]] = []
    for m in master:
        for a in mfu_cfg:
            if a["cohortgrp"] != m["cohortgrp"]:
                continue
            lo, hi = _win(m["indexdt"], a["mfufrom"], a["mfuto"])
            if a["codecat"] == "DX":
                for d in diagnoses:
                    if (
                        d["patid"] == m["patid"]
                        and lo <= d["adate"] <= hi
                        and (
                            not a["codetype"]
                            or str(d["codetype"]).upper() == a["codetype"].upper()
                        )
                    ):
                        claims.append(
                            (
                                m["cohortgrp"],
                                a["analysisnum"],
                                str(d["code"]),
                                "DX",
                                str(d["codetype"]),
                                m["patid"],
                            )
                        )
            else:
                for d in dispensings:
                    if d["patid"] == m["patid"] and lo <= d["adate"] <= hi:
                        claims.append(
                            (
                                m["cohortgrp"],
                                a["analysisnum"],
                                str(d["code"]),
                                "RX",
                                "ND",
                                m["patid"],
                            )
                        )
    counted: dict[tuple[str, int, str, str, str], tuple[int, set[int]]] = {}
    for grp, an, code, cc, ct, patid in claims:
        k = (grp, an, code, cc, ct)
        n, pats = counted.get(k, (0, set()))
        counted[k] = (n + 1, pats | {patid})
    params = {
        (a["cohortgrp"], a["analysisnum"]): (a["countmethod"].upper(), a["topxx"])
        for a in mfu_cfg
    }
    by_analysis: dict[
        tuple[str, int], list[tuple[tuple[int, int, str], str, str, str, int, int]]
    ] = defaultdict(list)
    for (grp, an, code, cc, ct), (n, pats) in counted.items():
        meth, _top = params[(grp, an)]
        metric = len(pats) if meth == "PATCOUNT" else n
        by_analysis[(grp, an)].append(((-metric, -n, code), code, cc, ct, n, len(pats)))
    rows = []
    for (grp, an), entries in by_analysis.items():
        entries.sort(key=lambda e: e[0])
        top = params[(grp, an)][1]
        for rank, (_, code, cc, ct, n, p) in enumerate(entries[:top], 1):
            rows.append(
                {
                    "cohortgrp": grp,
                    "analysisnum": an,
                    "code": code,
                    "codecat": cc,
                    "codetype": ct,
                    "codecount": n,
                    "patcount": p,
                    "rank": rank,
                }
            )
    return rows


# ---------------------------------------------------------------------
# 76_labs.sql (path '02' LOINC only, as fixtures configure)
# ---------------------------------------------------------------------


def expected_labs(
    master: list[dict[str, Any]],
    labs: list[dict[str, Any]],
    lab_cfg: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out = []
    seen: set[tuple[Any, ...]] = set()
    for m in master:
        for k in lab_cfg:
            if k["cohortgrp"] != m["cohortgrp"]:
                continue
            for lr in labs:
                if lr["patid"] != m["patid"]:
                    continue
                if k["path"] == "02":
                    if str(lr["loinc"]) != str(k["code"]):
                        continue
                elif k["path"] not in ("01", "02"):
                    if str(lr["px"]) != str(k["code"]):
                        continue
                else:
                    if (
                        str(lr["ms_test_name"]).upper().strip()
                        != str(k["ms_test_name"]).upper().strip()
                        or str(lr["ms_test_sub_category"]).upper().strip()
                        != str(k["ms_test_sub_category"]).upper().strip()
                        or str(lr["specimen_source"]).upper().strip()
                        != str(k["specimen_source"]).upper().strip()
                        or str(lr["ms_result_unit"]).upper().strip()
                        != str(k["ms_result_unit"]).upper().strip()
                        or str(lr["result_type"]).upper().strip()
                        != str(k["map_result_type"]).upper().strip()
                        or str(lr["fast_ind"]).upper().strip()
                        != str(k["fast_ind"]).upper().strip()
                        or str(lr["pt_loc"]).upper().strip()
                        != str(k["pt_loc"]).upper().strip()
                    ):
                        continue
                adate = _lab_date(lr, str(k["labdatetype"]))
                if adate is None:
                    continue
                if not _lab_pass(lr, k):
                    continue
                if not (
                    m["indexdt"] - timedelta(days=365) <= adate <= m["indexdt"] - DAY
                ):
                    continue
                key = (
                    m["cohortgrp"],
                    m["patid"],
                    m["indexdt"],
                    str(k["code"]),
                    adate,
                    lr["result_num"],
                )
                if key not in seen:
                    seen.add(key)
                    out.append(
                        {
                            "cohortgrp": m["cohortgrp"],
                            "patid": m["patid"],
                            "indexdt": m["indexdt"],
                            "labcode": str(k["code"]),
                            "adate": adate,
                            "result_num": lr["result_num"],
                        }
                    )
    return out


def _lab_date(lr: dict[str, Any], priority: str) -> date | None:
    for ch in priority[:3]:
        v = {"L": lr["lab_dt"], "R": lr["result_dt"], "O": lr["order_dt"]}.get(ch)
        if v is not None:
            return v
    return None


def _lab_pass(lr: dict[str, Any], k: dict[str, Any]) -> bool:
    op = k["op"]
    if op is None:
        return True
    if str(k["resulttyp"]).upper() != "N" or str(lr["result_type"]).upper() != "N":
        return True
    v = lr["result_num"]
    if op == "<=":
        return v is not None and v <= k["bound_lo"]
    if op == "<":
        return v is not None and v < k["bound_lo"]
    if op == ">=":
        return v is not None and v >= k["bound_lo"]
    if op == ">":
        return v is not None and v > k["bound_lo"]
    if op == "~=":
        return v is not None and v != k["bound_lo"]
    if op == ":":
        return v is not None and k["bound_lo"] <= v <= k["bound_hi"]
    if op == "=":
        return v is not None and v == k["bound_lo"]
    return True


def expected_lab_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    per: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        per[(r["cohortgrp"], r["labcode"])].append(r)
    out = []
    for (grp, code), rs in sorted(per.items()):
        nums = [r["result_num"] for r in rs if r["result_num"] is not None]
        # Exact-decimal mean: the inputs are decimal-rounded results and
        # the documented rounding (SAS half-away) is decimal semantics.
        # Float summation is ORDER-sensitive and on an exact .0005
        # boundary moves the rounded digit (observed: identical four
        # rows rounded 134.082 vs 134.083 purely by summation order).
        from decimal import Decimal

        if nums:
            total = sum(Decimal(str(v)) for v in nums)
            mean: float | None = float(total / len(nums))
            lo: float | None = min(nums)
            hi: float | None = max(nums)
        else:
            # All-character results (Result_Type 'C' rows matching via a
            # result-type-agnostic config): avg/min/max over an empty
            # numeric set are NULL in the engine (76_labs.sql), not zero.
            mean = lo = hi = None
        out.append(
            {
                "cohortgrp": grp,
                "labcode": code,
                "records": len(rs),
                "npts": len({r["patid"] for r in rs}),
                "mean_result": None if mean is None else round_half_away(mean, 3),
                "min_result": lo,
                "max_result": hi,
            }
        )
    return out


# ---------------------------------------------------------------------
# 85_riskscores.sql — documented mapping + arithmetic ONLY; the CLINICAL
# validity of any weight set is external to this repository and is NOT
# asserted anywhere here.
# ---------------------------------------------------------------------


def expected_risk_scores(
    master: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    risk_codes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scores = sorted({r["riskscore"] for r in risk_codes})
    intercept = {
        s: sum(
            r["weight"] for r in risk_codes if r["riskscore"] == s and r["is_intercept"]
        )
        for s in scores
    }
    conds: dict[tuple[str, int, date, str, str], float] = {}
    for m in master:
        for r in risk_codes:
            if r["is_intercept"]:
                continue
            if r["codecat"] in ("DX", "PX", "RX"):
                ep_end = m.get("episodeenddt")
                idx = m["indexdt"]
                anchor_from = (
                    (ep_end if ep_end is not None else idx)
                    if r["riskfromanchor"] == "EPISODEENDDT"
                    else idx
                )
                anchor_to = (
                    (ep_end if ep_end is not None else idx)
                    if r["risktoanchor"] == "EPISODEENDDT"
                    else idx
                )
                w_from = anchor_from + timedelta(days=r["riskfrom"])
                w_to = anchor_to + timedelta(days=r["riskto"])
                best = None
                for s in claims:
                    if (
                        s["patid"] != m["patid"]
                        or s["code"] != r["code"]
                        or s["codecat"] != r["codecat"]
                    ):
                        continue
                    if r["enctype"] != "**" and r["enctype"] != s["enctype"]:
                        continue
                    if r["pdx"] != "*" and r["pdx"] != s["pdx"]:
                        continue
                    end = s["expiredt"] if s["codecat"] == "RX" else s["adate"]
                    if not (end >= w_from and s["adate"] <= w_to):
                        continue
                    best = max(best, r["weight"]) if best is not None else r["weight"]
                if best is not None:
                    k = (
                        m["cohortgrp"],
                        m["patid"],
                        m["indexdt"],
                        r["riskscore"],
                        r["condid"],
                    )
                    conds[k] = max(conds.get(k, best), best)
            elif r["codecat"] == "DM":
                if (
                    str(r["code"]).upper() == str(m["sex"]).upper()
                    or str(r["code"]).upper() == str(m["agegroup"]).upper()
                ):
                    k = (
                        m["cohortgrp"],
                        m["patid"],
                        m["indexdt"],
                        r["riskscore"],
                        r["condid"],
                    )
                    conds[k] = max(conds.get(k, r["weight"]), r["weight"])
    totals: dict[tuple[str, int, date, str], float] = defaultdict(float)
    for (grp, pid, idx, sc, _cond), w in conds.items():
        totals[(grp, pid, idx, sc)] += w
    rows = []
    for m in master:
        for s in scores:
            rows.append(
                {
                    "cohortgrp": m["cohortgrp"],
                    "patid": m["patid"],
                    "indexdt": m["indexdt"],
                    "riskscore": s,
                    "score": totals.get(
                        (m["cohortgrp"], m["patid"], m["indexdt"], s), 0.0
                    )
                    + intercept[s],
                }
            )
    return rows


def expected_risk_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    per: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        per[(r["cohortgrp"], r["riskscore"])].append(r["score"])
    out = []
    for (grp, sc), vals in sorted(per.items()):
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        med = (
            vals_sorted[n // 2]
            if n % 2
            else (vals_sorted[n // 2 - 1] + vals_sorted[n // 2]) / 2
        )
        out.append(
            {
                "cohortgrp": grp,
                "riskscore": sc,
                "episodes": n,
                "mean_score": round_half_away(sum(vals) / n, 3),
                "min_score": min(vals),
                "median_score": round_half_away(med, 3),
                "max_score": max(vals),
            }
        )
    return out


# ---------------------------------------------------------------------
# 70_outputs.sql — denominators (exposed person-time), censoring
# ---------------------------------------------------------------------

_DEN_GSETS = (
    ("agegroup", "sex", "year"),
    ("agegroup", "sex"),
    ("agegroup",),
    ("sex",),
    ("year",),
    (),
)


def expected_denominators(cohort_final: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """GROUPING SETS margins with 'ALL' coalescing, per cohort."""
    rows = []
    for grp in sorted({r["cohortgrp"] for r in cohort_final}):
        rs = [r for r in cohort_final if r["cohortgrp"] == grp]
        for gset in _DEN_GSETS:
            buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
            for r in rs:
                key = tuple(
                    ("agegroup", r["agegroup"])
                    if c == "agegroup"
                    else ("sex", r["sex"])
                    if c == "sex"
                    else ("year", str(r["indexdt"].year))
                    for c in gset
                )
                buckets[key].append(r)
            for key, brs in buckets.items():
                d = dict(key)
                pd = sum(r["person_days"] for r in brs)
                ev = sum(r["has_event"] for r in brs)
                rows.append(
                    {
                        "cohortgrp": grp,
                        "agegroup": d.get("agegroup", "ALL"),
                        "sex": d.get("sex", "ALL"),
                        "index_year": d.get("year", "ALL"),
                        "episodes": len(brs),
                        "patients": len({r["patid"] for r in brs}),
                        "person_days": pd,
                        "events": ev,
                        "rate_per_1000_py": round_half_away(
                            1000.0 * 365.25 * ev / pd, 2
                        )
                        if pd
                        else None,
                    }
                )
    return rows


def expected_censoring(
    cohort_final: list[dict[str, Any]],
    flags: list[dict[str, Any]],
    levels: list[dict[str, Any]],
    end_date: date,
    censor_date: date,
) -> list[dict[str, Any]]:
    """The cens_* table: flags against min(enr_end, death, query end,
    DP end), timetocensor as the category string. GROUP BY ALL runs per
    level over the LEVEL-NULLED stratum values, so rows the level does
    not stratify on collapse together."""
    fl = {(f["cohortgrp"], f["patid"], f["indexdt"]): f for f in flags}
    out = []
    for lv in levels:
        if lv["tableid"] != "t2cida":
            continue
        grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for r in cohort_final:
            f = fl[(r["cohortgrp"], r["patid"], r["indexdt"])]
            ttocensor = (
                min(
                    r["enr_end"],
                    r["deathdt"] if r["deathdt"] is not None else date(9999, 12, 31),
                    end_date,
                    censor_date,
                )
                - r["indexdt"]
            ).days + 1
            key = (
                r["cohortgrp"],
                r["agegroup"] if lv["has_agegroup"] else None,
                r["sex"] if lv["has_sex"] else None,
                r["race"] if lv["has_race"] else None,
                r["hispanic"] if lv["has_hispanic"] else None,
                str(r["indexdt"].year) if lv["has_year"] else None,
                str(ttocensor),
            )
            grouped[key].append(f)
        for key, rs in grouped.items():
            out.append(
                {
                    "group": key[0],
                    "level": lv["level_id"],
                    "censdays_value_cat": key[6],
                    "agegroup": key[1],
                    "sex": key[2],
                    "race": key[3],
                    "hispanic": key[4],
                    "year": key[5],
                    "episodes": len(rs),
                    "cens_elig": sum(r["cens_elig"] for r in rs),
                    "cens_dth": sum(r["cens_dth"] for r in rs),
                    "cens_qryend": sum(r["cens_qryend"] for r in rs),
                    "cens_dpend": sum(r["cens_dpend"] for r in rs),
                }
            )
    return out


# ---------------------------------------------------------------------
# 47_geography.sql
# ---------------------------------------------------------------------


def expected_geography(
    master: list[dict[str, Any]], zipfile: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Unknown cascades off missing zip OR unmatched state; cb_reg also
    off 'other'; zip_uncertain 'Y' unless zip_date <= indexdt."""
    zl = {z["zip"]: z for z in zipfile}
    rows = []
    for m in master:
        z = zl.get(m["zip"])
        missing = m["zip"] is None or str(m["zip"]).strip() == ""
        if not missing and z is not None and z["statecode"] is None:
            z = None  # unmatched state: the Unknown rules cascade
        if missing or z is None:
            zip3 = state = hhs = cb = "Unknown"
            sdi = None
            sdi_cat = "5"
        else:
            zip3 = str(m["zip"]).strip()[:3]
            state = z["statecode"]
            hhs = "Unknown" if z["hhs_region"] is None else z["hhs_region"]
            cb = (
                "Unknown"
                if z["cb_region"] is None or str(z["cb_region"]).lower() == "other"
                else z["cb_region"]
            )
            sdi = z["sdi"]
            sdi_cat = (
                "1"
                if 0 <= z["sdi"] < 25
                else "2"
                if 25 <= z["sdi"] < 50
                else "3"
                if 50 <= z["sdi"] < 75
                else "4"
                if 75 <= z["sdi"] <= 100
                else "5"
            )
        rows.append(
            {
                "cohortgrp": m["cohortgrp"],
                "patid": m["patid"],
                "indexdt": m["indexdt"],
                "zip": m["zip"],
                "zip3": zip3,
                "state": state,
                "hhs_reg": hhs,
                "cb_reg": cb,
                "sdi": sdi,
                "sdi_cat": sdi_cat,
                "zip_uncertain": (
                    "Y"
                    if m["zip_date"] is None or m["indexdt"] < m["zip_date"]
                    else "N"
                ),
            }
        )
    return rows


# ---------------------------------------------------------------------
# 80_covariates.sql — DX/PX windows and the RX stockpile chain
# ---------------------------------------------------------------------


def expected_covariates(
    master: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    cov_cfg: list[dict[str, Any]],
    codes_by_covarnum: dict[int, set[tuple[str, str]]],
    spans_by_cfg: dict[str, list[tuple[int, date, date]]],
    start_date: date,
) -> list[dict[str, Any]]:
    """One row per (cohort, episode, covarnum) with ANY qualifying claim
    overlapping the anchor window. RX claims go through the documented
    chain: clip to per-span pieces, same-day sum, chain, clip the
    chained coverage to enrollment again, overlap by day (dateonly
    covariates use start dates only)."""
    # cfg_covariates carries one row per (cohortgrp, covarnum); keying
    # by covarnum alone kept only the last cohort's row and silently
    # skipped every other cohort's episodes (masked while the starved
    # fixtures compared empty against empty).
    defs = {(c["cohortgrp"], c["covarnum"]): c for c in cov_cfg}
    return _covariate_rows(
        master, claims, defs, codes_by_covarnum, spans_by_cfg, start_date
    )


def _covariate_rows(master, claims, defs, codes, spans_by_cfg, start_date):
    out: set[tuple[Any, ...]] = set()
    rx_claims = [c for c in claims if c["codecat"] == "RX"]
    other_claims = [c for c in claims if c["codecat"] != "RX"]
    # build code sets from the cfg rows the caller collected
    for m in master:
        for (_grp, covnum), cov in defs.items():
            if _grp != m["cohortgrp"]:
                continue
            dateonly = bool(cov.get("dateonly"))
            anchor_f = (
                m.get("episodeenddt")
                if cov["covfromanchor"] == "EPISODEENDDT"
                else m["indexdt"]
            )
            anchor_t = (
                m.get("episodeenddt")
                if cov["covtoanchor"] == "EPISODEENDDT"
                else m["indexdt"]
            )
            ws = anchor_f + timedelta(days=cov["covfrom"])
            we = anchor_t + timedelta(days=cov["covto"])
            wanted = codes.get(covnum, set())
            if not wanted:
                continue
            hit = False
            for s in other_claims:
                if (str(s["code"]), s["codecat"]) not in wanted:
                    continue
                if s["patid"] != m["patid"]:
                    continue
                cend = s["adate"] if dateonly else s["expiredt"]
                if cend >= ws and s["adate"] <= we:
                    hit = True
                    break
            if not hit and rx_claims and wanted:
                hit = _rx_chain_hit(
                    m["patid"],
                    ws,
                    we,
                    dateonly,
                    wanted,
                    rx_claims,
                    spans_by_cfg.get(cov["enr_cfg_id"], []),
                    start_date,
                    cov["enr_days"],
                )
            if hit:
                out.add(
                    (m["cohortgrp"], m["patid"], m["indexdt"], covnum, cov["covarname"])
                )
    return [
        {"cohortgrp": a, "patid": b, "indexdt": c, "covarnum": d, "covarname": e}
        for a, b, c, d, e in sorted(out)
    ]


def _rx_chain_hit(
    patid, ws, we, dateonly, wanted, rx_claims, spans, start_date, enr_days
):
    # Documented chain (80_covariates.sql `clipped`/`reclipped` CTEs;
    # ms_cidacov_codeextraction.sas:590-672 as cited there): each
    # dispensing is CLIPPED to every enrollment span it overlaps (one
    # piece per span, supply cut at the span edges). The clipped CTE is
    # SELECT DISTINCT, so a duplicate dispensing with the same shape
    # (same day, supply, code — the same_supply_ties knob) contributes
    # ONCE; genuinely different same-day fills then sum in `sameday`.
    # Pieces chain back-to-back and the chained coverage is clipped TO
    # ENROLLMENT AGAIN — a continuation falling outside every span (the
    # gap, or past the last span end) is dropped. Codes within one
    # covarnum share the default per-covarnum stockgroup here, so one
    # chain per (patid, covarnum) matches production's per-stockgroup
    # partition.
    pieces: set[tuple[date, int, str]] = set()
    for s in rx_claims:
        if s["patid"] != patid or (str(s["code"]), "RX") not in wanted:
            continue
        if s["expiredt"] < start_date - timedelta(days=enr_days):
            continue
        for pid, sp_start, sp_end in spans:
            if pid == patid and s["adate"] <= sp_end and s["expiredt"] >= sp_start:
                lo = max(s["adate"], sp_start)
                hi = min(s["expiredt"], sp_end)
                pieces.add((lo, (hi - lo).days + 1, str(s["code"])))
    day_sup: dict[date, int] = defaultdict(int)
    for lo, sup, _code in pieces:
        day_sup[lo] += sup
    # chain: each piece's supply continues from where coverage already
    # runs — max(prior end, piece start), then add the supply.
    chained: list[tuple[date, date]] = []
    end: date | None = None
    for adate in sorted(day_sup):
        sup = day_sup[adate]
        start = adate if end is None else max(end + DAY, adate)
        end = start + timedelta(days=sup - 1)
        chained.append((start, end))
    # second shave: clip the chained coverage to enrollment again
    for cs, ce in chained:
        for pid, sp_start, sp_end in spans:
            if pid != patid or cs > sp_end or ce < sp_start:
                continue
            a = max(cs, sp_start)
            b = min(ce, sp_end)
            cend = a if dateonly else b
            if cend >= ws and a <= we:
                return True
    return False


# ---------------------------------------------------------------------
# 62_mstr_wide.sql — the columns the widen adds (checked per episode)
# ---------------------------------------------------------------------


def expected_mstr_added(
    pre_final: list[dict[str, Any]],
    flags: list[dict[str, Any]],
    distindex_ep: list[dict[str, Any]],
    end_date: date,
) -> list[dict[str, Any]]:
    fl = {(f["cohortgrp"], f["patid"], f["indexdt"]): f for f in flags}
    di = {(d["cohortgrp"], d["patid"], d["indexdt"]): d for d in distindex_ep}
    rows = []
    for r in pre_final:
        f = fl[(r["cohortgrp"], r["patid"], r["indexdt"])]
        d = di[(r["cohortgrp"], r["patid"], r["indexdt"])]
        fu = r["followuptime"]
        cat = "0-90" if fu <= 90 else "91-180" if fu <= 180 else "181+"
        sort = 1.0 if fu <= 90 else 2.0 if fu <= 180 else 3.0
        rows.append(
            {
                "cohortgrp": r["cohortgrp"],
                "patid": r["patid"],
                "indexdt": r["indexdt"],
                "year": r["indexdt"].year,
                "month": r["indexdt"].month,
                "quarter": (r["indexdt"].month - 1) // 3 + 1,
                "PeriodID": 1,
                "IndexLookEndDt": end_date,
                "RawDisp": r["episode_rawdisp"],
                "AdjustedDisp": r["episode_adjusteddisp"],
                "TotRxSup": r["episode_totrxsup"],
                "TotRxAmt": round(r["episode_totrxamt"], 9),
                "distindexexp": d["distindexexp"] or "",
                "distindexhoi": d["distindexhoi"] or "",
                "numevents": r["numevents"] or None,
                "fupdays_value_cat": cat,
                "Censorcat_sort": sort,
                **{
                    k: f[k]
                    for k in (
                        "fup_elig",
                        "fup_dpend",
                        "fup_episend",
                        "fup_spec",
                        "fup_event",
                        "cens_elig",
                        "cens_dth",
                        "cens_qryend",
                        "cens_dpend",
                    )
                },
            }
        )
    return rows


# ---------------------------------------------------------------------
# 90_cidatables.sql — numcounts and the merged t2_cida
# ---------------------------------------------------------------------

_T2_SUMS = (
    "adjustedcodecount",
    "rawcodecount",
    "daysupp",
    "amtsupp",
    "followuptime",
    "timetocensor",
)


def expected_numcounts(
    cohort_final: list[dict[str, Any]], levels: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for lv in levels:
        if lv["tableid"] != "t2cida":
            continue
        buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for r in cohort_final:
            key = (
                r["cohortgrp"],
                r["agegroup"] if lv["has_agegroup"] else None,
                r["agegroupnum"] if lv["has_agegroup"] else None,
                r["sex"] if lv["has_sex"] else None,
                r["race"] if lv["has_race"] else None,
                r["hispanic"] if lv["has_hispanic"] else None,
                str(r["indexdt"].year) if lv["has_year"] else None,
            )
            buckets[key].append(r)
        for key, brs in buckets.items():
            rows.append(
                {
                    "level": lv["level_id"],
                    "group": key[0],
                    "agegroup": key[1],
                    "agegroupnum": key[2],
                    "sex": key[3],
                    "race": key[4],
                    "hispanic": key[5],
                    "index_year": key[6],
                    "npts": len({b["patid"] for b in brs}),
                    "episodes": len(brs),
                    "adjustedcodecount": sum(b["episode_adjusteddisp"] for b in brs),
                    "rawcodecount": sum(b["episode_rawdisp"] for b in brs),
                    "daysupp": sum(b["episode_totrxsup"] for b in brs),
                    "amtsupp": round(sum(b["episode_totrxamt"] for b in brs), 9),
                    "all_events": sum(b["numevents"] or 0 for b in brs),
                    "eps_wevents": sum(b["has_event"] for b in brs),
                    "followuptime": sum(b["followuptime"] for b in brs),
                    "timetocensor": sum(b["timetocensor"] for b in brs),
                }
            )
    return rows


def expected_t2_cida(
    num: list[dict[str, Any]], denom: list[tuple[Any, ...]]
) -> list[dict[str, Any]]:
    """FULL merge of numerator and denomcounts (expected tuples from
    d_denom), SAS metric names, zero-filling the absent side."""
    drows: dict[tuple[Any, ...], tuple[Any, ...]] = {}
    for d in denom:
        key = (d[0], d[1], d[2], d[4], d[5], d[6], d[7])
        drows[key] = d
    nrows: dict[tuple[Any, ...], dict[str, Any]] = {}
    for n in num:
        key = (
            n["level"],
            n["group"],
            n["agegroup"],
            n["sex"],
            n["race"],
            n["hispanic"],
            n["index_year"],
        )
        nrows[key] = n
    rows = []
    for key in set(nrows) | set(drows):
        nr = nrows.get(key)
        dr = drows.get(key)
        rows.append(
            {
                "level": key[0],
                "group": key[1],
                "agegroup": key[2],
                "agegroupnum": nr["agegroupnum"]
                if nr and nr["agegroupnum"] is not None
                else (dr[3] if dr is not None else None),
                "sex": key[3],
                "race": key[4],
                "hispanic": key[5],
                "year": key[6],
                # the fine time/geography strata this package does not offer
                "month": None,
                "quarter": None,
                "zip3": None,
                "state": None,
                "hhs_reg": None,
                "cb_reg": None,
                "zip_uncertain": None,
                "npts": nr["npts"] if nr else 0,
                "episodes": nr["episodes"] if nr else 0,
                "adjustedcodecount": nr["adjustedcodecount"] if nr else 0,
                "rawcodecount": nr["rawcodecount"] if nr else 0,
                "daysupp": nr["daysupp"] if nr else 0,
                "amtsupp": nr["amtsupp"] if nr else 0,
                "all_events": nr["all_events"] if nr else 0,
                "eps_wevents": nr["eps_wevents"] if nr else 0,
                "followuptime": nr["followuptime"] if nr else 0,
                "timetocensor": nr["timetocensor"] if nr else 0,
                "dennumpts": dr[15] if dr else 0,
                "dennummemdays": dr[16] if dr is not None else 0,
            }
        )
    return rows


# ---------------------------------------------------------------------
# 94_followuptime.sql
# ---------------------------------------------------------------------

_FUP_CATS = (("0-90", 1), ("91-180", 2), ("181+", 3))


def expected_followuptime(
    cohort_final: list[dict[str, Any]],
    flags: list[dict[str, Any]],
    levels: list[dict[str, Any]],
    all_cohorts: set[str],
) -> list[dict[str, Any]]:
    fl = {(f["cohortgrp"], f["patid"], f["indexdt"]): f for f in flags}
    have_levels = [lv for lv in levels if lv["tableid"] == "t2followuptime"]
    active = have_levels or [
        {
            "level_id": "1",
            "has_agegroup": False,
            "has_sex": False,
            "has_race": False,
            "has_hispanic": False,
            "has_year": False,
            "has_censdays_value": False,
            "has_censdays_cat": False,
        }
    ]
    out = []
    # The implementation GROUPs BY ALL per level: stratum values the
    # level does not use are NULLed BEFORE grouping, so a shell row
    # (all dims NULL) merges with any same-key episode row, summing
    # episodes and flags — one row per distinct key, per level.
    for lv in active:
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for r in cohort_final:
            f = fl[(r["cohortgrp"], r["patid"], r["indexdt"])]
            fu = r["followuptime"]
            cat = "0-90" if fu <= 90 else "91-180" if fu <= 180 else "181+"
            sort = 1.0 if fu <= 90 else 2.0 if fu <= 180 else 3.0
            key = (
                r["cohortgrp"],
                r["agegroup"] if lv["has_agegroup"] else None,
                r["sex"] if lv["has_sex"] else None,
                r["race"] if lv["has_race"] else None,
                r["hispanic"] if lv["has_hispanic"] else None,
                str(r["indexdt"].year) if lv["has_year"] else None,
                fu if lv["has_censdays_value"] else None,
                sort if lv["has_censdays_cat"] else None,
                cat if lv["has_censdays_cat"] else None,
            )
            groups[key].append(f)
        for grp in sorted(all_cohorts):
            for label, sort in _FUP_CATS:
                shell_key = (
                    grp,
                    None,
                    None,
                    None,
                    None,
                    None,
                    1 if lv["has_censdays_value"] else None,
                    float(sort) if lv["has_censdays_cat"] else None,
                    label if lv["has_censdays_cat"] else None,
                )
                groups.setdefault(shell_key, [])
        for key, rows_ in groups.items():
            out.append(
                {
                    "group": key[0],
                    "level": lv["level_id"],
                    "censdays_value": key[6],
                    "censorcat_sort": key[7],
                    "censdays_value_cat": key[8],
                    "agegroup": key[1],
                    "sex": key[2],
                    "race": key[3],
                    "hispanic": key[4],
                    "year": key[5],
                    "episodes": len(rows_),
                    "cens_elig": sum(r["fup_elig"] for r in rows_),
                    "cens_dpend": sum(r["fup_dpend"] for r in rows_),
                    "cens_episend": sum(r["fup_episend"] for r in rows_),
                    "cens_spec": sum(r["fup_spec"] for r in rows_),
                    "cens_event": sum(r["fup_event"] for r in rows_),
                }
            )
    return out


# ---------------------------------------------------------------------
# 96_baseline.sql — the long table (the wide table is its PIVOT)
# ---------------------------------------------------------------------


def expected_baseline_long(
    cohort_final: list[dict[str, Any]],
    util: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    baseline_cohorts: set[str],
    covarnums: tuple[int, ...],
    covariates: list[dict[str, Any]],
    age_labels: tuple[str, ...],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """baseline_long rows for createbaseline cohorts only. Levels are
    DECLARED, not read from the data: Sex F/M/O, Race 0-5/M, Hispanic
    Y/N/U, the cohort's AGE bands, and every query-period year; absent
    declared levels are 0. stddev rows are emitted per cohort with a
    NULL value when a single episode can't have a sample SD."""
    bl = [r for r in cohort_final if r["cohortgrp"] in baseline_cohorts]
    rows: list[tuple[str, str, str, Any]] = []
    for grp in sorted({r["cohortgrp"] for r in bl}):
        rs = [r for r in bl if r["cohortgrp"] == grp]
        rows.append((grp, "patient", "count", len({r["patid"] for r in rs})))
        rows.append((grp, "n_episodes", "count", len(rs)))
        # demographics once per patient, first episode
        first: dict[int, dict[str, Any]] = {}
        for r in sorted(rs, key=lambda r: r["indexdt"]):
            first.setdefault(r["patid"], r)
        for var, col, levels_ in (
            ("Sex", "sex", ("F", "M", "O")),
            ("Race", "race", ("0", "1", "2", "3", "4", "5", "M")),
            ("Hispanic", "hispanic", ("Y", "N", "U")),
        ):
            counts: dict[str, int] = defaultdict(int)
            for r in first.values():
                counts[r[col]] += 1
            for lev in levels_:
                rows.append((grp, f"{var}_{lev}", "count", counts.get(lev, 0)))
            for lev, n in counts.items():
                if lev not in levels_:
                    rows.append((grp, f"{var}_{lev}", "undeclared", n))
        # age bands per episode, DECLARED from the cohort's agestrat:
        # SAS names each band AGE<band> with '-' -> '_' and the
        # characters < > + space removed
        band_counts: dict[str, int] = defaultdict(int)
        for r in rs:
            band_counts[r["agegroup"]] += 1
        for label in age_labels:
            name = "AGE" + re.sub(r"[ <>+]", "", label.replace("-", "_")).upper()
            rows.append((grp, name, "count", band_counts.get(label, 0)))
        # index years: every year of the query period plus observed
        years = set(range(start_date.year, end_date.year + 1))
        years |= {r["indexdt"].year for r in rs}
        year_counts: dict[int, int] = defaultdict(int)
        for r in rs:
            year_counts[r["indexdt"].year] += 1
        for y in sorted(years):
            rows.append((grp, f"Year_{y}", "count", year_counts.get(y, 0)))
        # covariates: distinct episodes carrying each covarnum
        cov_eps: dict[int, set[tuple[int, date]]] = defaultdict(set)
        for c in covariates:
            if c["cohortgrp"] == grp:
                cov_eps[int(c["covarnum"])].add((c["patid"], c["indexdt"]))
        for n in sorted(covarnums):
            rows.append((grp, f"covar{n}", "count", len(cov_eps.get(n, ()))))
        # means / sample SDs per episode
        u_by = {(u["patid"], u["indexdt"]): u for u in util if u["cohortgrp"] == grp}
        vals: dict[str, list[float]] = defaultdict(list)
        for r in rs:
            u = u_by.get((r["patid"], r["indexdt"]), {})
            vals["age"].append(r["age"])
            for name, col in (
                ("NumAV", "enc_av"),
                ("NumOA", "enc_oa"),
                ("NumIP", "enc_ip"),
                ("NumIS", "enc_is"),
                ("NumED", "enc_ed"),
                ("NumGeneric", "numgeneric"),
                ("NumClass", "numclass"),
                ("NumRx", "numrx"),
            ):
                vals[name].append(u.get(col, 0))
        for name, xs in vals.items():
            rows.append((grp, f"mean_{name}", "stat", round(sum(xs) / len(xs), 9)))
            if len(xs) > 1:
                mean = sum(xs) / len(xs)
                ssd = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
                rows.append((grp, f"std_{name}", "stat", round(ssd**0.5, 9)))
            else:
                rows.append((grp, f"std_{name}", "stat", None))
        r_by = {(r["patid"], r["indexdt"]): r for r in risks if r["cohortgrp"] == grp}
        by_score: dict[str, list[float]] = defaultdict(list)
        for r in rs:
            rk = r_by.get((r["patid"], r["indexdt"]))
            if rk is not None:
                by_score[rk["riskscore"]].append(rk["score"])
        for sc, xs in by_score.items():
            rows.append((grp, f"mean_{sc}", "stat", round(sum(xs) / len(xs), 9)))
            if len(xs) > 1:
                mean = sum(xs) / len(xs)
                ssd = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
                rows.append((grp, f"std_{sc}", "stat", round(ssd**0.5, 9)))
            else:
                rows.append((grp, f"std_{sc}", "stat", None))
    return [
        {
            "cohortgrp": g,
            "stat": s,
            "kind": k,
            "n": v if k in ("count", "undeclared") else None,
            "v": v if k == "stat" else None,
        }
        for g, s, k, v in rows
    ]


# ---------------------------------------------------------------------
# 70_outputs.sql — attrition funnel arithmetic and bookkeeping. Levels
# are recomputed from the stage's INPUT relations (production intermediates)
# per the SAS definitions quoted in the shipped comments; the funnel
# arithmetic, the blank/carry rows and the 18.n information rows are what
# this oracle checks.
# ---------------------------------------------------------------------

_ATTR_SKELETON = (
    # (level, kind, claim_level, descr): the SAS layout skeleton
    # (ms_attrition.sas via 70_outputs.sql). descr is a fixed layout
    # constant, not computed logic — the oracle pins the funnel
    # arithmetic and the blank/carry bookkeeping around it.
    (
        1.0,
        "init",
        "Member",
        "Initial Member Count - Members with a non-missing birth date/sex at any enrollment episode overlapping the query period",
    ),
    (
        2.0,
        "excl",
        "Member",
        "Exclusion - Members must have at least one enrollment span meeting the medical and/or drug coverage requirement during the query period",
    ),
    (
        3.0,
        "excl",
        "Member",
        "Exclusion - Members must satisfy the age range condition within the query period",
    ),
    (
        4.0,
        "excl",
        "Member",
        "Exclusion - Members must meet chart availability criterion within the query period",
    ),
    (
        5.0,
        "excl",
        "Member",
        "Exclusion - Members must satisfy the demographic (sex, race and hispanic) condition",
    ),
    (
        6.0,
        "excl",
        "Member",
        "Exclusion - Members must have at least one claim with cohort-identifying codes within the query period",
    ),
    (
        7.0,
        "init",
        "Episode",
        "Initial Episode Count (among eligible members from previous step) - All claims with cohort-identifying codes",
    ),
    (8.0, "blank", "", ""),
    (9.0, "blank", "", ""),
    (
        10.0,
        "excl",
        "Episode",
        "Exclusion - Cohort episode must have an index date within the age range condition",
    ),
    (11.0, "blank", "", ""),
    (
        12.0,
        "excl",
        "Episode",
        "Exclusion - Episode-defining index claims must be during the query period",
    ),
    (13.0, "blank", "", ""),
    (14.0, "blank", "", ""),
    (15.0, "blank", "", ""),
    (
        16.0,
        "excl",
        "Episode",
        "Exclusion - Cohort episodes must satisfy the pre-index enrollment criterion",
    ),
    (17.0, "blank", "", ""),
    (
        18.0,
        "excl",
        "Episode",
        "Exclusion - Cohort episodes must satisfy all exclusion and inclusion criteria",
    ),
    (
        19.0,
        "excl",
        "Episode",
        "Exclusion - Cohort episodes must meet HOI incidence criterion",
    ),
    (
        20.0,
        "excl",
        "Episode",
        "Exclusion - Cohort episodes must satisfy the post-index enrollment and available data criteria",
    ),
    (
        21.0,
        "excl",
        "Episode",
        "Exclusion - Cohort episodes must have at least minimum days supplied",
    ),
    (
        22.0,
        "excl",
        "Episode",
        "Exclusion - Cohort episodes must have at least minimum days duration",
    ),
    (
        23.0,
        "excl",
        "Episode",
        "Exclusion - Cohort episodes must be longer than blackout days duration",
    ),
    (
        24.0,
        "excl",
        "Episode",
        "Exclusion - Cohort episodes must meet HOI blackout criterion",
    ),
    (25.0, "blank", "", ""),
    (26.0, "blank", "", ""),
    (27.0, "final", "Member", "Information - Unique number of members in final cohort"),
)
_LEVELS = tuple(s[0] for s in _ATTR_SKELETON)


def expected_attrition(
    demographics: list[dict[str, Any]],
    enrollment_raw: list[dict[str, Any]],
    spans: list[dict[str, Any]],
    stockpiled: list[dict[str, Any]],
    cohort_cfg: list[dict[str, Any]],
    age_strata: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    incl_excl: list[dict[str, Any]],
    hoi: list[dict[str, Any]],
    cohort_final: list[dict[str, Any]],
    inclusion_cfg: list[dict[str, Any]],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    counts: dict[tuple[str, float], int] = {}
    # level 1: members with birth date and sex, enrolled overlapping period
    overlapping = {
        e["patid"]
        for e in enrollment_raw
        if e["enr_start"] <= end_date and e["enr_end"] >= start_date
    }
    dem = {
        d["patid"]
        for d in demographics
        if d["birth_date"] is not None and d["sex"] is not None
    }
    l1 = len(dem & overlapping)
    cfg_enr: dict[str, str] = {c["cohortgrp"]: c["enr_cfg_id"] for c in cohort_cfg}
    censor_death = {c["cohortgrp"]: bool(c["censor_death"]) for c in cohort_cfg}
    per_cfg: dict[str, set[int]] = defaultdict(set)
    for s in spans:
        per_cfg[s["enr_cfg_id"]].add(s["patid"])
    # levels 6/7: stockpiled records shaved to spans, distinct (member, start)
    spans_by = defaultdict(list)
    for s in spans:
        spans_by[(s["enr_cfg_id"], s["patid"])].append(s)
    gi: dict[str, set[tuple[int, date]]] = defaultdict(set)
    for st in stockpiled:
        if not (start_date <= st["adate"] <= end_date):
            continue
        for s in spans_by.get((cfg_enr[st["cohortgrp"]], st["patid"]), ()):
            end_cmp = (
                s.get("enr_end_dth", s["enr_end"])
                if censor_death[st["cohortgrp"]]
                else s["enr_end"]
            )
            if s["enr_start"] <= st["adate"] <= end_cmp:
                gi[st["cohortgrp"]].add((st["patid"], st["adate"]))
    band = {(a["cohortgrp"], a["lo"], a["hi"], a["unit"]) for a in age_strata}
    l10: dict[str, int] = defaultdict(int)
    for grp, hits in gi.items():
        for patid, adate in hits:
            d = next(d for d in demographics if d["patid"] == patid)
            if any(
                _age(d["birth_date"], adate, u) is not None
                and lo <= _age(d["birth_date"], adate, u) <= hi
                for g, lo, hi, u in band
                if g == grp
            ):
                l10[grp] += 1
    hoi_by = {(h["cohortgrp"], h["patid"], h["indexdt"]): h for h in hoi}
    exc_by = {(x["cohortgrp"], x["patid"], x["indexdt"]) for x in incl_excl}
    ep: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in episodes:
        row = dict(e)
        row["incl_met"] = (e["cohortgrp"], e["patid"], e["indexdt"]) not in exc_by
        h = hoi_by.get((e["cohortgrp"], e["patid"], e["indexdt"]), {})
        row["hoi_incidence_met"] = h.get("hoi_incidence_met")
        row["hoi_blackout_met"] = h.get("hoi_blackout_met")
        ep[e["cohortgrp"]].append(row)
    for grp in cfg_enr:
        counts[(grp, 1.0)] = l1
        n2 = len(per_cfg.get(cfg_enr[grp], set()))
        for lv in (2.0, 3.0, 4.0, 5.0):
            counts[(grp, lv)] = n2
        # counts over GROUP BY cohortgrp produce NO row when the cohort
        # has no records at all — n stays NULL (absent) in this raw
        # grid, mirroring production's `_att_counts`. The skeleton walk
        # below applies the counted-CTE contract to that absence: a
        # missing counted row is 0, only blanks carry.
        gi_rows = gi.get(grp, set())
        if gi_rows:
            # level 6 counts DISTINCT members, level 7 the records
            counts[(grp, 6.0)] = len({patid for patid, _adate in gi_rows})
            counts[(grp, 7.0)] = len(gi_rows)
            counts[(grp, 10.0)] = l10.get(grp, 0)
        ers = ep.get(grp, [])
        if ers:
            counts[(grp, 12.0)] = len(ers)
            counts[(grp, 16.0)] = sum(bool(r["min_enr_days_met"]) for r in ers)
            counts[(grp, 18.0)] = sum(
                bool(r["min_enr_days_met"]) and r["incl_met"] for r in ers
            )

            def with_flags(r: dict[str, Any]) -> bool:
                return (
                    bool(r["min_enr_days_met"])
                    and r["incl_met"]
                    and bool(r["hoi_incidence_met"])
                )

            counts[(grp, 19.0)] = sum(with_flags(r) for r in ers)
            counts[(grp, 20.0)] = sum(
                with_flags(r) and bool(r["min_enr_aft_met"]) for r in ers
            )
            counts[(grp, 21.0)] = sum(
                with_flags(r)
                and bool(r["min_enr_aft_met"])
                and bool(r["min_day_sup_met"])
                for r in ers
            )
            counts[(grp, 22.0)] = sum(
                with_flags(r)
                and bool(r["min_enr_aft_met"])
                and bool(r["min_day_sup_met"])
                and bool(r["min_epi_dur_met"])
                for r in ers
            )
            counts[(grp, 23.0)] = sum(
                with_flags(r)
                and bool(r["min_enr_aft_met"])
                and bool(r["min_day_sup_met"])
                and bool(r["min_epi_dur_met"])
                and bool(r["post_blackout_met"])
                for r in ers
            )
            counts[(grp, 24.0)] = sum(
                with_flags(r)
                and bool(r["min_enr_aft_met"])
                and bool(r["min_day_sup_met"])
                and bool(r["min_epi_dur_met"])
                and bool(r["post_blackout_met"])
                and bool(r["hoi_blackout_met"])
                for r in ers
            )
        if any(r["cohortgrp"] == grp for r in cohort_final):
            counts[(grp, 27.0)] = len(
                {r["patid"] for r in cohort_final if r["cohortgrp"] == grp}
            )
    rows = []
    for grp in sorted(cfg_enr):
        # F1 empty-step contract (ms_attrition_cidacompute.sas:77-88,
        # :112-119; 70_outputs.sql `counted` CTE): a counted step's
        # count(*) over an EMPTY relation is 0, never missing — the first
        # empty counted step takes the whole drop as its exclusion and
        # every later counted step reports 0. Only 'blank' rows carry
        # the previous remaining forward. prev_counted mirrors the
        # production lag's (init|excl) partition: blanks and the final
        # row never feed an exclusion's previous count.
        prev_counted: int | None = None
        last_n0: int | None = None
        for lv, kind, claim_level, descr in _ATTR_SKELETON:
            n0 = counts.get((grp, lv))
            if n0 is None and kind != "blank":
                n0 = 0
            remaining = n0 if n0 is not None else last_n0
            if kind == "blank":
                excluded = 0
            elif kind == "init" or kind == "final":
                excluded = None
            else:
                # an excl row always carries a count; only blanks are None
                step = 0 if n0 is None else n0
                excluded = None if prev_counted is None else prev_counted - step
            rows.append(
                {
                    "group": grp,
                    "level": lv,
                    "claim_level": claim_level,
                    "descr": descr,
                    "remaining": remaining,
                    "excluded": excluded,
                }
            )
            if kind in ("init", "excl"):
                prev_counted = n0
            if n0 is not None:
                last_n0 = n0
        # 18.n information rows, one per (criteria, cond) of the cohort
        conds = sorted(
            {
                (r["criteria"], r["cond"], r["cond_name"])
                for r in inclusion_cfg
                if r["cohortgrp"] == grp
            }
        )
        ers = ep.get(grp, [])
        for i, (crit, cond, name) in enumerate(conds, 1):
            n_exc = sum(
                1
                for x in incl_excl
                if x["cohortgrp"] == grp
                and x["failed_criteria"] == crit
                and x["first_failed_cond"] == cond
                and (x["cohortgrp"], x["patid"], x["indexdt"])
                in {
                    (e["cohortgrp"], e["patid"], e["indexdt"])
                    for e in ers
                    if e["min_enr_days_met"]
                }
            )
            rows.append(
                {
                    "group": grp,
                    "level": 18.0 + i / 10.0,
                    "claim_level": "Episode",
                    "descr": f"Information: Episodes excluded for {name}",
                    "remaining": None,
                    "excluded": n_exc,
                }
            )
    return rows


def _age(birth: date, as_of: date, unit: str) -> int | None:
    if unit == "years":
        return (
            as_of.year
            - birth.year
            - ((as_of.month, as_of.day) < (birth.month, birth.day))
        )
    if unit == "months":
        return (
            (as_of.year - birth.year) * 12
            + as_of.month
            - birth.month
            - (as_of.day < birth.day)
        )
    if unit == "days":
        return (as_of - birth).days
    if unit == "weeks":
        return (as_of - birth).days // 7
    return None


# ---------------------------------------------------------------------
# 72_codedistribution.sql — entity numbering, episode lists, counts
# ---------------------------------------------------------------------

_EXP_SETTINGS = ("IP", "IS", "ED", "AV", "OA")
_DX_PDX = ("P", "S", "X", "")


def expected_distindex(
    cohort_final: list[dict[str, Any]],
    stockpiled: list[dict[str, Any]],
    dx: list[dict[str, Any]],
    px: list[dict[str, Any]],
    dispensings: list[dict[str, Any]],
    codes_cfg: list[dict[str, Any]],
    care: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """distindexmap + distindex + distindex_episodes, from the documented
    numbering: drug stockgroups sorted first, then medical defs sorted by
    (codecat, codetype, enctype, pdx, code), each expanded across its
    settings and principal flags; ids joined '_' in CHARACTER order."""
    care_by = defaultdict(list)
    for c in care:
        care_by[(c["cohortgrp"], c["code"])].append((c["enctype"], c["pdx"]))
    ids: dict[str, dict[str, dict[Any, int]]] = defaultdict(lambda: defaultdict(dict))
    maps: list[dict[str, Any]] = []
    for grp in sorted({k["cohortgrp"] for k in codes_cfg}):
        defs = [
            k
            for k in codes_cfg
            if k["cohortgrp"] == grp and k["role"] in ("DEF", "EVENT")
        ]
        for typ, role in (("exp", "DEF"), ("hoi", "EVENT")):
            rxs = sorted(
                {
                    k["stockgroup"]
                    for k in defs
                    if k["role"] == role and k["codecat"] == "RX"
                }
            )
            for i, sg in enumerate(rxs, 1):
                ids[grp][typ][(sg,)] = i
            med = sorted(
                {
                    (
                        k["codecat"],
                        k["codetype"] or "",
                        _enc(k, grp, care_by),
                        _pdx(k, grp, care_by),
                        k["code"],
                    )
                    for k in defs
                    if k["role"] == role and k["codecat"] in ("PX", "DX")
                }
            )
            n_rx = len(rxs)
            # entity ids in expansion order: per med def (sorted by
            # codecat, codetype, enctype, pdx, code), settings then
            # principal flags (base_n, enc_i, pdx_i), continuing after
            # the drug stockgroups
            med_ids: dict[Any, int] = {}
            nxt = n_rx + 1
            for base_n, (cc, ct, et, px_, code) in enumerate(med, 1):
                ets = _EXP_SETTINGS if et == "**" else [et]
                for e2 in ets:
                    pdxs = (
                        _DX_PDX
                        if (cc == "DX" and px_ == "*")
                        else (["X", ""] if cc == "PX" else [px_])
                    )
                    for p2 in pdxs:
                        med_ids[(cc, ct, e2, p2, code)] = nxt
                        nxt += 1
            for k, v in med_ids.items():
                ids[grp][typ][k] = v
    # map rows, exactly three branches:
    #   exp entities  — every CONFIG cohort with exp ids (episodes or not)
    #   hoi entities  — cohorts WITH a first event only
    #   blank hoi     — cohorts IN cohort_final without an event
    with_event = {r["cohortgrp"] for r in cohort_final if r["feventdt"] is not None}
    for grp in sorted(set(ids)):
        for key, ident in sorted(ids[grp]["exp"].items(), key=lambda kv: str(kv[1])):
            maps.append(
                {
                    "group": grp,
                    "distindextype": "exp",
                    "stockgroup": key[0] if len(key) == 1 else None,
                    # RX entity rows carry the literal codecat 'RX'
                    "codecat": "RX" if len(key) == 1 else key[0],
                    "codetype": key[1] if len(key) == 5 else None,
                    "enctype": key[2] if len(key) == 5 else None,
                    "pdx": key[3] if len(key) == 5 else None,
                    "code": key[4] if len(key) == 5 else None,
                    "distindexid": ident,
                }
            )
    for grp in sorted(with_event):
        for key, ident in sorted(ids[grp]["hoi"].items(), key=lambda kv: str(kv[1])):
            maps.append(
                {
                    "group": grp,
                    "distindextype": "hoi",
                    "stockgroup": key[0] if len(key) == 1 else None,
                    "codecat": "RX" if len(key) == 1 else key[0],
                    "codetype": key[1] if len(key) == 5 else None,
                    "enctype": key[2] if len(key) == 5 else None,
                    "pdx": key[3] if len(key) == 5 else None,
                    "code": key[4] if len(key) == 5 else None,
                    "distindexid": ident,
                }
            )
    for grp in sorted({r["cohortgrp"] for r in cohort_final} - with_event):
        maps.append(
            {
                "group": grp,
                "distindextype": "hoi",
                "stockgroup": None,
                "codecat": None,
                "codetype": None,
                "enctype": None,
                "pdx": None,
                "code": None,
                "distindexid": None,
            }
        )

    def lookup(grp: str, typ: str, key: Any) -> int | None:
        return ids.get(grp, {}).get(typ, {}).get(key)

    def match_med(grp: str, typ: str, claim: dict[str, Any]) -> list[int]:
        out = []
        for key, ident in ids.get(grp, {}).get(typ, {}).items():
            if len(key) != 5:
                continue
            cc, ct, et, px_, code = key
            if cc != claim["codecat"] or code != claim["code"]:
                continue
            if ct != "" and ct != str(claim["codetype"] or ""):
                continue
            # Exact match against the five concrete enctypes the '**'
            # wildcard expands to: an 'XX' (null-column fallback) claim
            # matches NO entity here even though it fires as an event
            # under '**' (see d_denom's event pass). SAS parity per
            # ms_codedistribution's expansion-then-equality semantics.
            if et != claim["enctype"] or px_ != claim["pdx"]:
                continue
            out.append(ident)
        return out

    ep_rows = []
    for r in cohort_final:
        exp_ids: set[int] = set()
        for s in stockpiled:
            if (
                s["cohortgrp"] == r["cohortgrp"]
                and s["patid"] == r["patid"]
                and s["adate"] == r["indexdt"]
            ):
                hit = lookup(r["cohortgrp"], "exp", (s["stockgroup"],))
                if hit is not None:
                    exp_ids.add(hit)
        for claim in [
            c
            for c in px + dx
            if c["patid"] == r["patid"] and c["adate"] == r["indexdt"]
        ]:
            exp_ids.update(match_med(r["cohortgrp"], "exp", claim))
        hoi_ids: set[int] = set()
        if r["feventdt"] is not None:
            # cdm_dispensing is the NORMALIZED view (10_normalize):
            # columns are patid/adate/code, not the raw rxdate/rx.
            for d in dispensings:
                if d["patid"] == r["patid"] and d["adate"] == r["feventdt"]:
                    for k in codes_cfg:
                        if (
                            k["cohortgrp"] == r["cohortgrp"]
                            and k["role"] == "EVENT"
                            and k["codecat"] == "RX"
                            and k["code"] == str(d["code"])
                        ):
                            hit = lookup(r["cohortgrp"], "hoi", (k["stockgroup"],))
                            if hit is not None:
                                hoi_ids.add(hit)
            for claim in [
                c
                for c in px + dx
                if c["patid"] == r["patid"] and c["adate"] == r["feventdt"]
            ]:
                hoi_ids.update(match_med(r["cohortgrp"], "hoi", claim))
        ep_rows.append(
            {
                "cohortgrp": r["cohortgrp"],
                "patid": r["patid"],
                "indexdt": r["indexdt"],
                "distindexexp": "_".join(sorted(str(i) for i in exp_ids))
                if exp_ids
                else None,
                "distindexhoi": "_".join(sorted(str(i) for i in hoi_ids))
                if hoi_ids
                else None,
            }
        )
    lists: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in ep_rows:
        if e["distindexexp"]:
            lists[(e["cohortgrp"], "exp")][e["distindexexp"]] += 1
        if e["distindexhoi"]:
            lists[(e["cohortgrp"], "hoi")][e["distindexhoi"]] += 1
    dist_rows = []
    for (grp, typ), cnts in lists.items():
        for lst, n in cnts.items():
            dist_rows.append(
                {
                    "group": grp,
                    "distindextype": typ,
                    "distindexlist": lst,
                    "episodes": n,
                }
            )
    cohorts_ep = sorted({(r["cohortgrp"]) for r in ep_rows})
    for grp in cohorts_ep:
        for typ in ("exp", "hoi"):
            if not lists.get((grp, typ)):
                dist_rows.append(
                    {
                        "group": grp,
                        "distindextype": typ,
                        "distindexlist": "",
                        "episodes": None,
                    }
                )
    return {"distindexmap": maps, "distindex": dist_rows, "distindex_episodes": ep_rows}


def _enc(k: dict[str, Any], grp: str, care_by) -> str:
    for e, _p in care_by.get((grp, k["code"]), []):
        return e or "**"
    return "**"


def _pdx(k: dict[str, Any], grp: str, care_by) -> str:
    for _e, p in care_by.get((grp, k["code"]), []):
        return p or "*"
    return "*"
