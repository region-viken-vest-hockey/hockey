"""Conformance guards for the canonical scheduling rule catalog.

These are cheap, deterministic guards -- not a rule engine. They keep the
catalog from drifting away from the code it describes:

* every public hard-verifier violation code has exactly one registered rule ID;
* duplicate catalog IDs and invalid precedence references fail;
* active entries point at importable owner modules and existing tests;
* the generated agent-facing ownership table is current;
* the hosting coverage vs. proportional balance precedence is explicit.

The catalog itself is metadata only; the deterministic implementations it
names stay the executable truth.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tournament_scheduler import rule_catalog as catalog

REPO_ROOT = Path(__file__).resolve().parent.parent

# These modules emit the public hard-verifier / canonical-lock violation codes
# that ``verify_candidate``/``verify_final_candidate`` return. The scan is an
# AST scan, not a text grep, so a renamed code or a hidden/deferred emission is
# still detected.
_VIOLATION_EMITTERS = (
    "tournament_scheduler/planning_contract.py",
    "tournament_scheduler/final_verification.py",
)
_CODE_DICT_EMITTERS = ("tournament_scheduler/canonical_baseline.py",)

# The season findings projector emits the actionable finding codes a
# controller/user sees. A finding that re-surfaces a raw verifier violation
# carries that verifier code instead, so both vocabularies are accepted.
_SEASON_FINDING_EMITTER = "tournament_scheduler/season_maintenance.py"


def _scanned_verifier_codes() -> set[str]:
    codes: set[str] = set()
    for relative in _VIOLATION_EMITTERS:
        tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "_violate" and node.args:
                candidate = node.args[0]
            elif name == "_add" and len(node.args) >= 2:
                candidate = node.args[1]
            else:
                continue
            if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                codes.add(candidate.value)

    for relative in _CODE_DICT_EMITTERS:
        tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "code"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    codes.add(value.value)
    return codes


def _literal_strings(node: ast.AST) -> set[str]:
    """String constants a ``"code"`` value can take, without the branch test."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _literal_strings(node.body) | _literal_strings(node.orelse)
    return set()


def _scanned_season_finding_codes() -> set[str]:
    tree = ast.parse(
        (REPO_ROOT / _SEASON_FINDING_EMITTER).read_text(encoding="utf-8"),
        filename=_SEASON_FINDING_EMITTER,
    )
    codes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "code":
                codes |= _literal_strings(value)
    return codes


def test_catalog_validates_with_no_problems() -> None:
    assert catalog.validate_catalog(REPO_ROOT) == []


def test_catalog_ids_are_unique_and_stable_shaped() -> None:
    ids = [entry.id for entry in catalog.RULE_CATALOG]
    assert len(ids) == len(set(ids))
    for rule_id in ids:
        assert rule_id == rule_id.lower()
        assert rule_id.replace("_", "").isalnum()
        assert " " not in rule_id
        # No production club/age/date/season names may become a rule identity.
        assert not any(char.isdigit() for char in rule_id)


def test_every_scanned_verifier_code_has_a_registered_rule_id() -> None:
    unregistered = sorted(
        code for code in _scanned_verifier_codes() if catalog.rule_id_for_verifier_code(code) is None
    )
    assert unregistered == [], (
        "public verifier codes without a catalog rule ID: " + ", ".join(unregistered)
    )


def test_scanned_verifier_code_set_is_not_trivially_empty() -> None:
    codes = _scanned_verifier_codes()
    # Sanity: the AST scan must actually find the known core codes.
    assert {"host_team_missing", "bye_team_not_allowed", "canonical_placement_locked"} <= codes


def test_annotate_violations_attaches_rule_ids() -> None:
    violations = [
        {"code": "host_team_missing", "message": "m"},
        {"code": "canonical_placement_locked", "message": "m"},
        {"code": "not_a_real_code", "message": "m"},
    ]
    catalog.annotate_violations(violations)
    assert violations[0]["rule_id"] == "host_representation"
    assert violations[1]["rule_id"] == "canonical_placement_preserved"
    assert "rule_id" not in violations[2]


