"""Tests for `tournament_scheduler.participant_selection` roster-size planning.

issue #316: greedy full-packing of tournament slots (always filling to
`tournament_capacity`) can strand a final slot below `MIN_TEAMS_PER_TOURNAMENT`
even when the total participation demand is perfectly packable. These tests
cover the balanced packing helper directly (fast, no `SeasonPlanner` needed).
"""

from datetime import date
from typing import Dict

import pytest

from tournament_scheduler.participant_relocation import relocate_structurally_impossible_scheduled_slots
from tournament_scheduler.models import Roster, Team
from tournament_scheduler.participant_selection import (
    MIN_TEAMS_PER_TOURNAMENT,
    pick_scored_participants,
    plan_roster_sizes,
    plan_roster_sizes_for_age_group,
    rebalance_roster_sizes_across_dates,
    relocate_structurally_impossible_slots,
    select_participants,
    target_tournaments_for_age_group,
)


class _StubRoster:
    """Minimal `Roster` double: only `by_age_group` is used by relocation."""

    def __init__(self, team_count: int):
        self._team_count = team_count

    def by_age_group(self, age_group):
        return [object()] * self._team_count


class TestPlanRosterSizes:
    def test_seventeen_teams_capacity_four_target_seven_packs_into_full_no_bye_slots(self):
        """17 teams * target 7 = 119 participations at capacity 4 now yields
        only full, even no-bye tournaments; the leftover participations are
        surfaced later as explicit shortfall evidence instead of a bye slot."""
        demand = 17 * 7
        capacity = 4
        sizes = plan_roster_sizes(demand, capacity)

        assert len(sizes) == 29
        assert sum(sizes) == 116

    def test_seven_teams_capacity_four_target_seven_packs_into_full_no_bye_slots(self):
        """7 teams * target 7 = 49 participations at capacity 4 yields 12
        full no-bye slots, leaving one participation for shortfall evidence."""
        demand = 7 * 7
        capacity = 4
        sizes = plan_roster_sizes(demand, capacity)

        assert len(sizes) == 12
        assert sum(sizes) == 48

    def test_planned_sizes_stay_within_no_bye_bounds_and_do_not_invent_demand(self):
        """Sizes must never create odd/bye tournaments or invent extra
        participations when demand is not exactly representable."""
        demand = 17 * 7
        capacity = 4
        sizes = plan_roster_sizes(demand, capacity)

        assert sum(sizes) <= demand
        for size in sizes:
            assert size % 2 == 0
            assert 4 <= size <= capacity

    def test_matches_no_bye_packing_for_u12_and_ju12(self):
        """U12/JU12 capacity-4 planning now materializes only full no-bye
        slots; unrepresented odd demand remains an explicit shortfall."""
        u12_sizes = plan_roster_sizes(17 * 7, 4)
        assert sorted(u12_sizes) == sorted([4] * 29)

        ju12_sizes = plan_roster_sizes(7 * 7, 4)
        assert sorted(ju12_sizes) == sorted([4] * 12)

    def test_no_slot_is_stranded_below_minimum_when_demand_is_packable(self):
        """issue #316: greedy full-packing of 49 at capacity 4 leaves a
        13th slot with only 1 eligible team (12*4=48, 1 remains). The
        balanced planner must never produce a slot below the minimum when
        the demand is mathematically packable."""
        sizes = plan_roster_sizes(49, 4)
        assert all(size >= MIN_TEAMS_PER_TOURNAMENT for size in sizes)

    def test_exact_capacity_multiple_packs_into_uniform_full_slots(self):
        sizes = plan_roster_sizes(48, 4)
        assert sizes == [4] * 12

    def test_no_demand_produces_no_slots(self):
        assert plan_roster_sizes(0, 4) == []

    def test_non_positive_capacity_produces_no_slots(self):
        assert plan_roster_sizes(10, 0) == []


