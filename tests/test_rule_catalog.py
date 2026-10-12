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

from tournament_scheduler.rules import catalog, catalog_doc

REPO_ROOT = Path(__file__).resolve().parent.parent

# These modules emit the public hard-verifier / canonical-lock violation codes
# that ``verify_candidate``/``verify_final_candidate`` return. The scan is an
# AST scan, not a text grep, so a renamed code or a hidden/deferred emission is
# still detected.
_VIOLATION_EMITTERS = (
    "tournament_scheduler/planning/contract.py",
    "tournament_scheduler/planning/verification.py",
)
_CODE_DICT_EMITTERS = ("tournament_scheduler/season/baseline_overlay.py",)

# The season findings projector emits the actionable finding codes a
# controller/user sees. A finding that re-surfaces a raw verifier violation
# carries that verifier code instead, so both vocabularies are accepted.
_SEASON_FINDING_EMITTER = "tournament_scheduler/season/findings.py"


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
    assert "hosting.coverage" in coverage.canonical_owner

    proportional = catalog.CATALOG_BY_ID["hosting_proportional_balance"]
    assert proportional.classification == catalog.SOFT_OBJECTIVE
    assert "hosting.coverage" in proportional.canonical_owner
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
    assert "hosting.responsibility" in responsibility.canonical_owner
    assert "hosting_age_group_coverage" in responsibility.depends_on


def test_hosting_precedence_chain_places_representation_first() -> None:
    assert catalog.outranks("host_representation", "hosting_age_group_coverage")
    assert catalog.outranks("host_representation", "hosting_proportional_balance")


def test_hosting_coverage_owner_computes_a_coverage_floor() -> None:
    # The catalog claims ``hosting_coverage`` owns the coverage-floor target
    # math; prove the named facade exists and actually floors small clubs to 1
    # when coverage is arithmetically possible (Jar 4 teams / Kongsberg 1 team,
    # 2 U8 tournaments -> both must be targeted for >=1, never 2/0).
    from tournament_scheduler.hosting.coverage import hosting_targets_with_coverage_floor

    targets, unmet = hosting_targets_with_coverage_floor({"Jar": 4, "Kongsberg": 1}, 2)
    assert targets == {"Jar": 1, "Kongsberg": 1}
    assert unmet == []


def test_hosting_coverage_owner_surfaces_structural_shortfall() -> None:
    from tournament_scheduler.hosting.coverage import hosting_targets_with_coverage_floor

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
    from tournament_scheduler.scoring.quality_objectives import QUALITY_METRIC_PATHS

    missing = [
        path for path, _direction in QUALITY_METRIC_PATHS if catalog.rule_id_for_score_path(path) is None
    ]
    assert missing == [], "quality metric paths without an objective ID: " + ", ".join(missing)


def test_compare_quality_scores_carries_objective_ids() -> None:
    from tournament_scheduler.scoring.quality_objectives import compare_quality_scores

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
    assert target.read_text(encoding="utf-8") == catalog_doc.render_markdown(), (
        "docs/architecture/rule-catalog.md is stale; run scripts/render-rule-catalog.py"
    )


def test_rules_model_entries_reuse_catalog_identity() -> None:
    from tournament_scheduler.models import SeasonPlan
    from tournament_scheduler.rules.model import build_rules_model

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
    from tournament_scheduler.hosting.responsibility import RESPONSIBILITY_TRANSFER_CODE

    entry = catalog.CATALOG_BY_ID["hosting_responsibility"]
    assert RESPONSIBILITY_TRANSFER_CODE in entry.finding_codes


def test_request_constraint_codes_match_owner() -> None:
    from tournament_scheduler.season import request_constraints as request_constraints

    expected = {
        "request_team_unavailable": request_constraints.TEAM_UNAVAILABLE,
        "request_minimum_gap": request_constraints.MINIMUM_GAP,
        "request_opponent_avoidance": request_constraints.OPPONENT_AVOIDANCE,
    }
    for rule_id, code in expected.items():
        entry = catalog.CATALOG_BY_ID[rule_id]
        assert code in entry.verifier_codes, f"{rule_id} must register {code!r}"


def test_search_coverage_finding_codes_match_owner() -> None:
    from tournament_scheduler.repair import capability as search_capability

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
    rendered = catalog_doc.render_markdown()
    for entry in catalog.RULE_CATALOG:
        if entry.status == catalog.STATUS_ACTIVE:
            assert f"`{entry.id}`" in rendered


