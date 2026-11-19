"""Per-club RVV calendar sources (Outlook / BookUp / iCal) bound to the ice-time port.

No new scraping, availability or trust policy lives here (#333 owns capability
consolidation). The existing Stage 2 per-source scrape (``_scrape_source``) still
acquires events, and the canonical integrity verdict
(``pipeline.source_integrity.evaluate_source_integrity``) still decides trust.
This module only translates that result onto the port via the generic
``LiveCalendarSource`` / ``legacy_events_fetcher`` path from #968:

* blocked / skipped / errored source  -> ``PortError(UNAVAILABLE)``: a typed failure
  (the live adapter retains a prior snapshot as stale evidence, never free ice);
* integrity ``complete`` + coverage proven + club trusted for auto placement
  -> ``COMPLETE`` coverage (the legacy ``known`` calendar status);
* anything else (suspicious, partial, unproven, untrusted club) -> ``PARTIAL``.

Events are occupancy evidence only; no ``BookingAssertion`` is ever emitted.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from tournament_scheduler.application.ports.common import PortError, PortErrorCode
from tournament_scheduler.application.ports.ice_time import Completeness, SourceScope

from .legacy_calendar import legacy_events_fetcher
from .live import LiveCalendarSource, LivePayload

TIMEZONE = "Europe/Oslo"
PROVIDERS = ("outlook", "bookup", "ical")

ScrapeFn = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class ClubSourceBinding:
    """Stable identity of one per-club calendar source and its provider family."""

    club: str
    source_name: str
    provider: str  # one of PROVIDERS
    venue_id: str
    source_cfg: Mapping[str, Any]

    @property
    def source_id(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.club.lower()).strip("-")
        return f"rvv-club/{slug}/{self.provider}"


def _provider(source_cfg: Mapping[str, Any]) -> str:
    """Same routing as Stage 2 ``_scrape_source`` dispatch, read-only."""
    from tournament_scheduler.pipeline.scraper_constants import _BROWSER_SOURCE_TYPES, _ICAL_SOURCE_TYPES
    from tournament_scheduler.pipeline.scraper_strategies import get_deterministic_scraper_type, get_strategy

    name, stype = source_cfg.get("name", ""), str(source_cfg.get("type", "outlook")).lower()
    if stype in _ICAL_SOURCE_TYPES:
        return "ical"
    strategy = get_strategy(name)
    if strategy is not None:
        if get_deterministic_scraper_type(strategy) == "bookup":
            return "bookup"
        if stype in _BROWSER_SOURCE_TYPES:
            return "outlook"
        raise ValueError(f"source {name!r} is not an Outlook/BookUp/iCal source")
    if stype in _BROWSER_SOURCE_TYPES:
        return "outlook"
    raise ValueError(f"source {name!r} is not an Outlook/BookUp/iCal source")


def bind_club_source(source_cfg: Mapping[str, Any]) -> ClubSourceBinding:
    """Resolve a Stage 1 source row to its stable per-club binding."""
    from tournament_scheduler.clubs.registry import CLUB_REGISTRY, club_for_source_name

    name = str(source_cfg.get("name", ""))
    club = club_for_source_name(name)
    if club is None:
        raise ValueError(f"source {name!r} matches no RVV club")
    return ClubSourceBinding(club, name, _provider(source_cfg), CLUB_REGISTRY[club].arena, dict(source_cfg))


def _default_scrape(cfg: Mapping[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
    from tournament_scheduler.pipeline.stage2_scraping import _scrape_source

    return _scrape_source(dict(cfg), start_date=start, end_date=end)


def club_source_fetcher(binding: ClubSourceBinding, scrape: ScrapeFn | None = None):
    """Fetcher for ``LiveCalendarSource``: scrape, apply the canonical verdict, translate."""
    from tournament_scheduler.clubs.registry import CLUB_REGISTRY
    from tournament_scheduler.models import CalendarEvent
    from tournament_scheduler.pipeline.source_integrity import INTEGRITY_COMPLETE, evaluate_source_integrity

    run_scrape = scrape or _default_scrape

    def fetch(scope: SourceScope) -> LivePayload:
        captured: dict[str, Any] = {}

        def run(start: datetime, end: datetime):
            result = run_scrape(binding.source_cfg, start, end)
            integrity = evaluate_source_integrity(
                result, requested_start=start.strftime("%Y-%m-%d"), requested_end=end.strftime("%Y-%m-%d"))
            if integrity["status"] == "failed":
                raise PortError(PortErrorCode.UNAVAILABLE,
                                f"{binding.source_id}: " + "; ".join(integrity["reasons"] or ["source failed"]))
            captured["integrity"] = integrity
            return [CalendarEvent.from_source_dict(e) for e in result.get("events") or []]

        payload = legacy_events_fetcher(run, venue_id=binding.venue_id, timezone=TIMEZONE)(scope)
        integrity = captured["integrity"]
        trusted = (integrity["status"] == INTEGRITY_COMPLETE and integrity["coverage_proven"]
                   and CLUB_REGISTRY[binding.club].trusted_for_auto_placement)
        digest = hashlib.sha256(f"{payload.fingerprint}|{integrity['fingerprint']}".encode()).hexdigest()
        return replace(payload, fingerprint=digest,
                       completeness=Completeness.COMPLETE if trusted else Completeness.PARTIAL)

    return fetch


def club_live_source(source_cfg: Mapping[str, Any], clock: Callable[[], datetime],
                     scrape: ScrapeFn | None = None, ttl: timedelta = timedelta(hours=6)) -> LiveCalendarSource:
    """An ``IceTimeSourcePort`` for one per-club source; call ``refresh(scope)`` before reading."""
    binding = bind_club_source(source_cfg)
    return LiveCalendarSource(binding.source_id, TIMEZONE, club_source_fetcher(binding, scrape), clock, ttl)
