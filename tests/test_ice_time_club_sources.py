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


def test_verdict_parity_with_manual_and_csv_for_all_three_verdicts(monkeypatch, tmp_path):
    from tournament_scheduler.application.ice_time_sources.authority import Authority
    from tournament_scheduler.application.ports.ice_time import WindowKind
    from tournament_scheduler.infrastructure.ice_time.file_import import file_source, import_snapshot
    from tournament_scheduler.infrastructure.ice_time.manual import build_manual_snapshot, manual_source

    fx, _ = install(monkeypatch, "ical")
    binding = bind_club_source(fx["source"])
    v = binding.venue_id
    sc = scope_for(v)
    live = club_live_source(fx["source"], lambda: NOW)
    live.refresh(sc)
    assert live.coverage(sc)[0].completeness is Completeness.COMPLETE and live.coverage(sc)[0].is_fresh(NOW)
    auth = Authority(issuer="rink office", scope="November", effective_from=NOW - timedelta(days=1),
                     effective_until=NOW + timedelta(days=30), venue_id=v,
                     covers_from=sc.from_date, covers_to=sc.to_date, document="ice.csv")

    def at(d, h1, h2):
        return datetime(2026, 11, d, h1, tzinfo=OSLO), datetime(2026, 11, d, h2, tzinfo=OSLO)

    busy, free = at(7, 9, 10), at(28, 9, 10)
    blocked_w = [(*at(7, 9, 11), WindowKind.BLOCKED)]
    avail_w = [(*at(28, 9, 12), WindowKind.AVAILABLE)]

    def man(windows, authority=auth):
        return manual_source(build_manual_snapshot(
            source_id="m", actor="op", timezone="Europe/Oslo", scope=sc, windows=windows,
            authority=authority, captured_at=NOW))

    def csv(body, authority=auth):
        p = tmp_path / "ice.csv"
        p.write_text("venue_id,date,start,end,kind\n" + body)
        return file_source(import_snapshot(p, source_id="f", timezone="Europe/Oslo", scope=sc,
                                           captured_at=NOW, authority=authority))

    def verdict(src, probe):
        return availability_verdict(src, sc, v, *probe, NOW)

    # UNAVAILABLE: a complete, fresh live event, a manual block and a CSV block agree.
    assert (verdict(live, busy) is verdict(man(blocked_w), busy)
            is verdict(csv(f"{v},2026-11-07,09:00,11:00,blocked\n"), busy) is Verdict.UNAVAILABLE)
    # AVAILABLE needs an explicit allocation window: manual/CSV give it, live absence never does.
    assert (verdict(man(avail_w), free)
            is verdict(csv(f"{v},2026-11-28,09:00,12:00,available\n"), free) is Verdict.AVAILABLE)
    assert verdict(live, free) is not Verdict.AVAILABLE
    empty = fx_empty_live(monkeypatch)
    assert empty.coverage(sc)[0].completeness is Completeness.COMPLETE
    assert verdict(empty, free) is not Verdict.AVAILABLE
    # UNKNOWN: unattested manual, unattested CSV, partial live all refuse a positive answer.
    assert (verdict(man([], authority=None), free) is verdict(csv("", authority=None), free)
            is Verdict.UNKNOWN)
    fx2, _ = install(monkeypatch, "ical", coverage={"status": "partial", "navigation_complete": False,
                                                    "exceptions": ["stopped early"]})
    part = club_live_source(fx2["source"], lambda: NOW)
    part.refresh(sc)
    assert verdict(part, free) is Verdict.UNKNOWN


def fx_empty_live(monkeypatch):
    fx = FIX["ical"]
    monkeypatch.setattr(stage2, "_run_ical_scraper", lambda *a, **k: [])
    src = club_live_source(fx["source"], lambda: NOW)
    src.refresh(scope_for(bind_club_source(fx["source"]).venue_id))
    return src


@pytest.mark.parametrize("provider", ["outlook", "bookup", "ical"])
def test_unexpected_acquisition_exception_is_typed_and_retains_stale(monkeypatch, provider):
    fx, _ = install(monkeypatch, provider)
    binding = bind_club_source(fx["source"])
    clock = {"now": NOW}
    src = club_live_source(fx["source"], lambda: clock["now"])
    sc = scope_for(binding.venue_id)
    src.refresh(sc)
    from tournament_scheduler.infrastructure.ice_time import club_sources

    def explode(cfg, start, end):
        raise ValueError("unexpected parser crash")

    fetch = club_sources.club_source_fetcher(binding, explode)
    with pytest.raises(PortError) as exc:
        fetch(sc)
    assert exc.value.code is PortErrorCode.UNAVAILABLE and "ValueError" in exc.value.message
    # Through the live adapter with a previous good snapshot: retained as stale, not raised.
    src._fetch = fetch
    clock["now"] = NOW + timedelta(hours=7)
    src.refresh(sc)
    (cov,) = src.coverage(sc)
    assert cov.completeness is Completeness.PARTIAL and not cov.is_fresh(clock["now"])
    assert src.observed_events(sc)
    # Same boundary for a malformed payload.
    bad = club_sources.club_source_fetcher(binding, lambda c, s, e: {"events": [{"oops": 1}]})
    with pytest.raises(PortError) as exc2:
        bad(sc)
    assert exc2.value.code is PortErrorCode.UNAVAILABLE


def test_source_identity_is_unique_per_configured_calendar_and_stable():
    ice = {"name": "Kongsberg ishall", "type": "outlook", "url": "https://frozen.invalid/a"}
    ball = {"name": "Kongsberg ballhall", "type": "outlook", "url": "https://frozen.invalid/b"}
    a, b = bind_club_source(ice), bind_club_source(ball)
    assert a.club == b.club and a.provider == b.provider
    assert a.source_id != b.source_id
    assert a.source_id == bind_club_source(dict(ice)).source_id
    assert b.source_id == bind_club_source(dict(ball)).source_id
    assert club_live_source(ice, lambda: NOW).capabilities().adapter_id == a.source_id


def test_non_club_or_unsupported_source_is_rejected():
    with pytest.raises(ValueError):
        bind_club_source({"name": "Not A Club", "type": "outlook"})
    with pytest.raises(ValueError):
        bind_club_source({"name": "Frisk Asker", "type": "fixed_allocation"})
