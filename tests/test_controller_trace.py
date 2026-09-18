"""Unit tests for the append-only controller decision trace (#390).

These tests cover the persistence/identity/summary contract of the trace
independently of the convergence driver: path scoping, append-only sequence
continuity across writer instances (restart/resume), malformed-line tolerance,
candidate-lineage reconstruction and the evidence-bundle reference.
"""

from __future__ import annotations

import json
from pathlib import Path

from tournament_scheduler.pipeline.controller_trace import (
    CONTROLLER_TRACE_FILENAME,
    EVENT_CANDIDATE_MUTATION,
    EVENT_DIRECTION_SELECTED,
    EVENT_FRONTIER_MUTATION,
    EVENT_OPERATOR_ANSWER,
    EVENT_PUBLICATION,
    EVENT_RUN_START,
    EVENT_STAGE_DECISION,
    EVENT_TERMINAL,
    ControllerTrace,
    candidate_lineage,
    controller_trace_path,
    controller_trace_reference,
    metric_pairs,
    read_controller_trace,
    resolve_trace_run_id,
    summarize_controller_trace,
)

RUN_ID = "20260918T135604Z-8d38f146"


def test_trace_path_is_run_scoped_and_outside_the_export_tree(tmp_path: Path) -> None:
    path = controller_trace_path(tmp_path, RUN_ID)
    assert path == tmp_path / "logs" / RUN_ID / CONTROLLER_TRACE_FILENAME
    assert "export" not in path.parts
    # An unresolved run identity still gets a durable, non-colliding scope.
    assert controller_trace_path(tmp_path, None) == tmp_path / "logs" / "unscoped" / CONTROLLER_TRACE_FILENAME


def test_trace_appends_across_writer_instances_and_skips_malformed_lines(
    tmp_path: Path,
) -> None:
    first = ControllerTrace(tmp_path, RUN_ID)
    first.emit(EVENT_RUN_START, phase="convergence", candidate_fingerprint="fp0")
    first.emit(EVENT_DIRECTION_SELECTED, epoch=1, direction="placement", finding_id="f1")

    # A second writer (a resumed process) must continue the same append-only
    # file with a monotonically increasing sequence, not truncate it.
    second = ControllerTrace(tmp_path, RUN_ID)
    second.emit(EVENT_TERMINAL, reason="pass", detail="done")

    path = controller_trace_path(tmp_path, RUN_ID)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("not json\n\n")

    events = read_controller_trace(tmp_path, RUN_ID)
    assert [entry["seq"] for entry in events] == [1, 2, 3]
    assert all(entry["run_id"] == RUN_ID for entry in events)
    assert events[-1]["event"] == EVENT_TERMINAL


def test_trace_events_never_persist_chain_of_thought_fields(tmp_path: Path) -> None:
    trace = ControllerTrace(tmp_path, RUN_ID)
    record = trace.emit(
        EVENT_CANDIDATE_MUTATION,
        candidate_before="a",
        candidate_after="b",
        rationale="non-dominated placement candidate; reduced one unresolved placement",
        metrics_before={"unresolved_placements": 44},
        metrics_after={"unresolved_placements": 43},
    )
    assert record["rationale"].startswith("non-dominated")
    assert "chain_of_thought" not in record
    persisted = json.loads(controller_trace_path(tmp_path, RUN_ID).read_text(encoding="utf-8"))
    assert set(persisted).issubset(
        {
            "schema_version",
            "run_id",
            "seq",
            "at",
            "event",
            "candidate_before",
            "candidate_after",
            "rationale",
            "metrics_before",
            "metrics_after",
        }
    )


def test_candidate_lineage_reconstructs_replaced_repairs() -> None:
    events = [
        {"event": EVENT_RUN_START, "seq": 1},
        {
            "event": EVENT_CANDIDATE_MUTATION,
            "seq": 2,
            "epoch": 1,
            "direction": "placement",
            "finding_id": "unplaced:X:1",
            "option_id": "o1",
            "candidate_before": "fp0",
            "candidate_after": "fp1",
            "hard_verification_ok": True,
            "metrics_before": {"unresolved_placements": 44},
            "metrics_after": {"unresolved_placements": 43},
        },
        {
            "event": EVENT_CANDIDATE_MUTATION,
            "seq": 3,
            "epoch": 2,
            "direction": "hosting",
            "finding_id": "hosting_deficit:Y",
            "option_id": "o2",
            "candidate_before": "fp1",
            "candidate_after": "fp2",
            "hard_verification_ok": True,
            "metrics_before": {"unresolved_placements": 43},
            "metrics_after": {"unresolved_placements": 43},
        },
    ]
    lineage = candidate_lineage(events)
    assert [item["option_id"] for item in lineage] == ["o1", "o2"]
    assert lineage[0]["candidate_after"] == lineage[1]["candidate_before"] == "fp1"


