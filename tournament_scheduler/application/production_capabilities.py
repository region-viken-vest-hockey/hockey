"""Production-maintenance capability navigation map.

This module is intentionally metadata-only. It gives operators, agents and code
reviewers a compact starting point for published-season maintenance work: which
stable public application boundary to use, which focused implementation slice to
load next, and which tests characterize the behavior. It is **not** a business
rule engine and it must not duplicate verifier, evidence or publication policy.

Deterministic feasibility and hard validity remain owned by repository verifiers (including the planning/final verification contracts and solver-backed capabilities). Semantic audit is residual second-pass judgment over repository-produced evidence; it is not a competing feasibility engine and cannot override verifier results.\n\nThe common mutation contract for schedule/decision writes remains
``CanonicalSeasonService`` plus ``application.canonical_season.lifecycle`` and
``application.canonical_season.shared``. Capability entries below point at that
boundary instead of reconstructing their own verification or persistence path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CapabilityStatus = Literal["active", "via_batch"]
BoundaryKind = Literal["canonical", "delivery"]


@dataclass(frozen=True)
class ProductionCapability:
    """Navigation metadata for one cohesive production-maintenance capability."""

    key: str
    tasks: tuple[str, ...]
    public_api: tuple[str, ...]
    boundary: BoundaryKind
    implementation_owner: str
    invariants: tuple[str, ...]
    focused_tests: tuple[str, ...]
    status: CapabilityStatus = "active"


_COMMON_MUTATION_CONTRACT = (
    "CanonicalSeasonService is the public application facade for promoted-season writes.",
    "application/canonical_season/lifecycle.py owns load/verify/reconcile/history/revision/atomic commit.",
    "application/canonical_season/shared.py owns shared effective canonical readers/projections.",
    "Rejected mutations leave canonical schedule and decisions unchanged unless a focused ADR 0005 evidence path explicitly records rejected evidence.",
    "Publication/export authorization is separate from accepting a canonical mutation.",
    "Deterministic feasibility/hard-validity findings come from repository verifier owners; semantic audit cannot replace or override them.",
)


PRODUCTION_CAPABILITIES: tuple[ProductionCapability, ...] = (
    ProductionCapability(
        key="calendar_evidence",
        tasks=("refresh calendars", "reconcile booking evidence", "assess booking status"),
        boundary="canonical",
        public_api=(
            "CanonicalSeasonService.refresh_calendars",
            "CanonicalSeasonService.reconcile_calendar_bookings",
            "CanonicalSeasonService.calendar_booking_assessment",
            "CanonicalSeasonService.calendar_booking_findings",
        ),
        implementation_owner="tournament_scheduler/application/canonical_season/calendars/",
        invariants=(
            *_COMMON_MUTATION_CONTRACT,
            "Calendar overlap is occupancy evidence, not booking confirmation.",
            "Refresh/reconciliation never moves tournaments or edits rosters as a side effect.",
        ),
        focused_tests=(
            "tests/test_refresh_calendar_evidence.py",
            "tests/test_booking_assessment.py",
            "tests/test_operational_booking_state.py",
        ),
    ),
    ProductionCapability(
        key="booking_confirmation",
        tasks=("confirm scraped booking", "release booking association", "record manual booking assertion"),
        boundary="canonical",
        public_api=(
            "CanonicalSeasonService.calendar_booking_candidates",
            "CanonicalSeasonService.confirm_calendar_booking",
            "CanonicalSeasonService.release_calendar_booking",
            "CanonicalSeasonService.set_manual_booking_assertion",
            "CanonicalSeasonService.clear_manual_booking_assertion",
            "CanonicalSeasonService.booking_status_report",
        ),
        implementation_owner="tournament_scheduler/application/canonical_season/calendars/",
        invariants=(
            *_COMMON_MUTATION_CONTRACT,
            "Only an explicit association/assertion projects as confirmed booked or manually booked.",
            "Authoritative interval changes use the shared ice-time override/effective occupancy path.",
        ),
        focused_tests=(
            "tests/test_booking_assessment.py",
            "tests/test_operational_booking_state.py",
            "tests/test_canonical_baseline.py",
        ),
    ),
    ProductionCapability(
        key="request_constraints",
        tasks=("team unavailable", "minimum gap", "opponent avoidance", "global banned/holiday exception dates"),
        boundary="canonical",
        public_api=(
            "CanonicalSeasonService.request_constraint_report",
            "CanonicalSeasonService.add_request_constraint",
            "CanonicalSeasonService.release_request_constraints",
            "CanonicalSeasonService.add_banned_date",
            "CanonicalSeasonService.release_banned_dates",
            "CanonicalSeasonService.allow_holiday_date",
            "CanonicalSeasonService.disallow_holiday_dates",
        ),
        implementation_owner="tournament_scheduler/application/canonical_season/constraints.py",
        invariants=(
            *_COMMON_MUTATION_CONTRACT,
            "Recording a constraint is decision-only and may precede the repair that satisfies it.",
            "Every later schedule-changing mutation re-checks the complete active constraint set.",
        ),
        focused_tests=(
            "tests/test_request_constraints.py",
            "tests/test_canonical_banned_dates.py",
            "tests/test_canonical_batch_maintenance.py",
        ),
    ),
    ProductionCapability(
        key="placement",
        tasks=("move tournament", "normalize placement state", "apply verified repair candidate"),
        boundary="canonical",
        public_api=(
            "CanonicalSeasonService.move_tournament",
            "CanonicalSeasonService.normalize_placements",
            "CanonicalSeasonService.normalize_arena_identities",
            "CanonicalSeasonService.apply_candidate",
        ),
        implementation_owner="tournament_scheduler/application/canonical_season/placements.py",
        invariants=(
            *_COMMON_MUTATION_CONTRACT,
            "Approvals, placement locks and protected decisions are preserved unless the typed operation explicitly changes them.",
            "Planner search/replanning is not invoked for ordinary production moves.",
        ),
        focused_tests=(
            "tests/test_season_state.py",
            "tests/test_season_maintenance.py",
            "tests/test_canonical_batch_maintenance.py",
        ),
    ),
    ProductionCapability(
        key="participants",
        tasks=("swap participants", "replace participant", "remove/withdraw team", "release withdrawal"),
        boundary="canonical",
        public_api=(
            "CanonicalSeasonService.swap_participants",
            "CanonicalSeasonService.replace_participant",
            "CanonicalSeasonService.remove_participant",
            "CanonicalSeasonService.withdrawal_report",
            "CanonicalSeasonService.release_participation_withdrawals",
        ),
        implementation_owner="tournament_scheduler/application/canonical_season/roster.py + replacement.py + withdrawal.py",
        invariants=(
            *_COMMON_MUTATION_CONTRACT,
            "Roster mutations reproduce a typed scoped operation before sealed-season apply.",
            "Placement, host-confirmation metadata and unrelated tournaments cannot drift.",
        ),
        focused_tests=(
            "tests/test_participant_removal.py",
            "tests/test_participant_roster_repair.py",
            "tests/test_canonical_batch_maintenance.py",
        ),
    ),
    ProductionCapability(
        key="cancellation",
        tasks=("cancel one or more tournaments as part of an explicit batch maintenance request",),
        boundary="canonical",
        public_api=("CanonicalSeasonService.batch_maintenance",),
        implementation_owner="tournament_scheduler/application/canonical_season/batch.py",
        invariants=(
            *_COMMON_MUTATION_CONTRACT,
            "Cancellation is scoped, explicit and committed only after the final whole-season candidate passes the canonical gates.",
            "Active guest reservations must be released explicitly before cancellation.",
        ),
        focused_tests=("tests/test_canonical_batch_maintenance.py",),
        status="via_batch",
    ),
    ProductionCapability(
        key="audit_export_publication",
        tasks=("mark export fresh", "report publication evidence", "seal/reopen publication lifecycle"),
        boundary="delivery",
        public_api=(
            "CanonicalSeasonService.mark_export_fresh",
            "CanonicalSeasonService.publication_evidence_report",
            "CanonicalSeasonService.seal_published_season",
            "CanonicalSeasonService.verify_sealed_reconciliation",
            "CanonicalSeasonService.reopen_planning",
        ),
        implementation_owner="tournament_scheduler/application/canonical_season/publication.py + export_freshness.py + lifecycle_status.py",
        invariants=(
            *_COMMON_MUTATION_CONTRACT,
            "A fresh export or successful preflight is not public-write authorization.",
            "Sealed reconciliation replays authorized mutations rather than recomputing a plan.",
        ),
        focused_tests=(
            "tests/test_published_season_sealing.py",
            "tests/test_canonical_export_context.py",
        ),
    ),
)


CAPABILITIES_BY_KEY: dict[str, ProductionCapability] = {
    capability.key: capability for capability in PRODUCTION_CAPABILITIES
}


TASK_TO_CAPABILITY: dict[str, str] = {
    task: capability.key
    for capability in PRODUCTION_CAPABILITIES
    for task in capability.tasks
}


__all__ = [
    "CAPABILITIES_BY_KEY",
    "PRODUCTION_CAPABILITIES",
    "ProductionCapability",
    "TASK_TO_CAPABILITY",
]