class TestRebalanceRosterSizesAcrossDates:
    """issue #318: several parallel same-age-group slots can land on one
    date. The half-wide balanced list from `plan_roster_sizes` must be
    resized per date so no date's demand exceeds the age group's distinct
    team count, without silently dropping placeable demand.
    """

    def test_plenty_of_slack_matches_flat_plan_roster_sizes(self):
        """One slot per date, dates never share -- no date-group ever hits
        the distinct-team ceiling, so output must equal the flat plan."""
        flat_sizes = plan_roster_sizes(17 * 7, 4)  # 29x4; odd remainder is not materialized
        date_groups = [(date(2026, 9, d), 1) for d in range(1, 1 + len(flat_sizes))]

        sizes_by_date, evidence = rebalance_roster_sizes_across_dates(
            date_groups, flat_sizes, capacity=4, distinct_team_count=17
        )

        assert evidence == []
        flattened = [size for d, _ in date_groups for size in sizes_by_date[d]]
        assert sorted(flattened) == sorted(flat_sizes)

    def test_overflow_on_one_date_is_carried_forward_not_dropped(self):
        """5 parallel U12 slots planned at capacity (20 total) exceed the 17
        distinct teams available on that date, but a later date with slack
        can absorb the 3-team overflow -- no demand should be lost."""
        capacity = 4
        distinct_team_count = 17
        date_groups = [(date(2026, 9, 5), 5), (date(2026, 9, 12), 4)]
        flat_sizes = [4, 4, 4, 4, 4, 4, 4, 4]  # 20 + 12; date 2 has a spare slot for carry

        sizes_by_date, evidence = rebalance_roster_sizes_across_dates(
            date_groups, flat_sizes, capacity, distinct_team_count
        )

        first_total = sum(sizes_by_date[date(2026, 9, 5)])
        second_total = sum(sizes_by_date[date(2026, 9, 12)])
        assert first_total == 16  # capped at the largest no-bye total below the team-pool ceiling
        # The 4 teams that didn't fit on the first date are carried forward
        # and absorbed by the second date's spare slot (12 + 4 = 16).
        assert first_total + second_total == sum(flat_sizes)
        assert second_total <= min(4 * capacity, distinct_team_count)
        for sizes in sizes_by_date.values():
            for size in sizes:
                assert MIN_TEAMS_PER_TOURNAMENT <= size <= capacity
        assert not any(e["category"] == "same_date_uniqueness_limit" for e in evidence)
        assert not any(e["category"] == "same_date_participant_pool_capacity" for e in evidence)

    def test_structural_same_date_uniqueness_limit_is_reported(self):
        """6 parallel slots on one date each need >=3 distinct teams (18
        total) but only 17 teams exist -- physically impossible regardless
        of how demand is rebalanced."""
        date_groups = [(date(2026, 9, 5), 6)]
        flat_sizes = [4, 4, 3, 3, 3, 3]  # 20 total

        sizes_by_date, evidence = rebalance_roster_sizes_across_dates(
            date_groups, flat_sizes, capacity=4, distinct_team_count=17
        )

        uniqueness_entries = [e for e in evidence if e["category"] == "same_date_uniqueness_limit"]
        assert len(uniqueness_entries) == 1
        assert uniqueness_entries[0]["requested_slots"] == 6
        assert uniqueness_entries[0]["feasible_slots"] == 5
        sizes = sizes_by_date[date(2026, 9, 5)]
        assert len(sizes) <= 5
        assert sum(sizes) <= 17
        for size in sizes:
            assert MIN_TEAMS_PER_TOURNAMENT <= size <= 4

    def test_exact_boundary_slot_count_is_not_flagged_as_uniqueness_limit(self):
        """4 slots * min_teams(3) == distinct_team_count(12) exactly -- every
        team plays once, no slack, but it IS feasible. A `>=` in place of `>`
        here would wrongly report a structural uniqueness limit."""
        date_groups = [(date(2026, 9, 5), 4)]
        flat_sizes = [4, 4, 4, 4]  # 16 requested, capped to the 12-team pool

        sizes_by_date, evidence = rebalance_roster_sizes_across_dates(
            date_groups, flat_sizes, capacity=4, distinct_team_count=12
        )

        assert not any(e["category"] == "same_date_uniqueness_limit" for e in evidence)
        assert sum(sizes_by_date[date(2026, 9, 5)]) == 12

    def test_genuine_end_of_season_shortfall_is_reported_with_limiting_dates(self):
        """Demand that can never be placed even after using every date's
        full capacity is a genuine physical shortfall, not a silent drop."""
        date_groups = [(date(2026, 9, 5), 5)]
        flat_sizes = [4, 4, 4, 4, 4]  # 20 requested, only 17 teams exist and no later date

        sizes_by_date, evidence = rebalance_roster_sizes_across_dates(
            date_groups, flat_sizes, capacity=4, distinct_team_count=17
        )

        shortfall_entries = [e for e in evidence if e["category"] == "same_date_participant_pool_capacity"]
        assert len(shortfall_entries) == 1
        assert shortfall_entries[0]["unplaced_participations"] == 4
        assert shortfall_entries[0]["limiting_dates"] == ["2026-09-05"]
        assert sum(sizes_by_date[date(2026, 9, 5)]) == 16


