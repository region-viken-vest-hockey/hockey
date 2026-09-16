"""Integration tests for ``_resolve_arena_conflict_decisions`` (the CLI
orchestrator side of the internal arena/time double-booking decision):
detecting a collision, pausing for a harness decision, and applying a
recorded decision to a freshly rebuilt candidate by its stable key rather
than by (ephemeral, regenerated-every-rebuild) tournament id.
"""

from tournament_scheduler.cli.pipeline_orchestrator.arena_conflict_decisions import (
    _apply_arena_conflict_decision,
    _collision_facts,
    _resolve_arena_conflict_decisions,
)
from tournament_scheduler.cli.pipeline_orchestrator.run_command_interactive import (
    _emit_pending_stage3_subdecision_context,
)
from tournament_scheduler.pipeline.state import PipelineState

ICE_TIME = {"U11": 30, "U10": 30, "U12": 30}


def _tournament(tid, age_group, arena, date_str, start_time):
    return {
        "id": tid,
        "date": date_str,
        "arena": arena,
        "age_group": age_group,
        "host_club": "Jar",
        "teams": [{"club": "Jar", "label": f"Jar {age_group}", "age_group": age_group}],
        "games": [{"home": f"Jar {age_group}", "away": f"Jar {age_group}", "round_number": 1, "parallel_slot": 0}],
        "start_time": start_time,
        "cancelled": False,
    }


def _candidate_with_collision():
    return {
        "tournaments": [
            _tournament("aaa11111", "U11", "Jar Isforum", "2026-11-07", "09:00"),
            _tournament("bbb22222", "U10", "Jar Isforum", "2026-11-07", "09:15"),
        ]
    }


def _candidate_with_unmappable_same_side_collision():
    return {
        "tournaments": [
            _tournament("same-side-1", "U11", "Jar Isforum", "2026-11-07", "09:00"),
            _tournament("same-side-2", "U11", "Jar Isforum", "2026-11-07", "09:15"),
        ]
    }


def _candidate_with_two_collisions():
    return {
        "tournaments": [
            _tournament("aaa11111", "U11", "Jar Isforum", "2026-11-07", "09:00"),
            _tournament("bbb22222", "U10", "Jar Isforum", "2026-11-07", "09:15"),
            _tournament("ccc33333", "U12", "Jar Isforum", "2026-11-07", "09:20"),
        ]
    }


class TestCollisionFacts:
    def test_detects_the_overlap(self):
        candidate = _candidate_with_collision()
        facts = _collision_facts(candidate, ICE_TIME)

        assert len(facts) == 1
        side_ids = {s["tournament_id"] for s in facts[0]["sides"]}
        assert side_ids == {"aaa11111", "bbb22222"}

    def test_no_facts_when_not_overlapping(self):
        candidate = _candidate_with_collision()
        candidate["tournaments"][1]["start_time"] = "13:00"
        facts = _collision_facts(candidate, ICE_TIME)
        assert facts == []


class TestApplyArenaConflictDecision:
    def test_demotes_the_losing_tournament_to_manual_placement(self):
        candidate = _candidate_with_collision()
        _apply_arena_conflict_decision(candidate, "aaa11111", "bbb22222", "kept higher hosting deficit")

        loser = next(t for t in candidate["tournaments"] if t["id"] == "bbb22222")
        winner = next(t for t in candidate["tournaments"] if t["id"] == "aaa11111")
        assert loser["start_time"] is None
        assert loser["manual_booking_reason"]
        assert winner["start_time"] == "09:00"

        placements = candidate["manual_arena_conflict_placements"]
        assert placements[0]["tournament_id"] == "bbb22222"
        assert placements[0]["kept_tournament_id"] == "aaa11111"

        # The demoted tournament no longer occupies an arena interval, so
        # the collision is gone.
        assert _collision_facts(candidate, ICE_TIME) == []


class TestPendingStage3SubdecisionContext:
    def test_reemits_pending_arena_conflict_instead_of_restarting_stage3(self, tmp_path, capsys):
        import json

        from tournament_scheduler.arena_conflict_decision import build_arena_conflict_decision_context
        from tournament_scheduler.cli.pipeline_orchestrator.interactive_state_io import _current_run_id

        state = PipelineState(tmp_path)
        run_id = _current_run_id(state)
        facts = _collision_facts(_candidate_with_collision(), ICE_TIME)[0]
        context = build_arena_conflict_decision_context(run_id, facts)
        (tmp_path / "arena_conflict_decision_state.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "pending": {"key": "arena|date|sides"},
                    "last_context": context.to_dict(),
                    "decisions": [],
                    "unresolved": [],
                }
            ),
            encoding="utf-8",
        )

        code = _emit_pending_stage3_subdecision_context(state, str(tmp_path), 3)

        assert code == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["capability"] == "arena_conflict_resolution"

    def test_ignores_pending_subdecision_for_other_stages(self, tmp_path):
        state = PipelineState(tmp_path)

        assert _emit_pending_stage3_subdecision_context(state, str(tmp_path), 4) is None


