"""Production-maintenance capability navigation map.

This module is intentionally metadata-only. It gives operators, agents and code
reviewers a compact starting point for published-season maintenance work: which
stable public application boundary to use, which focused implementation slice to
load next, and which tests characterize the behavior. It is **not** a business
rule engine and it must not duplicate verifier, evidence or publication policy.

Deterministic feasibility and hard validity remain owned by repository verifiers (including the planning/final verification contracts and solver-backed capabilities). Semantic audit is residual second-pass judgment over repository-produced evidence; it is not a competing feasibility engine and cannot override verifier results.

Canonical mutation capabilities point at ``CanonicalSeasonService`` plus
``application.canonical_season.lifecycle`` and ``application.canonical_season.shared``.
Export, audit and publication capabilities declare their own authoritative public
boundary. They may consume exact revision-bound canonical state and evidence, but they
do not inherit the mutation facade merely because some current compatibility methods
live on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CapabilityStatus = Literal["active", "via_batch"]
BoundaryKind = Literal["canonical_mutation", "canonical_read", "export", "audit", "publication"]


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


_COMMON_PRODUCTION_INVARIANTS = (
    "Deterministic feasibility/hard-validity findings come from repository verifier owners; semantic audit cannot replace or override them.",
    "Publication/export authorization is separate from accepting a canonical mutation.",
    "Delivery capabilities consume exact revision-bound canonical state/evidence through their declared boundary rather than reconstructing a second verifier or evidence engine.",
)

_CANONICAL_READ_CONTRACT = (
    "application/canonical_season/shared.py owns shared effective canonical readers/projections.",
    *_COMMON_PRODUCTION_INVARIANTS,
)

_CANONICAL_MUTATION_CONTRACT = (
    "CanonicalSeasonService is the public application facade for promoted-season writes.",
    "application/canonical_season/lifecycle.py owns load/verify/reconcile/history/revision/atomic commit.",
    *_CANONICAL_READ_CONTRACT,
    "Rejected mutations leave canonical schedule and decisions unchanged unless a focused ADR 0005 evidence path explicitly records rejected evidence.",
    "Preview, verification, rejection, retry and successful canonical commits do not materialize export artifacts; callers must enter the explicit export boundary for delivery output.",
)

_EXPORT_CONTRACT = (
    "The export boundary materializes an already selected canonical revision and records immutable export lifecycle/evidence identity.",
    "Export projection guards compare generated artifacts with the authoritative canonical plan instead of becoming scheduling policy.",
    *_COMMON_PRODUCTION_INVARIANTS,
)

_AUDIT_CONTRACT = (
    "The audit boundary consumes repository-produced export evidence and records a residual semantic workflow verdict.",
    "Audit/convergence workflow state is revision/export-fingerprint bound and cannot satisfy a newer export.",
    *_COMMON_PRODUCTION_INVARIANTS,
)

_PUBLICATION_CONTRACT = (
    "The publication boundary publishes or seals an already exported, preflight-checked bundle; it never generates or mutates the schedule.",
    "A fresh export, audit pass or successful preflight is not public-write authorization.",
    "Sealed reconciliation replays authorized mutations rather than recomputing a plan.",
    *_COMMON_PRODUCTION_INVARIANTS,
)


PRODUCTION_CAPABILITIES: tuple[ProductionCapability, ...] = (
    ProductionCapability(
        key="calendar_evidence",
        tasks=("refresh calendars", "reconcile booking evidence", "assess booking status"),
        boundary="canonical_mutation",
        public_api=(
            "CanonicalSeasonService.refresh_calendars",
            "CanonicalSeasonService.reconcile_calendar_bookings",
            "CanonicalSeasonService.calendar_booking_assessment",
            "CanonicalSeasonService.calendar_booking_findings",
        ),
        implementation_owner="tournament_scheduler/application/canonical_season/calendars/",
        invariants=(
            *_CANONICAL_MUTATION_CONTRACT,
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
        boundary="canonical_mutation",
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
            *_CANONICAL_MUTATION_CONTRACT,
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
        boundary="canonical_mutation",
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
            *_CANONICAL_MUTATION_CONTRACT,
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
        key="season_inspection",
        tasks=(
            "inspect one tournament and roster",
            "inspect constraints for a team/tournament/date",
            "find and validate legal replacement candidates",
            "inspect approval/booking/provenance for one tournament",
        ),
        boundary="canonical_read",
        public_api=(
            "CanonicalSeasonService.tournament_inspection",
            "CanonicalSeasonService.constraint_inspection",
            "CanonicalSeasonService.replacement_candidates",
        ),
        implementation_owner="tournament_scheduler/application/canonical_season/inspection.py",
        invariants=(
            *_CANONICAL_READ_CONTRACT,
            "Read-only inspection never mutates canonical schedule or decisions and never invents evidence, approval or candidates.",
            "Replacement-candidate legality is evaluated through the same read-only replacement gates as the mutation dry-run, not re-derived by callers.",
        ),
        focused_tests=("tests/test_season_inspection.py",),
    ),
    ProductionCapability(
        key="placement",
        tasks=("move tournament", "normalize placement state", "apply verified repair candidate"),
        boundary="canonical_mutation",
        public_api=(
            "CanonicalSeasonService.move_tournament",
            "CanonicalSeasonService.normalize_placements",
            "CanonicalSeasonService.normalize_arena_identities",
            "CanonicalSeasonService.apply_candidate",
        ),
        implementation_owner="tournament_scheduler/application/canonical_season/placements.py",
        invariants=(
            *_CANONICAL_MUTATION_CONTRACT,
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
        boundary="canonical_mutation",
        public_api=(
            "CanonicalSeasonService.swap_participants",
            "CanonicalSeasonService.replace_participant",
            "CanonicalSeasonService.remove_participant",
            "CanonicalSeasonService.withdrawal_report",
            "CanonicalSeasonService.release_participation_withdrawals",
        ),
        implementation_owner="tournament_scheduler/application/canonical_season/roster.py + replacement.py + withdrawal.py",
        invariants=(
            *_CANONICAL_MUTATION_CONTRACT,
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
        boundary="canonical_mutation",
        public_api=("CanonicalSeasonService.batch_maintenance",),
        implementation_owner="tournament_scheduler/application/canonical_season/batch.py",
        invariants=(
            *_CANONICAL_MUTATION_CONTRACT,
            "Cancellation is scoped, explicit and committed only after the final whole-season candidate passes the canonical gates.",
            "Active guest reservations must be released explicitly before cancellation.",
        ),
        focused_tests=("tests/test_canonical_batch_maintenance.py",),
        status="via_batch",
    ),
    ProductionCapability(
        key="operational_blockers",
        tasks=("report current publication blockers", "separate accepted and diagnostic debt"),
        boundary="canonical_read",
        public_api=("tournament_scheduler.application.season_blockers.season_blockers",),
        implementation_owner="tournament_scheduler/application/season_blockers.py",
        invariants=(
            *_CANONICAL_READ_CONTRACT,
            "The report composes canonical findings, lifecycle reconciliation, export freshness, publication scope and audit evidence without defining new blocker semantics.",
            "A full-season audit failure remains diagnostic when deterministic publication scope is eligible and global safety gates are clear.",
        ),
        focused_tests=("tests/test_season_blockers.py", "tests/test_cli_contract.py"),
    ),
    ProductionCapability(
        key="canonical_export",
        tasks=("materialize canonical export", "verify export projection"),
        boundary="export",
        public_api=(
            "tournament_scheduler.pipeline.stage4_export.run",
            "tournament_scheduler.pipeline.canonical_export_evidence.build_canonical_export_evidence",
            "tournament_scheduler.pipeline.export_lifecycle.write_draft_manifest",
            "tournament_scheduler.pipeline.export_projection_guard.assert_export_preserves_canonical_plan",
        ),
        implementation_owner="tournament_scheduler/pipeline/stage4_export.py + canonical_export_evidence.py + export_lifecycle.py + export_projection_guard.py",
        invariants=(
            *_EXPORT_CONTRACT,
            "Canonical preview/rejection/commit does not materialize exports unless the caller explicitly enters this export boundary.",
        ),
        focused_tests=(
            "tests/test_stage4_export.py",
            "tests/test_canonical_export_context.py",
            "tests/test_export_parity.py",
        ),
    ),
    ProductionCapability(
        key="semantic_audit",
        tasks=("build audit evidence", "record audit workflow verdict", "report audit completion blockers"),
        boundary="audit",
        public_api=(
            "tournament_scheduler.pipeline.audit_context.build_audit_context",
            "tournament_scheduler.pipeline.audit_result.write_audit_result",
            "tournament_scheduler.application.audit_lifecycle.current_workflow",
            "tournament_scheduler.application.audit_lifecycle.record_audit_verdict",
        ),
        implementation_owner="tournament_scheduler/pipeline/audit_context.py + audit_result.py + application/audit_lifecycle.py",
        invariants=(
            *_AUDIT_CONTRACT,
        ),
        focused_tests=(
            "tests/test_audit_context.py",
            "tests/test_audit_result.py",
            "tests/test_audit_workflow.py",
            "tests/test_audit_convergence.py",
        ),
    ),
    ProductionCapability(
        key="publication_lifecycle_state",
        tasks=("mark export freshness state", "seal publication lifecycle", "reopen planning lifecycle"),
        boundary="canonical_mutation",
        public_api=(
            "CanonicalSeasonService.mark_export_fresh",
            "CanonicalSeasonService.seal_published_season",
            "CanonicalSeasonService.reopen_planning",
        ),
        implementation_owner="tournament_scheduler/application/canonical_season/publication.py + lifecycle_status.py",
        invariants=(
            *_CANONICAL_MUTATION_CONTRACT,
            "Export freshness, sealing and reopening change canonical lifecycle decisions only; they do not publish Pages content or materialize exports.",
        ),
        focused_tests=(
            "tests/test_export_freshness.py",
            "tests/test_published_season_sealing.py",
            "tests/test_legacy_publication_identity.py",
        ),
    ),
    ProductionCapability(
        key="publication",
        tasks=("report publication evidence", "publish Pages bundle", "verify publication preflight"),
        boundary="publication",
        public_api=(
            "tournament_scheduler.pipeline.publication_evidence.build_publication_evidence",
            "tournament_scheduler.pipeline.publication_lifecycle.assert_publication_allowed",
            "tournament_scheduler.pipeline.pages_publish.publish",
            "tournament_scheduler.application.canonical_season.lifecycle_status.publication_evidence_report",
            "tournament_scheduler.application.canonical_season.lifecycle_status.verify_sealed_reconciliation",
        ),
        implementation_owner="tournament_scheduler/pipeline/publication_evidence.py + publication_lifecycle.py + pages_publish.py + application/canonical_season/lifecycle_status.py",
        invariants=(
            *_PUBLICATION_CONTRACT,
        ),
        focused_tests=(
            "tests/test_publication_evidence.py",
            "tests/test_pages_publish.py",
            "tests/test_publish_audit_gate.py",
            "tests/test_published_season_sealing.py",
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
