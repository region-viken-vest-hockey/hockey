"""Canonical club-level opponent diversity and exposure (see opponent_diversity)."""

from __future__ import annotations

from tournament_scheduler.opponent_diversity import (
    MEASURE_CO_ATTENDANCE,
    MEASURE_GAMES,
    compute_opponent_diversity,
)
from tournament_scheduler.team_schedule_quality import (
    compare_team_schedule_consequence,
)


def _team(club: str, label: str, age_group: str = "U10") -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(
    tournament_id: str,
    date: str,
    teams: list[dict],
    *,
    age_group: str = "U10",
    games: list[dict] | None = None,
) -> dict:
    return {
        "id": tournament_id,
        "date": date,
        "arena": "",
        "age_group": age_group,
        "host_club": teams[0]["club"] if teams else "",
        "teams": teams,
        "games": games or [],
    }


def _games_for(subject_label: str, opponents: list[str]) -> list[dict]:
    return [{"home": subject_label, "away": label} for label in opponents]


def test_club_is_the_primary_opponent_identity_across_squad_labels() -> None:
    subject = _team("Tønsberg", "Tønsberg Grå")
    plan = {
        "tournaments": [
            _tournament("t1", "2026-09-06", [subject, _team("Frisk Asker", "Frisk Asker 1")]),
            _tournament("t2", "2026-09-20", [subject, _team("Frisk Asker", "Frisk Asker 2")]),
            _tournament("t3", "2026-10-04", [subject, _team("Frisk Asker", "Frisk Asker 3")]),
        ]
    }
    diversity = compute_opponent_diversity(plan, ("Tønsberg", "Tønsberg Grå", "U10"))

    assert diversity.measure == MEASURE_CO_ATTENDANCE
    assert diversity.distinct_clubs == 1
    frisk = diversity.club_record("Frisk Asker")
    assert frisk is not None
    assert frisk.encounters == 3
    assert frisk.squad_encounters == 3
    assert frisk.opponent_squads == 3
    # Exact squad labels remain inspectable as diagnostics.
    assert {record.identity[1] for record in diversity.squads} == {
        "Frisk Asker 1",
        "Frisk Asker 2",
        "Frisk Asker 3",
    }


def test_age_groups_stay_separate_for_the_same_club_name() -> None:
    subject = _team("Tønsberg", "Tønsberg Grå", "U10")
    plan = {
        "tournaments": [
            _tournament("u10", "2026-09-06", [subject, _team("Frisk Asker", "Frisk Asker 1", "U10")]),
            _tournament(
                "u12",
                "2026-09-06",
                [_team("Tønsberg", "Tønsberg Grå", "U12"), _team("Frisk Asker", "Frisk Asker 1", "U12")],
                age_group="U12",
            ),
        ]
    }
    diversity = compute_opponent_diversity(plan, ("Tønsberg", "Tønsberg Grå", "U10"))

    assert diversity.tournament_count == 1
    assert diversity.club_record("Frisk Asker").encounters == 1
    assert all(record.age_group == "U10" for record in diversity.clubs)


def test_opportunity_baseline_favours_neither_large_nor_small_clubs() -> None:
    subject = _team("Tønsberg", "Tønsberg Grå")
    large = [
        _team("Stor", "Stor 1"),
        _team("Stor", "Stor 2"),
        _team("Stor", "Stor 3"),
    ]
    small = [_team("Liten", "Liten 1")]
    plan = {
        "tournaments": [
            _tournament("t1", "2026-09-06", [subject, large[0], small[0]]),
            _tournament("t2", "2026-09-20", [subject, large[0], small[0]]),
            _tournament("t3", "2026-10-04", [subject, large[1], small[0]]),
            _tournament("t4", "2026-10-18", [subject, large[1], small[0]]),
            _tournament("t5", "2026-11-01", [subject, large[2]]),
            _tournament("t6", "2026-11-15", [subject, large[2]]),
        ]
    }
    diversity = compute_opponent_diversity(plan, ("Tønsberg", "Tønsberg Grå", "U10"))

    stor = diversity.club_record("Stor")
    liten = diversity.club_record("Liten")
    assert stor.opponent_squads == 3
    assert liten.opponent_squads == 1
    # Stor is met six times across its three squads; Liten is met four times
    # from a single squad. Stor's larger squad supply makes its encounters less
    # concentrated even though it is met more often.
    assert stor.encounters == 6
    assert liten.encounters == 4
    assert liten.exposure_index > stor.exposure_index


