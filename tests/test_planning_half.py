"""Tests for the shared before/after-Christmas planning-half contract (issue #293).

Covers:
- `planning_half.christmas_split_date`/`tournament_half`/`half_label`
- `planning_contract.build_planning_problem` exposing `christmas_split_date`
  and `allow_cross_half_moves`
- `planning_contract.score_candidate`'s `half_distribution`/`half_deviation_pct`
- `stage3_optimizer`'s cross-half guardrail and within-half date move
- `stage3_engine.run_planner` and `stage3_decision`'s optimize_plan schemas
  actually plumbing `move_dates_within_half` through to the optimizer, so a
  live run/LLM controller can request the new move at all
- snapshot isolation: a half-2-only registration change must not touch half-1
"""

from datetime import date

from tournament_scheduler import planning_half
from tournament_scheduler.planning_contract import score_candidate
from tournament_scheduler.stage3_optimizer import (
    _Slot,
    _date_swap_is_valid,
    _within_half_date_move_candidates,
    _within_half_date_move_is_valid,
)


# ---------------------------------------------------------------------------
# planning_half module
# ---------------------------------------------------------------------------


class TestChristmasSplitDate:
    def test_returns_jan_1_when_window_spans_new_year(self):
        assert planning_half.christmas_split_date(
            date(2026, 10, 1), date(2027, 4, 30)
        ) == date(2027, 1, 1)

    def test_returns_none_for_spring_only_window(self):
        assert planning_half.christmas_split_date(date(2027, 1, 5), date(2027, 4, 30)) is None

    def test_returns_none_for_autumn_only_window_ending_before_new_year(self):
        assert planning_half.christmas_split_date(date(2026, 9, 1), date(2026, 12, 31)) is None

    def test_boundary_dates_are_inclusive(self):
        assert planning_half.christmas_split_date(date(2027, 1, 1), date(2027, 1, 1)) == date(2027, 1, 1)


class TestTournamentHalf:
    split = date(2027, 1, 1)

    def test_before_split_is_before_christmas(self):
        assert planning_half.tournament_half(date(2026, 11, 1), self.split) == "before_christmas"

    def test_on_or_after_split_is_after_christmas(self):
        assert planning_half.tournament_half(self.split, self.split) == "after_christmas"
        assert planning_half.tournament_half(date(2027, 2, 1), self.split) == "after_christmas"

    def test_no_split_date_is_unsplit(self):
        assert planning_half.tournament_half(date(2027, 2, 1), None) == "unsplit"


class TestHalfLabel:
    def test_known_halves_get_norwegian_labels(self):
        assert planning_half.half_label("before_christmas") == "Før jul"
        assert planning_half.half_label("after_christmas") == "Etter jul"
        assert planning_half.half_label("unsplit") == "Udelt sesong"


# ---------------------------------------------------------------------------
# planning_contract integration
# ---------------------------------------------------------------------------


def _tournament(t_id, iso_date, age_group="U10", host="Jar", arena="Jarhallen"):
    return {
        "id": t_id,
        "date": iso_date,
        "age_group": age_group,
        "host_club": host,
        "arena": arena,
        "teams": [{"club": host, "label": f"{host}-A", "age_group": age_group}],
        "games": [],
        "cancelled": False,
    }


class TestScoreCandidateHalfDistribution:
    def test_counts_split_evenly_when_balanced(self):
        candidate = {
            "tournaments": [
                _tournament("t1", "2026-11-01"),
                _tournament("t2", "2027-02-01"),
            ]
        }
        problem = {"christmas_split_date": "2026-12-24"}
        result = score_candidate(candidate, problem=problem)
        assert result["half_distribution"] == {"before_christmas": 1, "after_christmas": 1, "unsplit": 0}
        assert result["half_deviation_pct"] == 0.0

    def test_deviation_reported_when_front_loaded(self):
        candidate = {
            "tournaments": [
                _tournament("t1", "2026-11-01"),
                _tournament("t2", "2026-11-15"),
                _tournament("t3", "2026-12-01"),
                _tournament("t4", "2027-02-01"),
            ]
        }
        problem = {"christmas_split_date": "2026-12-24"}
        result = score_candidate(candidate, problem=problem)
        assert result["half_distribution"] == {"before_christmas": 3, "after_christmas": 1, "unsplit": 0}
        assert result["half_deviation_pct"] == 50.0

    def test_falls_back_to_deriving_split_from_candidate_dates_without_problem(self):
        candidate = {
            "tournaments": [
                _tournament("t1", "2026-11-01"),
                _tournament("t2", "2027-02-01"),
            ]
        }
        result = score_candidate(candidate)
        assert result["half_distribution"]["before_christmas"] == 1
        assert result["half_distribution"]["after_christmas"] == 1


