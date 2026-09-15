from copy import deepcopy

from tournament_scheduler.application.decisions import DecisionAction, decide
from tournament_scheduler.host_team_missing_repair import (
    apply_host_team_missing_repair_option,
    build_host_team_missing_decision_context,
    candidate_fingerprint,
    enumerate_host_team_missing_repairs,
)
from tournament_scheduler.planning_contract import verify_candidate


def _team(club, label=None, age="U12", target=None):
    row = {"club": club, "label": label or club, "age_group": age}
    if target is not None:
        row["target_tournament_count"] = target
    return row


def _game(home, away, round_number):
    return {"home": home, "away": away, "round_number": round_number, "parallel_slot": 0}


def _tournament(tid, host, teams, *, arena=None, start="10:00", date="2026-01-10", age_group="U12"):
    labels = [team["label"] for team in teams]
    games = [
        _game(labels[0], labels[3], 1),
        _game(labels[1], labels[2], 1),
        _game(labels[0], labels[2], 2),
        _game(labels[3], labels[1], 2),
        _game(labels[0], labels[1], 3),
        _game(labels[2], labels[3], 3),
    ]
    return {
        "id": tid,
        "date": date,
        "age_group": age_group,
        "host_club": host,
        "arena": arena or f"{host} Arena",
        "start_time": start,
        "duration_minutes": 120,
        "teams": teams,
        "games": games,
    }


def _problem(extra_teams=(), **overrides):
    teams = [
        _team("Host", "Host 1"),
        _team("Host", "Host 2"),
        _team("B"),
        _team("C"),
        _team("D"),
        _team("E"),
        *extra_teams,
    ]
    problem = {
        "teams": teams,
        "parallel_games": {"U12": 2},
        "club_arenas": {"Host": "Host Arena", "B": "B Arena", "C": "C Arena", "D": "D Arena", "E": "E Arena"},
        "club_calendar_status": {"Host": "known", "B": "known", "C": "known", "D": "known", "E": "known"},
        "club_busy_intervals": {},
    }
    problem.update(overrides)
    return problem


def _invalid_candidate():
    teams = [_team("B"), _team("C"), _team("D"), _team("E")]
    return {"schema_version": 1, "tournaments": [_tournament("t1", "Host", teams)]}


def test_host_club_sibling_teams_expose_replacement_options_with_deficit_effects():
    candidate = _invalid_candidate()
    problem = _problem(target_tournament_count=1)

    repair_set = enumerate_host_team_missing_repairs(candidate, problem, run_id="r1")

    assert not verify_candidate(candidate, problem)["ok"]
    options = repair_set["options"]
    host_options = [o for o in options if o["action"] == "replace_participant" and o["arguments"]["add_team"]["club"] == "Host"]
    assert {o["arguments"]["add_team"]["label"] for o in host_options} == {"Host 1", "Host 2"}
    assert all("participation_deficit" in o["effects"] for o in host_options)


def test_same_date_host_team_is_rejected_with_explicit_reason():
    candidate = _invalid_candidate()
    candidate["tournaments"].append(_tournament("t2", "B", [_team("Host", "Host 1"), _team("B"), _team("C"), _team("D")], arena="B Arena"))
    problem = _problem()

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    assert any(r["reason"] == "already_plays_same_date" and r["team"]["label"] == "Host 1" for r in repair_set["rejected_candidates"])


def test_team_at_participation_max_is_rejected_with_explicit_reason():
    candidate = _invalid_candidate()
    candidate["tournaments"].append(_tournament("t2", "B", [_team("Host", "Host 1"), _team("B"), _team("C"), _team("D")], arena="B Arena"))
    candidate["tournaments"][1]["date"] = "2026-01-17"
    problem = _problem(target_tournament_count=1)

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    assert any(r["reason"] == "at_participation_max" and r["team"]["label"] == "Host 1" for r in repair_set["rejected_candidates"])


def test_rehost_option_exposes_arena_and_time_evidence():
    candidate = _invalid_candidate()
    problem = _problem()

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    option = next(o for o in repair_set["options"] if o["action"] == "rehost" and o["arguments"]["host_club"] == "B")
    assert option["arguments"]["arena"] == "B Arena"
    assert option["evidence"]["start_time"] == "10:00"
    assert option["evidence"]["end_time"] == "12:00"


def test_represented_club_external_conflict_is_rejected_with_reason():
    candidate = _invalid_candidate()
    problem = _problem(club_busy_intervals={"B": [{"date": "2026-01-10", "start": "09:00", "end": "18:00"}]})

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    assert any(r["reason"] == "external_calendar_conflict" and r["host_club"] == "B" for r in repair_set["rejected_candidates"])


