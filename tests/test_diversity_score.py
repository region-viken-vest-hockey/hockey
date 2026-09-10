"""issue #305: opponent-diversity ("Motstandervariasjon") must stay bounded.

`diversity_score` divides how many *eligible* inter-club opponents a team
has faced by how many it *could* face. Before this fix, the numerator could
include `_opponent_history` entries that fall outside the eligible
denominator (e.g. a same-club pairing recorded for another reason), which
let the ratio exceed 1.0 -- a coverage score above 100% is meaningless.
"""

from __future__ import annotations

from tournament_scheduler.game_generation import diversity_score
from tournament_scheduler.models import Team


class _StubRoster:
    def __init__(self, teams):
        self.teams = teams


class _StubPlanner:
    """Minimal stand-in exposing only what `diversity_score` reads."""

    def __init__(self, teams, opponent_history):
        self.roster = _StubRoster(teams)
        self._opponent_history = opponent_history

    def _team_key(self, team: Team) -> str:
        return team.label


def test_diversity_score_is_bounded_even_with_ineligible_history_entries():
    teams = [
        Team(club="Jar", label="Jar A", age_group="U10"),
        Team(club="Jar", label="Jar B", age_group="U10"),  # same club as "Jar A": ineligible opponent
        Team(club="Jutul", label="Jutul A", age_group="U10"),
    ]
    # "Jar A" has only one *eligible* inter-club opponent ("Jutul A"), but
    # `_opponent_history` also records a same-club pairing with "Jar B" --
    # that entry must not inflate the numerator beyond the eligible set.
    opponent_history = [
        ("Jar A", "Jutul A"),
        ("Jar A", "Jar B"),
    ]
    planner = _StubPlanner(teams, opponent_history)

    score = diversity_score(planner, tournaments=[])

    assert 0.0 <= score <= 1.0


def test_diversity_score_still_rewards_full_eligible_coverage():
    teams = [
        Team(club="Jar", label="Jar A", age_group="U10"),
        Team(club="Jutul", label="Jutul A", age_group="U10"),
    ]
    planner = _StubPlanner(teams, opponent_history=[("Jar A", "Jutul A")])

    score = diversity_score(planner, tournaments=[])

    assert score == 1.0
