"""Tests for the placed/provisional/unplaced placement-state model (#381).

The core contract: a tournament the deterministic search could not place, or
whose concrete placement provably collides with a trusted/fixed external
booking, is *unplaced* -- removed from ``plan.tournaments`` and retained only
as a stable ``unresolved_tournament_placements`` obligation. Provisional
(host-confirmation / calendar-unavailable) placements stay, visibly marked.
"""

from __future__ import annotations

import copy

from tournament_scheduler.placement_normalization import (
    PLACED,
    PROVISIONAL,
    REASON_ARENA_CONFLICT,
    REASON_CALENDAR_UNAVAILABLE,
    REASON_EXHAUSTED_SEARCH,
    REASON_FIXED_EXTERNAL_CONFLICT,
    REASON_HOST_CONFIRMATION_REQUIRED,
    UNPLACED,
    classify_tournament,
    demote_tournament_to_unplaced,
    normalize_unplaced_placements,
)

LEGACY_MARKER = "Ingen verifisert ledig istid for H 2026-01-10 — turneringen må plasseres manuelt."
CALENDAR_REASON = "Kalender utilgjengelig for H — istid må bookes/verifiseres manuelt."


def _team(club, label, age="U10"):
    return {"club": club, "label": label, "age_group": age}


def _games(teams):
    labels = [team["label"] for team in teams]
    games = []
    round_number = 1
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            games.append(
                {
                    "home": labels[i],
                    "away": labels[j],
                    "parallel_slot": 0,
                    "round_number": round_number,
                }
            )
            round_number += 1
    return games


def _tournament(tid, host, *, date="2026-01-10", arena="H Arena", start="10:00", **extra):
    teams = [_team(host, f"{host}1"), _team("B", "B1"), _team("C", "C1"), _team("D", "D1")]
    tournament = {
        "id": tid,
        "date": date,
        "age_group": "U10",
        "host_club": host,
        "arena": arena,
        "start_time": start,
        "duration_minutes": 90,
        "teams": teams,
        "games": _games(teams),
    }
    tournament.update(extra)
    return tournament


def _problem(**overrides):
    problem = {
        "ice_time_minutes": {"U10": 90},
        "club_calendar_status": {"H": "known"},
        "club_busy_intervals": {},
    }
    problem.update(overrides)
    return problem


def _fixed_busy(host="H", date="2026-01-10", start="10:30", end="12:00"):
    return {
        host: [
            {
                "date": date,
                "start": start,
                "end": end,
                "availability": "fixed_busy",
                "kind": "external",
                "calendar_event": "U18 kamp",
            }
        ]
    }


class TestClassifyTournament:
    def test_verified_free_placement_is_placed(self):
        verdict = classify_tournament(_tournament("t1", "H"), _problem())
        assert verdict["state"] == PLACED

    def test_movable_host_confirmation_is_provisional(self):
        tournament = _tournament("t1", "H", requires_host_confirmation=True)
        verdict = classify_tournament(tournament, _problem())
        assert verdict["state"] == PROVISIONAL
        assert verdict["reason"] == REASON_HOST_CONFIRMATION_REQUIRED

    def test_calendar_unavailable_is_provisional(self):
        tournament = _tournament("t1", "H", manual_booking_reason=CALENDAR_REASON)
        verdict = classify_tournament(tournament, _problem())
        assert verdict["state"] == PROVISIONAL
        assert verdict["reason"] == REASON_CALENDAR_UNAVAILABLE

    def test_exhausted_search_marker_is_unplaced(self):
        tournament = _tournament("t1", "H", manual_booking_reason=LEGACY_MARKER)
        verdict = classify_tournament(tournament, _problem())
        assert verdict["state"] == UNPLACED
        assert verdict["reason"] == REASON_EXHAUSTED_SEARCH

    def test_fixed_external_conflict_is_unplaced(self):
        problem = _problem(club_busy_intervals=_fixed_busy())
        verdict = classify_tournament(_tournament("t1", "H"), problem)
        assert verdict["state"] == UNPLACED
        assert verdict["reason"] == REASON_FIXED_EXTERNAL_CONFLICT
        assert verdict["evidence"]["arena"] == "H Arena"

    def test_movable_interval_is_not_a_fixed_conflict(self):
        problem = _problem(
            club_busy_intervals={
                "H": [
                    {
                        "date": "2026-01-10",
                        "start": "10:30",
                        "end": "12:00",
                        "availability": "movable_busy",
                        "kind": "club_controlled",
                    }
                ]
            }
        )
        verdict = classify_tournament(_tournament("t1", "H"), problem)
        assert verdict["state"] == PLACED

    def test_operator_approved_conflict_stays_placed(self):
        problem = _problem(club_busy_intervals=_fixed_busy())
        verdict = classify_tournament(
            _tournament("t1", "H"),
            problem,
            approvals={"t1": {"status": "approved", "placement_locked": True}},
        )
        assert verdict["state"] == PLACED

    def test_missing_start_time_without_marker_is_left_for_the_verifier(self):
        tournament = _tournament("t1", "H")
        tournament["start_time"] = None
        verdict = classify_tournament(tournament, _problem())
        assert verdict["state"] == PLACED


