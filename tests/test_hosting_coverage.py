"""Tests for `tournament_scheduler.hosting_coverage` (issue #266 P0)."""

from tournament_scheduler.hosting_coverage import (
    hosting_breakdown_by_club_and_age_group,
    hosting_coverage_matrix,
    hosting_targets_with_coverage_floor,
    proportional_integer_targets,
    required_club_age_group_pairs,
    shared_registration_facts,
    unresolved_from_matrix,
)


def _team(club: str, age_group: str) -> dict:
    return {"club": club, "age_group": age_group, "label": f"{club} {age_group}"}


def _tournament(host_club: str, age_group: str, cancelled: bool = False) -> dict:
    return {"host_club": host_club, "age_group": age_group, "cancelled": cancelled}


class TestRequiredClubAgeGroupPairs:
    def test_deduplicates_and_preserves_first_seen_order(self):
        teams = [_team("Jar", "U10"), _team("Jar", "U10"), _team("Jutul", "U11"), _team("Jar", "U11")]
        assert required_club_age_group_pairs(teams) == [
            ("Jar", "U10"),
            ("Jutul", "U11"),
            ("Jar", "U11"),
        ]

    def test_ignores_teams_missing_club_or_age_group(self):
        teams = [{"club": "", "age_group": "U10"}, {"club": "Jar", "age_group": ""}]
        assert required_club_age_group_pairs(teams) == []


class TestHostingCoverageMatrix:
    def test_flags_zero_hosted_as_unresolved(self):
        teams = [_team("Jar", "U10"), _team("Jutul", "U10")]
        tournaments = [_tournament("Jar", "U10")]

        rows = hosting_coverage_matrix(teams, tournaments)

        by_club = {row["club"]: row for row in rows}
        assert by_club["Jar"] == {"club": "Jar", "age_group": "U10", "teams": 1, "hosted": 1, "unresolved": False}
        assert by_club["Jutul"] == {"club": "Jutul", "age_group": "U10", "teams": 1, "hosted": 0, "unresolved": True}

    def test_hosting_in_a_different_age_group_does_not_count(self):
        teams = [_team("Jutul", "U10")]
        tournaments = [_tournament("Jutul", "U11")]

        rows = hosting_coverage_matrix(teams, tournaments)

        assert rows == [{"club": "Jutul", "age_group": "U10", "teams": 1, "hosted": 0, "unresolved": True}]

    def test_cancelled_tournaments_do_not_count_as_hosted(self):
        teams = [_team("Jutul", "U10")]
        tournaments = [_tournament("Jutul", "U10", cancelled=True)]

        rows = hosting_coverage_matrix(teams, tournaments)

        assert rows[0]["hosted"] == 0
        assert rows[0]["unresolved"] is True

    def test_unresolved_from_matrix_lists_only_unresolved_rows(self):
        rows = hosting_coverage_matrix(
            [_team("Jar", "U10"), _team("Jutul", "U10")],
            [_tournament("Jar", "U10")],
        )
        assert unresolved_from_matrix(rows) == [{"club": "Jutul", "age_group": "U10"}]


class TestJointClubCoverage:
    """issue #274: a joint registration's obligation is satisfied when any
    constituent hosts it, since the planner always resolves "A/B" to a
    single physical host club before a real tournament exists."""

    def test_hosted_by_either_constituent_resolves_the_joint_row(self):
        teams = [_team("Kongsberg/Tønsberg", "JU12")]
        tournaments = [_tournament("Kongsberg", "JU12")]

        rows = hosting_coverage_matrix(teams, tournaments)

        assert rows == [
            {
                "club": "Kongsberg/Tønsberg",
                "age_group": "JU12",
                "teams": 1,
                "hosted": 1,
                "unresolved": False,
            }
        ]

    def test_unresolved_when_neither_constituent_hosts(self):
        teams = [_team("Kongsberg/Tønsberg", "JU12")]
        tournaments = [_tournament("Jar", "JU12")]

        rows = hosting_coverage_matrix(teams, tournaments)

        assert rows[0]["hosted"] == 0
        assert rows[0]["unresolved"] is True

    def test_kongsberg_own_obligation_is_independent_of_the_joint_row(self):
        # Kongsberg's own U9 registration must not be satisfied by a
        # Kongsberg/Tønsberg JU12 hosting -- different age groups.
        teams = [_team("Kongsberg", "U9"), _team("Kongsberg/Tønsberg", "JU12")]
        tournaments = [_tournament("Kongsberg", "JU12")]

        rows = hosting_coverage_matrix(teams, tournaments)

        by_key = {(row["club"], row["age_group"]): row for row in rows}
        assert by_key[("Kongsberg", "U9")]["unresolved"] is True
        assert by_key[("Kongsberg/Tønsberg", "JU12")]["unresolved"] is False