def test_harness_selects_stable_option_id_and_participant_repair_is_atomic_and_verified():
    candidate = _invalid_candidate()
    original = deepcopy(candidate)
    problem = _problem()
    context = build_host_team_missing_decision_context(candidate, problem, run_id="run-1")
    option = next(o for o in context.facts["repair_options"] if o["action"] == "replace_participant")

    result = decide(context, DecisionAction(action_id="apply_repair_option", arguments={"option_id": option["option_id"], "candidate_fingerprint": context.facts["candidate_fingerprint"]}))
    assert result.accepted

    applied = apply_host_team_missing_repair_option(candidate, problem, option_id=option["option_id"], expected_fingerprint=context.facts["candidate_fingerprint"], run_id="run-1")
    assert applied["ok"]
    assert applied["verification"]["ok"]
    assert candidate == original
    assert applied["before_fingerprint"] != applied["after_fingerprint"]
    repaired_tournament = applied["candidate"]["tournaments"][0]
    assert any(team["club"] == "Host" for team in repaired_tournament["teams"])
    assert len(repaired_tournament["games"]) == 6


def test_selected_rehost_recomputes_placement_and_passes_full_verifier():
    candidate = _invalid_candidate()
    problem = _problem()
    repair_set = enumerate_host_team_missing_repairs(candidate, problem)
    option = next(o for o in repair_set["options"] if o["action"] == "rehost" and o["arguments"]["host_club"] == "B")

    applied = apply_host_team_missing_repair_option(candidate, problem, option_id=option["option_id"], expected_fingerprint=repair_set["candidate_fingerprint"])

    assert applied["ok"]
    tournament = applied["candidate"]["tournaments"][0]
    assert tournament["host_club"] == "B"
    assert tournament["arena"] == "B Arena"
    assert applied["verification"]["ok"]


def test_stale_option_or_failed_application_does_not_mutate_candidate():
    candidate = _invalid_candidate()
    original = deepcopy(candidate)
    problem = _problem()
    repair_set = enumerate_host_team_missing_repairs(candidate, problem)
    wrong_fingerprint = candidate_fingerprint({"different": True})

    stale = apply_host_team_missing_repair_option(candidate, problem, option_id=repair_set["options"][0]["option_id"], expected_fingerprint=wrong_fingerprint)

    assert not stale["ok"]
    assert stale["reason"] == "stale_candidate_fingerprint"
    assert candidate == original


def _arenas(*clubs, status="known"):
    return {club: f"{club} Arena" for club in clubs}, {club: status for club in clubs}


def test_shared_joint_registration_resolves_to_legal_physical_constituents():
    """A joint registration represents either physical host, and a rehost may
    target any represented physical constituent of the current participants."""
    arenas, statuses = _arenas("Jutul", "Jar", "Ull", "B", "C", "D", "E")
    candidate = {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "Jutul", [_team("Jar/Ull"), _team("C"), _team("D"), _team("E")]),
        ],
    }
    problem = {
        "teams": [_team("Jutul/Jar"), _team("Jar/Ull"), _team("C"), _team("D"), _team("E")],
        "parallel_games": {"U12": 2},
        "club_arenas": arenas,
        "club_calendar_status": statuses,
        "club_busy_intervals": {},
    }

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    # A team registered as "Jutul/Jar" satisfies the Jutul host obligation.
    participant_options = [o for o in repair_set["options"] if o["action"] == "replace_participant"]
    assert {o["arguments"]["add_team"]["club"] for o in participant_options} == {"Jutul/Jar"}
    # "Jar/Ull" participants resolve to two physical host candidates.
    rehost_hosts = {o["arguments"]["host_club"] for o in repair_set["options"] if o["action"] == "rehost"}
    assert {"Jar", "Ull"} <= rehost_hosts


def test_host_club_team_in_wrong_age_group_is_rejected_with_explicit_reason():
    arenas, statuses = _arenas("Host", "B", "C", "D", "E")
    candidate = {
        "schema_version": 1,
        "tournaments": [_tournament("t1", "Host", [_team("B"), _team("C"), _team("D"), _team("E")])],
    }
    problem = {
        "teams": [
            _team("Host", "Host 1"),
            _team("Host", "Host U16", age="U16"),
            _team("B"),
            _team("C"),
            _team("D"),
            _team("E"),
        ],
        "parallel_games": {"U12": 2},
        "club_arenas": arenas,
        "club_calendar_status": statuses,
        "club_busy_intervals": {},
    }

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    mismatched = [
        r for r in repair_set["rejected_candidates"]
        if r["reason"] == "age_group_mismatch" and r["team"]["label"] == "Host U16"
    ]
    assert mismatched and mismatched[0]["registered_age_group"] == "U16"


