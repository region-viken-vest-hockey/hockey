"""Unit tests for tournament_scheduler.stage3_optimizer (issue #257, scope item 5)."""

from __future__ import annotations

import random

import pytest

from tournament_scheduler.planning_contract import score_candidate, verify_candidate
from tournament_scheduler.stage3_optimizer import optimize_candidate, optimize_candidate_pareto


def _team(club: str, label: str, age_group: str) -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(t_id: str, date_str: str, arena: str, age_group: str, teams: list[dict]) -> dict:
    game_pairs = [(a["label"], b["label"]) for i, a in enumerate(teams) for b in teams[i + 1 :]]
    return {
        "id": t_id,
        "date": date_str,
        "arena": arena,
        "age_group": age_group,
        "host_club": teams[0]["club"] if teams else None,
        "teams": teams,
        "games": [
            {"home": home, "away": away, "parallel_slot": 0, "round_number": 1}
            for home, away in game_pairs
        ],
    }


def _clustered_candidate() -> dict:
    """A deliberately bad candidate: the same two quartets meet twice each."""
    teams = {f"T{i}": _team(f"Club{i}", f"T{i}", "U10") for i in range(1, 9)}
    group_a = [teams["T1"], teams["T2"], teams["T3"], teams["T4"]]
    group_b = [teams["T5"], teams["T6"], teams["T7"], teams["T8"]]
    return {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "2026-01-05", "Arena1", "U10", group_a),
            _tournament("t2", "2026-02-04", "Arena1", "U10", group_a),
            _tournament("t3", "2026-03-06", "Arena5", "U10", group_b),
            _tournament("t4", "2026-04-05", "Arena5", "U10", group_b),
        ],
    }


