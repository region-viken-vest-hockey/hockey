"""Targeted repairs for locally underfilled tournament rosters.

The repair in this module is intentionally narrow: when a built tournament is
shorter than the shared effective tournament shape even though the registered
pool can supply more legal teams, try to fill the missing roster slot(s) before
any broad Stage 3 optimizer reshuffles the season.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Optional, Sequence

from tournament_scheduler.effective_tournament_shape import compute_effective_tournament_shape
from tournament_scheduler.models import SeasonPlan, Team, Tournament
from tournament_scheduler.participant_selection import deficit_score
from tournament_scheduler.planning_contract import HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT


@dataclass
class RosterRepairRecord:
    """Audit evidence for one targeted roster-repair attempt."""

    tournament_id: str
    age_group: str
    date: str
    actual_team_count: int
    effective_team_count: int
    missing_slots: int
    status: str
    added_teams: list[dict[str, str]] = field(default_factory=list)
    rejected_candidates: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tournament_id": self.tournament_id,
            "age_group": self.age_group,
            "date": self.date,
            "actual_team_count": self.actual_team_count,
            "effective_team_count": self.effective_team_count,
            "missing_slots": self.missing_slots,
            "status": self.status,
            "added_teams": list(self.added_teams),
            "rejected_candidates": list(self.rejected_candidates),
        }


def attempt_underfilled_roster_repairs(
    planner: Any,
    plan: SeasonPlan,
    *,
    split_date: Optional[date] = None,
    has_split_targets: bool = False,
) -> list[dict[str, Any]]:
    """Fill locally underfilled unpinned tournaments from legal registered teams.

    The caller owns independent verification after this mutation.  This
    function only applies deterministic, local participant additions that pass
    the same hard eligibility invariants available to the baseline planner.
    """

    pinned_ids = set((plan.manual_adjustments or {}).get("pinned_tournament_ids", []))
    records: list[RosterRepairRecord] = []

    for tournament in plan.tournaments:
        if tournament.cancelled or tournament.id in pinned_ids:
            continue

        shape = compute_effective_tournament_shape(
            tournament.age_group,
            len(planner.roster.by_age_group(tournament.age_group)),
            configured_rounds=planner.rounds_per_tournament_for_age_group.get(tournament.age_group),
            parallel_game_capacity=planner.parallel_games_for_age_group.get(tournament.age_group),
        )
        actual = len(tournament.teams)
        if not shape.has_explicit_target or actual >= shape.effective_team_count:
            continue

        missing = shape.effective_team_count - actual
        # If even the canonical registered pool cannot supply the effective
        # shape, this is not a local construction defect this repair can fix.
        if len(planner.roster.by_age_group(tournament.age_group)) < shape.effective_team_count:
            continue

        period = _period_for_tournament(planner, tournament.date, split_date, has_split_targets)
        record = RosterRepairRecord(
            tournament_id=tournament.id,
            age_group=tournament.age_group,
            date=tournament.date.isoformat(),
            actual_team_count=actual,
            effective_team_count=shape.effective_team_count,
            missing_slots=missing,
            status="unrepaired",
        )

        direct_fill_applied = False
        while len(tournament.teams) < shape.effective_team_count:
            ranked = _ranked_direct_fill_candidates(planner, plan, tournament, period, record)
            if not ranked:
                break
            chosen = ranked[0]
            _add_team_and_regenerate(planner, tournament, chosen)
            direct_fill_applied = True
            record.added_teams.append(_team_dict(chosen))
            planner.recompute_tournament_participations(plan)

        if len(tournament.teams) < shape.effective_team_count:
            while len(tournament.teams) < shape.effective_team_count:
                swap = _attempt_one_local_swap_fill(
                    planner,
                    plan,
                    tournament,
                    period,
                    split_date=split_date,
                    has_split_targets=has_split_targets,
                )
                if swap is None:
                    break
                source_team, donor_tournament, donor_fill_team = swap
                _remove_team_and_regenerate(planner, donor_tournament, source_team)
                _add_team_and_regenerate(planner, tournament, source_team)
                _add_team_and_regenerate(planner, donor_tournament, donor_fill_team)
                record.added_teams.append({**_team_dict(source_team), "via": "local_swap"})
                planner.recompute_tournament_participations(plan)

        if len(tournament.teams) >= shape.effective_team_count:
            record.status = "repaired_by_direct_fill" if direct_fill_applied else "repaired_by_local_swap"
        else:
            record.status = "targeted_repair_impossible"
        if record.status == "repaired_by_local_swap":
            record.rejected_candidates.append({"reason": "direct_fill_impossible_before_local_swap"})
        records.append(record)

    return [record.as_dict() for record in records]


def _period_for_tournament(
    planner: Any,
    tournament_date: date,
    split_date: Optional[date],
    has_split_targets: bool,
) -> Optional[str]:
    if not has_split_targets or split_date is None:
        return None
    from tournament_scheduler import planning_half

    half = planning_half.tournament_half(tournament_date, split_date)
    return half if half in ("before_christmas", "after_christmas") else None


def _ranked_direct_fill_candidates(
    planner: Any,
    plan: SeasonPlan,
    tournament: Tournament,
    period: Optional[str],
    record: RosterRepairRecord,
) -> list[Team]:
    selected_keys = {planner._team_key(team) for team in tournament.teams}
    candidates = [
        team
        for team in planner.roster.by_age_group(tournament.age_group)
        if planner._team_key(team) not in selected_keys
    ]

    legal: list[Team] = []
    seen_rejections = {
        (entry.get("label", ""), entry.get("reason", ""))
        for entry in record.rejected_candidates
    }
    for team in candidates:
        reason = _direct_fill_rejection_reason(planner, plan, tournament, team, period)
        if reason is None:
            legal.append(team)
            continue
        key = (team.label, reason)
        if key not in seen_rejections:
            record.rejected_candidates.append({**_team_dict(team), "reason": reason})
            seen_rejections.add(key)

    original_order = {planner._team_key(team): index for index, team in enumerate(candidates)}
    return sorted(
        legal,
        key=lambda team: (
            -deficit_score(planner, team, tournament.age_group, period),
            planner._tournament_participations.get(planner._team_key(team), 0),
            team.club,
            team.label,
            original_order[planner._team_key(team)],
        ),
    )


def _direct_fill_rejection_reason(
    planner: Any,
    plan: SeasonPlan,
    tournament: Tournament,
    team: Team,
    period: Optional[str],
) -> Optional[str]:
    if _plays_on_date(planner, plan.tournaments, team, tournament.date):
        return "already_plays_same_date"
    if planner._team_at_target(team, period):
        return "at_participation_max"
    if _would_exceed_hard_club_cap(tournament.teams, team):
        return "hard_club_cap"
    return None


def _attempt_one_local_swap_fill(
    planner: Any,
    plan: SeasonPlan,
    target: Tournament,
    target_period: Optional[str],
    *,
    split_date: Optional[date],
    has_split_targets: bool,
) -> Optional[tuple[Team, Tournament, Team]]:
    target_keys = {planner._team_key(team) for team in target.teams}
    source_options: list[tuple[float, Team, Tournament]] = []
    for donor in plan.tournaments:
        if donor is target or donor.cancelled or donor.age_group != target.age_group:
            continue
        for source_team in donor.teams:
            if planner._team_key(source_team) in target_keys:
                continue
            if _would_exceed_hard_club_cap(target.teams, source_team):
                continue
            # The source team's participation count will remain unchanged: one
            # same-age tournament is exchanged for another.  Temporarily ignore
            # the "already plays this date" and max-participation checks that
            # made direct addition impossible, but require the donor to be
            # refillable so both rosters stay repaired.
            source_options.append((deficit_score(planner, source_team, target.age_group, target_period), source_team, donor))

    for _score, source_team, donor in sorted(source_options, key=lambda item: (-item[0], item[1].club, item[1].label)):
        donor_without_source = [team for team in donor.teams if planner._team_key(team) != planner._team_key(source_team)]
        donor_period = _period_for_tournament(planner, donor.date, split_date, has_split_targets)
        donor_fill = _best_donor_fill_candidate(planner, plan, donor, donor_without_source, source_team, donor_period)
        if donor_fill is not None:
            return source_team, donor, donor_fill
    return None


def _best_donor_fill_candidate(
    planner: Any,
    plan: SeasonPlan,
    donor: Tournament,
    donor_without_source: Sequence[Team],
    source_team: Team,
    donor_period: Optional[str],
) -> Optional[Team]:
    donor_keys = {planner._team_key(team) for team in donor_without_source}
    source_key = planner._team_key(source_team)
    candidates: list[Team] = []
    for team in planner.roster.by_age_group(donor.age_group):
        key = planner._team_key(team)
        if key == source_key or key in donor_keys:
            continue
        if _plays_on_date(planner, plan.tournaments, team, donor.date):
            continue
        if planner._team_at_target(team, donor_period):
            continue
        if _would_exceed_hard_club_cap(donor_without_source, team):
            continue
        candidates.append(team)
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda team: (
            -deficit_score(planner, team, donor.age_group, donor_period),
            planner._tournament_participations.get(planner._team_key(team), 0),
            team.club,
            team.label,
        ),
    )[0]


def _add_team_and_regenerate(planner: Any, tournament: Tournament, team: Team) -> None:
    tournament.teams.append(team)
    tournament.games = planner._generate_tournament_games(
        tournament.age_group,
        tournament.teams,
        planner._parallel_games_for(tournament.age_group),
    )


def _remove_team_and_regenerate(planner: Any, tournament: Tournament, team: Team) -> None:
    key = planner._team_key(team)
    tournament.teams = [existing for existing in tournament.teams if planner._team_key(existing) != key]
    tournament.games = planner._generate_tournament_games(
        tournament.age_group,
        tournament.teams,
        planner._parallel_games_for(tournament.age_group),
    )


def _plays_on_date(planner: Any, tournaments: Iterable[Tournament], team: Team, tournament_date: date) -> bool:
    key = planner._team_key(team)
    for tournament in tournaments:
        if tournament.date != tournament_date or tournament.cancelled:
            continue
        if any(planner._team_key(existing) == key for existing in tournament.teams):
            return True
    return False


def _would_exceed_hard_club_cap(existing: Sequence[Team], team: Team) -> bool:
    return sum(1 for selected in existing if selected.club == team.club) >= HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT


def _team_dict(team: Team) -> dict[str, str]:
    return {"club": team.club, "label": team.label, "age_group": team.age_group}
