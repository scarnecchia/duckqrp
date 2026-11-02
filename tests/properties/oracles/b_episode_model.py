"""Independent model of episodes and the episode criteria / master list
(50_episodes.sql).

Chaining contract (non-point cohorts): stockpiled records chain into one
exposure episode while the blank days between a record and the latest
supply end so far stay within `episode_gap` (fixed days), within
`episode_gap * previous record's supply / 100` (type 'P'), or forever
(NULL gap: every record its own episode). A record also breaks when its
enrollment span differs from the previous record's — a disenrol/re-enrol
patient gets two episodes. The episode end extends by exp_ext_per.

Point cohorts skip chaining: one episode per index date, capped by
max_epis_dur (or left to run to enrollment end).

episode_criteria joins each POV1 index date to the episode STARTING on
that date (an index date that falls mid-chain starts no episode row),
censors the end (FUT none here; study censor date; enrollment end; death
when censoring; max episode duration), and evaluates SAS's five master
list flags. ptsmasterlist keeps rows where all five hold.

Derivation: sequential scans and per-episode min/max/sum. The production
SQL uses gap-and-islands windows; nothing here repeats them.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from qrp.config import CohortConfig, StudyConfig
from qrp.pipeline import _enr_cfg_id

from ..synth import SyntheticDataset
from .b_enrollment_model import containing_episodes, enrollment_episodes

EPISODES_COLS = ("cohortgrp", "patid", "episode", "episodestartdt",
                 "episodeenddt", "episode_rxsup")

CRITERIA_COLS = (
    "cohortgrp", "patid", "indexdt", "episode", "episodestartdt",
    "episodeenddt", "origepisenddt", "trunkdt", "atriskindexdt",
    "dataavail_dt", "episode_days", "age", "agegroup", "agegroupnum",
    "sex", "race", "hispanic", "zip", "zip_date", "birth_date", "deathdt",
    "enr_start", "enr_end", "rxsup", "rxamt", "numdispensing",
    "episode_rxsup", "min_enr_days_met", "min_enr_aft_met",
    "min_day_sup_met", "min_epi_dur_met", "post_blackout_met",
)

MSTR_COLS = CRITERIA_COLS[: -5]

FAR_FUTURE = date(9999, 12, 31)


@dataclass(frozen=True)
class _ChainRow:
    adate: date
    expiredt: date
    rxsup: int
    claim_enr_start: date | None


def model_episodes(dataset: SyntheticDataset, study: StudyConfig,
                   stockpiled: list[tuple],
                   pov1: list[tuple]) -> list[tuple]:
    """Model the `episodes` table (EPISODES_COLS order): chain episodes
    for non-point cohorts, one per index date for point cohorts."""
    enrollment = enrollment_episodes(dataset, study)
    out: list[tuple] = []
    for cohort in study.cohorts:
        cfg = _enr_cfg_id(cohort)
        if cohort.point:
            by_patid: dict[int, list[tuple]] = {}
            for p in pov1:
                if p[0] == cohort.cohortgrp:
                    by_patid.setdefault(p[1], []).append(p)
            for patid, pts in by_patid.items():
                pts.sort(key=lambda p: (p[2], p[3], p[4]))
                for rn, p in enumerate(pts, start=1):
                    end = (p[2] + timedelta(days=cohort.max_epis_dur - 1)
                           if cohort.max_epis_dur >= 1
                           else p[8])  # p[8]=enr_end
                    out.append((cohort.cohortgrp, patid, rn, p[2], end, p[4]))
            continue
        rows: dict[int, list[_ChainRow]] = {}
        for row in stockpiled:
            if row[0] != cohort.cohortgrp:
                continue
            patid = row[2]
            # Parity (B2 ruling): `located` LEFT JOINs enrollment_spans
            # per containing span, so a record inside several episodes
            # chains once PER span (each copy carrying that span's
            # start; the span-change break then separates them). A
            # record outside every span keeps NULL (LEFT JOIN) — not
            # reachable for reshave-surviving RX rows, kept for parity.
            hits = containing_episodes(enrollment.get((cfg, patid), ()),
                                       row[3])
            for ep in hits or (None,):
                rows.setdefault(patid, []).append(_ChainRow(
                    adate=row[3], expiredt=row[4], rxsup=row[6],
                    claim_enr_start=ep.enr_start if ep else None))
        for patid, recs in rows.items():
            recs.sort(key=lambda r: (r.adate, r.expiredt, r.rxsup))
            episode_no = 0
            prev: _ChainRow | None = None
            prev_runout: date | None = None
            members: list[_ChainRow] = []
            for rec in recs:
                gap_days = (
                    None if prev_runout is None
                    else (rec.adate - prev_runout).days - 1)
                brk = False
                gap = cohort.episode_gap
                if prev is None:
                    brk = True
                elif gap is None:
                    brk = True
                elif gap_days is not None:
                    if cohort.episode_gap_type == "P":
                        brk = gap_days > (gap * prev.rxsup / 100.0)
                    else:
                        brk = gap_days > gap
                if not brk and prev is not None and (
                        rec.claim_enr_start != prev.claim_enr_start):
                    brk = True
                if brk:
                    if members:
                        out.append(_emit_episode(cohort.cohortgrp, patid,
                                                 episode_no, members,
                                                 cohort.exp_ext_per))
                    episode_no += 1
                    members = []
                members.append(rec)
                prev = rec
                prev_runout = (rec.expiredt if prev_runout is None
                               or rec.expiredt > prev_runout else prev_runout)
            if members:
                out.append(_emit_episode(cohort.cohortgrp, patid,
                                         episode_no, members,
                                         cohort.exp_ext_per))
    out.sort(key=lambda r: (r[0], r[1], r[2]))
    return out


def _emit_episode(grp: str, patid: int, episode_no: int,
                  members: list[_ChainRow], ext_per: int) -> tuple:
    start = min(r.adate for r in members)
    end = max(r.expiredt for r in members)
    if ext_per:
        end = end + timedelta(days=ext_per)
    return (grp, patid, episode_no, start, end,
            float(sum(r.rxsup for r in members)))


def model_episode_criteria(dataset: SyntheticDataset, study: StudyConfig,
                           episodes: list[tuple],
                           pov1: list[tuple]) -> tuple[list[tuple], list[tuple]]:
    """Model `episode_criteria` and `ptsmasterlist`. No FUT truncation:
    this package's studies define no truncation codes."""
    for cohort in study.cohorts:
        if cohort.trunc_codes:
            raise AssertionError("oracle covers studies without FUT codes")
    episode_by_start: dict[tuple[str, int, date], list[tuple]] = {}
    for e in episodes:
        episode_by_start.setdefault((e[0], e[1], e[3]), []).append(e)
    criteria: list[tuple] = []
    for cohort in study.cohorts:
        for p in pov1:
            if p[0] != cohort.cohortgrp:
                continue
            # POV1 rows and episodes BOTH fan out per containing span,
            # so several episodes can start on one index date; the SQL
            # joins pov1 x episodes on episodestartdt = indexdt (a
            # cartesian over that group, each pov1 row with each
            # episode).
            for ep in episode_by_start.get((cohort.cohortgrp, p[1], p[2]), []):
                death = p[18]
                raw_end: date = ep[4]
                death_cap = (death if cohort.censor_death and death is not None
                             else FAR_FUTURE)
                dur_cap = (p[2] + timedelta(days=cohort.max_epis_dur - 1)
                           if cohort.max_epis_dur > 0 else FAR_FUTURE)
                end = min(raw_end, study.effective_censor_date, p[8],
                          death_cap, dur_cap)
                dataavail = min(p[8], study.effective_censor_date, death_cap)
                if p[2] > end or not (
                        study.start_date <= p[2] <= study.end_date):
                    continue
                flags = _master_flags(cohort, p, ep, end, dataavail)
                atrisk = (p[2] + timedelta(days=cohort.at_risk_start)
                          if cohort.at_risk_start > 0 else p[2])
                row = (
                    cohort.cohortgrp, p[1], p[2], ep[2], p[2], end, raw_end,
                    None, atrisk, dataavail, (end - p[2]).days + 1,
                    p[15], p[16], p[17], p[10], p[11], p[12], p[13], p[14],
                    p[9], death, p[7], p[8], p[4], p[5], p[6], ep[5],
                ) + flags
                criteria.append(row)
    master = [r for r in criteria if all(r[-5:])]
    criteria.sort(key=lambda r: (r[0], r[1], r[2]))
    master.sort(key=lambda r: (r[0], r[1], r[2]))
    return criteria, master