class TestOptimizeCandidate:
    def test_preserves_participation_counts(self):
        candidate = _clustered_candidate()
        before = score_candidate(candidate)["participation"]["counts_by_team"]

        optimized = optimize_candidate(candidate, iterations=2000, seed=1)

        after = score_candidate(optimized)["participation"]["counts_by_team"]
        assert after == before

    def test_preserves_tournament_roster_sizes_and_skeleton(self):
        candidate = _clustered_candidate()
        optimized = optimize_candidate(candidate, iterations=2000, seed=1)

        by_id = {t["id"]: t for t in candidate["tournaments"]}
        for t in optimized["tournaments"]:
            original = by_id[t["id"]]
            assert len(t["teams"]) == len(original["teams"])
            assert t["date"] == original["date"]
            assert t["arena"] == original["arena"]
            assert t["age_group"] == original["age_group"]

    def test_reduces_repeated_pairings(self):
        candidate = _clustered_candidate()
        before = score_candidate(candidate)["opponent_diversity"]

        optimized = optimize_candidate(candidate, iterations=3000, seed=1)
        after = score_candidate(optimized)["opponent_diversity"]

        assert after["max_pair_repeat"] <= before["max_pair_repeat"]
        assert after["pairs_meeting_3_plus"] <= before["pairs_meeting_3_plus"]
        assert after["unique_pairs"] >= before["unique_pairs"]

    def test_optimized_candidate_still_passes_hard_verification(self):
        candidate = _clustered_candidate()
        optimized = optimize_candidate(candidate, iterations=2000, seed=1)

        result = verify_candidate(optimized)
        assert result["ok"], result["violations"]

    def test_deterministic_for_fixed_seed(self):
        candidate = _clustered_candidate()
        a = optimize_candidate(candidate, iterations=1500, seed=42)
        b = optimize_candidate(candidate, iterations=1500, seed=42)
        assert a["tournaments"] == b["tournaments"]

    def test_zero_iterations_is_a_no_op_copy(self):
        candidate = _clustered_candidate()
        optimized = optimize_candidate(candidate, iterations=0, seed=1)
        assert optimized["tournaments"] == candidate["tournaments"]
        assert optimized is not candidate

    def test_untouched_when_only_one_tournament_per_age_group(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-01-10", "Jarhallen", "U10", teams)]}
        optimized = optimize_candidate(candidate, iterations=500, seed=1)
        assert optimized["tournaments"] == candidate["tournaments"]

    def test_cancelled_tournaments_pass_through_unchanged(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        cancelled = _tournament("tc", "2026-01-10", "Jarhallen", "U10", teams)
        cancelled["cancelled"] = True
        candidate = _clustered_candidate()
        candidate["tournaments"].append(cancelled)

        optimized = optimize_candidate(candidate, iterations=500, seed=1)

        optimized_by_id = {t["id"]: t for t in optimized["tournaments"]}
        assert optimized_by_id["tc"] == cancelled


class TestPerAgeGroupWeights:
    def test_resolve_weights_falls_back_to_base(self):
        from tournament_scheduler.stage3_optimizer import DEFAULT_WEIGHTS, _resolve_weights

        resolved = _resolve_weights(DEFAULT_WEIGHTS, {"JU12": {"gap_under_7": 99.0}}, "JU14")
        assert resolved == DEFAULT_WEIGHTS

    def test_resolve_weights_applies_age_group_override_on_top_of_base(self):
        from tournament_scheduler.stage3_optimizer import DEFAULT_WEIGHTS, _resolve_weights

        resolved = _resolve_weights(DEFAULT_WEIGHTS, {"JU12": {"gap_under_7": 99.0}}, "JU12")
        assert resolved["gap_under_7"] == 99.0
        assert resolved["pair_repeat"] == DEFAULT_WEIGHTS["pair_repeat"]

    def test_objective_weights_each_age_group_independently(self):
        from tournament_scheduler.stage3_optimizer import DEFAULT_WEIGHTS, _build_slots, _objective

        two_group_candidate = {
            "tournaments": [
                *_clustered_candidate()["tournaments"],
                _tournament(
                    "u12-t1",
                    "2026-01-05",
                    "Arena2",
                    "U12",
                    [_team("Jar", "Jar 1", "U12"), _team("Kongsberg", "Kongsberg 1", "U12")],
                ),
                _tournament(
                    "u12-t2",
                    "2026-02-04",
                    "Arena2",
                    "U12",
                    [_team("Jar", "Jar 1", "U12"), _team("Kongsberg", "Kongsberg 1", "U12")],
                ),
            ]
        }
        slots, _ = _build_slots(two_group_candidate, None)

        baseline = _objective(slots, DEFAULT_WEIGHTS)
        # Zeroing every U12 weight must remove exactly the U12 contribution
        # (the repeated Jar-vs-Kongsberg pair and its zero-gap turnaround)
        # while leaving the U10 contribution untouched.
        zeroed_u12 = _objective(
            slots, DEFAULT_WEIGHTS, {"U12": {name: 0.0 for name in DEFAULT_WEIGHTS}}
        )
        assert zeroed_u12 < baseline

        u10_only = _objective(
            [s for s in slots if s.age_group == "U10"], DEFAULT_WEIGHTS
        )
        assert zeroed_u12 == u10_only


def _turnaround_fixable_by_date_swap_candidate() -> dict:
    """Turnaround violation for team A that a pure date swap (no team swap) can fix.

    Team A appears in t1 (2026-01-01) and t3 (2026-01-06) — a 5-day gap
    (violates the 7-day threshold). t2 sits on 2026-01-15 with unrelated
    teams C/D. Swapping t2's and t3's *dates* only (arenas differ, so no
    conflict) leaves A at 2026-01-01/2026-01-15 — a clean 14-day gap — while
    every team's opponents stay exactly who they were.
    """
    teams_ab = [_team("Jar", "A", "U9"), _team("Kongsberg", "B", "U9")]
    teams_cd = [_team("Ringerike", "C", "U9"), _team("Hønefoss", "D", "U9")]
    teams_ae = [_team("Jar", "A", "U9"), _team("Asker", "E", "U9")]
    return {
        "tournaments": [
            _tournament("t1", "2026-01-01", "Arena1", "U9", teams_ab),
            _tournament("t2", "2026-01-15", "Arena2", "U9", teams_cd),
            _tournament("t3", "2026-01-06", "Arena3", "U9", teams_ae),
        ]
    }


class TestMoveDates:
    def test_off_by_default_dates_unchanged(self):
        candidate = _clustered_candidate()
        optimized = optimize_candidate(candidate, iterations=1000, seed=1)
        assert [t["date"] for t in optimized["tournaments"]] == [
            t["date"] for t in candidate["tournaments"]
        ]

    def test_date_swap_only_preserves_pairings_participation_and_date_multiset(self):
        candidate = _clustered_candidate()
        before_diversity = score_candidate(candidate)["opponent_diversity"]
        before_participation = score_candidate(candidate)["participation"]

        optimized = optimize_candidate(
            candidate, iterations=2000, seed=3, move_dates=True, date_swap_probability=1.0
        )

        assert score_candidate(optimized)["opponent_diversity"] == before_diversity
        assert score_candidate(optimized)["participation"] == before_participation
        assert sorted(t["date"] for t in optimized["tournaments"]) == sorted(
            t["date"] for t in candidate["tournaments"]
        )
        result = verify_candidate(optimized)
        assert result["ok"], result["violations"]

    def test_date_swap_resolves_turnaround_without_touching_pairings(self):
        candidate = _turnaround_fixable_by_date_swap_candidate()
        before = score_candidate(candidate)
        assert before["turnaround"]["gaps_under_days"][7] == 1

        optimized = optimize_candidate(
            candidate,
            iterations=4000,
            seed=7,
            move_dates=True,
            date_swap_probability=1.0,
            weights={"gap_under_7": 50.0, "gap_under_14": 10.0},
        )

        after = score_candidate(optimized)
        assert after["turnaround"]["gaps_under_days"][7] == 0
        assert after["opponent_diversity"] == before["opponent_diversity"]
        result = verify_candidate(optimized)
        assert result["ok"], result["violations"]

    def test_deterministic_for_fixed_seed(self):
        candidate = _clustered_candidate()
        a = optimize_candidate(candidate, iterations=1500, seed=42, move_dates=True)
        b = optimize_candidate(candidate, iterations=1500, seed=42, move_dates=True)
        assert a["tournaments"] == b["tournaments"]

    def test_never_swaps_a_tournament_onto_an_externally_booked_date(self):
        """issue #264 P0: a date swap keeps each tournament's own host/arena,
        but must still reject landing on a date the new host's real calendar
        shows as busy at that time, not just a sibling-candidate collision."""
        t1_teams = [_team("Jar", "A", "U10"), _team("Kongsberg", "B", "U10")]
        t2_teams = [_team("Ringerike", "C", "U10"), _team("Holmen", "D", "U10")]
        t1 = _tournament("t1", "2026-01-05", "Jarhallen", "U10", t1_teams)
        t1["start_time"] = "10:00"
        t2 = _tournament("t2", "2026-01-19", "Ringerikshallen", "U10", t2_teams)
        t2["start_time"] = "10:00"
        candidate = {"schema_version": 1, "tournaments": [t1, t2]}
        problem = {
            "round_length_minutes": {"U10": 30},
            "club_calendar_status": {"Jar": "known", "Ringerike": "known"},
            # Jar's own hall is externally booked on 2026-01-19 (t2's date),
            # so swapping t1 onto that date must never be accepted.
            "club_busy_intervals": {
                "Jar": [{"date": "2026-01-19", "start": "09:00", "end": "12:00"}],
            },
        }
        optimized = optimize_candidate(
            candidate, problem, iterations=1000, seed=11, move_dates=True, date_swap_probability=1.0
        )
        assert [t["date"] for t in optimized["tournaments"]] == ["2026-01-05", "2026-01-19"]


def _host_move_candidate() -> dict:
    teams = [
        _team("Jar", "Jar 1", "U10"),
        _team("Kongsberg", "Kongsberg 1", "U10"),
        _team("Ringerike", "Ringerike 1", "U10"),
        _team("Holmen", "Holmen 1", "U10"),
    ]
    return {
        "schema_version": 1,
        "tournaments": [_tournament("t1", "2026-01-05", "Jarhallen", "U10", teams)],
    }


def _host_move_problem(club_calendar_status: dict | None = None) -> dict:
    return {
        "clubs": {
            "Jar": "Jarhallen",
            "Kongsberg": "Kongsberghallen",
            "Ringerike": "Ringerikshallen",
            "Holmen": "Holmenkollen ishall",
        },
        "round_length_minutes": {"U10": 30},
        "club_calendar_status": club_calendar_status or {},
    }


class TestMoveHosts:
    """issue #262 P1: reassigning host among a tournament's own participants."""

    def test_off_by_default_hosts_unchanged(self):
        candidate = _host_move_candidate()
        optimized = optimize_candidate(candidate, _host_move_problem(), iterations=500, seed=1)
        assert optimized["tournaments"][0]["host_club"] == "Jar"

    def test_reassigns_to_another_participating_club(self):
        candidate = _host_move_candidate()
        problem = _host_move_problem()
        optimized = optimize_candidate(
            candidate, problem, iterations=200, seed=2, move_hosts=True, date_swap_probability=1.0
        )
        t = optimized["tournaments"][0]
        assert t["host_club"] != "Jar"
        assert t["host_club"] in {"Kongsberg", "Ringerike", "Holmen"}
        assert t["arena"] == problem["clubs"][t["host_club"]]
        assert t["host_club"] in {team["club"] for team in t["teams"]}
        # Participation/roster are untouched by a pure host move.
        assert {team["club"] for team in t["teams"]} == {
            team["club"] for team in candidate["tournaments"][0]["teams"]
        }

    def test_never_picks_a_club_with_unknown_calendar_status(self):
        candidate = _host_move_candidate()
        problem = _host_move_problem(
            club_calendar_status={
                "Jar": "known",
                "Kongsberg": "unknown",
                "Ringerike": "unknown",
                "Holmen": "unknown",
            }
        )
        optimized = optimize_candidate(
            candidate, problem, iterations=200, seed=1, move_hosts=True, date_swap_probability=1.0
        )
        assert optimized["tournaments"][0]["host_club"] == "Jar"

    def test_deterministic_for_fixed_seed(self):
        candidate = _host_move_candidate()
        problem = _host_move_problem()
        a = optimize_candidate(candidate, problem, iterations=300, seed=9, move_hosts=True)
        b = optimize_candidate(candidate, problem, iterations=300, seed=9, move_hosts=True)
        assert a["tournaments"] == b["tournaments"]

    def test_never_picks_a_host_with_a_conflicting_external_booking(self):
        """issue #264 P0: 'known' calendar status alone must not be treated
        as proof every candidate host/time is free -- every other
        participating club's own hall is externally booked at the
        tournament's actual time, so the host must stay unchanged even
        though all of them are otherwise eligible move targets."""
        candidate = _host_move_candidate()
        candidate["tournaments"][0]["start_time"] = "10:00"
        problem = _host_move_problem(
            club_calendar_status={
                "Jar": "known",
                "Kongsberg": "known",
                "Ringerike": "known",
                "Holmen": "known",
            }
        )
        problem["club_busy_intervals"] = {
            club: [{"date": "2026-01-05", "start": "09:00", "end": "11:00"}]
            for club in ("Kongsberg", "Ringerike", "Holmen")
        }
        optimized = optimize_candidate(
            candidate, problem, iterations=300, seed=2, move_hosts=True, date_swap_probability=1.0
        )
        assert optimized["tournaments"][0]["host_club"] == "Jar"


class TestMoveSlots:
    """issue #262 P1: reassigning a tournament's start time."""

    def test_off_by_default_start_times_unchanged(self):
        teams = [_team("Jar", "A", "U10"), _team("Kongsberg", "B", "U10")]
        t = _tournament("t1", "2026-01-05", "Jarhallen", "U10", teams)
        t["start_time"] = "10:00"
        candidate = {"schema_version": 1, "tournaments": [t]}
        problem = {"round_length_minutes": {"U10": 30}}

        optimized = optimize_candidate(candidate, problem, iterations=500, seed=1)
        assert optimized["tournaments"][0]["start_time"] == "10:00"

    def test_reassigns_start_time_within_candidate_window(self):
        teams = [_team("Jar", "A", "U10"), _team("Kongsberg", "B", "U10")]
        t = _tournament("t1", "2026-01-05", "Jarhallen", "U10", teams)
        t["start_time"] = "10:00"
        candidate = {"schema_version": 1, "tournaments": [t]}
        problem = {"round_length_minutes": {"U10": 30}}

        optimized = optimize_candidate(
            candidate, problem, iterations=200, seed=1, move_slots=True, date_swap_probability=1.0
        )
        result_t = optimized["tournaments"][0]
        assert result_t["start_time"] != "10:00"
        assert result_t["date"] == t["date"]
        assert result_t["arena"] == t["arena"]

    def test_never_creates_arena_double_booking(self):
        teams1 = [_team("Jar", "A", "U10"), _team("Kongsberg", "B", "U10")]
        teams2 = [_team("Ringerike", "C", "U10"), _team("Holmen", "D", "U10")]
        t1 = _tournament("t1", "2026-01-05", "Jarhallen", "U10", teams1)
        t1["start_time"] = "10:00"
        t2 = _tournament("t2", "2026-01-05", "Jarhallen", "U10", teams2)
        t2["start_time"] = "13:00"
        candidate = {"schema_version": 1, "tournaments": [t1, t2]}
        problem = {"round_length_minutes": {"U10": 30}}

        optimized = optimize_candidate(
            candidate, problem, iterations=500, seed=5, move_slots=True, date_swap_probability=1.0
        )

        result = verify_candidate(optimized, problem)
        assert result["ok"], result["violations"]

    def test_deterministic_for_fixed_seed(self):
        teams = [_team("Jar", "A", "U10"), _team("Kongsberg", "B", "U10")]
        t = _tournament("t1", "2026-01-05", "Jarhallen", "U10", teams)
        t["start_time"] = "10:00"
        candidate = {"schema_version": 1, "tournaments": [t]}
        problem = {"round_length_minutes": {"U10": 30}}

        a = optimize_candidate(candidate, problem, iterations=300, seed=3, move_slots=True)
        b = optimize_candidate(candidate, problem, iterations=300, seed=3, move_slots=True)
        assert a["tournaments"] == b["tournaments"]

    def test_never_picks_a_time_conflicting_with_external_booking(self):
        """issue #264 P0: the planning_problem contract does not carry
        per-time external evidence for sibling-only conflict checks, but it
        does now carry club_busy_intervals -- a candidate start time must be
        rejected if it collides with the host's own real booking, not just
        another tournament in this candidate."""
        teams = [_team("Jar", "A", "U10"), _team("Kongsberg", "B", "U10")]
        t = _tournament("t1", "2026-01-05", "Jarhallen", "U10", teams)
        t["start_time"] = "10:00"
        candidate = {"schema_version": 1, "tournaments": [t]}
        problem = {
            "round_length_minutes": {"U10": 30},
            "club_calendar_status": {"Jar": "known"},
            # Busy from just after the current start through end of day --
            # every other candidate window (10:30-15:00) conflicts.
            "club_busy_intervals": {
                "Jar": [{"date": "2026-01-05", "start": "10:25", "end": "24:00"}],
            },
        }
        optimized = optimize_candidate(
            candidate, problem, iterations=300, seed=1, move_slots=True, date_swap_probability=1.0
        )
        assert optimized["tournaments"][0]["start_time"] == "10:00"


class TestSearchStateIncrementalMatchesFullRecompute:
    """issue #265 P0 acceptance: "Delta score/state is bit-for-bit or
    tolerance-equivalent to recomputing the reference objective" and
    "Property/regression tests compare incremental state against full
    recomputation over randomized move sequences." """

    @staticmethod
    def _season_candidate(rng: random.Random) -> dict:
        """A season with several age groups, each with enough
        tournaments/teams that a team plays in several tournaments across
        the season -- this is what exercises the "a team is a member of
        both slots in this move" telescoping edge cases in
        :class:`tournament_scheduler.stage3_optimizer._SearchState`."""
        tournaments = []
        base_index = 0
        for age_group, n_teams, n_tournaments, roster_size in [
            ("U10", 8, 6, 4),
            ("U12", 6, 5, 3),
            ("U14", 5, 4, 4),
        ]:
            clubs = [f"Club{age_group}{i}" for i in range(n_teams)]
            teams = [_team(clubs[i], f"{clubs[i]}-{age_group}", age_group) for i in range(n_teams)]
            for t_index in range(n_tournaments):
                roster = rng.sample(teams, k=min(roster_size, len(teams)))
                base_index += 1
                tournaments.append(
                    _tournament(
                        f"t{base_index}",
                        f"2026-{1 + (base_index % 9):02d}-{1 + (base_index % 27):02d}",
                        f"Arena{base_index % 4}",
                        age_group,
                        roster,
                    )
                )
        return {"schema_version": 1, "tournaments": tournaments}

    def test_incremental_team_swaps_match_full_objective(self):
        from tournament_scheduler.stage3_optimizer import (
            DEFAULT_WEIGHTS,
            _SearchState,
            _build_slots,
            _candidate_swaps,
            _resolve_weights,
            _swap_is_valid,
        )

        rng = random.Random(7)
        candidate = self._season_candidate(rng)
        slots, _ = _build_slots(candidate, None)
        weights_by_age_group = {
            slot.age_group: _resolve_weights(DEFAULT_WEIGHTS, None, slot.age_group) for slot in slots
        }
        state = _SearchState(slots, weights_by_age_group)
        assert state.total == state.full_objective(DEFAULT_WEIGHTS)

        for _ in range(300):
            move = _candidate_swaps(slots, rng)
            if move is None:
                continue
            slot_a, pos_a, slot_b, pos_b = move
            if not _swap_is_valid(slots, slot_a, pos_a, slot_b, pos_b, state):
                continue
            state.apply_team_swap(slot_a, pos_a, slot_b, pos_b)
            assert state.total == pytest.approx(state.full_objective(DEFAULT_WEIGHTS), abs=1e-6)

            # Revert (self-inverse) should also match exactly.
            state.apply_team_swap(slot_a, pos_a, slot_b, pos_b)
            assert state.total == pytest.approx(state.full_objective(DEFAULT_WEIGHTS), abs=1e-6)

    def test_candidate_swaps_never_move_teams_between_u_and_ju(self):
        from tournament_scheduler.stage3_optimizer import _build_slots, _candidate_swaps

        u10_teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        ju10_teams = [_team("Jar", "Jar 2", "JU10"), _team("Kongsberg", "Kongsberg 2", "JU10")]
        candidate = {
            "schema_version": 1,
            "tournaments": [
                _tournament("t1", "2026-01-10", "Jarhallen", "U10", u10_teams),
                _tournament("t2", "2026-01-17", "Jarhallen", "U10", list(reversed(u10_teams))),
                _tournament("t3", "2026-01-10", "Kongsberghallen", "JU10", ju10_teams),
                _tournament("t4", "2026-01-17", "Kongsberghallen", "JU10", list(reversed(ju10_teams))),
            ],
        }
        slots, _ = _build_slots(candidate, None)
        rng = random.Random(3)

        for _ in range(200):
            move = _candidate_swaps(slots, rng)
            if move is None:
                continue
            slot_a, _pos_a, slot_b, _pos_b = move
            assert slots[slot_a].age_group == slots[slot_b].age_group

    def test_incremental_date_swaps_match_full_objective(self):
        from tournament_scheduler.stage3_optimizer import (
            DEFAULT_WEIGHTS,
            _SearchState,
            _build_slots,
            _date_swap_candidates,
            _date_swap_is_valid,
            _resolve_weights,
        )

        rng = random.Random(11)
        candidate = self._season_candidate(rng)
        slots, _ = _build_slots(candidate, None)
        weights_by_age_group = {
            slot.age_group: _resolve_weights(DEFAULT_WEIGHTS, None, slot.age_group) for slot in slots
        }
        state = _SearchState(slots, weights_by_age_group)

        for _ in range(300):
            move = _date_swap_candidates(slots, rng)
            if move is None:
                continue
            slot_a, slot_b = move
            if not _date_swap_is_valid(slots, slot_a, slot_b, state):
                continue
            state.apply_date_swap(slot_a, slot_b)
            assert state.total == pytest.approx(state.full_objective(DEFAULT_WEIGHTS), abs=1e-6)
            state.apply_date_swap(slot_a, slot_b)  # revert
            assert state.total == pytest.approx(state.full_objective(DEFAULT_WEIGHTS), abs=1e-6)

    def test_incremental_matches_full_objective_with_shared_teams_across_slots(self):
        """A team that plays in *both* tournaments being swapped/date-swapped
        (issue #265 P0's telescoping-sum correctness argument) must not
        throw off the incremental total."""
        from tournament_scheduler.stage3_optimizer import (
            DEFAULT_WEIGHTS,
            _SearchState,
            _build_slots,
            _date_swap_is_valid,
            _resolve_weights,
            _swap_is_valid,
        )

        shared = _team("SharedClub", "Shared-U10", "U10")
        other_a = [_team("A1", "A1-U10", "U10"), _team("A2", "A2-U10", "U10")]
        other_b = [_team("B1", "B1-U10", "U10"), _team("B2", "B2-U10", "U10")]
        candidate = {
            "schema_version": 1,
            "tournaments": [
                _tournament("ta", "2026-01-05", "ArenaA", "U10", [shared, *other_a]),
                _tournament("tb", "2026-02-04", "ArenaB", "U10", [shared, *other_b]),
            ],
        }
        slots, _ = _build_slots(candidate, None)
        weights_by_age_group = {"U10": _resolve_weights(DEFAULT_WEIGHTS, None, "U10")}
        state = _SearchState(slots, weights_by_age_group)
        assert state.total == state.full_objective(DEFAULT_WEIGHTS)

        # Date swap: both slots share `shared` as a common team.
        assert _date_swap_is_valid(slots, 0, 1, state)
        state.apply_date_swap(0, 1)
        assert state.total == pytest.approx(state.full_objective(DEFAULT_WEIGHTS), abs=1e-6)
        state.apply_date_swap(0, 1)
        assert state.total == pytest.approx(state.full_objective(DEFAULT_WEIGHTS), abs=1e-6)

        # Team swap: an "other" team from each slot, with `shared` present
        # in both slots' rosters throughout.
        pos_a = slots[0].team_ids.index(("A1", "A1-U10", "U10"))
        pos_b = slots[1].team_ids.index(("B1", "B1-U10", "U10"))
        assert _swap_is_valid(slots, 0, pos_a, 1, pos_b, state)
        state.apply_team_swap(0, pos_a, 1, pos_b)
        assert state.total == pytest.approx(state.full_objective(DEFAULT_WEIGHTS), abs=1e-6)


class TestOptimizeCandidatePareto:
    """issue #264 P1 / issue #265 P1: multi-objective search over a small,
    shared set of deliberate epochs with a bounded non-dominated archive."""

    def test_returns_non_empty_non_dominated_archive(self):
        candidate = _clustered_candidate()
        result = optimize_candidate_pareto(candidate, iterations_per_epoch=500, seed=1)

        assert result["candidates"]
        assert len(result["candidates"]) <= 5
        vectors = [entry["objective_vector"] for entry in result["candidates"]]
        for i, a in enumerate(vectors):
            for j, b in enumerate(vectors):
                if i == j:
                    continue
                # No archive entry may dominate another -- that would mean
                # a strictly worse candidate slipped through the filter.
                assert not all(a[k] <= b[k] for k in a) or not any(a[k] < b[k] for k in a)

    def test_archive_respects_max_archive_size(self):
        candidate = _clustered_candidate()
        result = optimize_candidate_pareto(
            candidate, iterations_per_epoch=300, seed=2, max_archive_size=2
        )
        assert len(result["candidates"]) <= 2

    def test_every_candidate_carries_comparable_metrics_and_verification(self):
        candidate = _clustered_candidate()
        result = optimize_candidate_pareto(candidate, iterations_per_epoch=500, seed=3)

        assert "baseline_objective_vector" in result
        assert "baseline_score" in result
        for entry in result["candidates"]:
            assert set(entry["objective_vector"].keys()) == set(result["baseline_objective_vector"].keys())
            assert "score" in entry
            assert "weights_used" in entry
            assert "verify_result" in entry
            assert isinstance(entry["dominates_baseline"], bool)
            result_check = verify_candidate(entry["candidate"])
            assert result_check["ok"], result_check["violations"]

    def test_search_summary_reports_one_entry_per_epoch(self):
        candidate = _clustered_candidate()
        result = optimize_candidate_pareto(candidate, iterations_per_epoch=200, seed=4)

        assert result["search_summary"]["epochs"] == len(result["search_summary"]["epoch_summaries"])
        for summary in result["search_summary"]["epoch_summaries"]:
            assert "weights" in summary
            assert "objective_vector" in summary
            assert "search_summary" in summary

    def test_deterministic_for_fixed_seed(self):
        candidate = _clustered_candidate()
        a = optimize_candidate_pareto(candidate, iterations_per_epoch=300, seed=5)
        b = optimize_candidate_pareto(candidate, iterations_per_epoch=300, seed=5)
        assert [entry["objective_vector"] for entry in a["candidates"]] == [
            entry["objective_vector"] for entry in b["candidates"]
        ]

    def test_custom_weight_vectors_control_epoch_count(self):
        candidate = _clustered_candidate()
        vectors = [{"pair_repeat": 10.0}, {"gap_under_7": 10.0}]
        result = optimize_candidate_pareto(
            candidate, iterations_per_epoch=200, seed=6, weight_vectors=vectors
        )
        assert result["search_summary"]["epochs"] == 2

    def test_does_not_mutate_input_candidate(self):
        candidate = _clustered_candidate()
        import copy

        before = copy.deepcopy(candidate)
        optimize_candidate_pareto(candidate, iterations_per_epoch=200, seed=7)
        assert candidate == before

    def test_preserves_participation_and_roster_skeleton_for_every_candidate(self):
        candidate = _clustered_candidate()
        result = optimize_candidate_pareto(candidate, iterations_per_epoch=300, seed=8)
        before_participation = score_candidate(candidate)["participation"]["counts_by_team"]

        for entry in result["candidates"]:
            after_participation = score_candidate(entry["candidate"])["participation"]["counts_by_team"]
            assert after_participation == before_participation