def test_guest_integrity_verifier_code_matches_owner() -> None:
    from tournament_scheduler.shape.guest_slots import GUEST_RESERVATION_INTEGRITY

    entry = catalog.CATALOG_BY_ID["guest_reservation_integrity"]
    assert GUEST_RESERVATION_INTEGRITY in entry.verifier_codes


# ---------------------------------------------------------------------------
# Adoption regression identity and priority tiers
# ---------------------------------------------------------------------------

# Regression codes deliberately outside the scheduling catalog: adoption
# invariants (no new hard violation, no revisited state), not one rule's semantic.
_UNCATALOGUED_ADOPTION_REGRESSIONS = frozenset(
    {"hard_verification_regression", "cycle_detected"}
)



def _adoption_regression_codes() -> set[str]:
    from tournament_scheduler.repair import adoption_guard as guard

    return {
        value
        for name, value in vars(guard).items()
        if name.startswith("REGRESSION_") and isinstance(value, str)
    }


def test_every_adoption_regression_code_is_catalogued_or_explicitly_uncatalogued() -> None:
    codes = _adoption_regression_codes()
    registered = {code for entry in catalog.RULE_CATALOG for code in entry.regression_codes}
    unregistered = sorted(codes - registered - _UNCATALOGUED_ADOPTION_REGRESSIONS)
    assert unregistered == [], (
        "adoption regression codes without a catalog rule: " + ", ".join(unregistered)
    )
    stale = sorted(_UNCATALOGUED_ADOPTION_REGRESSIONS - codes)
    assert stale == [], "uncatalogued regression codes no longer emitted: " + ", ".join(stale)


def test_quality_materiality_codes_resolve_through_the_catalog() -> None:
    from tournament_scheduler.repair.adoption_guard import _QUALITY_MATERIALITY

    missing = sorted(
        code
        for code, material in _QUALITY_MATERIALITY.values()
        if material and catalog.rule_id_for_regression_code(code) is None
    )
    assert missing == [], "material codes without a catalog rule: " + ", ".join(missing)


def test_every_quality_materiality_code_keeps_its_adoption_tier() -> None:
    # Characterization over the whole materiality table, including diagnostic
    # codes that were never tiered. Any change here changes which regression an
    # adoption may auto-waive, so it must be deliberate.
    from tournament_scheduler.repair import adoption_guard as guard
    from tournament_scheduler.repair.adoption_guard import _QUALITY_MATERIALITY

    expected = {
        "more_gaps_under_7_days": catalog.TIER_SOFT,
        "more_gaps_under_14_days": catalog.TIER_SOFT,
        "temporal_coverage_materially_worse": catalog.TIER_SOFT,
        "temporal_offenders_worse": catalog.TIER_SOFT,
        "participation_target_deviation_worse": catalog.TIER_STRONG_GOAL,
        "club_pool_shortfall_worse": catalog.TIER_STRONG_GOAL,
        "avoidable_participation_worse": catalog.TIER_STRONG_GOAL,
        "club_pair_repetition_worse": catalog.TIER_SOFT,
        "more_concentrated_club_exposure": catalog.TIER_SOFT,
        "same_club_clustering_worse": catalog.TIER_SOFT,
        "fewer_unique_opponent_clubs": catalog.TIER_SOFT,
        "inter_club_coverage_worse": catalog.TIER_SOFT,
        "hosting_balance_worse": catalog.TIER_STRONG_GOAL,
        "unresolved_hosting_obligation_worse": catalog.TIER_OPERATIONAL_OBLIGATION,
        "home_representation_worse": catalog.TIER_STRONG_GOAL,
        # Diagnostic-only: reported, never material, default soft tier.
        "fewer_unique_opponents": catalog.TIER_SOFT,
        "exact_squad_repeat_worse": catalog.TIER_SOFT,
        "intra_club_label_spread_worse": catalog.TIER_SOFT,
    }
    codes = {code for code, _material in _QUALITY_MATERIALITY.values()}
    assert codes == expected.keys()
    for code, tier in expected.items():
        assert guard.regression_tier(code) == tier, code


def test_quality_materiality_path_and_code_share_one_owning_rule() -> None:
    from tournament_scheduler.repair.adoption_guard import _QUALITY_MATERIALITY

    mismatches = []
    for path, (code, _material) in _QUALITY_MATERIALITY.items():
        path_rule = catalog.rule_id_for_score_path(path)
        code_rule = catalog.rule_id_for_regression_code(code)
        if path_rule is None or code_rule is None:
            continue  # reported by the resolution test above
        if path_rule != code_rule:
            mismatches.append(f"{path} -> {path_rule} but {code} -> {code_rule}")
    assert mismatches == [], "score path and regression code disagree: " + "; ".join(mismatches)


