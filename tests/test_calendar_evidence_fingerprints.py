from __future__ import annotations

import copy

import pytest

from tournament_scheduler.application.canonical_season.calendars.fingerprints import (
    calendar_evidence_fingerprint,
    source_evidence_fingerprint,
    stage2_evidence_fingerprint,
)


def _problem() -> dict:
    return {
        "club_busy_dates": {"Skien": ["2026-10-17"]},
        "club_busy_intervals": {
            "Skien": [
                {
                    "date": "2026-10-17",
                    "start": "10:00",
                    "end": "12:00",
                    "calendar_event": "RVV U10",
                    "availability": "fixed_busy",
                    "resources": ["Bane 2", "Bane 1"],
                },
                {
                    "date": "2026-10-17",
                    "start": "10:00",
                    "end": "11:00",
                    "calendar_event": "Offentlig skøyting",
                    "availability": "fixed_busy",
                },
            ]
        },
        "club_calendar_status": {"Skien": "known"},
        "club_source_integrity": {"Skien": "complete"},
        "club_source_integrity_details": {
            "Skien": {"status": "complete", "reasons": ["covered", "parsed"]}
        },
        "club_coverage_proven": {"Skien": True},
        "unclassified_calendar_events": [],
    }


def _policy() -> dict:
    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "sources": [
            {
                "name": "Skien ishall",
                "type": "ical",
                "url": "https://example.test/skien.ics",
                "event_classification_rules": [
                    {"pattern": "RVV", "classification": "movable_busy"},
                    {"pattern": "skøyting", "classification": "fixed_busy"},
                ],
            }
        ],
    }


def _source() -> dict:
    return {
        "name": "Skien ishall",
        "type": "ical",
        "url": "https://example.test/skien.ics",
        "events": [
            {
                "date": "17.10.2026",
                "name": "RVV U10",
                "datetime": "2026-10-17T10:00:00+02:00",
                "duration_minutes": 120,
                "location": "Skien ishall",
                "order_id": "booking-2",
            },
            {
                "date": "17.10.2026",
                "name": "Offentlig skøyting",
                "datetime": "2026-10-17T10:00:00+02:00",
                "duration_minutes": 60,
                "location": "Skien ishall",
                "order_id": "booking-1",
            },
        ],
        "event_count": 2,
        "blocked": False,
        "integrity": {"status": "complete", "reasons": ["covered", "parsed"]},
        "scrape_timestamp": "2026-10-13T10:00:00+00:00",
        "from_cache": False,
        "cache_age_hours": 0.0,
        "run_id": "run-one",
    }


def test_calendar_fingerprint_ignores_order_and_volatile_provenance() -> None:
    problem = _problem()
    reordered = copy.deepcopy(problem)
    reordered["club_busy_intervals"]["Skien"].reverse()
    next(
        event
        for event in reordered["club_busy_intervals"]["Skien"]
        if event["calendar_event"] == "RVV U10"
    )["resources"] = ["Bane 1", "Bane 2"]
    reordered["club_source_integrity_details"]["Skien"]["reasons"].reverse()
    reordered["club_busy_intervals"]["Skien"][0].update(
        {"fetched_at": "later", "run_id": "another-run", "cache_path": "/tmp/cache"}
    )
    problem["club_busy_intervals"]["Skien"][1].update(
        {"fetched_at": "earlier", "run_id": "first-run", "cache_path": "/other/cache"}
    )

    assert calendar_evidence_fingerprint(problem, _policy()) == calendar_evidence_fingerprint(
        reordered, _policy()
    )


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("date", "2026-10-18"),
        ("start", "10:30"),
        ("end", "12:30"),
        ("calendar_event", "Changed title"),
        ("resources", ["Bane 3"]),
    ],
)
def test_calendar_fingerprint_changes_for_material_event_facts(field: str, changed: object) -> None:
    original = _problem()
    modified = copy.deepcopy(original)
    modified["club_busy_intervals"]["Skien"][0][field] = changed

    assert calendar_evidence_fingerprint(original, _policy()) != calendar_evidence_fingerprint(
        modified, _policy()
    )


def test_calendar_fingerprint_changes_for_coverage_or_source_policy() -> None:
    problem = _problem()
    changed_coverage = copy.deepcopy(problem)
    changed_coverage["club_coverage_proven"]["Skien"] = False
    changed_policy = _policy()
    changed_policy["sources"][0]["url"] = "https://example.test/replacement.ics"

    baseline = calendar_evidence_fingerprint(problem, _policy())
    assert calendar_evidence_fingerprint(changed_coverage, _policy()) != baseline
    assert calendar_evidence_fingerprint(problem, changed_policy) != baseline


def test_source_and_stage2_fingerprints_ignore_order_and_fetch_provenance() -> None:
    source = _source()
    repeated = copy.deepcopy(source)
    repeated["events"].reverse()
    repeated["integrity"]["reasons"].reverse()
    repeated.update(
        {
            "scrape_timestamp": "2026-10-13T10:05:00+00:00",
            "from_cache": True,
            "cache_age_hours": 1.5,
            "cache_path": "/tmp/a-different-cache",
            "run_id": "run-two",
        }
    )

    assert source_evidence_fingerprint(source) == source_evidence_fingerprint(repeated)

    first_scrape = {
        "sources": [source],
        "club_calendar_status": {"Skien": "known"},
        "club_source_integrity": {"Skien": "complete"},
        "club_coverage_proven": {"Skien": True},
        "blocked": [],
        "empty_sources": [],
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "run_id": "outer-run-one",
    }
    second_scrape = {**first_scrape, "sources": [repeated], "run_id": "outer-run-two"}
    assert stage2_evidence_fingerprint(first_scrape) == stage2_evidence_fingerprint(second_scrape)

    changed = copy.deepcopy(repeated)
    changed["events"][0]["order_id"] = "materially-different-booking"
    assert source_evidence_fingerprint(source) != source_evidence_fingerprint(changed)
