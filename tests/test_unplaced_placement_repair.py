"""Materialization repair/search for genuine unplaced placement obligations.

An unresolved obligation has a responsible host, roster, source date and shape
but no ``Tournament``. These tests exercise the repository-owned repair ladder
that turns it into a verified tournament (same-date start time, same-host
date, participant reselection and coupled capacity release), the search-coverage
reporting that keeps ``search_incomplete`` distinct from a claimed exhaustion,
and the canonical promoted-season apply boundary.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tournament_scheduler.planning_contract import build_planning_problem, verify_candidate
from tournament_scheduler.season_maintenance import (
    apply_repair,
    list_findings,
    load_context,
    repair_options,
    search,
)
from tournament_scheduler.season_state import schedule_fingerprint
from tournament_scheduler.unplaced_placement_repair import (
    SEARCH_BOUNDED_EXHAUSTED,
    SEARCH_INCOMPLETE,
    SEARCH_OPTION_AVAILABLE,
    enumerate_unplaced_placement_repairs,
)

YEAR = "2026-2027"


def _teams(clubs: Iterable[str], age_group: str = "U10") -> List[Dict[str, str]]:
    return [
        {"club": club, "label": f"{club} {index}", "age_group": age_group}
        for club in clubs
        for index in (1, 2)
    ]


def _problem(
    teams: List[Dict[str, str]],
    *,
    start: date,
    end: date,
    busy: Optional[Dict[str, List[Dict[str, str]]]] = None,
) -> Dict[str, Any]:
    clubs = sorted({team["club"] for team in teams})
    config = {
        "teams": teams,
        "age_groups": sorted({team["age_group"] for team in teams}),
        "parallel_games": {"U10": 2, "U12": 2, "JU10": 2},
        "round_length_minutes": {"U10": 30, "U12": 30, "JU10": 30},
        "ice_time_minutes": {"U10": 90, "U12": 90, "JU10": 90},
        "rounds_per_tournament": {"U10": 3, "U12": 3, "JU10": 3},
    }
    problem = build_planning_problem(config, None, start, end)
    problem["clubs"] = {club: f"{club} Arena" for club in clubs}
    problem["club_calendar_status"] = {club: "known" for club in clubs}
    if busy:
        problem["club_busy_intervals"] = busy
    return problem


def _tournament(
    tournament_id: str,
    day: str,
    host: str,
    teams: List[Dict[str, str]],
    *,
    start_time: str = "10:00",
    age_group: str = "U10",
) -> Dict[str, Any]:
    labels = [team["label"] for team in teams]
    games = [
        {
            "home": labels[index],
            "away": labels[(index + 1) % len(labels)],
            "parallel_slot": 0,
            "round_number": 1,
        }
        for index in range(len(labels))
    ]
    return {
        "id": tournament_id,
        "date": day,
        "arena": f"{host} Arena",
        "age_group": age_group,
        "host_club": host,
        "teams": [dict(team) for team in teams],
        "games": games,
        "start_time": start_time,
    }


def _obligation(
    *,
    age_group: str,
    day: str,
    host: str,
    roster: List[Dict[str, str]],
    obligation_id: Optional[str] = None,
    source_tournament_id: str = "rvv-9001",
) -> Dict[str, Any]:
    return {
        "id": obligation_id or f"unplaced_placement:{age_group}:{day}:1",
        "age_group": age_group,
        "date": day,
        "period": "before_christmas",
        "responsible_host": host,
        "participant_teams": [dict(team) for team in roster],
        "participant_team_count": len(roster),
        "required_duration_minutes": 90,
        "category": "manual_tournament_placement",
        "search_attempted": True,
        "bounded_repair_exhausted": True,
        "reason": "no_participant_host_slot",
        "source_tournament_id": source_tournament_id,
    }


def _write_season(root: Path, plan: Dict[str, Any], problem: Dict[str, Any]) -> str:
    season_dir = root / YEAR
    season_dir.mkdir(parents=True, exist_ok=True)
    revision = schedule_fingerprint(plan)
    schedule = {
        "schema_version": 1,
        "season": YEAR,
        "revision": revision,
        "fingerprint": revision,
        "plan_schema_version": 1,
        "plan": plan,
        "verification_context": {"problem": problem},
    }
    decisions = {
        "schema_version": 1,
        "season": YEAR,
        "schedule_fingerprint": revision,
        "decisions": {
            tournament["id"]: {
                "status": "pending_review",
                "placement_locked": False,
                "participants_locked": False,
                "approved_fingerprint": None,
            }
            for tournament in plan["tournaments"]
        },
    }
    (season_dir / "schedule.json").write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (season_dir / "decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return revision


def _base_plan(
    tournaments: List[Dict[str, Any]],
    obligation: Dict[str, Any],
    *,
    start: str,
    end: str,
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "start_date": start,
        "end_date": end,
        "tournaments": tournaments,
        "unresolved_tournament_placements": [obligation],
    }


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def test_materialization_builds_verified_tournament_and_removes_obligation(tmp_path: Path) -> None:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams, start=date(2026, 10, 1), end=date(2026, 10, 31))
    plan = _base_plan(
        [],
        _obligation(
            age_group="U10",
            day="2026-10-10",
            host="Sorby",
            roster=teams,
        ),
        start="2026-10-01",
        end="2026-10-31",
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    report = repair_options(YEAR, "unplaced_placement:U10:2026-10-10:1", root=root)

    assert report["option_count"] >= 1
    option = report["options"][0]
    assert option["family"] == "unplaced_placement"
    assert option["action"] == "materialize_same_date_start_time"
    assert option["arguments"]["date"] == "2026-10-10"
    # Hosting responsibility is preserved exactly, never transferred.
    assert option["evidence"]["responsible_host"] == "Sorby"

    result = apply_repair(
        YEAR,
        option["option_id"],
        report["revision"],
        root=root,
        finding_id="unplaced_placement:U10:2026-10-10:1",
    )

    assert result["ok"] is True, result
    assert result["delta"]["unresolved_placement_obligations_before"] == 1
    assert result["delta"]["unresolved_placement_obligations_after"] == 0
    assert result["delta"]["hard_violations_after"] == 0
    _schedule, _decisions, persisted, problem_loaded = load_context(YEAR, root=root)
    assert len(persisted["tournaments"]) == 1
    assert persisted.get("unresolved_tournament_placements") == []
    materialized = persisted["tournaments"][0]
    assert materialized["host_club"] == "Sorby"
    assert materialized["age_group"] == "U10"
    assert materialized["arena"] == "Sorby Arena"
    assert materialized["games"]
    assert verify_candidate(persisted, problem_loaded)["ok"]


def test_materialization_option_is_bound_to_candidate_fingerprint(tmp_path: Path) -> None:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams, start=date(2026, 10, 1), end=date(2026, 10, 31))
    plan = _base_plan(
        [],
        _obligation(age_group="U10", day="2026-10-10", host="Sorby", roster=teams),
        start="2026-10-01",
        end="2026-10-31",
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)
    report = repair_options(YEAR, "unplaced_placement:U10:2026-10-10:1", root=root)

    stale = apply_repair(
        YEAR,
        report["options"][0]["option_id"],
        "not-the-current-revision",
        root=root,
        finding_id="unplaced_placement:U10:2026-10-10:1",
    )

    assert stale["ok"] is False
    assert stale["reason"] == "stale_canonical_revision"
    _schedule, _decisions, persisted, _problem_loaded = load_context(YEAR, root=root)
    assert persisted.get("unresolved_tournament_placements")


# ---------------------------------------------------------------------------
# Participant reselection
# ---------------------------------------------------------------------------


def test_participant_reselection_materializes_when_roster_already_plays(tmp_path: Path) -> None:
    teams = _teams(["Nordby", "Vestby"]) + [
        {"club": "Sorby", "label": f"Sorby {index}", "age_group": "U10"}
        for index in (1, 2, 3, 4)
    ]
    problem = _problem(teams, start=date(2026, 10, 10), end=date(2026, 10, 10))
    roster = [team for team in teams if team["club"] == "Nordby"] + [
        team for team in teams if team["club"] == "Sorby" and team["label"] in ("Sorby 1", "Sorby 2")
    ]
    # The obligation roster already plays a tournament on its source date, so
    # only a host-representing replacement roster can materialize it.
    collision = _tournament("COL", "2026-10-10", "Ekstern", roster)
    plan = _base_plan(
        [collision],
        _obligation(age_group="U10", day="2026-10-10", host="Sorby", roster=roster),
        start="2026-10-10",
        end="2026-10-10",
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    report = repair_options(YEAR, "unplaced_placement:U10:2026-10-10:1", root=root)

    reselected = [
        option
        for option in report["options"]
        if option["arguments"]["roster_source"] == "alternate"
    ]
    assert reselected, report["options"]
    result = apply_repair(
        YEAR,
        reselected[0]["option_id"],
        report["revision"],
        root=root,
        finding_id="unplaced_placement:U10:2026-10-10:1",
    )
    assert result["ok"] is True, result
    _schedule, _decisions, persisted, problem_loaded = load_context(YEAR, root=root)
    materialized = next(t for t in persisted["tournaments"] if t["id"] != "COL")
    assert any(team["club"] == "Sorby" for team in materialized["teams"])
    assert verify_candidate(persisted, problem_loaded)["ok"]


def test_participant_reselection_applies_to_a_colliding_alternate_date(tmp_path: Path) -> None:
    """Reselection is date-aware, not gated on the source date alone.

    The source date has no roster collision (its ice is simply booked), but an
    alternate date does. Reselection can only help on the colliding alternate
    date, yet the old applicability check looked only at the source date and so
    never attempted the dimension anywhere -- the obligation stayed
    ``search_incomplete`` forever. A host-representing replacement roster must
    materialize on the colliding alternate date.
    """
    teams = _teams(["Nordby", "Vestby"]) + [
        {"club": "Sorby", "label": f"Sorby {index}", "age_group": "U10"}
        for index in (1, 2, 3, 4)
    ]
    problem = _problem(
        teams,
        start=date(2026, 10, 10),
        end=date(2026, 10, 11),
        busy={
            "Sorby": [
                # Source date: the responsible host's ice is booked all day.
                {"date": "2026-10-10", "start": "00:00", "end": "23:59", "calendar_event": "Booked"}
            ]
        },
    )
    roster = [team for team in teams if team["club"] in ("Nordby", "Sorby")][:4]
    # The obligation's current roster already plays on the alternate date, so
    # neither the current-roster same-host-date pass nor the source date can
    # place it; only a host-representing replacement roster on 2026-10-11 can.
    collision = _tournament("COL", "2026-10-11", "Ekstern", roster)
    plan = _base_plan(
        [collision],
        _obligation(age_group="U10", day="2026-10-10", host="Sorby", roster=roster),
        start="2026-10-10",
        end="2026-10-11",
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    report = search(YEAR, "unplaced_placement:U10:2026-10-10:1", root=root)

    reselected = [
        option
        for option in report["options"]
        if option["arguments"]["roster_source"] == "alternate"
        and option["arguments"]["date"] == "2026-10-11"
    ]
    assert reselected, report["options"]
    coverage = report["finding"]["search_coverage"]
    assert coverage["status"] == SEARCH_OPTION_AVAILABLE
    assert "participant_reselection" in coverage["attempted"]

    result = apply_repair(
        YEAR,
        reselected[0]["option_id"],
        report["revision"],
        root=root,
        finding_id="unplaced_placement:U10:2026-10-10:1",
    )
    assert result["ok"] is True, result
    _schedule, _decisions, persisted, problem_loaded = load_context(YEAR, root=root)
    materialized = next(t for t in persisted["tournaments"] if t["id"] != "COL")
    assert materialized["date"] == "2026-10-11"
    assert any(team["club"] == "Sorby" for team in materialized["teams"])
    assert verify_candidate(persisted, problem_loaded)["ok"]


def test_inapplicable_reselection_terminates_search_coverage(tmp_path: Path) -> None:
    """A structurally inapplicable dimension is resolved, not forever-untried.

    When the roster collides on no tried date, participant reselection has an
    unmet hard precondition and can never run. Reporting it as ``untried`` kept
    the obligation at ``search_incomplete`` on every request, inviting an
    unbounded retry loop. It must be reported as ``inapplicable`` and excluded
    from ``untried`` so the bounded search terminates as
    ``bounded_search_exhausted``.
    """
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(
        teams,
        start=date(2026, 10, 10),
        end=date(2026, 10, 10),
        busy={
            "Sorby": [
                {"date": "2026-10-10", "start": "00:00", "end": "23:59", "calendar_event": "Booked"}
            ]
        },
    )
    plan = _base_plan(
        [],
        _obligation(age_group="U10", day="2026-10-10", host="Sorby", roster=teams),
        start="2026-10-10",
        end="2026-10-10",
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    result = enumerate_unplaced_placement_repairs(
        plan,
        problem,
        finding_ids=["unplaced_placement:U10:2026-10-10:1"],
        allow_search=True,
    )

    coverage = result["coverage"]["unplaced_placement:U10:2026-10-10:1"]
    assert result["options"] == []
    assert coverage["status"] == SEARCH_BOUNDED_EXHAUSTED
    assert coverage["untried"] == []
    assert coverage["inapplicable"] == ["participant_reselection"]
    assert coverage["proven_infeasible"] is False
    # The canonical search view no longer advertises the dimension as a
    # still-retryable direction once the search has resolved it.
    bounded = search(YEAR, "unplaced_placement:U10:2026-10-10:1", root=root)
    assert bounded["option_count"] == 0
    assert bounded["finding"]["search_coverage"]["status"] == SEARCH_BOUNDED_EXHAUSTED
    assert bounded["finding"]["search_coverage"]["inapplicable"] == [
        "participant_reselection"
    ]
    assert bounded["escalation"]["reason"] == SEARCH_BOUNDED_EXHAUSTED
    assert bounded["escalation"]["untried_dimensions"] == []


# ---------------------------------------------------------------------------
# Coupled capacity release (cross-age)
# ---------------------------------------------------------------------------


def _coupled_fixture() -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Jar-style fixture: a JU10 blocker consumes the arena needed by U12.

    The obligation can only be placed once the cross-age blocker is moved off
    the shared arena/date, which is exactly the coupled improvement the
    production evidence was missing.
    """
    teams = _teams(["Jar", "Frisk Asker"], "U12") + _teams(["Jar", "Holmen"], "JU10")
    problem = _problem(
        teams,
        start=date(2027, 1, 10),
        end=date(2027, 1, 17),
        busy={
            "Jar": [
                {
                    "date": "2027-01-10",
                    "start": "08:00",
                    "end": "20:00",
                    "calendar_event": "Booked",
                }
            ]
        },
    )
    # The blocker must own the whole widened same-day start-time search window
    # (not just the first couple of candidate times), otherwise the cheap pass
    # finds a real gap right after the blocker's own game and this fixture stops
    # exercising the coupled capacity-release path it is for. 480 minutes keeps
    # the shared Jar arena busy from the blocker's start through the latest
    # generated start (16:00) plus the obligation's own duration.
    problem["ice_time_minutes"] = dict(problem["ice_time_minutes"])
    problem["ice_time_minutes"]["JU10"] = 480
    obligation_age = "U12"
    blocker_age = "JU10"
    blocker = _tournament(
        "rvv-0098",
        "2027-01-16",
        "Jar",
        _teams(["Jar", "Holmen"], blocker_age),
        age_group=blocker_age,
    )
    occupied_15 = _tournament(
        "O15",
        "2027-01-17",
        "Vestby",
        _teams(["Jar", "Frisk Asker"], "U12"),
        age_group="U12",
    )
    roster = _teams(["Jar", "Frisk Asker"], obligation_age)
    obligation = _obligation(
        age_group=obligation_age,
        day="2027-01-10",
        host="Jar",
        roster=roster,
        obligation_id="unplaced_placement:U12:2027-01-10:1",
        source_tournament_id="rvv-9200",
    )
    plan = _base_plan(
        [blocker, occupied_15],
        obligation,
        start="2027-01-10",
        end="2027-01-17",
    )
    return plan, problem