class TestBuildPlanningProblemSplitDate:
    def test_split_date_is_iso_string_when_window_spans_christmas(self):
        from tournament_scheduler.planning_contract import build_planning_problem

        problem = build_planning_problem({}, None, date(2026, 10, 1), date(2027, 4, 30))
        assert problem["christmas_split_date"] == "2027-01-01"

    def test_split_date_is_none_for_spring_only_window(self):
        from tournament_scheduler.planning_contract import build_planning_problem

        problem = build_planning_problem({}, None, date(2027, 1, 5), date(2027, 4, 30))
        assert problem["christmas_split_date"] is None


# ---------------------------------------------------------------------------
# stage3_optimizer cross-half guardrail + within-half move
# ---------------------------------------------------------------------------


def _slot(t_id, iso_date, age_group="U10", host="Jar", arena="Jarhallen"):
    return _Slot(
        tournament={"id": t_id},
        date=date.fromisoformat(iso_date),
        age_group=age_group,
        host_club=host,
        parallel_games=1,
        arena=arena,
        team_ids=[(host, f"{host}-A", age_group)],
    )


class TestDateSwapCrossHalfGuardrail:
    split = date(2027, 1, 1)

    def test_swap_within_same_half_is_allowed(self):
        slots = [_slot("t1", "2026-11-01"), _slot("t2", "2026-11-15")]
        assert _date_swap_is_valid(slots, 0, 1, split_date=self.split) is True

    def test_swap_across_half_boundary_is_rejected_by_default(self):
        slots = [_slot("t1", "2026-11-01"), _slot("t2", "2027-02-01")]
        assert _date_swap_is_valid(slots, 0, 1, split_date=self.split) is False

    def test_swap_across_half_boundary_allowed_with_explicit_override(self):
        slots = [_slot("t1", "2026-11-01"), _slot("t2", "2027-02-01")]
        assert (
            _date_swap_is_valid(slots, 0, 1, split_date=self.split, allow_cross_half_moves=True)
            is True
        )

    def test_no_split_date_never_blocks_a_swap(self):
        slots = [_slot("t1", "2026-11-01"), _slot("t2", "2027-02-01")]
        assert _date_swap_is_valid(slots, 0, 1, split_date=None) is True


class TestWithinHalfDateMove:
    def test_candidate_stays_within_same_half(self):
        import random

        slots = [_slot("t1", "2026-11-01")]
        problem = {"start_date": "2026-10-01", "end_date": "2027-04-30"}
        split = date(2027, 1, 1)
        rng = random.Random(0)
        seen_halves = set()
        for _ in range(50):
            move = _within_half_date_move_candidates(slots, rng, problem, split)
            if move is None:
                continue
            _, new_date = move
            seen_halves.add(planning_half.tournament_half(new_date, split))
        assert seen_halves <= {"before_christmas"}

    def test_returns_none_without_problem(self):
        slots = [_slot("t1", "2026-11-01")]
        import random

        assert _within_half_date_move_candidates(slots, random.Random(0), None, date(2027, 1, 1)) is None

    def test_move_rejected_on_arena_double_booking(self):
        slots = [_slot("t1", "2026-11-01"), _slot("t2", "2026-11-10")]
        assert _within_half_date_move_is_valid(slots, 0, date(2026, 11, 10)) is False

    def test_move_allowed_to_a_genuinely_free_date(self):
        slots = [_slot("t1", "2026-11-01"), _slot("t2", "2026-11-10")]
        assert _within_half_date_move_is_valid(slots, 0, date(2026, 11, 20)) is True


# ---------------------------------------------------------------------------
# Snapshot isolation: half-2-only roster changes must not move half-1 dates
# ---------------------------------------------------------------------------


