"""Tests for `tournament_scheduler.participant_selection` roster-size planning.

issue #316: greedy full-packing of tournament slots (always filling to
`tournament_capacity`) can strand a final slot below `MIN_TEAMS_PER_TOURNAMENT`
even when the total participation demand is perfectly packable. These tests
cover the balanced packing helper directly (fast, no `SeasonPlanner` needed).
"""

from tournament_scheduler.participant_selection import (
    MIN_TEAMS_PER_TOURNAMENT,
    plan_roster_sizes,
)


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
