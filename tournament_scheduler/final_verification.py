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
from tournament_scheduler.effective_tournament_shape import compute_effective_tournament_shape
from tournament_scheduler.guest_slots import capacity_places, has_open_guest_slots
from tournament_scheduler.limited_rounds import minimum_same_club_games_for_limited_rounds
from tournament_scheduler.models import Team
from tournament_scheduler.participation_targets import INTRA_CLUB_DISTRIBUTION
from tournament_scheduler.planning_contract import verify_candidate as _verify_candidate
from tournament_scheduler.rule_catalog import annotate_violations

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
    # While a guest place is still open the pairings against that unknown team
    # cannot exist yet, so the games are explicitly provisional: participant
    # and round integrity of the games that DO exist is still verified, but a
    # missing pair or an unfinished round count is not a defect until the
    # slot is filled (or released).
    provisional = has_open_guest_slots(tournament)
    rounds_per_tournament = (problem or {}).get("rounds_per_tournament") or {}
    configured_rounds = rounds_per_tournament.get(age_group)
    limited_rounds = isinstance(configured_rounds, int) and configured_rounds > 0
    expected_limited_rounds = configured_rounds
    if limited_rounds and problem is not None:
        registered_count = sum(
            1
            for team in problem.get("teams", []) or []
            if isinstance(team, dict) and str(team.get("age_group") or "") == age_group
        )
        shape = compute_effective_tournament_shape(
            age_group,
            registered_count,
            configured_rounds=int(configured_rounds),
            parallel_game_capacity=((problem.get("parallel_games") or {}).get(age_group)),
        )
        expected_limited_rounds = shape.effective_round_count
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
        home_team = team_by_label.get(home, {})
        away_team = team_by_label.get(away, {})
        home_club = str(home_team.get("club") or "")
        away_club = str(away_team.get("club") or "")
        # A guest is not part of the RVV club-clustering picture, so a guest
        # sharing a club name with an RVV participant is not a same-club game.
        if (
            not home_team.get("guest")
            and not away_team.get("guest")
            and home_club
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
        if not provisional and actual_rounds and max(actual_rounds) != expected_limited_rounds:
            _add(
                violations,
                "configured_round_count_mismatch",
                f"Tournament {tid} has {max(actual_rounds)} round(s); expected {expected_limited_rounds} "
                f"for configured {configured_rounds}",
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
                int(expected_limited_rounds or configured_rounds),
            )
            if not provisional and same_club_count > min_same:
                _add(
                    violations,
                    "avoidable_same_club_matchup",
                    f"Tournament {tid} has {same_club_count} same-club game(s); minimum is {min_same}",
                    tid,
                )
    elif missing and not provisional:
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
    # An operator waiver never blocks export, but it must remain visible: a
    # plan that only passes because a hard rule was explicitly waived is not
    # the same as one that passes with no exception at all.
    waived = list(result.get("waived_violations") or [])
    if waived:
        reasons.append({"code": "operator_waivers", "count": len(waived)})
    skipped = list(result.get("skipped") or [])
    if skipped:
        reasons.append(
            {"code": "incomplete_verification", "count": len(skipped)}
        )

    for field, code in (
        ("unresolved_hosting_obligations", "unresolved_hosting"),
        ("hosting_balance_imbalances", "hosting_balance_imbalances"),
        ("manual_calendar_placements", "manual_calendar_placements"),
        ("manual_external_conflict_placements", "external_calendar_conflicts"),
        # A tournament placed in a host-controlled movable
        # interval is a valid candidate, but it displaces an existing event
        # (e.g. open ice) and therefore requires explicit host confirmation
        # before the placement can be treated as locked/booked.
        ("movable_allocations_used", "movable_host_confirmation_required"),
        ("stale_approvals", "stale_approvals"),
        ("orphaned_approvals", "orphaned_approvals"),
    ):
        count = len(result.get(field) or [])
        if count:
            reasons.append({"code": code, "count": count})

    # Participation findings are split by the canonical club-pool
    # classification: only a genuine club/player-pool (or single-team) deficit
    # blocks as `participation_shortfalls`. A pure intra-club label imbalance
    # (an aggregate-complete pool split 5+3) stays visible as informational
    # evidence but is not counted as an equivalent missing participation
    # opportunity.
    participation_entries = [
        item for item in (result.get("manual_participation_placements") or []) if isinstance(item, dict)
    ]
    unresolved_participation = [
        item for item in participation_entries if item.get("counts_as_unresolved_shortfall", True)
    ]
    intra_club_distribution = [
        item for item in participation_entries if not item.get("counts_as_unresolved_shortfall", True)
    ]
    informational_reasons: list[dict[str, Any]] = []
    if unresolved_participation:
        reasons.append({"code": "participation_shortfalls", "count": len(unresolved_participation)})
    if intra_club_distribution:
        informational_reasons.append(
            {"code": "intra_club_participation_distribution", "count": len(intra_club_distribution)}
        )

    # Over-target participation is never a hard failure (a target is a strong
    # goal, not a ceiling), but a candidate that exceeds its configured targets
    # still needs explicit review instead of publishing as if it matched them.
    # A multi-team pool's aggregate-complete over/under split is intra-club
    # distribution, not an independent deviation.
    over_target = [
        deviation
        for deviation in (result.get("participation_deviations") or [])
        if deviation.get("direction") == "over_target"
        and deviation.get("club_pool_classification") != INTRA_CLUB_DISTRIBUTION
    ]
    if over_target:
        reasons.append({"code": "participation_target_deviation", "count": len(over_target)})

    status = "REVIEW_REQUIRED" if reasons else "PUBLISHABLE"
    return {
        "status": status,
        "publishable": status == "PUBLISHABLE",
        "reasons": reasons,
        "informational_reasons": informational_reasons,
    }


def verify_final_candidate(
    candidate: dict[str, Any],
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run base verification plus final minimum-size and game-integrity checks."""
    result = dict(_verify_candidate(candidate, problem))
    # Effective-shape rule: `verify_candidate` now only raises `bye_team_not_allowed`
    # for an avoidable bye/underscheduling shape (the registered pool could
    # support a bigger no-bye shape) -- a genuine input-constrained
    # adaptation is reported separately as `input_constrained_shapes` and is
    # never in `violations`. Final verification must therefore keep, not
    # discard, this violation instead of blanket-stripping it as before.
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
        team_count = capacity_places(tournament)
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
    annotate_violations(result["violations"])
    result["ok"] = not violations
    readiness = publication_readiness(result)
    result["publication_readiness"] = readiness
    result["publishable"] = readiness["publishable"]
    return result