def test_incompatible_half_target_is_rejected_with_explicit_reason():
    arenas, statuses = _arenas("Host", "B", "C", "D", "E")
    candidate = {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "Host", [_team("B"), _team("C"), _team("D"), _team("E")], date="2026-01-10"),
            _tournament("t2", "B", [_team("Host", "Host 1"), _team("B"), _team("C"), _team("D")], date="2026-01-17", arena="B Arena"),
        ],
    }
    problem = {
        "teams": [_team("Host", "Host 1"), _team("B"), _team("C"), _team("D"), _team("E")],
        "parallel_games": {"U12": 2},
        "club_arenas": arenas,
        "club_calendar_status": statuses,
        "club_busy_intervals": {},
        "start_date": "2026-01-01",
        "end_date": "2026-06-30",
        "participation_targets_by_age_group": {"U12": {"before_christmas": 1, "after_christmas": 1}},
    }

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    assert any(
        r["reason"] == "incompatible_half_target" and r["team"]["label"] == "Host 1"
        for r in repair_set["rejected_candidates"]
    )


def test_hard_per_club_cap_violation_is_rejected_with_explicit_reason():
    """A repair that leaves a club above the hard per-tournament cap must be
    rejected by the canonical verifier with its explicit code, never exposed."""
    b_teams = [_team("B", f"B{index}") for index in range(1, 6)]
    candidate = {"schema_version": 1, "tournaments": [_tournament("t1", "Host", list(b_teams))]}
    problem = {
        "teams": [_team("Host", "Host 1"), *b_teams],
        "parallel_games": {"U12": 4},
        "club_arenas": {"Host": "Host Arena", "B": "B Arena"},
        "club_calendar_status": {"Host": "known", "B": "known"},
        "club_busy_intervals": {},
    }

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    assert repair_set["options"] == []
    assert any(r["reason"] == "club_hard_max_exceeded" for r in repair_set["rejected_candidates"])


def test_no_explicit_target_still_offers_replacement_when_growth_breaks_shape():
    """Without an explicit target, an append that breaks the legal roster
    shape must not hide the replacement family that keeps the size legal."""
    teams = [_team("B", age="U10"), _team("C", age="U10"), _team("D", age="U10"), _team("E", age="U10")]
    candidate = {
        "schema_version": 1,
        "tournaments": [_tournament("t1", "Host", teams, age_group="U10")],
    }
    problem = {
        "teams": [_team("Host", "Host 1", age="U10"), *teams, _team("F", age="U10")],
        "club_arenas": {club: f"{club} Arena" for club in ("Host", "B", "C", "D", "E", "F")},
        "club_calendar_status": {club: "known" for club in ("Host", "B", "C", "D", "E", "F")},
        "club_busy_intervals": {},
    }

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    assert {
        (o["action"], o["arguments"].get("remove_team", {}).get("label"))
        for o in repair_set["options"]
        if o["action"] == "replace_participant"
    } == {
        ("replace_participant", "B"),
        ("replace_participant", "C"),
        ("replace_participant", "D"),
        ("replace_participant", "E"),
    }
    # Appending would break the no-bye shape; that rejection stays visible.
    assert any(r["reason"] == "bye_team_not_allowed" for r in repair_set["rejected_candidates"])


def test_no_legal_local_option_returns_rejection_evidence_and_escalation_actions():
    """When no local repair is legal, the context must hand back why each
    candidate failed plus the broader search/escalation actions."""
    arenas, statuses = _arenas("Host", "F", "G")
    candidate = {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "Host", [_team("B"), _team("C"), _team("D"), _team("E")]),
            _tournament("t2", "F", [_team("Host", "Host 1"), _team("Host", "Host 2"), _team("F"), _team("G")]),
        ],
    }
    problem = {
        "teams": [
            _team("Host", "Host 1"),
            _team("Host", "Host 2"),
            _team("B"),
            _team("C"),
            _team("D"),
            _team("E"),
            _team("F"),
            _team("G"),
        ],
        "parallel_games": {"U12": 2},
        # The represented clubs B..E have no trusted arena evidence, so no
        # rehost is legal either.
        "club_arenas": arenas,
        "club_calendar_status": statuses,
        "club_busy_intervals": {},
    }
    context = build_host_team_missing_decision_context(candidate, problem, run_id="run-1")

    assert context.facts["repair_options"] == []
    reasons = {r["reason"] for r in context.facts["rejected_candidates"]}
    assert {"already_plays_same_date", "arena_not_configured"} <= reasons
    assert context.available_actions == ("optimize_plan", "request_operator")
    assert "apply_repair_option" not in context.available_actions
    assert context.warnings


