"""Teams that carry a hosting obligation during host assignment (#511).

A planner restricted to the canonical eligible pool (a durable age-group
withdrawal leaves it) yields only that pool; otherwise the registered roster
is used, so planners without a pool restriction keep their behaviour.
"""

from __future__ import annotations

from typing import Dict, List

from tournament_scheduler.models import Team


def hosting_teams(planner) -> List[Team]:
    pool_teams = getattr(planner, "hosting_pool_teams", None)
    return list(pool_teams()) if pool_teams is not None else list(planner.roster.teams)


def hosting_club_team_counts(planner, age_group: str) -> Dict[str, int]:
    club_team_counts: Dict[str, int] = {}
    for team in hosting_teams(planner):
        if team.age_group == age_group:
            club_team_counts[team.club] = club_team_counts.get(team.club, 0) + 1
    return club_team_counts