def test_summary_reports_epochs_directions_frontier_and_terminal() -> None:
    events = [
        {"event": EVENT_RUN_START, "seq": 1},
        {"event": EVENT_DIRECTION_SELECTED, "seq": 2, "direction": "placement"},
        {"event": EVENT_DIRECTION_SELECTED, "seq": 3, "direction": "hosting"},
        {"event": EVENT_DIRECTION_SELECTED, "seq": 4, "direction": "placement"},
        {
            "event": EVENT_FRONTIER_MUTATION,
            "seq": 5,
            "accepted": True,
            "candidate_ref": "pareto:placement:aaa",
            "dominated_refs": [],
            "pruned_refs": ["pareto:old:bbb"],
        },
        {
            "event": EVENT_CANDIDATE_MUTATION,
            "seq": 6,
            "epoch": 1,
            "direction": "placement",
            "candidate_before": "fp0",
            "candidate_after": "fp1",
            "hard_verification_ok": True,
        },
        {"event": EVENT_TERMINAL, "seq": 7, "reason": "pass", "detail": "done"},
    ]
    summary = summarize_controller_trace(events)
    assert summary["event_count"] == 7
    assert summary["directions"] == {"hosting": 1, "placement": 2}
    assert summary["committed_mutations"] == 1
    assert summary["frontier_adds"] == ["pareto:placement:aaa"]
    assert summary["frontier_prunes"] == ["pareto:old:bbb"]
    assert summary["terminal_reason"] == "pass"
    assert summary["first_candidate_fingerprint"] == "fp0"
    assert summary["last_candidate_fingerprint"] == "fp1"


def test_metric_pairs_projects_before_after_delta() -> None:
    before, after = metric_pairs(
        {
            "hard_violations_before": 0,
            "hard_violations_after": 0,
            "unresolved_placement_obligations_before": 3,
            "unresolved_placement_obligations_after": 2,
            "changed_tournament_count": 1,
        }
    )
    assert before == {"hard_violations": 0, "unresolved_placement_obligations": 3}
    assert after == {"hard_violations": 0, "unresolved_placement_obligations": 2}


def test_summary_includes_stage_decisions_answers_and_publications() -> None:
    events = [
        {
            "event": EVENT_STAGE_DECISION,
            "seq": 1,
            "stage": "planning",
            "capability": "stage3_interactive",
            "action_id": "apply_candidate",
            "accepted": True,
            "candidate_ref": "stage3_interactive:attempt_2",
            "candidate_fingerprint": "fp1",
        },
        {
            "event": EVENT_OPERATOR_ANSWER,
            "seq": 2,
            "question_id": "q-1",
            "question_type": "external_publication",
            "scope": "run",
            "answer": "godkjenn",
            "decided_by": "operator",
            "candidate_fingerprint": "fp1",
            "export_fingerprint": "efp1",
        },
        {
            "event": EVENT_PUBLICATION,
            "seq": 3,
            "status": "ok",
            "export_fingerprint": "efp1",
            "bundle_fingerprint": "bfp1",
            "pages_commit": "abc123",
            "pages_branch": "gh-pages",
            "verify_status": "ok",
        },
    ]
    summary = summarize_controller_trace(events)
    assert summary["event_counts"] == {
        EVENT_OPERATOR_ANSWER: 1,
        EVENT_PUBLICATION: 1,
        EVENT_STAGE_DECISION: 1,
    }
    assert summary["stage_decisions"][0]["action_id"] == "apply_candidate"
    assert summary["operator_answers"][0]["answer"] == "godkjenn"
    assert summary["publications"][0]["pages_commit"] == "abc123"


def test_resolve_trace_run_id_falls_back_to_the_manifest(tmp_path: Path) -> None:
    from tournament_scheduler.pipeline.run_manifest import RunManifest

    RunManifest(str(tmp_path)).start_run("objective", run_id="run-fallback")
    assert resolve_trace_run_id(tmp_path) == "run-fallback"
    assert resolve_trace_run_id(tmp_path, "explicit") == "explicit"


def test_trace_reference_is_compact_and_includes_a_summary(tmp_path: Path) -> None:
    trace = ControllerTrace(tmp_path, RUN_ID)
    trace.emit(EVENT_RUN_START, phase="convergence")
    trace.emit(EVENT_TERMINAL, reason="pass", detail="done")
    reference = controller_trace_reference(tmp_path, RUN_ID)
    assert reference["run_id"] == RUN_ID
    assert reference["event_count"] == 2
    assert reference["summary"]["terminal_reason"] == "pass"
    # The reference never embeds the raw event list.
    assert "events" not in reference