def test_context_answers_registration_question_from_canonical_input_evidence():
    """Canonical registered teams answer ''does this club field the age group?''
    before any operator question -- the context exposes the repair instead."""
    candidate = _invalid_candidate()
    problem = _problem()

    context = build_host_team_missing_decision_context(candidate, problem, run_id="run-1")

    assert context.capability == "host_team_missing_repair"
    assert "apply_repair_option" in context.available_actions
    assert context.requires_human_approval is False
    registered_labels = {
        option["arguments"]["add_team"]["label"]
        for option in context.facts["repair_options"]
        if option["action"] in {"append_participant", "replace_participant"}
    }
    assert registered_labels == {"Host 1", "Host 2"}


def _u11_round_robin(tid, day, host, teams):
    labels = [team["label"] for team in teams]
    rotation, games = list(labels), []
    for round_number in range(1, len(rotation)):
        for index in range(len(rotation) // 2):
            games.append(
                {
                    "home": rotation[index],
                    "away": rotation[-1 - index],
                    "parallel_slot": 0,
                    "round_number": round_number,
                }
            )
        rotation = [rotation[0]] + [rotation[-1]] + rotation[1:-1]
    return {
        "id": tid,
        "date": day,
        "age_group": "U11",
        "host_club": host,
        "arena": f"{host} Arena",
        "start_time": "10:00",
        "duration_minutes": 90,
        "teams": teams,
        "games": games,
    }


def _frisk_style_u11_fixture():
    """Faithful U11 fixture: a Frisk Asker-hosted tournament whose participants
    are all other clubs, while Frisk Asker has two registered U11 teams.

    Built through the canonical ``build_planning_problem`` contract (not a
    hand-rolled problem dict) so the production problem shape, club arena
    resolution and calendar evidence are exercised end to end.
    """
    from datetime import date

    from tournament_scheduler.planning_contract import build_planning_problem

    registered = [
        _team(club, f"{club} {index}", age="U11")
        for club in ("Frisk Asker", "Jar", "Kongsberg", "Holmen", "Ringerike")
        for index in (1, 2)
    ]
    config = {
        "teams": registered,
        "parallel_games": {"U11": 2},
        "rounds_per_tournament": {"U11": 3},
        "ice_time_minutes": {"U11": 90},
    }
    scraping = {
        "club_calendar_status": {team["club"]: "known" for team in registered},
        "events_by_club": {},
    }
    problem = build_planning_problem(config, scraping, date(2026, 9, 1), date(2027, 4, 30))
    candidate = {
        "schema_version": 1,
        "tournaments": [
            _u11_round_robin(
                "t1",
                "2026-10-10",
                "Frisk Asker",
                [_team("Jar", "Jar 1", age="U11"), _team("Kongsberg", "Kongsberg 1", age="U11"),
                 _team("Holmen", "Holmen 1", age="U11"), _team("Ringerike", "Ringerike 1", age="U11")],
            ),
            _u11_round_robin(
                "t2",
                "2026-10-17",
                "Jar",
                [_team("Jar", "Jar 2", age="U11"), _team("Kongsberg", "Kongsberg 2", age="U11"),
                 _team("Holmen", "Holmen 2", age="U11"), _team("Ringerike", "Ringerike 2", age="U11")],
            ),
        ],
    }
    return candidate, problem


def test_production_frisk_style_u11_host_missing_is_repaired_atomically():
    candidate, problem = _frisk_style_u11_fixture()
    original = deepcopy(candidate)

    verification = verify_candidate(candidate, problem)
    assert [v["code"] for v in verification["violations"]] == ["host_team_missing"]

    context = build_host_team_missing_decision_context(candidate, problem, run_id="run-1")
    assert "apply_repair_option" in context.available_actions
    registered_host_teams = {
        option["arguments"]["add_team"]["label"]
        for option in context.facts["repair_options"]
        if option["action"] == "replace_participant"
    }
    assert registered_host_teams == {"Frisk Asker 1", "Frisk Asker 2"}

    option = next(o for o in context.facts["repair_options"] if o["action"] == "replace_participant")
    decision = decide(
        context,
        DecisionAction(
            action_id="apply_repair_option",
            arguments={
                "option_id": option["option_id"],
                "candidate_fingerprint": context.facts["candidate_fingerprint"],
            },
        ),
    )
    assert decision.accepted

    applied = apply_host_team_missing_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=context.facts["candidate_fingerprint"],
        run_id="run-1",
    )

    assert applied["ok"] and applied["verification"]["ok"]
    assert candidate == original
    repaired = applied["candidate"]["tournaments"][0]
    assert any(team["club"] == "Frisk Asker" for team in repaired["teams"])
    assert len(repaired["games"]) == 6


