"""Bounded Pareto-convergence control plane (issue #384).

These tests pin the repository-owned convergence *mechanics*: bounded archive
retention, domination pruning, search_incomplete handling, plateau detection
and the truthful terminal vocabulary. They deliberately use synthetic
objective vectors -- no planner/optimizer -- because the controller must not
own scheduling rules.
"""

from __future__ import annotations

from tournament_scheduler.application.pareto_convergence import (
    PAUSE_BUDGET_EXHAUSTED,
    TERMINAL_BOUNDED_SEARCH_EXHAUSTED,
    TERMINAL_NONE,
    TERMINAL_OPERATOR_REQUIRED,
    TERMINAL_PARETO_STABLE,
    TERMINAL_PASS,
    ArchiveEntry,
    ConvergenceController,
    ConvergenceState,
    ParetoArchive,
    archive_entry_from_option,
    classify_finding,
    classify_findings,
    describe_pause,
    describe_terminal,
    rank_frontier_for_review,
    recommended_review_candidate_ref,
    recorded_exhaustion_is_stale,
)
from tournament_scheduler.search_capability import SearchCapability, capability_is_stale


def _entry(ref: str, vector: dict[str, float], fingerprint: str = "") -> ArchiveEntry:
    return ArchiveEntry(
        candidate_ref=ref,
        candidate_fingerprint=fingerprint or f"fp-{ref}",
        objective_vector=vector,
    )


# ---------------------------------------------------------------------------
# Bounded archive + domination pruning
# ---------------------------------------------------------------------------


def test_archive_rejects_dominated_and_prunes_entries_it_dominates() -> None:
    archive = ParetoArchive(max_size=8)
    assert archive.consider(_entry("a", {"x": 1.0, "y": 5.0}))["accepted"] is True
    assert archive.consider(_entry("b", {"x": 5.0, "y": 1.0}))["accepted"] is True
    # ``dominated`` is worse/equal in every dimension -> rejected, no mutation.
    dominated = archive.consider(_entry("c", {"x": 6.0, "y": 6.0}))
    assert dominated["accepted"] is False
    assert set(dominated["dominated_by"]) == {"a", "b"}
    assert archive.refs() == ["a", "b"]

    # A candidate that dominates ``a`` removes it but keeps the other tradeoff.
    outcome = archive.consider(_entry("d", {"x": 1.0, "y": 4.0}))
    assert outcome["accepted"] is True
    assert outcome["dominated_refs"] == ["a"]
    assert archive.refs() == ["b", "d"]


def test_archive_ignores_exact_duplicate_candidate_fingerprint() -> None:
    archive = ParetoArchive(max_size=4)
    archive.consider(_entry("a", {"x": 1.0}, fingerprint="fp-1"))
    duplicate = archive.consider(_entry("a-again", {"x": 0.0}, fingerprint="fp-1"))
    assert duplicate["accepted"] is False
    assert duplicate["duplicate"] is True
    assert archive.refs() == ["a"]


def test_archive_is_bounded_and_keeps_the_newest_candidate() -> None:
    archive = ParetoArchive(max_size=2)
    archive.consider(_entry("a", {"x": 1.0, "y": 9.0}))
    archive.consider(_entry("b", {"x": 5.0, "y": 5.0}))
    outcome = archive.consider(_entry("c", {"x": 9.0, "y": 1.0}))
    assert outcome["accepted"] is True
    assert len(archive.entries) == 2
    assert "c" in archive.refs()
    assert outcome["pruned_refs"]


def test_incomparable_vectors_are_never_silently_dominated() -> None:
    archive = ParetoArchive(max_size=4)
    archive.consider(_entry("a", {"x": 1.0}))
    outcome = archive.consider(_entry("b", {"different": 99.0}))
    assert outcome["accepted"] is True
    assert archive.refs() == ["a", "b"]


# ---------------------------------------------------------------------------
# Finding classification
# ---------------------------------------------------------------------------


