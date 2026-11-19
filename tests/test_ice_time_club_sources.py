"""Per-club Outlook/BookUp/iCal sources through the ice-time port (#978).

Frozen offline 2026-27 fixtures drive the *real* Stage 2 ``_scrape_source`` dispatch
(only the provider scraper functions are replaced), so the port output is compared
with the legacy coverage/integrity/calendar-status behaviour. No network.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tournament_scheduler.application.ice_time_sources.verdict import Verdict, availability_verdict
from tournament_scheduler.application.ports.common import PortError, PortErrorCode
from tournament_scheduler.application.ports.ice_time import (
    Completeness, IceTimeSourcePort, SourceScope,
)
from tournament_scheduler.clubs.registry import CLUB_REGISTRY
from tournament_scheduler.infrastructure.ice_time.club_sources import bind_club_source, club_live_source
from tournament_scheduler.infrastructure.ice_time.live import StalePolicy
from tournament_scheduler.models import CalendarEvent
from tournament_scheduler.pipeline import stage2_scraping as stage2
from tournament_scheduler.pipeline.scraper_event_helpers import _group_club_calendar_status
from tournament_scheduler.pipeline.source_integrity import (
    downgrade_calendar_status_for_integrity, integrity_by_source, with_coverage,
)

FIX = json.loads((Path(__file__).parent / "fixtures/ice_time_club_sources/season_2026_27.json").read_text())
OSLO = ZoneInfo("Europe/Oslo")
NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
START, END = datetime(2026, 11, 1), datetime(2026, 12, 1)
PROVIDER_FN = {"outlook": "_run_outlook_scraper", "bookup": "_run_bookup_scraper", "ical": "_run_ical_scraper"}


def install(monkeypatch, provider: str, *, coverage="fixture", raises: str | None = None):
    fx = FIX[provider]
    events = [CalendarEvent.from_source_dict(e) for e in fx["events"]]
    cov = fx["coverage"] if coverage == "fixture" else coverage
    calls: list = []

    def fake(*args, **kwargs):
        calls.append(args)
        if raises:
            raise RuntimeError(raises)
        out = with_coverage(events, **cov) if cov else events
        return out if provider == "ical" else (out, "")

    monkeypatch.setattr(stage2, PROVIDER_FN[provider], fake)
    return fx, calls


def scope_for(venue: str) -> SourceScope:
    return SourceScope(venue, START.replace(tzinfo=OSLO), END.replace(tzinfo=OSLO))


def legacy_view(fx):
    res = stage2._scrape_source(dict(fx["source"]), start_date=START, end_date=END)
    integ = integrity_by_source([res], requested_start="2026-11-01", requested_end="2026-12-01")
    status = downgrade_calendar_status_for_integrity(_group_club_calendar_status([res]), [res], integ)
    return res, integ, status


@pytest.mark.parametrize("provider", ["outlook", "bookup", "ical"])
def test_provider_family_exercised_through_port_with_legacy_parity(monkeypatch, provider):
    fx, _ = install(monkeypatch, provider)
    binding = bind_club_source(fx["source"])
    assert binding.provider == provider
    src = club_live_source(fx["source"], lambda: NOW)
    assert isinstance(src, IceTimeSourcePort)
    scope = scope_for(binding.venue_id)
    src.refresh(scope)
    legacy, _, legacy_status = legacy_view(fx)
    # events: same intervals/titles/identity as the legacy scrape output
    seen = sorted((e.starts_at, e.ends_at, e.title, e.event_id) for e in src.observed_events(scope))
    want = sorted(
        (datetime.fromisoformat(e["datetime"]).replace(tzinfo=OSLO),
         (datetime.fromisoformat(e["datetime"]) + timedelta(minutes=e["duration_minutes"])).replace(tzinfo=OSLO),
         e["name"], e.get("order_id") or f"{datetime.fromisoformat(e['datetime']).replace(tzinfo=OSLO).isoformat()}|{e['name']}")
        for e in legacy["events"])
    assert seen == want
    # coverage/trust: legacy "known" <=> port COMPLETE; anything else never COMPLETE
    (cov,) = src.coverage(scope)
    club = binding.club
    assert (cov.completeness is Completeness.COMPLETE) == (legacy_status[club] == "known")
    assert cov.source.source_id == binding.source_id and cov.source.fingerprint
    assert src.capabilities().requires_network is True


def test_trust_translation_matches_registry_and_integrity(monkeypatch):
    out = {}
    for p in ("outlook", "bookup", "ical"):
        fx, _ = install(monkeypatch, p)
        s = club_live_source(fx["source"], lambda: NOW)
        sc = scope_for(bind_club_source(fx["source"]).venue_id)
        s.refresh(sc)
        out[p] = s.coverage(sc)[0].completeness
    assert out["outlook"] is Completeness.COMPLETE
    assert out["ical"] is Completeness.COMPLETE          # structurally covered feed
    assert not CLUB_REGISTRY["Tønsberg"].trusted_for_auto_placement
    assert out["bookup"] is Completeness.PARTIAL          # legacy "untrusted"


def test_partial_navigation_is_partial_never_free_ice(monkeypatch):
    fx, _ = install(monkeypatch, "outlook",
                    coverage={"status": "partial", "navigation_complete": False,
                              "exceptions": ["Outlook month navigation stopped early: boom"]})
    src = club_live_source(fx["source"], lambda: NOW)
    binding = bind_club_source(fx["source"])
    sc = scope_for(binding.venue_id)
    src.refresh(sc)
    assert src.coverage(sc)[0].completeness is Completeness.PARTIAL
    assert legacy_view(fx)[2][binding.club] != "known"
    free = (datetime(2026, 11, 28, 9, tzinfo=OSLO), datetime(2026, 11, 28, 12, tzinfo=OSLO))
    assert availability_verdict(src, sc, binding.venue_id, *free, NOW) is Verdict.UNKNOWN


@pytest.mark.parametrize("provider", ["outlook", "bookup", "ical"])
def test_source_failure_is_typed_failure_not_free_ice(monkeypatch, provider):
    fx, _ = install(monkeypatch, provider, raises="connection reset")
    src = club_live_source(fx["source"], lambda: NOW)
    binding = bind_club_source(fx["source"])
    sc = scope_for(binding.venue_id)
    with pytest.raises(PortError) as exc:
        src.refresh(sc)
    assert exc.value.code is PortErrorCode.UNAVAILABLE
    with pytest.raises(PortError):  # nothing readable after a failed first refresh
        src.observed_events(sc)
    assert legacy_view(fx)[2][binding.club] != "known"


def test_failed_refresh_retains_stale_limited_evidence_then_expires(monkeypatch):
    fx, _ = install(monkeypatch, "outlook")
    clock = {"now": NOW}
    binding = bind_club_source(fx["source"])
    src = club_live_source(fx["source"], lambda: clock["now"])
    sc = scope_for(binding.venue_id)
    src.refresh(sc)
    install(monkeypatch, "outlook", raises="timeout")
    clock["now"] = NOW + timedelta(hours=7)
    src.refresh(sc)  # retained
    (cov,) = src.coverage(sc)
    assert cov.completeness is Completeness.PARTIAL and not cov.is_fresh(clock["now"])
    free = (datetime(2026, 11, 28, 9, tzinfo=OSLO), datetime(2026, 11, 28, 12, tzinfo=OSLO))
    assert availability_verdict(src, sc, binding.venue_id, *free, clock["now"]) is Verdict.UNKNOWN
    clock["now"] = NOW + StalePolicy().max_age + timedelta(hours=1)
    with pytest.raises(PortError):
        src.refresh(sc)


def test_observed_events_block_and_never_become_booking_assertions(monkeypatch):
    fx, _ = install(monkeypatch, "ical")
    binding = bind_club_source(fx["source"])
    src = club_live_source(fx["source"], lambda: NOW)
    sc = scope_for(binding.venue_id)
    src.refresh(sc)
    busy = (datetime(2026, 11, 7, 9, tzinfo=OSLO), datetime(2026, 11, 7, 11, tzinfo=OSLO))
    assert availability_verdict(src, sc, binding.venue_id, *busy, NOW) is Verdict.UNAVAILABLE
    assert not hasattr(src, "assertions")


def test_verdict_matches_manual_adapter_for_equivalent_semantics(monkeypatch):
    from tournament_scheduler.application.ports.ice_time import WindowKind
    from tournament_scheduler.infrastructure.ice_time.manual import build_manual_snapshot, manual_source

    fx, _ = install(monkeypatch, "ical")
    binding = bind_club_source(fx["source"])
    sc = scope_for(binding.venue_id)
    live = club_live_source(fx["source"], lambda: NOW)
    live.refresh(sc)
    manual = manual_source(build_manual_snapshot(
        source_id="m", actor="op", timezone="Europe/Oslo", scope=sc,
        windows=[(datetime(2026, 11, 7, 9, tzinfo=OSLO), datetime(2026, 11, 7, 11, tzinfo=OSLO), WindowKind.BLOCKED),
                 (datetime(2026, 11, 15, 14, tzinfo=OSLO), datetime(2026, 11, 15, 15, 30, tzinfo=OSLO), WindowKind.BLOCKED)],
        authority=None, captured_at=NOW))
    busy = (datetime(2026, 11, 7, 9, tzinfo=OSLO), datetime(2026, 11, 7, 10, tzinfo=OSLO))
    other = (datetime(2026, 11, 28, 9, tzinfo=OSLO), datetime(2026, 11, 28, 10, tzinfo=OSLO))
    for probe in (busy,):  # blocking evidence is verdict-equal across source kinds
        assert (availability_verdict(live, sc, binding.venue_id, *probe, NOW)
                is availability_verdict(manual, sc, binding.venue_id, *probe, NOW) is Verdict.UNAVAILABLE)
    # without a typed authority the manual snapshot is UNKNOWN; so is a free probe on a partial live source
    assert availability_verdict(manual, sc, binding.venue_id, *other, NOW) is Verdict.UNKNOWN


def test_non_club_or_unsupported_source_is_rejected():
    with pytest.raises(ValueError):
        bind_club_source({"name": "Not A Club", "type": "outlook"})
    with pytest.raises(ValueError):
        bind_club_source({"name": "Frisk Asker", "type": "fixed_allocation"})
