"""Deterministic source-integrity verdict (fail closed for negative claims).

A successful Stage 2 scrape is not automatically trustworthy evidence for the
conclusion that a tournament is *not* booked. These tests pin the reusable
verdict and the calendar-status downgrade it drives.
"""

from __future__ import annotations

from tournament_scheduler.pipeline.source_integrity import (
    INTEGRITY_COMPLETE,
    INTEGRITY_FAILED,
    INTEGRITY_PARTIAL,
    INTEGRITY_SUSPICIOUS,
    club_coverage_proven,
    downgrade_calendar_status_for_integrity,
    evaluate_source_integrity,
    integrity_by_source,
    with_coverage,
)


def _event(day: int, *, hour: int = 10, name: str = "Miniputt", location: str = "") -> dict:
    return {
        "date": f"{day:02d}.09.2026",
        "datetime": f"2026-09-{day:02d}T{hour:02d}:00:00",
        "name": name,
        "duration_hours": 2.0,
        "location": location,
    }


def _ok_events(count: int = 5) -> list[dict]:
    return [_event(day, name=f"Miniputt U10 #{day}") for day in range(1, count + 1)]


def test_clean_ical_source_is_complete_and_coverage_proven():
    source = {"name": "Ringerike", "type": "ical", "events": _ok_events(), "event_count": 5}

    integrity = evaluate_source_integrity(source, requested_start="2026-09-01", requested_end="2026-09-30")

    assert integrity["status"] == INTEGRITY_COMPLETE
    assert integrity["coverage_proven"] is True
    assert integrity["trusted_for_negative_claim"] is True
    assert integrity["reasons"] == []


def test_browser_navigation_source_without_proof_is_not_coverage_proven():
    # A week-navigating browser source that reports no coverage is not proven
    # even though it returned a clean event set.
    source = {"name": "Jar", "type": "forumbooking", "events": _ok_events(), "event_count": 5}

    integrity = evaluate_source_integrity(source)

    assert integrity["status"] == INTEGRITY_COMPLETE
    assert integrity["coverage_proven"] is False
    assert integrity["trusted_for_negative_claim"] is False


def test_blocked_and_skipped_sources_fail_closed():
    blocked = evaluate_source_integrity({"name": "Holmen", "type": "outlook", "blocked": True, "block_reason": "timeout"})
    skipped = evaluate_source_integrity({"name": "Holmen", "type": "outlook", "skipped": True, "skip_reason": "tom URL"})

    assert blocked["status"] == INTEGRITY_FAILED
    assert skipped["status"] == INTEGRITY_FAILED
    assert blocked["trusted_for_negative_claim"] is False
    assert skipped["trusted_for_negative_claim"] is False


def test_scraper_error_fails_closed_even_with_events():
    source = {
        "name": "Skien",
        "type": "brp_exigo",
        "events": _ok_events(3),
        "event_count": 3,
        "scraper_error": "Playwright crash mid-navigation",
    }

    integrity = evaluate_source_integrity(source)

    assert integrity["status"] == INTEGRITY_FAILED
    assert integrity["exceptions"] == ["Playwright crash mid-navigation"]


def test_suspicious_event_expectation_fails_closed():
    source = {
        "name": "Kongsberg",
        "type": "outlook",
        "events": _ok_events(2),
        "event_count": 2,
        "event_expectation": {"status": "low", "message": "for få hendelser"},
    }

    integrity = evaluate_source_integrity(source)

    assert integrity["status"] == INTEGRITY_SUSPICIOUS
    assert "for få hendelser" in integrity["reasons"]


def test_duplicate_heavy_source_fails_closed():
    events = [_event(day, name="Booket") for day in range(1, 6)] * 2
    source = {"name": "Tønsberg", "type": "bookup", "events": events, "event_count": len(events)}

    integrity = evaluate_source_integrity(source)

    assert integrity["status"] == INTEGRITY_SUSPICIOUS
    assert any("duplikater" in reason for reason in integrity["reasons"])


def test_event_count_regression_versus_previous_scrape_is_suspicious():
    source = {"name": "Tønsberg", "type": "bookup", "events": _ok_events(5), "event_count": 5}

    integrity = evaluate_source_integrity(source, previous_event_count=671)

    assert integrity["status"] == INTEGRITY_SUSPICIOUS
    assert any("671" in reason and "5" in reason for reason in integrity["reasons"])