def test_annotate_violations_does_not_overwrite_existing_rule_id() -> None:
    violations = [{"code": "host_team_missing", "rule_id": "explicit"}]
    catalog.annotate_violations(violations)
    assert violations[0]["rule_id"] == "explicit"


def test_annotate_findings_attaches_rule_ids() -> None:
    findings = [
        {"code": "unresolved_hosting_obligation"},
        {"code": "host_team_missing"},
        {"code": "not_a_real_code"},
    ]
    catalog.annotate_findings(findings)
    assert findings[0]["rule_id"] == "hosting_age_group_coverage"
    # A finding re-surfacing a raw verifier violation keeps the verifier identity.
    assert findings[1]["rule_id"] == "host_representation"
    assert "rule_id" not in findings[2]


def test_annotate_findings_does_not_overwrite_existing_rule_id() -> None:
    findings = [{"code": "unresolved_hosting_obligation", "rule_id": "explicit"}]
    catalog.annotate_findings(findings)
    assert findings[0]["rule_id"] == "explicit"


def test_hard_constraints_have_stable_ids_and_owners() -> None:
    hard = catalog.entries_by_classification(catalog.HARD_CONSTRAINT)
    assert hard, "expected registered hard constraints"
    for entry in hard:
        assert entry.canonical_owner
        assert entry.verifier_owner or entry.verifier_codes


def test_hosting_coverage_is_the_distinct_obligation_owner() -> None:
    coverage = catalog.CATALOG_BY_ID["hosting_age_group_coverage"]
    assert coverage.classification == catalog.OPERATIONAL_OBLIGATION
    assert "hosting_coverage" in coverage.canonical_owner

    proportional = catalog.CATALOG_BY_ID["hosting_proportional_balance"]
    assert proportional.classification == catalog.SOFT_OBJECTIVE
    assert "hosting_coverage" in proportional.canonical_owner
    # Proportional balance is subordinate to coverage and can never act as the
    # coverage rule.
    assert catalog.outranks("hosting_age_group_coverage", "hosting_proportional_balance")
    assert not catalog.outranks("hosting_proportional_balance", "hosting_age_group_coverage")
    assert "hosting_age_group_coverage" in proportional.depends_on
    assert "hosting_age_group_coverage" not in proportional.verifier_codes
    assert "hosting_age_group_coverage" not in proportional.finding_codes


def test_hosting_responsibility_is_a_distinct_cross_path_obligation() -> None:
    responsibility = catalog.CATALOG_BY_ID["hosting_responsibility"]
    assert responsibility.classification == catalog.OPERATIONAL_OBLIGATION
    assert "hosting_responsibility" in responsibility.canonical_owner
    assert "hosting_age_group_coverage" in responsibility.depends_on


def test_hosting_precedence_chain_places_representation_first() -> None:
    assert catalog.outranks("host_representation", "hosting_age_group_coverage")
    assert catalog.outranks("host_representation", "hosting_proportional_balance")


def test_hosting_coverage_owner_computes_a_coverage_floor() -> None:
    # The catalog claims ``hosting_coverage`` owns the coverage-floor target
    # math; prove the named facade exists and actually floors small clubs to 1
    # when coverage is arithmetically possible (Jar 4 teams / Kongsberg 1 team,
    # 2 U8 tournaments -> both must be targeted for >=1, never 2/0).
    from tournament_scheduler.hosting_coverage import hosting_targets_with_coverage_floor

    targets, unmet = hosting_targets_with_coverage_floor({"Jar": 4, "Kongsberg": 1}, 2)
    assert targets == {"Jar": 1, "Kongsberg": 1}
    assert unmet == []


def test_hosting_coverage_owner_surfaces_structural_shortfall() -> None:
    from tournament_scheduler.hosting_coverage import hosting_targets_with_coverage_floor

    # Fewer tournaments than clubs needing coverage is a structural shortfall:
    # surfaced in ``unmet`` rather than hidden as a soft imbalance.
    _, unmet = hosting_targets_with_coverage_floor({"A": 1, "B": 1, "C": 1}, 2)
    assert unmet


