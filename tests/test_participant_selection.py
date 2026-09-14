"""Tests for `tournament_scheduler.participant_selection` roster-size planning.

issue #316: greedy full-packing of tournament slots (always filling to
`tournament_capacity`) can strand a final slot below `MIN_TEAMS_PER_TOURNAMENT`
even when the total participation demand is perfectly packable. These tests
cover the balanced packing helper directly (fast, no `SeasonPlanner` needed).
"""

from datetime import date
from typing import Dict

from tournament_scheduler.participant_relocation import relocate_structurally_impossible_scheduled_slots
from tournament_scheduler.models import Roster, Team
from tournament_scheduler.participant_selection import (
    MIN_TEAMS_PER_TOURNAMENT,
    pick_scored_participants,
    plan_roster_sizes,
    rebalance_roster_sizes_across_dates,
    relocate_structurally_impossible_slots,
)


class _StubRoster:
    """Minimal `Roster` double: only `by_age_group` is used by relocation."""

    def __init__(self, team_count: int):
        self._team_count = team_count

    def by_age_group(self, age_group):
        return [object()] * self._team_count


class TestPlanRosterSizes:
    def test_seventeen_teams_capacity_four_target_seven_packs_into_thirty_slots(self):
        """issue #316: U12 production case -- 17 teams * target 7 = 119
        participations at capacity 4 must need exactly 30 slots (matching
        `target_tournaments_for_age_group`'s own ceil(119/4) derivation) and
        every one of those 119 participations must be packable."""
        demand = 17 * 7
        capacity = 4
        sizes = plan_roster_sizes(demand, capacity)

        assert len(sizes) == 30
        assert sum(sizes) == demand

    def test_seven_teams_capacity_four_target_seven_packs_into_thirteen_slots(self):
        """issue #316: JU12 production case -- 7 teams * target 7 = 49
        participations at capacity 4 must need exactly 13 slots."""
        demand = 7 * 7
        capacity = 4
        sizes = plan_roster_sizes(demand, capacity)

        assert len(sizes) == 13
        assert sum(sizes) == demand

    def test_planned_sizes_stay_within_min_and_capacity_and_sum_to_demand(self):
        """issue #316: sizes must never fall below `MIN_TEAMS_PER_TOURNAMENT`
        or exceed `tournament_capacity`, and must sum exactly to demand --
        the packing must not silently drop or invent participations."""
        demand = 17 * 7
        capacity = 4
        sizes = plan_roster_sizes(demand, capacity)

        assert sum(sizes) == demand
        for size in sizes:
            assert MIN_TEAMS_PER_TOURNAMENT <= size <= capacity

    def test_matches_issue_reported_balanced_packing_for_u12_and_ju12(self):
        """issue #316: the issue's own worked examples -- U12 as 29 full
        slots of 4 plus one slot of 3, JU12 as 10 full slots of 4 plus three
        slots of 3 -- not a greedy-then-stranded-remainder split."""
        u12_sizes = plan_roster_sizes(17 * 7, 4)
        assert sorted(u12_sizes) == sorted([4] * 29 + [3] * 1)

        ju12_sizes = plan_roster_sizes(7 * 7, 4)
        assert sorted(ju12_sizes) == sorted([4] * 10 + [3] * 3)

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
        flat_sizes = plan_roster_sizes(17 * 7, 4)  # 29x4 + 1x3
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
        date_groups = [(date(2026, 9, 5), 5), (date(2026, 9, 12), 3)]
        flat_sizes = [4, 4, 4, 4, 4, 3, 3, 3]  # 20 + 9 = 29 total; date 2 has room for 12 (3*4)

        sizes_by_date, evidence = rebalance_roster_sizes_across_dates(
            date_groups, flat_sizes, capacity, distinct_team_count
        )

        first_total = sum(sizes_by_date[date(2026, 9, 5)])
        second_total = sum(sizes_by_date[date(2026, 9, 12)])
        assert first_total == distinct_team_count  # capped at the team-pool ceiling
        # The 3 teams that didn't fit on the first date are carried forward
        # and absorbed by the second date's own slack (9 + 3 = 12 <= 3*4).
        assert first_total + second_total == sum(flat_sizes)
        assert second_total <= min(3 * capacity, distinct_team_count)
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
        """5 slots * min_teams(3) == distinct_team_count(15) exactly -- every
        team plays once, no slack, but it IS feasible. A `>=` in place of `>`
        here would wrongly report a structural uniqueness limit."""
        date_groups = [(date(2026, 9, 5), 5)]
        flat_sizes = [3, 3, 3, 3, 3]  # exactly uses all 15 teams

        sizes_by_date, evidence = rebalance_roster_sizes_across_dates(
            date_groups, flat_sizes, capacity=4, distinct_team_count=15
        )

        assert not any(e["category"] == "same_date_uniqueness_limit" for e in evidence)
        assert sum(sizes_by_date[date(2026, 9, 5)]) == 15

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
        assert shortfall_entries[0]["unplaced_participations"] == 3
        assert shortfall_entries[0]["limiting_dates"] == ["2026-09-05"]
        assert sum(sizes_by_date[date(2026, 9, 5)]) == 17


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
        flat_sizes = [4, 4, 4, 4, 4, 1, 4, 4, 4, 4, 4]

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


class TestPickScoredParticipantsClubCapTiering:
    """issue #324 (reopened): a large club's high-deficit teams must not
    outcompete available legal (<=2-per-club) candidates for a roster slot
    just because their fairness deficit is bigger than the club-cap
    penalty -- the production regression was a `Jarhallen` U11 tournament
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