def test_per_squad_and_global_club_diversity_keep_separate_owners() -> None:
    from tournament_scheduler.repair import adoption_guard as guard

    assert catalog.rule_id_for_score_path("opponent_diversity.min_distinct_opponent_clubs") == (
        "opponent_club_diversity"
    )
    assert catalog.rule_id_for_regression_code(guard.REGRESSION_UNIQUE_OPPONENTS) == (
        "opponent_club_diversity"
    )
    assert catalog.rule_id_for_score_path("opponent_diversity.inter_club_diversity") == (
        "inter_club_diversity"
    )
    assert catalog.rule_id_for_regression_code(guard.REGRESSION_INTER_CLUB_COVERAGE) == (
        "inter_club_diversity"
    )


def test_regression_tiers_match_the_adoption_precedence() -> None:
    # Characterization of the tier model adoption comparison has always used.
    # Moving ownership into the catalog must not change which regression is
    # auto-waivable.
    from tournament_scheduler.repair import adoption_guard as guard

    expected = {
        guard.REGRESSION_HARD_VERIFICATION: catalog.TIER_HARD,
        guard.REGRESSION_CYCLE: catalog.TIER_HARD,
        guard.REGRESSION_HOSTING_OBLIGATION: catalog.TIER_OPERATIONAL_OBLIGATION,
        guard.REGRESSION_PARTICIPATION_DEVIATION: catalog.TIER_STRONG_GOAL,
        guard.REGRESSION_PARTICIPATION_SHORTFALL: catalog.TIER_STRONG_GOAL,
        guard.REGRESSION_PARTICIPATION_AVOIDABLE: catalog.TIER_STRONG_GOAL,
        guard.REGRESSION_HOSTING_BALANCE: catalog.TIER_STRONG_GOAL,
        guard.REGRESSION_HOME_REPRESENTATION: catalog.TIER_STRONG_GOAL,
        guard.REGRESSION_MORE_GAPS_UNDER_7: catalog.TIER_SOFT,
        guard.REGRESSION_MORE_GAPS_UNDER_14: catalog.TIER_SOFT,
        guard.REGRESSION_TEMPORAL_COVERAGE: catalog.TIER_SOFT,
        guard.REGRESSION_TEMPORAL_OFFENDERS: catalog.TIER_SOFT,
        guard.REGRESSION_CLUB_EXPOSURE: catalog.TIER_SOFT,
        guard.REGRESSION_CLUB_REPETITION: catalog.TIER_SOFT,
        guard.REGRESSION_SAME_CLUB_CLUSTERING: catalog.TIER_SOFT,
        guard.REGRESSION_UNIQUE_OPPONENTS: catalog.TIER_SOFT,
        guard.REGRESSION_INTER_CLUB_COVERAGE: catalog.TIER_SOFT,
        guard.REGRESSION_TRAVEL: catalog.TIER_SOFT,
    }
    assert expected.keys() == _adoption_regression_codes()
    for code, tier in expected.items():
        assert guard.regression_tier(code) == tier, code


def test_impact_priority_tiers_agree_with_regression_tiers() -> None:
    from tournament_scheduler.repair import adoption_guard as guard
    from tournament_scheduler.season.impact_analysis import _priority_improvement_tier

    def improved(prefix: str) -> dict[str, int]:
        return {f"{prefix}_before": 1, f"{prefix}_after": 0}

    assert _priority_improvement_tier(
        {"hard_violations_before": 1, "hard_violations_after": 0}
    ) == guard.regression_tier(guard.REGRESSION_HARD_VERIFICATION)
    assert _priority_improvement_tier(
        {"unresolved_hosting_obligations_before": 1, "unresolved_hosting_obligations_after": 0}
    ) == guard.regression_tier(guard.REGRESSION_HOSTING_OBLIGATION)
    assert _priority_improvement_tier(
        {"participation_deviations_before": 1, "participation_deviations_after": 0}
    ) == guard.regression_tier(guard.REGRESSION_PARTICIPATION_DEVIATION)
    assert _priority_improvement_tier(improved("hosting_balance_imbalances")) == guard.regression_tier(
        guard.REGRESSION_HOSTING_BALANCE
    )


