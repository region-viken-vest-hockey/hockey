"""Reusable bounded placement repair that preserves hosting responsibility.

Hosting responsibility and automatic placement are separate concerns (see
``docs/system-architecture.md``). When the responsible host is represented and
still owes hosting responsibility, a missing slot on the initially selected
date must not immediately become ``MANUAL PLACEMENT REQUIRED`` while another
legal date/slot exists for that *same* host.

This module owns that bounded search as a small, deterministic,
planner-independent capability: callers provide the candidate dates, a
per-date slot search and the legality predicate. It never transfers hosting
responsibility to another club -- only other legal dates/slots for the same
responsible host are considered.

The result records exactly which dates were searched so placement evidence can
distinguish options actually searched from candidates merely known to exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, List, Optional, Sequence, Tuple


Slot = Tuple[str, str, str]


@dataclass
class SameHostDateSearch:
    """Outcome of a bounded same-responsible-host date search."""

    chosen_date: Optional[date] = None
    chosen_slot: Optional[Slot] = None
    # Candidate dates actually searched, in search order. This is the truthful
    # evidence set: dates filtered out by the legality predicate are not
    # claimed as searched.
    dates_checked: List[date] = field(default_factory=list)
    # True when the bounded candidate-date set was walked to the end without a
    # verified slot. This is a bounded-set statement, not an unbounded or
    # exhaustive-search claim.
    exhausted: bool = False

    @property
    def repaired(self) -> bool:
        return self.chosen_slot is not None


def find_same_host_date_placement(
    *,
    current_date: date,
    candidate_dates: Sequence[date],
    slot_search: Callable[[date], Optional[Slot]],
    is_acceptable_date: Callable[[date], bool],
) -> SameHostDateSearch:
    """Search other dates for a verified slot belonging to the responsible host.

    Parameters
    ----------
    current_date:
        The date that failed. Never searched again.
    candidate_dates:
        Bounded, ordered set of dates to consider. Callers should order these
        deterministically (for example nearest-date-first).
    slot_search:
        Returns ``(host, start, end)`` for a verified slot on the given date,
        or ``None`` when no legal/free slot exists for the responsible host.
    is_acceptable_date:
        Predicate that rejects a date for reason of date-skeleton occupancy,
        season-half boundary, same-day participant reuse or a fresh
        overlapping-age-group collision. Rejected dates are not searched and
        therefore not recorded in ``dates_checked``.
    """
    checked: List[date] = []
    for candidate_date in candidate_dates:
        if candidate_date == current_date:
            continue
        if not is_acceptable_date(candidate_date):
            continue
        checked.append(candidate_date)
        slot = slot_search(candidate_date)
        if slot is not None:
            return SameHostDateSearch(chosen_date=candidate_date, chosen_slot=slot, dates_checked=checked, exhausted=False)
    return SameHostDateSearch(dates_checked=checked, exhausted=True)
