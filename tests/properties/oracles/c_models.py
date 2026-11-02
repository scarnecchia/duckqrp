# pattern: Functional Core
"""Brute-force models for dose restrictions, inclusion criteria, dose
censoring, and follow-up/censoring (package C).

These models are the INDEPENDENT oracles for stages 42, 52, 55 and 60.
They never transliterate the shipped SQL: each rule below is restated
from `docs/INCLUSION.md`, the stage SQL's contract comments, and the
SAS semantics they document, then evaluated with plain date arithmetic
over bounded windows. Upstream stage outputs (episodes, candidates,
claim sets) are INPUTS, taken as pinned by their own packages.

Everything here is a pure function of its arguments: no I/O, no
engine, no harness. The harness glue lives in `c_glue.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Mapping, Sequence

# ---------------------------------------------------------------------
# Numeric helpers. DuckDB's round() (and SAS's) round half AWAY FROM
# ZERO; Python's round() is banker's rounding, so the model must not
# use it.
# ---------------------------------------------------------------------


def round_half_away(x: float) -> int:
    """round() as DuckDB means it: halves go away from zero."""
    d = Decimal(repr(float(x))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(d)


def round1_half_away(x: float) -> float:
    """round(x, 1) as DuckDB means it."""
    return float(
        Decimal(repr(float(x))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    )


def overlap_days(a_start: date, a_end: date, b_start: date, b_end: date) -> int:
    """Length of the overlap of two CLOSED day intervals, 0 if disjoint."""
    lo = max(a_start, b_start)
    hi = min(a_end, b_end)
    return max(0, (hi - lo).days + 1)


# ---------------------------------------------------------------------
# Dose restrictions (42_dose.sql over the _claim_dose_view.sql view).
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class DoseClaim:
    """One `claim_dose` row: an exposure dispensing with dose attached.

    `strength is None` models a code absent from cfg_code_strength:
    the view's INNER join drops such claims entirely.
    """

    adate: date
    rxsup: int
    rxamt: float
    strength: float | None

    @property
    def expiredt(self) -> date:
        return self.adate + timedelta(days=self.rxsup - 1)

    @property
    def cumdose(self) -> float | None:
        if self.strength is None:
            return None
        return self.strength * self.rxamt

    @property
    def cfdd(self) -> float | None:
        if self.strength is None or self.rxsup <= 0:
            return None
        return self.strength * self.rxamt / self.rxsup


@dataclass(frozen=True)
class DoseCandidate:
    """One `index_candidates` row the dose stage may exclude."""

    cohortgrp: str
    patid: int
    adate: date          # the (possibly chained) index date
    orig_adate: date     # the raw fill date dose arithmetic anchors on


@dataclass(frozen=True)
class DoseVerdict:
    """What 42_dose.sql decides for one candidate.

    `reasons` is the SET of DISTINCT reason rows dose_excluded holds
    for the candidate: the shipped exclusion table is DISTINCT over
    (cohortgrp, patid, adate, reason) above a fanned join, so two
    same-day claims failing DIFFERENT bounds produce two reason rows.
    Each row's reason comes from the CASE's first true branch
    (min_cumdose > max_cumdose > min_cfdd > max_cfdd), each branch
    requiring its OWN predicate -- a row failing only the max bound
    reports max_cfdd even when min_cfdd is also set, which is the
    equal-min/max corner a priority-over-lumped-failure reading gets
    wrong. A row excluded through NULL cfdd gets reason None (the
    CASE's cfdd branches lack the NULL guard the WHERE has).
    """

    excluded: bool
    reasons: frozenset[str | None]


def dose_verdict(
    candidate: DoseCandidate,
    claims: Sequence[DoseClaim],
    *,
    min_cum_dose: float | None,
    max_cum_dose: float | None,
    cum_dose_per: int | None,
    min_cfdd: float | None,
    max_cfdd: float | None,
) -> DoseVerdict:
    """Evaluate one candidate index date by hand.

    Prior cumulative dose sums every dispensing whose FILL DATE lies in
    [orig_adate - cum_dose_per, orig_adate - 1] (the RANGE frame's
    closed day window; the candidate's own day is never counted). The
    exclusion CASE is evaluated PER JOINED ROW -- one row per same-day
    dispensing -- and dose_excluded keeps the DISTINCT reasons across
    rows. With no claim rows at all on the index day the LEFT JOIN
    emits one all-NULL row whose NULL cfdd fails either cfdd bound in
    the WHERE (reason None from the CASE, or a cum reason when a cum
    bound fires on the coalesced-zero prior).
    """
    # Same-day claims; the day's prior dose is identical on every row.
    same_day = [c for c in claims if c.adate == candidate.orig_adate]

    if cum_dose_per is None:
        prior = 0.0   # empty dose_lookback: COALESCE(prior_cumdose, 0)
    else:
        lo = candidate.orig_adate - timedelta(days=cum_dose_per)
        hi = candidate.orig_adate - timedelta(days=1)
        prior = 0.0
        for c in claims:
            if c.strength is None:
                continue    # dropped by the view's inner join
            if lo <= c.adate <= hi:
                prior += float(c.cumdose or 0.0)
    rounded_prior = round_half_away(prior)

    def row_reason(cfdd_value: float | None) -> str | None:
        """One joined row's CASE outcome: first true branch wins, each
        branch requiring its own predicate. NULL cfdd makes the CASE's
        cfdd branches fall through (None) even when the WHERE's NULL
        guard excludes the row -- WHERE membership is tracked
        separately in row_fired."""
        if min_cum_dose is not None and rounded_prior < min_cum_dose:
            return "min_cumdose"
        if max_cum_dose is not None and rounded_prior > max_cum_dose:
            return "max_cumdose"
        if min_cfdd is not None and cfdd_value is not None \
                and round_half_away(cfdd_value) < min_cfdd:
            return "min_cfdd"
        if max_cfdd is not None and cfdd_value is not None \
                and round_half_away(cfdd_value) > max_cfdd:
            return "max_cfdd"
        return None

    def row_fired(cfdd_value: float | None, reason: str | None) -> bool:
        """Whether the WHERE keeps this row: its own predicate on a
        non-NULL cfdd, or the NULL guard (any set cfdd bound) plus the
        cum predicates on NULL cfdd."""
        if reason is not None:
            return True
        return (cfdd_value is None
                and (min_cfdd is not None or max_cfdd is not None))

    reasons: set[str | None] = set()
    excluded = False
    for c in same_day:
        reason = row_reason(c.cfdd)
        if row_fired(c.cfdd, reason):
            excluded = True
            reasons.add(reason)
    if not same_day:
        # LEFT JOIN emits one all-NULL row: NULL cfdd fires either set
        # cfdd bound; the cum predicates evaluate on the coalesced
        # zero prior.
        reason = row_reason(None)
        fired = row_fired(None, reason)
        excluded = fired
        if fired:
            reasons.add(reason)
    return DoseVerdict(excluded=excluded, reasons=frozenset(reasons))


# ---------------------------------------------------------------------
# Inclusion criteria (52_inclusion.sql), index- and event-anchored.
# The same evaluator serves IEV/EEV: anchor every window on the event
# date and ignore the EPISODEENDDT anchors (the shipped event stage
# never consults them).
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class IncClaim:
    """One `cohort_claims` row (the covar_source narrow)."""

    patid: int
    adate: date
    expiredt: date
    code: str
    codecat: str
    rxsup: int | None
    rxamt: float | None


@dataclass(frozen=True)
class Episode:
    """One episode a rule set may exclude."""

    cohortgrp: str
    patid: int
    indexdt: date
    episodeenddt: date


@dataclass(frozen=True)
class RuleKey:
    """One (criteria, cond, subcond) condition key."""

    criteria: str
    cond: int
    subcond: int


@dataclass(frozen=True)
class RuleSpec:
    """The threshold set of one condition key (consistent across the
    key's code rows, as SAS requires and the loader warns about)."""

    codes: frozenset[str]
    codecat: str
    condfrom: int
    condto: int
    condfromanchor: str
    condtoanchor: str
    codedays: int
    minrxdays: int          # already reset to 1 for non-RX, as cfg carries
    mincumdose: float | None
    minafdd: float | None
    maxafdd: float | None
    subcond_inclusion: bool


def _window_for(spec: RuleSpec, anchor: date, episode_end: date,
                event_anchored: bool) -> tuple[date, date]:
    if event_anchored:
        return anchor + timedelta(days=spec.condfrom), \
            anchor + timedelta(days=spec.condto)
    start_anchor = episode_end if spec.condfromanchor == "EPISODEENDDT" else anchor
    end_anchor = episode_end if spec.condtoanchor == "EPISODEENDDT" else anchor
    return (start_anchor + timedelta(days=spec.condfrom),
            end_anchor + timedelta(days=spec.condto))


def _subcond_met(
    spec: RuleSpec,
    claims: Sequence[IncClaim],
    strengths: Mapping[str, float],
    window: tuple[date, date],
) -> bool:
    """Whether one subcondition is met.

    Counts are POOLED across the subcondition's codes onto the shared
    window: distinct matched days for `codedays`, summed supply overlap
    for `minrxdays`, pro-rated strength x amount for `mincumdose`, and
    the average filled daily dose for the afdd band. (The shipped
    stage aggregates one hits row per condition key; see the ledger's
    open question on per-code versus pooled reading of "alternatives".)
    """
    w_from, w_to = window
    matched = [
        c for c in claims
        if c.code in spec.codes and c.codecat == spec.codecat
        and c.adate <= w_to and c.expiredt >= w_from
    ]
    n_days = len({c.adate for c in matched})
    if n_days < spec.codedays:
        return False
    if spec.codecat == "RX":
        supply = sum(overlap_days(c.adate, c.expiredt, w_from, w_to)
                     for c in matched)
    else:
        supply = len(matched)   # SAS resets minrxdays to 1 elsewhere
    if supply < spec.minrxdays:
        return False
    if spec.mincumdose is None and spec.minafdd is None and spec.maxafdd is None:
        return True
    cumdose = 0.0
    cfdd_sum = 0.0
    for c in matched:
        strength = strengths.get(c.code)
        if strength is None or c.rxsup is None or c.rxamt is None:
            continue    # no strength: no dose (coalesce -> 0)
        ov = overlap_days(c.adate, c.expiredt, w_from, w_to)
        # Same shape as the shipped expression:
        #   strength * rxamt * CASE WHEN rxsup > 0 THEN ov / rxsup ELSE 1 END
        # i.e. (strength*rxamt) * (ov/rxsup) -- the CASE wraps the overlap
        # fraction only, so the operator order matters for inexact-binary
        # quotients.
        frac = ov / c.rxsup if c.rxsup > 0 else 1.0
        cumdose += strength * c.rxamt * frac
        if c.rxsup > 0:
            cfdd_sum += strength * c.rxamt / c.rxsup
    if spec.mincumdose is not None and cumdose < spec.mincumdose:
        return False
    if matched and (spec.minafdd is not None or spec.maxafdd is not None):
        afdd = round1_half_away(cfdd_sum / len(matched))
        if spec.minafdd is not None and afdd < spec.minafdd:
            return False
        if spec.maxafdd is not None and afdd > spec.maxafdd:
            return False
    return True


@dataclass(frozen=True)
class InclusionVerdict:
    """What 52_inclusion.sql decides for one episode."""

    excluded: bool
    first_failed_cond: int | None
    failed_criteria: tuple[str, ...]


def inclusion_verdict(
    episode: Episode,
    claims: Sequence[IncClaim],
    rules: Mapping[RuleKey, RuleSpec],
    strengths: Mapping[str, float],
) -> InclusionVerdict:
    """AND across conditions, AND across subconditions, pooled OR
    within a subcondition, with sub-exclusions inverting their sense.

    INC passes when met; EXC fails when met. `first_failed_cond` is the
    smallest failing cond number across both senses, matching the
    stage's min() over its WHERE-filtered rows.
    """
    pat_claims = [c for c in claims if c.patid == episode.patid]
    cond_met: dict[tuple[str, int], bool] = {}
    for key, spec in rules.items():
        if key.criteria not in ("INC", "EXC"):
            continue
        window = _window_for(spec, episode.indexdt, episode.episodeenddt,
                             event_anchored=False)
        met = _subcond_met(spec, pat_claims, strengths, window)
        satisfied = met if spec.subcond_inclusion else not met
        ck = (key.criteria, key.cond)
        cond_met[ck] = satisfied and cond_met.get(ck, True)

    failed_conds: list[int] = []
    failed_criteria: set[str] = set()
    for (criteria, cond), ok in sorted(cond_met.items()):
        bad = (criteria == "INC" and not ok) or (criteria == "EXC" and ok)
        if bad:
            failed_conds.append(cond)
            failed_criteria.add(criteria)
    if not failed_conds:
        return InclusionVerdict(False, None, ())
    return InclusionVerdict(True, min(failed_conds), tuple(sorted(failed_criteria)))


def event_exclusion(
    events: Sequence[date],
    claims: Sequence[IncClaim],
    rules: Mapping[RuleKey, RuleSpec],
    strengths: Mapping[str, float],
) -> set[date]:
    """The IEV/EEV-excluded event dates among `events`.

    Evaluated per DISTINCT event date (SAS keeps one row per
    patid/eventdt); a failing IEV drops that event only, never the
    episode.
    """
    out: set[date] = set()
    for eventdt in sorted(set(events)):
        cond_met: dict[tuple[str, int], bool] = {}
        for key, spec in rules.items():
            if key.criteria not in ("IEV", "EEV"):
                continue
            window = _window_for(spec, eventdt, eventdt, event_anchored=True)
            met = _subcond_met(spec, claims, strengths, window)
            satisfied = met if spec.subcond_inclusion else not met
            ck = (key.criteria, key.cond)
            cond_met[ck] = satisfied and cond_met.get(ck, True)
        failed = [
            (crit, cond)
            for (crit, cond), ok in sorted(cond_met.items())
            if (crit == "IEV" and not ok) or (crit == "EEV" and ok)
        ]
        if failed:
            out.add(eventdt)
    return out


# ---------------------------------------------------------------------
# Dose censoring (55_dose_censor.sql).
# ---------------------------------------------------------------------


def dose_censor_dates(
    episodes: Sequence[Episode],
    claims_by_key: Mapping[tuple[str, int], Sequence[DoseClaim]],
    *,
    max_cum_dose: float,
    cum_dose_per: int,
) -> dict[tuple[str, int, date], date]:
    """The max-cumulative-dose censor date per episode.

    Claims enter the running total when
    indexdt - cum_dose_per <= adate <= episodeenddt; the running sum
    follows (adate, expiredt) order; the censor date is the EARLIEST
    expiry among claims where the rounded running total first exceeds
    the cap (SAS's min() over the exceeding rows, reproduced).
    """
    out: dict[tuple[str, int, date], date] = {}
    for ep in episodes:
        rows = claims_by_key.get((ep.cohortgrp, ep.patid), ())
        window = [
            r for r in rows
            if ep.indexdt - timedelta(days=cum_dose_per) <= r.adate
            <= ep.episodeenddt
        ]
        window.sort(key=lambda r: (r.adate, r.expiredt, r.rxamt))
        total = 0.0
        exceeding: list[date] = []
        for r in window:
            total += float(r.cumdose or 0.0)
            if round_half_away(total) > max_cum_dose:
                exceeding.append(r.expiredt)
        if exceeding:
            out[(ep.cohortgrp, ep.patid, ep.indexdt)] = min(exceeding)
    return out


# ---------------------------------------------------------------------
# Follow-up, blackout, and event attribution (60_followup.sql).
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class FollowUpConfig:
    fup_wash_per: int | None     # None: any prior event disqualifies
    blackout_per: int
    at_risk_start: int
    end_date: date
    censor_date: date


@dataclass(frozen=True)
class EpisodeOutcome:
    cohortgrp: str
    patid: int
    indexdt: date
    kept: bool                   # survived hoi incidence + blackout
    feventdt: date | None
    numevents: int
    followuptime: int
    episodelength: int
    episodeenddt_censor: date
    timetocensor: int
    person_days: int
    exit_reason: str


def _hoi_incidence_met(
    indexdt: date,
    prior_events: Sequence[date],
    fup_wash_per: int | None,
) -> bool:
    """Follow-up washout: an outcome (or IOC) claim strictly before the
    index date disqualifies the episode when it sits inside
    [indexdt - fup_wash, indexdt - 1]. fup_wash <= 0 disables the
    washout entirely; None means ANY prior event disqualifies."""
    if fup_wash_per is None:
        return not prior_events
    if fup_wash_per <= 0:
        return True
    horizon = indexdt - timedelta(days=fup_wash_per)
    return all(not (indexdt > e >= horizon) for e in prior_events)


def _hoi_blackout_met(
    indexdt: date, events: Sequence[date], blackout_per: int
) -> bool:
    """Blackout: any outcome claim in [indexdt, indexdt + blackout - 1]
    drops the episode (day 1 is the index day itself)."""
    if blackout_per <= 0:
        return True
    hi = indexdt + timedelta(days=blackout_per - 1)
    return all(not (indexdt <= e <= hi) for e in events)


def episode_outcome(
    ep: Episode,
    *,
    enr_end: date,
    deathdt: date | None,
    atriskindexdt: date,
    events: Sequence[date],
    ioc_dates: Sequence[date],
    cfg: FollowUpConfig,
    ie_rules: Mapping[RuleKey, RuleSpec] | None,
    inc_claims: Sequence[IncClaim],
    strengths: Mapping[str, float],
) -> EpisodeOutcome:
    """Model one episode through 60_followup.sql.

    `events` are the episode's outcome claim dates (already the
    event_claims set for the cohort). IEV/EEV rules drop individual
    event dates; the first surviving event anchors person-time.
    """
    prior = [d for d in list(events) + list(ioc_dates) if d < ep.indexdt]
    inc_met = _hoi_incidence_met(ep.indexdt, prior, cfg.fup_wash_per)
    black_met = _hoi_blackout_met(ep.indexdt, events, cfg.blackout_per)
    kept = inc_met and black_met

    candidates = [d for d in events if atriskindexdt <= d <= ep.episodeenddt]
    excluded: set[date] = set()
    if ie_rules:
        excluded = event_exclusion(candidates, inc_claims, ie_rules, strengths)
    surviving = [d for d in candidates if d not in excluded]
    # numevents counts claim rows, not distinct days; callers pass one
    # entry per claim row in `events`.
    num = sum(1 for d in surviving)
    feventdt = min(surviving) if surviving else None

    fu_last = min(ep.episodeenddt, enr_end,
                  feventdt if feventdt else date.max)
    followuptime = max(
        0, (fu_last - ep.indexdt).days - cfg.blackout_per
        - cfg.at_risk_start + 1)
    episodelength = (min(ep.episodeenddt, enr_end) - ep.indexdt).days + 1
    eec = min(
        enr_end,
        deathdt if deathdt else date.max,
        cfg.censor_date,
        cfg.end_date,
    )
    exit_reason = "exposure_end"
    if feventdt is not None:
        exit_reason = "event"
    elif deathdt is not None and ep.episodeenddt == deathdt:
        exit_reason = "death"
    elif ep.episodeenddt == enr_end:
        exit_reason = "disenrollment"
    elif ep.episodeenddt == cfg.censor_date:
        exit_reason = "study_end"
    person_days = (min(feventdt if feventdt else ep.episodeenddt,
                       ep.episodeenddt) - atriskindexdt).days + 1
    return EpisodeOutcome(
        cohortgrp=ep.cohortgrp, patid=ep.patid, indexdt=ep.indexdt,
        kept=kept, feventdt=feventdt, numevents=num,
        followuptime=followuptime, episodelength=episodelength,
        episodeenddt_censor=eec, timetocensor=(eec - ep.indexdt).days + 1,
        person_days=person_days, exit_reason=exit_reason,
    )


def episodes_with_censor_applied(
    episodes: Sequence[Episode],
    censor_dates: Mapping[tuple[str, int, date], date],
) -> tuple[Episode, ...]:
    """55_dose_censor's ptsmasterlist rebuild: episodeenddt truncated
    to the censor date when one exists (and episode_days with it)."""
    out: list[Episode] = []
    for ep in episodes:
        cens = censor_dates.get((ep.cohortgrp, ep.patid, ep.indexdt))
        end = min(ep.episodeenddt, cens) if cens else ep.episodeenddt
        out.append(Episode(ep.cohortgrp, ep.patid, ep.indexdt, end))
    return tuple(out)


def flagged_episode_keys(
    episodes: Sequence[Episode],
    *,
    events_by_key: Mapping[tuple[str, int], Sequence[date]],
    ioc_by_key: Mapping[tuple[str, int], Sequence[date]],
    fup_wash_per: int | None,
    blackout_per: int,
) -> set[tuple[str, int, date]]:
    """Episode keys passing both hoi flags (episode_hoi, evaluated on
    the level-16 survivors)."""
    out: set[tuple[str, int, date]] = set()
    for ep in episodes:
        ev = events_by_key.get((ep.cohortgrp, ep.patid), ())
        ioc = ioc_by_key.get((ep.cohortgrp, ep.patid), ())
        prior = [d for d in list(ev) + list(ioc) if d < ep.indexdt]
        if (_hoi_incidence_met(ep.indexdt, prior, fup_wash_per)
                and _hoi_blackout_met(ep.indexdt, ev, blackout_per)):
            out.add((ep.cohortgrp, ep.patid, ep.indexdt))
    return out


def claims_in_window(claims: Iterable[DoseClaim], lo: date, hi: date) -> list[DoseClaim]:
    """Claims whose fill date lies in the CLOSED window [lo, hi]."""
    return [c for c in claims if lo <= c.adate <= hi]