class TestRelocateStructurallyImpossibleSlots:
    """issue #318: an overloaded date's excess slot must be relocated to
    another legal date in the same half before being accepted as a
    shortfall -- `rebalance_roster_sizes_across_dates` alone only
    redistributes participant *demand* across already-selected dates, it
    never moves the *slot* itself.
    """

    def test_excess_slot_relocates_to_a_legal_alternative_date(self):
        """17 U12 teams, min 3 per tournament: 6 parallel slots requested on
        one date is structurally impossible (6*3=18 > 17), but a second
        legal date (no overlapping age group scheduled) can absorb the
        excess slot -- all 6 slots must materialize somewhere."""
        overloaded = date(2026, 12, 13)
        alternative = date(2026, 12, 20)
        date_groups = [(overloaded, 6)]

        new_date_groups, evidence = relocate_structurally_impossible_slots(
            date_groups,
            distinct_team_count=17,
            age_group="U12",
            age_groups_by_date={overloaded: ["U12"]},
            alternative_dates=[alternative],
        )

        assert evidence == []
        assert dict(new_date_groups) == {overloaded: 5, alternative: 1}

    def test_no_legal_alternative_is_reported_with_attempted_evidence(self):
        """When every alternative date is already full (or overlaps), the
        excess slot remains unplaced and the evidence must show which
        alternatives were tried and why each was rejected."""
        overloaded = date(2026, 12, 13)
        full_alternative = date(2026, 12, 20)
        # `full_alternative` already has 5 of this age group's own slots
        # scheduled -- adding a 6th would itself exceed the 17-team ceiling
        # ((5 + 1) * 3 = 18 > 17), so it cannot legally absorb the excess.
        date_groups = [(overloaded, 6), (full_alternative, 5)]

        new_date_groups, evidence = relocate_structurally_impossible_slots(
            date_groups,
            distinct_team_count=17,
            age_group="U12",
            age_groups_by_date={overloaded: ["U12"], full_alternative: ["U12"] * 5},
            alternative_dates=[full_alternative],
        )

        assert dict(new_date_groups) == {overloaded: 6, full_alternative: 5}
        assert len(evidence) == 1
        entry = evidence[0]
        assert entry["category"] == "same_date_uniqueness_limit"
        assert entry["unrelocated_slots"] == 1
        assert entry["relocated_slots"] == 0
        assert entry["alternatives_considered"] == [
            {"date": full_alternative.isoformat(), "rejected_reason": "same_date_uniqueness_limit"}
        ]

    def test_alternative_date_with_overlapping_age_group_is_rejected(self):
        """A date already hosting an age group that overlaps `age_group`
        cannot legally take the relocated slot even if it has team-pool
        slack -- the same hard conflict `_check_overlap_collision` guards
        against later."""
        overloaded = date(2026, 12, 13)
        conflicting = date(2026, 12, 20)
        other_ag = "JU13"  # U12/JU13 pools overlap (see models.AGE_GROUP_OVERLAP)
        date_groups = [(overloaded, 6)]

        new_date_groups, evidence = relocate_structurally_impossible_slots(
            date_groups,
            distinct_team_count=17,
            age_group="U12",
            age_groups_by_date={overloaded: ["U12"], conflicting: [other_ag]},
            alternative_dates=[conflicting],
        )

        assert dict(new_date_groups) == {overloaded: 6}
        assert evidence[0]["alternatives_considered"] == [
            {"date": conflicting.isoformat(), "rejected_reason": "age_group_overlap"}
        ]

    def test_feasible_date_groups_are_left_untouched(self):
        date_groups = [(date(2026, 9, 5), 3), (date(2026, 9, 12), 2)]

        new_date_groups, evidence = relocate_structurally_impossible_slots(
            date_groups,
            distinct_team_count=17,
            age_group="U12",
            age_groups_by_date={},
            alternative_dates=[date(2026, 9, 19)],
        )

        assert new_date_groups == date_groups
        assert evidence == []

    def test_exactly_at_capacity_is_not_reported_as_limiting(self):
        """Demand landing exactly on a date's ceiling (not exceeding it) must
        not be recorded as a limiting date or produce any shortfall evidence
        -- there was no actual overflow to report."""
        date_groups = [(date(2026, 9, 5), 4)]
        flat_sizes = [4, 4, 4, 4]  # exactly fills 4 slots at capacity 4 == distinct_team_count

        sizes_by_date, evidence = rebalance_roster_sizes_across_dates(
            date_groups, flat_sizes, capacity=4, distinct_team_count=16
        )

        assert evidence == []
        assert sum(sizes_by_date[date(2026, 9, 5)]) == 16

    def test_date_exactly_at_capacity_is_excluded_from_limiting_dates(self):
        """A middle date that exactly absorbs incoming carry (no overflow)
        must not appear in the final shortfall's `limiting_dates`, even when
        an earlier and later date both genuinely overflow. Otherwise
        operators reviewing the evidence would be pointed at a date that was
        never actually the bottleneck."""
        capacity = 4
        distinct_team_count = 17
        date_a, date_b, date_c = date(2026, 9, 5), date(2026, 9, 12), date(2026, 9, 19)
        date_groups = [(date_a, 5), (date_b, 1), (date_c, 5)]
        flat_sizes = [4, 4, 4, 4, 4, 0, 4, 4, 4, 4, 4]

        _, evidence = rebalance_roster_sizes_across_dates(
            date_groups, flat_sizes, capacity, distinct_team_count
        )

        shortfall = [e for e in evidence if e["category"] == "same_date_participant_pool_capacity"]
        assert len(shortfall) == 1
        assert shortfall[0]["limiting_dates"] == [date_a.isoformat(), date_c.isoformat()]

    def test_zero_demand_date_group_produces_no_slots_or_evidence(self):
        sizes_by_date, evidence = rebalance_roster_sizes_across_dates(
            [(date(2026, 9, 5), 1)], [], capacity=4, distinct_team_count=17
        )
        assert sizes_by_date[date(2026, 9, 5)] == []
        assert evidence == []