def test_pinned_tournament_reports_manual_restriction_for_every_mutation():
    candidate = _invalid_candidate()
    problem = _problem()
    problem["manual_adjustments"] = {"pinned_tournament_ids": ["t1"]}

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    assert repair_set["options"] == []
    reasons = {entry["reason"] for entry in repair_set["rejected_candidates"]}
    assert reasons == {"manual_restriction_forbids_mutation"}


def _surplus_remove_fixture():
    teams_t1 = [_team("B"), _team("C"), _team("D"), _team("E")]
    teams_t2 = [_team("Host", "Host 1"), _team("F"), _team("G"), _team("H")]
    candidate = {
        "schema_version": 1,
        "arena_counts": {"Host Arena": 2},
        "team_tournament_participations": {team["label"]: 1 for team in [*teams_t1, *teams_t2]},
        "team_game_counts": {team["label"]: 3 for team in [*teams_t1, *teams_t2]},
        "unresolved_tournament_placements": [{"tournament_id": "t1", "reason": "stale if kept"}],
        "tournaments": [
            _tournament("t1", "Host", teams_t1, date="2026-01-10"),
            _tournament("t2", "Host", teams_t2, date="2026-01-10"),
        ],
    }
    problem = {
        "teams": [_team("Host", "Host 1"), *teams_t1, *teams_t2[1:]],
        "parallel_games": {"U12": 2},
        "club_arenas": {"Host": "Host Arena"},
        "club_calendar_status": {"Host": "known"},
        "club_busy_intervals": {},
    }
    return candidate, problem


def test_surplus_tournament_with_no_participant_or_rehost_repair_exposes_remove_option():
    candidate, problem = _surplus_remove_fixture()

    repair_set = enumerate_host_team_missing_repairs(candidate, problem, run_id="run-1")

    options = [o for o in repair_set["options"] if o["action"] == "remove_tournament"]
    assert len(options) == 1
    option = options[0]
    assert option["hard_feasible"] is True
    assert option["effects"]["host_team_missing"] == -1
    assert option["effects"]["tournament_count"] == -1
    assert option["effects"]["hosting.Host.U12"] == -1
    assert option["effects"]["participation.B"] == -1
    assert option["effects"]["new_hard_violations"] == 0
    reasons = {r["reason"] for r in repair_set["rejected_candidates"]}
    assert {"already_plays_same_date", "arena_not_configured"} <= reasons


def test_selected_remove_tournament_is_atomic_verified_and_refreshes_derived_state():
    candidate, problem = _surplus_remove_fixture()
    original = deepcopy(candidate)
    repair_set = enumerate_host_team_missing_repairs(candidate, problem)
    option = next(o for o in repair_set["options"] if o["action"] == "remove_tournament")

    applied = apply_host_team_missing_repair_option(
        candidate,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=repair_set["candidate_fingerprint"],
    )

    assert applied["ok"]
    assert applied["verification"]["ok"]
    assert candidate == original
    assert [t["id"] for t in applied["candidate"]["tournaments"]] == ["t2"]
    assert applied["candidate"]["arena_counts"] == {"Host Arena": 1}
    assert "B" not in applied["candidate"]["team_tournament_participations"]
    assert applied["candidate"]["unresolved_tournament_placements"] == []


def test_remove_tournament_rejected_when_it_creates_new_hosting_obligation():
    candidate = _invalid_candidate()
    candidate["tournaments"].append(
        _tournament("t2", "F", [_team("Host", "Host 1"), _team("F"), _team("G"), _team("H")], date="2026-01-10")
    )
    problem = {
        "teams": [
            _team("Host", "Host 1"),
            _team("B"),
            _team("C"),
            _team("D"),
            _team("E"),
            _team("F"),
            _team("G"),
            _team("H"),
        ],
        "parallel_games": {"U12": 2},
        "club_arenas": {"Host": "Host Arena"},
        "club_calendar_status": {"Host": "known"},
        "club_busy_intervals": {},
    }
    # No legal participant or represented rehost, so removal is evaluated; it
    # must still be rejected because Host would no longer host its U12 share.

    repair_set = enumerate_host_team_missing_repairs(candidate, problem)

    assert [o for o in repair_set["options"] if o["action"] == "remove_tournament"] == []
    assert any(r["reason"] == "hosting_obligation_would_be_unresolved" for r in repair_set["rejected_candidates"])
