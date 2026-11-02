"""Independent day-enumeration oracle for 92_cidadenom.sql (denomcounts).

Contract (docs/SAS_PARITY.md "enrolled member-days", docs/PARITY_FINDINGS.md
"Third fix: shave by the exclusion conditions", docs/OUTPUTS.md OUTPUTDENOM):

* window  : denom_start = max(enr_start + enr_days, query start);
            denom_end   = min(death-truncated enr_end, query end) - pullback,
            pullback    = max(0, min_epis_dur-1, min_days_supp-1, blackout_per,
                              req_days_aft_ind,
                              req_days_aft_epi + max(min_epis_dur-1,
                                                     min_days_supp-1,
                                                     blackout_per));
            kept only when denom_end >= denom_start.
* shaving : with wash_per <> 0, stockpiled exposure inside an enrollment
            span makes [chained_start+1, min(chained_end, span_end)+wash_per]
            ineligible; an EXCLUSION-condition claim at D makes
            [D-condto, D-condfrom] ineligible; a follow-up EVENT claim at A
            makes [A-blackout+1, A+code_supply-1+fup_wash_per] ineligible
            (per-cohort scopes, not part of the shared config).
* counting: per USERSTRATA t2cida level and stratum, dennumpts counts
            DISTINCT members with >= 1 eligible day; dennummemdays sums
            eligible days (NULL when output_denom = 'M'). Stratum values
            are taken at each contiguous eligible segment's start (age at
            segment start, year of segment start), as the docs state.
* gates   : members with duplicate or missing demographics are excluded;
            cohorts with output_denom = 'N' get no rows.

INDEPENDENCE: eligibility is decided per CALENDAR DAY by subtracting
ineligible intervals from the window's day set; segments are the maximal
contiguous runs of surviving days. The implementation instead does closed-
form interval algebra with lag/lead gap-splitting; no expression is shared.
Exclusion shaving is part of this contract per docs/PARITY_FINDINGS.md
"Third fix"; INCLUSION-condition shaving is NOT modelled because it is
implemented nowhere in the package (recorded as an open question in
investigation/packages/D.md).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from qrp.config import StudyConfig

from ..synth import SyntheticDataset, rows_for_table

DAY = timedelta(days=1)


def _days(a: date, b: date) -> set[date]:
    """Every day of a closed interval. Small domains only (bounded fixtures)."""
    if b < a:
        return set()
    return {a + timedelta(i) for i in range((b - a).days + 1)}


def _merge(intervals: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Union of closed intervals."""
    out: list[list[date]] = []
    for s, e in sorted(i for i in intervals if i[0] <= i[1]):
        if out and s <= _add(out[-1][1], 1):
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _add(d: date, n: int) -> date:
    return d + timedelta(days=n)


def _segments(
    window: tuple[date, date],
    ineligible: list[tuple[date, date]],
) -> list[tuple[date, date]]:
    """Maximal contiguous runs of eligible days inside a window.

    THE day enumeration: the window's days minus every ineligible day,
    then grouped into runs. Each run is one output segment, mirroring the
    documented "a window split by an exposure becomes two windows".
    """
    days = _days(*window)
    for s, e in ineligible:
        days -= _days(max(s, window[0]), min(e, window[1]))
    runs: list[tuple[date, date]] = []
    for d in sorted(days):
        if runs and _add(runs[-1][1], 1) == d:
            runs[-1] = (runs[-1][0], d)
        else:
            runs.append((d, d))
    return runs


def _age_years(birth: date, as_of: date) -> int:
    """SAS INTCK('YEAR', ..., 'C'): birthday-aware whole years."""
    return as_of.year - birth.year - (
        (as_of.month, as_of.day) < bday(birth))


def bday(b: date) -> tuple[int, int]:
    return (b.month, b.day)


def _age_in_unit(birth: date, as_of: date, unit: str) -> int | None:
    if unit == "years":
        return _age_years(birth, as_of)
    if unit == "months":
        return (as_of.year - birth.year) * 12 + as_of.month - birth.month \
            - (as_of.day < birth.day)
    if unit == "days":
        return (as_of - birth).days
    if unit == "weeks":
        return (as_of - birth).days // 7
    return None


# ---------------------------------------------------------------------
# Modeled upstream state (documented semantics, not SQL)
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class _Member:
    patid: int
    birth_date: date
    sex_raw: str
    race: str
    hispanic: str


@dataclass(frozen=True)
class _Span:
    patid: int
    episode: int
    start: date
    end: date


