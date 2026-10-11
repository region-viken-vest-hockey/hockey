"""Soft fairness target model for season planning.

The planner uses this as a tunable fairness model: it turns per-team game
counts into a club-size-aware target for each team in an age group. The model
is intentionally tiny and isolated so organizers can tweak its weights
without rewriting `SeasonPlanner`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, Mapping, Sequence

from tournament_scheduler.models import SeasonPlan, Team, team_key


@dataclass(frozen=True)
class FairnessModelConfig:
    """Configuration for the soft fairness target model.

    `club_share_weight` controls how strongly larger clubs are nudged above
    the age-group average (and smaller clubs slightly below it). `max_adjust`
    keeps the target from drifting too far from the actual age-group average.
    """

    club_share_weight: float = 0.35
    max_adjustment: float = 3.0


class SeasonFairnessModel:
    """Compute soft per-team target game counts from a finished season plan.

    The model uses the age-group average as the baseline and then adds a
    club-size adjustment:

        target = average_games + club_effect

    where `club_effect` grows with the club's share of teams in the age group.
    Larger clubs are therefore allowed to sit slightly above the average per
    team, while single-team clubs stay near the average. This is a soft target,
    not a hard constraint.
    """

    def __init__(self, config: FairnessModelConfig | None = None):
        self.config = config or FairnessModelConfig()

    @staticmethod
    def _duplicate_labels(teams: Sequence[Team]) -> set[str]:
        seen_ids: set[tuple[str, str, str]] = set()
        label_counts: Counter[str] = Counter()
        for team in teams:
            team_id = (team.club, team.label, team.age_group)
            if team_id in seen_ids:
                continue
            seen_ids.add(team_id)
            label_counts[team.label] += 1
        return {label for label, count in label_counts.items() if count > 1}

    def _target_games_from_counts(
        self,
        team: Team,
        age_group_teams: Sequence[Team],
        team_game_counts: Mapping[str, int],
        duplicate_labels: set[str] | None = None,
    ) -> float:
        if not age_group_teams:
            return 0.0

        duplicate_labels = duplicate_labels or self._duplicate_labels(age_group_teams)
        counts = [team_game_counts.get(team_key(t, duplicate_labels), 0) for t in age_group_teams]
        if not counts:
            return 0.0

        average_games = sum(counts) / len(counts)
        club_team_count = sum(1 for t in age_group_teams if t.club == team.club)
        club_share = club_team_count / len(age_group_teams)
        average_share = 1 / len(age_group_teams)

        # Larger clubs get nudged a bit above the age-group average, smaller
        # clubs a bit below it. The clamp keeps the target easy to reason about
        # and tune later.
        club_effect = (
            (club_share - average_share)
            * average_games
            * self.config.club_share_weight
        )
        target = average_games + club_effect

        if self.config.max_adjustment is not None:
            target = min(target, average_games + self.config.max_adjustment)
            target = max(target, max(0.0, average_games - self.config.max_adjustment))

        return round(target, 3)

    def target_games_for_team(
        self,
        team: Team,
        age_group_teams: Sequence[Team],
        team_game_counts: Mapping[str, int],
        duplicate_labels: set[str] | None = None,
    ) -> float:
        """Return the soft target game count for `team`.

        This is the diagnostic / post-plan form: it uses the actual counts
        recorded so far.
        """
        return self._target_games_from_counts(team, age_group_teams, team_game_counts, duplicate_labels=duplicate_labels)

    def planning_target_games_for_team(
        self,
        team: Team,
        age_group_teams: Sequence[Team],
        team_game_counts: Mapping[str, int],
        duplicate_labels: set[str] | None = None,
    ) -> float:
        """Return a planning-time soft target for `team`.

        When an age group has no recorded games yet, the planner needs a
        non-zero prior to break symmetry. In that case the model seeds each
        team with one synthetic game before applying the club-size adjustment.
        This keeps the model useful early in planning while still converging
        to the real count-based target once games exist.
        """
        if not age_group_teams:
            return 0.0

        duplicate_labels = duplicate_labels or self._duplicate_labels(age_group_teams)
        if any(team_game_counts.get(team_key(t, duplicate_labels), 0) for t in age_group_teams):
            return self._target_games_from_counts(team, age_group_teams, team_game_counts, duplicate_labels=duplicate_labels)

        seeded_counts = {team_key(t, duplicate_labels): 1 for t in age_group_teams}
        return self._target_games_from_counts(team, age_group_teams, seeded_counts, duplicate_labels=duplicate_labels)

    def planning_targets_for_age_group(
        self,
        age_group_teams: Sequence[Team],
        team_game_counts: Mapping[str, int],
        duplicate_labels: set[str] | None = None,
    ) -> Dict[str, float]:
        """Return planning-time soft targets for every team in an age group."""
        duplicate_labels = duplicate_labels or self._duplicate_labels(age_group_teams)
        return {
            team_key(team, duplicate_labels): self.planning_target_games_for_team(team, age_group_teams, team_game_counts, duplicate_labels=duplicate_labels)
            for team in age_group_teams
        }

    def adjustment_rows_for_plan(self, plan: SeasonPlan) -> list[dict[str, object]]:
        """Return a sorted fairness-adjustment overview for a finished plan.

        Each row contains the team label, club, age group, actual game count,
        soft target, the adjustment needed to hit that target, the team's
        per-team tournament participation target (if set), and the actual
        number of tournaments the team participated in.
        """
        teams_by_age_group: dict[str, list[Team]] = {}
        seen_keys: set[str] = set()
        all_teams = [team for tournament in plan.tournaments for team in tournament.teams]
        duplicate_labels = self._duplicate_labels(all_teams)

        # Compute per-team tournament participation counts
        tournament_participations: dict[str, int] = {}
        for tournament in plan.tournaments:
            for team in tournament.teams:
                key = team_key(team, duplicate_labels)
                tournament_participations[key] = tournament_participations.get(key, 0) + 1

        for tournament in plan.tournaments:
            for team in tournament.teams:
                key = team_key(team, duplicate_labels)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                teams_by_age_group.setdefault(team.age_group, []).append(team)

        rows: list[dict[str, object]] = []
        for age_group, teams in teams_by_age_group.items():
            if not teams:
                continue
            targets = self.targets_for_age_group(teams, plan.team_game_counts, duplicate_labels=duplicate_labels)
            for team in teams:
                key = team_key(team, duplicate_labels)
                actual = int(plan.team_game_counts.get(key, 0))
                target = float(targets.get(key, 0.0))
                delta = round(target - actual, 3)
                # Per-team tournament participation info
                team_target_ttc = team.target_tournament_count or None  # None = not set, uses global
                actual_participations = tournament_participations.get(key, 0)
                rows.append({
                    "label": team.label,
                    "key": key,
                    "club": team.club,
                    "age_group": age_group,
                    "actual": actual,
                    "target": target,
                    "adjustment": delta,
                    "status": "under" if delta > 0.5 else ("over" if delta < -0.5 else "on_target"),
                    "target_tournaments": team_target_ttc,
                    "actual_tournaments": actual_participations,
                })

        rows.sort(
            key=lambda row: (
                -abs(float(row["adjustment"])),
                str(row["age_group"]),
                str(row["club"]),
                str(row["label"]),
                str(row.get("key", row["label"])),
            )
        )
        return rows

    def targets_for_age_group(
        self,
        age_group_teams: Sequence[Team],
        team_game_counts: Mapping[str, int],
        duplicate_labels: set[str] | None = None,
    ) -> Dict[str, float]:
        """Return soft target counts for every team in an age group."""
        duplicate_labels = duplicate_labels or self._duplicate_labels(age_group_teams)
        return {
            team_key(team, duplicate_labels): self.target_games_for_team(team, age_group_teams, team_game_counts, duplicate_labels=duplicate_labels)
            for team in age_group_teams
        }