def test_classify_finding_maps_categories_and_codes_to_directions() -> None:
    hosting = classify_finding({"finding_id": "h", "category": "hosting"})
    assert hosting.direction == "hosting"
    unplaced = classify_finding(
        {"finding_id": "u", "category": "manual_placement", "code": "unplaced_tournament_placement"}
    )
    assert unplaced.direction == "unplaced_placement"


def test_classify_finding_treats_operator_authority_and_acceptance_as_non_actionable() -> None:
    operator = classify_finding(
        {"finding_id": "p", "category": "participation", "requires_operator": True}
    )
    assert operator.operator_required is True
    assert operator.actionable is False
    accepted = classify_finding(
        {"finding_id": "a", "category": "participation", "accepted": True}
    )
    assert accepted.actionable is False
    bounded = classify_finding(
        {
            "finding_id": "b",
            "category": "hosting",
            "search_coverage": {"status": "bounded_search_exhausted"},
        }
    )
    assert bounded.bounded_exhausted is True
    assert bounded.actionable is False
    incomplete = classify_finding(
        {
            "finding_id": "i",
            "category": "hosting",
            "search_coverage": {"status": "search_incomplete"},
        }
    )
    assert incomplete.search_incomplete is True
    assert incomplete.actionable is True


# ---------------------------------------------------------------------------
# Exploration order
# ---------------------------------------------------------------------------


def test_next_direction_prefers_unexplored_then_incomplete_search() -> None:
    state = ConvergenceState(explored_findings=["h"])
    controller = ConvergenceController(state, ParetoArchive(max_size=4))
    directions = classify_findings(
        [
            {"finding_id": "h", "category": "hosting", "search_coverage": {"status": "search_incomplete"}},
            {"finding_id": "p", "category": "participation"},
        ]
    )
    chosen = controller.next_direction(directions)
    assert chosen is not None and chosen.finding_id == "p"

    # Once every finding is explored, an incomplete search is re-explored
    # instead of falsely declaring convergence.
    state.explored_findings = ["h", "p"]
    chosen = controller.next_direction(directions)
    assert chosen is not None and chosen.finding_id == "h"


def test_next_direction_respects_a_forced_finding() -> None:
    controller = ConvergenceController(ConvergenceState(), ParetoArchive(max_size=4))
    directions = classify_findings(
        [{"finding_id": "h", "category": "hosting"}, {"finding_id": "p", "category": "participation"}]
    )
    chosen = controller.next_direction(directions, force_finding_id="p")
    assert chosen is not None and chosen.finding_id == "p"


# ---------------------------------------------------------------------------
# Terminal reasons
# ---------------------------------------------------------------------------


def _run_epoch(
    controller: ConvergenceController,
    *,
    findings: list[dict],
    generated: list[ArchiveEntry] | None = None,
    direction: str | None = None,
    incomplete: list[str] | None = None,
) -> object:
    classified = classify_findings(findings)
    chosen = next((d for d in classified if d.direction == direction), None)
    if chosen is None and classified:
        chosen = classified[0]
    return controller.record_epoch(
        direction=chosen,
        generated=generated or [],
        findings=classified,
        search_incomplete_directions=incomplete or [],
    )


def test_terminal_pass_when_no_material_findings_remain() -> None:
    controller = ConvergenceController(ConvergenceState(), ParetoArchive(max_size=4))
    outcome = _run_epoch(controller, findings=[])
    assert outcome.terminal_reason == TERMINAL_PASS
    assert "no remaining material finding" in describe_terminal(TERMINAL_PASS).lower()


def test_terminal_operator_required_when_only_authority_findings_remain() -> None:
    controller = ConvergenceController(ConvergenceState(), ParetoArchive(max_size=4))
    outcome = _run_epoch(
        controller,
        findings=[{"finding_id": "p", "category": "participation", "requires_operator": True}],
    )
    assert outcome.terminal_reason == TERMINAL_OPERATOR_REQUIRED
    assert "operator" in outcome.terminal_detail.lower()


