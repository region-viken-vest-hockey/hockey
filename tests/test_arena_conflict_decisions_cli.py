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
from tournament_scheduler.pipeline.state import PipelineState

ICE_TIME = {"U11": 30, "U10": 30}


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
