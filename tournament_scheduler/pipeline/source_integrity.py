"""Deterministic source-coverage/integrity verdict for calendar evidence.

Stage 2 scraping can succeed *and still* be untrustworthy evidence for a
negative conclusion. A scrape that silently stopped navigating a week/month
calendar early, returned a suspiciously sparse or duplicate-heavy event set,
or tripped one of the scraper-quality fingerprints must not be read as "this
club's calendar is fully known and had nothing on that day".

This module is the one canonical owner of that verdict. It is a pure,
planner-independent function over a Stage 2 source result (plus the previous
scrape's event count when available) and returns a stable, machine-readable
record. Nothing here scrapes, mutates or plans; the caller decides how to fail
closed -- the canonical consumer is
:func:`tournament_scheduler.pipeline.stage2_scraping` (which folds it into the
planning problem's ``club_calendar_status``) and
:mod:`tournament_scheduler.pipeline.source_health` (which reports it to the
operator).

The verdict deliberately avoids a *hard* event-count threshold: a legitimate
calendar edit can legitimately change counts. It reports before/after counts
and per-source shape reasons so an operator can review, marks the source
``suspicious``/``partial``/``failed``, and never treats an unproven source as
trustworthy for a negative occupancy claim.
"""

from __future__ import annotations

import collections
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from .fingerprints import stable_payload_sha256

SOURCE_INTEGRITY_SCHEMA_VERSION = 1

# The source completed without any integrity signal.
INTEGRITY_COMPLETE = "complete"
# The source completed, but its shape looks like a scraper artifact or a
# suspicious regression versus the previous scrape.
INTEGRITY_SUSPICIOUS = "suspicious"
# The source itself reported incomplete navigation / a partial covered range /
# a swallowed exception while still returning events.
INTEGRITY_PARTIAL = "partial"
# The source was blocked, skipped or raised before returning evidence.
INTEGRITY_FAILED = "failed"

# Every non-complete status must fail closed for a negative occupancy claim.
_UNTRUSTED_STATUSES = frozenset(
    {INTEGRITY_SUSPICIOUS, INTEGRITY_PARTIAL, INTEGRITY_FAILED}
)

# A dropped fraction this large versus the previous successful scrape is worth
# a review flag (never a hard rejection). Kept distinct from the coarse
# minimum-count expectation, which does not catch a 671 -> 171 regression.
_COUNT_REGRESSION_MIN_PREVIOUS = 20
_COUNT_REGRESSION_RATIO = 0.5

# Fraction of events sharing the same date/time/name/location key. Multi-arena
# venues can legitimately repeat a title/time on two rinks, so location is part
# of the key.
_DUPLICATE_RATIO_WARNING_THRESHOLD = 0.3

# A scraper that fabricates a fallback start-time/duration instead of parsing
# it produces a distinctive fingerprint: almost every event shares one duration
# AND starts at literal midnight. Both signals must be near-universal.
_MONOCULTURE_MIN_EVENTS = 20
_MONOCULTURE_RATIO_THRESHOLD = 0.9

# Fixed/deterministic allocation sources are uniform by design -- that is real
# domain knowledge, not a scraper defect.
_MONOCULTURE_EXEMPT_SOURCE_TYPES = {"fixed_allocation"}

# Source types whose single request already covers the requested window: a
# successful fetch is a structural coverage proof (an HTTP/feed error raises and
# is handled by Stage 2). Browser "next week/month" scrapers are not listed and
# only become coverage-proven when they report explicit coverage metadata.
_STRUCTURALLY_COVERED_TYPES = {"fixed_allocation", "ical", "google"}

_DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d")


class CoverageAnnotatedEvents(list):
    """A list of scraped events carrying an optional coverage record.

    Browser week/month scrapers return a plain list today, so the record is
    attached as an attribute (not a new return element) to stay backward
    compatible with every existing caller and test double. Stage 2 copies
    ``events.coverage`` onto the source result, where the integrity verdict
    reads it.
    """

    coverage: dict[str, Any] | None = None


def with_coverage(events: Iterable[Any], **coverage: Any) -> CoverageAnnotatedEvents:
    """Return *events* as a list carrying a coverage record."""

    annotated = CoverageAnnotatedEvents(events)
    annotated.coverage = dict(coverage)
    return annotated


def _parse_event_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _event_date(event: Mapping[str, Any]) -> date | None:
    parsed = _parse_event_date(event.get("date"))
    if parsed is not None:
        return parsed
    return _parse_event_date(event.get("datetime") or event.get("start"))