class TestSharedRegistrationFacts:
    def test_omits_plain_single_club_rows(self):
        teams = [_team("Jar", "U10")]
        assert shared_registration_facts(teams, []) == []

    def test_reports_constituent_hosted_counts_and_trust(self):
        teams = [_team("Kongsberg/Tønsberg", "JU12")]
        tournaments = [
            _tournament("Kongsberg", "JU12"),
            _tournament("Kongsberg", "U10"),
        ]
        club_calendar_status = {"Kongsberg": "known", "Tønsberg": "untrusted"}

        facts = shared_registration_facts(teams, tournaments, club_calendar_status)

        assert facts == [
            {
                "registration": "Kongsberg/Tønsberg",
                "age_group": "JU12",
                "constituents": ["Kongsberg", "Tønsberg"],
                "hosted_by_constituent": {"Kongsberg": 1, "Tønsberg": 0},
                "hosted_by_constituent_total": {"Kongsberg": 2, "Tønsberg": 0},
                "calendar_trust": {"Kongsberg": "known", "Tønsberg": "untrusted"},
                "automatic_placement_possible": {"Kongsberg": True, "Tønsberg": False},
            }
        ]

    def test_missing_calendar_status_defaults_to_unknown_and_not_placeable(self):
        teams = [_team("Kongsberg/Tønsberg", "JU12")]
        facts = shared_registration_facts(teams, [])
        assert facts[0]["calendar_trust"] == {"Kongsberg": "unknown", "Tønsberg": "unknown"}
        assert facts[0]["automatic_placement_possible"] == {"Kongsberg": False, "Tønsberg": False}


class TestHostingBreakdown:
    def test_aggregates_by_club_and_age_group(self):
        rows = hosting_coverage_matrix(
            [_team("Jar", "U10"), _team("Jutul", "U10"), _team("Jar", "U11")],
            [_tournament("Jar", "U10"), _tournament("Jar", "U10")],
        )
        breakdown = hosting_breakdown_by_club_and_age_group(rows)
        assert breakdown["by_club"]["Jar"] == {"hosted": 2, "unresolved": 1}
        assert breakdown["by_club"]["Jutul"] == {"hosted": 0, "unresolved": 1}
        assert breakdown["by_age_group"]["U10"] == {"hosted": 2, "unresolved": 1}
        assert breakdown["by_age_group"]["U11"] == {"hosted": 0, "unresolved": 1}


class TestProportionalIntegerTargets:
    def test_sums_to_total(self):
        targets = proportional_integer_targets({"Jar": 3, "Jutul": 2, "Kongsberg": 1}, 6)
        assert targets == {"Jar": 3, "Jutul": 2, "Kongsberg": 1}
        assert sum(targets.values()) == 6

    def test_zero_total_gives_all_zero(self):
        assert proportional_integer_targets({"Jar": 3, "Jutul": 1}, 0) == {"Jar": 0, "Jutul": 0}


class TestHostingTargetsWithCoverageFloor:
    def test_bumps_zero_target_club_to_one_when_arithmetically_possible(self):
        # Plain rounding gives Jar:2, Jutul:1, Kongsberg:0 (see
        # test_zero_target_club_gets_a_coverage_floor_of_one in
        # test_host_assignment_regression.py) -- the floor borrows 1 from
        # the club with the largest remaining target.
        targets, unmet = hosting_targets_with_coverage_floor(
            {"Jar": 4, "Jutul": 1, "Kongsberg": 1}, 3
        )
        assert targets == {"Jar": 1, "Jutul": 1, "Kongsberg": 1}
        assert sum(targets.values()) == 3
        assert unmet == []

    def test_reports_unmet_when_fewer_tournaments_than_clubs(self):
        targets, unmet = hosting_targets_with_coverage_floor(
            {"Jar": 1, "Jutul": 1, "Kongsberg": 1}, 2
        )
        assert sum(targets.values()) == 2
        assert len(unmet) == 1
        assert unmet[0] in {"Jar", "Jutul", "Kongsberg"}
        assert targets[unmet[0]] == 0

    def test_zero_total_marks_every_weighted_club_unmet(self):
        targets, unmet = hosting_targets_with_coverage_floor({"Jar": 1, "Jutul": 0}, 0)
        assert targets == {"Jar": 0, "Jutul": 0}
        assert unmet == ["Jar"]

    def test_no_zero_targets_returns_unchanged(self):
        targets, unmet = hosting_targets_with_coverage_floor({"Jar": 3, "Jutul": 3}, 6)
        assert targets == {"Jar": 3, "Jutul": 3}
        assert unmet == []