def test_priority_tiers_are_derived_from_classification_by_default() -> None:
    hard = catalog.CATALOG_BY_ID["team_age_group_exact"]
    obligation = catalog.CATALOG_BY_ID["hosting_age_group_coverage"]
    soft = catalog.CATALOG_BY_ID["travel_distance"]
    assert catalog.priority_tier_for_rule(hard.id) == catalog.TIER_HARD
    assert catalog.priority_tier_for_rule(obligation.id) == catalog.TIER_OPERATIONAL_OBLIGATION
    assert catalog.priority_tier_for_rule(soft.id) == catalog.TIER_SOFT
    assert catalog.priority_tier_for_rule("not_a_rule") == catalog.TIER_SOFT


def test_validation_rejects_a_regression_code_with_two_owners(monkeypatch) -> None:
    import dataclasses

    owner = catalog.CATALOG_BY_ID["travel_distance"]
    other = catalog.CATALOG_BY_ID["temporal_spacing"]
    duplicate = dataclasses.replace(other, regression_codes=owner.regression_codes)
    entries = tuple(duplicate if entry.id == other.id else entry for entry in catalog.RULE_CATALOG)
    monkeypatch.setattr(catalog, "RULE_CATALOG", entries)
    problems = catalog.validate_catalog(REPO_ROOT)
    assert any("regression code 'travel_materially_worse'" in problem for problem in problems)


def test_validation_rejects_a_strong_goal_on_a_hard_constraint(monkeypatch) -> None:
    import dataclasses

    hard = catalog.CATALOG_BY_ID["team_age_group_exact"]
    promoted = dataclasses.replace(hard, priority_tier=catalog.TIER_STRONG_GOAL)
    entries = tuple(promoted if entry.id == hard.id else entry for entry in catalog.RULE_CATALOG)
    monkeypatch.setattr(catalog, "RULE_CATALOG", entries)
    problems = catalog.validate_catalog(REPO_ROOT)
    assert any(problem.startswith(f"{hard.id}: priority tier") for problem in problems)


def test_convergence_direction_resolves_through_the_catalog() -> None:
    from tournament_scheduler.application import pareto_convergence

    assert catalog.convergence_direction_for_finding_code("unplaced_tournament_placement") == (
        "unplaced_placement"
    )
    assert catalog.convergence_direction_for_finding_code("unplaced_placement") is None
    finding = {"code": "unplaced_tournament_placement", "category": "placement", "finding_id": "f"}
    assert pareto_convergence.classify_finding(finding).direction == "unplaced_placement"
    assert not hasattr(pareto_convergence, "DIRECTION_BY_CODE")


def test_validation_rejects_a_convergence_direction_for_a_foreign_finding_code(monkeypatch) -> None:
    import dataclasses

    owner = catalog.CATALOG_BY_ID["tournament_placement_obligation"]
    broken = dataclasses.replace(
        owner, convergence_directions=(("hosting_age_group_coverage_missing", "placement"),)
    )
    entries = tuple(broken if entry.id == owner.id else entry for entry in catalog.RULE_CATALOG)
    monkeypatch.setattr(catalog, "RULE_CATALOG", entries)
    problems = catalog.validate_catalog(REPO_ROOT)
    assert any("convergence direction for 'hosting_age_group_coverage_missing'" in p for p in problems)


def test_validation_rejects_duplicate_convergence_directions_within_one_entry(monkeypatch) -> None:
    import dataclasses

    owner = catalog.CATALOG_BY_ID["tournament_placement_obligation"]
    code = "unplaced_tournament_placement"
    broken = dataclasses.replace(
        owner, convergence_directions=((code, "unplaced_placement"), (code, "other_direction"))
    )
    entries = tuple(broken if entry.id == owner.id else entry for entry in catalog.RULE_CATALOG)
    monkeypatch.setattr(catalog, "RULE_CATALOG", entries)
    problems = catalog.validate_catalog(REPO_ROOT)
    assert any(f"convergence direction for {code!r} declared more than once" in p for p in problems)


def test_validation_rejects_a_duplicate_evidence_category(monkeypatch) -> None:
    import dataclasses

    original = catalog.EVIDENCE_CATEGORIES
    duplicate = dataclasses.replace(original[0], description="a second declaration")
    monkeypatch.setattr(catalog, "EVIDENCE_CATEGORIES", (*original, duplicate))
    problems = catalog.validate_catalog(REPO_ROOT)
    assert f"evidence category {original[0].name!r} is declared more than once" in problems


def test_audit_evidence_reads_categories_from_the_catalog() -> None:
    from tournament_scheduler.pipeline import audit_evidence

    from tournament_scheduler.rules import evidence_categories

    assert audit_evidence.EVIDENCE_CATEGORIES is evidence_categories.EVIDENCE_CATEGORIES
    assert catalog.EVIDENCE_CATEGORIES is evidence_categories.EVIDENCE_CATEGORIES
    assert all(
        isinstance(category, evidence_categories.EvidenceCategory)
        for category in evidence_categories.EVIDENCE_CATEGORIES
    )