def observed_event_range(events: Iterable[Mapping[str, Any]]) -> tuple[str | None, str | None]:
    """Return the ``(min, max)`` event date as ISO strings, or ``(None, None)``."""
    dates = [parsed for event in events if (parsed := _event_date(event)) is not None]
    if not dates:
        return None, None
    return min(dates).isoformat(), max(dates).isoformat()


def duplicate_ratio(events: list[dict[str, Any]]) -> float:
    """Fraction of events sharing the same date/time/name/location key."""
    if not events:
        return 0.0
    keys = [
        (
            str(e.get("date") or e.get("start") or ""),
            str(e.get("datetime") or e.get("start") or ""),
            str(e.get("name") or e.get("title") or ""),
            str(e.get("location") or e.get("resource") or e.get("resourceId") or ""),
        )
        for e in events
        if isinstance(e, dict)
    ]
    if not keys:
        return 0.0
    unique = len(set(keys))
    return 1.0 - (unique / len(keys))


def hardcoded_value_signal(events: list[dict[str, Any]], source_type: str) -> str | None:
    """Detect a scraper that fabricates start-time/duration instead of parsing it."""
    if source_type in _MONOCULTURE_EXEMPT_SOURCE_TYPES:
        return None
    if not events or len(events) < _MONOCULTURE_MIN_EVENTS:
        return None

    durations = collections.Counter(e.get("duration_hours") for e in events)
    _, top_duration_count = durations.most_common(1)[0]
    duration_ratio = top_duration_count / len(events)

    midnight_count = sum(
        1 for e in events
        if not e.get("all_day") and str(e.get("datetime") or "")[11:16] == "00:00"
    )
    midnight_ratio = midnight_count / len(events)

    if duration_ratio >= _MONOCULTURE_RATIO_THRESHOLD and midnight_ratio >= _MONOCULTURE_RATIO_THRESHOLD:
        return (
            f"{round(duration_ratio * 100)}% av hendelsene har identisk varighet og "
            f"{round(midnight_ratio * 100)}% starter kl. 00:00 uten å være heldagshendelser "
            "-- dette matcher mønsteret til en skraper som fyller inn en fallback-verdi i "
            "stedet for å lese faktisk start/varighet fra kilden, ikke ekte bookinger."
        )
    return None


def non_schedulable_arena_signal(club_name: str | None, events: list[dict[str, Any]]) -> str | None:
    """Flag events an existing per-club arena classifier tags as non-schedulable."""
    if not club_name or not events:
        return None
    try:
        from ..club_registry import CLUB_REGISTRY
    except ImportError:
        return None
    entry = CLUB_REGISTRY.get(club_name)
    if entry is None or not entry.non_schedulable_arena_aliases:
        return None

    aliases = set(entry.non_schedulable_arena_aliases)
    tagged = [str(e.get("arena")) for e in events if e.get("arena")]
    if not tagged:
        return None
    flagged = sum(1 for arena in tagged if arena in aliases)
    if flagged == 0:
        return None
    ratio = flagged / len(events)
    return (
        f"{flagged} av {len(events)} hendelser ({round(ratio * 100)}%) er merket som "
        f"{'/'.join(sorted(aliases))} av den klubbspesifikke klassifisereren, et arena-alias "
        f"RVV aldri kan booke -- bekreft med klubben om disse faktisk er {entry.arena}-belegg "
        "eller om de bør ekskluderes fra tilgjengelighetsbevis."
    )