def _has_coverage(medcov: str, drugcov: str, coverage: str) -> bool:
    m, d = medcov.upper(), drugcov.upper()
    return (coverage.upper() == "MD" and m in ("Y", "A") and d == "Y") \
        or (coverage.upper() == "M" and m in ("Y", "A")) \
        or (coverage.upper() == "D" and d == "Y") \
        or coverage.upper() not in ("MD", "M", "D")


def _modeled_demographics(dataset: SyntheticDataset) -> \
        tuple[dict[int, _Member], set[int]]:
    """One member per patid (total-order tie-break), plus the duplicate set.

    The duplicate set is what the denominator excludes on (members with
    more than one demographic row), regardless of which row wins.
    """
    by_patid: dict[int, list[tuple[Any, ...]]] = defaultdict(list)
    for r in rows_for_table(dataset, "demographic"):
        if r["birth_date"] is None or r["sex"] is None:
            continue
        by_patid[r["patid"]].append(
            (r["birth_date"], str(r["sex"]).upper(),
             str(r["race"]).upper(), str(r["hispanic"]).upper(),
             str(r["postalcode"])))
    members: dict[int, _Member] = {}
    multi: set[int] = set()
    for patid, rows in by_patid.items():
        if len(rows) > 1:
            multi.add(patid)
        b, sx, rc, hi, _ = min(rows)
        members[patid] = _Member(
            patid, b, sx,
            rc if rc is not None else "M",
            hi if hi is not None else "U")
    return members, multi


def _modeled_enrollment(
    dataset: SyntheticDataset, study: StudyConfig,
) -> dict[tuple[str, int, bool], list[_Span]]:
    """Enrollment episodes per (coverage, enrol_gap, chart_required).

    Gap-and-islands on sorted spans: a run breaks when the gap exceeds
    enrol_gap or the coverage flags change in the mode's watched set;
    patients with any non-chart span overlapping the query period are
    dropped entirely when chart_required.
    """
    spans_raw = [
        r for r in rows_for_table(dataset, "enrollment")
        if r["enr_start"] is not None and r["enr_end"] is not None
        and r["enr_end"] >= r["enr_start"]
    ]
    out: dict[tuple[str, int, bool], list[_Span]] = {}
    for c in study.cohorts:
        key = (c.coverage, c.enrol_gap, c.chart_required)
        if key in out:
            continue
        if c.chart_required:
            nochart = {r["patid"] for r in spans_raw
                       if str(r.get("chart") or "N").upper() != "Y"
                       and r["enr_start"] <= study.end_date
                       and r["enr_end"] >= study.start_date}
        else:
            nochart = set()
        per_patid: dict[int, list[tuple[date, date, str, str]]] = defaultdict(
            list)
        for r in spans_raw:
            if r["patid"] in nochart:
                continue
            if not _has_coverage(str(r["medcov"]), str(r["drugcov"]),
                                 c.coverage):
                continue
            per_patid[r["patid"]].append(
                (r["enr_start"], r["enr_end"],
                 str(r["medcov"]).upper(), str(r["drugcov"]).upper()))
        spans: list[_Span] = []
        for patid, rows in per_patid.items():
            rows.sort()
            ep = 0
            prev_end: date | None = None
            prev_m = prev_d = ""
            for s, e, m, d in rows:
                new = prev_end is None \
                    or (s - prev_end).days - 1 > c.enrol_gap \
                    or (c.coverage.upper() == "MD"
                        and (m != prev_m or d != prev_d)) \
                    or (c.coverage.upper() == "M" and m != prev_m) \
                    or (c.coverage.upper() == "D" and d != prev_d)
                if new:
                    ep += 1
                    spans.append(_Span(patid, ep, s, e))
                else:
                    last = spans[-1]
                    spans[-1] = _Span(patid, ep, min(last.start, s),
                                      max(last.end, e))
                prev_end, prev_m, prev_d = e, m, d
        out[key] = spans
    return out


def _ip_days(dataset: SyntheticDataset, study: StudyConfig) -> set[
        tuple[int, date]]:
    """Inpatient stay days under the study's run_envelope setting."""
    days: set[tuple[int, date]] = set()
    for r in rows_for_table(dataset, "encounter"):
        if str(r["enctype"]).upper().strip() != "IP" or r["adate"] is None:
            continue
        end = max(r["adate"], r["ddate"] or r["adate"])
        start = r["adate"] if study.run_envelope == 0 else _add(r["adate"], 1)
        if study.run_envelope == 2:
            continue
        days |= {(r["patid"], d) for d in _days(start, end)}
    return days


