from tournament_scheduler.planning.verification import verify_final_candidate


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


def test_source_confirmed_below_floor_booking_reclassifies_to_finding():
    # A source-confirmed accepted booking interval below the governing floor is
    # a durable finding at the audit boundary, not a hard planning violation.
    problem = {
        **_problem(),
        "ice_time_minutes": {"U10": 120},
        "ice_time_minutes_overrides": {"t1": 110},
        "calendar_booking_associations": [
            {
                "id": "calendar_booking:event:t1",
                "status": "active",
                "event_fingerprint": "event",
                "tournament_id": "t1",
                "club": "Jar",
                "date": "2026-02-01",
                "start": "10:00",
                "end": "11:50",
                "tournament_facts": {
                    "host_club": "Jar",
                    "arena": "Jarahallen",
                    "date": "2026-02-01",
                    "start_time": "10:00",
                    "age_group": "U10",
                },
                "tournament_interval": {
                    "date": "2026-02-01",
                    "start_time": "10:00",
                    "duration_minutes": "110",
                    "end_time": "11:50",
                },
            }
        ],
    }

    result = verify_final_candidate(_candidate(), problem)

    assert result["ok"], result["violations"]
    assert [w["code"] for w in result["booking_feasibility_warnings"]] == [
        "ice_time_governing_minimum"
    ]
    assert all(w.get("accepted_booking_interval") for w in result["booking_feasibility_warnings"])
    informational = result["publication_readiness"]["informational_reasons"]
    assert any(item["code"] == "booking_feasibility_warnings" for item in informational)


def _accepted_short_booking_problem() -> dict:
    # A source-confirmed 110-minute booking below the 120-minute governing floor,
    # the same accepted interval as the reclassification test above.
    return {
        **_problem(),
        "ice_time_minutes": {"U10": 120},
        "ice_time_minutes_overrides": {"t1": 110},
        "calendar_booking_associations": [
            {
                "id": "calendar_booking:event:t1",
                "status": "active",
                "event_fingerprint": "event",
                "tournament_id": "t1",
                "club": "Jar",
                "date": "2026-02-01",
                "start": "10:00",
                "end": "11:50",
                "tournament_facts": {
                    "host_club": "Jar",
                    "arena": "Jarahallen",
                    "date": "2026-02-01",
                    "start_time": "10:00",
                    "age_group": "U10",
                },
                "tournament_interval": {
                    "date": "2026-02-01",
                    "start_time": "10:00",
                    "duration_minutes": "110",
                    "end_time": "11:50",
                },
            }
        ],
    }


def test_accepted_short_booking_does_not_mask_independent_team_double_booking():
    # Accepting a short interval must not hide a separate hard planning violation:
    # the same team plays twice in round 1 of the accepted tournament.
    games = [
        {"home": "Jar 1", "away": "Jar 2", "parallel_slot": 0, "round_number": 1},
        {"home": "Jar 1", "away": "Jar 3", "parallel_slot": 1, "round_number": 1},
        {"home": "Jar 2", "away": "Jar 3", "parallel_slot": 0, "round_number": 2},
    ]

    result = verify_final_candidate(_candidate(games=games), _accepted_short_booking_problem())

    assert result["ok"] is False
    hard_codes = {violation["code"] for violation in result["violations"]}
    assert "team_double_booked_in_round" in hard_codes
    assert "ice_time_governing_minimum" not in hard_codes
    assert [w["code"] for w in result["booking_feasibility_warnings"]] == [
        "ice_time_governing_minimum"
    ]


def test_accepted_short_booking_does_not_mask_actual_playing_shortfall():
    # An accepted short interval is not a licence to play fewer games: dropping the
    # round-3 pairing is a real playing shortfall and must stay a hard violation.
    games = [
        {"home": "Jar 1", "away": "Jar 2", "parallel_slot": 0, "round_number": 1},
        {"home": "Jar 1", "away": "Jar 3", "parallel_slot": 0, "round_number": 2},
    ]

    result = verify_final_candidate(_candidate(games=games), _accepted_short_booking_problem())

    assert result["ok"] is False
    assert [violation["code"] for violation in result["violations"]] == [
        "round_robin_missing_pair"
    ]
    assert [w["code"] for w in result["booking_feasibility_warnings"]] == [
        "ice_time_governing_minimum"
    ]


def test_accepted_short_booking_with_valid_schedule_is_publishable_with_warning_only():
    # Baseline for the two tests above: with a complete, valid schedule the accepted
    # short interval must produce no hard violation, only the informational warning.
    result = verify_final_candidate(_candidate(), _accepted_short_booking_problem())

    assert result["ok"] is True, result["violations"]
    assert result["violations"] == []
    assert [w["code"] for w in result["booking_feasibility_warnings"]] == [
        "ice_time_governing_minimum"
    ]


def _second_tournament_same_arena(start_time: str) -> dict:
    return {
        "id": "t2",
        "date": "2026-02-01",
        "arena": "Jarahallen",
        "host_club": "Jar",
        "age_group": "U10",
        "start_time": start_time,
        "teams": [_team("Jar 1"), _team("Jar 2"), _team("Jar 3")],
        "games": [
            {"home": "Jar 1", "away": "Jar 2", "parallel_slot": 0, "round_number": 1},
            {"home": "Jar 1", "away": "Jar 3", "parallel_slot": 0, "round_number": 2},
            {"home": "Jar 2", "away": "Jar 3", "parallel_slot": 0, "round_number": 3},
        ],
    }