def test_search_incomplete_prevents_false_plateau_convergence() -> None:
    controller = ConvergenceController(
        ConvergenceState(), ParetoArchive(max_size=4), max_no_improvement_epochs=1, max_epochs=4
    )
    findings = [
        {"finding_id": "h", "category": "hosting", "search_coverage": {"status": "search_incomplete"}}
    ]
    # Two no-improvement epochs; the plateau threshold is reached, but an
    # incomplete supported search must keep exploration open.
    first = _run_epoch(controller, findings=findings, incomplete=["hosting"])
    assert first.terminal_reason == TERMINAL_NONE
    second = _run_epoch(controller, findings=findings, incomplete=["hosting"])
    assert second.terminal_reason == TERMINAL_NONE
    # At the configured epoch budget it pauses truthfully as a resumable
    # budget stop, never as proof of optimality.
    third = _run_epoch(controller, findings=findings, incomplete=["hosting"])
    fourth = _run_epoch(controller, findings=findings, incomplete=["hosting"])
    assert fourth.terminal_reason == ""
    assert fourth.pause_reason == PAUSE_BUDGET_EXHAUSTED
    assert controller.state.is_paused() is True
    assert "not completed convergence" in fourth.pause_detail.lower()


def test_plateau_detected_when_a_complete_epoch_adds_nothing_useful() -> None:
    controller = ConvergenceController(
        ConvergenceState(), ParetoArchive(max_size=4), max_no_improvement_epochs=1, max_epochs=6
    )
    findings = [{"finding_id": "h", "category": "hosting"}]
    # First epoch improves the frontier.
    first = _run_epoch(
        controller, findings=findings, generated=[_entry("a", {"x": 1.0})]
    )
    assert first.improved is True
    assert first.terminal_reason == TERMINAL_NONE
    # Second epoch produces only a dominated candidate -> no improvement.
    second = _run_epoch(
        controller, findings=findings, generated=[_entry("b", {"x": 5.0})]
    )
    assert second.improved is False
    assert second.terminal_reason == TERMINAL_PARETO_STABLE
    assert "pareto-stable" in second.terminal_detail.lower()


def test_bounded_search_exhausted_is_distinct_from_operator_required() -> None:
    controller = ConvergenceController(ConvergenceState(), ParetoArchive(max_size=4))
    outcome = _run_epoch(
        controller,
        findings=[
            {
                "finding_id": "h",
                "category": "hosting",
                "search_coverage": {"status": "bounded_search_exhausted"},
            }
        ],
    )
    assert outcome.terminal_reason == TERMINAL_BOUNDED_SEARCH_EXHAUSTED
    assert "not proof of infeasibility" in outcome.terminal_detail


def test_describe_terminal_never_claims_global_optimality() -> None:
    for reason in (
        TERMINAL_PASS,
        TERMINAL_OPERATOR_REQUIRED,
        TERMINAL_BOUNDED_SEARCH_EXHAUSTED,
        TERMINAL_PARETO_STABLE,
    ):
        text = describe_terminal(reason).lower()
        assert "globally" not in text
        assert "global optimum" not in text
    # A budget pause is not a terminal; its wording is bounded-search evidence
    # and names the resumable budget rather than claiming convergence.
    pause_text = describe_pause(PAUSE_BUDGET_EXHAUSTED).lower()
    assert "not completed convergence" in pause_text
    assert "globally" not in pause_text


def test_later_dominated_attempt_cannot_drop_an_earlier_frontier_candidate() -> None:
    controller = ConvergenceController(ConvergenceState(), ParetoArchive(max_size=4))
    _run_epoch(
        controller,
        findings=[{"finding_id": "h", "category": "hosting"}],
        generated=[_entry("good", {"x": 1.0, "y": 1.0})],
    )
    _run_epoch(
        controller,
        findings=[{"finding_id": "h", "category": "hosting"}],
        generated=[_entry("worse", {"x": 9.0, "y": 9.0})],
    )
    assert "good" in controller.archive.refs()
    assert "worse" not in controller.archive.refs()