def test_games_measure_counts_only_played_games_not_co_attendance() -> None:
    subject = _team("Tønsberg", "Tønsberg Grå")
    # Limited rounds: four participants, subject plays only two opponents.
    teams = [
        subject,
        _team("A", "A1"),
        _team("B", "B1"),
        _team("C", "C1"),
    ]
    plan = {
        "tournaments": [
            _tournament(
                "t1",
                "2026-09-06",
                teams,
                games=[
                    {"home": "Tønsberg Grå", "away": "A1"},
                    {"home": "Tønsberg Grå", "away": "B1"},
                    {"home": "C1", "away": "A1"},
                ],
            )
        ]
    }
    games = compute_opponent_diversity(
        plan, ("Tønsberg", "Tønsberg Grå", "U10"), measure="auto"
    )
    co_attendance = compute_opponent_diversity(
        plan, ("Tønsberg", "Tønsberg Grå", "U10"), measure=MEASURE_CO_ATTENDANCE
    )

    assert games.measure == MEASURE_GAMES
    assert games.total_opponent_encounters == 2
    # C is co-attended but was never played, so it has zero game encounters.
    assert games.club_record("C").encounters == 0
    assert games.club_record("C").available_tournaments == 1
    assert co_attendance.total_opponent_encounters == 3
    assert co_attendance.club_record("C").encounters == 1


def test_same_club_siblings_and_guest_teams_are_excluded_from_club_diversity() -> None:
    subject = _team("Tønsberg", "Tønsberg Grå")
    plan = {
        "tournaments": [
            _tournament(
                "t1",
                "2026-09-06",
                [
                    subject,
                    _team("Tønsberg", "Tønsberg Grønn"),
                    {"club": "Guest", "label": "Guest 1", "age_group": "U10", "guest": True},
                    _team("Frisk Asker", "Frisk Asker 1"),
                ],
            )
        ]
    }
    diversity = compute_opponent_diversity(plan, ("Tønsberg", "Tønsberg Grå", "U10"))

    assert diversity.same_club_encounters == 1
    assert diversity.distinct_clubs == 1
    assert diversity.club_record("Guest") is None
    assert {record.identity[1] for record in diversity.squads} == {
        "Tønsberg Grønn",
        "Frisk Asker 1",
    }


def test_squad_label_swap_is_not_a_material_regression() -> None:
    """The operational repair: Frisk Asker 2 -> Frisk Asker 1 stays acceptable."""

    subject = _team("Tønsberg", "Tønsberg Grå")

    def plan(fifth_is_one: bool) -> dict:
        fifth = _team("Frisk Asker", "Frisk Asker 1" if fifth_is_one else "Frisk Asker 2")
        return {
            "tournaments": [
                _tournament("t1", "2026-09-06", [subject, _team("Frisk Asker", "Frisk Asker 1"), _team("Jar", "Jar 1")]),
                _tournament("t2", "2026-09-20", [subject, _team("Frisk Asker", "Frisk Asker 1"), _team("Jar", "Jar 1")]),
                _tournament("t3", "2026-10-04", [subject, _team("Frisk Asker", "Frisk Asker 1"), _team("Kongsberg", "Kongsberg 1")]),
                _tournament("t4", "2026-10-18", [subject, _team("Frisk Asker", "Frisk Asker 1"), _team("Kongsberg", "Kongsberg 1")]),
                _tournament("t5", "2026-11-01", [subject, fifth, _team("Jar", "Jar 1")]),
                _tournament("t6", "2026-11-15", [subject, _team("Frisk Asker", "Frisk Asker 2"), _team("Kongsberg", "Kongsberg 1")]),
                _tournament("t7", "2026-11-29", [subject, _team("Frisk Asker", "Frisk Asker 3"), _team("Jar", "Jar 1")]),
            ]
        }

    before = plan(False)
    after = plan(True)
    comparison = compare_team_schedule_consequence(
        before, after, ("Tønsberg", "Tønsberg Grå", "U10")
    )

    material_codes = {entry["code"] for entry in comparison["material_regressions"]}
    warning_codes = {entry["code"] for entry in comparison["warnings"]}
    assert "more_concentrated_club_exposure" not in material_codes
    assert "more_repeated_opponent_excess" in warning_codes
    assert comparison["acceptable"] is True

    # Club-level Frisk Asker exposure is unchanged by the label swap.
    before_diversity = compute_opponent_diversity(before, ("Tønsberg", "Tønsberg Grå", "U10"))
    after_diversity = compute_opponent_diversity(after, ("Tønsberg", "Tønsberg Grå", "U10"))
    assert before_diversity.club_record("Frisk Asker").encounters == 7
    assert after_diversity.club_record("Frisk Asker").encounters == 7


