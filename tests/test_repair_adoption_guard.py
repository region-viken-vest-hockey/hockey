"""Cross-rule repair-adoption guard and season-wide audit tests.

These exercise the planner-independent guard directly: normalized assignment
identity, cycle detection, material regression classification, explicit operator
overrides and the catalog-driven completion gate.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List

import pytest

from tournament_scheduler import repair_adoption_guard
from tournament_scheduler.planning_contract import build_planning_problem, verify_candidate
from tournament_scheduler.repair_adoption_guard import (
    REGRESSION_CYCLE,
    REGRESSION_HARD_VERIFICATION,
    REGRESSION_MORE_GAPS_UNDER_14,
    REGRESSION_MORE_GAPS_UNDER_7,
    REGRESSION_TEMPORAL_COVERAGE,
    REGRESSION_TRAVEL,
    TIER_HARD,
    TIER_OPERATIONAL_OBLIGATION,
    TIER_STRONG_GOAL,
    RepairPassLedger,
    apply_adoption_overrides,
    classify_quality_regressions,
    compare_finding_sets,
    evaluate_adoption,
    normalized_assignment_fingerprint,
    parse_adoption_overrides,
    season_wide_audit,
    select_blocking_regressions,
)
from tournament_scheduler.rule_catalog import CATALOG_BY_ID


def _teams(clubs: Iterable[str]) -> List[Dict[str, str]]:
    return [
        {"club": club, "label": f"{club} {index}", "age_group": "U10"}
        for club in clubs
        for index in (1, 2)
    ]


def _problem(teams: List[Dict[str, str]]) -> Dict[str, Any]:
    config: Dict[str, Any] = {
        "teams": teams,
        "age_groups": ["U10"],
        "parallel_games": {"U10": 2},
        "round_length_minutes": {"U10": 30},
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
    }
    problem = build_planning_problem(config, None, date(2026, 9, 1), date(2027, 4, 30))
    problem["clubs"] = {club: f"{club} Arena" for club in {team["club"] for team in teams}}
    return problem


def _tournament(tournament_id: str, day: str, host: str, teams: List[Dict[str, str]]) -> Dict[str, Any]:
    labels = [team["label"] for team in teams]
    games = [
        {"home": labels[index], "away": labels[(index + 1) % len(labels)], "parallel_slot": 0, "round_number": 1}
        for index in range(len(labels))
    ]
    return {
        "id": tournament_id,
        "date": day,
        "arena": f"{host} Arena",
        "age_group": "U10",
        "host_club": host,
        "teams": teams,
        "games": games,
        "start_time": "10:00",
    }


def _plan(tournaments: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": tournaments,
    }


def _wide_gap_season() -> tuple[Dict[str, Any], Dict[str, Any]]:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams)
    plan = _plan(
        [
            _tournament("T1", "2026-10-10", "Nordby", teams),
            _tournament("T2", "2027-01-16", "Sorby", teams),
        ]
    )
    assert verify_candidate(plan, problem)["ok"]
    return plan, problem


def _tightened_candidate() -> Dict[str, Any]:
    teams = _teams(["Nordby", "Sorby"])
    return _plan(
        [
            _tournament("T1", "2026-10-10", "Nordby", teams),
            _tournament("T2", "2026-10-14", "Sorby", teams),
        ]
    )


# ---------------------------------------------------------------------------
# Normalized assignment identity
# ---------------------------------------------------------------------------


def test_fingerprint_ignores_timestamps_history_and_order() -> None:
    teams = _teams(["Nordby", "Sorby"])
    plan = _plan([_tournament("T1", "2026-10-10", "Nordby", teams)])
    reordered = _plan([_tournament("T1", "2026-10-10", "Nordby", list(reversed(teams)))])
    reordered["generated_at"] = "2020-01-01T00:00:00Z"
    reordered["history"] = [{"event": "whatever"}]

    assert normalized_assignment_fingerprint(plan) == normalized_assignment_fingerprint(reordered)


def test_fingerprint_changes_when_assignment_changes() -> None:
    teams = _teams(["Nordby", "Sorby"])
    plan = _plan([_tournament("T1", "2026-10-10", "Nordby", teams)])
    moved = _plan([_tournament("T1", "2026-10-11", "Nordby", teams)])

    assert normalized_assignment_fingerprint(plan) != normalized_assignment_fingerprint(moved)


# ---------------------------------------------------------------------------
# Finding-set comparison
# ---------------------------------------------------------------------------


def test_compare_finding_sets_classifies_resolved_new_and_persisting() -> None:
    before = [
        {"finding_id": "a", "code": "hosting_balance_imbalance", "category": "hosting", "deficit": 2},
        {"finding_id": "b", "code": "temporal_clustering", "category": "temporal_clustering"},
    ]
    after = [
        {"finding_id": "b", "code": "temporal_clustering", "category": "temporal_clustering", "min_gap_days": 3},
        {"finding_id": "c", "code": "participation_deviation", "category": "participation"},
    ]

    delta = compare_finding_sets(before, after)

    assert [item["finding_id"] for item in delta["resolved"]] == ["a"]
    assert [item["finding_id"] for item in delta["new"]] == ["c"]
    assert [item["finding_id"] for item in delta["persisting"]] == ["b"]
    assert delta["resolved_count"] == 1
    assert delta["new_count"] == 1


# ---------------------------------------------------------------------------
# Material regression classification
# ---------------------------------------------------------------------------


def test_classify_quality_regressions_marks_back_to_back_increase_material() -> None:
    comparison = {
        "metrics": [
            {
                "metric": "turnaround.gaps_under_days.7",
                "direction": "lower",
                "old": 1,
                "new": 3,
                "delta": 2,
                "regressed": True,
            },
            {
                "metric": "opponent_diversity.max_pair_repeat",
                "direction": "lower",
                "old": 2,
                "new": 3,
                "delta": 1,
                "regressed": True,
            },
        ],
        "regressions": ["turnaround.gaps_under_days.7", "opponent_diversity.max_pair_repeat"],
    }

    records = classify_quality_regressions(comparison)
    material_codes = {record["code"] for record in records if record["material"]}
    warning_codes = {record["code"] for record in records if not record["material"]}

    assert REGRESSION_MORE_GAPS_UNDER_7 in material_codes
    # Exact squad-label repetition is diagnostic only.
    assert "exact_squad_repeat_worse" in warning_codes


# ---------------------------------------------------------------------------
# Adoption evaluation
# ---------------------------------------------------------------------------


def test_net_deterioration_from_spacing_is_not_automatically_adopted() -> None:
    before, problem = _wide_gap_season()
    candidate = _tightened_candidate()

    result = evaluate_adoption(before, candidate, problem=problem)

    codes = {item["code"] for item in result["material_regressions"]}
    assert REGRESSION_MORE_GAPS_UNDER_7 in codes
    assert REGRESSION_MORE_GAPS_UNDER_14 in codes
    assert result["adoptable"] is False
    assert result["unaccepted_regressions"]


def test_explicit_named_override_accepts_a_known_trade_off() -> None:
    before, problem = _wide_gap_season()
    candidate = _tightened_candidate()

    without = evaluate_adoption(before, candidate, problem=problem)
    accepted = evaluate_adoption(
        before,
        candidate,
        problem=problem,
        accept_regressions=[
            REGRESSION_MORE_GAPS_UNDER_7,
            REGRESSION_MORE_GAPS_UNDER_14,
            REGRESSION_TEMPORAL_COVERAGE,
        ],
        regression_reason="Operator accepted the tighter turnaround to book the only ice",
    )

    assert without["adoptable"] is False
    assert accepted["adoptable"] is True
    assert {item["code"] for item in accepted["accepted_regressions"]} == {
        REGRESSION_MORE_GAPS_UNDER_7,
        REGRESSION_MORE_GAPS_UNDER_14,
        REGRESSION_TEMPORAL_COVERAGE,
    }


def test_override_requires_a_reason_and_rejects_hard_codes() -> None:
    with pytest.raises(ValueError):
        parse_adoption_overrides([REGRESSION_MORE_GAPS_UNDER_7], reason="")
    with pytest.raises(ValueError):
        parse_adoption_overrides([REGRESSION_HARD_VERIFICATION], reason="because")


def test_unmatched_override_is_refused() -> None:
    overrides = parse_adoption_overrides([REGRESSION_TRAVEL], reason="accepted")
    result = apply_adoption_overrides(
        [{"code": REGRESSION_MORE_GAPS_UNDER_7, "material": True}],
        overrides,
    )

    assert result["acceptable"] is False
    assert result["unmatched_acceptances"]


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


def test_multi_step_cycle_is_detected_even_without_direct_reversal() -> None:
    baseline = _plan(
        [
            _tournament("T1", "2026-10-10", "Nordby", _teams(["Nordby", "Sorby"])),
            _tournament("T2", "2026-11-14", "Sorby", _teams(["Nordby", "Sorby"])),
        ]
    )
    baseline_fingerprint = normalized_assignment_fingerprint(baseline)
    ledger = RepairPassLedger(baseline_fingerprint=baseline_fingerprint)

    def variant(day: str) -> Dict[str, Any]:
        return _plan(
            [
                _tournament("T1", "2026-10-10", "Nordby", _teams(["Nordby", "Sorby"])),
                _tournament("T2", day, "Sorby", _teams(["Nordby", "Sorby"])),
            ]
        )

    step_b = variant("2026-11-20")
    step_c = variant("2026-12-01")
    ledger.record(state_fingerprint=normalized_assignment_fingerprint(step_b), option_id="opt-b")
    ledger.record(state_fingerprint=normalized_assignment_fingerprint(step_c), option_id="opt-c")

    # A -> B -> C -> A: returning to the baseline is a cycle even though C does
    # not reverse B.
    result = evaluate_adoption(step_c, baseline, ledger=ledger)

    assert result["cycle_detected"] is True
    assert result["candidate_fingerprint"] == baseline_fingerprint
    assert REGRESSION_CYCLE in {item["code"] for item in result["material_regressions"]}


def test_ledger_is_bounded_and_keeps_newest_records() -> None:
    ledger = RepairPassLedger(baseline_fingerprint="base", max_records=3)

    for index in range(5):
        ledger.record(state_fingerprint=f"fp-{index}", option_id=f"opt-{index}")

    assert len(ledger.records) == 3
    assert ledger.dropped_records == 2
    assert [record["state_fingerprint"] for record in ledger.records] == ["fp-2", "fp-3", "fp-4"]
    assert ledger.has_visited("base")
    assert ledger.has_visited("fp-4")
    assert not ledger.has_visited("fp-0")


def test_ledger_round_trips_through_dict() -> None:
    ledger = RepairPassLedger(baseline_revision="rev", baseline_fingerprint="base")
    ledger.record(state_fingerprint="fp", option_id="opt", trigger_rule="temporal_clustering")

    restored = RepairPassLedger.from_dict(ledger.to_dict())

    assert restored.baseline_fingerprint == "base"
    assert restored.records[0]["state_fingerprint"] == "fp"


# ---------------------------------------------------------------------------
# Catalog-driven season-wide audit
# ---------------------------------------------------------------------------


def _small_catalog():
    return [
        CATALOG_BY_ID["team_age_group_exact"],
        CATALOG_BY_ID["team_unique_per_date"],
    ]


def test_audit_passes_only_with_complete_catalog_evidence() -> None:
    plan, problem = _wide_gap_season()
    verification = verify_candidate(plan, problem)
    fingerprint = normalized_assignment_fingerprint(plan)

    report = season_wide_audit(
        plan=plan,
        findings=[],
        verification=verification,
        reconciliation={"ok": True},
        expected_fingerprint=fingerprint,
        catalog=_small_catalog(),
    )

    assert report["ok"] is True
    assert report["status"] == "PASS"
    assert report["incomplete_checks"] == []


def test_audit_reports_incomplete_when_verifier_evidence_is_missing() -> None:
    plan, problem = _wide_gap_season()
    verification = verify_candidate(plan, problem)

    report = season_wide_audit(
        plan=plan,
        findings=[],
        verification=verification,
        catalog=[CATALOG_BY_ID["canonical_locked_tournament_preserved"]],
    )

    assert report["ok"] is False
    assert report["status"] == "INCOMPLETE"
    assert report["incomplete_checks"] == ["canonical_locked_tournament_preserved"]


def test_audit_fails_on_hard_violation() -> None:
    plan, problem = _wide_gap_season()

    report = season_wide_audit(
        plan=plan,
        findings=[],
        verification={
            "ok": False,
            "violations": [{"code": "duplicate_participation_same_date"}],
        },
        catalog=[CATALOG_BY_ID["team_unique_per_date"]],
    )

    assert report["ok"] is False
    assert report["status"] == "FAIL"
    assert report["violation_checks"] == ["team_unique_per_date"]


def test_audit_does_not_attribute_another_rules_violation() -> None:
    plan, problem = _wide_gap_season()

    report = season_wide_audit(
        plan=plan,
        findings=[],
        verification={
            "ok": False,
            "violations": [{"code": "duplicate_participation_same_date"}],
        },
        catalog=[CATALOG_BY_ID["team_unique_per_date"], CATALOG_BY_ID["team_age_group_exact"]],
    )

    statuses = {check["rule_id"]: check["status"] for check in report["checks"]}
    assert statuses["team_unique_per_date"] == "violation"
    assert statuses["team_age_group_exact"] == "clear"
    assert report["ok"] is False


def test_audit_requires_reconciliation_and_current_fingerprint() -> None:
    plan, problem = _wide_gap_season()
    verification = verify_candidate(plan, problem)

    reconciliation_failed = season_wide_audit(
        plan=plan,
        findings=[],
        verification=verification,
        reconciliation={"ok": False},
        catalog=_small_catalog(),
    )
    stale = season_wide_audit(
        plan=plan,
        findings=[],
        verification=verification,
        reconciliation={"ok": True},
        expected_fingerprint="stale",
        catalog=_small_catalog(),
    )

    assert reconciliation_failed["ok"] is False
    assert stale["ok"] is False
    assert stale["revision_matches"] is False


def test_audit_requires_explicit_reconciliation_evidence() -> None:
    plan, problem = _wide_gap_season()
    verification = verify_candidate(plan, problem)

    missing = season_wide_audit(
        plan=plan,
        findings=[],
        verification=verification,
        reconciliation=None,
        catalog=_small_catalog(),
    )
    empty = season_wide_audit(
        plan=plan,
        findings=[],
        verification=verification,
        reconciliation={},
        catalog=_small_catalog(),
    )

    assert missing["ok"] is False
    assert missing["reconciliation_status"] == "missing"
    assert missing["reconciliation_ok"] is None
    assert empty["ok"] is False
    assert empty["reconciliation_status"] == "missing"


def test_audit_detects_revision_drift() -> None:
    plan, problem = _wide_gap_season()
    verification = verify_candidate(plan, problem)

    report = season_wide_audit(
        plan=plan,
        findings=[],
        verification=verification,
        reconciliation={"ok": True},
        expected_revision="revision-before",
        current_revision="revision-after",
        catalog=_small_catalog(),
    )

    assert report["ok"] is False
    assert report["revision_matches"] is False
    assert report["current_revision"] == "revision-after"


def test_higher_tier_fix_waives_only_soft_regressions() -> None:
    soft = {"code": REGRESSION_MORE_GAPS_UNDER_7, "material": True}
    strong = {"code": "hosting_balance_worse", "material": True}
    operational = {"code": "unresolved_hosting_obligation_worse", "material": True}
    hard = {"code": REGRESSION_HARD_VERIFICATION, "material": True}

    waived = select_blocking_regressions([soft, strong, operational, hard], TIER_HARD)
    waived_codes = {record["code"] for record in waived}

    # A hard-constraint improvement only auto-waives the soft regression; the
    # strong-goal, operational and hard regressions still require an explicit
    # acceptance.
    assert waived_codes == {"hosting_balance_worse", "unresolved_hosting_obligation_worse", REGRESSION_HARD_VERIFICATION}
    # Without any higher-tier improvement, even a soft regression blocks.
    assert len(select_blocking_regressions([soft], None)) == 1
    # A strong-goal improvement does not waive another strong-goal regression.
    assert select_blocking_regressions([strong], TIER_STRONG_GOAL) == [strong]
    assert select_blocking_regressions([soft], TIER_STRONG_GOAL) == []
    assert select_blocking_regressions([soft], TIER_OPERATIONAL_OBLIGATION) == []


def test_ledger_resets_visited_states_at_baseline_boundary() -> None:
    history = [
        {
            "event": "repair_option_applied",
            "details": {
                "adoption": {
                    "candidate_fingerprint": "first-pass-state",
                    "baseline_fingerprint": "first-pass-baseline",
                }
            },
        },
        {"event": "season_baseline_advance", "details": {}},
        {
            "event": "repair_option_applied",
            "details": {
                "adoption": {
                    "candidate_fingerprint": "second-pass-state",
                    "baseline_fingerprint": "second-pass-baseline",
                }
            },
        },
    ]

    ledger = RepairPassLedger.from_history(history)

    assert ledger.baseline_fingerprint == "second-pass-baseline"
    assert ledger.has_visited("second-pass-state")
    # The first pass's state is not part of the current pass, so a legitimate
    # return to it in a new pass is not a false cycle.
    assert not ledger.has_visited("first-pass-state")
    assert not ledger.has_visited("first-pass-baseline")


def test_audit_blocks_on_unresolved_mandatory_operational_finding() -> None:
    plan, problem = _wide_gap_season()
    verification = verify_candidate(plan, problem)

    report = season_wide_audit(
        plan=plan,
        findings=[
            {
                "finding_id": "unplaced_placement:U10:2026-10-10:1",
                "code": "unplaced_tournament_placement",
                "severity": "unresolved",
            }
        ],
        verification=verification,
        reconciliation={"ok": True},
        catalog=[CATALOG_BY_ID["tournament_placement_obligation"]],
    )

    assert report["status"] == "FAIL"
    assert report["ok"] is False
    assert report["mandatory_finding_checks"] == ["tournament_placement_obligation"]
    assert any("unresolved mandatory obligation" in reason for reason in report["reasons"])


def test_audit_does_not_count_an_accepted_obligation_as_blocking() -> None:
    plan, problem = _wide_gap_season()
    verification = verify_candidate(plan, problem)

    report = season_wide_audit(
        plan=plan,
        findings=[
            {
                "finding_id": "unplaced_placement:U10:2026-10-10:1",
                "code": "unplaced_tournament_placement",
                "rule_id": "tournament_placement_obligation",
                "severity": "unresolved",
                "accepted": True,
            }
        ],
        verification=verification,
        reconciliation={"ok": True},
        catalog=[CATALOG_BY_ID["tournament_placement_obligation"]],
    )

    assert report["status"] == "PASS"
    assert report["accepted_exceptions"] == ["tournament_placement_obligation"]


def test_travel_measurement_unavailable_blocks_adoption(monkeypatch) -> None:
    before, problem = _wide_gap_season()
    candidate = _tightened_candidate()
    monkeypatch.setattr(
        repair_adoption_guard,
        "compute_travel",
        lambda plan: {"total_travel_km": 0.0, "max_team_travel_km": 0.0, "available": False},
    )

    result = evaluate_adoption(before, candidate, problem=problem)

    assert result["adoptable"] is False
    assert result["measurement_incomplete"] == ["travel"]
