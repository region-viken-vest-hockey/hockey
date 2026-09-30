"""Regression coverage for the shared accepted-exception admissibility policy.

These tests pin the contract of
:mod:`tournament_scheduler.canonical_exception_policy` at its canonical owner:
an exact, source-confirmed accepted interval is a visible follow-up finding and
never blocks an unrelated mutation, while a changed/worsened/unverified interval
stays hard. The baseline-vs-candidate classification is also covered here so
every canonical writer can rely on one structured comparison.
"""

from __future__ import annotations

from tournament_scheduler.canonical_exception_policy import (
    INTRODUCED,
    MATERIALLY_MODIFIED,
    UNCHANGED_ACCEPTED,
    UNCHANGED_UNACCEPTED,
    WORSENED,
    classify_candidate_violations,
    reclassify_accepted_exceptions,
    reclassify_accepted_source_interval,
)
from tournament_scheduler.final_verification import verify_canonical_candidate


def _team(label: str) -> dict:
    return {"club": "Jar", "label": label, "age_group": "U10"}


def _tournament(
    tournament_id: str,
    *,
    date: str = "2026-02-01",
    arena: str = "Jarahallen",
    start_time: str = "10:00",
) -> dict:
    return {
        "id": tournament_id,
        "date": date,
        "arena": arena,
        "host_club": "Jar",
        "age_group": "U10",
        "start_time": start_time,
        "teams": [_team("Jar 1"), _team("Jar 2"), _team("Jar 3")],
    }


def _problem(*, accepted_minutes: int = 110, date: str = "2026-02-01") -> dict:
    return {
        "teams": [_team("Jar 1"), _team("Jar 2"), _team("Jar 3")],
        "ice_time_minutes": {"U10": 120},
        "ice_time_minutes_overrides": {"t-accepted": accepted_minutes},
        "calendar_booking_associations": [
            {
                "id": "calendar_booking:event:t-accepted",
                "status": "active",
                "event_fingerprint": "event",
                "tournament_id": "t-accepted",
                "club": "Jar",
                "date": date,
                "start": "10:00",
                "end": "11:50",
                "tournament_facts": {
                    "host_club": "Jar",
                    "arena": "Jarahallen",
                    "date": date,
                    "start_time": "10:00",
                    "age_group": "U10",
                },
                "tournament_interval": {
                    "date": date,
                    "start_time": "10:00",
                    "duration_minutes": str(accepted_minutes),
                    "end_time": "11:50",
                },
            }
        ],
    }


def _floor_violation(tournament_id: str, configured: int, minimum: int = 120) -> dict:
    return {
        "code": "ice_time_governing_minimum",
        "message": f"Tournament {tournament_id} is below the governing floor",
        "tournament_id": tournament_id,
        "age_group": "U10",
        "configured_ice_time_minutes": configured,
        "minimum_required_minutes": minimum,
    }


def test_exact_accepted_interval_reclassifies_to_follow_up_finding():
    candidate = {"tournaments": [_tournament("t-accepted")]}
    problem = _problem()

    classified = reclassify_accepted_exceptions(
        problem, candidate, [_floor_violation("t-accepted", 110)]
    )

    assert classified["blocking_violations"] == []
    assert len(classified["accepted_exceptions"]) == 1
    finding = classified["accepted_exceptions"][0]
    assert finding["code"] == "ice_time_governing_minimum"
    assert finding["accepted_booking_interval"] is True
    identity = finding["accepted_exception"]
    assert identity["rule"] == "ice_time_governing_minimum"
    assert identity["tournament_id"] == "t-accepted"
    assert identity["interval"]["duration_minutes"] == "110"
    assert identity["authority"] == "calendar_event_association"


def test_changed_interval_does_not_inherit_accepted_exception():
    # The stored evidence describes 2026-02-01, but the candidate moved the
    # tournament: the exact fact match fails and the violation stays hard.
    candidate = {"tournaments": [_tournament("t-accepted", date="2026-02-08")]}
    classified = reclassify_accepted_exceptions(
        _problem(), candidate, [_floor_violation("t-accepted", 110)]
    )

    assert classified["accepted_exceptions"] == []
    assert [v["code"] for v in classified["blocking_violations"]] == [
        "ice_time_governing_minimum"
    ]


def test_unverified_below_floor_override_remains_blocking():
    candidate = {"tournaments": [_tournament("t-accepted")]}
    problem = {
        "teams": [_team("Jar 1"), _team("Jar 2"), _team("Jar 3")],
        "ice_time_minutes": {"U10": 120},
        "ice_time_minutes_overrides": {"t-accepted": 110},
    }

    classified = reclassify_accepted_exceptions(
        problem, candidate, [_floor_violation("t-accepted", 110)]
    )

    assert classified["accepted_exceptions"] == []
    assert [v["code"] for v in classified["blocking_violations"]] == [
        "ice_time_governing_minimum"
    ]


