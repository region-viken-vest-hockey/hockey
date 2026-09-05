"""Tests for tournament_scheduler.pipeline.stage3_planning."""

from collections import Counter
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from tournament_scheduler.models import Game, SeasonPlan, Team, Tournament
from tournament_scheduler.pipeline.stage3_planning import (
    Stage3Error,
    _plan_to_dict,
    run,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.season_planner import _normalize_penalty_hints


def _make_config():
    clubs = [
        "Kongsberg", "Skien", "Ringerike", "Tønsberg",
        "Frisk Asker", "Sandefjord Penguins", "Jar",
    ]
    teams = [
        {"club": c, "label": f"{c} U10A", "age_group": "U10"}
        for c in clubs
    ]
    return {
        "start_date": "2025-09-01",
        "end_date": "2025-12-15",
        "age_groups": ["U10"],
        "parallel_games": {"U10": 2},
        "teams": teams,
    }


def _make_duplicate_label_config():
    return {
        "start_date": "2025-09-01",
        "end_date": "2025-12-01",
        "age_groups": ["U10", "U11"],
        "parallel_games": {"U10": 2, "U11": 2},
        "fairness_thresholds": {
            "max_game_count_spread": 999,
            "max_hosting_deviation": 999,
            "max_team_travel_km": 9999,
            "min_diversity_score": 0.0,
            "min_pairwise_matchup_score": 0.0,
            "min_month_balance_score": 0.0,
            "max_same_weekend_club_load": 999,
            "max_consecutive_weekend_club_load": 999,
            "max_holiday_stretch_club_load": 999,
        },
        "teams": [
            {"club": "Jar", "label": "Jar 1 U10", "age_group": "U10"},
            {"club": "Kongsberg", "label": "Kongsberg 1 U10", "age_group": "U10"},
            {"club": "Ringerike", "label": "Ringerike 1 U10", "age_group": "U10"},
            {"club": "Jar", "label": "Jar 1 U11", "age_group": "U11"},
            {"club": "Kongsberg", "label": "Kongsberg 1 U11", "age_group": "U11"},
            {"club": "Ringerike", "label": "Ringerike 1 U11", "age_group": "U11"},
        ],
    }


class TestRunStage3:
    def test_season_planner_normalizes_structured_penalty_hints(self):
        assert _normalize_penalty_hints({
            "source": "mid_planning_critic",
            "penalty_hints": {"diversity_score": "70", "bad": "not-a-number"},
        }) == {"diversity_score": 70.0}

    @pytest.mark.slow
    def test_accepts_canonical_workbook_config(self, tmp_path, canonical_input_data, canonical_season_window):
        state = PipelineState(tmp_path / "pipeline")
        start, end = canonical_season_window
        result = run(canonical_input_data, {}, state, start, end)

        assert state.is_done(StageName.PLANNING)
        assert "plan" in result
        assert len(result["plan"]["tournaments"]) > 0

        configured_age_groups = set(canonical_input_data["age_groups"])
        planned_age_groups = {t["age_group"] for t in result["plan"]["tournaments"]}
        assert planned_age_groups <= configured_age_groups
        assert planned_age_groups

    @pytest.mark.slow
    def test_canonical_workbook_plan_covers_multiple_age_groups(self, tmp_path, canonical_input_data, canonical_season_window):
        state = PipelineState(tmp_path / "pipeline")
        start, end = canonical_season_window
        result = run(canonical_input_data, {}, state, start, end)

        counts = Counter(t["age_group"] for t in result["plan"]["tournaments"])
        assert len(counts) >= 3
        assert counts["U10"] > 0

    def test_accepts_plan_without_llm(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        result = run(
            _make_config(), {},
            state,
            datetime(2025, 9, 1), datetime(2025, 12, 15),
        )
        assert state.is_done(StageName.PLANNING)
        assert "plan" in result
        assert len(result["plan"]["tournaments"]) > 0

    def test_empty_roster_produces_not_started_placeholder_plan(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        cfg = {**_make_config(), "teams": []}
        result = run(
            cfg, {},
            state,
            datetime(2025, 9, 1), datetime(2025, 12, 15),
        )

        assert state.is_done(StageName.PLANNING)
        assert result["not_started"] is True
        assert result["plan"]["tournaments"] == []
        assert result["plan"]["placeholder"] == "not_started"
        assert "Ikke begynt" in result["plan"]["message"]

    def test_plan_contains_expected_fields(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        result = run(
            _make_config(), {},
            state,
            datetime(2025, 9, 1), datetime(2025, 12, 15),
        )
        plan = result["plan"]
        assert "tournaments" in plan
        assert "diversity_score" in plan
        assert "month_balance_score" in plan
        assert "arena_day_collisions" in plan

    def test_structured_planning_critic_hints_are_persisted_and_flattened(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        hints = {
            "source": "mid_planning_critic",
            "iteration": 1,
            "issues": ["game spread"],
            "penalty_hints": {"game_count_spread_score": "25", "ignored": "not-a-number"},
        }
        fake_planner = MagicMock()
        fake_planner.build_plan.return_value = SeasonPlan(
            tournaments=[
                Tournament(
                    date=date(2025, 10, 5),
                    arena="Kongsberghallen",
                    age_group="U10",
                    teams=[
                        Team(club="Kongsberg", label="Kongsberg U10A", age_group="U10"),
                        Team(club="Skien", label="Skien U10A", age_group="U10"),
                        Team(club="Jar", label="Jar U10A", age_group="U10"),
                    ],
                    games=[],
                )
            ]
        )
        fake_planner.rules_report.return_value = {"ok": True}
        cfg = {**_make_config(), "planning_critic_hints": hints}

        with patch("tournament_scheduler.pipeline.stage3_planning._make_planner", return_value=fake_planner) as make_planner, patch(
            "tournament_scheduler.pipeline.stage3_planning._build_fairness_gate",
            return_value={"score": 42},
        ):
            result = run(cfg, {}, state, datetime(2025, 9, 1), datetime(2025, 12, 15))

        assert result["planning_critic_hints"] == hints
        assert make_planner.call_args.kwargs["penalty_hints"] == {"game_count_spread_score": 25.0}

    def test_flat_penalty_hints_remain_supported(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        flat_hints = {"hosting_deviation_score": 30}
        fake_planner = MagicMock()
        fake_planner.build_plan.return_value = SeasonPlan(
            tournaments=[
                Tournament(
                    date=date(2025, 10, 5),
                    arena="Kongsberghallen",
                    age_group="U10",
                    teams=[
                        Team(club="Kongsberg", label="Kongsberg U10A", age_group="U10"),
                        Team(club="Skien", label="Skien U10A", age_group="U10"),
                        Team(club="Jar", label="Jar U10A", age_group="U10"),
                    ],
                    games=[],
                )
            ]
        )
        fake_planner.rules_report.return_value = {"ok": True}
        cfg = {**_make_config(), "penalty_hints": flat_hints}

        with patch("tournament_scheduler.pipeline.stage3_planning._make_planner", return_value=fake_planner) as make_planner, patch(
            "tournament_scheduler.pipeline.stage3_planning._build_fairness_gate",
            return_value={"score": 42},
        ):
            result = run(cfg, {}, state, datetime(2025, 9, 1), datetime(2025, 12, 15))

        assert result["planning_critic_hints"] == {
            "source": "penalty_hints",
            "penalty_hints": {"hosting_deviation_score": 30.0},
        }
        assert make_planner.call_args.kwargs["penalty_hints"] == {"hosting_deviation_score": 30.0}

    def _fake_planner_with_plan(self) -> MagicMock:
        fake_planner = MagicMock()
        fake_planner.build_plan.return_value = SeasonPlan(
            tournaments=[
                Tournament(
                    date=date(2025, 10, 5),
                    arena="Kongsberghallen",
                    age_group="U10",
                    teams=[
                        Team(club="Kongsberg", label="Kongsberg U10A", age_group="U10"),
                        Team(club="Skien", label="Skien U10A", age_group="U10"),
                        Team(club="Jar", label="Jar U10A", age_group="U10"),
                    ],
                    games=[],
                )
            ]
        )
        fake_planner.rules_report.return_value = {"ok": True}
        return fake_planner

    def test_allow_penalty_hint_relaxation_defaults_true(self, tmp_path):
        """Legacy behavior unchanged for any caller that doesn't set the
        config flag (issue #260 Phase 4)."""
        state = PipelineState(tmp_path / "pipeline")
        fake_planner = self._fake_planner_with_plan()

        with patch("tournament_scheduler.pipeline.stage3_planning._make_planner", return_value=fake_planner) as make_planner, patch(
            "tournament_scheduler.pipeline.stage3_planning._build_fairness_gate",
            return_value={"status": "pass", "score": 42, "metrics": []},
        ):
            run(_make_config(), {}, state, datetime(2025, 9, 1), datetime(2025, 12, 15))

        assert make_planner.call_args.kwargs["allow_penalty_hint_relaxation"] is True

    def test_allow_penalty_hint_relaxation_threaded_from_config_flag(self, tmp_path):
        """issue #260 Phase 4 ("remove penalty_hints threshold relaxation
        from the canonical decision-driven path"): pipeline_orchestrator
        ._run_stage3 sets allow_penalty_hint_relaxation=False in the merged
        config whenever a headless judge is configured; stage3_planning.run
        must thread it through to every _make_planner call, not just honor
        it for the first seed."""
        state = PipelineState(tmp_path / "pipeline")
        fake_planner = self._fake_planner_with_plan()
        cfg = {**_make_config(), "allow_penalty_hint_relaxation": False}

        with patch("tournament_scheduler.pipeline.stage3_planning._make_planner", return_value=fake_planner) as make_planner, patch(
            "tournament_scheduler.pipeline.stage3_planning._build_fairness_gate",
            return_value={"status": "warn", "score": 42, "metrics": [{"key": "game_count_spread", "status": "warn", "score": 42}]},
        ):
            run(cfg, {}, state, datetime(2025, 9, 1), datetime(2025, 12, 15), iterations=2)

        assert make_planner.call_count == 2
        assert all(c.kwargs["allow_penalty_hint_relaxation"] is False for c in make_planner.call_args_list)

    def test_weak_metric_from_best_attempt_is_fed_forward_to_later_seeds(self, tmp_path):
        """When attempts stay stuck at warn/fail, later seeds should be biased toward
        fixing the best-so-far candidate's weak metric instead of repeating a blind
        random restart (issue follow-up: "improve the seed algorithm")."""
        state = PipelineState(tmp_path / "pipeline")
        fake_planner = MagicMock()
        fake_planner.build_plan.return_value = SeasonPlan(
            tournaments=[
                Tournament(
                    date=date(2025, 10, 5),
                    arena="Kongsberghallen",
                    age_group="U10",
                    teams=[
                        Team(club="Kongsberg", label="Kongsberg U10A", age_group="U10"),
                        Team(club="Skien", label="Skien U10A", age_group="U10"),
                        Team(club="Jar", label="Jar U10A", age_group="U10"),
                    ],
                    games=[],
                )
            ]
        )
        fake_planner.rules_report.return_value = {"ok": True}
        fake_planner.fallback_host_substitutions = []
        stuck_gate = {
            "status": "warn",
            "score": 60,
            "metrics": [{"key": "game_count_spread", "label": "Kamper per lag", "status": "warn", "score": 60, "detail": "x"}],
        }

        # penalty_hints is the same dict object mutated in place across seeds, so
        # call_args_list would only ever show its final state — snapshot a copy at
        # the moment each call happens instead.
        captured_hints: list[dict[str, float]] = []

        def _make_planner_spy(*args, **kwargs):
            captured_hints.append(dict(kwargs.get("penalty_hints") or {}))
            return fake_planner

        with patch(
            "tournament_scheduler.pipeline.stage3_planning._make_planner", side_effect=_make_planner_spy
        ), patch(
            "tournament_scheduler.pipeline.stage3_planning._build_fairness_gate",
            side_effect=[dict(stuck_gate), dict(stuck_gate), dict(stuck_gate)],
        ):
            run(_make_config(), {}, state, datetime(2025, 9, 1), datetime(2025, 12, 15), iterations=3)

        assert len(captured_hints) == 3
        # First seed has no prior best attempt to learn from.
        assert captured_hints[0] == {}
        # Once an attempt is on the books, subsequent seeds inherit its weak metric.
        assert captured_hints[1] == {"game_count_spread_score": 60.0}
        assert captured_hints[2] == {"game_count_spread_score": 60.0}

    def test_workbook_level_planning_settings_are_passed_to_planner(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        fake_planner = MagicMock()
        fake_planner.build_plan.return_value = SeasonPlan(
            tournaments=[
                Tournament(
                    date=date(2025, 10, 5),
                    arena="Kongsberghallen",
                    age_group="U10",
                    teams=[
                        Team(club="Kongsberg", label="Kongsberg U10A", age_group="U10"),
                        Team(club="Skien", label="Skien U10A", age_group="U10"),
                        Team(club="Jar", label="Jar U10A", age_group="U10"),
                    ],
                    games=[],
                )
            ]
        )
        fake_planner.rules_report.return_value = {"ok": True}
        cfg = {
            **_make_config(),
            "target_tournament_count": 5,
            "max_hosting_days_per_month": 2,
        }

        with patch("tournament_scheduler.pipeline.stage3_planning._make_planner", return_value=fake_planner) as make_planner, patch(
            "tournament_scheduler.pipeline.stage3_planning._build_fairness_gate",
            return_value={"score": 42},
        ):
            run(cfg, {}, state, datetime(2025, 9, 1), datetime(2025, 12, 15))

        assert make_planner.call_args.args[7] == 5
        assert make_planner.call_args.kwargs["max_hosting_days_per_month"] == 2

    def test_plan_accepted_without_llm_evaluation(self, tmp_path):
        """Plan is accepted deterministically without LLM evaluation."""
        state = PipelineState(tmp_path / "pipeline")
        result = run(
            _make_config(), {},
            state,
            datetime(2025, 9, 1), datetime(2025, 12, 15),
        )

        assert state.is_done(StageName.PLANNING)
        assert "plan" in result
        assert len(result["plan"]["tournaments"]) > 0
        # No LLM fields should be present (LLM eval was removed from Stage 3)
        assert "llm_confidence" not in result
        assert "llm_skipped" not in result

    def test_emits_more_progress_after_optimization(self, tmp_path, capsys):
        state = PipelineState(tmp_path / "pipeline")
        run(
            _make_config(), {},
            state,
            datetime(2025, 9, 1), datetime(2025, 12, 15),
        )

        out = capsys.readouterr().out
        assert "[plan] Optimalisering: ferdig" in out
        assert "kjører finjustering og forbedringsanalyse" in out
        assert "[plan] Forsøk 1/1: fairness score=" in out

    def test_marks_checkpoint_done(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        run(
            _make_config(), {},
            state,
            datetime(2025, 9, 1), datetime(2025, 12, 15),
        )
        assert state.is_done(StageName.PLANNING)

    def test_preserves_manual_adjustments_from_previous_checkpoint(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        first = run(
            _make_config(), {},
            state,
            datetime(2025, 9, 1), datetime(2025, 12, 15),
        )
        checkpoint = dict(first)
        checkpoint["plan"] = dict(first["plan"])
        checkpoint["plan"]["manual_adjustments"] = {
            "locked_dates": ["2025-10-05"],
            "pinned_tournament_ids": ["abc12345"],
        }
        state.write_stage(StageName.PLANNING, checkpoint, status=StageStatus.DONE)

        second = run(
            _make_config(), {},
            state,
            datetime(2025, 9, 1), datetime(2025, 12, 15),
        )

        assert second["plan"]["manual_adjustments"]["locked_dates"] == ["2025-10-05"]
        assert second["plan"]["manual_adjustments"]["pinned_tournament_ids"] == ["abc12345"]

    def test_duplicate_labels_are_disambiguated_in_counts(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        result = run(
            _make_duplicate_label_config(), {},
            state,
            datetime(2025, 9, 1), datetime(2025, 12, 1),
        )
        plan = result["plan"]

        assert state.is_done(StageName.PLANNING)
        assert len(plan["team_game_counts"]) == 6
        assert any("U10" in key for key in plan["team_game_counts"])
        assert any("U11" in key for key in plan["team_game_counts"])
        assert plan["fairness_gate"]["status"] == "pass"


class TestIterationsFlag:
    """Tests for the --iterations multi-seed planning loop."""

    def test_iterations_one_produces_valid_plan(self, tmp_path):
        """iterations=1 (default) produces a non-empty plan, matching existing behavior."""
        state = PipelineState(tmp_path / "pipeline")
        result = run(
            _make_config(), {},
            state,
            datetime(2025, 9, 1), datetime(2025, 12, 15),
            iterations=1,
        )
        assert state.is_done(StageName.PLANNING)
        assert "plan" in result
        assert len(result["plan"]["tournaments"]) > 0

    def test_iterations_three_produces_valid_plan(self, tmp_path):
        """iterations=3 runs three seeds and keeps the best-scoring plan."""
        state = PipelineState(tmp_path / "pipeline")
        result = run(
            _make_config(), {},
            state,
            datetime(2025, 9, 1), datetime(2025, 12, 15),
            iterations=3,
        )
        assert state.is_done(StageName.PLANNING)
        assert "plan" in result
        assert len(result["plan"]["tournaments"]) > 0

    def test_stops_early_once_an_attempt_passes(self, tmp_path):
        """Once an attempt clears the fairness gate, remaining iterations are skipped."""
        state = PipelineState(tmp_path / "pipeline")
        result = run(
            _make_duplicate_label_config(), {},
            state,
            datetime(2025, 9, 1), datetime(2025, 12, 1),
            iterations=3,
        )
        assert result["plan"]["fairness_gate"]["status"] == "pass"
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["status"] == "pass"

    def test_multi_iteration_score_at_least_single_iteration(self, tmp_path):
        """The best plan from 3 iterations has a composite score >= the single-iteration plan."""
        cfg = _make_config()
        start = datetime(2025, 9, 1)
        end = datetime(2025, 12, 15)

        state_single = PipelineState(tmp_path / "single")
        result_single = run(cfg, {}, state_single, start, end, iterations=1)
        score_single = result_single["plan"].get("fairness_gate", {}).get("score", 0)

        state_multi = PipelineState(tmp_path / "multi")
        result_multi = run(cfg, {}, state_multi, start, end, iterations=3)
        score_multi = result_multi["plan"].get("fairness_gate", {}).get("score", 0)

        assert score_multi >= score_single


class TestCandidateRankFunction:
    """Unit tests for the pure candidate-ranking helper."""

    def test_fail_status_always_ranks_below_warn_and_pass(self):
        from tournament_scheduler.pipeline.stage3_planning import _candidate_rank

        fail_rank = _candidate_rank("fail", score=99, tournament_count=100)
        warn_rank = _candidate_rank("warn", score=1, tournament_count=1)
        pass_rank = _candidate_rank("pass", score=1, tournament_count=1)

        assert fail_rank < warn_rank
        assert fail_rank < pass_rank
        assert warn_rank < pass_rank

    def test_same_status_breaks_tie_by_score_then_tournament_count(self):
        from tournament_scheduler.pipeline.stage3_planning import _candidate_rank

        assert _candidate_rank("pass", 80, 5) < _candidate_rank("pass", 90, 5)
        assert _candidate_rank("pass", 90, 5) < _candidate_rank("pass", 90, 6)

    def test_unknown_status_is_treated_like_warn(self):
        from tournament_scheduler.pipeline.stage3_planning import _candidate_rank

        assert _candidate_rank("mystery", 50, 1) == _candidate_rank("warn", 50, 1)


class TestCandidateTracking:
    """Tests for per-candidate reproducibility metadata recorded on the checkpoint."""

    def _fake_plan(self) -> SeasonPlan:
        return SeasonPlan(
            tournaments=[
                Tournament(
                    date=date(2025, 10, 5),
                    arena="Kongsberghallen",
                    age_group="U10",
                    teams=[
                        Team(club="Kongsberg", label="Kongsberg U10A", age_group="U10"),
                        Team(club="Skien", label="Skien U10A", age_group="U10"),
                    ],
                    games=[],
                )
            ]
        )

    def test_real_run_records_one_candidate_per_iteration(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        result = run(
            _make_config(), {},
            state,
            datetime(2025, 9, 1), datetime(2025, 12, 15),
            iterations=3,
        )

        assert len(result["candidates"]) == 3
        assert [c["attempt"] for c in result["candidates"]] == [1, 2, 3]
        assert result["selected_candidate_attempt"] in (1, 2, 3)
        for candidate in result["candidates"]:
            assert candidate["planner_version"]
            assert candidate["config_fingerprint"]
            assert candidate["source_fingerprint"]
            assert candidate["status"] in ("pass", "warn", "fail")
            assert isinstance(candidate["rank"], list)

    def test_config_fingerprint_is_stable_for_identical_config(self, tmp_path):
        cfg = _make_config()
        start, end = datetime(2025, 9, 1), datetime(2025, 12, 15)

        state_a = PipelineState(tmp_path / "a")
        result_a = run(cfg, {}, state_a, start, end, iterations=1)

        state_b = PipelineState(tmp_path / "b")
        result_b = run(cfg, {}, state_b, start, end, iterations=1)

        fp_a = result_a["candidates"][0]["config_fingerprint"]
        fp_b = result_b["candidates"][0]["config_fingerprint"]
        assert fp_a == fp_b

    def test_selected_candidate_matches_exported_plan(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        result = run(
            _make_config(), {},
            state,
            datetime(2025, 9, 1), datetime(2025, 12, 15),
            iterations=2,
        )
        selected = result["selected_candidate_attempt"]
        selected_candidate = next(c for c in result["candidates"] if c["attempt"] == selected)
        assert selected_candidate["score"] == result["plan"]["fairness_gate"]["score"]

    def test_fail_status_candidate_is_never_selected_over_a_passing_one(self, tmp_path):
        """A high raw score on a "fail" (hard-constraint) candidate must not
        win over a lower-scoring "pass" candidate — this is the exact bug
        issue #4 calls out: a good aggregate score must not hide a
        hard-constraint violation."""
        state = PipelineState(tmp_path / "pipeline")
        fake_planner = MagicMock()
        fake_planner.build_plan.return_value = self._fake_plan()
        fake_planner.rules_report.return_value = {"ok": True}

        with patch(
            "tournament_scheduler.pipeline.stage3_planning._make_planner",
            return_value=fake_planner,
        ), patch(
            "tournament_scheduler.pipeline.stage3_planning._build_fairness_gate",
            side_effect=[
                {"status": "fail", "score": 99, "metrics": []},  # attempt 1: high score, hard fail
                {"status": "pass", "score": 40, "metrics": []},  # attempt 2: lower score, but passes
            ],
        ):
            result = run(_make_config(), {}, state, datetime(2025, 9, 1), datetime(2025, 12, 15), iterations=2)

        assert result["selected_candidate_attempt"] == 2
        assert [c["status"] for c in result["candidates"]] == ["fail", "pass"]
        assert [c["score"] for c in result["candidates"]] == [99, 40]


class TestPlanToDict:
    def test_serializes_round_number(self):
        home = Team(club="Kongsberg", label="Kongsberg U10A", age_group="U10")
        away = Team(club="Skien", label="Skien U10A", age_group="U10")
        game = Game(home=home, away=away, parallel_slot=1, round_number=3)
        tournament = Tournament(
            date=date(2025, 10, 5),
            arena="Kongsberghallen",
            age_group="U10",
            teams=[home, away],
            games=[game],
        )
        plan = SeasonPlan(tournaments=[tournament])

        plan_dict = _plan_to_dict(plan)

        game_dict = plan_dict["tournaments"][0]["games"][0]
        assert game_dict["round_number"] == 3