def test_genuinely_concentrated_club_exposure_is_material() -> None:
    subject = _team("Tønsberg", "Tønsberg Grå")
    dates = [
        "2026-09-06", "2026-09-13", "2026-09-20", "2026-09-27",
        "2026-10-04", "2026-10-11", "2026-10-18", "2026-10-25",
        "2026-11-01", "2026-11-08",
    ]

    def tournament(index: int, opponent: dict) -> dict:
        return _tournament(f"t{index}", dates[index], [subject, opponent])

    # `Liten` has one squad; the other opponents are distinct one-squad clubs,
    # so a repeated Liten relationship is genuinely concentrated.
    def plan(liten_meetings: int) -> dict:
        opponents = [
            _team("Liten", "Liten 1"),
            _team("Jar", "Jar 1"),
            _team("Kongsberg", "Kongsberg 1"),
            _team("Frisk Asker", "Frisk Asker 1"),
        ]
        sequence = [0] * liten_meetings + [1, 2, 3]
        return {
            "tournaments": [
                tournament(index, opponents[club_index])
                for index, club_index in enumerate(sequence)
            ]
        }

    before = plan(1)
    after = plan(6)
    comparison = compare_team_schedule_consequence(
        before, after, ("Tønsberg", "Tønsberg Grå", "U10")
    )
    material_codes = {entry["code"] for entry in comparison["material_regressions"]}
    assert "more_concentrated_club_exposure" in material_codes
    assert comparison["acceptable"] is False


def test_six_squad_club_is_not_penalized_for_supplying_more_opponents() -> None:
    subject = _team("Tønsberg", "Tønsberg Grå")
    stor = [_team("Stor", f"Stor {index}") for index in range(1, 7)]
    dates = [f"2026-{month:02d}-{day:02d}" for month, day in (
        (9, 6), (9, 20), (10, 4), (10, 18), (11, 1), (11, 15), (11, 29), (12, 13)
    )]
    tournaments = [
        _tournament(f"t{index}", dates[index], [subject, stor[index]])
        for index in range(6)
    ]
    tournaments.append(_tournament("t6", dates[6], [subject, _team("Liten", "Liten 1")]))
    tournaments.append(_tournament("t7", dates[7], [subject, _team("Annen", "Annen 1")]))
    diversity = compute_opponent_diversity(
        {"tournaments": tournaments}, ("Tønsberg", "Tønsberg Grå", "U10")
    )

    stor_record = diversity.club_record("Stor")
    liten_record = diversity.club_record("Liten")
    assert stor_record.opponent_squads == 6
    assert stor_record.encounters == 6
    assert liten_record.encounters == 1
    # Even though Stor is met six times, its six-squad supply keeps the
    # exposure index well below the concentration threshold.
    assert stor_record.exposure_index < 2.0
    assert stor_record.exposure_index < liten_record.exposure_index or liten_record.encounters < 3