class TestRelocateStructurallyImpossibleScheduledSlots:
    """issue #318: `relocate_structurally_impossible_scheduled_slots` fans
    `relocate_structurally_impossible_slots` out across a full season's
    `scheduled` list. These cover the production U12 shape end to end and
    the planning-half boundary the per-group unit tests can't exercise."""

    @staticmethod
    def _period_for_date(tournament_date):
        return "before_christmas" if tournament_date < date(2027, 1, 1) else "after_christmas"

    def test_seventeen_teams_target_seven_capacity_four_all_thirty_slots_materialize(self):
        """Production U12 shape: 17 teams, half target 7, capacity 4 needs
        30 slots. A skeleton that asks one date for 6 parallel slots (6*3=18
        > 17, structurally impossible) but leaves another legal date in the
        same half free must relocate the excess slot so all 30 still land
        somewhere in the half."""
        overloaded = date(2026, 12, 13)
        other_dates = [date(2026, 12, d) for d in (6, 20, 27)]
        padding_dates = [date(2026, 11, d) for d in (1, 8, 15)]
        relocation_target = date(2026, 11, 29)
        scheduled = [(overloaded, "U12")] * 6
        for d, count in zip(other_dates, (5, 5, 5)):
            scheduled.extend([(d, "U12")] * count)
        for d in padding_dates:
            scheduled.extend([(d, "U12")] * 3)  # pad to 30 total, none overloaded
        assert len(scheduled) == 30

        new_scheduled, evidence = relocate_structurally_impossible_scheduled_slots(
            _StubRoster(17),
            scheduled,
            free_dates=[relocation_target, date(2027, 1, 10)],
            period_for_date=self._period_for_date,
        )

        assert len(new_scheduled) == 30
        assert evidence == []
        counts_by_date: dict = {}
        for d, _ in new_scheduled:
            counts_by_date[d] = counts_by_date.get(d, 0) + 1
        assert counts_by_date[overloaded] == 5
        assert counts_by_date[relocation_target] == 1

    def test_relocation_never_crosses_the_planning_half_boundary(self):
        """Even when an after-christmas date is free and would otherwise be
        a viable candidate, an excess before-christmas slot must never be
        relocated across the half boundary."""
        overloaded = date(2026, 12, 13)
        after_christmas_free_date = date(2027, 1, 10)
        before_christmas_free_date = date(2026, 12, 20)
        scheduled = [(overloaded, "U12")] * 6

        new_scheduled, evidence = relocate_structurally_impossible_scheduled_slots(
            _StubRoster(17),
            scheduled,
            free_dates=[after_christmas_free_date, before_christmas_free_date],
            period_for_date=self._period_for_date,
        )

        assert evidence == []
        assert all(self._period_for_date(d) == "before_christmas" for d, _ in new_scheduled)
        assert (after_christmas_free_date, "U12") not in new_scheduled
        assert (before_christmas_free_date, "U12") in new_scheduled