def test_participation_target_is_soft_and_hard_max_is_separate() -> None:
    target = catalog.CATALOG_BY_ID["participation_target"]
    hard_max = catalog.CATALOG_BY_ID["participation_hard_max"]
    assert target.classification == catalog.SOFT_OBJECTIVE
    assert hard_max.classification == catalog.HARD_CONSTRAINT
    assert target.id != hard_max.id


def test_no_verifier_code_is_registered_by_two_rules() -> None:
    owners: dict[str, str] = {}
    for entry in catalog.RULE_CATALOG:
        for code in entry.verifier_codes:
            assert code not in owners or owners[code] == entry.id, code
            owners[code] = entry.id


def test_every_season_finding_code_has_a_registered_rule_id() -> None:
    unregistered = sorted(
        code
        for code in _scanned_season_finding_codes()
        if catalog.rule_id_for_finding_code(code) is None
        and catalog.rule_id_for_verifier_code(code) is None
    )
    assert unregistered == [], (
        "season finding codes without a catalog rule ID: " + ", ".join(unregistered)
    )


def test_season_finding_code_set_is_not_trivially_empty() -> None:
    codes = _scanned_season_finding_codes()
    assert {"unresolved_hosting_obligation", "home_representation_skew"} <= codes


def test_no_finding_code_is_registered_by_two_rules() -> None:
    owners: dict[str, str] = {}
    for entry in catalog.RULE_CATALOG:
        for code in entry.finding_codes:
            assert code not in owners or owners[code] == entry.id, code
            owners[code] = entry.id


def test_every_quality_metric_path_has_an_objective_id() -> None:
    from tournament_scheduler.quality_objectives import QUALITY_METRIC_PATHS

    missing = [
        path for path, _direction in QUALITY_METRIC_PATHS if catalog.rule_id_for_score_path(path) is None
    ]
    assert missing == [], "quality metric paths without an objective ID: " + ", ".join(missing)


def test_compare_quality_scores_carries_objective_ids() -> None:
    from tournament_scheduler.quality_objectives import compare_quality_scores

    report = {
        "participation": {"spread": 1},
        "opponent_diversity": {"max_pair_repeat": 2},
        "hosting": {"spread": 1},
        "home_representation": {"max_material_spread": 1},
        "temporal": {"max_gap_days": 10, "offenders_count": 0},
        "turnaround": {"gaps_under_days": {7: 0, 14: 0}},
    }
    comparison = compare_quality_scores(dict(report), dict(report))
    by_metric = {metric["metric"]: metric for metric in comparison["metrics"]}
    assert by_metric["hosting.spread"]["objective_id"] == "hosting_proportional_balance"
    assert by_metric["participation.spread"]["objective_id"] == "participation_target"


def test_rule_semantics_projection_matches_catalog_entries() -> None:
    semantics = catalog.rule_semantics("hosting_age_group_coverage")
    entry = catalog.CATALOG_BY_ID["hosting_age_group_coverage"]
    assert semantics is not None
    assert semantics["id"] == entry.id
    assert semantics["classification"] == entry.classification
    assert semantics["classification_label"] == catalog.CLASSIFICATION_LABELS[entry.classification]
    assert semantics["meaning"] == entry.meaning
    assert semantics["canonical_owner"] == entry.canonical_owner
    assert semantics["precedes"] == list(entry.precedes)
    assert semantics["depends_on"] == list(entry.depends_on)


def test_rule_semantics_unknown_id_is_none() -> None:
    assert catalog.rule_semantics("not_a_rule") is None


def test_active_catalog_references_cover_exactly_the_active_entries() -> None:
    references = {reference["id"] for reference in catalog.active_catalog_references()}
    active = {entry.id for entry in catalog.RULE_CATALOG if entry.status == catalog.STATUS_ACTIVE}
    assert references == active


def test_rules_model_semantics_resolves_registered_entry_ids() -> None:
    semantics = catalog.rules_model_semantics("hosting_obligation_coverage")
    assert semantics is not None and semantics["id"] == "hosting_age_group_coverage"
    # An entry with no catalog registration resolves to no semantics rather
    # than a fabricated one.
    assert catalog.rules_model_semantics("christmas_half_boundary") is None


