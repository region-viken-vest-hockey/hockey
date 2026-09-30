"""Durable placement-infeasibility proofs (#600).

A bounded search that exhausts its declared scope proves infeasibility only for
that scope and only while the authoritative capacity/calendar facts are
unchanged. These tests cover the canonical evidence lifecycle:

* a current, obligation-specific proof resolves the unplaced finding as
  ``proven_infeasible_with_current_capacity`` while the raw obligation stays
  visible;
* the same bounded exhaustion *without* a proof stays actionable;
* a proof whose authoritative capacity/calendar fingerprint changed becomes
  stale and reopens the obligation;
* a proof recorded under a superseded search capability reopens the obligation;
* releasing a proof reopens it explicitly.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

from tests.test_unplaced_placement_repair import (
    YEAR,
    _base_plan,
    _obligation,
    _problem,
    _teams,
    _tournament,
    _write_season,
)
from tournament_scheduler.placement_infeasibility import (
    build_infeasibility_proof,
    coverage_from_proof,
    proof_is_current,
    proofs_by_obligation,
    search_scope_complete,
)
from tournament_scheduler.season_maintenance import list_findings, load_context, season_audit
from tournament_scheduler.season_state import (
    placement_infeasibility_report,
    record_placement_infeasibility_proofs,
    release_placement_infeasibility_proofs,
)
from tournament_scheduler.search_capability import SearchCapability
from tournament_scheduler.unplaced_placement_repair import (
    SEARCH_BOUNDED_EXHAUSTED,
    SEARCH_INCOMPLETE,
    enumerate_unplaced_placement_repairs,
    unplaced_placement_search_capability,
)

OBLIGATION_ID = "unplaced_placement:U10:2026-10-10:1"


def _infeasible_fixture(tmp_path: Path) -> Path:
    """A host whose calendar is fully booked and whose roster collides everywhere.

    The full supported ladder runs and finds nothing, so the search reaches
    ``bounded_search_exhausted`` with no untried applicable dimension.
    """

    teams = _teams(["Nordby", "Sorby", "Vestby"])
    problem = _problem(
        teams,
        start=date(2026, 10, 10),
        end=date(2026, 10, 10),
        busy={
            "Sorby": [
                {
                    "date": "2026-10-10",
                    "start": "00:00",
                    "end": "23:59",
                    "calendar_event": "Booked",
                }
            ]
        },
    )
    roster = [team for team in teams if team["club"] in ("Nordby", "Sorby")]
    collision = _tournament("COL", "2026-10-10", "Ekstern", roster)
    plan = _base_plan(
        [collision],
        _obligation(age_group="U10", day="2026-10-10", host="Sorby", roster=roster),
        start="2026-10-10",
        end="2026-10-10",
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)
    return root


def _coverage(root: Path) -> dict:
    _schedule, _decisions, plan, problem = load_context(YEAR, root=root)
    result = enumerate_unplaced_placement_repairs(
        plan, problem, finding_ids=[OBLIGATION_ID], allow_search=True
    )
    return result["coverage"][OBLIGATION_ID]


def _finding(root: Path) -> dict:
    report = list_findings(YEAR, root=root)
    return next(
        entry for entry in report["findings"] if entry["finding_id"] == OBLIGATION_ID
    )


def _rewrite_problem(root: Path, mutate) -> None:
    schedule_path = root / YEAR / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    mutate(schedule["verification_context"]["problem"])
    schedule_path.write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_full_scope_exhaustion_without_proof_stays_actionable(tmp_path: Path) -> None:
    root = _infeasible_fixture(tmp_path)

    coverage = _coverage(root)
    assert coverage["status"] == SEARCH_BOUNDED_EXHAUSTED
    assert coverage["untried"] == []
    assert coverage["proven_infeasible"] is False
    assert search_scope_complete(coverage) is True

    finding = _finding(root)
    assert finding["search_coverage"]["proven_infeasible"] is False
    assert finding["resolution"]["status"] == "actionable_unresolved_violation"
    assert finding["resolution"]["blocking"] is True


def test_recorded_proof_resolves_obligation_but_keeps_it_visible(tmp_path: Path) -> None:
    root = _infeasible_fixture(tmp_path)

    result = record_placement_infeasibility_proofs(season=YEAR, root=root)
    assert result["recorded_count"] == 1
    assert result["skipped_count"] == 0
    proof = result["recorded"][0]
    assert proof["obligation_id"] == OBLIGATION_ID
    assert proof["proven_infeasible"] is True
    assert proof["coverage_status"] == "proven_infeasible"
    assert proof["candidate_count"] >= 1
    assert proof["capacity_fingerprint"]
    assert proof["search_capability"]["fingerprint"]

    finding = _finding(root)
    assert finding["search_coverage"]["status"] == "proven_infeasible"
    assert finding["search_coverage"]["proven_infeasible"] is True
    assert finding["search_coverage"]["candidate_count"] == proof["candidate_count"]
    assert finding["resolution"]["status"] == "proven_infeasible_with_current_capacity"
    assert finding["resolution"]["blocking"] is False
    # The raw obligation is never removed.
    _schedule, _decisions, plan, _problem = load_context(YEAR, root=root)
    assert [entry["id"] for entry in plan["unresolved_tournament_placements"]] == [
        OBLIGATION_ID
    ]


def test_audit_does_not_count_proven_obligation_as_mandatory_blocker(tmp_path: Path) -> None:
    root = _infeasible_fixture(tmp_path)
    record_placement_infeasibility_proofs(season=YEAR, root=root)

    report = season_audit(YEAR, root=root)
    assert "tournament_placement_obligation" not in report["audit"]["mandatory_finding_checks"]
    assert not any(
        "tournament_placement_obligation" in reason
        and "unresolved mandatory" in reason
        for reason in report["audit"]["reasons"]
    )
    check = next(
        entry
        for entry in report["audit"]["checks"]
        if entry["rule_id"] == "tournament_placement_obligation"
    )
    assert check["resolved_statuses"] == ["proven_infeasible_with_current_capacity"]


def test_capacity_change_invalidates_proof_and_reopens_obligation(tmp_path: Path) -> None:
    root = _infeasible_fixture(tmp_path)
    record_placement_infeasibility_proofs(season=YEAR, root=root)
    assert _finding(root)["resolution"]["blocking"] is False

    # Authoritative host calendar changes: a previously busy interval opens.
    _rewrite_problem(
        root,
        lambda problem: problem.pop("club_busy_intervals", None),
    )

    finding = _finding(root)
    coverage = finding["search_coverage"]
    assert coverage["proven_infeasible"] is False
    assert coverage["capability_stale"] is True
    assert coverage["stale_reason"] == "authoritative_capacity_changed"
    assert finding["resolution"]["blocking"] is True


def test_capability_change_invalidates_proof_and_reopens_obligation(tmp_path: Path) -> None:
    root = _infeasible_fixture(tmp_path)
    _schedule, decisions, plan, problem = load_context(YEAR, root=root)
    coverage = _coverage(root)
    proof = build_infeasibility_proof(
        obligation=plan["unresolved_tournament_placements"][0],
        coverage=coverage,
        rejected_candidates=[
            {"finding_id": OBLIGATION_ID, "reason": "no_verified_materialization"}
        ],
        capability=SearchCapability("unplaced_placement", "an-old-version", {}),
        plan=plan,
        problem=problem,
        canonical_revision="revision-under-test",
        actor="tester",
        recorded_at="2026-10-01T00:00:00+00:00",
    )
    assert proof is not None
    decisions = copy.deepcopy(decisions)
    decisions["placement_infeasibility_proofs"] = [proof]
    schedule_path = root / YEAR / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    schedule["plan"] = plan
    schedule_path.write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    decisions_path = root / YEAR / "decisions.json"
    decisions_path.write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    finding = _finding(root)
    assert finding["search_coverage"]["capability_stale"] is True
    assert finding["search_coverage"]["stale_reason"] == "search_capability_changed"
    assert finding["resolution"]["blocking"] is True


def test_release_proof_reopens_obligation(tmp_path: Path) -> None:
    root = _infeasible_fixture(tmp_path)
    record_placement_infeasibility_proofs(season=YEAR, root=root)
    assert _finding(root)["resolution"]["blocking"] is False

    release_placement_infeasibility_proofs(season=YEAR, root=root)

    report = placement_infeasibility_report(season=YEAR, root=root)
    assert report["active_proof_count"] == 0
    assert report["unproven_obligation_ids"] == [OBLIGATION_ID]
    finding = _finding(root)
    assert finding["resolution"]["blocking"] is True
    assert finding["search_coverage"]["proven_infeasible"] is False


def test_proof_is_scoped_to_the_obligation_and_current_capacity(tmp_path: Path) -> None:
    root = _infeasible_fixture(tmp_path)
    coverage = _coverage(root)
    _schedule, _decisions, plan, problem = load_context(YEAR, root=root)
    obligation = plan["unresolved_tournament_placements"][0]
    capability = unplaced_placement_search_capability()
    proof = build_infeasibility_proof(
        obligation=obligation,
        coverage=coverage,
        rejected_candidates=[],
        capability=capability,
        plan=plan,
        problem=problem,
        canonical_revision="rev",
        actor="tester",
        recorded_at="2026-10-01T00:00:00+00:00",
    )
    assert proof is not None

    current, _reason = proof_is_current(
        proof, plan=plan, problem=problem, obligation=obligation, current_capability=capability
    )
    assert current is True

    # A coverage payload that only claims exhaustion (no proof) must never be
    # treated as an infeasibility proof.
    incomplete = {"status": SEARCH_INCOMPLETE, "search_requested": False, "untried": ["host"]}
    assert build_infeasibility_proof(
        obligation=obligation,
        coverage=incomplete,
        rejected_candidates=[],
        capability=capability,
        plan=plan,
        problem=problem,
        canonical_revision="rev",
        actor="tester",
        recorded_at="2026-10-01T00:00:00+00:00",
    ) is None

    # The attached coverage carries the explicit proven-infeasible verdict.
    attached = coverage_from_proof(proof)
    assert attached["status"] == "proven_infeasible"
    assert attached["proven_infeasible"] is True
    assert proofs_by_obligation({"placement_infeasibility_proofs": [proof]})[
        OBLIGATION_ID
    ]["id"] == OBLIGATION_ID


def test_recording_and_releasing_proofs_are_decision_only_for_reconciliation() -> None:
    """A proof write must not replay as a schedule mutation on a sealed season."""

    from tournament_scheduler.published_mutation_history import replay_recorded_mutations

    seed = {
        "t1": {
            "id": "t1",
            "date": "2026-09-12",
            "start_time": "10:00",
            "duration_minutes": 120,
            "end_time": "12:00",
            "arena": "Arena A",
            "host_club": "A",
            "age_group": "U10",
            "participants": [],
        }
    }
    history = [
        {
            "event": "record_placement_infeasibility",
            "tournament_id": "",
            "details": {"obligation_ids": [OBLIGATION_ID]},
        },
        {
            "event": "release_placement_infeasibility",
            "tournament_id": "",
            "details": {"obligation_ids": [OBLIGATION_ID]},
        },
    ]

    projection, applied = replay_recorded_mutations(seed, history)

    assert projection["t1"]["start_time"] == "10:00"
    assert applied == []