class TestResolveArenaConflictDecisionsPauseAndResume:
    def test_pauses_for_a_collision_and_persists_pending_state(self, tmp_path):
        state = PipelineState(tmp_path)
        plan = {"plan": _candidate_with_collision()}

        pause_code = _resolve_arena_conflict_decisions(state, plan, ICE_TIME, lambda msg: None, interactive=True)

        assert pause_code == 2
        saved_path = tmp_path / "arena_conflict_decision_state.json"
        assert saved_path.exists()

    def test_no_pause_when_no_collision(self, tmp_path):
        state = PipelineState(tmp_path)
        candidate = _candidate_with_collision()
        candidate["tournaments"][1]["start_time"] = "13:00"
        plan = {"plan": candidate}

        pause_code = _resolve_arena_conflict_decisions(state, plan, ICE_TIME, lambda msg: None, interactive=True)
        assert pause_code is None

    def test_a_recorded_decision_survives_a_stage3_rebuild_by_stable_key(self, tmp_path):
        import json

        from tournament_scheduler.arena_conflict_decision import arena_conflict_decision_record, collision_key
        from tournament_scheduler.cli.pipeline_orchestrator.interactive_state_io import _current_run_id

        state = PipelineState(tmp_path)
        run_id = _current_run_id(state)

        # First attempt's plan produces the collision and its facts.
        first_candidate = _candidate_with_collision()
        facts = _collision_facts(first_candidate, ICE_TIME)[0]

        # Record a resolved decision the way the CLI's pending_arena_conflict
        # handling does after a harness answers -- keyed on the stable
        # (arena, date, sides) identity, not the ephemeral tournament ids.
        record = arena_conflict_decision_record(facts, "aaa11111", "bbb22222", "kept the fairness-deficit host")
        (tmp_path / "arena_conflict_decision_state.json").write_text(
            json.dumps({"run_id": run_id, "decisions": [record], "unresolved": []}), encoding="utf-8"
        )

        # Stage 3 "rebuilds" the plan with brand-new tournament ids for the
        # very same real-world collision (same arena/date/age_group/host).
        rebuilt_candidate = {
            "tournaments": [
                _tournament("fresh-id-1", "U11", "Jar Isforum", "2026-11-07", "09:00"),
                _tournament("fresh-id-2", "U10", "Jar Isforum", "2026-11-07", "09:15"),
            ]
        }
        plan = {"plan": rebuilt_candidate}

        pause_code = _resolve_arena_conflict_decisions(state, plan, ICE_TIME, lambda msg: None, interactive=True)

        # The previously recorded decision matches by stable key and is
        # re-applied automatically -- no new pause needed.
        assert pause_code is None
        loser = next(t for t in rebuilt_candidate["tournaments"] if t["age_group"] == "U10")
        winner = next(t for t in rebuilt_candidate["tournaments"] if t["age_group"] == "U11")
        assert loser["start_time"] is None
        assert loser["manual_booking_reason"]
        assert winner["start_time"] == "09:00"
        assert collision_key(facts) == record["key"]

    def test_unmappable_recorded_key_does_not_suppress_still_real_collision(self, tmp_path, capsys):
        import json

        from tournament_scheduler.arena_conflict_decision import arena_conflict_decision_record
        from tournament_scheduler.cli.pipeline_orchestrator.interactive_state_io import _current_run_id

        state = PipelineState(tmp_path)
        run_id = _current_run_id(state)
        candidate = _candidate_with_unmappable_same_side_collision()
        facts = _collision_facts(candidate, ICE_TIME)[0]
        record = arena_conflict_decision_record(facts, "same-side-1", "same-side-2", "ambiguous same-side pair")
        assert record["keep_side"] == record["manual_side"]
        (tmp_path / "arena_conflict_decision_state.json").write_text(
            json.dumps({"run_id": run_id, "decisions": [record], "unresolved": []}), encoding="utf-8"
        )

        pause_code = _resolve_arena_conflict_decisions(
            state, {"plan": candidate}, ICE_TIME, lambda msg: None, interactive=True
        )

        assert pause_code == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["capability"] == "arena_conflict_resolution"
        assert _collision_facts(candidate, ICE_TIME)

    def test_stage3_emit_persists_recorded_arena_decision_before_stage4(self, tmp_path, monkeypatch, capsys):
        import json
        from datetime import datetime

        from tournament_scheduler.arena_conflict_decision import arena_conflict_decision_record
        from tournament_scheduler.cli.pipeline_orchestrator import interactive_decision_emit as emit
        from tournament_scheduler.cli.pipeline_orchestrator.interactive_state_io import _current_run_id
        from tournament_scheduler.pipeline.state import StageName

        state = PipelineState(tmp_path)
        run_id = _current_run_id(state)
        original_candidate = _candidate_with_collision()
        facts = _collision_facts(original_candidate, ICE_TIME)[0]
        record = arena_conflict_decision_record(facts, "aaa11111", "bbb22222", "operator kept U11")
        (tmp_path / "arena_conflict_decision_state.json").write_text(
            json.dumps({"run_id": run_id, "decisions": [record], "unresolved": []}), encoding="utf-8"
        )
        plan = {"plan": _candidate_with_collision(), "warnings": []}

        monkeypatch.setattr(emit, "_mid_planning_decision_problem", lambda *args, **kwargs: {"ice_time_minutes": ICE_TIME})
        monkeypatch.setattr(emit, "_maybe_run_stage3_cp_sat_shadow", lambda *args, **kwargs: None)
        monkeypatch.setattr(emit, "_baseline_hard_violations_for_plan", lambda *args, **kwargs: [])
        monkeypatch.setattr(emit, "_host_team_missing_repair_context", lambda *args, **kwargs: None)

        code = emit._emit_stage3_interactive_decision(
            state,
            str(tmp_path),
            {"stage3_wall_clock_ceiling_seconds": 0},
            {},
            datetime(2026, 9, 1),
            datetime(2027, 4, 1),
            plan,
            lambda msg: None,
        )

        assert code == 2
        capsys.readouterr()
        persisted = state.read_stage(StageName.PLANNING)
        loser = next(t for t in persisted["plan"]["tournaments"] if t["id"] == "bbb22222")
        assert loser["start_time"] is None
        assert loser["manual_booking_reason"]

    def test_answering_arena_conflict_mutates_checkpoint_without_replanning(self, tmp_path, monkeypatch, capsys):
        import json
        from types import SimpleNamespace
        from unittest.mock import patch

        from tournament_scheduler.cli.pipeline_orchestrator.run_command_interactive import _cmd_run_interactive
        from tournament_scheduler.pipeline.state import StageName, StageStatus

        state = PipelineState(tmp_path)
        cfg = {"start_date": "2026-09-01", "end_date": "2027-04-30"}
        state.write_stage(StageName.CONFIG, cfg, status=StageStatus.DONE)
        state.write_stage(StageName.SCRAPING, {"sources": []}, status=StageStatus.DONE)
        checkpoint = {"plan": _candidate_with_two_collisions(), "warnings": []}
        state.write_stage(StageName.PLANNING, checkpoint, status=StageStatus.DONE)

        assert _resolve_arena_conflict_decisions(state, checkpoint, ICE_TIME, lambda msg: None, interactive=True) == 2
        first_context = json.loads(capsys.readouterr().out)
        keep = first_context["facts"]["sides"][0]["tournament_id"]

        args = SimpleNamespace(
            work_dir=str(tmp_path),
            input="input.xlsx",
            resume_from="3",
            non_strict=False,
            decision_action=json.dumps({
                "action_id": "resolve_arena_conflict",
                "arguments": {"keep_tournament_id": keep},
                "rationale": "keep the harder slot",
            }),
            decision_action_file=None,
        )
        monkeypatch.setattr(
            "tournament_scheduler.pipeline.stage1_config.load_effective_config",
            lambda *args, **kwargs: cfg,
        )
        with patch(
            "tournament_scheduler.cli.pipeline_orchestrator.run_command_interactive._mid_planning_decision_problem",
            return_value={"ice_time_minutes": ICE_TIME},
        ), patch(
            "tournament_scheduler.cli.pipeline_orchestrator.run_command_interactive._run_stage3",
        ) as run_stage3:
            exit_code = _cmd_run_interactive(args)

        assert exit_code == 2
        run_stage3.assert_not_called()
        persisted = state.read_stage(StageName.PLANNING)
        demoted = [t for t in persisted["plan"]["tournaments"] if t.get("start_time") is None]
        assert len(demoted) == 1
        second_context = json.loads(capsys.readouterr().out)
        assert second_context["capability"] == "arena_conflict_resolution"