class _FakeFairnessModel:
    """Stub returning a fixed per-team planning target, independent of the
    age group's own team list or running counts (only `pick_scored_participants`'s
    club-cap tiering is under test here, not real fairness math)."""

    def __init__(self, target_by_label: dict):
        self._target_by_label = target_by_label

    def planning_target_games_for_team(self, team, age_group_teams, running_game_counts):
        return self._target_by_label[team.label]


class _FakeClubCapPlanner:
    """Minimal planner double exposing exactly what `participant_selection_score`
    and `_within_club_cap` read, with no `SeasonPlanner`/`build_plan` machinery."""

    def __init__(self, teams, target_by_label, max_club_teams_per_tournament=2):
        self.roster = Roster(teams=teams)
        self.max_club_teams_per_tournament = max_club_teams_per_tournament
        self._running_game_counts = {team.label: 0 for team in teams}
        self._invite_counts = {team.label: 0 for team in teams}
        self._club_age_group_team_counts = {team.label: 1 for team in teams}
        self._opponent_history = {}
        self._grouped_with = {}
        self._club_cap_overrides = 0
        self.fairness_model = _FakeFairnessModel(target_by_label)

    def _team_key(self, team):
        return team.label

    def _team_at_target(self, team, period=None):
        return False


class _FixedCohortPlanner(_FakeClubCapPlanner):
    def __init__(self, teams, target_by_label, *, parallel_games=3, rounds=5):
        super().__init__(teams, target_by_label, max_club_teams_per_tournament=2)
        self.parallel_games_for_age_group = {teams[0].age_group: parallel_games} if teams else {}
        self.rounds_per_tournament_for_age_group = {teams[0].age_group: rounds} if teams else {}
        self.target_tournament_count = None
        self.participation_targets_by_age_group = {}
        self._tournament_participations = {team.label: 0 for team in teams}
        self._tournament_participations_by_half = {"before_christmas": {}, "after_christmas": {}}

    def _team_target_tournament_count(self, team, period=None):
        return self.fairness_model._target_by_label[team.label]

    def _team_at_target(self, team, period=None):
        return self._tournament_participations.get(team.label, 0) >= self._team_target_tournament_count(team, period)


