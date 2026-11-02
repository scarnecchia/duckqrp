"""Independent day-model of enrollment episodes (20_enrollment.sql).

Contract (docs/SAS_PARITY.md "Enrollment spans" + 20_enrollment.sql header):
spans separated by at most `enrol_gap` blank days merge into one episode; a
span also ruptures when the coverage flags the study cares about change; a
patient with any non-chart span overlapping the study window is dropped
entirely when `chart_required`; spans whose coverage fails the study's mode
never qualify at all.

Derivation: an explicit sorted interval scan with a running episode
accumulator (compare each span against the episode built so far). This is
NOT the SQL's formulation, which breaks each span against the immediately
PREVIOUS SPAN via `lag()` and numbers islands with a running sum. The two
derivations agree whenever every raw span either merges with its ordered
predecessor or starts beyond every earlier span's end; they diverge on
overlapping raw spans whose inner boundary ruptures mid-episode (see
investigation/packages/B.md, lead B1).

Pure functions only: dataset rows in, episode intervals out. No SQL, no
window formulas, no IO.
"""

from dataclasses import dataclass
from datetime import date

from qrp.config import StudyConfig
from qrp.pipeline import _enr_cfg_id

from ..synth import SyntheticDataset

# ---------------------------------------------------------------------
# Normalization (mirrors cdm_enrollment in 10_normalize.sql: upper-case
# flags, NULL chart -> 'N', drop rows with NULL or inverted dates).
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class NormSpan:
    patid: int
    enr_start: date
    enr_end: date
    medcov: str
    drugcov: str
    chart: str


def normalize_spans(dataset: SyntheticDataset) -> tuple[NormSpan, ...]:
    out: list[NormSpan] = []
    for s in dataset.enrollment:
        if s.enr_start is None or s.enr_end is None or s.enr_end < s.enr_start:
            continue
        out.append(NormSpan(
            patid=s.patid,
            enr_start=s.enr_start,
            enr_end=s.enr_end,
            medcov=(s.medcov or "").upper(),
            drugcov=(s.drugcov or "").upper(),
            chart=(s.chart or "N").upper(),
        ))
    return tuple(out)


def has_coverage(medcov: str, drugcov: str, coverage: str) -> bool:
    """%ms_episoderec2 coverage predicate, stated from its SAS intent:
    MD needs both, M medical only, D drug only."""
    m, d, c = medcov.upper(), drugcov.upper(), coverage.upper()
    if c == "MD":
        return m in ("Y", "A") and d == "Y"
    if c == "M":
        return m in ("Y", "A")
    if c == "D":
        return d == "Y"
    return True


def _flag_change(prev: NormSpan, cur: NormSpan, coverage: str) -> bool:
    c = coverage.upper()
    if c == "MD":
        return prev.medcov != cur.medcov or prev.drugcov != cur.drugcov
    if c == "M":
        return prev.medcov != cur.medcov
    if c == "D":
        return prev.drugcov != cur.drugcov
    return False


@dataclass(frozen=True)
class Episode:
    enr_start: date
    enr_end: date


def enrollment_episodes(
    dataset: SyntheticDataset, study: StudyConfig,
) -> dict[tuple[str, int], tuple[Episode, ...]]:
    """Model output of 20_enrollment.sql: per (enr_cfg_id, patid), the
    merged continuous enrollment episodes."""
    spans = normalize_spans(dataset)
    out: dict[tuple[str, int], tuple[Episode, ...]] = {}
    for cohort in study.cohorts:
        cfg = _enr_cfg_id(cohort)
        # chart filter: one disqualifying span removes the whole patient
        # for this configuration.
        dropped: set[int] = set()
        if cohort.chart_required:
            for s in spans:
                overlaps_window = (
                    s.enr_start <= study.end_date
                    and s.enr_end >= study.start_date)
                if s.chart != "Y" and overlaps_window:
                    dropped.add(s.patid)
        per_pat: dict[int, list[NormSpan]] = {}
        for s in spans:
            if s.patid in dropped:
                continue
            if not has_coverage(s.medcov, s.drugcov, cohort.coverage):
                continue
            per_pat.setdefault(s.patid, []).append(s)
        for patid, group in per_pat.items():
            group = sorted(group, key=lambda s: (s.enr_start, s.enr_end))
            episodes: list[Episode] = []
            cur: NormSpan | None = None
            cur_end: date | None = None
            for s in group:
                if cur is None or cur_end is None:
                    cur, cur_end = s, s.enr_end
                    continue
                blank_gap = (s.enr_start - cur_end).days - 1
                if blank_gap > cohort.enrol_gap or _flag_change(
                        cur, s, cohort.coverage):
                    episodes.append(Episode(cur.enr_start, cur_end))
                    cur, cur_end = s, s.enr_end
                else:
                    # merge: the episode extends to the farthest end seen
                    cur_end = max(cur_end, s.enr_end)
            if cur is not None and cur_end is not None:
                episodes.append(Episode(cur.enr_start, cur_end))
            out[(cfg, patid)] = tuple(episodes)
    return out


def containing_episode(
    episodes: tuple[Episode, ...], day: date,
) -> Episode | None:
    """The episode covering `day`, or None. The accumulator model cannot
    produce overlapping episodes, so at most one can contain a day."""
    hits = [e for e in episodes if e.enr_start <= day <= e.enr_end]
    if len(hits) > 1:
        raise AssertionError(
            "day model produced overlapping episodes; the model is wrong")
    return hits[0] if hits else None


__all__ = [
    "Episode",
    "NormSpan",
    "containing_episode",
    "enrollment_episodes",
    "has_coverage",
    "normalize_spans",
]