def test_individual_squad_exposure_is_not_hidden_by_club_aggregation() -> None:
    gra = _team("Tønsberg", "Tønsberg Grå")
    hvit = _team("Tønsberg", "Tønsberg Hvit")
    dates = [f"2026-{month:02d}-{day:02d}" for month, day in (
        (9, 6), (9, 20), (10, 4), (10, 18), (11, 1), (11, 15), (11, 29), (12, 13)
    )]

    def plan() -> dict:
        tournaments = []
        for index in range(6):
            tournaments.append(
                _tournament(f"g{index}", dates[index], [gra, _team("Frisk Asker", "Frisk Asker 1")])
            )
        tournaments.append(_tournament("g6", dates[6], [gra, _team("Jar", "Jar 1")]))
        tournaments.append(_tournament("g7", dates[7], [gra, _team("Kongsberg", "Kongsberg 1")]))
        tournaments.append(
            _tournament("h0", dates[0], [hvit, _team("Frisk Asker", "Frisk Asker 1")])
        )
        for index in range(1, 8):
            tournaments.append(
                _tournament(
                    f"h{index}",
                    dates[index],
                    [hvit, _team(f"Klubb{index}", f"Klubb{index} 1")],
                )
            )
        return {"tournaments": tournaments}

    gra_diversity = compute_opponent_diversity(plan(), ("Tønsberg", "Tønsberg Grå", "U10"))
    hvit_diversity = compute_opponent_diversity(plan(), ("Tønsberg", "Tønsberg Hvit", "U10"))

    assert gra_diversity.club_record("Frisk Asker").exposure_index >= 2.0
    assert hvit_diversity.club_record("Frisk Asker").exposure_index < 1.5
    assert (
        gra_diversity.club_record("Frisk Asker").exposure_index
        > hvit_diversity.club_record("Frisk Asker").exposure_index
    )


def test_repeat_blocker_does_not_reject_a_consecutive_day_repair() -> None:
    subject = _team("Tønsberg", "Tønsberg Grå")
    opponents = {
        "Frisk Asker 1": _team("Frisk Asker", "Frisk Asker 1"),
        "Frisk Asker 2": _team("Frisk Asker", "Frisk Asker 2"),
        "Jar 1": _team("Jar", "Jar 1"),
        "Kongsberg 1": _team("Kongsberg", "Kongsberg 1"),
    }

    def tournament(tournament_id: str, date: str, labels: list[str]) -> dict:
        return _tournament(tournament_id, date, [subject, *(opponents[label] for label in labels)])

    # Before: t2 and t3 are only four days apart, and t1/t2/t3 each meet
    # Frisk Asker 1. The repair moves t3 and swaps Frisk Asker 2 in for one
    # Frisk Asker 1 meeting.
    before = {
        "tournaments": [
            tournament("t1", "2026-09-06", ["Frisk Asker 1", "Jar 1"]),
            tournament("t2", "2026-09-10", ["Frisk Asker 1", "Kongsberg 1"]),
            tournament("t3", "2026-09-14", ["Frisk Asker 1", "Jar 1"]),
            tournament("t4", "2026-10-04", ["Kongsberg 1", "Jar 1"]),
        ]
    }
    after = {
        "tournaments": [
            tournament("t1", "2026-09-06", ["Frisk Asker 1", "Jar 1"]),
            tournament("t2", "2026-09-20", ["Frisk Asker 1", "Kongsberg 1"]),
            tournament("t3", "2026-10-04", ["Frisk Asker 2", "Jar 1"]),
            tournament("t4", "2026-10-18", ["Kongsberg 1", "Jar 1"]),
        ]
    }

    comparison = compare_team_schedule_consequence(
        before, after, ("Tønsberg", "Tønsberg Grå", "U10")
    )
    assert comparison["acceptable"] is True
    assert "more_gaps_under_7_days" not in {
        entry["code"] for entry in comparison["material_regressions"]
    }