class TestFixedCohortParticipantSelection:
    """Full-pool effective shapes bypass generic participant subset selection."""

    def test_registered_count_equal_effective_count_selects_complete_pool_despite_planned_size_cap(self):
        teams = [Team(club=f"Club{i}", label=f"U11-{i}", age_group="U11") for i in range(6)]
        planner = _FixedCohortPlanner(teams, {team.label: 3 for team in teams}, parallel_games=3, rounds=5)

        selected = select_participants(planner, "U11", planned_roster_size=4)

        assert selected == teams

    def test_fixed_cohort_stops_when_any_member_has_reached_target_instead_of_returning_partial_pool(self):
        teams = [Team(club=f"Club{i}", label=f"U10-{i}", age_group="U10") for i in range(6)]
        planner = _FixedCohortPlanner(teams, {team.label: 1 for team in teams}, parallel_games=3, rounds=5)
        planner._tournament_participations[teams[0].label] = 1

        assert select_participants(planner, "U10") == []

    def test_fixed_cohort_volume_uses_common_participation_target_not_rotating_subsets(self):
        teams = [
            Team(club=f"Club{i}", label=f"U9-{i}", age_group="U9", target_tournament_count=4)
            for i in range(6)
        ]
        planner = _FixedCohortPlanner(teams, {team.label: 4 for team in teams}, parallel_games=3, rounds=5)

        assert target_tournaments_for_age_group(planner, "U9") == 4
        assert plan_roster_sizes_for_age_group(planner, "U9") == [6, 6, 6, 6]


class TestPickScoredParticipantsClubCapTiering:
    """issue #324 (reopened): a large club's high-deficit teams must not
    outcompete available legal (<=2-per-club) candidates for a roster slot
    just because their fairness deficit is bigger than the club-cap
    penalty -- the production regression was a `Jar Isforum` U11 tournament
    that filled 6/6 with one club's teams despite four other clubs having
    eligible, never-yet-invited teams for that same slot.
    """

    def test_high_deficit_club_does_not_crowd_out_available_other_clubs(self):
        """Jar's 6 teams are all given a much larger fairness deficit
        (target 20 vs 0 played) than the 4 other clubs' teams (target 1
        each). Under the old single-score-pool selection, Jar's deficit term
        (-350 * 20 = -7000) dwarfed the club-cap penalty for a 3rd+ Jar pick
        (+1500 per excess step), so Jar could fill the whole 6-team roster.
        With tiered selection, the 4 other-club teams stay in the legal
        (<=2-per-club) tier and must be exhausted before a 3rd Jar team is
        even considered -- exactly enough supply exists here (2 Jar + 4
        others = 6) that no override should ever be needed.
        """
        jar_teams = [Team(club="Jar", label=f"Jar U11-{i}", age_group="U11") for i in range(1, 7)]
        other_teams = [
            Team(club=club, label=f"{club} U11", age_group="U11")
            for club in ("Kongsberg", "Skien", "Holmen", "Ringerike")
        ]
        teams = jar_teams + other_teams
        target_by_label = {team.label: 20 for team in jar_teams}
        target_by_label.update({team.label: 1 for team in other_teams})

        planner = _FakeClubCapPlanner(teams, target_by_label)

        selected = pick_scored_participants(planner, teams, count=6, age_group="U11")

        club_counts: Dict[str, int] = {}
        for team in selected:
            club_counts[team.club] = club_counts.get(team.club, 0) + 1

        assert club_counts.get("Jar", 0) <= 2
        assert set(club_counts) == {"Jar", "Kongsberg", "Skien", "Holmen", "Ringerike"}
        assert planner._club_cap_overrides == 0

    def test_hard_cap_leaves_roster_short_instead_of_selecting_a_4th_same_club_team(self):
        """issue #326: when every candidate is from one club, the preferred
        (<=2) tier is empty, so `pick_scored_participants` falls back to the
        cap-exceeding pool -- but that fallback must itself stop at 3 teams,
        never silently pick a 4th, even though the caller asked for a
        6-team roster and 6 Jar teams are available.
        """
        jar_teams = [Team(club="Jar", label=f"Jar U11-{i}", age_group="U11") for i in range(1, 7)]
        target_by_label = {team.label: 1 for team in jar_teams}
        planner = _FakeClubCapPlanner(jar_teams, target_by_label)

        selected = pick_scored_participants(planner, jar_teams, count=6, age_group="U11")

        assert len(selected) == 3
        assert all(team.club == "Jar" for team in selected)

    def test_third_team_is_still_allowed_when_no_legal_alternative_remains(self):
        """When every other club's sole team is already excluded (simulated
        here by a roster with no other clubs at all), the flat cap must
        remain a soft preference, not a hard filter -- a 3rd Jar team is the
        only way to complete the roster and must be selected, with the
        exception measurable via `_club_cap_overrides`."""
        jar_teams = [Team(club="Jar", label=f"Jar U11-{i}", age_group="U11") for i in range(1, 4)]
        target_by_label = {team.label: 20 for team in jar_teams}

        planner = _FakeClubCapPlanner(jar_teams, target_by_label)

        selected = pick_scored_participants(planner, jar_teams, count=3, age_group="U11")

        assert len(selected) == 3
        assert planner._club_cap_overrides > 0


