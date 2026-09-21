"""Canonical scheduling rule/objective/obligation catalog.

This module is deliberately *metadata only*. It is the one searchable place
that answers, for every recurring scheduling semantic:

    What is this rule called?
    What kind of semantic is it?
    What does it mean?
    Where does its authoritative truth/verification live?
    Which configuration/fact feeds it?
    Which action/search paths must preserve it?
    Which tests prove it?
    How is it exposed in operator evidence/reporting?
    Which other semantics must take precedence over it?

The deterministic implementations named by each entry remain the executable
truth. The catalog never verifies a candidate, never scores a plan and never
generates rule code; it is navigation/identity metadata so an agent (or a
report) can locate the owning facade and tests before editing behavior.

Design constraints (from the issue):

* exactly one primary ``classification`` per entry;
* no generic rules DSL and no rule engine;
* no planner/verifier imports -- entries reference modules by dotted string,
  so this module stays a leaf dependency that any layer may import;
* stable IDs contain no club/age-group/date/season names;
* ``precedes``/``depends_on`` express the small amount of precedence that
  actually matters (for hosting: coverage before proportional balance) without
  becoming a rule engine.

``validate_catalog`` and ``render_markdown`` are consumed by the conformance
guards in ``tests/test_rule_catalog.py`` and by
``scripts/render-rule-catalog.py``. The generated agent-facing ownership table
lives in ``docs/architecture/rule-catalog.md``.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# --- classification vocabulary ------------------------------------------------

HARD_CONSTRAINT = "hard_constraint"
OPERATIONAL_OBLIGATION = "operational_obligation"
SOFT_OBJECTIVE = "soft_objective"
OPERATOR_DECISION = "operator_decision"
FACT_EVIDENCE_SEMANTIC = "fact_evidence_semantic"

CLASSIFICATIONS: tuple[str, ...] = (
    HARD_CONSTRAINT,
    OPERATIONAL_OBLIGATION,
    SOFT_OBJECTIVE,
    OPERATOR_DECISION,
    FACT_EVIDENCE_SEMANTIC,
)

CLASSIFICATION_LABELS: dict[str, str] = {
    HARD_CONSTRAINT: "Hard constraint",
    OPERATIONAL_OBLIGATION: "Operational obligation",
    SOFT_OBJECTIVE: "Soft objective",
    OPERATOR_DECISION: "Operator decision",
    FACT_EVIDENCE_SEMANTIC: "Fact / evidence semantic",
}

STATUS_ACTIVE = "active"
STATUS_LEGACY = "legacy"
STATUS_COMPATIBILITY_ONLY = "compatibility-only"

STATUSES: tuple[str, ...] = (STATUS_ACTIVE, STATUS_LEGACY, STATUS_COMPATIBILITY_ONLY)

# Generated agent-facing ownership table. Keep in one place so the renderer and
# the conformance guard agree.
CATALOG_DOC_PATH = Path("docs/architecture/rule-catalog.md")


@dataclass(frozen=True)
class RuleEntry:
    """One catalogued scheduling semantic.

    ``precedes`` lists lower-priority rule IDs this semantic must be satisfied
    before. ``depends_on`` lists rule IDs whose own satisfaction this semantic
    is contingent on. Both reference other catalog IDs and are validated.
    """

    id: str
    classification: str
    meaning: str
    canonical_owner: str
    input_source: str
    verifier_owner: str = ""
    mutation_providers: tuple[str, ...] = ()
    search_providers: tuple[str, ...] = ()
    evidence_projection: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    status: str = STATUS_ACTIVE
    verifier_codes: tuple[str, ...] = ()
    finding_codes: tuple[str, ...] = ()
    score_paths: tuple[str, ...] = ()
    precedes: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    waivable: bool = True


# ---------------------------------------------------------------------------
# Hard constraints
# ---------------------------------------------------------------------------

_HARD: tuple[RuleEntry, ...] = (
    RuleEntry(
        id="team_age_group_exact",
        classification=HARD_CONSTRAINT,
        meaning="Every team participating in a tournament belongs to that tournament's age group.",
        canonical_owner="tournament_scheduler.planning_contract",
        input_source="planning_problem.teams[].age_group + candidate tournament age_group",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("tournament_scheduler.participant_selection",),
        search_providers=("tournament_scheduler.search_neighborhood_repair",),
        evidence_projection=("verify_candidate.violations", "rules_model age_group_exact_match"),
        tests=("tests/test_planning_contract.py",),
        verifier_codes=("age_group_mismatch",),
    ),
    RuleEntry(
        id="team_unique_in_tournament",
        classification=HARD_CONSTRAINT,
        meaning="A team may appear at most once in a single tournament.",
        canonical_owner="tournament_scheduler.planning_contract",
        input_source="candidate tournament roster",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("tournament_scheduler.participant_selection",),
        search_providers=("tournament_scheduler.search_neighborhood_repair",),
        evidence_projection=("verify_candidate.violations",),
        tests=("tests/test_planning_contract.py",),
        verifier_codes=("duplicate_team_in_tournament",),
    ),
    RuleEntry(
        id="team_unique_per_date",
        classification=HARD_CONSTRAINT,
        meaning="A team may not be scheduled in two different tournaments on the same date.",
        canonical_owner="tournament_scheduler.planning_contract",
        input_source="candidate tournament dates + roster",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("tournament_scheduler.participant_selection",),
        search_providers=("tournament_scheduler.search_neighborhood_repair",),
        evidence_projection=("verify_candidate.violations", "rules_model no_same_date_double_participation"),
        tests=("tests/test_planning_contract.py",),
        verifier_codes=("duplicate_participation_same_date",),
    ),
    RuleEntry(
        id="tournament_roster_shape",
        classification=HARD_CONSTRAINT,
        meaning=(
            "A tournament has an admissible, avoidable-by-free participant shape: even teams and "
            "no pause/bye rounds (or the exact age-group team count where configured). An "
            "input-constrained scarce shape is surfaced separately, never as a soft preference."
        ),
        canonical_owner="tournament_scheduler.effective_tournament_shape",
        input_source="planning_problem registered pool + rounds_per_tournament",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("tournament_scheduler.participant_roster_sizing",),
        search_providers=("tournament_scheduler.participant_roster_repair",),
        evidence_projection=("verify_candidate.violations", "verify_candidate.input_constrained_shapes"),
        tests=("tests/test_effective_tournament_shape.py", "tests/test_planning_contract.py"),
        verifier_codes=("bye_team_not_allowed",),
        finding_codes=("input_constrained_shape",),
    ),
    RuleEntry(
        id="club_hard_max",
        classification=HARD_CONSTRAINT,
        meaning="At most three teams from one club may participate in one tournament.",
        canonical_owner="tournament_scheduler.planning_contract",
        input_source="candidate tournament roster club labels",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("tournament_scheduler.participant_selection",),
        search_providers=("tournament_scheduler.search_neighborhood_repair", "tournament_scheduler.stage3_cpsat_club_cap"),
        evidence_projection=("verify_candidate.violations",),
        tests=("tests/test_planning_contract.py",),
        verifier_codes=("club_hard_max_exceeded",),
    ),
    RuleEntry(
        id="tournament_ice_booking_duration",
        classification=HARD_CONSTRAINT,
        meaning=(
            "A tournament's configured ice_time_minutes is the complete hall occupancy window. "
            "It must be at least the actual rounds times round length plus the per-round "
            "changeover buffer, and any governing per-series-round booking floor."
        ),
        canonical_owner="tournament_scheduler.occupancy",
        input_source="planning_problem ice_time_minutes + round_length_minutes + generated round count",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        evidence_projection=("verify_candidate.violations", "rules_model tournament_duration"),
        tests=("tests/test_occupancy.py", "tests/test_planning_contract.py", "tests/test_stage1_config.py"),
        verifier_codes=("ice_time_playing_minimum", "ice_time_governing_minimum"),
        waivable=False,
    ),
    RuleEntry(
        id="arena_interval_non_overlap",
        classification=HARD_CONSTRAINT,
        meaning=(
            "Two tournaments must never require the same arena in overlapping datetime "
            "intervals. A failure to evaluate the interval data is itself a blocking "
            "verification result, not a silent pass."
        ),
        canonical_owner="tournament_scheduler.arena_conflicts",
        input_source="candidate start_time/duration + arena identity",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("arena_conflict_decision", "date_policy_relocation"),
        search_providers=("tournament_scheduler.search_neighborhood_repair",),
        evidence_projection=("verify_candidate.violations", "plan.arena_day_collisions", "rules_model arena_day_collisions"),
        tests=("tests/test_arena_conflicts.py", "tests/test_planning_contract.py"),
        verifier_codes=("arena_interval_conflict", "arena_interval_check_failed"),
    ),
    RuleEntry(
        id="tournament_capacity",
        classification=HARD_CONSTRAINT,
        meaning="A tournament must not exceed its configured participant capacity.",
        canonical_owner="tournament_scheduler.planning_contract",
        input_source="planning_problem tournament sizing config",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("tournament_scheduler.participant_roster_sizing",),
        evidence_projection=("verify_candidate.violations", "rules_model tournament_capacity"),
        tests=("tests/test_planning_contract.py",),
        verifier_codes=("tournament_over_capacity",),
    ),
    RuleEntry(
        id="tournament_min_size",
        classification=HARD_CONSTRAINT,
        meaning="A tournament with enough registered teams in its age group must have at least three participants.",
        canonical_owner="tournament_scheduler.final_verification",
        input_source="planning_problem registered pool + candidate capacity",
        verifier_owner="tournament_scheduler.final_verification.verify_final_candidate",
        mutation_providers=("tournament_scheduler.underfilled_roster_repair",),
        evidence_projection=("verify_final_candidate.violations",),
        tests=("tests/test_final_verification.py",),
        verifier_codes=("tournament_under_minimum",),
    ),
    RuleEntry(
        id="parallel_capacity",
        classification=HARD_CONSTRAINT,
        meaning="A tournament must not schedule more concurrent games than the configured parallel capacity.",
        canonical_owner="tournament_scheduler.final_verification",
        input_source="planning_problem parallel_games + generated games",
        verifier_owner="tournament_scheduler.final_verification.verify_final_candidate",
        mutation_providers=("tournament_scheduler.game_generation",),
        evidence_projection=("verify_final_candidate.violations",),
        tests=("tests/test_final_verification.py",),
        verifier_codes=("parallel_capacity_exceeded",),
    ),
    RuleEntry(
        id="registered_teams_only",
        classification=HARD_CONSTRAINT,
        meaning="Every RVV participant of a tournament must exist in the registered roster (guests are explicit, separate places).",
        canonical_owner="tournament_scheduler.planning_contract",
        input_source="planning_problem.teams",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("tournament_scheduler.participant_selection",),
        evidence_projection=("verify_candidate.violations", "rules_model registered_teams_only"),
        tests=("tests/test_planning_contract.py",),
        verifier_codes=("unregistered_team",),
    ),
    RuleEntry(
        id="valid_tournament_date",
        classification=HARD_CONSTRAINT,
        meaning="A tournament must carry a parseable date; a missing/invalid date is structural corruption.",
        canonical_owner="tournament_scheduler.planning_contract",
        input_source="candidate tournament date",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        evidence_projection=("verify_candidate.violations",),
        tests=("tests/test_planning_contract.py",),
        verifier_codes=("invalid_date",),
        waivable=False,
    ),
    RuleEntry(
        id="date_within_window",
        classification=HARD_CONSTRAINT,
        meaning="Every tournament date lies inside the configured planning/season window.",
        canonical_owner="tournament_scheduler.planning_contract",
        input_source="planning_problem start/end date",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("date_policy_relocation",),
        evidence_projection=("verify_candidate.violations", "rules_model date_within_planning_window"),
        tests=("tests/test_planning_contract.py",),
        verifier_codes=("date_outside_window",),
    ),
    RuleEntry(
        id="holiday_date_admissible",
        classification=HARD_CONSTRAINT,
        meaning="Tournaments must not be scheduled on canonically excluded holiday dates for the planning window.",
        canonical_owner="tournament_scheduler.date_policy",
        input_source="planning_problem window -> derived date exclusions",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("date_policy_relocation",),
        evidence_projection=("verify_candidate.violations", "rules_model holiday_dates_not_used"),
        tests=("tests/test_holiday_date_policy.py",),
        verifier_codes=("holiday_date_used",),
    ),
    RuleEntry(
        id="banned_dates_not_used",
        classification=HARD_CONSTRAINT,
        meaning="Tournaments must not be scheduled on an operator-banned global date.",
        canonical_owner="tournament_scheduler.canonical_banned_dates",
        input_source="decisions.json banned dates projected into the planning problem",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("canonical season batch", "date_policy_relocation"),
        evidence_projection=("verify_candidate.violations", "season banned-dates --json"),
        tests=("tests/test_canonical_banned_dates.py",),
        verifier_codes=("banned_date_used",),
    ),
    RuleEntry(
        id="excluded_host_club_not_used",
        classification=HARD_CONSTRAINT,
        meaning="A club explicitly excluded as host for a scope must not host there.",
        canonical_owner="tournament_scheduler.planning_contract",
        input_source="planning_problem manual_adjustments excluded_host_clubs",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("host_placement_repair",),
        search_providers=("tournament_scheduler.search_neighborhood_repair",),
        evidence_projection=("verify_candidate.violations", "rules_model excluded_host_clubs_not_used"),
        tests=("tests/test_planning_contract.py",),
        verifier_codes=("excluded_host_club_used",),
    ),
    RuleEntry(
        id="host_representation",
        classification=HARD_CONSTRAINT,
        meaning=(
            "A tournament's host club must be represented by its own participating team "
            "(shared/joint registrations count for either constituent). This is the hard "
            "representation invariant; proportional hosting balance is a separate soft objective."
        ),
        canonical_owner="tournament_scheduler.host_representation",
        input_source="candidate host_club + tournament roster",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("host_team_missing_repair", "placement_preserving_roster_repair"),
        search_providers=("tournament_scheduler.search_neighborhood_repair",),
        evidence_projection=("verify_candidate.violations",),
        tests=("tests/test_stage3_optimizer_host_representation.py", "tests/test_host_team_missing_repair.py"),
        verifier_codes=("host_team_missing",),
        precedes=("hosting_age_group_coverage", "hosting_proportional_balance"),
    ),
    RuleEntry(
        id="locked_date_preserved",
        classification=HARD_CONSTRAINT,
        meaning="A canonical locked date must still have a scheduled tournament.",
        canonical_owner="tournament_scheduler.canonical_baseline",
        input_source="canonical decisions.json locked dates",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        evidence_projection=("verify_candidate.violations", "rules_model locked_dates_preserved"),
        tests=("tests/test_canonical_baseline.py",),
        verifier_codes=("locked_date_missing",),
    ),
    RuleEntry(
        id="pinned_tournament_preserved",
        classification=HARD_CONSTRAINT,
        meaning="A pinned tournament id must remain present in the candidate.",
        canonical_owner="tournament_scheduler.canonical_baseline",
        input_source="canonical decisions.json pinned tournament ids",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        evidence_projection=("verify_candidate.violations", "rules_model pinned_tournaments_preserved"),
        tests=("tests/test_canonical_baseline.py",),
        verifier_codes=("pinned_tournament_missing",),
    ),
    RuleEntry(
        id="canonical_locked_tournament_preserved",
        classification=HARD_CONSTRAINT,
        meaning="An approved/placement-locked canonical tournament must not be dropped by replanning.",
        canonical_owner="tournament_scheduler.canonical_baseline",
        input_source="canonical decisions.json locks",
        verifier_owner="tournament_scheduler.canonical_baseline.verify_canonical_locks",
        evidence_projection=("verify_candidate.violations",),
        tests=("tests/test_canonical_baseline.py",),
        verifier_codes=("canonical_locked_tournament_missing",),
        waivable=False,
    ),
    RuleEntry(
        id="canonical_placement_preserved",
        classification=HARD_CONSTRAINT,
        meaning="A placement-locked canonical tournament keeps its protected placement fields.",
        canonical_owner="tournament_scheduler.canonical_baseline",
        input_source="canonical decisions.json locks",
        verifier_owner="tournament_scheduler.canonical_baseline.verify_canonical_locks",
        evidence_projection=("verify_candidate.violations", "season protections"),
        tests=("tests/test_canonical_baseline.py", "tests/test_approval_lifecycle.py"),
        verifier_codes=("canonical_placement_locked",),
        waivable=False,
    ),
    RuleEntry(
        id="canonical_participants_preserved",
        classification=HARD_CONSTRAINT,
        meaning="A participant-locked canonical tournament keeps its protected participant list.",
        canonical_owner="tournament_scheduler.canonical_baseline",
        input_source="canonical decisions.json locks",
        verifier_owner="tournament_scheduler.canonical_baseline.verify_canonical_locks",
        evidence_projection=("verify_candidate.violations", "season protections"),
        tests=("tests/test_canonical_baseline.py", "tests/test_approval_lifecycle.py"),
        verifier_codes=("canonical_participants_locked",),
        waivable=False,
    ),
    RuleEntry(
        id="participation_hard_max",
        classification=HARD_CONSTRAINT,
        meaning=(
            "An explicitly configured participation hard maximum is a legality boundary and is "
            "operator-waivable. It is separate from the ordinary participation target, which is "
            "a soft objective and never a hard cap."
        ),
        canonical_owner="tournament_scheduler.participation_targets",
        input_source="planning_problem participation_hard_max / participation_hard_max_by_age_group",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("tournament_scheduler.participation_deviation_repair",),
        evidence_projection=("verify_candidate.violations", "verify_candidate.waived_violations", "waiver list"),
        tests=("tests/test_participation_targets.py", "tests/test_operator_waivers.py"),
        verifier_codes=("participation_hard_max_exceeded",),
    ),
    RuleEntry(
        id="game_team_membership",
        classification=HARD_CONSTRAINT,
        meaning="Every exported game is between two declared participants of its tournament.",
        canonical_owner="tournament_scheduler.final_verification",
        input_source="candidate tournament roster + games",
        verifier_owner="tournament_scheduler.final_verification.verify_final_candidate",
        evidence_projection=("verify_final_candidate.violations",),
        tests=("tests/test_final_verification.py",),
        verifier_codes=("game_team_not_participant",),
    ),
    RuleEntry(
        id="valid_game_record",
        classification=HARD_CONSTRAINT,
        meaning="Every exported game record is a well-formed object with distinct participants.",
        canonical_owner="tournament_scheduler.final_verification",
        input_source="candidate tournament games",
        verifier_owner="tournament_scheduler.final_verification.verify_final_candidate",
        evidence_projection=("verify_final_candidate.violations",),
        tests=("tests/test_final_verification.py",),
        verifier_codes=("invalid_game_record",),
        waivable=False,
    ),
    RuleEntry(
        id="game_round_integrity",
        classification=HARD_CONSTRAINT,
        meaning="Every exported game has a valid positive round number.",
        canonical_owner="tournament_scheduler.final_verification",
        input_source="candidate tournament games",
        verifier_owner="tournament_scheduler.final_verification.verify_final_candidate",
        evidence_projection=("verify_final_candidate.violations",),
        tests=("tests/test_final_verification.py",),
        verifier_codes=("invalid_game_round",),
        waivable=False,
    ),
    RuleEntry(
        id="game_integrity_ambiguous_participants",
        classification=HARD_CONSTRAINT,
        meaning="A tournament's participant labels must be present and unique before game integrity can be judged.",
        canonical_owner="tournament_scheduler.final_verification",
        input_source="candidate tournament roster labels",
        verifier_owner="tournament_scheduler.final_verification.verify_final_candidate",
        evidence_projection=("verify_final_candidate.violations",),
        tests=("tests/test_final_verification.py",),
        verifier_codes=("game_integrity_ambiguous_participants",),
        waivable=False,
    ),
    RuleEntry(
        id="round_robin_pairing",
        classification=HARD_CONSTRAINT,
        meaning="A round-robin tournament plays every required pairing exactly once.",
        canonical_owner="tournament_scheduler.final_verification",
        input_source="candidate tournament roster + games",
        verifier_owner="tournament_scheduler.final_verification.verify_final_candidate",
        mutation_providers=("tournament_scheduler.game_generation",),
        evidence_projection=("verify_final_candidate.violations",),
        tests=("tests/test_final_verification.py",),
        verifier_codes=("round_robin_missing_pair", "round_robin_duplicate_pair"),
    ),
    RuleEntry(
        id="team_unique_per_round",
        classification=HARD_CONSTRAINT,
        meaning="A team plays at most one game per round.",
        canonical_owner="tournament_scheduler.final_verification",
        input_source="candidate tournament games",
        verifier_owner="tournament_scheduler.final_verification.verify_final_candidate",
        mutation_providers=("tournament_scheduler.game_generation",),
        evidence_projection=("verify_final_candidate.violations",),
        tests=("tests/test_final_verification.py",),
        verifier_codes=("team_double_booked_in_round",),
    ),
    RuleEntry(
        id="tournament_round_count",
        classification=HARD_CONSTRAINT,
        meaning="A limited-rounds tournament produces exactly the effective configured round count.",
        canonical_owner="tournament_scheduler.final_verification",
        input_source="planning_problem rounds_per_tournament + effective shape",
        verifier_owner="tournament_scheduler.final_verification.verify_final_candidate",
        mutation_providers=("tournament_scheduler.game_generation",),
        evidence_projection=("verify_final_candidate.violations",),
        tests=("tests/test_final_verification.py",),
        verifier_codes=("configured_round_count_mismatch",),
    ),
    RuleEntry(
        id="intra_club_game_minimum",
        classification=HARD_CONSTRAINT,
        meaning="Same-club matchups are limited to the minimum required by the limited-rounds format.",
        canonical_owner="tournament_scheduler.final_verification",
        input_source="planning_problem parallel_games + roster club labels",
        verifier_owner="tournament_scheduler.final_verification.verify_final_candidate",
        mutation_providers=("tournament_scheduler.game_generation",),
        evidence_projection=("verify_final_candidate.violations",),
        tests=("tests/test_final_verification.py",),
        verifier_codes=("avoidable_same_club_matchup",),
    ),
    RuleEntry(
        id="request_team_unavailable",
        classification=HARD_CONSTRAINT,
        meaning="A durable operator request constraint: the team must not play inside its declared unavailable date range.",
        canonical_owner="tournament_scheduler.request_constraints",
        input_source="canonical decisions.json request constraints",
        verifier_owner="tournament_scheduler.request_constraints.request_constraint_violations",
        evidence_projection=("season constraints --json", "canonical apply boundary refusals"),
        tests=("tests/test_request_constraints.py",),
        verifier_codes=("team_unavailable",),
    ),
    RuleEntry(
        id="request_minimum_gap",
        classification=HARD_CONSTRAINT,
        meaning="A durable operator request constraint: the team must keep at least the requested days between tournaments.",
        canonical_owner="tournament_scheduler.request_constraints",
        input_source="canonical decisions.json request constraints",
        verifier_owner="tournament_scheduler.request_constraints.request_constraint_violations",
        evidence_projection=("season constraints --json", "canonical apply boundary refusals"),
        tests=("tests/test_request_constraints.py",),
        verifier_codes=("minimum_gap",),
    ),
    RuleEntry(
        id="request_opponent_avoidance",
        classification=HARD_CONSTRAINT,
        meaning="A durable operator request constraint: two teams must not meet inside the declared date range.",
        canonical_owner="tournament_scheduler.request_constraints",
        input_source="canonical decisions.json request constraints",
        verifier_owner="tournament_scheduler.request_constraints.request_constraint_violations",
        evidence_projection=("season constraints --json", "canonical apply boundary refusals"),
        tests=("tests/test_request_constraints.py",),
        verifier_codes=("opponent_avoidance",),
    ),
)


# ---------------------------------------------------------------------------
# Operational obligations
# ---------------------------------------------------------------------------

_OBLIGATIONS: tuple[RuleEntry, ...] = (
    RuleEntry(
        id="hosting_age_group_coverage",
        classification=OPERATIONAL_OBLIGATION,
        meaning=(
            "For every (club, age_group) with at least one registered team, the club receives "
            "at least one hosting responsibility in that age group during the season whenever "
            "the number of tournaments makes it mathematically possible. This is club x "
            "age-group coverage, not aggregate club hosting. A structural shortfall (fewer "
            "tournaments than clubs needing coverage) is surfaced explicitly, never hidden as "
            "a soft imbalance."
        ),
        canonical_owner="tournament_scheduler.hosting_coverage.hosting_targets_with_coverage_floor",
        input_source="planning_problem.teams + candidate tournaments (hosting_coverage_matrix)",
        verifier_owner="tournament_scheduler.hosting_coverage.hosting_coverage_matrix",
        mutation_providers=(
            "hosting_balance_repair",
            "host_placement_repair",
            "unplaced_placement_repair",
            "hosting_same_age_repair",
            "hosting_cross_age_repair",
        ),
        search_providers=("tournament_scheduler.search_neighborhood_repair",),
        evidence_projection=(
            "plan.unresolved_hosting_obligations",
            "verify_candidate.unresolved_hosting_obligations",
            "publication_readiness unresolved_hosting",
            "rules_model hosting_obligation_coverage",
        ),
        tests=("tests/test_hosting_coverage.py", "tests/test_hosting_same_age_repair.py"),
        finding_codes=(
            "unresolved_hosting_obligations",
            "unresolved_hosting",
            "unresolved_hosting_obligation",
        ),
        score_paths=("hosting.unresolved_obligations_count",),
        precedes=("hosting_proportional_balance",),
        depends_on=("host_representation",),
    ),
    RuleEntry(
        id="hosting_responsibility",
        classification=OPERATIONAL_OBLIGATION,
        meaning=(
            "Once a hosting responsibility is assigned, calendar convenience must not silently "
            "transfer it to another club. If the responsible host has no verified automatic "
            "slot, the responsibility is preserved and the tournament becomes MANUAL PLACEMENT "
            "REQUIRED rather than moving the burden. This is a cross-path invariant over "
            "placement/repair/search."
        ),
        canonical_owner="tournament_scheduler.hosting_responsibility",
        input_source="hosting_coverage target/actual ledger + candidate physical hosting",
        verifier_owner="tournament_scheduler.hosting_responsibility.hosting_responsibility_facts",
        mutation_providers=("responsibility_preserving_repair", "unplaced_placement_repair"),
        evidence_projection=(
            "hosting_responsibility finding code",
            "repair/search option rejection evidence",
            "review/audit evidence",
        ),
        tests=("tests/test_hosting_responsibility.py",),
        finding_codes=("unexplained_hosting_responsibility_transfer",),
        depends_on=("hosting_age_group_coverage",),
    ),
    RuleEntry(
        id="tournament_placement_obligation",
        classification=OPERATIONAL_OBLIGATION,
        meaning=(
            "A scheduled tournament's host is chosen among its participants; when no legal and "
            "trustworthy slot exists for the responsible host, the obligation stays unresolved "
            "for manual placement instead of being given to an unrelated club."
        ),
        canonical_owner="tournament_scheduler.placement_normalization",
        input_source="candidate placements + host calendar availability",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("unplaced_placement_repair", "host_placement_repair"),
        evidence_projection=("plan.unresolved_tournament_placements", "manual work items"),
        tests=("tests/test_placement_normalization.py", "tests/test_unplaced_placement_repair.py"),
        finding_codes=("unplaced_placement", "unplaced_tournament_placement"),
    ),
    RuleEntry(
        id="guest_reservation_integrity",
        classification=OPERATIONAL_OBLIGATION,
        meaning=(
            "A reserved guest place is a deliberate capacity reservation that counts toward "
            "capacity/ice-time shape but never toward RVV participation, hosting, fairness or "
            "travel; participant optimization/repair cannot consume it."
        ),
        canonical_owner="tournament_scheduler.guest_slots",
        input_source="canonical tournament guest_slot records",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("guest-fill", "guest-release"),
        evidence_projection=("season_plan.html guest places", "semantic audit guest_reservations"),
        tests=("tests/test_guest_slots.py",),
    ),
)


# ---------------------------------------------------------------------------
# Soft objectives
# ---------------------------------------------------------------------------

_SOFT: tuple[RuleEntry, ...] = (
    RuleEntry(
        id="hosting_proportional_balance",
        classification=SOFT_OBJECTIVE,
        meaning=(
            "After the age-group coverage floor is satisfied (or a structural shortfall is "
            "explicitly surfaced), remaining hosting responsibility is distributed roughly in "
            "proportion to registered team counts. This objective is secondary to coverage: a "
            "larger club may legitimately under-host relative to its proportional target so a "
            "smaller club gets its first responsibility."
        ),
        canonical_owner="tournament_scheduler.hosting_coverage.hosting_balance_matrix",
        input_source="planning_problem.teams + candidate tournaments",
        verifier_owner="tournament_scheduler.hosting_coverage.material_hosting_balance_imbalances",
        mutation_providers=("hosting_balance_repair",),
        evidence_projection=(
            "score_candidate.hosting.spread",
            "verify_candidate.hosting_balance_imbalances",
            "rules_model hosting_deviation",
        ),
        tests=("tests/test_hosting_coverage.py",),
        score_paths=("hosting.spread",),
        finding_codes=("hosting_balance_imbalances", "hosting_balance_imbalance"),
        depends_on=("hosting_age_group_coverage",),
    ),
    RuleEntry(
        id="participation_target",
        classification=SOFT_OBJECTIVE,
        meaning=(
            "The configured per-team/per-half tournament participation target is a desired "
            "optimization goal with evidenced relaxation, not an unconditional obligation or "
            "hard bound. Attainment is optimized within real season capacity; a "
            "capacity-constrained miss is not itself a planning failure."
        ),
        canonical_owner="tournament_scheduler.participation_targets",
        input_source="controlled workbook participation_targets_by_age_group / explicit team targets",
        verifier_owner="tournament_scheduler.participation_targets.evaluate_participation",
        mutation_providers=("tournament_scheduler.participation_deviation_repair",),
        search_providers=("tournament_scheduler.stage3_optimizer",),
        evidence_projection=(
            "score_candidate.participation.*",
            "verify_candidate.participation_deviations",
            "rules_model participation_target_deviation",
            "rules_model participation_shortfalls",
        ),
        tests=("tests/test_participation_targets.py",),
        finding_codes=(
            "participation_target_deviation",
            "participation_shortfalls",
            "participation_deviation",
        ),
        score_paths=(
            "participation.spread",
            "participation.club_pool_unresolved_avoidable_deviation_count",
            "participation.club_pool_unresolved_shortfall_count",
            "participation.club_pool_unresolved_season_total_absolute_deviation",
            "participation.club_pool_unresolved_max_team_season_deviation",
            "participation.club_pool_unresolved_half_total_absolute_deviation",
            "participation.club_pool_unresolved_max_team_half_deviation",
        ),
    ),
    RuleEntry(
        id="intra_club_participation_distribution",
        classification=SOFT_OBJECTIVE,
        meaning=(
            "Within a multi-team club and age group, participation is rotated evenly across the "
            "club's sibling team labels. An aggregate-complete but label-uneven pool is a "
            "distribution imbalance, not a missing participation opportunity."
        ),
        canonical_owner="tournament_scheduler.participation_targets",
        input_source="planning_problem club pools + candidate participations",
        verifier_owner="tournament_scheduler.participation_targets",
        mutation_providers=("intra_club_distribution_repair",),
        evidence_projection=("verify_candidate.participation_club_pools", "rules_model club_participation_fairness"),
        tests=("tests/test_participation_targets.py", "tests/test_intra_club_distribution.py"),
        finding_codes=("intra_club_participation_distribution",),
    ),
    RuleEntry(
        id="home_representation",
        classification=SOFT_OBJECTIVE,
        meaning=(
            "When a multi-team club hosts a tournament, the club's sibling teams take turns "
            "representing it. A spread of 0-1 is balanced; hosting coverage/balance is unchanged "
            "by which sibling shows up."
        ),
        canonical_owner="tournament_scheduler.home_representation",
        input_source="candidate host club + tournament roster",
        verifier_owner="tournament_scheduler.home_representation",
        mutation_providers=("home_representation_repair",),
        evidence_projection=("score_candidate.home_representation.*", "rules_model"),
        tests=("tests/test_home_representation.py",),
        finding_codes=("home_representation", "home_representation_skew"),
        score_paths=(
            "home_representation.max_material_spread",
            "home_representation.material_skew_pool_count",
        ),
    ),
    RuleEntry(
        id="opponent_repetition",
        classification=SOFT_OBJECTIVE,
        meaning="Teams should meet diverse opponents; repeated pairings beyond the configured expectation are minimized.",
        canonical_owner="tournament_scheduler.quality_objectives",
        input_source="candidate generated games",
        verifier_owner="tournament_scheduler.planning_contract.score_candidate",
        evidence_projection=("score_candidate.opponent_diversity.*", "rules_model pairwise_matchups"),
        tests=("tests/test_quality_objectives.py",),
        score_paths=(
            "opponent_diversity.unique_pairs",
            "opponent_diversity.pairwise_novelty",
            "opponent_diversity.max_pair_repeat",
            "opponent_diversity.pairs_meeting_3_plus",
        ),
    ),
    RuleEntry(
        id="inter_club_diversity",
        classification=SOFT_OBJECTIVE,
        meaning="Tournaments should mix teams across clubs rather than concentrating same-club matchups.",
        canonical_owner="tournament_scheduler.quality_objectives",
        input_source="candidate generated games",
        verifier_owner="tournament_scheduler.planning_contract.score_candidate",
        evidence_projection=("score_candidate.opponent_diversity.inter_club_diversity",),
        tests=("tests/test_quality_objectives.py",),
        score_paths=(
            "opponent_diversity.inter_club_diversity",
            "opponent_diversity.same_club_pairing_count",
            "opponent_diversity.max_same_club_teams_per_tournament",
            "opponent_diversity.club_count_excess_over_2",
            "opponent_diversity.tournaments_with_3plus_same_club",
        ),
    ),
    RuleEntry(
        id="temporal_spacing",
        classification=SOFT_OBJECTIVE,
        meaning="A team's tournaments should be spaced sensibly; very short turnaround gaps are minimized.",
        canonical_owner="tournament_scheduler.team_schedule_quality",
        input_source="candidate tournament dates per team",
        verifier_owner="tournament_scheduler.planning_contract.score_candidate",
        evidence_projection=("score_candidate.turnaround.*", "rules_model"),
        tests=("tests/test_team_schedule_quality.py", "tests/test_fairness_temporal.py"),
        score_paths=(
            "turnaround.min_turnaround_days",
            "turnaround.gaps_under_days.7",
            "turnaround.gaps_under_days.14",
        ),
        finding_codes=("temporal_clustering",),
    ),
    RuleEntry(
        id="temporal_coverage",
        classification=SOFT_OBJECTIVE,
        meaning="A team's tournaments should cover the season rather than cluster in one stretch.",
        canonical_owner="tournament_scheduler.temporal_coverage",
        input_source="candidate tournament dates per team",
        verifier_owner="tournament_scheduler.planning_contract.score_candidate",
        evidence_projection=("score_candidate.temporal.*",),
        tests=("tests/test_temporal_coverage.py",),
        score_paths=("temporal.max_gap_days", "temporal.offenders_count"),
    ),
    RuleEntry(
        id="travel_distance",
        classification=SOFT_OBJECTIVE,
        meaning="Estimated team travel between home and away arenas should be kept reasonable.",
        canonical_owner="tournament_scheduler.club_distances",
        input_source="configured club distances + candidate placements",
        verifier_owner="tournament_scheduler.planning_contract.score_candidate",
        evidence_projection=("score_candidate travel evidence", "rules_model travel detail rows"),
        tests=("tests/test_club_distances.py",),
        score_paths=("travel",),
    ),
)


# ---------------------------------------------------------------------------
# Operator decisions
# ---------------------------------------------------------------------------

_DECISIONS: tuple[RuleEntry, ...] = (
    RuleEntry(
        id="shared_host_choice",
        classification=OPERATOR_DECISION,
        meaning=(
            "A joint registration such as Kongsberg/Tønsberg can genuinely require a "
            "contextual choice of which constituent physically carries the shared hosting "
            "responsibility. This is the limited case where agent/operator judgment over "
            "deterministic registration facts is legitimate."
        ),
        canonical_owner="tournament_scheduler.shared_host_decision",
        input_source="joint-registration club labels + hosting_responsibility facts",
        verifier_owner="tournament_scheduler.hosting_coverage.shared_registration_facts",
        mutation_providers=("shared host decision action",),
        evidence_projection=("plan.shared_host_decisions", "rules_model shared_host_* decisions"),
        tests=("tests/test_shared_host_decision.py",),
        depends_on=("hosting_age_group_coverage", "hosting_responsibility"),
    ),
    RuleEntry(
        id="operator_waiver",
        classification=OPERATOR_DECISION,
        meaning=(
            "An authorized operator may explicitly waive a classified hard planning rule for a "
            "precise scope. The planner/agent may suggest but never create or broaden a waiver; "
            "waived violations stay visible and downgrade publication readiness."
        ),
        canonical_owner="tournament_scheduler.operator_waivers",
        input_source="canonical operator waiver records",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("waiver create/revoke",),
        evidence_projection=("verify_candidate.waived_violations", "publication_readiness operator_waivers"),
        tests=("tests/test_operator_waivers.py",),
        finding_codes=("operator_waivers",),
    ),
    RuleEntry(
        id="operator_accepted_participation_deviation",
        classification=OPERATOR_DECISION,
        meaning=(
            "An operator may deliberately live with a bounded participation deviation. The "
            "acceptance never changes the target or the schedule, is bound to the deviation "
            "scope/magnitude and becomes stale when the target changes or the deviation worsens."
        ),
        canonical_owner="tournament_scheduler.participation_targets",
        input_source="canonical decisions.json operator acceptances",
        verifier_owner="tournament_scheduler.participation_targets.evidence_covers_deviation",
        evidence_projection=("season findings", "participation avoidability classification"),
        tests=("tests/test_participation_targets.py", "tests/test_season_maintenance.py"),
    ),
    RuleEntry(
        id="manual_placement_opt_in",
        classification=OPERATOR_DECISION,
        meaning=(
            "An automatic canonical mutation may not newly introduce a manual/unt trusted-calendar "
            "placement or host-confirmation dependency. A deliberate provisional placement "
            "requires the exact explicit opt-in and is audited."
        ),
        canonical_owner="tournament_scheduler.operational_acceptability",
        input_source="current canonical baseline + candidate placements",
        verifier_owner="tournament_scheduler.operational_acceptability",
        evidence_projection=("operational_acceptable / requires_operational_opt_in", "decisions.json audit"),
        tests=("tests/test_operational_acceptability.py",),
        finding_codes=("manual_placement",),
    ),
    RuleEntry(
        id="guest_slot_reservation",
        classification=OPERATOR_DECISION,
        meaning="The operator reserves guest places as intent; the selection among legal alternatives is recorded as a decision.",
        canonical_owner="tournament_scheduler.guest_slots",
        input_source="operator guest intent + season guest-candidates",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=("guest-reserve", "guest-fill", "guest-release"),
        evidence_projection=("season guest-report", "season_plan.html"),
        tests=("tests/test_guest_slots.py",),
    ),
    RuleEntry(
        id="season_quality_baseline",
        classification=OPERATOR_DECISION,
        meaning=(
            "An operator may accept the current set and severity of non-hard findings as the "
            "season's regression reference. The baseline records stable finding ids and "
            "measurements (never aggregate counts alone), so later maintenance classifies fresh "
            "findings as known/improved/resolved/regressed/new. It never suppresses hard "
            "verification failures and never changes the schedule."
        ),
        canonical_owner="tournament_scheduler.season_baseline",
        input_source="canonical decisions.json season baseline + fresh season findings",
        verifier_owner="tournament_scheduler.planning_contract.verify_candidate",
        mutation_providers=(
            "season baseline create",
            "season baseline advance",
            "season baseline replace",
        ),
        evidence_projection=(
            "season findings baseline comparison",
            "season/<season>/decisions.json season_baseline",
            "export/evidence_bundle.json season_baseline",
        ),
        tests=("tests/test_season_baseline.py",),
    ),
    RuleEntry(
        id="operator_banned_date",
        classification=OPERATOR_DECISION,
        meaning="The operator may ban a global date as unusable for every tournament; the ban is durable policy, not a one-off.",
        canonical_owner="tournament_scheduler.canonical_banned_dates",
        input_source="canonical decisions.json banned dates",
        mutation_providers=("season ban-date", "season unban-date"),
        evidence_projection=("season banned-dates --json",),
        tests=("tests/test_canonical_banned_dates.py",),
    ),
    RuleEntry(
        id="operator_holiday_date_exception",
        classification=OPERATOR_DECISION,
        meaning=(
            "The operator may allow one date that is excluded only by the derived holiday/date policy; "
            "the exception does not override explicit banned dates."
        ),
        canonical_owner="tournament_scheduler.canonical_holiday_exceptions",
        input_source="canonical decisions.json holiday_date_exceptions",
        mutation_providers=("season allow-holiday-date", "season disallow-holiday-date"),
        evidence_projection=("season holiday-date-exceptions --json",),
        tests=("tests/test_holiday_date_policy.py",),
    ),
)


# ---------------------------------------------------------------------------
# Fact / evidence semantics
# ---------------------------------------------------------------------------

_EVIDENCE: tuple[RuleEntry, ...] = (
    RuleEntry(
        id="calendar_source_trust",
        classification=FACT_EVIDENCE_SEMANTIC,
        meaning=(
            "A per-club calendar is only trustworthy when the configured source produced usable "
            "evidence this run. A 'known' status does not prove every candidate time is free; "
            "an untrusted calendar yields manual placement, never a silent pass."
        ),
        canonical_owner="tournament_scheduler.pipeline.source_health",
        input_source="Stage 2 source/calendar evidence + cache provenance",
        verifier_owner="tournament_scheduler.calendar_availability",
        evidence_projection=("club_calendar_status", "manual_calendar_placements", "rules_model calendar_trust_by_host"),
        tests=("tests/test_source_health.py", "tests/test_calendar_availability.py"),
        finding_codes=("manual_calendar_placements",),
    ),
    RuleEntry(
        id="calendar_interval_classification",
        classification=FACT_EVIDENCE_SEMANTIC,
        meaning=(
            "A host's calendar interval is classified fixed_busy (hard external conflict), "
            "movable_busy (host-controlled, requires host confirmation) or unclassified. The "
            "classification is policy evidence, not a scheduling rule in itself. A promoted season may also carry an explicit event-to-tournament booking association overlay that makes one fixed event non-conflicting only for the associated tournament."
        ),
        canonical_owner="tournament_scheduler.calendar_availability / tournament_scheduler.calendar_bookings",
        input_source="configured/stage-2 calendar intervals + decisions.json calendar_booking_associations",
        verifier_owner="tournament_scheduler.planning_contract.external_calendar_conflict",
        mutation_providers=("movable_capacity_repair", "CanonicalSeasonService.confirm_calendar_booking"),
        evidence_projection=("movable_allocations_used", "manual_external_conflict_placements", "calendar_booking_associations"),
        tests=("tests/test_calendar_availability.py", "tests/test_movable_capacity_repair.py", "tests/test_approval_lifecycle.py"),
        finding_codes=(
            "external_calendar_conflicts",
            "movable_host_confirmation_required",
            "movable_capacity_opportunity",
            "stale_calendar_booking_association",
        ),
    ),
    RuleEntry(
        id="club_pool_classification",
        classification=FACT_EVIDENCE_SEMANTIC,
        meaning=(
            "For each club x age group x scope, the registered teams' aggregate target/actual is "
            "classified as complete / intra_club_distribution / minor|material_club_pool_shortfall "
            "/ single_team_deviation / over_target. The classification is the single source of "
            "truth for whether a residual is a genuine player-pool shortage."
        ),
        canonical_owner="tournament_scheduler.participation_targets",
        input_source="planning_problem club pools + candidate participations",
        evidence_projection=("verify_candidate.participation_club_pools", "rules_model club_participation_fairness"),
        tests=("tests/test_participation_targets.py",),
    ),
    RuleEntry(
        id="search_coverage_evidence",
        classification=FACT_EVIDENCE_SEMANTIC,
        meaning=(
            "A bounded repair/search result records what was actually tried: option_available, "
            "search_incomplete, bounded_search_exhausted, proven_infeasible or inapplicable, "
            "bound to a search capability/version. Zero options means only that this search "
            "found none, never proof of global infeasibility."
        ),
        canonical_owner="tournament_scheduler.search_capability",
        input_source="repair/search provider run results",
        evidence_projection=("season findings search_coverage", "stage3 converge coverage"),
        tests=("tests/test_search_neighborhood_repair.py",),
        finding_codes=("bounded_search_exhausted", "proven_infeasible", "search_incomplete"),
    ),
    RuleEntry(
        id="approval_lifecycle",
        classification=FACT_EVIDENCE_SEMANTIC,
        meaning=(
            "Operator approvals/locks live in decisions.json separately from schedule facts. A "
            "protected placement whose fingerprint changed becomes a stale approval whose lock is "
            "dropped; an approval whose tournament is gone becomes orphaned. Both require re-review."
        ),
        canonical_owner="tournament_scheduler.canonical_baseline",
        input_source="canonical decisions.json approvals",
        evidence_projection=("verify_candidate.stale_approvals", "verify_candidate.orphaned_approvals"),
        tests=("tests/test_approval_lifecycle.py", "tests/test_canonical_baseline.py"),
        verifier_codes=("stale_approval", "orphaned_approval"),
        finding_codes=("stale_approvals", "orphaned_approvals"),
    ),
    RuleEntry(
        id="verification_completeness",
        classification=FACT_EVIDENCE_SEMANTIC,
        meaning=(
            "Verification reports which checks were skipped because their required input was "
            "unavailable. An incomplete verification is never a pass."
        ),
        canonical_owner="tournament_scheduler.planning_contract",
        input_source="available planning problem fields",
        evidence_projection=("verify_candidate.skipped", "publication_readiness incomplete_verification"),
        tests=("tests/test_planning_contract.py",),
        finding_codes=("incomplete_verification",),
    ),
)


RULE_CATALOG: tuple[RuleEntry, ...] = _HARD + _OBLIGATIONS + _SOFT + _DECISIONS + _EVIDENCE

CATALOG_BY_ID: dict[str, RuleEntry] = {entry.id: entry for entry in RULE_CATALOG}

# Link the per-run Rules ("Regler") model entries to their catalog identity.
# The Rules model remains the per-run *result* projection; this mapping lets its
# renderer reuse the one catalog instead of maintaining a second semantic list.
# ``metric_`` prefixes are stripped before lookup.
RULES_MODEL_ID_TO_RULE_ID: dict[str, str] = {
    "arena_day_collisions": "arena_interval_non_overlap",
    "age_group_exact_match": "team_age_group_exact",
    "no_same_date_double_participation": "team_unique_per_date",
    "date_within_planning_window": "date_within_window",
    "holiday_dates_not_used": "holiday_date_admissible",
    "banned_dates_not_used": "banned_dates_not_used",
    "excluded_host_clubs_not_used": "excluded_host_club_not_used",
    "locked_dates_preserved": "locked_date_preserved",
    "pinned_tournaments_preserved": "pinned_tournament_preserved",
    "calendar_trust_by_host": "calendar_source_trust",
    "registered_teams_only": "registered_teams_only",
    "tournament_capacity": "tournament_capacity",
    "hosting_deviation": "hosting_proportional_balance",
    "hosting_obligation_coverage": "hosting_age_group_coverage",
    "tournament_placement_shortfall": "tournament_placement_obligation",
    "external_calendar_conflicts": "calendar_interval_classification",
    "participation_target_deviation": "participation_target",
    "participation_shortfalls": "participation_target",
    "participation_targets_by_age_group": "participation_target",
    "club_participation_fairness": "intra_club_participation_distribution",
    "pairwise_matchups": "opponent_repetition",
    "opponent_diversity": "opponent_repetition",
    "team_temporal_coverage": "temporal_coverage",
    "travel_distance": "travel_distance",
    "same_weekend_club_load": "temporal_spacing",
    "consecutive_weekend_club_load": "temporal_spacing",
}


def catalog_id_for_rules_model_entry(entry_id: str) -> str | None:
    """Return the catalog rule ID for a per-run Rules-model entry id, if known."""
    key = entry_id[7:] if entry_id.startswith("metric_") else entry_id
    return RULES_MODEL_ID_TO_RULE_ID.get(key)


def rule_semantics(rule_id: str) -> dict[str, Any] | None:
    """Return the canonical, render-ready semantics for one catalog ID.

    This is the projection the Rules/audit surfaces consume so their semantic
    labels (classification, meaning, owner, precedence) are derived from the
    one catalog instead of a hand-maintained parallel list. Run-specific
    results (status, ``ok``, detail rows) remain owned by the per-run model.
    """
    entry = CATALOG_BY_ID.get(rule_id)
    if entry is None:
        return None
    return {
        "id": entry.id,
        "classification": entry.classification,
        "classification_label": CLASSIFICATION_LABELS.get(entry.classification, entry.classification),
        "meaning": entry.meaning,
        "canonical_owner": entry.canonical_owner,
        "status": entry.status,
        "waivable": entry.waivable,
        "precedes": list(entry.precedes),
        "depends_on": list(entry.depends_on),
    }


def rules_model_semantics(entry_id: str) -> dict[str, Any] | None:
    """Canonical semantics for a per-run Rules-model entry id, if registered."""
    rule_id = catalog_id_for_rules_model_entry(entry_id)
    if rule_id is None:
        return None
    return rule_semantics(rule_id)


def active_catalog_references() -> tuple[dict[str, Any], ...]:
    """Every active catalog entry projected for reference rendering."""
    references: list[dict[str, Any]] = []
    for entry in RULE_CATALOG:
        if entry.status != STATUS_ACTIVE:
            continue
        semantics = rule_semantics(entry.id)
        if semantics is not None:
            references.append(semantics)
    return tuple(references)


def entries_by_classification(classification: str) -> tuple[RuleEntry, ...]:
    """Return every catalog entry of one primary *classification*."""
    return tuple(entry for entry in RULE_CATALOG if entry.classification == classification)


def rule_id_for_verifier_code(code: str) -> str | None:
    """Map a verifier/publication violation *code* to its stable catalog ID."""
    for entry in RULE_CATALOG:
        if code in entry.verifier_codes:
            return entry.id
    return None


def rule_id_for_finding_code(code: str) -> str | None:
    """Map a finding/search coverage code to its stable catalog ID."""
    for entry in RULE_CATALOG:
        if code in entry.finding_codes:
            return entry.id
    return None


def rule_id_for_score_path(path: str) -> str | None:
    """Map a ``score_candidate`` score path to its stable catalog objective ID."""
    for entry in RULE_CATALOG:
        if path in entry.score_paths:
            return entry.id
    return None


def annotate_findings(findings: list[dict[str, Any]]) -> None:
    """Attach the stable ``rule_id`` to each finding dict in place.

    Called at the season-findings projection boundary only. A finding code is
    resolved first as a finding code and then as a verifier code, so a finding
    that re-surfaces a raw verifier violation keeps the same identity as the
    violation it came from. Internal helper code does not need to know the
    full catalog; identity belongs at the semantic/application boundary.
    """
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if finding.get("rule_id"):
            continue
        code = finding.get("code")
        if not isinstance(code, str):
            continue
        rule_id = rule_id_for_finding_code(code) or rule_id_for_verifier_code(code)
        if rule_id:
            finding["rule_id"] = rule_id


def annotate_violations(violations: list[dict[str, Any]]) -> None:
    """Attach the stable ``rule_id`` to each violation dict in place.

    Called at the canonical verifier boundary only. Internal helper code does
    not need to know the full catalog; identity belongs at the semantic/
    application boundary.
    """
    for violation in violations:
        if not isinstance(violation, dict):
            continue
        code = violation.get("code")
        if not isinstance(code, str):
            continue
        rule_id = rule_id_for_verifier_code(code)
        if rule_id and "rule_id" not in violation:
            violation["rule_id"] = rule_id


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


def precedence_pairs() -> tuple[tuple[str, str], ...]:
    """Return ``(higher_priority_id, lower_priority_id)`` pairs declared by ``precedes``."""
    return tuple(
        (entry.id, lower)
        for entry in RULE_CATALOG
        for lower in entry.precedes
    )


def outranks(higher: str, lower: str) -> bool:
    """True when *higher* transitively precedes *lower* in the catalog.

    This is intentionally a tiny transitive check over declared edges, not a
    rule engine: a caller that must not trade a higher-priority obligation for
    a lower-priority metric can use it as a guard, while the actual decisions
    stay in the deterministic code that owns each rule.
    """
    visited: set[str] = set()
    pending = [higher]
    while pending:
        current = pending.pop()
        if current == lower:
            return True
        if current in visited:
            continue
        visited.add(current)
        entry = CATALOG_BY_ID.get(current)
        if entry is None:
            continue
        pending.extend(entry.precedes)
    return False


# ---------------------------------------------------------------------------
# Validation (consumed by tests/test_rule_catalog.py)
# ---------------------------------------------------------------------------


def _module_importable(dotted: str) -> bool:
    """True when *dotted* resolves to an importable module or module.attr.

    Tries the full string as a module first, then progressively trims trailing
    components (so ``pkg.mod.func`` resolves via ``pkg.mod``).
    """
    parts = dotted.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        try:
            importlib.import_module(candidate)
            return True
        except Exception:
            continue
    return False


def _importable_attribute(dotted: str) -> bool:
    """True when *dotted* names an existing attribute (or an importable module)."""
    if "." not in dotted:
        return _module_importable(dotted)
    module_name, _, attribute = dotted.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return _module_importable(dotted)
    return hasattr(module, attribute)


def validate_catalog(repo_root: Path | None = None) -> list[str]:
    """Return a list of conformance problems (empty when the catalog is sound).

    Checks duplicate/invalid identity, unknown classifications/statuses,
    dangling precedence references, precedence cycles, unimportable owners and
    missing test references.
    """
    problems: list[str] = []
    root = repo_root or Path(__file__).resolve().parent.parent

    seen: dict[str, RuleEntry] = {}
    for entry in RULE_CATALOG:
        if entry.id in seen:
            problems.append(f"duplicate catalog id: {entry.id}")
            continue
        seen[entry.id] = entry
        if entry.classification not in CLASSIFICATIONS:
            problems.append(f"{entry.id}: unknown classification {entry.classification!r}")
        if entry.status not in STATUSES:
            problems.append(f"{entry.id}: unknown status {entry.status!r}")
        if not entry.meaning:
            problems.append(f"{entry.id}: missing meaning")
        if not entry.canonical_owner:
            problems.append(f"{entry.id}: missing canonical owner")
        elif not _module_importable(entry.canonical_owner):
            problems.append(f"{entry.id}: canonical owner not importable: {entry.canonical_owner}")
        if entry.verifier_owner and not _importable_attribute(entry.verifier_owner):
            problems.append(f"{entry.id}: verifier owner not importable: {entry.verifier_owner}")
        if not entry.tests:
            problems.append(f"{entry.id}: no test references")
        for test_path in entry.tests:
            if not (root / test_path).exists():
                problems.append(f"{entry.id}: missing test reference: {test_path}")

    # Duplicate verifier-code registration would let two rules claim one code.
    code_owner: dict[str, str] = {}
    for entry in RULE_CATALOG:
        for code in entry.verifier_codes:
            if code in code_owner and code_owner[code] != entry.id:
                problems.append(
                    f"verifier code {code!r} registered by both {code_owner[code]} and {entry.id}"
                )
            code_owner[code] = entry.id

    # Precedence / dependency references must exist and precedence must be acyclic.
    for entry in RULE_CATALOG:
        for reference in (*entry.precedes, *entry.depends_on):
            if reference not in CATALOG_BY_ID:
                problems.append(f"{entry.id}: unknown precedence/dependency reference {reference!r}")
            if reference == entry.id:
                problems.append(f"{entry.id}: references itself in precedence/dependency")

    for entry in RULE_CATALOG:
        for lower in entry.precedes:
            if lower in CATALOG_BY_ID and outranks(lower, entry.id):
                problems.append(f"precedence cycle between {entry.id} and {lower}")

    # Rules-model linkage must point at registered catalog IDs.
    for model_id, rule_id in RULES_MODEL_ID_TO_RULE_ID.items():
        if rule_id not in CATALOG_BY_ID:
            problems.append(f"rules-model id {model_id!r} maps to unknown rule {rule_id!r}")

    return problems


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _md_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _code_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "—"


def _owner_module(dotted: str) -> str:
    return f"`{dotted}`"


def _summary_table(entries: tuple[RuleEntry, ...]) -> list[str]:
    lines = [
        "| Rule ID | Meaning | Canonical owner | Verifier / measurement | Tests |",
        "|---|---|---|---|---|",
    ]
    for entry in entries:
        lines.append(
            "| `{id}` | {meaning} | {owner} | {verifier} | {tests} |".format(
                id=entry.id,
                meaning=_md_escape(entry.meaning),
                owner=_owner_module(entry.canonical_owner),
                verifier=_code_list((entry.verifier_owner,)) if entry.verifier_owner else "—",
                tests=_code_list(entry.tests),
            )
        )
    return lines


def _entry_detail(entry: RuleEntry) -> list[str]:
    classification = CLASSIFICATION_LABELS.get(entry.classification, entry.classification)
    lines = [
        f"### `{entry.id}`",
        "",
        f"`{classification}` · status `{entry.status}` · "
        f"operator-waivable: {'yes' if entry.waivable else 'no (structural)'}  ",
        f"**Meaning:** {entry.meaning}  ",
        f"**Canonical owner:** `{entry.canonical_owner}`  ",
        f"**Input / fact source:** {entry.input_source or '—'}  ",
        f"**Verifier / measurement:** {('`' + entry.verifier_owner + '`') if entry.verifier_owner else '—'}  ",
        "**Codes:** "
        f"verifier {_code_list(entry.verifier_codes)} · "
        f"finding {_code_list(entry.finding_codes)} · "
        f"score {_code_list(entry.score_paths)}  ",
        "**Providers:** "
        f"mutation {_code_list(entry.mutation_providers)} · "
        f"search {_code_list(entry.search_providers)}  ",
        "**Evidence / report:** " + _code_list(entry.evidence_projection) + "  ",
        "**Tests:** " + _code_list(entry.tests) + "  ",
        "**Precedence:** "
        f"precedes {_code_list(entry.precedes)} · "
        f"depends on {_code_list(entry.depends_on)}",
        "",
    ]
    return lines


def render_markdown() -> str:
    """Render the canonical agent-facing ownership table."""
    lines: list[str] = [
        "# Scheduling rule catalog",
        "",
        "> **Generated** from `tournament_scheduler/rule_catalog.py` by",
        "> `python3 scripts/render-rule-catalog.py`. Do not edit by hand.",
        "",
        "This is the one compact, searchable answer to *what scheduling semantics exist, who",
        "owns each one, and which semantics take precedence when they interact*. It is metadata",
        "and navigation only: the deterministic implementations named by each entry remain the",
        "executable truth.",
        "",
        "Search this page for a rule ID (for example `host_representation`,",
        "`hosting_age_group_coverage`, `hosting_proportional_balance` or",
        "`arena_interval_non_overlap`) to find the owning facade, its verifier and its tests",
        "before editing code.",
        "",
        "## Stable identity in deterministic outputs",
        "",
        "The catalog is the one source of rule/objective identity, and the semantic/application",
        "boundaries reuse it instead of re-deriving prose:",
        "",
        "* verifier violations carry `rule_id` (attached by `annotate_violations`);",
        "* `season findings` carry `rule_id` per finding plus a `counts_by_rule_id` summary",
        "  (attached by `annotate_findings`);",
        "* quality/score metrics in `compare_quality_scores` carry `objective_id` for every",
        "  registered score path;",
        "* the Regler/rules model carries `catalog_id` per per-run row.",
        "",
        "`rule_id_for_verifier_code`, `rule_id_for_finding_code` and `rule_id_for_score_path`",
        "own the lookups; callers must not hard-code a second mapping.",
        "",
        "## How to use this catalog",
        "",
        "Before introducing a new rule because of a production failure, determine which case it is:",
        "",
        "1. **Does a catalog entry already describe the intended semantic?** Fix the owning",
        "   implementation/conformance; do not add another rule.",
        "2. **Is the semantic correct but no legal repair is exposed?** Add an action/search",
        "   capability under the existing `local_repair_options` boundary, not another hard rule.",
        "3. **Is the candidate correct but state/resume is wrong?** Route to the stage-3",
        "   session/lifecycle owner (not a hockey rule).",
        "4. **Is the candidate correct but evidence/reporting is wrong?** Fix the evidence/report",
        "   projection.",
        "5. **Is this genuinely new operator policy?** Add/catalog it deliberately with",
        "   classification, source and generic tests.",
        "",
        "Production clubs/dates remain regression fixtures, never rule identities.",
        "",
        "For hosting anomalies specifically, check in this order:",
        "",
        "```text",
        "Is club x age-group coverage satisfied or structurally impossible?",
        "  -> Is assigned responsibility consistent with coverage + proportional target?",
        "     -> Was responsibility preserved through placement/repair/search?",
        "        -> Only then inspect lower-priority balance/placement quality.",
        "```",
        "",
        "## Classification vocabulary",
        "",
        "| Classification | Meaning |",
        "|---|---|",
        "| `hard_constraint` | Legality boundary; a violation blocks validity (waivable only where declared). |",
        "| `operational_obligation` | Must be satisfied or surfaced as unresolved manual work. |",
        "| `soft_objective` | Optimized, but never justifies regressing a higher-priority obligation. |",
        "| `operator_decision` | A deliberate operator/agent choice among valid alternatives. |",
        "| `fact_evidence_semantic` | A classification/evidence fact feeding rules, not a rule itself. |",
        "",
        "## Precedence",
        "",
        "The catalog declares only the precedence that actually matters. For hosting:",
        "",
        "```text",
        "hard legality / host_representation",
        "        -> hosting_age_group_coverage",
        "           -> hosting_proportional_balance",
        "              -> placement convenience / calendar quality",
        "```",
        "",
        "and responsibility preservation applies once a target is assigned:",
        "",
        "```text",
        "hosting target -> hosting_responsibility -> placement / repair / search",
        "```",
        "",
        "An agent must not compare two candidates solely by a lower-priority metric when one",
        "candidate regresses a higher-priority operational obligation.",
        "",
        "| Higher priority | Must precede |",
        "|---|---|",
    ]
    pairs = precedence_pairs()
    if pairs:
        for higher, lower in pairs:
            lines.append(f"| `{higher}` | `{lower}` |")
    else:
        lines.append("| — | — |")

    sections = [
        ("Hard constraints", HARD_CONSTRAINT),
        ("Operational obligations", OPERATIONAL_OBLIGATION),
        ("Soft objectives", SOFT_OBJECTIVE),
        ("Operator decisions", OPERATOR_DECISION),
        ("Fact / evidence semantics", FACT_EVIDENCE_SEMANTIC),
    ]
    for title, classification in sections:
        lines.extend(["", f"## {title}", ""])
        lines.extend(_summary_table(entries_by_classification(classification)))

    lines.extend(["", "## Stable ID map", "", "| Emitted code / score path | Rule / objective ID |", "|---|---|"])
    for entry in RULE_CATALOG:
        for code in entry.verifier_codes:
            lines.append(f"| `{code}` (verifier) | `{entry.id}` |")
    for entry in RULE_CATALOG:
        for code in entry.finding_codes:
            lines.append(f"| `{code}` (finding) | `{entry.id}` |")
    for entry in RULE_CATALOG:
        for path in entry.score_paths:
            lines.append(f"| `{path}` (score) | `{entry.id}` |")
    lines.append("")

    lines.extend(["## Entry details", ""])
    for entry in RULE_CATALOG:
        lines.extend(_entry_detail(entry))

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write the generated ownership table; ``--check`` verifies it is current."""
    import argparse

    parser = argparse.ArgumentParser(description="Render docs/architecture/rule-catalog.md")
    parser.add_argument("--check", action="store_true", help="fail if the committed doc is stale")
    parser.add_argument("--root", default=None, help="repository root")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    target = root / CATALOG_DOC_PATH
    rendered = render_markdown()
    if args.check:
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if existing != rendered:
            print(f"{CATALOG_DOC_PATH} is stale; run scripts/render-rule-catalog.py")
            return 1
        print(f"{CATALOG_DOC_PATH} is current")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    print(f"Wrote {CATALOG_DOC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())  # pragma: no cover