def test_archive_entry_from_option_uses_measured_objectives() -> None:
    entry = archive_entry_from_option(
        {"option_id": "o1", "finding_id": "h", "family": "hosting_balance", "objectives": {"hard_violations": 0.0}},
        direction="hosting",
        candidate_ref="pareto:hosting:o1",
        candidate_fingerprint="fp",
    )
    assert entry.objective_vector == {"hard_violations": 0.0}
    assert entry.source["option_id"] == "o1"


# ---------------------------------------------------------------------------
# Resolved search coverage tracking
# ---------------------------------------------------------------------------


def test_recorded_bounded_exhaustion_overrides_cheap_incomplete_view() -> None:
    controller = ConvergenceController(ConvergenceState(), ParetoArchive(max_size=4), max_epochs=6)
    findings = classify_findings(
        [{"finding_id": "h", "category": "hosting", "search_coverage": {"status": "search_incomplete"}}]
    )
    outcome = controller.record_epoch(
        direction=findings[0],
        generated=[],
        findings=findings,
        resolved_coverage={"h": {"status": "bounded_search_exhausted"}},
    )
    assert controller.state.search_coverage["h"]["status"] == "bounded_search_exhausted"
    assert outcome.terminal_reason == TERMINAL_BOUNDED_SEARCH_EXHAUSTED
    # A finding recorded as exhausted is no longer re-explored on this candidate.
    fresh = classify_findings(
        [{"finding_id": "h", "category": "hosting", "search_coverage": {"status": "search_incomplete"}}]
    )
    assert controller.next_direction(fresh) is None


def test_candidate_change_clears_recorded_coverage_and_explored_findings() -> None:
    controller = ConvergenceController(ConvergenceState(), ParetoArchive(max_size=4), max_epochs=6)
    finding = classify_findings([{"finding_id": "h", "category": "hosting"}])[0]
    controller.record_epoch(
        direction=finding,
        generated=[],
        findings=[finding],
        resolved_coverage={"h": {"status": "bounded_search_exhausted"}},
    )
    assert controller.state.explored_findings == ["h"]
    assert controller.state.search_coverage["h"]["status"] == "bounded_search_exhausted"
    assert controller.state.round_explored_directions == ["hosting"]
    # A committed candidate is a new baseline: the old search evidence no
    # longer describes it, so the next epoch may search it again. The
    # controller-round fairness history is *not* candidate-scoped and survives.
    controller.record_epoch(
        direction=None,
        generated=[],
        findings=[finding],
        candidate_changed=True,
    )
    assert controller.state.search_coverage == {}
    assert controller.state.explored_findings == []
    assert controller.state.round_explored_directions == ["hosting"]
    assert "hosting" in controller.state.explored_directions


def test_budget_exhaustion_is_a_resumable_pause_not_a_terminal() -> None:
    controller = ConvergenceController(
        ConvergenceState(), ParetoArchive(max_size=8), max_epochs=1
    )
    finding = classify_findings([{"finding_id": "h", "category": "hosting"}])[0]

    first = controller.record_epoch(
        direction=finding,
        generated=[_entry("a", {"x": 1.0, "y": 5.0})],
        findings=[finding],
    )
    assert first.epoch == 1
    assert first.terminal_reason == ""
    assert first.pause_reason == PAUSE_BUDGET_EXHAUSTED
    assert controller.state.is_terminal() is False
    assert controller.state.is_paused() is True

    # A larger budget resumes from the persisted epoch in place, without a
    # candidate mutation, forced finding or state edit.
    controller.max_epochs = 4
    assert controller.next_direction([finding]) is not None
    second = controller.record_epoch(
        direction=finding,
        generated=[_entry("b", {"x": 2.0, "y": 4.0})],
        findings=[finding],
    )
    assert second.epoch == 2
    assert second.terminal_reason == ""
    assert second.pause_reason == ""
    assert controller.state.is_paused() is False
    # The archive survived the pause.
    assert controller.archive.refs() == ["a", "b"]