# Module-level literal tables outside the catalog that are keyed by, or hold,
# verifier/finding codes. Each entry names a different identity space (plan
# field, report dimension, category label) or a known consolidation target.
# A new entry must be justified here; a new secondary code table otherwise fails.
_SANCTIONED_CODE_TABLES: dict[tuple[str, str], str] = {
    (
        "tournament_scheduler/application/canonical_season/scoped_mutation.py",
        "_IGNORED_PLAN_FIELDS",
    ): "plan field names that happen to share spelling with finding codes",
    (
        "tournament_scheduler/application/pareto_convergence.py",
        "DIRECTION_BY_CATEGORY",
    ): "finding category labels, not finding codes",
    (
        "tournament_scheduler/application/pareto_convergence.py",
        "REVIEW_PRIORITY_DIMENSIONS",
    ): "review priority report counter names mapped to metric dimensions, not finding codes",
    (
        "tournament_scheduler/application/convergence_refinement.py",
        "_MATERIAL_METRIC_LABELS",
    ): "quality metric names mapped to human-readable labels, not finding codes",
    (
        "tournament_scheduler/repair/local_options.py",
        "REPAIR_PROVIDERS",
    ): "repair enumerator dispatch keyed by defect name; move into the catalog repair projection",
    (
        "tournament_scheduler/repair/evaluation.py",
        "MAINTENANCE_DEFECT_DIMENSIONS",
    ): "defect dimension names for repair evaluation",
    (
        "tournament_scheduler/season/history_summary.py",
        "_COUNTED_LIST_FIELDS",
    ): "verification report field names",
}


def _literal_table_code_hits(tree: ast.Module, code_strings: set[str]) -> dict[str, list[str]]:
    """Module-level dict/set/tuple/list literals holding a catalog code as a key, value, or nested element."""
    hits: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        if not isinstance(target, ast.Name):
            continue
        if not isinstance(node.value, (ast.Dict, ast.Set, ast.Tuple, ast.List)):
            continue
        found = sorted(
            {
                element.value
                for element in ast.walk(node.value)
                if isinstance(element, ast.Constant)
                and isinstance(element.value, str)
                and element.value in code_strings
            }
        )
        if found:
            hits[target.id] = found
    return hits


# The registry boundary: catalog.py and the audit evidence categories it owns may
# hold catalog codes; every other module must resolve through them.
_REGISTRY_MODULES = frozenset(
    {
        "tournament_scheduler/rules/catalog.py",
        "tournament_scheduler/rules/evidence_categories.py",
    }
)


def _code_table_hits() -> dict[tuple[str, str], list[str]]:
    code_strings = {
        code
        for entry in catalog.RULE_CATALOG
        for code in (*entry.verifier_codes, *entry.finding_codes)
    }
    hits: dict[tuple[str, str], list[str]] = {}
    for path in sorted((REPO_ROOT / "tournament_scheduler").rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in _REGISTRY_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for name, found in _literal_table_code_hits(tree, code_strings).items():
            hits[(relative, name)] = found
    return hits


def test_code_table_scan_catches_a_code_held_only_as_a_value() -> None:
    code = "unplaced_tournament_placement"
    tree = ast.parse(
        f'PLACEMENT_BY_NAME = {{"placement": "{code}"}}\n'
        f'NESTED = {{"placement": ("{code}",)}}\n'
        f'PLAIN = {{"placement": "not_a_code"}}\n'
    )

    hits = _literal_table_code_hits(tree, {code})

    assert hits == {"PLACEMENT_BY_NAME": [code], "NESTED": [code]}


def test_no_secondary_verifier_or_finding_code_table_outside_the_catalog() -> None:
    unsanctioned = sorted(set(_code_table_hits()) - set(_SANCTIONED_CODE_TABLES))
    assert unsanctioned == [], (
        "module-level tables keyed by catalog verifier/finding codes must resolve through "
        "tournament_scheduler/rules/catalog.py or be sanctioned here with a reason: "
        + ", ".join(f"{path}:{name}" for path, name in unsanctioned)
    )


def test_sanctioned_code_tables_are_not_stale() -> None:
    stale = sorted(set(_SANCTIONED_CODE_TABLES) - set(_code_table_hits()))
    assert stale == [], "sanctioned code tables no longer exist or no longer hold catalog codes: " + str(stale)


def test_render_check_uses_the_repository_root_by_default() -> None:
    assert catalog_doc.main(["--check"]) == 0