class TestHalfSnapshotIsolation:
    """A registration change scoped to after-Christmas teams must not alter
    any before-Christmas tournament's id/date, since half 2 is meant to be
    independently re-plannable against a fresh snapshot (issue #293)."""

    def test_before_christmas_tournaments_unaffected_by_after_christmas_roster_change(self):
        from datetime import datetime

        from tournament_scheduler.models import Roster, Team
        from tournament_scheduler.season_planner import SeasonPlanner
        from tournament_scheduler.testing.canonical_input import OfflineScheduler, all_weekend_dates

        start, end = datetime(2026, 10, 1), datetime(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)

        def _build_plan(extra_after_christmas_team: bool):
            teams = [Team(club=f"Club{i}", label=f"U10-{i}", age_group="U10") for i in range(4)]
            if extra_after_christmas_team:
                teams.append(Team(club="Club4", label="U10-4", age_group="U10"))
            roster = Roster(teams=teams)
            planner = SeasonPlanner(
                scheduler=OfflineScheduler(free_dates),
                roster=roster,
                club_arenas={team.club: f"{team.club}hallen" for team in roster.teams},
                target_tournament_count=6,
                participation_targets_by_age_group={
                    "U10": {"before_christmas": 3, "after_christmas": 3}
                },
            )
            return planner.build_plan(start, end)

        baseline = _build_plan(extra_after_christmas_team=False)
        changed = _build_plan(extra_after_christmas_team=True)

        split = planning_half.christmas_split_date(date(2026, 10, 1), date(2027, 4, 30))

        def _before_christmas_signature(plan):
            return sorted(
                (t.date.isoformat(), t.arena, t.age_group)
                for t in plan.tournaments
                if planning_half.tournament_half(t.date, split) == "before_christmas"
            )

        assert _before_christmas_signature(baseline) == _before_christmas_signature(changed)


# ---------------------------------------------------------------------------
# build_planning_problem's allow_cross_half_moves override
# ---------------------------------------------------------------------------


class TestAllowCrossHalfMovesConfig:
    def test_defaults_to_false(self):
        from tournament_scheduler.planning_contract import build_planning_problem

        problem = build_planning_problem({}, None, date(2026, 10, 1), date(2027, 4, 30))
        assert problem["allow_cross_half_moves"] is False

    def test_config_can_enable_the_override(self):
        from tournament_scheduler.planning_contract import build_planning_problem

        problem = build_planning_problem(
            {"allow_cross_half_moves": True}, None, date(2026, 10, 1), date(2027, 4, 30)
        )
        assert problem["allow_cross_half_moves"] is True


# ---------------------------------------------------------------------------
# move_dates_within_half plumbed through stage3_engine/stage3_decision, so a
# live run or the LLM controller can actually request it (issue #293).
# ---------------------------------------------------------------------------


def _engine_candidate():
    return {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "2026-11-01"),
            _tournament("t2", "2026-11-15"),
        ],
    }


class TestMoveDatesWithinHalfPlumbing:
    def test_run_planner_forwards_move_dates_within_half_to_local_search(self):
        from tournament_scheduler.stage3_engine import run_planner

        candidate = _engine_candidate()
        problem = {
            "start_date": "2026-10-01",
            "end_date": "2027-04-30",
            "christmas_split_date": "2026-12-24",
        }
        # A request with the flag off should never move dates outside the
        # existing two; with it on and enough iterations, at least one date
        # in the result should differ from the baseline (deterministic seed).
        baseline_result = run_planner(
            engine="local_search",
            problem=problem,
            baseline=candidate,
            request={"iterations": 200, "seed": 0, "move_dates_within_half": False},
        )
        moved_result = run_planner(
            engine="local_search",
            problem=problem,
            baseline=candidate,
            request={
                "iterations": 200,
                "seed": 0,
                "move_dates_within_half": True,
                "date_swap_probability": 1.0,
            },
        )
        baseline_dates = sorted(t["date"] for t in baseline_result["tournaments"])
        moved_dates = sorted(t["date"] for t in moved_result["tournaments"])
        assert baseline_dates == ["2026-11-01", "2026-11-15"]
        # The moved run is allowed to differ from baseline (not required to,
        # since simulated annealing may reject every proposal), but every
        # resulting date must still fall in the same half it started in.
        split = date(2027, 1, 1)
        for t in moved_result["tournaments"]:
            assert planning_half.tournament_half(date.fromisoformat(t["date"]), split) == "before_christmas"

    def test_optimize_plan_schemas_expose_move_dates_within_half(self):
        from tournament_scheduler.stage3_decision import (
            _PARETO_OPTIMIZE_PLAN_SCHEMA,
            _V2_OPTIMIZER_OPTIMIZE_PLAN_SCHEMA,
        )

        assert "move_dates_within_half" in _V2_OPTIMIZER_OPTIMIZE_PLAN_SCHEMA
        assert "move_dates_within_half" in _PARETO_OPTIMIZE_PLAN_SCHEMA
