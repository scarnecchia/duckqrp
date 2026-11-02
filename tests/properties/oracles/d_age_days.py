# pattern: Functional Core
"""Bounded year-age oracle, independent of interval threshold SQL.

SAS ms_cidadenom.sas:1046 uses INTNX with SAMEDAY alignment. An anniversary
on a missing day uses the month's last day (February 29 -> February 28).
Finite-top/gapped bands have ambiguous SAS denominator semantics: this core
implements literal bounds, but integration parity claims exclude them.
"""
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from qrp.config import AgeStrata


@dataclass(frozen=True)
class MemberDay:
    patid: int
    birth: date
    day: date


@dataclass(frozen=True)
class BandCount:
    label: str
    ordinal: int
    members: int
    days: int


def attained_years(birth: date, day: date) -> int:
    if day < birth:
        raise ValueError("day precedes birth")
    anniversary = (birth.month, min(birth.day, monthrange(day.year, birth.month)[1]))
    return day.year - birth.year - ((day.month, day.day) < anniversary)


def count_age_days(days: tuple[MemberDay, ...], bands: AgeStrata) -> tuple[BandCount, ...]:
    if any(b.unit != "years" or b.lo < 0 or b.hi < b.lo for b in bands.strata):
        raise ValueError("oracle requires valid year bands")
    members: dict[int, set[int]] = defaultdict(set)
    counts: dict[int, int] = defaultdict(int)
    for item in days:
        age = attained_years(item.birth, item.day)
        for band in bands.strata:
            if band.lo <= age <= band.hi:
                members[band.ordinal].add(item.patid)
                counts[band.ordinal] += 1
    return tuple(BandCount(b.label, b.ordinal, len(members[b.ordinal]), counts[b.ordinal])
                 for b in bands.strata if counts[b.ordinal])
