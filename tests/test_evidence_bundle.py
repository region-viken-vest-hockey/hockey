"""Unit tests for tournament_scheduler.pipeline.evidence_bundle (issue #264 P0)."""

from __future__ import annotations

from tournament_scheduler.pipeline.evidence_bundle import (
    append_stage3_attempt_log_entry,
    build_run_evidence_bundle,
    build_stage3_attempt_entry,
    clear_stage3_attempt_log,
    read_stage3_attempt_log,
    stage3_attempt_log_path,
)


def _team(club: str, label: str, age_group: str) -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _candidate(seed: int) -> dict:
    teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
    return {
        "schema_version": 1,
        "source": {"planner": "stage3_optimizer", "seed": seed},
        "tournaments": [
            {
                "id": f"t1-{seed}",
                "date": "2026-01-05",
                "arena": "Jarhallen",
                "age_group": "U10",
                "host_club": "Jar",
                "teams": teams,
                "games": [{"home": "Jar 1", "away": "Kongsberg 1", "parallel_slot": 0, "round_number": 1}],
            }
        ],
    }


class TestStage3AttemptLog:
    def test_read_returns_empty_list_when_missing(self, tmp_path):
        assert read_stage3_attempt_log(tmp_path) == []

    def test_append_then_read_round_trips(self, tmp_path):
        entry = build_stage3_attempt_entry(attempt=1, candidate=_candidate(1), problem=None)
        append_stage3_attempt_log_entry(tmp_path, entry)
        log = read_stage3_attempt_log(tmp_path)
        assert len(log) == 1
        assert log[0]["attempt"] == 1
        assert log[0]["candidate_fingerprint"] == entry["candidate_fingerprint"]

    def test_append_accumulates_across_calls(self, tmp_path):
        append_stage3_attempt_log_entry(
            tmp_path, build_stage3_attempt_entry(attempt=1, candidate=_candidate(1), problem=None)
        )
        append_stage3_attempt_log_entry(
            tmp_path, build_stage3_attempt_entry(attempt=2, candidate=_candidate(2), problem=None)
        )
        log = read_stage3_attempt_log(tmp_path)
        assert [entry["attempt"] for entry in log] == [1, 2]

    def test_clear_removes_the_file(self, tmp_path):
        append_stage3_attempt_log_entry(
            tmp_path, build_stage3_attempt_entry(attempt=1, candidate=_candidate(1), problem=None)
        )
        assert stage3_attempt_log_path(tmp_path).exists()
        clear_stage3_attempt_log(tmp_path)
        assert not stage3_attempt_log_path(tmp_path).exists()
        assert read_stage3_attempt_log(tmp_path) == []

    def test_clear_on_missing_file_does_not_raise(self, tmp_path):
        clear_stage3_attempt_log(tmp_path)  # no-op, must not raise

    def test_malformed_file_reads_as_empty(self, tmp_path):
        stage3_attempt_log_path(tmp_path).write_text("not json", encoding="utf-8")
        assert read_stage3_attempt_log(tmp_path) == []

    def test_non_list_json_reads_as_empty(self, tmp_path):
        stage3_attempt_log_path(tmp_path).write_text('{"not": "a list"}', encoding="utf-8")
        assert read_stage3_attempt_log(tmp_path) == []


class TestBuildStage3AttemptEntry:
    def test_entry_carries_independent_verify_and_score_results(self):
        entry = build_stage3_attempt_entry(attempt=1, candidate=_candidate(1), problem=None)
        assert entry["verify_result"]["ok"] is True
        assert "opponent_diversity" in entry["score_result"]
        assert entry["candidate_source"] == {"planner": "stage3_optimizer", "seed": 1}

    def test_fingerprint_is_stable_for_identical_content(self):
        entry_a = build_stage3_attempt_entry(attempt=1, candidate=_candidate(7), problem=None)
        entry_b = build_stage3_attempt_entry(attempt=2, candidate=_candidate(7), problem=None)
        assert entry_a["candidate_fingerprint"] == entry_b["candidate_fingerprint"]

    def test_fingerprint_changes_with_content(self):
        entry_a = build_stage3_attempt_entry(attempt=1, candidate=_candidate(1), problem=None)
        entry_b = build_stage3_attempt_entry(attempt=1, candidate=_candidate(2), problem=None)
        assert entry_a["candidate_fingerprint"] != entry_b["candidate_fingerprint"]


class TestBuildRunEvidenceBundle:
    def test_bundle_contains_expected_top_level_keys(self):
        candidate = _candidate(1)
        bundle = build_run_evidence_bundle(
            run_id="run-123",
            input_fingerprint={"path": "input.xlsx", "sha256": "abc"},
            decision_log=[{"context": {}, "action": {}, "result": {}}],
            scraping_checkpoint={
                "sources": [{"name": "a"}, {"name": "b"}],
                "blocked": ["b"],
                "club_calendar_status": {"Jar": "known", "Tønsberg": "unknown"},
            },
            stage3_attempt_log=[build_stage3_attempt_entry(attempt=1, candidate=candidate, problem=None)],
            final_candidate=candidate,
            final_verify_result={"ok": True, "violations": []},
            final_score_result={"participation": {}},
            export_dir="/tmp/export/2026-01-01T0000",
            export_output_files={"excel": "a.xlsx"},
        )
        assert bundle["run_id"] == "run-123"
        assert bundle["input_fingerprint"]["sha256"] == "abc"
        assert bundle["source_summary"]["sources_scanned"] == 2
        assert bundle["source_summary"]["blocked_sources"] == ["b"]
        assert bundle["source_summary"]["club_calendar_status"]["Tønsberg"] == "unknown"
        assert len(bundle["decision_log"]) == 1
        assert len(bundle["stage3_attempt_log"]) == 1
        assert bundle["final_candidate"]["fingerprint"]
        assert bundle["final_candidate"]["source"] == candidate["source"]
        assert bundle["final_verify_result"]["ok"] is True
        assert bundle["export"]["dir"] == "/tmp/export/2026-01-01T0000"
        assert bundle["export"]["output_files"] == {"excel": "a.xlsx"}

    def test_bundle_handles_missing_optional_inputs(self):
        bundle = build_run_evidence_bundle(
            run_id="",
            input_fingerprint=None,
            decision_log=None,
            scraping_checkpoint=None,
            stage3_attempt_log=None,
            final_candidate=None,
            final_verify_result=None,
            final_score_result=None,
            export_dir=None,
            export_output_files=None,
        )
        assert bundle["final_candidate"] is None
        assert bundle["decision_log"] == []
        assert bundle["stage3_attempt_log"] == []
        assert bundle["source_summary"]["sources_scanned"] == 0