def test_capacity_release_crosses_age_groups_when_arena_is_shared(tmp_path: Path) -> None:
    plan, problem = _coupled_fixture()
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    # The cheap pass cannot place the obligation: the JU10 blocker owns the
    # Jar arena on the only usable date.
    cheap = repair_options(YEAR, "unplaced_placement:U12:2027-01-10:1", root=root)
    assert cheap["option_count"] == 0
    assert cheap["finding"]["search_coverage"]["status"] == SEARCH_INCOMPLETE

    bounded = search(YEAR, "unplaced_placement:U12:2027-01-10:1", root=root)

    assert bounded["option_count"] >= 1
    coupled = [
        option
        for option in bounded["options"]
        if option["action"] == "materialize_after_capacity_release"
    ]
    assert coupled, bounded["options"]
    chosen = coupled[0]
    assert chosen["arguments"]["moves"]
    assert chosen["arguments"]["moves"][0]["tournament_id"] == "rvv-0098"

    result = apply_repair(
        YEAR,
        chosen["option_id"],
        bounded["revision"],
        root=root,
        finding_id="unplaced_placement:U12:2027-01-10:1",
    )
    assert result["ok"] is True, result
    # Both the blocker move and the materialization are committed atomically.
    assert result["delta"]["changed_tournament_count"] == 2
    assert result["delta"]["unresolved_placement_obligations_after"] == 0
    assert result["delta"]["hard_violations_after"] == 0
    _schedule, _decisions, persisted, problem_loaded = load_context(YEAR, root=root)
    blocker_after = next(t for t in persisted["tournaments"] if t["id"] == "rvv-0098")
    assert blocker_after["host_club"] == "Jar"
    materialized = next(t for t in persisted["tournaments"] if t["id"] == "rvv-9200")
    assert materialized["host_club"] == "Jar"
    assert verify_candidate(persisted, problem_loaded)["ok"]


