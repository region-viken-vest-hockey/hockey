from tournament_scheduler.final_verification import verify_final_candidate


def _team(label: str) -> dict:
    return {"club": "Jar", "label": label, "age_group": "U10"}


def _problem(target: int = 1, calendar_status: str = "known") -> dict:
    return {
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "teams": [_team("Jar 1"), _team("Jar 2"), _team("Jar 3")],
        "parallel_games": {"U10": 2},
        "round_length_minutes": {"U10": 15},
        "target_tournament_count": target,
        "participation_targets_by_age_group": {},
        "manual_adjustments": {
            "locked_dates": [],
            "banned_dates": [],
            "forced_host_clubs": [],
            "excluded_host_clubs": [],
            "pinned_tournament_ids": [],
        },
        "club_calendar_status": {"Jar": calendar_status},
        "club_busy_intervals": {},
    }


def _candidate(teams=None, games=None) -> dict:
    teams = teams or [_team("Jar 1"), _team("Jar 2"), _team("Jar 3")]
    games = games if games is not None else [
        {"home": "Jar 1", "away": "Jar 2", "parallel_slot": 0, "round_number": 1},
        {"home": "Jar 1", "away": "Jar 3", "parallel_slot": 0, "round_number": 2},
        {"home": "Jar 2", "away": "Jar 3", "parallel_slot": 0, "round_number": 3},
    ]
    return {
        "tournaments": [
            {
                "id": "t1",
                "date": "2026-02-01",
                "arena": "Jarahallen",
                "host_club": "Jar",
                "age_group": "U10",
                "start_time": "10:00",
                "teams": teams,
                "games": games,
            }
        ]
    }


def test_clean_final_candidate_is_publishable():
    result = verify_final_candidate(_candidate(), _problem())
    assert result["ok"] is True
    assert result["publication_readiness"]["status"] == "PUBLISHABLE"
    assert result["publishable"] is True


def test_tournament_below_minimum_is_invalid():
    result = verify_final_candidate(
        _candidate(
            teams=[_team("Jar 1"), _team("Jar 2")],
            games=[
                {"home": "Jar 1", "away": "Jar 2", "parallel_slot": 0, "round_number": 1}
            ],
        ),
        _problem(),
    )
    assert result["ok"] is False
    assert "tournament_under_minimum" in {item["code"] for item in result["violations"]}
    assert result["publication_readiness"]["status"] == "INVALID"


def test_missing_round_robin_pair_is_invalid():
    result = verify_final_candidate(
        _candidate(
            games=[
                {"home": "Jar 1", "away": "Jar 2", "parallel_slot": 0, "round_number": 1},
                {"home": "Jar 1", "away": "Jar 3", "parallel_slot": 0, "round_number": 2},
            ]
        ),
        _problem(),
    )
    assert "round_robin_missing_pair" in {item["code"] for item in result["violations"]}
    assert result["publication_readiness"]["status"] == "INVALID"


def test_team_cannot_play_twice_in_same_round():
    result = verify_final_candidate(
        _candidate(
            games=[
                {"home": "Jar 1", "away": "Jar 2", "parallel_slot": 0, "round_number": 1},
                {"home": "Jar 1", "away": "Jar 3", "parallel_slot": 1, "round_number": 1},
                {"home": "Jar 2", "away": "Jar 3", "parallel_slot": 0, "round_number": 2},
            ]
        ),
        _problem(),
    )
    assert "team_double_booked_in_round" in {item["code"] for item in result["violations"]}


def test_participation_shortfall_is_valid_but_review_required():
    result = verify_final_candidate(_candidate(), _problem(target=2))
    assert result["ok"] is True
    assert result["publishable"] is False
    assert result["publication_readiness"]["status"] == "REVIEW_REQUIRED"
    assert "participation_shortfalls" in {
        reason["code"] for reason in result["publication_readiness"]["reasons"]
    }


def test_unknown_calendar_is_valid_but_review_required():
    result = verify_final_candidate(_candidate(), _problem(calendar_status="unknown"))
    assert result["ok"] is True
    assert result["publishable"] is False
    assert result["publication_readiness"]["status"] == "REVIEW_REQUIRED"


