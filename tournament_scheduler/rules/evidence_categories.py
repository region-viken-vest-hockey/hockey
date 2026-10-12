"""Audit evidence categories: what each evidence bucket reports and how the checklist treats it.

The registry lives beside the rule catalog so the audit's category identities are
validated with the rest of the catalog (see ``catalog.validate_catalog``). Rule
semantics stay in ``catalog``; this module owns only the evidence projection metadata.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceCategory:
    """One audit evidence category.

    ``unresolved`` marks categories that keep an audit open until addressed;
    ``checklist_item`` is the operator checklist item the category feeds.
    """

    name: str
    description: str
    checklist_item: int
    unresolved: bool
    severity: str


EVIDENCE_CATEGORIES: tuple[EvidenceCategory, ...] = (
    EvidenceCategory(
        name="participation_shortfalls",
        description="Teams whose final participation count is below target (per-team category/reason).",
        checklist_item=1,
        unresolved=True,
        severity="major",
    ),
    EvidenceCategory(
        name="club_participation_fairness",
        description="Per club/age-group proportional participation share (target vs actual).",
        checklist_item=1,
        unresolved=False,
        severity="info",
    ),
    EvidenceCategory(
        name="participation_club_pools",
        description="Club x age-group x scope aggregate player-pool view: aggregate target/actual, exact team distribution and the deterministic classification (intra_club_distribution / minor / material club-pool shortfall / single-team) that separates a genuine shortage from an intra-club label imbalance.",
        checklist_item=1,
        unresolved=False,
        severity="info",
    ),
    EvidenceCategory(
        name="manual_participation_placements",
        description="Manual participation placements the final verifier surfaced for operator follow-up.",
        checklist_item=1,
        unresolved=True,
        severity="major",
    ),
    EvidenceCategory(
        name="participation_target_deviations",
        description="Bounded participation-target deviations (over or under) with the deterministic avoidability classification (avoidable / proven_infeasible / bounded_search_exhausted / operator_accepted) the controller uses to judge the residual trade-off.",
        checklist_item=1,
        unresolved=True,
        severity="major",
    ),
    EvidenceCategory(
        name="unresolved_hosting_obligations",
        description="Club x age-group hosting obligations with no placed tournament, including untried reallocation candidates.",
        checklist_item=2,
        unresolved=True,
        severity="major",
    ),
    EvidenceCategory(
        name="home_representation",
        description="Per-sibling participation in a multi-team club's own home tournaments (spread; 0-1 is balanced).",
        checklist_item=2,
        unresolved=False,
        severity="info",
    ),
    EvidenceCategory(
        name="same_age_hosting_repairs",
        description="Same-age hosting-coverage repairs attempted (repaired or rejected-with-reason).",
        checklist_item=2,
        unresolved=False,
        severity="info",
    ),
    EvidenceCategory(
        name="cross_age_hosting_repairs",
        description="Cross-age hosting-coverage repairs attempted (repaired or rejected-with-reason).",
        checklist_item=2,
        unresolved=False,
        severity="info",
    ),
    EvidenceCategory(
        name="unresolved_tournament_placements",
        description="Concrete rosters/dates that could not be given a participant-host arena/time.",
        checklist_item=2,
        unresolved=True,
        severity="major",
    ),
    EvidenceCategory(
        name="unresolved_external_conflicts",
        description="Hosts with an external calendar conflict the plan could not route around.",
        checklist_item=4,
        unresolved=True,
        severity="major",
    ),
    EvidenceCategory(
        name="manual_external_conflict_placements",
        description="External calendar conflicts the final verifier surfaced for operator follow-up.",
        checklist_item=4,
        unresolved=True,
        severity="major",
    ),
    EvidenceCategory(
        name="movable_allocations_used",
        description="Tournaments placed in a host-controlled movable_busy interval (e.g. Kongsberg open ice): feasible subject to the host moving/replacing the listed event.",
        checklist_item=4,
        unresolved=True,
        severity="major",
    ),
    EvidenceCategory(
        name="calendar_interpretations_used",
        description="Controller-requested inferred movable_busy interpretations recorded on the candidate (host confirmation required); the source calendar is unchanged.",
        checklist_item=4,
        unresolved=True,
        severity="major",
    ),
    EvidenceCategory(
        name="tournament_duration",
        description="Per-tournament required duration/end time derived from configured ice time and rounds.",
        checklist_item=3,
        unresolved=False,
        severity="info",
    ),
    EvidenceCategory(
        name="blocking_byes",
        description="Tournaments with pause/bye teams or an invalid no-bye roster (a hard policy violation).",
        checklist_item=3,
        unresolved=True,
        severity="critical",
    ),
    EvidenceCategory(
        name="input_constrained_shape",
        description="Tournaments whose odd/bye shape is a legitimate input-constrained adaptation, not a defect.",
        checklist_item=3,
        unresolved=False,
        severity="info",
    ),
    EvidenceCategory(
        name="guest_reservations",
        description="Reserved guest places and their open/filled/released lifecycle status.",
        checklist_item=3,
        unresolved=False,
        severity="info",
    ),
    EvidenceCategory(
        name="underfilled_tournaments",
        description="Tournaments below their configured full-capacity game count.",
        checklist_item=4,
        unresolved=True,
        severity="minor",
    ),
    EvidenceCategory(
        name="calendar_sources",
        description="Persisted Stage 2 source/calendar health (blocked, empty, cached, warnings).",
        checklist_item=4,
        unresolved=False,
        severity="info",
    ),
    EvidenceCategory(
        name="host_participation",
        description="Tournaments whose host club has no participating team.",
        checklist_item=5,
        unresolved=True,
        severity="major",
    ),
    EvidenceCategory(
        name="duplicate_team_days",
        description="Teams scheduled in more than one tournament on the same day.",
        checklist_item=6,
        unresolved=True,
        severity="major",
    ),
    EvidenceCategory(
        name="same_club_participants",
        description="Tournaments with more than two teams from the same club.",
        checklist_item=7,
        unresolved=False,
        severity="minor",
    ),
    EvidenceCategory(
        name="same_club_participants_hard_max",
        description="Tournaments above the hard maximum number of teams from one club (an invalid plan).",
        checklist_item=7,
        unresolved=True,
        severity="critical",
    ),
    EvidenceCategory(
        name="export_consistency",
        description="Per-output-format row/event counts cross-checked against the selected plan.",
        checklist_item=8,
        unresolved=False,
        severity="info",
    ),
    EvidenceCategory(
        name="verify_violations",
        description="Violations from the deterministic Stage 4 verification result.",
        checklist_item=9,
        unresolved=True,
        severity="critical",
    ),
    EvidenceCategory(
        name="operator_waivers",
        description="Active operator waivers that authorize a precise exception to a hard rule.",
        checklist_item=9,
        unresolved=False,
        severity="info",
    ),
    EvidenceCategory(
        name="waived_violations",
        description="Hard-rule violations that passed only because of an operator waiver.",
        checklist_item=9,
        unresolved=False,
        severity="minor",
    ),
    EvidenceCategory(
        name="stale_approvals",
        description="Per-tournament approvals whose protected fields changed and must be re-approved.",
        checklist_item=9,
        unresolved=True,
        severity="major",
    ),
)

EVIDENCE_CATEGORY_BY_NAME: dict[str, EvidenceCategory] = {
    category.name: category for category in EVIDENCE_CATEGORIES
}