class TestNormalizeUnplacedPlacements:
    def test_removes_only_unplaced_and_keeps_provisional(self):
        plan = {
            "tournaments": [
                _tournament("free", "H"),
                _tournament("provisional", "H", manual_booking_reason=CALENDAR_REASON),
                _tournament("conflict", "H", date="2026-01-17"),
                _tournament("exhausted", "H", date="2026-01-24", manual_booking_reason=LEGACY_MARKER),
            ]
        }
        problem = _problem(club_busy_intervals=_fixed_busy(date="2026-01-17"))

        report = normalize_unplaced_placements(plan, problem)

        assert report["changed"] is True
        assert {t["id"] for t in plan["tournaments"]} == {"free", "provisional"}
        assert {entry["tournament_id"] for entry in report["unplaced"]} == {"conflict", "exhausted"}
        assert [entry["tournament_id"] for entry in report["provisional"]] == ["provisional"]
        assert len(plan["unresolved_tournament_placements"]) == 2

    def test_obligations_have_stable_ids_and_search_evidence(self):
        plan = {
            "tournaments": [
                _tournament("exhausted", "H", date="2026-01-24", manual_booking_reason=LEGACY_MARKER)
            ]
        }
        normalize_unplaced_placements(plan, _problem())
        obligation = plan["unresolved_tournament_placements"][0]
        assert obligation["id"] == "unplaced_placement:U10:2026-01-24:1"
        assert obligation["responsible_host"] == "H"
        assert obligation["participant_teams"]
        assert obligation["required_duration_minutes"]
        assert obligation["reason"] == REASON_EXHAUSTED_SEARCH

    def test_reuses_existing_obligation_and_keeps_richer_search_evidence(self):
        plan = {
            "tournaments": [
                _tournament("exhausted", "H", date="2026-01-24", manual_booking_reason=LEGACY_MARKER)
            ],
            "unresolved_tournament_placements": [
                {
                    "age_group": "U10",
                    "date": "2026-01-24",
                    "candidate_hosts": ["H", "B", "C"],
                    "search_hosts_tried": ["H", "B"],
                    "alternate_roster_attempted": True,
                    "category": "manual_tournament_placement",
                }
            ],
        }
        normalize_unplaced_placements(plan, _problem())
        assert len(plan["unresolved_tournament_placements"]) == 1
        obligation = plan["unresolved_tournament_placements"][0]
        assert obligation["search_hosts_tried"] == ["H", "B"]
        assert obligation["alternate_roster_attempted"] is True
        assert obligation["responsible_host"] == "H"
        assert obligation["id"] == "unplaced_placement:U10:2026-01-24:1"

    def test_parallel_obligations_get_distinct_sequences(self):
        plan = {
            "tournaments": [
                _tournament("a", "H", date="2026-01-24", manual_booking_reason=LEGACY_MARKER),
                _tournament("b", "B", date="2026-01-24", manual_booking_reason=LEGACY_MARKER),
            ]
        }
        normalize_unplaced_placements(plan, _problem())
        ids = sorted(entry["id"] for entry in plan["unresolved_tournament_placements"])
        assert ids == [
            "unplaced_placement:U10:2026-01-24:1",
            "unplaced_placement:U10:2026-01-24:2",
        ]

    def test_noop_plan_reports_no_change(self):
        plan = {"tournaments": [_tournament("free", "H")]}
        report = normalize_unplaced_placements(plan, _problem())
        assert report["changed"] is False
        assert [t["id"] for t in plan["tournaments"]] == ["free"]


class TestDemoteTournamentToUnplaced:
    def test_moves_one_tournament_into_an_obligation(self):
        plan = {"tournaments": [_tournament("keep", "H"), _tournament("lose", "H", date="2026-01-17")]}
        obligation = demote_tournament_to_unplaced(
            plan, "lose", reason=REASON_ARENA_CONFLICT, ice_time_for_age_group={"U10": 90}
        )
        assert obligation is not None
        assert obligation["reason"] == REASON_ARENA_CONFLICT
        assert [t["id"] for t in plan["tournaments"]] == ["keep"]
        assert plan["unresolved_tournament_placements"][0]["id"] == "unplaced_placement:U10:2026-01-17:1"

    def test_unknown_tournament_is_a_noop(self):
        plan = {"tournaments": [_tournament("keep", "H")]}
        assert demote_tournament_to_unplaced(plan, "missing", reason=REASON_ARENA_CONFLICT) is None
        assert [t["id"] for t in plan["tournaments"]] == ["keep"]

    def test_demotion_does_not_mutate_the_caller_candidate(self):
        plan = {"tournaments": [_tournament("lose", "H", date="2026-01-17")]}
        before = copy.deepcopy(plan)
        demote_tournament_to_unplaced(plan, "lose", reason=REASON_ARENA_CONFLICT)
        assert plan != before