def test_configured_round_mismatch_uses_effective_input_constrained_rounds():
    teams = [
        {"club": f"Club {idx}", "label": f"Team {idx}", "age_group": "U10"}
        for idx in range(1, 5)
    ]
    games = [
        {"home": "Team 1", "away": "Team 2", "parallel_slot": 0, "round_number": 1},
        {"home": "Team 3", "away": "Team 4", "parallel_slot": 1, "round_number": 1},
        {"home": "Team 1", "away": "Team 3", "parallel_slot": 0, "round_number": 2},
        {"home": "Team 2", "away": "Team 4", "parallel_slot": 1, "round_number": 2},
        {"home": "Team 1", "away": "Team 4", "parallel_slot": 0, "round_number": 3},
        {"home": "Team 2", "away": "Team 3", "parallel_slot": 1, "round_number": 3},
    ]
    problem = _problem()
    problem["teams"] = teams
    problem["rounds_per_tournament"] = {"U10": 5}
    problem["parallel_games"] = {"U10": 3}

    result = verify_final_candidate(_candidate(teams=teams, games=games), problem)

    assert result["ok"] is True, result["violations"]
    assert result["input_constrained_shapes"][0]["effective_round_count"] == 3


def test_avoidable_configured_round_mismatch_still_blocks():
    registered = [
        {"club": f"Club {idx}", "label": f"Team {idx}", "age_group": "U10"}
        for idx in range(1, 9)
    ]
    teams = registered[:4]
    games = [
        {"home": "Team 1", "away": "Team 2", "parallel_slot": 0, "round_number": 1},
        {"home": "Team 3", "away": "Team 4", "parallel_slot": 1, "round_number": 1},
        {"home": "Team 1", "away": "Team 3", "parallel_slot": 0, "round_number": 2},
        {"home": "Team 2", "away": "Team 4", "parallel_slot": 1, "round_number": 2},
        {"home": "Team 1", "away": "Team 4", "parallel_slot": 0, "round_number": 3},
        {"home": "Team 2", "away": "Team 3", "parallel_slot": 1, "round_number": 3},
    ]
    problem = _problem()
    problem["teams"] = registered
    problem["rounds_per_tournament"] = {"U10": 5}
    problem["parallel_games"] = {"U10": 3}

    result = verify_final_candidate(_candidate(teams=teams, games=games), problem)

    assert result["ok"] is False
    assert "configured_round_count_mismatch" in {item["code"] for item in result["violations"]}


def test_problem_less_verification_cannot_be_publishable():
    # An even-sized roster so the fixture only exercises the "no problem ->
    # never publishable" behavior under test, independent of the (also
    # problem-less, therefore strict) no-bye rule.
    even_teams = [
        {"club": "Jar", "label": "Jar 1", "age_group": "U10"},
        {"club": "Kongsberg", "label": "Kongsberg 1", "age_group": "U10"},
        {"club": "Holmen", "label": "Holmen 1", "age_group": "U10"},
        {"club": "Jutul", "label": "Jutul 1", "age_group": "U10"},
    ]
    # A standard 4-team round-robin (circle method): no bye rounds, no team
    # double-booked within a round.
    even_games = [
        {"home": "Jar 1", "away": "Kongsberg 1", "parallel_slot": 0, "round_number": 1},
        {"home": "Holmen 1", "away": "Jutul 1", "parallel_slot": 1, "round_number": 1},
        {"home": "Jar 1", "away": "Holmen 1", "parallel_slot": 0, "round_number": 2},
        {"home": "Kongsberg 1", "away": "Jutul 1", "parallel_slot": 1, "round_number": 2},
        {"home": "Jar 1", "away": "Jutul 1", "parallel_slot": 0, "round_number": 3},
        {"home": "Kongsberg 1", "away": "Holmen 1", "parallel_slot": 1, "round_number": 3},
    ]
    result = verify_final_candidate(_candidate(teams=even_teams, games=even_games))
    assert result["ok"] is True
    assert result["publishable"] is False
    assert result["publication_readiness"]["status"] == "REVIEW_REQUIRED"


def test_rules_doc_does_not_claim_same_club_games_are_filtered():
    from pathlib import Path

    text = Path("docs/rvv-miniputt-rules-report.md").read_text(encoding="utf-8")
    assert "hoppes det over kamper mellom to lag fra samme klubb" not in text
    assert "Klubb-interne kamper følger round-robin" in text