class _FakeClubShareFairnessPlanner(_FakeClubCapPlanner):
    """`_FakeClubCapPlanner` plus the roster-demand/running-count attributes
    `club_share_deficit` needs (issue #327) -- kept a separate double so the
    pre-#327 tiering tests above stay on the minimal interface and continue
    exercising the "no club-share machinery available" fallback path."""

    def __init__(self, teams, target_by_label, season_target_by_label, *, max_club_teams_per_tournament=2):
        super().__init__(teams, target_by_label, max_club_teams_per_tournament)
        self._season_target_by_label = season_target_by_label
        self._tournament_participations: dict = {}
        self._tournament_participations_by_half: dict = {"before_christmas": {}, "after_christmas": {}}

    def _team_target_tournament_count(self, team, period=None):
        return self._season_target_by_label[team.label]

    def set_actual(self, label, count):
        self._tournament_participations[label] = count


class TestPickScoredParticipantsClubShareFairness:
    """issue #327: a club materially behind its proportional demand share
    (a large club with many registered teams) must be allowed a 3rd team
    ahead of the hard-cap-only fallback, not only once literally no
    under-cap candidate remains -- otherwise a large club can be
    systematically underrepresented across the season purely because some
    small club always has a technically-available 3rd candidate.
    """

    def test_third_team_from_a_materially_behind_club_beats_a_no_deficit_under_cap_candidate(self):
        # Jar has 8 registered U11 teams vs. 4 each for three small clubs
        # (20 teams total, all target 5) -- Jar's demand share is
        # 8*5 / 20*5 = 40%, matching the issue's own worked example. Across
        # prior tournaments this season, Jar only has 2 participations while
        # each small club already has 3 (11 total participations so far):
        # Jar's fair share of those 11 is 4.4, but it only has 2 -- a
        # material (>=1) proportional deficit, while the small clubs are
        # each roughly at (slightly above) their own 20% share.
        jar_teams = [Team(club="Jar", label=f"Jar-{i}", age_group="U11") for i in range(1, 9)]
        holmen = [Team(club="Holmen", label=f"Holmen-{i}", age_group="U11") for i in range(1, 5)]
        jutul = [Team(club="Jutul", label=f"Jutul-{i}", age_group="U11") for i in range(1, 5)]
        kongsberg = [Team(club="Kongsberg", label=f"Kongsberg-{i}", age_group="U11") for i in range(1, 5)]
        teams = jar_teams + holmen + jutul + kongsberg
        season_target_by_label = {team.label: 5 for team in teams}
        # Only the club-share deficit should drive the outcome here -- keep
        # every team's own fairness-model target/running-count identical
        # (both candidates unplayed) so team-level `deficit_score` and
        # `normalized_invite_count` contribute equally to both sides.
        target_by_label = {team.label: 1 for team in teams}

        planner = _FakeClubShareFairnessPlanner(teams, target_by_label, season_target_by_label)
        planner.set_actual("Jar-1", 1)
        planner.set_actual("Jar-2", 1)
        for club_teams in (holmen, jutul, kongsberg):
            planner.set_actual(club_teams[0].label, 1)
            planner.set_actual(club_teams[1].label, 1)
            planner.set_actual(club_teams[2].label, 1)

        # Score the remaining candidates as if two Jar teams and one team
        # each from the other three clubs are already selected for *this*
        # tournament (at the preferred cap, every club already represented
        # here) -- an unplayed 3rd Jar team (deficit-driven) vs. an unplayed
        # 2nd Holmen team (under cap, no material deficit). `remaining=[]`
        # keeps `club_diversity_penalty` at 0 for both, isolating the
        # cap-penalty/club-share-deficit terms this change actually affects.
        from tournament_scheduler.participant_selection import participant_selection_score

        already_selected = [jar_teams[0], jar_teams[1], holmen[0], jutul[0], kongsberg[0]]
        jar_score = participant_selection_score(planner, already_selected, [], jar_teams[2], "U11")
        holmen_score = participant_selection_score(planner, already_selected, [], holmen[3], "U11")
        assert jar_score < holmen_score

    def test_third_team_stays_disfavored_when_club_is_already_at_its_fair_share(self):
        """Two equally-sized clubs, both already at their proportional pace
        -- a 3rd same-club team must still lose to a different, under-cap
        club's candidate (the pre-#327 #324 behavior), since there is no
        material club-share deficit to justify relaxing the cap."""
        club_a = [Team(club="A", label=f"A-{i}", age_group="U11") for i in range(1, 4)]
        club_b = [Team(club="B", label=f"B-{i}", age_group="U11") for i in range(1, 4)]
        teams = club_a + club_b
        season_target_by_label = {team.label: 3 for team in teams}
        target_by_label = {team.label: 1 for team in teams}

        planner = _FakeClubShareFairnessPlanner(teams, target_by_label, season_target_by_label)
        planner.set_actual("A-1", 1)
        planner.set_actual("A-2", 1)
        planner.set_actual("B-1", 1)

        from tournament_scheduler.participant_selection import participant_selection_score

        # Both clubs already represented in `selected`; `remaining=[]` keeps
        # `club_diversity_penalty` at 0 for both, isolating the cap-penalty/
        # club-share-deficit terms (same isolation as the deficit-driven
        # test above).
        already_selected = [club_a[0], club_a[1], club_b[0]]
        a_score = participant_selection_score(planner, already_selected, [], club_a[2], "U11")
        b_score = participant_selection_score(planner, already_selected, [], club_b[1], "U11")
        assert b_score < a_score


