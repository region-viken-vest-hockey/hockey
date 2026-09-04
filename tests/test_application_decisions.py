import pytest

from tournament_scheduler.application.decisions import (
    DecisionAction,
    DecisionActionNotAvailableError,
    DecisionContext,
    DecisionResult,
    HardViolationBlocksActionError,
    HumanApprovalRequiredError,
    InvalidDecisionArgumentsError,
    UnknownDecisionActionError,
    decide,
    record_llm_decision,
    validate_decision_action,
)
from tournament_scheduler.pipeline.run_manifest import RunManifest


def _context(**overrides) -> DecisionContext:
    defaults = dict(
        run_id="run-1",
        capability="scraping",
        stage="stage2",
        objective="collect calendar data",
        available_actions=("proceed", "abort", "recover_source", "request_operator"),
    )
    defaults.update(overrides)
    return DecisionContext(**defaults)


def test_decision_context_round_trips_through_dict():
    context = _context(
        facts={"sources_scanned": 5},
        hard_violations=("blocked:kongsberg",),
        warnings=("low-event-count:sandefjord",),
        scorecard={"blocked_ratio": 0.2},
        baseline_ref="baseline-1",
        candidate_ref="candidate-2",
        prior_results=({"action_id": "retry_stage"},),
    )

    restored = DecisionContext.from_dict(context.to_dict())

    assert restored == context


def test_decision_action_round_trips_through_dict():
    action = DecisionAction(
        action_id="recover_source",
        target="kongsberg",
        arguments={"source": "kongsberg"},
        rationale="retry blocked source before aborting",
    )

    assert DecisionAction.from_dict(action.to_dict()) == action


def test_decision_result_round_trips_through_dict():
    result = DecisionResult(
        accepted=True,
        action_id="proceed",
        rationale="enough sources succeeded",
        changed_metrics={"blocked_ratio": 0.1},
        next_available_actions=("proceed",),
    )

    assert DecisionResult.from_dict(result.to_dict()) == result


def test_unknown_action_id_is_rejected():
    context = _context()
    action = DecisionAction(action_id="do_something_else")

    with pytest.raises(UnknownDecisionActionError):
        validate_decision_action(context, action)


def test_action_not_offered_by_context_is_rejected():
    context = _context(available_actions=("proceed", "abort"))
    action = DecisionAction(action_id="optimize_plan")

    with pytest.raises(DecisionActionNotAvailableError):
        validate_decision_action(context, action)


def test_missing_required_argument_is_rejected():
    context = _context(available_actions=("recover_source",))
    action = DecisionAction(action_id="recover_source", arguments={})

    with pytest.raises(InvalidDecisionArgumentsError):
        validate_decision_action(context, action)


def test_recover_source_with_required_argument_is_valid():
    context = _context(available_actions=("recover_source",))
    action = DecisionAction(action_id="recover_source", arguments={"source": "kongsberg"})

    validate_decision_action(context, action)  # does not raise


def test_hard_violation_blocks_proceed_and_apply_candidate():
    context = _context(
        available_actions=("proceed", "apply_candidate", "abort"),
        hard_violations=("duplicate_team:U16",),
    )

    with pytest.raises(HardViolationBlocksActionError):
        validate_decision_action(context, DecisionAction(action_id="proceed"))
    with pytest.raises(HardViolationBlocksActionError):
        validate_decision_action(
            context, DecisionAction(action_id="apply_candidate", arguments={"candidate_ref": "c1"})
        )

    # abort remains available while a hard violation is outstanding.
    validate_decision_action(context, DecisionAction(action_id="abort"))


def test_hard_violation_does_not_block_recovery_actions():
    context = _context(
        available_actions=("recover_source", "retry_stage"),
        hard_violations=("blocked:kongsberg",),
    )

    validate_decision_action(
        context, DecisionAction(action_id="recover_source", arguments={"source": "kongsberg"})
    )
    validate_decision_action(context, DecisionAction(action_id="retry_stage", arguments={"stage": "stage2"}))


def test_human_approval_required_restricts_to_safe_actions():
    context = _context(
        available_actions=("proceed", "abort", "request_operator", "keep_baseline"),
        requires_human_approval=True,
    )

    with pytest.raises(HumanApprovalRequiredError):
        validate_decision_action(context, DecisionAction(action_id="proceed"))

    validate_decision_action(
        context, DecisionAction(action_id="request_operator", arguments={"question": "publish now?"})
    )
    validate_decision_action(context, DecisionAction(action_id="keep_baseline"))


def test_decide_rejects_invalid_action_without_raising():
    context = _context(available_actions=("proceed",))
    action = DecisionAction(action_id="apply_candidate", arguments={"candidate_ref": "c1"})

    result = decide(context, action)

    assert result.accepted is False
    assert result.rejection_reason == "decision_action_not_available"
    assert result.action_id == "apply_candidate"


def test_decide_accepts_valid_action():
    context = _context(available_actions=("proceed",))
    action = DecisionAction(action_id="proceed", rationale="enough sources succeeded")

    result = decide(
        context,
        action,
        result_ref="stage2-checkpoint",
        changed_metrics={"blocked_ratio": 0.1},
        next_available_actions=("optimize_plan",),
    )

    assert result.accepted is True
    assert result.result_ref == "stage2-checkpoint"
    assert result.changed_metrics == {"blocked_ratio": 0.1}
    assert result.next_available_actions == ("optimize_plan",)


def test_record_llm_decision_persists_to_manifest_decision_log(tmp_path):
    RunManifest(str(tmp_path)).start_run("objective")
    context = _context(available_actions=("proceed",))
    action = DecisionAction(action_id="proceed", rationale="enough sources succeeded")
    result = decide(context, action)

    record_llm_decision(str(tmp_path), context, action, result)

    manifest = RunManifest(str(tmp_path)).read()
    assert len(manifest["decision_log"]) == 1
    entry = manifest["decision_log"][0]
    assert entry["action"]["action_id"] == "proceed"
    assert entry["result"]["accepted"] is True
    assert entry["context"]["capability"] == "scraping"
    assert "recorded_at" in entry
