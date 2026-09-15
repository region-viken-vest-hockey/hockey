"""Strict final verification layered on the stable planning contract.

The planning-contract verifier remains the planner-neutral contract used
during search. This module adds final/export-only checks that must not affect
optimizer semantics: tournament minimum size, generated game integrity, and
publication readiness.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from typing import Any

from tournament_scheduler.host_representation import constituent_clubs
from tournament_scheduler.limited_rounds import minimum_same_club_games_for_limited_rounds
from tournament_scheduler.models import Team
from tournament_scheduler.planning_contract import verify_candidate as _verify_candidate

MIN_TEAMS_PER_TOURNAMENT = 3


def _add(
    violations: list[dict[str, Any]],
    code: str,
    message: str,
    tournament_id: str,
) -> None:
    violations.append(
        {"code": code, "message": message, "tournament_id": tournament_id}
    )


def _check_games(
    tournament: dict[str, Any],
    problem: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Check that exported games match the configured tournament game contract."""
    tid = str(tournament.get("id") or "?")
    labels = [
        str(team.get("label") or "")
        for team in (tournament.get("teams") or [])
        if isinstance(team, dict)
    ]
    violations: list[dict[str, Any]] = []

    if any(not label for label in labels) or len(labels) != len(set(labels)):
        _add(
            violations,
            "game_integrity_ambiguous_participants",
            f"Tournament {tid} has missing or duplicate participant labels",
            tid,
        )
        return violations

    age_group = str(tournament.get("age_group") or "")
    rounds_per_tournament = (problem or {}).get("rounds_per_tournament") or {}
    configured_rounds = rounds_per_tournament.get(age_group)
    limited_rounds = isinstance(configured_rounds, int) and configured_rounds > 0
    expected = {tuple(sorted(pair)) for pair in combinations(labels, 2)}
    actual: Counter[tuple[str, str]] = Counter()
    used_by_round: dict[int, set[str]] = defaultdict(set)
    same_club_count = 0
    team_by_label = {
        str(team.get("label") or ""): team
        for team in (tournament.get("teams") or [])
        if isinstance(team, dict)
    }

    for index, game in enumerate(tournament.get("games") or [], start=1):
        if not isinstance(game, dict):
            _add(
                violations,
                "invalid_game_record",
                f"Tournament {tid} game #{index} is not an object",
                tid,
            )
            continue

        home = str(game.get("home") or "")
        away = str(game.get("away") or "")
        if home == away or home not in labels or away not in labels:
            _add(
                violations,
                "game_team_not_participant",
                f"Tournament {tid} game #{index} has invalid teams {home!r}/{away!r}",
                tid,
            )
            continue

        actual[tuple(sorted((home, away)))] += 1
        home_club = str(team_by_label.get(home, {}).get("club") or "")
        away_club = str(team_by_label.get(away, {}).get("club") or "")
        if (
            home_club
            and away_club
            and set(constituent_clubs(home_club)) & set(constituent_clubs(away_club))
        ):
            same_club_count += 1
        round_number = game.get("round_number")
        if not isinstance(round_number, int) or round_number <= 0:
            _add(
                violations,
                "invalid_game_round",
                f"Tournament {tid} game #{index} has invalid round_number",
                tid,
            )
            continue

        if (
            home in used_by_round[round_number]
            or away in used_by_round[round_number]
        ):
            _add(
                violations,
                "team_double_booked_in_round",
                f"Tournament {tid} round {round_number} schedules one team twice",
                tid,
            )
        used_by_round[round_number].update((home, away))

    missing = expected - set(actual)
    repeated = {pair: count for pair, count in actual.items() if count > 1}
    parallel_limit = ((problem or {}).get("parallel_games") or {}).get(age_group)
    if isinstance(parallel_limit, int) and parallel_limit > 0:
        for round_number, used in used_by_round.items():
            games_in_round = sum(
                1
                for game in (tournament.get("games") or [])
                if isinstance(game, dict) and game.get("round_number") == round_number
            )
            if games_in_round > parallel_limit:
                _add(
                    violations,
                    "parallel_capacity_exceeded",
                    f"Tournament {tid} round {round_number} has {games_in_round} game(s); "
                    f"capacity is {parallel_limit}",
                    tid,
                )
    if limited_rounds:
        actual_rounds = {
            g.get("round_number")
            for g in (tournament.get("games") or [])
            if isinstance(g, dict)
        }
        if actual_rounds and max(actual_rounds) != configured_rounds:
            _add(
                violations,
                "configured_round_count_mismatch",
                f"Tournament {tid} has {max(actual_rounds)} round(s); configured for {configured_rounds}",
                tid,
            )
        if problem is not None:
            parallel = ((problem.get("parallel_games") or {}).get(age_group) or 1)
            teams = [
                Team(
                    club=str(t.get("club") or ""),
                    label=str(t.get("label") or ""),
                    age_group=age_group,
                )
                for t in (tournament.get("teams") or [])
                if isinstance(t, dict)
            ]
            min_same = minimum_same_club_games_for_limited_rounds(
                teams,
                int(parallel),
                int(configured_rounds),
            )
            if same_club_count > min_same:
                _add(
                    violations,
                    "avoidable_same_club_matchup",
                    f"Tournament {tid} has {same_club_count} same-club game(s); minimum is {min_same}",
                    tid,
                )
    elif missing:
        _add(
            violations,
            "round_robin_missing_pair",
            f"Tournament {tid} is missing {len(missing)} required matchup(s)",
            tid,
        )
    if repeated:
        _add(
            violations,
            "round_robin_duplicate_pair",
            f"Tournament {tid} repeats {len(repeated)} matchup(s)",
            tid,
        )
    return violations