class TestClubDemandShares:
    """issue #327: `club_demand_shares` must reduce to `team_count /
    total_team_count` for equal per-team targets, matching the issue's own
    worked example (Jar 8/20 = 40%, Holmen 2/20 = 10%, Jutul 2/20 = 10%)."""

    def test_matches_issue_worked_example_percentages(self):
        from tournament_scheduler.participant_roster_sizing import club_demand_shares

        jar = [Team(club="Jar", label=f"Jar-{i}", age_group="U11") for i in range(8)]
        holmen = [Team(club="Holmen", label=f"Holmen-{i}", age_group="U11") for i in range(2)]
        jutul = [Team(club="Jutul", label=f"Jutul-{i}", age_group="U11") for i in range(2)]
        rest = [Team(club=f"Club{i}", label=f"Club{i}-team", age_group="U11") for i in range(8)]
        teams = jar + holmen + jutul + rest
        target_by_label = {team.label: 5 for team in teams}

        planner = _FakeClubShareFairnessPlanner(teams, target_by_label, target_by_label)

        shares = club_demand_shares(planner, "U11")

        assert shares["Jar"] == pytest.approx(0.4)
        assert shares["Holmen"] == pytest.approx(0.1)
        assert shares["Jutul"] == pytest.approx(0.1)