def _enveloped_claims(dataset: SyntheticDataset, study: StudyConfig) -> \
        tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Diagnosis and procedure claims with care setting / principal flag
    after enveloping, as the normalized claim set presents them."""
    ips = _ip_days(dataset, study)
    dx: list[dict[str, Any]] = []
    for r in rows_for_table(dataset, "diagnosis"):
        if r["adate"] is None:
            continue
        env = (r["patid"], r["adate"]) in ips
        dx.append({
            "patid": r["patid"], "adate": r["adate"],
            "code": str(r["dx"]), "codetype": r["dx_codetype"],
            "pdx": "X" if env else str(r["pdx"] or "X").upper(),
            "enctype": "IP" if env else str(r["enctype"] or "XX").upper(),
        })
    px: list[dict[str, Any]] = []
    for r in rows_for_table(dataset, "procedure"):
        if r["adate"] is None:
            continue
        env = (r["patid"], r["adate"]) in ips
        px.append({
            "patid": r["patid"], "adate": r["adate"],
            "code": str(r["px"]), "codetype": r["px_codetype"],
            "pdx": "X" if env else "",
            "enctype": "IP" if env else str(r["enctype"] or "").upper(),
        })
    return dx, px


def _codetype_matches(configured: str, claimed: str | None) -> bool:
    if configured == "" or configured is None:
        return True
    if claimed is None:
        return True
    return claimed.upper() == configured.upper()


# ---------------------------------------------------------------------
# The oracle
# ---------------------------------------------------------------------

DENOM_COLUMNS = (
    "level", "group", "agegroup", "agegroupnum", "sex", "race", "hispanic",
    "year", "zip_uncertain", "zip3", "state", "hhs_reg", "cb_reg",
    "month", "quarter", "dennumpts", "dennummemdays",
)


def expected_denomcounts(dataset: SyntheticDataset,
                         study: StudyConfig,
                         facts: dict[str, int] | None = None,
                         ) -> list[tuple[Any, ...]]:
    """Expected denomcounts rows, in the table's column order.

    `facts`, when given, receives coverage counters (windows, shaving
    hits, exclusion hits) for the suite's event() statistics.
    """

    def _fact(k: str, n: int = 1) -> None:
        if facts is not None:
            facts[k] = facts.get(k, 0) + n

    claims_from = (study.start_date - timedelta(days=study.widest_lookback_days)
                   if study.widest_lookback_days is not None else date.min)
    claims_to = study.effective_censor_date

    members, multi = _modeled_demographics(dataset)
    deaths: dict[int, date] = {}
    for r in rows_for_table(dataset, "death"):
        if r["deathdt"] is not None:
            deaths[r["patid"]] = min(deaths.get(r["patid"], r["deathdt"]),
                                     r["deathdt"])
    enrollment = _modeled_enrollment(dataset, study)
    dx, px = _enveloped_claims(dataset, study)
    dispensings = [r for r in rows_for_table(dataset, "dispensing")
                   if r["rxsup"] is not None and r["rxsup"] > 0
                   and r["rxamt"] is not None and r["rxamt"] > 0
                   and r["rxdate"] is not None]

    levels = study.cida_levels()
    rows: list[tuple[Any, ...]] = []
    for cohort in study.cohorts:
        if cohort.output_denom == "N" or not levels:
            continue
        spans = enrollment[(cohort.coverage, cohort.enrol_gap,
                            cohort.chart_required)]
        spans_by_patid: dict[int, list[_Span]] = defaultdict(list)
        for s in spans:
            spans_by_patid[s.patid].append(s)

        def_rx: dict[str, int | None] = {}
        for code, codecat, codetype, supply in cohort.exposure_codes:
            if codecat == "RX":
                def_rx[code] = supply
        event_rx: dict[str, int | None] = {}
        event_med: set[tuple[str, str]] = set()
        for code, codecat, codetype, supply in cohort.event_codes:
            if codecat == "RX":
                event_rx[code] = supply
            else:
                event_med.add((code, codecat))
        care: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for code, enctype, pdx in cohort.event_care_settings:
            care[code].append((enctype, pdx))
        for code in list(event_rx) + [c for c, _ in event_med]:
            if not care[code]:
                care[code] = [("**", "*")]

        exc_rules = [r for r in study.inclusions
                     if r.cohortgrp == cohort.cohortgrp
                     and r.criteria == "EXC"
                     and getattr(r, "condfromanchor", "") != "EPISODEENDDT"
                     and getattr(r, "condtoanchor", "") != "EPISODEENDDT"]

        pullback = max(0, cohort.min_epis_dur - 1, cohort.min_days_supp - 1,
                       cohort.blackout_per, cohort.req_days_aft_ind,
                       cohort.req_days_aft_epi
                       + max(cohort.min_epis_dur - 1, cohort.min_days_supp - 1,
                             cohort.blackout_per))

        # ---- windows ---------------------------------------------------
        windows: list[tuple[int, date, date]] = []
        for s in spans:
            if s.end < study.start_date:
                continue
            death = deaths.get(s.patid)
            end_d = death if death is not None and death <= s.end else s.end
            if death is not None and death <= s.end:
                _fact("death-clipped-spans")
            dstart = max(_add(s.start, cohort.enr_days), study.start_date)
            dend = min(end_d, study.end_date)
            dend = dend - timedelta(days=pullback)
            if dend >= dstart:
                windows.append((s.patid, dstart, dend))

        # ---- shared ineligible: washout + exclusion --------------------
        washout: dict[int, list[tuple[date, date]]] = defaultdict(list)
        if (cohort.wash_per or 0) != 0:
            kept: dict[tuple[int, int, date], list[tuple[date, int]]] = \
                defaultdict(list)
            for d in dispensings:
                if str(d["rx"]) not in def_rx:
                    continue
                if not _codetype_matches("ND", d["rx_codetype"]):
                    continue
                rxsup = def_rx[str(d["rx"])] or d["rxsup"]
                if _add(d["rxdate"], rxsup - 1) < _add(study.start_date,
                                                       -cohort.enr_days):
                    continue
                for s in spans_by_patid.get(d["patid"], ()):
                    if s.start <= d["rxdate"] <= s.end:
                        kept[(s.patid, s.episode, s.start)].append(
                            (d["rxdate"], rxsup))
                        break
            for (patid, _ep, _span_start), fills in kept.items():
                fills.sort()
                cum = 0
                ra: int | None = None
                epoch = date(1970, 1, 1)
                for adate, sup in fills:
                    cum += sup
                    anchor = (adate - epoch).days - (cum - sup)
                    ra = anchor if ra is None else max(ra, anchor)
                    chained_start = epoch + timedelta(days=cum - 1 + ra - sup + 1)
                    chained_end = epoch + timedelta(days=cum - 1 + ra)
                    # The ineligible period attaches to ANY enrollment span
                    # containing the CHAINED start (the implementation
                    # re-joins on the chained date), which with gapped
                    # enrollment can be a later span than the claim's own.
                    host = next((x for x in spans_by_patid.get(patid, ())
                                 if x.start <= chained_start <= x.end), None)
                    if host is None:
                        continue
                    washout[patid].append((
                        _add(chained_start, 1),
                        _add(min(chained_end, host.end), cohort.wash_per or 0)))

        exclusion: dict[int, list[tuple[date, date]]] = defaultdict(list)
        for rule in exc_rules:
            wanted = {(code, rule.codecat) for code in rule.codes}
            claims: list[tuple[int, date]] = []
            for c in dx:
                if (c["code"], "DX") in wanted:
                    claims.append((c["patid"], c["adate"]))
            for c in px:
                if (c["code"], "PX") in wanted:
                    claims.append((c["patid"], c["adate"]))
            for d in dispensings:
                if (str(d["rx"]), "RX") in wanted:
                    claims.append((d["patid"], d["rxdate"]))
            for patid, adate in claims:
                if not (claims_from <= adate <= claims_to):
                    continue
                exclusion[patid].append((_add(adate, -rule.condto),
                                         _add(adate, -rule.condfrom)))

        shared: dict[int, list[tuple[date, date]]] = defaultdict(list)
        for patid in set(washout) | set(exclusion):
            shared[patid] = _merge(washout[patid] + exclusion[patid])
        if washout:
            _fact("washout-hit-members", len(washout))
        if exclusion:
            _fact("exclusion-hit-members", len(exclusion))

        # ---- event periods and the event-member set --------------------
        # A member with ANY event period row is an event member for the
        # scope algebra, even when the period is degenerate (start > end,
        # which happens when blackout_per = 0 and supply = 1 with no
        # follow-up wash): the implementation's member set is built
        # before the degenerate-period guard.
        ev_claims: list[tuple[int, date, str, str, str, int | None]] = []
        for c in dx:
            if (c["code"], "DX") in event_med:
                ev_claims.append((c["patid"], c["adate"], c["enctype"],
                                  c["pdx"], c["code"], None))
        for c in px:
            if (c["code"], "PX") in event_med:
                ev_claims.append((c["patid"], c["adate"], c["enctype"],
                                  c["pdx"], c["code"], None))
        for d in dispensings:
            if str(d["rx"]) in event_rx:
                ev_claims.append((d["patid"], d["rxdate"], "**", "",
                                  str(d["rx"]), event_rx[str(d["rx"])]))
        event_periods: dict[int, list[tuple[date, date]]] = defaultdict(list)
        event_patids: set[int] = set()
        for patid, adate, enctype, pdx, code, supply in ev_claims:
            if not (claims_from <= adate <= claims_to):
                continue
            if not any((ce == "**" or ce == enctype)
                       and (cp == "*" or cp == pdx)
                       for ce, cp in care[code]):
                continue
            event_patids.add(patid)
            eff_supply = supply if supply is not None else 1
            event_periods[patid].append((
                _add(adate, -cohort.blackout_per + 1),
                _add(adate, eff_supply - 1 + (cohort.fup_wash_per or 0))))

        # ---- strata accumulation ---------------------------------------
        star_pts: dict[tuple[Any, ...], set[int]] = defaultdict(set)
        star_days: dict[tuple[Any, ...], int] = defaultdict(int)
        a_pts: dict[tuple[Any, ...], set[int]] = defaultdict(set)
        a_days: dict[tuple[Any, ...], int] = defaultdict(int)
        b_pts: dict[tuple[Any, ...], set[int]] = defaultdict(set)
        b_days: dict[tuple[Any, ...], int] = defaultdict(int)

        for patid, dstart, dend in windows:
            _fact("windows")
            m = members.get(patid)
            if m is None or patid in multi:
                continue
            if (cohort.sex and m.sex_raw not in cohort.sex) \
                    or (cohort.race and m.race not in cohort.race) \
                    or (cohort.hispanic and m.hispanic not in cohort.hispanic):
                continue
            if not cohort.sex or not cohort.race or not cohort.hispanic:
                continue
            for level in levels:
                has = {"agegroup": "agegroup" in level.levelvars,
                       "sex": "sex" in level.levelvars,
                       "race": "race" in level.levelvars,
                       "hispanic": "hispanic" in level.levelvars,
                       "year": "index_year" in level.levelvars
                       or "year" in level.levelvars}
                for scope, segs in (
                        ("*", _segments((dstart, dend), shared.get(patid, []))),
                        ("a", _segments((dstart, dend),
                                        shared.get(patid, [])
                                        + event_periods.get(patid, [])))
                        if patid in event_patids else ("a", []),
                        ("b", _segments((dstart, dend), shared.get(patid, [])))
                        if patid in event_patids else ("b", [])):
                    for seg_start, seg_end in segs:
                        if segs != [(dstart, dend)]:
                            _fact("shaved-windows")
                        # An INNER join to the band table: a segment whose
                        # age matches NO band is dropped, and one matching
                        # several bands is counted once per band (the join
                        # fans out), exactly as the implementation's join
                        # does.
                        bands: list[tuple[str, int]] = []
                        for st in cohort.age_strata.strata:
                            v = _age_in_unit(m.birth_date, seg_start, st.unit)
                            if v is not None and st.lo <= v <= st.hi:
                                bands.append((st.label, st.ordinal))
                        if has["agegroup"] and not bands:
                            continue
                        if not has["agegroup"]:
                            bands = [(None, None)]  # type: ignore[list-item]
                        for band_label, band_ordinal in bands:
                            key = (
                                level.level_id,
                                cohort.cohortgrp,
                                band_label if has["agegroup"] else None,
                                band_ordinal if has["agegroup"] else None,
                                m.sex_raw if has["sex"] else None,
                                m.race if has["race"] else None,
                                m.hispanic if has["hispanic"] else None,
                                str(seg_start.year) if has["year"] else None,
                            )
                            n_days = (seg_end - seg_start).days + 1
                            if scope == "*":
                                star_pts[key].add(patid)
                                star_days[key] += n_days
                            elif scope == "a":
                                a_pts[key].add(patid)
                                a_days[key] += n_days
                            else:
                                b_pts[key].add(patid)
                                b_days[key] += n_days

        for key in set(star_pts) | set(a_pts) | set(b_pts):
            npts = len(star_pts[key]) + len(a_pts[key]) - len(b_pts[key])
            if npts <= 0:
                continue
            mdays = None if cohort.output_denom == "M" else (
                star_days[key] + a_days[key] - b_days[key])
            rows.append((*key, None, None, None, None, None, None, None,
                         npts, mdays))
    if rows:
        _fact("output-nonempty", len(rows))
    return rows
