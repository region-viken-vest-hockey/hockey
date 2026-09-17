"""Regression coverage for Stage 3 candidate identity normalization.

The interactive Stage3Session and deterministic repair providers must agree on
one candidate fingerprint even when an older/raw checkpoint omitted the
candidate schema_version that planning_contract.extract_candidate supplies.
"""

from tournament_scheduler.application.stage3_session import candidate_content_fingerprint
from tournament_scheduler.application.stage3_session_store import Stage3SessionStore, fingerprint_plan
from tournament_scheduler.host_team_missing_repair import candidate_fingerprint as repair_candidate_fingerprint
from tournament_scheduler.planning_contract import extract_candidate


def test_unversioned_checkpoint_has_same_session_and_repair_fingerprint(tmp_path):
    raw_candidate = {
        "tournaments": [
            {
                "id": "rvv-test-1",
                "age_group": "U11",
                "date": "2026-10-10",
                "host_club": "Alpha",
                "teams": [],
            }
        ]
    }
    checkpoint = {"plan": raw_candidate}

    # The production regression started with a raw Stage 3 checkpoint that did
    # not carry schema_version. The public planning contract normalizes that
    # shape before a repair provider fingerprints it.
    assert "schema_version" not in raw_candidate
    normalized_candidate = extract_candidate(checkpoint)
    assert "schema_version" in normalized_candidate

    session = Stage3SessionStore(tmp_path).bind_candidate(checkpoint, run_id="run-identity")
    repair_fingerprint = repair_candidate_fingerprint(normalized_candidate)

    assert session.candidate_fingerprint == repair_fingerprint
    assert fingerprint_plan(checkpoint) == repair_fingerprint
    assert candidate_content_fingerprint(raw_candidate) == repair_fingerprint


def test_candidate_scoped_pending_context_keeps_authoritative_identity(tmp_path):
    checkpoint = {"plan": {"tournaments": []}}
    store = Stage3SessionStore(tmp_path)
    session = store.bind_candidate(checkpoint, run_id="run-pending")

    normalized_candidate = extract_candidate(checkpoint)
    repair_fingerprint = repair_candidate_fingerprint(normalized_candidate)
    context = {
        "capability": "host_team_missing_repair",
        "facts": {"candidate_fingerprint": repair_fingerprint},
    }

    session.set_pending(capability="host_team_missing_repair", context=context)

    assert session.pending_fingerprint() == session.candidate_fingerprint
    assert session.pending_fingerprint() == repair_fingerprint
