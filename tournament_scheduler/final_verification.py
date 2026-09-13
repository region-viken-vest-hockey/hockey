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


def _check_games(tournament: dict[str, Any]) -> list[dict[str, Any]]:
    """Check that exported games are exactly one round-robin over participants."""
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

    expected = {tuple(sorted(pair)) for pair in combinations(labels, 2)}
    actual: Counter[tuple[str, str]] = Counter()
    used_by_round: dict[int, set[str]] = defaultdict(set)

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
    if missing:
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
    violations = [dict(item) for item in (result.get("violations") or [])]

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
        violations.extend(_check_games(tournament))

    result["violations"] = violations
    result["ok"] = not violations
    readiness = publication_readiness(result)
    result["publication_readiness"] = readiness
    result["publishable"] = readiness["publishable"]
    return result