# ---------------------------------------------------------------------------
# Search coverage
# ---------------------------------------------------------------------------


def test_findings_expose_attempted_and_untried_dimensions(tmp_path: Path) -> None:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams, start=date(2026, 10, 1), end=date(2026, 10, 31))
    plan = _base_plan(
        [],
        _obligation(age_group="U10", day="2026-10-10", host="Sorby", roster=teams),
        start="2026-10-01",
        end="2026-10-31",
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    findings = list_findings(YEAR, root=root)
    finding = next(
        entry for entry in findings["findings"] if entry["code"] == "unplaced_tournament_placement"
    )
    coverage = finding["search_coverage"]

    # The planner's own bounded_repair_exhausted flag must not read as global
    # infeasibility: untried capability dimensions are reported explicitly.
    assert coverage["status"] == SEARCH_INCOMPLETE
    assert coverage["untried"]
    assert coverage["proven_infeasible"] is False
    assert "capacity_release" in coverage["untried"]


def test_search_reports_option_available_and_searches_untried_dimensions(tmp_path: Path) -> None:
    plan, problem = _coupled_fixture()
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    bounded = search(YEAR, "unplaced_placement:U12:2027-01-10:1", root=root)

    coverage = bounded["finding"]["search_coverage"]
    assert coverage["status"] == SEARCH_OPTION_AVAILABLE
    assert coverage["proven_infeasible"] is False


def test_search_incomplete_is_distinct_from_bounded_search_exhausted(tmp_path: Path) -> None:
    # No free arena and a roster conflict on every date means the search runs
    # and finds nothing. It must report bounded_search_exhausted, not
    # proven_infeasible and not search_incomplete once every applicable
    # dimension ran.
    teams = _teams(["Nordby", "Sorby", "Vestby"])
    problem = _problem(teams, start=date(2026, 10, 10), end=date(2026, 10, 10))
    roster = [team for team in teams if team["club"] in ("Nordby", "Sorby")]
    busy = {
        "Sorby": [
            {
                "date": "2026-10-10",
                "start": "00:00",
                "end": "23:59",
                "calendar_event": "Booked",
            }
        ]
    }
    problem["club_busy_intervals"] = busy
    collision = _tournament("COL", "2026-10-10", "Ekstern", roster)
    plan = _base_plan(
        [collision],
        _obligation(age_group="U10", day="2026-10-10", host="Sorby", roster=roster),
        start="2026-10-10",
        end="2026-10-10",
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    result = enumerate_unplaced_placement_repairs(
        plan,
        problem,
        finding_ids=["unplaced_placement:U10:2026-10-10:1"],
        allow_search=True,
    )

    coverage = result["coverage"]["unplaced_placement:U10:2026-10-10:1"]
    assert result["options"] == []
    assert coverage["status"] == SEARCH_BOUNDED_EXHAUSTED
    assert coverage["untried"] == []
    assert coverage["proven_infeasible"] is False


def test_same_host_date_search_reaches_a_genuinely_open_date_beyond_the_nearest_few(
    tmp_path: Path,
) -> None:
    # Real-world case: the responsible host's arena is booked solid on the
    # source date and every one of the nearest few same-half weekends, but a
    # weekend more than 8 (the old bounded cap) weekends out is completely
    # open. The bounded same_host_date search must actually reach it instead
    # of reporting bounded_search_exhausted merely because it never looked
    # that far.
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams, start=date(2026, 10, 1), end=date(2026, 11, 30))
    # Every same-half weekend date in the window except 2026-11-21 -- the
    # only genuinely open date, and one the old 8-nearest-dates cap would
    # never have reached from the 2026-10-18 source date.
    blocked_dates = [
        "2026-10-03",
        "2026-10-04",
        "2026-10-10",
        "2026-10-11",
        "2026-10-17",
        "2026-10-18",  # source date
        "2026-10-24",
        "2026-10-25",
        "2026-10-31",
        "2026-11-01",
        "2026-11-07",
        "2026-11-08",
        "2026-11-14",
        "2026-11-15",
        "2026-11-22",
        "2026-11-28",
        "2026-11-29",
    ]
    problem["club_busy_intervals"] = {
        "Sorby": [
            {"date": day, "start": "00:00", "end": "23:59", "calendar_event": "Booked"}
            for day in blocked_dates
        ]
    }
    plan = _base_plan(
        [],
        _obligation(age_group="U10", day="2026-10-18", host="Sorby", roster=teams),
        start="2026-10-01",
        end="2026-11-30",
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    result = enumerate_unplaced_placement_repairs(
        plan,
        problem,
        finding_ids=["unplaced_placement:U10:2026-10-18:1"],
        allow_search=False,
    )

    options = result["options"]
    assert options, result["coverage"]
    dates_offered = {option["arguments"]["date"] for option in options}
    assert "2026-11-21" in dates_offered
    assert all(day not in dates_offered for day in blocked_dates)


# ---------------------------------------------------------------------------
# Start-time sampling reaches the whole allowed day (#392)
# ---------------------------------------------------------------------------


def test_alternate_date_afternoon_slot_is_found_within_the_start_time_cap(tmp_path: Path) -> None:
    """A capped alternate-date search must still reach a legal afternoon slot.

    The old ``[:6]`` slice of the ordered candidate list always tried morning
    starts (10:00-12:30) even though the generated window ran to 16:00, so an
    alternate date whose only gap began after lunch was reported as if it had
    no legal slot at all.
    """
    teams = _teams(["Jar", "Frisk Asker"])
    problem = _problem(
        teams,
        start=date(2026, 10, 10),
        end=date(2026, 10, 25),
        busy={
            "Jar": [
                # Source date has no gap at all.
                {"date": "2026-10-10", "start": "00:00", "end": "23:59", "calendar_event": "Kamp"},
                # The only usable alternate date is free from 15:00.
                {"date": "2026-10-17", "start": "00:00", "end": "14:30", "calendar_event": "Kamp"},
                {"date": "2026-10-18", "start": "00:00", "end": "14:30", "calendar_event": "Kamp"},
            ]
        },
    )
    plan = _base_plan(
        [],
        _obligation(age_group="U10", day="2026-10-10", host="Jar", roster=teams),
        start="2026-10-10",
        end="2026-10-25",
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    report = repair_options(YEAR, "unplaced_placement:U10:2026-10-10:1", root=root)

    afternoon = [
        option
        for option in report["options"]
        if option["arguments"]["date"] in {"2026-10-17", "2026-10-18"}
        and option["arguments"]["start_time"] in {"15:00", "15:30", "16:00"}
    ]
    assert afternoon, report["options"]
    chosen = afternoon[0]
    assert chosen["arguments"]["start_time"] <= "16:00"
    applied = apply_repair(
        YEAR,
        chosen["option_id"],
        report["revision"],
        root=root,
        finding_id="unplaced_placement:U10:2026-10-10:1",
    )
    assert applied["ok"] is True, applied


def test_alternate_date_latest_allowed_start_survives_preferred_time_insertion(
    tmp_path: Path,
) -> None:
    """The latest allowed start stays reachable for a non-sampled preferred start.

    Prepending the obligation's preferred start to a bounded, day-covering
    sample must not evict the last sampled endpoint. With preferred 12:00 (not
    itself one of the sampled values) a naive slice dropped 16:00, so an
    alternate date whose only legal generated start was exactly the latest
    allowed time was reported as having no legal slot.
    """
    teams = _teams(["Jar", "Frisk Asker"])
    problem = _problem(
        teams,
        start=date(2026, 10, 10),
        end=date(2026, 10, 25),
        busy={
            "Jar": [
                # Source date and every alternate date except 2026-10-11 have
                # no gap at all.
                {"date": "2026-10-10", "start": "00:00", "end": "23:59", "calendar_event": "Kamp"},
                {"date": "2026-10-17", "start": "00:00", "end": "23:59", "calendar_event": "Kamp"},
                {"date": "2026-10-18", "start": "00:00", "end": "23:59", "calendar_event": "Kamp"},
                {"date": "2026-10-24", "start": "00:00", "end": "23:59", "calendar_event": "Kamp"},
                {"date": "2026-10-25", "start": "00:00", "end": "23:59", "calendar_event": "Kamp"},
                # The only usable date is free only from 15:30, so the latest
                # allowed generated start (16:00) is the only legal slot.
                {"date": "2026-10-11", "start": "00:00", "end": "15:30", "calendar_event": "Kamp"},
            ]
        },
    )
    obligation = _obligation(age_group="U10", day="2026-10-10", host="Jar", roster=teams)
    obligation["preferred_start_time"] = "12:00"
    plan = _base_plan([], obligation, start="2026-10-10", end="2026-10-25")
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    report = repair_options(YEAR, "unplaced_placement:U10:2026-10-10:1", root=root)

    latest = [
        option
        for option in report["options"]
        if option["arguments"]["date"] == "2026-10-11"
        and option["arguments"]["start_time"] == "16:00"
    ]
    assert latest, report["options"]
    applied = apply_repair(
        YEAR,
        latest[0]["option_id"],
        report["revision"],
        root=root,
        finding_id="unplaced_placement:U10:2026-10-10:1",
    )
    assert applied["ok"] is True, applied


# ---------------------------------------------------------------------------
# Search-capability staleness (#392)
# ---------------------------------------------------------------------------


def test_bounded_exhaustion_marker_from_superseded_search_is_retryable(tmp_path: Path) -> None:
    """A planner exhaustion claim from an older search becomes stale/retryable.

    The marker records only the bounded search that produced it. When the
    repository widens the neighborhood, canonical maintenance must re-open the
    obligation instead of inheriting the superseded result.
    """
    from tournament_scheduler.search_capability import SearchCapability
    from tournament_scheduler.unplaced_placement_repair import (
        unplaced_placement_search_capability,
    )

    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams, start=date(2026, 10, 1), end=date(2026, 10, 31))
    obligation = _obligation(age_group="U10", day="2026-10-10", host="Sorby", roster=teams)
    obligation["bounded_repair_exhausted"] = True
    obligation["search_capability"] = SearchCapability(
        "unplaced_placement",
        "1",
        {"max_date_candidates": 8, "max_start_times_per_date": 2},
    ).to_dict()
    plan = _base_plan([], obligation, start="2026-10-01", end="2026-10-31")
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    report = list_findings(YEAR, root=root)
    finding = next(
        entry for entry in report["findings"] if entry["code"] == "unplaced_tournament_placement"
    )
    assert finding["bounded_repair_exhausted"] is True
    assert finding["bounded_repair_exhausted_stale"] is True
    coverage = finding["search_coverage"]
    assert coverage["status"] == SEARCH_INCOMPLETE
    assert coverage["capability_stale"] is True
    assert coverage["previous_status"] == SEARCH_BOUNDED_EXHAUSTED
    assert coverage["proven_infeasible"] is False
    assert coverage["capability"]["fingerprint"] == unplaced_placement_search_capability().fingerprint

    # The stale finding is eligible for the new search and the current search
    # produces a verified option.
    bounded = search(YEAR, finding["finding_id"], root=root)
    assert bounded["option_count"] >= 1
    assert bounded["finding"]["search_coverage"]["status"] == SEARCH_OPTION_AVAILABLE


def test_exhaustion_marker_from_current_search_is_not_stale(tmp_path: Path) -> None:
    from tournament_scheduler.unplaced_placement_repair import (
        unplaced_placement_search_capability,
    )

    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams, start=date(2026, 10, 1), end=date(2026, 10, 31))
    obligation = _obligation(age_group="U10", day="2026-10-10", host="Sorby", roster=teams)
    obligation["bounded_repair_exhausted"] = True
    obligation["search_capability"] = unplaced_placement_search_capability().to_dict()
    plan = _base_plan([], obligation, start="2026-10-01", end="2026-10-31")
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    finding = next(
        entry
        for entry in list_findings(YEAR, root=root)["findings"]
        if entry["code"] == "unplaced_tournament_placement"
    )
    assert finding["bounded_repair_exhausted_stale"] is False
    assert finding["search_coverage"]["capability_stale"] is False
    # The cheap listing still reports the supported ladder as untried; the
    # current marker is not itself an option-available claim.
    assert finding["search_coverage"]["status"] == SEARCH_INCOMPLETE