def test_direction_fairness_round_robin_survives_candidate_mutation() -> None:
    controller = ConvergenceController(
        ConvergenceState(), ParetoArchive(max_size=16), max_epochs=8, max_no_improvement_epochs=8
    )
    findings = classify_findings(
        [
            {"finding_id": "h", "category": "hosting"},
            {"finding_id": "m", "category": "manual_placement"},
            {"finding_id": "p", "category": "participation"},
            {"finding_id": "r", "category": "roster_shape"},
        ]
    )

    def explore_round() -> list[str]:
        order: list[str] = []
        for _ in range(4):
            direction = controller.next_direction(findings)
            assert direction is not None
            order.append(direction.direction)
            # Every successful mutation refreshes candidate-scoped evidence but
            # must not reset the controller round back to the first sorted
            # direction (nor let hosting monopolize the budget).
            controller.record_epoch(
                direction=direction,
                generated=[_entry(f"e{direction.direction}", {"x": 1.0, "y": -1.0})],
                findings=findings,
                candidate_changed=True,
            )
        return order

    expected = ["hosting", "placement", "participants", "roster_shape"]
    assert explore_round() == expected
    assert controller.state.explored_findings == ["r"]
    # A full round of successful mutations never let one direction monopolize:
    # the next round continues fairly instead of restarting from hosting on
    # every mutation.
    assert explore_round() == expected
    assert "hosting" in controller.state.explored_directions


def test_exploration_exhausted_reports_bounded_plateau_not_budget() -> None:
    controller = ConvergenceController(ConvergenceState(), ParetoArchive(max_size=4), max_epochs=6)
    findings = classify_findings([{"finding_id": "h", "category": "hosting"}])
    outcome = controller.record_epoch(
        direction=findings[0],
        generated=[],
        findings=findings,
        resolved_coverage={"h": {"status": "option_available"}},
        exploration_exhausted=True,
    )
    assert outcome.terminal_reason == TERMINAL_PARETO_STABLE
    assert "pareto-stable" in outcome.terminal_detail.lower()


# ---------------------------------------------------------------------------
# Search-capability identity: stale exhaustion is retryable
# ---------------------------------------------------------------------------


def _finding(capability: SearchCapability, *, finding_id: str = "h") -> dict:
    return {
        "finding_id": finding_id,
        "category": "hosting",
        "search_coverage": {"status": "search_incomplete", "capability": capability.to_dict()},
    }


def test_capability_fingerprint_changes_with_configuration_and_version() -> None:
    v1 = SearchCapability("hosting", "1", {"dimensions": ["participants"]})
    v1_same = SearchCapability("hosting", "1", {"dimensions": ["participants"]})
    widened = SearchCapability("hosting", "1", {"dimensions": ["participants", "host"]})
    bumped = SearchCapability("hosting", "2", {"dimensions": ["participants"]})
    assert v1.fingerprint == v1_same.fingerprint
    assert v1.fingerprint != widened.fingerprint
    assert v1.fingerprint != bumped.fingerprint
    assert v1.to_dict()["fingerprint"] == v1.fingerprint


def test_capability_is_stale_treats_unrecorded_evidence_as_stale() -> None:
    current = SearchCapability("hosting", "2", {"dimensions": ["participants"]})
    assert capability_is_stale({"capability": {"fingerprint": "deadbeef"}}, current) is True
    # No recorded capability cannot be shown to describe the current search.
    assert capability_is_stale({}, current) is True
    # A missing current capability cannot decide staleness and is never
    # treated as changed.
    assert capability_is_stale({"capability": {"fingerprint": "deadbeef"}}, "") is False
    assert recorded_exhaustion_is_stale({"capability": {"fingerprint": "deadbeef"}}, "") is False


