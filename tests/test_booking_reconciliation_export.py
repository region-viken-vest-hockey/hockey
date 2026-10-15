"""Normalized Stage 4 booking-reconciliation export regressions."""

from __future__ import annotations

import json

from tournament_scheduler.booking.assessment_export import (
    normalize_booking_reconciliation,
)


def _event(fingerprint: str, *, title: str, day: int) -> dict:
    return {
        "event_fingerprint": fingerprint,
        "club": "Skien",
        "date": f"2026-09-{day:02d}",
        "start": "10:00",
        "end": "12:00",
        "title": title,
        "availability": "fixed_busy",
        "candidate_count": 1,
        "candidate_tournaments": [],
        "covered_tournament_ids": [],
        "one_to_many": False,
        "group_booking": False,
        "unmatched": False,
    }


def _candidate(
    event: dict,
    *,
    actionable: bool,
    context_only: bool,
    relation: str,
    evidence: list[str],
    counterevidence: list[str],
) -> dict:
    return {
        "event_fingerprint": event["event_fingerprint"],
        "club": event["club"],
        "date": event["date"],
        "start": event["start"],
        "end": event["end"],
        "title": event["title"],
        "availability": event["availability"],
        "resource_coverage": "covers",
        "relation": relation,
        "date_delta_days": 1,
        "covers_current_interval": False,
        "overlaps_current_interval": relation == "same_date_overlap",
        "age_group_conflict": "age_group" in counterevidence,
        "arena_mismatch": False,
        "season_context_only": context_only,
        "source_trusted": True,
        "source_positive_evidence_usable": True,
        "actionable": actionable,
        "evidence": evidence,
        "counterevidence": counterevidence,
    }


def _assessment(*, tournaments: list[dict], events: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "season": "2026-2027",
        "canonical_state_revision": "revision-1",
        "date_window_days": 7,
        "clubs": ["Skien"],
        "sources": {"Skien": {"calendar_fingerprint": "calendar-1"}},
        "tournaments": tournaments,
        "events": events,
        "counts": {"unresolved_events": sum(bool(event["unmatched"]) for event in events)},
        "unresolved": {
            "tournament_ids": [],
            "event_fingerprints": [
                event["event_fingerprint"] for event in events if event["unmatched"]
            ],
        },
        "findings": [],
        "limitations": ["proposals_are_advisory_until_an_explicit_operator_confirmation"],
        "assessment_fingerprint": "full-assessment-fingerprint",
    }


def _context_fingerprints(projection: dict, tournament: dict) -> set[str]:
    group = next(
        group
        for group in projection["season_context"]["groups"]
        if group["group"] == tournament["season_context"]["group"]
    )
    fingerprints = set(group["event_fingerprints"])
    fingerprints.difference_update(
        relation["event_fingerprint"] for relation in tournament["candidate_relations"]
    )
    for excluded in group["excluded_pairs"]:
        if tournament["tournament_id"] in excluded["tournament_ids"]:
            fingerprints.discard(excluded["event_fingerprint"])
    return fingerprints