def test_accepted_short_booking_does_not_mask_real_arena_overlap():
    # The accepted 110-minute t1 interval (10:00-11:50) overlaps t2 in the same
    # hall at 11:00. That is a real arena conflict and must stay hard even though
    # the short interval is accepted.
    candidate = _candidate()
    candidate["tournaments"].append(_second_tournament_same_arena("11:00"))

    result = verify_final_candidate(candidate, _accepted_short_booking_problem())

    assert result["ok"] is False
    hard_codes = {violation["code"] for violation in result["violations"]}
    assert "arena_interval_conflict" in hard_codes
    assert "ice_time_governing_minimum" not in hard_codes
    assert [w["code"] for w in result["booking_feasibility_warnings"]] == [
        "ice_time_governing_minimum"
    ]


def test_accepted_short_booking_without_arena_overlap_has_no_arena_conflict():
    # Control for the test above: the same pair in the same hall, but t2 starts
    # after t1 ends (11:50), so the only finding left is the accepted warning.
    candidate = _candidate()
    candidate["tournaments"].append(_second_tournament_same_arena("12:00"))

    result = verify_final_candidate(candidate, _accepted_short_booking_problem())

    assert "arena_interval_conflict" not in {v["code"] for v in result["violations"]}


def test_rejected_calendar_association_is_not_publication_authority():
    # A rejected source association is evidence the host refused. It must not
    # reclassify the below-floor override into an accepted feasibility finding.
    problem = _accepted_short_booking_problem()
    problem["calendar_booking_associations"][0]["status"] = "rejected"

    result = verify_final_candidate(_candidate(), problem)

    assert "ice_time_governing_minimum" in {v["code"] for v in result["violations"]}
    assert result["booking_feasibility_warnings"] == []


def test_host_busy_conflict_beside_accepted_booking_is_unresolved_not_silently_clear():
    # A fixed external busy interval over the accepted 10:00-11:50 window is a
    # host-calendar double-booking. The verifier surfaces it for manual placement
    # rather than hard-blocking, so it must not look publishable-clean either.
    problem = _accepted_short_booking_problem()
    problem["club_busy_intervals"] = {
        "Jar": [
            {
                "date": "2026-02-01",
                "start": "10:30",
                "end": "11:00",
                "availability": "fixed_busy",
                "calendar_event": "Annen aktivitet",
            }
        ]
    }

    result = verify_final_candidate(_candidate(), problem)

    assert result["publication_readiness"]["status"] == "REVIEW_REQUIRED"
    assert [row["tournament_id"] for row in result["manual_external_conflict_placements"]] == ["t1"]
    assert "ice_time_governing_minimum" not in {v["code"] for v in result["violations"]}


def test_stale_or_wrong_source_authority_does_not_reclassify_below_floor():
    # Exact source evidence is required: an association whose recorded date no
    # longer matches the candidate interval (stale/wrong authority) must not
    # downgrade a below-floor override to a feasibility finding.
    problem = {
        **_problem(),
        "ice_time_minutes": {"U10": 120},
        "ice_time_minutes_overrides": {"t1": 110},
        "calendar_booking_associations": [
            {
                "id": "calendar_booking:event:t1",
                "status": "active",
                "event_fingerprint": "event",
                "tournament_id": "t1",
                "club": "Jar",
                "date": "2026-02-02",
                "start": "10:00",
                "end": "11:50",
                "tournament_facts": {
                    "host_club": "Jar",
                    "arena": "Jarahallen",
                    "date": "2026-02-01",
                    "start_time": "10:00",
                    "age_group": "U10",
                },
                "tournament_interval": {
                    "date": "2026-02-01",
                    "start_time": "10:00",
                    "duration_minutes": "110",
                    "end_time": "11:50",
                },
            }
        ],
    }

    result = verify_final_candidate(_candidate(), problem)

    assert "ice_time_governing_minimum" in {v["code"] for v in result["violations"]}
    assert result["booking_feasibility_warnings"] == []


def test_unverified_below_floor_override_remains_hard_invalid():
    # Without source-confirmed evidence matching the exact interval, a below-
    # floor override stays a hard planning violation (no blanket bypass).
    problem = {
        **_problem(),
        "ice_time_minutes": {"U10": 120},
        "ice_time_minutes_overrides": {"t1": 110},
    }

    result = verify_final_candidate(_candidate(), problem)

    assert "ice_time_governing_minimum" in {v["code"] for v in result["violations"]}
    assert result["booking_feasibility_warnings"] == []


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


def test_movable_busy_placement_is_valid_but_requires_host_confirmation():
    """issue #373: a placement inside a host-controlled movable interval is not a
    hard conflict, but it is not unconditionally free either -- it must surface as
    REVIEW_REQUIRED with an explicit host-confirmation requirement."""
    problem = _problem()
    problem["club_busy_intervals"] = {
        "Jar": [
            {
                "date": "2026-02-01",
                "start": "10:00",
                "end": "11:00",
                "kind": "club_controlled",
                "availability": "movable_busy",
                "calendar_event": "Åpen ishall",
                "reason": "host-controlled open ice",
            }
        ]
    }
    result = verify_final_candidate(_candidate(), problem)
    assert result["ok"] is True
    assert result["publishable"] is False
    assert result["publication_readiness"]["status"] == "REVIEW_REQUIRED"
    reasons = {reason["code"] for reason in result["publication_readiness"]["reasons"]}
    assert "movable_host_confirmation_required" in reasons
    assert result["manual_external_conflict_placements"] == []
    used = result["movable_allocations_used"]
    assert used and used[0]["requires_host_confirmation"] is True
    assert used[0]["calendar_event"] == "Åpen ishall"


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
    # The limited-round wording must still describe same-club games as
    # minimized/unavoidable, never as silently filtered out.
    assert "Klubb-interne kamper unngås i begrensede runder" in text
    assert "uunngåelig" in text