def test_source_interval_helper_requires_exact_current_interval():
    candidate = {"tournaments": [_tournament("t-accepted")]}
    problem = _problem()
    accepted_interval = {
        "date": "2026-02-01",
        "start_time": "10:00",
        "duration_minutes": 110,
        "end_time": "11:50",
    }

    matched = reclassify_accepted_source_interval(
        problem,
        candidate,
        [_floor_violation("t-accepted", 110)],
        tournament_id="t-accepted",
        accepted_interval=accepted_interval,
        authority="manual_club_confirmation",
    )
    assert matched["accepted_exceptions"]
    assert matched["blocking_violations"] == []
    assert matched["accepted_exceptions"][0]["accepted_exception"]["authority"] == (
        "manual_club_confirmation"
    )

    # A source interval that no longer equals the candidate's occupied interval
    # is never grandfathered.
    stale = {
        "date": "2026-02-01",
        "start_time": "10:00",
        "duration_minutes": 75,
        "end_time": "11:15",
    }
    rejected = reclassify_accepted_source_interval(
        problem,
        candidate,
        [_floor_violation("t-accepted", 110)],
        tournament_id="t-accepted",
        accepted_interval=stale,
        authority="manual_club_confirmation",
    )
    assert rejected["accepted_exceptions"] == []
    assert rejected["blocking_violations"]


def test_classification_distinguishes_accepted_debt_from_regressions():
    baseline = [
        _floor_violation("t-accepted", 110),
        _floor_violation("t-worse", 110),
        _floor_violation("t-resolved", 100),
    ]
    candidate = [
        _floor_violation("t-accepted", 110),
        _floor_violation("t-worse", 90),
        _floor_violation("t-new", 100),
    ]

    result = classify_candidate_violations(
        baseline,
        candidate,
        accepted_exception_violations=[_floor_violation("t-accepted", 110)],
    )

    assert result["acceptable"] is False
    assert {v["tournament_id"] for v in result["unchanged_accepted"]} == {"t-accepted"}
    assert {v["tournament_id"] for v in result["worsened"]} == {"t-worse"}
    assert {v["tournament_id"] for v in result["introduced"]} == {"t-new"}
    assert {v["tournament_id"] for v in result["resolved"]} == {"t-resolved"}
    assert {v["tournament_id"] for v in result["regressions"]} == {"t-worse", "t-new"}


def test_classification_allows_unchanged_accepted_but_not_unaccepted_debt():
    baseline = [_floor_violation("t-accepted", 110), _floor_violation("t-debt", 100)]
    candidate = [_floor_violation("t-accepted", 110), _floor_violation("t-debt", 100)]

    accepted_only = classify_candidate_violations(
        [_floor_violation("t-accepted", 110)],
        [_floor_violation("t-accepted", 110)],
        accepted_exception_violations=[_floor_violation("t-accepted", 110)],
    )
    assert accepted_only["acceptable"] is True
    assert accepted_only["unchanged_accepted"]

    with_unaccepted_debt = classify_candidate_violations(
        baseline,
        candidate,
        accepted_exception_violations=[_floor_violation("t-accepted", 110)],
    )
    assert with_unaccepted_debt["acceptable"] is False
    assert {v["tournament_id"] for v in with_unaccepted_debt["unchanged_unaccepted"]} == {
        "t-debt"
    }
    assert not with_unaccepted_debt["regressions"]


def test_classification_flags_materially_modified_facts_as_regression():
    baseline = [_floor_violation("t-accepted", 110)]
    moved = {**_floor_violation("t-accepted", 110), "date": "2026-02-08"}

    result = classify_candidate_violations(baseline, [moved], accepted_exception_violations=[moved])

    assert result["acceptable"] is False
    assert {v["tournament_id"] for v in result["materially_modified"]} == {"t-accepted"}
    assert result["unchanged_accepted"] == []


def test_unrelated_mutation_reports_unchanged_accepted_exception():
    baseline_plan = {
        "tournaments": [_tournament("t-accepted"), _tournament("t-other", date="2026-03-01")]
    }
    candidate_plan = {
        "tournaments": [_tournament("t-accepted"), _tournament("t-other", date="2026-03-08")]
    }
    problem = _problem()

    baseline = verify_canonical_candidate(baseline_plan, problem)
    result = verify_canonical_candidate(
        candidate_plan, problem, baseline_verification=baseline
    )

    assert result["ok"] is True
    classification = result["violation_classification"]
    assert classification["acceptable"] is True
    assert len(classification["unchanged_accepted"]) == 1
    assert classification["regressions"] == []
    assert result["booking_feasibility_warnings"][0]["accepted_exception"]["tournament_id"] == (
        "t-accepted"
    )


def test_changed_accepted_interval_stays_hard_in_canonical_verification():
    candidate_plan = {"tournaments": [_tournament("t-accepted", date="2026-02-08")]}
    result = verify_canonical_candidate(candidate_plan, _problem())

    assert result["ok"] is False
    assert "ice_time_governing_minimum" in {v["code"] for v in result["violations"]}
    assert result["booking_feasibility_warnings"] == []


def test_classification_bucket_constants_are_stable():
    # Guards against accidental renames that downstream diagnostics rely on.
    assert {
        INTRODUCED,
        WORSENED,
        MATERIALLY_MODIFIED,
        UNCHANGED_ACCEPTED,
        UNCHANGED_UNACCEPTED,
    } == {
        "introduced",
        "worsened",
        "materially_modified",
        "unchanged_accepted",
        "unchanged_unaccepted",
    }