def test_bounded_exhaustion_under_superseded_capability_is_retryable() -> None:
    v1 = SearchCapability("hosting", "1", {"dimensions": ["participants"]})
    v2 = SearchCapability("hosting", "2", {"dimensions": ["participants", "host"]})
    controller = ConvergenceController(ConvergenceState(), ParetoArchive(max_size=4), max_epochs=6)
    fresh_v2 = classify_findings([_finding(v2)])
    controller.record_epoch(
        direction=fresh_v2[0],
        generated=[],
        findings=fresh_v2,
        resolved_coverage={"h": {"status": "bounded_search_exhausted", "capability": v1.to_dict()}},
    )

    effective = controller.effective_findings(classify_findings([_finding(v2)]))
    assert effective[0].search_capability == v2.fingerprint
    assert effective[0].capability_stale is True
    assert effective[0].bounded_exhausted is False
    assert effective[0].actionable is True
    # The finding re-enters the actionable set instead of being suppressed by
    # exhaustion evidence produced by a superseded search.
    chosen = controller.next_direction(classify_findings([_finding(v2)]))
    assert chosen is not None and chosen.finding_id == "h"

    # Evidence recorded under the *current* capability is still honored and
    # does not endlessly re-open the same search.
    same = controller.effective_findings(classify_findings([_finding(v1)]))
    assert same[0].capability_stale is False
    assert same[0].bounded_exhausted is True
    assert same[0].actionable is False


def test_recorded_exhaustion_without_capability_is_honored_when_none_declared() -> None:
    controller = ConvergenceController(ConvergenceState(), ParetoArchive(max_size=4), max_epochs=6)
    finding = classify_findings([{"finding_id": "h", "category": "hosting"}])[0]
    controller.record_epoch(
        direction=finding,
        generated=[],
        findings=[finding],
        resolved_coverage={"h": {"status": "bounded_search_exhausted"}},
    )
    effective = controller.effective_findings(
        classify_findings([{"finding_id": "h", "category": "hosting"}])
    )
    assert effective[0].capability_stale is False
    assert effective[0].bounded_exhausted is True


# ---------------------------------------------------------------------------
# Review handoff ranking
# ---------------------------------------------------------------------------


def test_review_ranking_prefers_fewer_unresolved_placements() -> None:
    # Scenario A: repair B has one fewer unresolved placement (43 vs 44) and is
    # non-dominated because C improves another objective but reintroduces the
    # placement. B must remain on the frontier *and* be the preferred handoff.
    b = ArchiveEntry(
        candidate_ref="pareto:placement:b",
        candidate_fingerprint="fp-b",
        objective_vector={
            "hard_violations": 0.0,
            "unresolved_placement_obligations": 43.0,
            "changed_tournament_count": 4.0,
        },
        metrics={"hard_violations": 0.0, "unresolved_placement_count": 43.0},
    )
    c = ArchiveEntry(
        candidate_ref="pareto:hosting:c",
        candidate_fingerprint="fp-c",
        objective_vector={
            "hard_violations": 0.0,
            "unresolved_placement_obligations": 44.0,
            "changed_tournament_count": 1.0,
        },
        metrics={"hard_violations": 0.0, "unresolved_placement_count": 44.0},
    )
    archive = ParetoArchive(max_size=8)
    assert archive.consider(b)["accepted"] is True
    assert archive.consider(c)["accepted"] is True  # trade-off, not dominated

    ranked = rank_frontier_for_review(archive.entries)
    assert [entry["candidate_ref"] for entry in ranked] == [
        "pareto:placement:b",
        "pareto:hosting:c",
    ]
    assert ranked[0]["recommended"] is True
    assert ranked[0]["audit_priorities"]["unresolved_placement_count"] == 43.0
    assert ranked[1]["audit_priorities"]["unresolved_placement_count"] == 44.0
    assert "unresolved_placement_count" in ranked[0]["selection_reason"]
    assert recommended_review_candidate_ref(archive.entries) == "pareto:placement:b"


def test_review_ranking_matches_current_when_no_material_priorities_exist() -> None:
    a = ArchiveEntry(
        candidate_ref="pareto:hosting:a",
        candidate_fingerprint="fp-a",
        objective_vector={"x": 1.0, "y": 5.0},
    )
    b = ArchiveEntry(
        candidate_ref="pareto:hosting:b",
        candidate_fingerprint="fp-b",
        objective_vector={"x": 5.0, "y": 1.0},
    )
    ranked = rank_frontier_for_review([b, a])
    assert ranked[0]["recommended"] is True
    assert "deterministic candidate identity" in ranked[0]["selection_reason"]
