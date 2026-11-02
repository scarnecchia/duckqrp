"""Independent sequential models of stockpiling, index dates and POV1.

Stockpiling (30_exposure.sql claims to implement ms_stockpiling.sas):
for one (cohort, stockgroup, patient), fill dates processed one claim at
a time —

    s(1) = a(1);  e(i) = s(i) + r(i) - 1;  s(i) = max(a(i), e(i-1) + 1)

(docs/SAS_PARITY.md "Stockpiling recurrence"). PERCENTDAYS replaces the
unconditional push: push only when the overlap with the previous
record's supply, as a share of that supply, is at most the fraction;
otherwise the claim keeps its own date, starts a new sequence, and the
previous sequence is truncated against the new one.

Derivation: a plain Python for-loop over dates. The production SQL solves
the same recurrence in CLOSED FORM (a cumulative supply sum and a running
anchor maximum over window functions); nothing here repeats that algebra.
Same-day collapse, SUPRANGE/AMTRANGE filtering and the two enrollment
shaves are also sequential scans over this module's own day arithmetic.

Index dates (40_index.sql): candidates are the stockpiled records that
start inside an enrollment span, deduplicated per patient-day keeping the
longest supply, and a candidate survives the washout when NO prior
claim's supply reaches into the wash_per days before it — checked with a
running scan over one sorted list, not a window maximum.

POV1 (45_pov1.sql): demographic winner per patid (one row survives the
normalize tie-break; duplicate-demographic patids are excluded), the
configured sex/race/hispanic values, the index date inside enrollment,
the age stratum, and the reported SAS yrdif age.

Pure functions only; float comparisons are the caller's business.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from math import floor, isclose

from qrp.config import CohortConfig, StockpilingRule, StudyConfig
from qrp.pipeline import _enr_cfg_id

from ..synth import SyntheticDataset
from .b_enrollment_model import containing_episode, enrollment_episodes

# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------


def _round_half_away(x: float) -> int:
    """DuckDB round() on doubles: ties away from zero (probed: round(10.5)
    = 11, round(11.5) = 12). Supplies are positive, so floor(x + 0.5)."""
    return floor(x + 0.5)


def _apply_op(op: str, values: list[float]) -> float:
    if op == "sum":
        return float(sum(values))
    if op == "min":
        return min(values)
    if op == "max":
        return max(values)
    if op == "mean":
        return sum(values) / len(values)
    raise AssertionError(f"unsupported same-day operator {op!r}")


def _in_ranges(v: float, ranges: tuple) -> bool:
    """SUPRANGE/AMTRANGE pieces (lo, lo_open, hi, hi_open); a value must
    fall inside at least one piece."""
    return any(
        (v > lo if lo_open else v >= lo) and (v < hi if hi_open else v <= hi)
        for lo, lo_open, hi, hi_open in ranges
    )


def _claims_from(study: StudyConfig) -> date:
    """The extraction lower bound: widest_lookback_days for the simple
    studies this package builds (no covariates/inclusions/risk scores)."""
    for attr in ("covariates", "inclusions", "risk_scores"):
        if getattr(study, attr):
            raise AssertionError("oracle covers only plain studies")
    lookback = 0
    for c in study.cohorts:
        lookback = max(lookback, c.wash_per or 0, c.fup_wash_per or 0,
                       c.cum_dose_per or 0)
    return study.start_date - timedelta(days=lookback)


# ---------------------------------------------------------------------
# Stockpiling
# ---------------------------------------------------------------------

STOCKPILED_COLS = ("cohortgrp", "stockgroup", "patid", "adate", "expiredt",
                   "orig_adate", "rxsup", "rxamt", "numdispensing")


@dataclass(frozen=True)
class _Claim:
    stockgroup: str
    patid: int
    adate: date
    rxsup: int      # CODESUPPLY-applied supply
    rxamt: float
    codetype: str | None


def _rx_claims(dataset: SyntheticDataset, cohort: CohortConfig,
               claims_from: date, claims_to: date) -> list[_Claim]:
    """Exposure extraction for one cohort (RX branch only: this package's
    studies define exposure from dispensings alone)."""
    out: list[_Claim] = []
    supply_by_code = {
        code: code_supply
        for code, codecat, _ct, code_supply in cohort.exposure_codes
        if codecat == "RX"
    }
    codetype_by_code = {
        code: (codetype or "").upper()
        for code, codecat, codetype, _sup in cohort.exposure_codes
        if codecat == "RX"
    }
    for d in dataset.dispensings:
        if d.rxsup is None or d.rxsup <= 0 or not d.rxamt or d.rxamt <= 0:
            continue  # cdm_dispensing WHERE clause
        configured = codetype_by_code.get(d.rx)
        if configured is None:
            continue
        src_ct = None if d.rx_codetype is None else d.rx_codetype.upper()
        if configured != "" and src_ct is not None and src_ct != configured:
            continue
        rxsup = supply_by_code[d.rx] or d.rxsup
        if d.rxdate + timedelta(days=rxsup - 1) < claims_from:
            continue  # supply must REACH the window, not start in it
        if d.rxdate > claims_to:
            continue
        out.append(_Claim(
            stockgroup=dict(cohort.exposure_stockgroups).get(d.rx, "_default"),
            patid=d.patid, adate=d.rxdate, rxsup=rxsup, rxamt=float(d.rxamt),
            codetype=src_ct,
        ))
    return out


def _group_rule(study: StudyConfig, cohortgrp: str, stockgroup: str,
) -> StockpilingRule | None:
    for r in study.stockpiling:
        if r.cohortgrp == cohortgrp and r.stockgroup == stockgroup:
            return r
    return None


def model_stockpiled(dataset: SyntheticDataset,
                     study: StudyConfig) -> list[tuple]:
    """Model output of the `stockpiled` table (RX rows) for every cohort,
    in STOCKPILED_COLS order."""
    enrollment = enrollment_episodes(dataset, study)
    claims_from = _claims_from(study)
    claims_to = study.effective_censor_date
    rows: list[tuple] = []
    for cohort in study.cohorts:
        cfg = _enr_cfg_id(cohort)
        chain_floor = study.start_date - timedelta(days=cohort.enr_days)
        # ---- pre-shave: fill must START inside an episode; supply is
        # trimmed at the episode end, amount prorated.
        shaved: dict[tuple[str, int, date], list[tuple[float, float]]] = {}
        for claim in _rx_claims(dataset, cohort, claims_from, claims_to):
            if claim.adate + timedelta(days=claim.rxsup - 1) < chain_floor:
                continue  # the chain only admits fills reaching enr floor
            episodes = enrollment.get((cfg, claim.patid), ())
            ep = containing_episode(episodes, claim.adate)
            if ep is None:
                continue
            sup = min(claim.rxsup, (ep.enr_end - claim.adate).days + 1)
            amt = claim.rxamt * sup / claim.rxsup
            shaved.setdefault(
                (claim.stockgroup, claim.patid, claim.adate), []).append((sup, amt))
        # ---- ranges, same-day collapse, sequential recurrence per group
        groups: dict[tuple[str, int], list[tuple[date, float, float, int]]] = {}
        for (sg, patid, adate), pairs in shaved.items():
            rule = _group_rule(study, cohort.cohortgrp, sg)
            if rule is not None:
                keep = [p for p in pairs
                        if _in_ranges(p[0], rule.suprange)
                        and _in_ranges(p[1], rule.amtrange)]
            else:
                keep = list(pairs)
            if not keep:
                continue
            sup_op = rule.sup_op if rule else "sum"
            amt_op = rule.amt_op if rule else "sum"
            sups = [p[0] for p in keep]
            amts = [p[1] for p in keep]
            sup = (_round_half_away(_apply_op(sup_op, sups))
                   if sup_op == "mean" else int(_apply_op(sup_op, sups)))
            amt = _apply_op(amt_op, amts)
            groups.setdefault((sg, patid), []).append(
                (adate, float(sup), amt, len(keep)))
        for (sg, patid), day_rows in groups.items():
            rule = _group_rule(study, cohort.cohortgrp, sg)
            day_rows.sort(key=lambda r: r[0])
            recs = _sequential_recurrence(day_rows,
                                          rule.percentdays if rule else None)
            # ---- re-shave: a pushed start must sit inside an episode;
            # supply running past its end is trimmed there, amount prorated.
            for rec in recs:
                episodes = enrollment.get((cfg, patid), ())
                ep = containing_episode(episodes, rec.start)
                if ep is None:
                    continue
                new_end = min(rec.end, ep.enr_end)
                new_sup = (new_end - rec.start).days + 1
                if new_sup <= 0:
                    continue
                amt = rec.amt * new_sup / rec.sup
                rows.append((cohort.cohortgrp, sg, patid, rec.start,
                             new_end, rec.orig, new_sup, amt, rec.n))
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3], r[4]))
    return rows


@dataclass(frozen=True)
class _Rec:
    start: date      # (possibly pushed) supply start
    end: date        # (possibly pushed) supply end
    orig: date       # original fill date
    sup: float
    amt: float
    n: int           # numdispensing
    seq: int


def _sequential_recurrence(
    day_rows: list[tuple[date, float, float, int]],
    percentdays: float | None,
) -> list[_Rec]:
    """One record per fill day, claim at a time, then the PERCENTDAYS
    truncation of overrun sequences."""
    recs: list[_Rec] = []
    for adate, sup, amt, n in day_rows:
        if not recs:
            recs.append(_Rec(adate, adate + timedelta(days=int(sup) - 1),
                             adate, sup, amt, n, 1))
            continue
        prev = recs[-1]
        overlap = (prev.end - adate).days + 1
        if percentdays is None:
            push = overlap > 0
        else:
            push = overlap > 0 and overlap / prev.sup <= percentdays
        if push:
            start = prev.end + timedelta(days=1)
            end = start + timedelta(days=int(sup) - 1)
            recs.append(_Rec(start, end, adate, sup, amt, n, prev.seq))
        else:
            recs.append(_Rec(adate, adate + timedelta(days=int(sup) - 1),
                             adate, sup, amt, n, prev.seq + 1))
    if percentdays is not None:
        recs = _truncate_overrun(recs)
    return recs


def _truncate_overrun(recs: list[_Rec]) -> list[_Rec]:
    """Each sequence that was started because PERCENTDAYS was exceeded
    truncates the previous sequence: its records end the day before the
    new sequence starts, with days supplied reduced and amount prorated;
    a record left with no days is dropped."""
    starts: dict[int, date] = {}
    for r in recs:
        starts.setdefault(r.seq, r.start)
    out: list[_Rec] = []
    for r in recs:
        nxt = starts.get(r.seq + 1)
        if nxt is not None and r.end >= nxt:
            new_sup = (nxt - timedelta(days=1) - r.start).days + 1
            if new_sup <= 0:
                continue
            out.append(_Rec(r.start, nxt - timedelta(days=1), r.orig,
                            new_sup, r.amt * new_sup / r.sup, r.n, r.seq))
        else:
            out.append(r)
    return out


# ---------------------------------------------------------------------
# Index dates and POV1
# ---------------------------------------------------------------------

POV1_COLS = ("cohortgrp", "patid", "indexdt", "expiredt", "rxsup", "rxamt",
             "numdispensing", "enr_start", "enr_end", "birth_date", "sex",
             "race", "hispanic", "zip", "zip_date", "age", "agegroup",
             "agegroupnum", "deathdt")


def model_index_dates(dataset: SyntheticDataset, study: StudyConfig,
                      stockpiled: list[tuple]) -> list[tuple]:
    """Model `index_candidates`: dedupe per patient-day keeping the
    longest supply (SAS nodupkey, expiredt descending), then a sequential
    washout scan."""
    enrollment = enrollment_episodes(dataset, study)
    best: dict[tuple[str, int, date], tuple] = {}
    for row in stockpiled:
        cohortgrp, _sg, patid, adate, expiredt, orig, rxsup, rxamt, n = row
        cohort = next(c for c in study.cohorts if c.cohortgrp == cohortgrp)
        cfg = _enr_cfg_id(cohort)
        if containing_episode(enrollment.get((cfg, patid), ()), adate) is None:
            continue  # index detection is enrollment-bounded
        key = (cohortgrp, patid, adate)
        # total-order tie-break: longest supply wins, then the documented
        # residual order (rxsup, rxamt desc; orig_adate asc)
        cand = (-expiredt.toordinal(), -rxsup, -rxamt, orig.toordinal(), row)
        if key not in best or cand[:4] < best[key][:4]:
            best[key] = cand
    ordered = sorted(best.values(), key=lambda c: (c[4][0], c[4][2], c[4][3]))
    out: list[tuple] = []
    prior_max: dict[tuple[str, int], date | None] = {}
    for cand in ordered:
        row = cand[4]
        cohortgrp, _sg, patid, adate, expiredt = row[0], row[1], row[2], row[3], row[4]
        cohort = next(c for c in study.cohorts if c.cohortgrp == cohortgrp)
        prev = prior_max.get((cohortgrp, patid))
        prior_max[(cohortgrp, patid)] = (
            expiredt if prev is None or expiredt > prev else prev)
        wash = cohort.wash_per
        if wash is None:
            keep = prev is None
        elif wash == 0:
            keep = True
        else:
            keep = prev is None or prev < adate - timedelta(days=wash)
        if keep:
            out.append((cohortgrp, patid, adate, expiredt, row[5], row[6],
                        row[7], row[8]))
    return out


INDEX_COLS = ("cohortgrp", "patid", "adate", "expiredt", "orig_adate",
              "rxsup", "rxamt", "numdispensing")


def _demographics(dataset: SyntheticDataset) -> tuple[dict[int, dict], set[int]]:
    """One demographic row per patid (the normalize tie-break winner)
    plus the duplicate-demographic patids the pipeline excludes from
    POV1 entirely."""
    by_patid: dict[int, list] = {}
    for p in dataset.patients:
        by_patid.setdefault(p.patid, []).append(p)
    multi = {pid for pid, rows in by_patid.items() if len(rows) > 1}

    def sex_collapse(raw: str) -> str:
        u = raw.upper()
        return u if u in ("F", "M") else "O"

    winners: dict[int, dict] = {}
    for pid, rows in by_patid.items():
        if any(r.birth_date is None or r.sex is None for r in rows):
            continue  # demographics_missing: excluded from POV1
        best = min(rows, key=lambda r: (
            r.birth_date, sex_collapse(r.sex),
            (r.race or "M").upper(), (r.hispanic or "U").upper(),
            str(r.postalcode)))
        winners[pid] = {
            "birth_date": best.birth_date,
            "sex_raw": best.sex.upper(),
            "sex": sex_collapse(best.sex),
            "race": (best.race or "M").upper(),
            "hispanic": (best.hispanic or "U").upper(),
            "zip": str(best.postalcode),
            "zip_date": best.postalcode_date,
        }
    return winners, multi


def _age_years(birth: date, as_of: date) -> int:
    """Birthday-aware whole years (SAS INTCK 'YEAR' 'C')."""
    years = as_of.year - birth.year
    if (as_of.month, as_of.day) < (birth.month, birth.day):
        years -= 1
    return years


def _add_years(d: date, n: int) -> date:
    """Calendar-year addition with Feb-29 clamped to Feb-28 (probed
    DuckDB behavior: DATE '2000-02-29' + 1 YEAR = 2001-02-28)."""
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=d.day - 1)


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _age_yrdif(birth: date, as_of: date) -> float:
    """Reported SAS yrdif(..., 'AGE'): whole years plus the days since
    the last birthday divided by an ALWAYS-365 denominator, with any
    29 February in between NOT counted (the documented intent of the
    age_yrdif macro)."""
    whole = _age_years(birth, as_of)
    last_bday = _add_years(birth, whole)
    days = (as_of - last_bday).days
    leaps = sum(
        1 for y in range(last_bday.year, as_of.year + 1)
        if _is_leap(y) and last_bday < date(y, 2, 29) <= as_of)
    return whole + (days - leaps) / 365.0


def model_pov1(dataset: SyntheticDataset, study: StudyConfig,
               stockpiled: list[tuple]) -> list[tuple]:
    """Model `pov1` rows (POV1_COLS order)."""
    enrollment = enrollment_episodes(dataset, study)
    demos, multi = _demographics(dataset)
    deaths: dict[int, date] = {}
    for d in dataset.deaths:
        if d.deathdt is not None and (
                d.patid not in deaths or d.deathdt < deaths[d.patid]):
            deaths[d.patid] = d.deathdt
    candidates = model_index_dates(dataset, study, stockpiled)
    rows: list[tuple] = []
    for cohort in study.cohorts:
        cfg = _enr_cfg_id(cohort)
        for cand in candidates:
            if cand[0] != cohort.cohortgrp:
                continue
            _grp, patid, indexdt, expiredt, orig, rxsup, rxamt, n = cand
            if patid in multi or patid not in demos:
                continue
            dm = demos[patid]
            if (dm["sex_raw"] not in cohort.sex
                    or dm["race"] not in cohort.race
                    or dm["hispanic"] not in cohort.hispanic):
                continue
            ep = containing_episode(enrollment.get((cfg, patid), ()), indexdt)
            if ep is None:
                continue
            stratum = next(
                (s for s in cohort.age_strata.strata
                 if s.lo <= _age_years(dm["birth_date"], indexdt) <= s.hi
                 and s.unit == "years"),
                None)
            if stratum is None:
                continue  # the age-stratum join is INNER
            rows.append((
                cohort.cohortgrp, patid, indexdt, expiredt, rxsup, rxamt, n,
                ep.enr_start, ep.enr_end, dm["birth_date"], dm["sex"],
                dm["race"], dm["hispanic"], dm["zip"], dm["zip_date"],
                _age_yrdif(dm["birth_date"], indexdt),
                stratum.label, stratum.ordinal, deaths.get(patid),
            ))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return rows


def float_eq(a: float, b: float) -> bool:
    return isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


__all__ = [
    "INDEX_COLS",
    "POV1_COLS",
    "STOCKPILED_COLS",
    "float_eq",
    "model_index_dates",
    "model_pov1",
    "model_stockpiled",
]