def test_precedence_references_are_registered_and_acyclic() -> None:
    for entry in catalog.RULE_CATALOG:
        for reference in (*entry.precedes, *entry.depends_on):
            assert reference in catalog.CATALOG_BY_ID, f"{entry.id} -> {reference}"
        for lower in entry.precedes:
            assert not catalog.outranks(lower, entry.id), f"cycle {entry.id} <-> {lower}"


def test_generated_ownership_table_is_current() -> None:
    target = REPO_ROOT / catalog.CATALOG_DOC_PATH
    assert target.exists(), "run scripts/render-rule-catalog.py"
    assert target.read_text(encoding="utf-8") == catalog.render_markdown(), (
        "docs/architecture/rule-catalog.md is stale; run scripts/render-rule-catalog.py"
    )


def test_rules_model_entries_reuse_catalog_identity() -> None:
    from tournament_scheduler.models import SeasonPlan
    from tournament_scheduler.rules_model import build_rules_model

    rules = build_rules_model(SeasonPlan())
    by_id = {rule["id"]: rule for rule in rules}
    assert by_id["hosting_obligation_coverage"]["catalog_id"] == "hosting_age_group_coverage"
    assert by_id["arena_day_collisions"]["catalog_id"] == "arena_interval_non_overlap"
    assert by_id["age_group_exact_match"]["catalog_id"] == "team_age_group_exact"
    assert by_id["participation_shortfalls"]["catalog_id"] == "participation_target"
    # Any attached identity must point at a real catalog entry.
    for rule in rules:
        catalog_id = rule.get("catalog_id")
        if catalog_id:
            assert catalog_id in catalog.CATALOG_BY_ID, rule["id"]


def test_hosting_responsibility_finding_code_matches_owner() -> None:
    from tournament_scheduler.hosting_responsibility import RESPONSIBILITY_TRANSFER_CODE

    entry = catalog.CATALOG_BY_ID["hosting_responsibility"]
    assert RESPONSIBILITY_TRANSFER_CODE in entry.finding_codes


def test_request_constraint_codes_match_owner() -> None:
    from tournament_scheduler import request_constraints

    expected = {
        "request_team_unavailable": request_constraints.TEAM_UNAVAILABLE,
        "request_minimum_gap": request_constraints.MINIMUM_GAP,
        "request_opponent_avoidance": request_constraints.OPPONENT_AVOIDANCE,
    }
    for rule_id, code in expected.items():
        entry = catalog.CATALOG_BY_ID[rule_id]
        assert code in entry.verifier_codes, f"{rule_id} must register {code!r}"


def test_search_coverage_finding_codes_match_owner() -> None:
    from tournament_scheduler import search_capability

    entry = catalog.CATALOG_BY_ID["search_coverage_evidence"]
    for code in (
        search_capability.COVERAGE_SEARCH_INCOMPLETE,
        search_capability.COVERAGE_BOUNDED_EXHAUSTED,
        search_capability.COVERAGE_PROVEN_INFEASIBLE,
    ):
        assert code in entry.finding_codes


def test_publication_readiness_reason_codes_resolve_to_rules() -> None:
    codes = [
        "unresolved_hosting",
        "hosting_balance_imbalances",
        "manual_calendar_placements",
        "external_calendar_conflicts",
        "movable_host_confirmation_required",
        "stale_approvals",
        "orphaned_approvals",
        "participation_shortfalls",
        "intra_club_participation_distribution",
        "participation_target_deviation",
        "operator_waivers",
        "incomplete_verification",
    ]
    missing = [code for code in codes if catalog.rule_id_for_finding_code(code) is None]
    assert missing == [], "publication reasons without a catalog rule: " + ", ".join(missing)


def test_ownership_table_lists_every_active_rule() -> None:
    rendered = catalog.render_markdown()
    for entry in catalog.RULE_CATALOG:
        if entry.status == catalog.STATUS_ACTIVE:
            assert f"`{entry.id}`" in rendered
