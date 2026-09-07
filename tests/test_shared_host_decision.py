"""Tests for the shared/joint-club hosting decision (issue #274).

The LLM/controller may only choose among a shared registration's own
constituent clubs; this is enforced deterministically by the existing
`application.decisions.validate_decision_action` machinery via the
`assign_shared_host` action's `chosen_club` enum -- no separate ad hoc
validator is needed.
"""

from tournament_scheduler.application.decisions import (
    DecisionAction,
    validate_decision_action,
)
from tournament_scheduler.hosting_coverage import shared_registration_facts
from tournament_scheduler.shared_host_decision import (
    build_shared_host_decision_context,
    shared_host_decision_record,
)


def _facts():
    teams = [{"club": "Kongsberg/Tønsberg", "age_group": "JU12", "label": "Kongsberg/Tønsberg JU12"}]
    tournaments = [{"host_club": "Kongsberg", "age_group": "U9"}]
    club_calendar_status = {"Kongsberg": "known", "Tønsberg": "untrusted"}
    return shared_registration_facts(teams, tournaments, club_calendar_status)[0]


class TestBuildSharedHostDecisionContext:
    def test_context_carries_constituent_facts_and_enum(self):
        context = build_shared_host_decision_context("run-1", "Kongsberg/Tønsberg", "JU12", _facts())

        assert context.available_actions == ("assign_shared_host", "request_operator")
        assert context.facts["constituents"] == ["Kongsberg", "Tønsberg"]
        schema = context.action_parameters["assign_shared_host"]["chosen_club"]
        assert schema["enum"] == ["Kongsberg", "Tønsberg"]

    def test_accepts_either_constituent(self):
        context = build_shared_host_decision_context("run-1", "Kongsberg/Tønsberg", "JU12", _facts())

        for club in ("Kongsberg", "Tønsberg"):
            action = DecisionAction(action_id="assign_shared_host", arguments={"chosen_club": club})
            validate_decision_action(context, action)  # must not raise

    def test_rejects_a_non_constituent_club(self):
        context = build_shared_host_decision_context("run-1", "Kongsberg/Tønsberg", "JU12", _facts())
        action = DecisionAction(action_id="assign_shared_host", arguments={"chosen_club": "Jar"})

        try:
            validate_decision_action(context, action)
            assert False, "expected a validation error for a non-constituent club"
        except Exception as exc:  # InvalidDecisionArgumentValueError
            assert "chosen_club" in str(exc)

    def test_rejects_missing_chosen_club(self):
        context = build_shared_host_decision_context("run-1", "Kongsberg/Tønsberg", "JU12", _facts())
        action = DecisionAction(action_id="assign_shared_host", arguments={})

        try:
            validate_decision_action(context, action)
            assert False, "expected a validation error for a missing chosen_club"
        except Exception as exc:
            assert "missing required argument" in str(exc)

    def test_calendar_unavailability_does_not_shrink_the_enum(self):
        # Tønsberg is untrusted (issue #274 P0) but must still be a legal
        # choice -- placement status is decided after the fairness choice,
        # never before it.
        context = build_shared_host_decision_context("run-1", "Kongsberg/Tønsberg", "JU12", _facts())
        action = DecisionAction(action_id="assign_shared_host", arguments={"chosen_club": "Tønsberg"})
        validate_decision_action(context, action)  # must not raise


class TestSharedHostDecisionRecord:
    def test_builds_provenance_dict(self):
        record = shared_host_decision_record(
            "Kongsberg/Tønsberg",
            "JU12",
            "Tønsberg",
            "Kongsberg already hosts materially more tournaments this season.",
            decided_by="llm",
            decided_at="2026-09-07T00:00:00",
        )
        assert record == {
            "registration": "Kongsberg/Tønsberg",
            "age_group": "JU12",
            "chosen_club": "Tønsberg",
            "rationale": "Kongsberg already hosts materially more tournaments this season.",
            "decided_by": "llm",
            "decided_at": "2026-09-07T00:00:00",
        }