def _coverage_metadata(source: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = source.get("coverage")
    if isinstance(raw, Mapping):
        return dict(raw)
    # Scrapers may attach a coverage record to the returned event list (a
    # list subclass); Stage 2 copies it onto the source result, but accept it
    # directly too so a caller building a source dict by hand behaves the same.
    events = source.get("events")
    attached = getattr(events, "coverage", None)
    return dict(attached) if isinstance(attached, Mapping) else None


def evaluate_source_integrity(
    source: Mapping[str, Any],
    *,
    requested_start: str | None = None,
    requested_end: str | None = None,
    previous_event_count: int | None = None,
) -> dict[str, Any]:
    """Return the deterministic integrity verdict for one Stage 2 source result.

    ``status`` is ``complete`` when no integrity signal fired. Any other status
    means the source must never be used as proof that a tournament is *not*
    booked. The function never scrapes and never mutates; it only reads the
    already-produced source result.
    """

    name = str(source.get("name") or "ukjent")
    source_type = str(source.get("type") or "").lower()
    events = [e for e in (source.get("events") or []) if isinstance(e, dict)]
    event_count = int(source.get("event_count") or len(events))
    reasons: list[str] = []
    exceptions: list[str] = []
    status = INTEGRITY_COMPLETE

    coverage = _coverage_metadata(source)

    if source.get("skipped"):
        status = INTEGRITY_FAILED
        reasons.append(str(source.get("skip_reason") or "Kilden ble hoppet over."))
    elif source.get("blocked"):
        status = INTEGRITY_FAILED
        reasons.append(str(source.get("block_reason") or "Kilden ble blokkert."))
    elif source.get("scraper_error"):
        status = INTEGRITY_FAILED
        exceptions.append(str(source.get("scraper_error")))
        reasons.append(str(source.get("scraper_error")))
    else:
        # Explicit coverage metadata from the scraper takes precedence over the
        # generic shape heuristics for the partial/complete distinction.
        if coverage is not None:
            raw_exceptions = coverage.get("exceptions") or []
            if isinstance(raw_exceptions, str):
                raw_exceptions = [raw_exceptions]
            exceptions.extend(str(item) for item in raw_exceptions if str(item or "").strip())
            coverage_status = str(coverage.get("status") or "").lower()
            navigation_complete = coverage.get("navigation_complete")
            if coverage_status == INTEGRITY_FAILED:
                status = INTEGRITY_FAILED
                reasons.append("Skraperen rapporterte en fullstendig feilet dekning.")
            elif coverage_status == INTEGRITY_PARTIAL or exceptions:
                status = INTEGRITY_PARTIAL
                reasons.append("Skraperen rapporterte ufullstendig dekning eller en delvis feil.")
            elif navigation_complete is False:
                status = INTEGRITY_PARTIAL
                reasons.append("Skraperen nådde ikke slutten av den forespurte kalenderperioden.")

        expectation = source.get("event_expectation") or {}
        expectation_status = str(expectation.get("status") or "not_applicable")
        if status not in {INTEGRITY_FAILED, INTEGRITY_PARTIAL} and expectation_status in {"low", "suspicious"}:
            status = INTEGRITY_SUSPICIOUS
            reasons.append(
                str(expectation.get("message") or "Kildens kalenderdata ser mistenkelig ut for perioden.")
            )

        dup_ratio = duplicate_ratio(events)
        if status not in {INTEGRITY_FAILED, INTEGRITY_PARTIAL} and dup_ratio > _DUPLICATE_RATIO_WARNING_THRESHOLD:
            status = INTEGRITY_SUSPICIOUS
            reasons.append(f"{round(dup_ratio * 100)}% av hendelsene ser ut til å være duplikater.")

        hardcoded = hardcoded_value_signal(events, source_type)
        if status not in {INTEGRITY_FAILED, INTEGRITY_PARTIAL} and hardcoded:
            status = INTEGRITY_SUSPICIOUS
            reasons.append(hardcoded)

        if previous_event_count is not None and int(previous_event_count) >= _COUNT_REGRESSION_MIN_PREVIOUS:
            if event_count < int(previous_event_count) * (1.0 - _COUNT_REGRESSION_RATIO):
                if status not in {INTEGRITY_FAILED, INTEGRITY_PARTIAL}:
                    status = INTEGRITY_SUSPICIOUS
                reasons.append(
                    f"Kildens hendelsesantall falt fra {int(previous_event_count)} til {event_count} "
                    "siden forrige skraping -- kontroller kilden før negative bookingpåstander."
                )

        try:
            from ..club_registry import club_for_source_name
        except ImportError:
            club_for_source_name = None  # type: ignore[assignment]
        club = club_for_source_name(name) if club_for_source_name else None
        arena_signal = non_schedulable_arena_signal(club, events)
        if status not in {INTEGRITY_FAILED, INTEGRITY_PARTIAL} and arena_signal:
            status = INTEGRITY_SUSPICIOUS
            reasons.append(arena_signal)

    navigation_complete = coverage.get("navigation_complete") if coverage is not None else None
    coverage_proven = bool(
        status == INTEGRITY_COMPLETE
        and (navigation_complete is True or source_type in _STRUCTURALLY_COVERED_TYPES)
    )
    observed_start, observed_end = observed_event_range(events)

    integrity: dict[str, Any] = {
        "schema_version": SOURCE_INTEGRITY_SCHEMA_VERSION,
        "source": name,
        "type": source_type,
        "status": status,
        "trusted_for_negative_claim": bool(status not in _UNTRUSTED_STATUSES and coverage_proven),
        "coverage_proven": coverage_proven,
        "requested_start": requested_start,
        "requested_end": requested_end,
        "observed_start": observed_start,
        "observed_end": observed_end,
        "navigation_complete": navigation_complete,
        "event_count": event_count,
        "previous_event_count": int(previous_event_count) if previous_event_count is not None else None,
        "reasons": reasons,
        "exceptions": exceptions,
    }
    integrity["fingerprint"] = stable_payload_sha256(integrity)
    return integrity


def integrity_by_source(
    source_results: Iterable[Mapping[str, Any]],
    *,
    requested_start: str | None = None,
    requested_end: str | None = None,
    previous_counts: Mapping[str, int] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return ``{source_name: integrity}`` for every source result.

    Two configured rows can share a name; the later row wins because downstream
    keying is by name, mirroring how the rest of Stage 2 groups sources.
    """

    previous_counts = previous_counts or {}
    result: dict[str, dict[str, Any]] = {}
    for source in source_results:
        name = str(source.get("name") or "")
        if not name:
            continue
        result[name] = evaluate_source_integrity(
            source,
            requested_start=requested_start,
            requested_end=requested_end,
            previous_event_count=previous_counts.get(name),
        )
    return result


_INTEGRITY_SEVERITY = {
    INTEGRITY_COMPLETE: 0,
    INTEGRITY_SUSPICIOUS: 1,
    INTEGRITY_PARTIAL: 2,
    INTEGRITY_FAILED: 3,
}


def club_integrity_status(
    source_results: Iterable[Mapping[str, Any]],
    integrity: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    """Return the worst per-club integrity status across that club's sources.

    A club can have more than one configured source; a single partial/suspicious
    source must not be masked by a complete sibling.
    """

    statuses: dict[str, str] = {}
    try:
        from ..club_registry import club_for_source_name
    except ImportError:
        return statuses
    for source in source_results:
        name = str(source.get("name") or "")
        club = club_for_source_name(name)
        if club is None:
            continue
        status = str((integrity.get(name) or {}).get("status") or INTEGRITY_FAILED)
        current = statuses.get(club)
        if current is None or _INTEGRITY_SEVERITY.get(status, 99) > _INTEGRITY_SEVERITY.get(current, 99):
            statuses[club] = status
    return statuses


def club_coverage_proven(
    source_results: Iterable[Mapping[str, Any]],
    integrity: Mapping[str, Mapping[str, Any]],
) -> dict[str, bool]:
    """Return per-club coverage-proof truth: every configured source proven."""

    seen: dict[str, bool] = {}
    try:
        from ..club_registry import club_for_source_name
    except ImportError:
        return seen
    for source in source_results:
        club = club_for_source_name(str(source.get("name") or ""))
        if club is None:
            continue
        proven = bool((integrity.get(str(source.get("name") or "")) or {}).get("coverage_proven"))
        seen[club] = seen.get(club, True) and proven
    return seen


def downgrade_calendar_status_for_integrity(
    status: Mapping[str, str],
    source_results: Iterable[Mapping[str, Any]],
    integrity: Mapping[str, Mapping[str, Any]],
    *,
    operator_confirmed: Iterable[str] = (),
) -> dict[str, str]:
    """Return *status* with suspicious/partial sources failing closed.

    A club is only ``known`` when its calendar evidence is trustworthy for
    automatic placement *and* the source proved it covered the requested window.
    Any non-exempt source that is ``suspicious``/``partial`` **or** not
    coverage-proven downgrades the club from ``known`` to the explicit
    ``source_review_required`` tier, so no consumer treats an unproven calendar
    as evidence for a negative occupancy claim. ``failed`` sources keep the
    existing ``unknown``/``untrusted`` failure tier from
    ``_group_club_calendar_status``.

    A ``fixed_allocation`` source is never downgraded (its availability is a
    deliberate fact, not a scrape result). An operator-confirmed club is also
    never downgraded by this helper: the confirmation is out-of-band placement
    authority, deliberately separate from scrape coverage. Operator
    confirmation therefore does **not** make the source ``coverage_proven`` and
    must not be read as negative booking evidence; the other per-club maps
    (``club_coverage_proven`` / ``club_source_integrity``) continue to report the
    actual scrape verdict.
    """

    confirmed = {str(club) for club in operator_confirmed}
    try:
        from ..club_registry import club_for_source_name
    except ImportError:
        return dict(status)

    result = dict(status)
    for source in source_results:
        name = str(source.get("name") or "")
        club = club_for_source_name(name)
        if club is None or club in confirmed:
            continue
        if str(source.get("type") or "").lower() in _MONOCULTURE_EXEMPT_SOURCE_TYPES:
            continue
        entry = integrity.get(name) or {}
        integrity_status = str(entry.get("status") or "")
        coverage_proven = bool(entry.get("coverage_proven"))
        if result.get(club) == "known" and (
            integrity_status in {INTEGRITY_SUSPICIOUS, INTEGRITY_PARTIAL} or not coverage_proven
        ):
            result[club] = "source_review_required"
    return result
