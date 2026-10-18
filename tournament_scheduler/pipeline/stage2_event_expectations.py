"""Event-count expectations and suspicious-shape checks for Stage 2 sources.

Split out of :mod:`stage2_scraping` so that module stays within its file-length
budget. Pure helpers: they inspect source results and the Stage 1 config and
return warnings; they never change source trust on their own.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .scraper_constants import _ICAL_SOURCE_TYPES


def _active_age_groups(config: dict[str, Any]) -> list[str]:
    """Return configured age groups used to size Stage 2 event-count expectations."""
    configured = config.get("age_groups")
    if isinstance(configured, list):
        groups = [str(group).strip() for group in configured if str(group).strip()]
        if groups:
            return sorted(set(groups))

    teams = config.get("teams") or []
    if isinstance(teams, list):
        groups = [
            str(team.get("age_group", "")).strip()
            for team in teams
            if isinstance(team, dict) and str(team.get("age_group", "")).strip()
        ]
        if groups:
            return sorted(set(groups))

    return []


def _count_weekend_days(start_date: datetime, end_date: datetime) -> int:
    """Count Saturdays and Sundays in the inclusive scrape date range."""
    current: date = start_date.date()
    end: date = end_date.date()
    count = 0
    while current <= end:
        if current.weekday() >= 5:
            count += 1
        current += timedelta(days=1)
    return count


def _expected_min_events(config: dict[str, Any], start_date: datetime, end_date: datetime) -> int:
    """Estimate a lower-bound event count for each configured arena source.

    This is intentionally a coarse warning heuristic, not a hard validation rule.
    Across a normal RVV season, an active arena calendar should usually expose at
    least roughly one relevant booking for every four weekend days. The active
    age-group count scales that expectation down for tiny test/special-purpose
    configurations while keeping full RVV workbooks near the historical ~16 event
    lower bound over a September-April season.
    """
    age_group_count = len(_active_age_groups(config))
    if age_group_count == 0:
        return 0

    weekend_days = _count_weekend_days(start_date, end_date)
    if weekend_days == 0:
        return 0

    age_group_factor = min(1.0, max(0.25, age_group_count / 4.0))
    return max(1, round((weekend_days / 4.0) * age_group_factor))


def _apply_event_count_expectations(
    source_results: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    start_date: datetime,
    end_date: datetime,
) -> list[dict[str, Any]]:
    """Attach per-source expectation metadata and return source-shape warnings."""
    expected_min = _expected_min_events(config, start_date, end_date)
    age_groups = _active_age_groups(config)
    weekend_days = _count_weekend_days(start_date, end_date)
    warnings: list[dict[str, Any]] = []

    for source in source_results:
        actual = int(source.get("event_count") or 0)
        status = "not_applicable" if expected_min <= 0 or source.get("skipped") else "ok"
        message = ""
        if status == "ok" and actual < expected_min and not source.get("blocked"):
            status = "low"
            message = (
                f"{source.get('name', 'ukjent kilde')}: {actual} hendelser funnet, "
                f"forventet minst ca. {expected_min} for perioden."
            )
        elif status == "ok" and not source.get("blocked"):
            message = _suspicious_event_shape_message(source, weekend_days=weekend_days)
            if message:
                status = "suspicious"

        if message:
            warnings.append({
                "name": source.get("name", "ukjent kilde"),
                "event_count": actual,
                "expected_min_events": expected_min,
                "status": status,
                "message": message,
            })

        source["event_expectation"] = {
            "status": status,
            "event_count": actual,
            "expected_min_events": expected_min,
            "age_group_count": len(age_groups),
            "weekend_days": weekend_days,
            "basis": "weekend_days/4 scaled by active age-group count plus source-shape sanity checks",
        }
        if message:
            source["event_expectation"]["message"] = message

    return warnings


def _suspicious_event_shape_message(source: dict[str, Any], *, weekend_days: int) -> str:
    """Return a warning for event shapes that look like scraper artifacts."""
    source_type = str(source.get("type", "")).lower()
    if source_type in _ICAL_SOURCE_TYPES:
        return ""

    events = source.get("events") or []
    actual = int(source.get("event_count") or len(events))
    if actual < 20 or not isinstance(events, list):
        return ""

    dates: list[date] = []
    midnight_count = 0
    generic_booked_count = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        raw_datetime = str(event.get("datetime") or "")
        raw_date = str(event.get("date") or "")
        parsed_dt: datetime | None = None
        try:
            if "T" in raw_datetime:
                parsed_dt = datetime.fromisoformat(raw_datetime.replace("Z", "+00:00"))
            elif raw_date:
                parsed_dt = datetime.strptime(raw_date, "%d.%m.%Y")
        except ValueError:
            parsed_dt = None
        if parsed_dt:
            dates.append(parsed_dt.date())
            if parsed_dt.hour == 0 and parsed_dt.minute == 0:
                midnight_count += 1
        name = str(event.get("name") or "").strip().lower()
        if name in {"booket", "booking"}:
            generic_booked_count += 1

    if not dates:
        return ""

    distinct_dates = len(set(dates))
    first_day_count = sum(1 for parsed_date in dates if parsed_date.day == 1)
    weekend_count = sum(1 for parsed_date in dates if parsed_date.weekday() >= 5)
    generic_ratio = generic_booked_count / actual
    midnight_ratio = midnight_count / actual
    first_day_ratio = first_day_count / actual

    name = source.get("name", "ukjent kilde")
    if generic_ratio >= 0.9 and midnight_ratio >= 0.9:
        return (
            f"{name}: {actual} hendelser funnet, men nesten alle heter 'Booket' "
            "og ligger kl. 00:00; kalenderen ser ut til å mangle innloggede detaljer."
        )
    if distinct_dates <= 8 and first_day_ratio >= 0.8:
        return (
            f"{name}: {actual} hendelser funnet, men bare {distinct_dates} datoer "
            "og de fleste ligger på den 1. i måneden; dette ligner en datoparsingsfeil."
        )
    if weekend_days >= 20 and weekend_count < 4:
        return (
            f"{name}: {actual} hendelser funnet, men bare {weekend_count} helgehendelser; "
            "kontroller at full arena-/helgekalender ble lest."
        )
    return ""
