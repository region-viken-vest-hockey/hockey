"""Tests for `tournament_scheduler.participant_selection` roster-size planning.

issue #316: greedy full-packing of tournament slots (always filling to
`tournament_capacity`) can strand a final slot below `MIN_TEAMS_PER_TOURNAMENT`
even when the total participation demand is perfectly packable. These tests
cover the balanced packing helper directly (fast, no `SeasonPlanner` needed).
"""

from datetime import date

from tournament_scheduler.participant_selection import (
    MIN_TEAMS_PER_TOURNAMENT,
    plan_roster_sizes,
    rebalance_roster_sizes_across_dates,
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