def publication_readiness(result: dict[str, Any]) -> dict[str, Any]:
    """Separate structural validity from readiness for public publication."""
    violations = list(result.get("violations") or [])
    if violations:
        return {
            "status": "INVALID",
            "publishable": False,
            "reasons": [{"code": "hard_violations", "count": len(violations)}],
        }

    reasons: list[dict[str, Any]] = []
    skipped = list(result.get("skipped") or [])
    if skipped:
        reasons.append(
            {"code": "incomplete_verification", "count": len(skipped)}
        )

    for field, code in (
        ("unresolved_hosting_obligations", "unresolved_hosting"),
        ("manual_calendar_placements", "manual_calendar_placements"),
        ("manual_external_conflict_placements", "external_calendar_conflicts"),
        ("manual_participation_placements", "participation_shortfalls"),
    ):
        count = len(result.get(field) or [])
        if count:
            reasons.append({"code": code, "count": count})

    status = "REVIEW_REQUIRED" if reasons else "PUBLISHABLE"
    return {
        "status": status,
        "publishable": status == "PUBLISHABLE",
        "reasons": reasons,
    }


def verify_final_candidate(
    candidate: dict[str, Any],
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run base verification plus final minimum-size and game-integrity checks."""
    result = dict(_verify_candidate(candidate, problem))
    violations = [
        dict(item)
        for item in (result.get("violations") or [])
        if dict(item).get("code") != "bye_team_not_allowed"
    ]

    registered_by_age: Counter[str] = Counter()
    if problem is not None:
        registered_by_age.update(
            str(team.get("age_group") or "")
            for team in (problem.get("teams") or [])
            if isinstance(team, dict) and team.get("age_group")
        )

    for tournament in candidate.get("tournaments") or []:
        if not isinstance(tournament, dict) or tournament.get("cancelled"):
            continue
        tid = str(tournament.get("id") or "?")
        age_group = str(tournament.get("age_group") or "")
        team_count = len(tournament.get("teams") or [])
        if (
            registered_by_age.get(age_group, 0) >= MIN_TEAMS_PER_TOURNAMENT
            and team_count < MIN_TEAMS_PER_TOURNAMENT
        ):
            _add(
                violations,
                "tournament_under_minimum",
                f"Tournament {tid} has {team_count} team(s); minimum is {MIN_TEAMS_PER_TOURNAMENT}",
                tid,
            )
        violations.extend(_check_games(tournament, problem))

    result["violations"] = violations
    result["ok"] = not violations
    readiness = publication_readiness(result)
    result["publication_readiness"] = readiness
    result["publishable"] = readiness["publishable"]
    return result