def test_projection_preserves_shifted_primary_and_conflicting_evidence_without_event_duplication():
    """The #710 selection semantics survive the normalized representation."""

    shifted = _event("shifted", title="Miniputt U10", day=13)
    wrong_age = _event("wrong-age", title="U12 Skien - Stjernen", day=12)
    context = _event("context", title="Recurring U11 16:30", day=14)
    context["unmatched"] = True
    candidates = [
        _candidate(
            shifted,
            actionable=True,
            context_only=False,
            relation="proximate_date_shift",
            evidence=["event_title_age_group_matches"],
            counterevidence=["event_date_differs_from_canonical"],
        ),
        _candidate(
            wrong_age,
            actionable=False,
            context_only=False,
            relation="same_date_overlap",
            evidence=["same_date_interval_overlap"],
            counterevidence=["age_group", "event_title_age_group_differs"],
        ),
        _candidate(
            context,
            actionable=False,
            context_only=True,
            relation="proximate_date_shift",
            evidence=[],
            counterevidence=["event_lacks_identity_evidence"],
        ),
    ]
    tournament = {
        "tournament_id": "skien-u10",
        "host_club": "Skien",
        "classification": "proposed_changed_slot",
        "primary_candidate_event_fingerprint": "shifted",
        "proposal_is_binding": False,
        "candidate_count": 3,
        "candidates": candidates,
    }

    projection = normalize_booking_reconciliation(
        _assessment(tournaments=[tournament], events=[shifted, wrong_age, context])
    )

    assert projection["schema_version"] == 2
    assert projection["assessment_schema_version"] == 1
    assert set(projection["events"]) == {"shifted", "wrong-age", "context"}
    row = projection["tournaments"][0]
    assert row["classification"] == "proposed_changed_slot"
    assert row["primary_candidate_event_fingerprint"] == "shifted"
    assert {relation["event_fingerprint"] for relation in row["candidate_relations"]} == {
        "shifted",
        "wrong-age",
    }
    wrong_age_relation = next(
        relation
        for relation in row["candidate_relations"]
        if relation["event_fingerprint"] == "wrong-age"
    )
    assert wrong_age_relation["actionable"] is False
    assert "event_title_age_group_differs" in wrong_age_relation["counterevidence"]
    assert _context_fingerprints(projection, row) == {"context"}
    assert row["season_context"]["event_count"] == 1
    assert projection["events"]["context"]["unmatched"] is True

    # Full event metadata lives only in the event table. Relations carry only
    # stable references plus relation/evidence fields.
    assert all(
        key not in relation
        for relation in row["candidate_relations"]
        for key in ("club", "date", "start", "end", "title", "availability")
    )
    serialized = json.dumps(projection, ensure_ascii=False)
    assert serialized.count("Recurring U11 16:30") == 1
    assert serialized.count("U12 Skien - Stjernen") == 1


def _production_shaped_assessment(tournament_count: int, event_count: int) -> dict:
    events = [
        _event(f"event-{index:04d}", title=f"Ordinary club event {index:04d}", day=1 + index % 28)
        for index in range(event_count)
    ]
    tournaments = []
    for tournament_index in range(tournament_count):
        candidates = [
            _candidate(
                event,
                actionable=False,
                context_only=True,
                relation="proximate_date_shift",
                evidence=[],
                counterevidence=[
                    "event_lacks_identity_evidence",
                    "canonical_slot_not_covered",
                ],
            )
            for event in events
        ]
        tournaments.append(
            {
                "tournament_id": f"tournament-{tournament_index:03d}",
                "host_club": "Skien",
                "classification": "unmatched",
                "candidate_count": len(candidates),
                "candidates": candidates,
            }
        )
    return _assessment(tournaments=tournaments, events=events)


def test_production_shaped_context_matrix_serializes_near_linearly():
    """Doubling tournaments and events must not retain 4x full-object growth."""

    small_assessment = _production_shaped_assessment(20, 200)
    large_assessment = _production_shaped_assessment(40, 400)
    small_projection = normalize_booking_reconciliation(small_assessment)
    large_projection = normalize_booking_reconciliation(large_assessment)

    small_size = len(json.dumps(small_projection, ensure_ascii=False, sort_keys=True))
    large_size = len(json.dumps(large_projection, ensure_ascii=False, sort_keys=True))
    denormalized_large_size = len(
        json.dumps(large_assessment, ensure_ascii=False, sort_keys=True)
    )

    assert large_size < small_size * 2.3
    assert large_size < denormalized_large_size / 10
    assert len(large_projection["events"]) == 400
    assert all(
        not row["candidate_relations"] for row in large_projection["tournaments"]
    )
    assert large_projection["season_context"]["groups"] == [
        {
            "group": "Skien",
            "host_club": "Skien",
            "tournament_ids": [f"tournament-{index:03d}" for index in range(40)],
            "event_fingerprints": [f"event-{index:04d}" for index in range(400)],
            "excluded_pairs": [],
        }
    ]
