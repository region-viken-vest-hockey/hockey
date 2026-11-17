"""Validation and normalization of source-explicit booking intervals."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping

from tournament_scheduler.booking.interval_facts import _parse_hhmm


def validate_stated_interval(
    start: str | None,
    end: str | None,
    date: str | None = None,
) -> dict[str, str]:
    """Validate and normalize an optional source-stated booking window.

    A source may state only a start: the end then stays proposal-derived and is
    never recorded as a stated fact. A stated end needs a start, and rejects
    malformed, zero-length and reversed windows. Overnight windows are
    deliberately not supported: ``end`` must be strictly after ``start`` on the
    same local date.
    """

    if not start and not end and not date:
        return {}
    if not start:
        raise ValueError("A stated source interval requires --stated-start")
    if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date)):
        raise ValueError(f"Invalid stated date: {date!r}; expected YYYY-MM-DD")
    if date:
        try:
            datetime.strptime(str(date), "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"Invalid stated date: {date!r}; expected YYYY-MM-DD") from exc
    start_minutes = _parse_hhmm(start)
    if start_minutes is None:
        raise ValueError(f"Invalid stated start time: {start!r}; expected HH:MM")
    row = {"start": str(start)}
    if end:
        end_minutes = _parse_hhmm(end)
        if end_minutes is None:
            raise ValueError(f"Invalid stated end time: {end!r}; expected HH:MM")
        if end_minutes <= start_minutes:
            raise ValueError(
                f"Invalid stated interval {start}-{end}: end must be after start on the same day; "
                "overnight intervals are not supported"
            )
        row["end"] = str(end)
    if date:
        row["date"] = str(date)
    return row


def _normalized_stated_interval(stated_interval: Mapping[str, Any] | None) -> dict[str, str]:
    """Normalize an optional source-stated interval for durable comparison."""

    if not isinstance(stated_interval, Mapping):
        return {}
    start = str(stated_interval.get("start") or "")
    end = str(stated_interval.get("end") or "")
    date = str(stated_interval.get("date") or "")
    duration = 0
    if start and end:
        start_minutes = _parse_hhmm(start)
        end_minutes = _parse_hhmm(end)
        if start_minutes is not None and end_minutes is not None and end_minutes >= start_minutes:
            duration = end_minutes - start_minutes
    # A start-only statement records no end or duration: none was stated.
    return {"date": date, "start": start, **({"end": end, "duration_minutes": str(duration)} if end else {})}
