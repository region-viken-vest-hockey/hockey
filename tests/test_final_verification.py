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


def test_problem_less_verification_cannot_be_publishable():
    result = verify_final_candidate(_candidate())
    assert result["ok"] is True
    assert result["publishable"] is False
    assert result["publication_readiness"]["status"] == "REVIEW_REQUIRED"


def test_rules_doc_does_not_claim_same_club_games_are_filtered():
    from pathlib import Path

    text = Path("docs/rvv-miniputt-rules-report.md").read_text(encoding="utf-8")
    assert "hoppes det over kamper mellom to lag fra samme klubb" not in text
    assert "Klubb-interne kamper følger round-robin" in text