def _master_flags(cohort: CohortConfig, p: tuple, ep: tuple,
                  end: date, dataavail: date) -> tuple[bool, bool, bool, bool, bool]:
    indexdt = p[2]
    enr_start, enr_end = p[7], p[8]
    min_enr_days = enr_start <= indexdt - timedelta(days=cohort.enr_days)
    dur_ok = cohort.min_epis_dur <= 0 or (
        enr_start <= indexdt
        and indexdt + timedelta(days=cohort.min_epis_dur - 1) <= enr_end)
    min_enr_aft = (
        dataavail >= indexdt + timedelta(days=max(0, cohort.req_days_aft_ind))
        and dataavail >= end + timedelta(days=max(0, cohort.req_days_aft_epi))
        and dur_ok)
    episode_rxsup = ep[5]
    min_day_sup = (cohort.min_days_supp <= 0
                   or episode_rxsup >= cohort.min_days_supp)
    min_epi_dur = (end - indexdt).days + 1 >= cohort.min_epis_dur
    blackout = cohort.blackout_per <= 0 or (
        (end - indexdt).days - cohort.blackout_per >= 0)
    return min_enr_days, min_enr_aft, min_day_sup, min_epi_dur, blackout


__all__ = [
    "CRITERIA_COLS",
    "EPISODES_COLS",
    "MSTR_COLS",
    "model_episode_criteria",
    "model_episodes",
]