def test_small_event_count_change_is_not_flagged():
    source = {"name": "Tønsberg", "type": "bookup", "events": _ok_events(9), "event_count": 9}

    integrity = evaluate_source_integrity(source, previous_event_count=10)

    assert integrity["status"] == INTEGRITY_COMPLETE


def test_explicit_partial_coverage_fails_closed():
    source = {
        "name": "Jar",
        "type": "forumbooking",
        "events": _ok_events(3),
        "event_count": 3,
        "coverage": {"status": INTEGRITY_PARTIAL, "navigation_complete": False, "exceptions": ["uke 12 mangler"]},
    }

    integrity = evaluate_source_integrity(source)

    assert integrity["status"] == INTEGRITY_PARTIAL
    assert integrity["navigation_complete"] is False
    assert integrity["exceptions"] == ["uke 12 mangler"]
    assert integrity["trusted_for_negative_claim"] is False


def test_coverage_annotated_events_carry_the_proof():
    events = with_coverage(_ok_events(3), status=INTEGRITY_COMPLETE, navigation_complete=True, exceptions=[])
    source = {"name": "Sandefjord Penguins", "type": "bookup", "events": events, "event_count": 3}

    integrity = evaluate_source_integrity(source)

    assert integrity["status"] == INTEGRITY_COMPLETE
    assert integrity["coverage_proven"] is True


def test_downgrade_marks_known_club_as_source_review_required():
    sources = [{"name": "Jar", "type": "forumbooking", "events": _ok_events(2), "event_count": 2,
                "event_expectation": {"status": "suspicious", "message": "mistanke"}}]
    integrity = integrity_by_source(sources)

    status = downgrade_calendar_status_for_integrity(
        {"Jar": "known", "Holmen": "known"}, sources, integrity
    )

    assert status["Jar"] == "source_review_required"
    assert status["Holmen"] == "known"


def test_downgrade_preserves_operator_confirmed_and_fixed_allocation():
    sources = [
        {"name": "Jar", "type": "forumbooking", "events": _ok_events(2), "event_count": 2,
         "event_expectation": {"status": "low", "message": "lite"}},
        {"name": "Sandefjord Penguins", "type": "fixed_allocation", "events": [], "event_count": 0,
         "event_expectation": {"status": "low", "message": "lite"}},
    ]
    integrity = integrity_by_source(sources)

    status = downgrade_calendar_status_for_integrity(
        {"Jar": "known", "Sandefjord Penguins": "known"},
        sources,
        integrity,
        operator_confirmed=["Jar"],
    )

    assert status["Jar"] == "known"
    assert status["Sandefjord Penguins"] == "known"


def test_downgrade_does_not_promote_unknown_or_untrusted():
    sources = [{"name": "Tønsberg", "type": "bookup", "events": [], "event_count": 0,
                "event_expectation": {"status": "low", "message": "lite"}}]
    integrity = integrity_by_source(sources)

    status = downgrade_calendar_status_for_integrity({"Tønsberg": "untrusted"}, sources, integrity)

    assert status["Tønsberg"] == "untrusted"


def test_club_coverage_proven_requires_every_source():
    sources = [
        {"name": "Ringerike", "type": "ical", "events": _ok_events(2), "event_count": 2},
        {"name": "Ringerike ishall", "type": "forumbooking", "events": _ok_events(2), "event_count": 2},
    ]
    integrity = integrity_by_source(sources)

    proven = club_coverage_proven(sources, integrity)

    assert proven["Ringerike"] is False


def test_club_integrity_status_reports_the_worst_sibling():
    from tournament_scheduler.pipeline.source_integrity import club_integrity_status

    sources = [
        {"name": "Holmen", "type": "ical", "events": _ok_events(2), "event_count": 2},
        {"name": "Holmen Sportello", "type": "outlook", "events": _ok_events(2), "event_count": 2,
         "coverage": {"status": INTEGRITY_PARTIAL, "navigation_complete": False, "exceptions": ["uke mangler"]}},
    ]
    integrity = integrity_by_source(sources)

    assert club_integrity_status(sources, integrity)["Holmen"] == INTEGRITY_PARTIAL
