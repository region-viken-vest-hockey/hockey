"""Which tournaments a neighborhood-search result actually changed."""

from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Tuple

from tournament_scheduler.repair.host_team_missing import _identity


def changed_tournaments(
    before_candidate: Mapping[str, Any],
    after_candidate: Mapping[str, Any],
    scope_ids: Iterable[str],
) -> List[str]:
    scope = {str(item) for item in scope_ids}
    before_by_id = {
        str(t.get("id")): t for t in before_candidate.get("tournaments", [])
    }
    after_by_id = {str(t.get("id")): t for t in after_candidate.get("tournaments", [])}
    changed: List[str] = []
    for tournament_id in sorted(scope):
        before = before_by_id.get(tournament_id)
        after = after_by_id.get(tournament_id)
        if before is None or after is None:
            changed.append(tournament_id)
            continue
        if tournament_signature(before) != tournament_signature(after):
            changed.append(tournament_id)
    return changed


def tournament_signature(tournament: Mapping[str, Any]) -> Tuple[Any, ...]:
    teams = tuple(sorted(_identity(team) for team in tournament.get("teams", []) or []))
    return (
        tournament.get("date"),
        tournament.get("host_club"),
        tournament.get("arena"),
        tournament.get("start_time"),
        teams,
    )
