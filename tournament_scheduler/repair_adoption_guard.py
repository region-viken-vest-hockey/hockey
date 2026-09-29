"""Cross-rule before/after adoption guard for canonical season repairs.

A promoted-season repair is normally triggered by one finding (a spacing
cluster, a repeated opponent, a participation deviation, ...). Fixing that one
finding locally is not proof that the *whole season* got better: a legal swap
can remove one tight pair while creating two elsewhere, concentrate one team's
opponents on a single club, widen a participation shortfall or make a displaced
squad's schedule materially worse. Running many such swaps in sequence adds a
second failure mode: ``A -> B -> C -> A`` ping-pong between already-valid
states.

This module owns the planner-independent evaluation that answers, for one
candidate repair against one canonical revision:

* the season-wide *finding* delta -- resolved, newly introduced and persisting
  findings, keyed by stable finding id, for the current state and for a pinned
  pass baseline;
* the season-wide *quality* delta on the shared objective vector
  (:mod:`tournament_scheduler.quality_objectives`) plus canonical travel;
* an explicit list of material regression codes. Material regressions are never
  accepted by default: an operator must name the exact code and give a reason,
  mirroring the per-team ``team_schedule_quality`` acceptance contract;
* cycle detection over a deterministic normalized-assignment fingerprint. The
  bounded :class:`RepairPassLedger` records the adopted state fingerprints plus
  a compact decision lineage so revisiting an already-seen assignment is
  detected even when no single swap directly reverses the previous one.

The guard never verifies a candidate itself and never mutates canonical state:
:func:`tournament_scheduler.season_maintenance.apply_repair` remains the atomic
mutation boundary. The guard consumes the same independent verifier, the same
soft-quality comparison and the same travel implementation every other surface
uses, so it cannot drift into a second scoring rule.

The module also owns the catalog-driven :func:`season_wide_audit` completion
gate. It enumerates the applicable checks from
:mod:`tournament_scheduler.rule_catalog` and the verification/finding/score
evidence it is handed, reports every check's scope/result/status, and refuses a
green completion when a mandatory check was skipped, a hard violation remains,
reconciliation failed or the audited revision no longer matches current state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .pipeline.fingerprints import stable_payload_sha256
from .final_verification import verify_canonical_candidate
from .planning_contract import score_candidate
from .quality_objectives import compare_quality_scores, with_unresolved_obligations_count
from .rule_catalog import (
    CATALOG_BY_ID,
    HARD_CONSTRAINT,
    OPERATIONAL_OBLIGATION,
    SOFT_OBJECTIVE,
)
from .season_baseline import (
    compare_findings_to_baseline,
    finding_identity_record,
    finding_severity_score,
)
from .team_schedule_quality import (
    DEFAULT_TEMPORAL_COVERAGE_THRESHOLD_DAYS,
    MATERIAL_CLUB_EXPOSURE_INDEX_DELTA,
    MATERIAL_TEMPORAL_GAP_DELTA_DAYS,
    MATERIAL_TRAVEL_INCREASE_KM,
    MATERIAL_TRAVEL_INCREASE_RATIO,
)

ADOPTION_GUARD_SCHEMA_VERSION = 1

# Canonical history events that start a new improvement pass. Visited-state
# cycle detection is scoped to the pass that begins at the most recent boundary,
# so a legitimate return to an earlier assignment in a new pass is not blocked
# by an old pass's fingerprints.
BASELINE_PASS_BOUNDARY_EVENTS = frozenset(
    {"season_baseline_create", "season_baseline_replace", "season_baseline_advance"}
)

# Every material regression the guard can raise. The code vocabulary is the
# contract an operator override names; it must stay stable.
REGRESSION_HARD_VERIFICATION = "hard_verification_regression"
REGRESSION_MORE_GAPS_UNDER_7 = "more_gaps_under_7_days"
REGRESSION_MORE_GAPS_UNDER_14 = "more_gaps_under_14_days"
REGRESSION_TEMPORAL_COVERAGE = "temporal_coverage_materially_worse"
REGRESSION_TEMPORAL_OFFENDERS = "temporal_offenders_worse"
REGRESSION_PARTICIPATION_DEVIATION = "participation_target_deviation_worse"
REGRESSION_PARTICIPATION_SHORTFALL = "club_pool_shortfall_worse"
REGRESSION_PARTICIPATION_AVOIDABLE = "avoidable_participation_worse"
REGRESSION_CLUB_EXPOSURE = "more_concentrated_club_exposure"
REGRESSION_CLUB_REPETITION = "club_pair_repetition_worse"
REGRESSION_SAME_CLUB_CLUSTERING = "same_club_clustering_worse"
REGRESSION_UNIQUE_OPPONENTS = "fewer_unique_opponent_clubs"
REGRESSION_HOSTING_BALANCE = "hosting_balance_worse"
REGRESSION_HOSTING_OBLIGATION = "unresolved_hosting_obligation_worse"
REGRESSION_HOME_REPRESENTATION = "home_representation_worse"
REGRESSION_TRAVEL = "travel_materially_worse"
REGRESSION_CYCLE = "cycle_detected"

# Codes an operator may explicitly accept. A hard-verification regression is
# deliberately not acceptable here: a hard constraint is never a soft
# trade-off. Participation target compliance stays a guardrail the operator
# may accept deliberately (it is a strong goal, not a legality rule).
ACCEPTABLE_ADOPTION_REGRESSION_CODES = frozenset(
    {
        REGRESSION_MORE_GAPS_UNDER_7,
        REGRESSION_MORE_GAPS_UNDER_14,
        REGRESSION_TEMPORAL_COVERAGE,
        REGRESSION_TEMPORAL_OFFENDERS,
        REGRESSION_PARTICIPATION_DEVIATION,
        REGRESSION_PARTICIPATION_SHORTFALL,
        REGRESSION_PARTICIPATION_AVOIDABLE,
        REGRESSION_CLUB_EXPOSURE,
        REGRESSION_CLUB_REPETITION,
        REGRESSION_SAME_CLUB_CLUSTERING,
        REGRESSION_UNIQUE_OPPONENTS,
        REGRESSION_HOSTING_BALANCE,
        REGRESSION_HOSTING_OBLIGATION,
        REGRESSION_HOME_REPRESENTATION,
        REGRESSION_TRAVEL,
    }
)

# Defect priority tiers (lower number = higher priority), matching the catalog
# precedence. A regression is auto-waived only when a strictly higher tier
# improved AND the regression itself is a soft objective. Operational-obligation
# and strong-goal regressions always require an explicit per-code acceptance, so
# a higher-tier fix can never silently waive an unrelated shortfall or exposure
# regression.
TIER_HARD = 0
TIER_OPERATIONAL_OBLIGATION = 1
TIER_STRONG_GOAL = 2
TIER_SOFT = 3

REGRESSION_TIERS: Dict[str, int] = {
    REGRESSION_HARD_VERIFICATION: TIER_HARD,
    REGRESSION_CYCLE: TIER_HARD,
    REGRESSION_HOSTING_OBLIGATION: TIER_OPERATIONAL_OBLIGATION,
    REGRESSION_PARTICIPATION_DEVIATION: TIER_STRONG_GOAL,
    REGRESSION_PARTICIPATION_SHORTFALL: TIER_STRONG_GOAL,
    REGRESSION_PARTICIPATION_AVOIDABLE: TIER_STRONG_GOAL,
    REGRESSION_HOSTING_BALANCE: TIER_STRONG_GOAL,
    REGRESSION_HOME_REPRESENTATION: TIER_STRONG_GOAL,
    REGRESSION_MORE_GAPS_UNDER_7: TIER_SOFT,
    REGRESSION_MORE_GAPS_UNDER_14: TIER_SOFT,
    REGRESSION_TEMPORAL_COVERAGE: TIER_SOFT,
    REGRESSION_TEMPORAL_OFFENDERS: TIER_SOFT,
    REGRESSION_CLUB_EXPOSURE: TIER_SOFT,
    REGRESSION_CLUB_REPETITION: TIER_SOFT,
    REGRESSION_SAME_CLUB_CLUSTERING: TIER_SOFT,
    REGRESSION_UNIQUE_OPPONENTS: TIER_SOFT,
    REGRESSION_TRAVEL: TIER_SOFT,
}


def regression_tier(code: str) -> int:
    """Return the catalog priority tier for a regression code."""

    return REGRESSION_TIERS.get(str(code or ""), TIER_SOFT)


def select_blocking_regressions(
    material_regressions: Sequence[Mapping[str, Any]],
    improvement_tier: Optional[int],
) -> List[Dict[str, Any]]:
    """Return the regressions that still require an explicit acceptance.

    A soft regression may be auto-waived only when a strictly higher-priority
    tier improved. Hard-verification and cycle regressions, and every
    operational-obligation or strong-goal regression, always block unless the
    operator accepts that exact code with a reason.
    """

    if improvement_tier is None:
        return [dict(record) for record in material_regressions]
    blocking: List[Dict[str, Any]] = []
    for record in material_regressions:
        tier = regression_tier(str(record.get("code") or ""))
        waived = tier == TIER_SOFT and improvement_tier < tier
        if not waived:
            blocking.append(dict(record))
    return blocking

# Quality metric path -> (regression code, material-by-default). The shared
# ``compare_quality_scores`` owns every metric; this table only classifies a
# metric regression as material or as a visible warning, and names the code the
# operator would override. Exact-squad opponent repetition is intentionally
# absent from the material set: per the club-level exposure contract, squad
# labels are administrative and exact-label repetition alone never blocks a
# repair.
_QUALITY_MATERIALITY: Dict[str, tuple[str, bool]] = {
    "turnaround.gaps_under_days.7": (REGRESSION_MORE_GAPS_UNDER_7, True),
    "turnaround.gaps_under_days.14": (REGRESSION_MORE_GAPS_UNDER_14, True),
    "temporal.max_gap_days": (REGRESSION_TEMPORAL_COVERAGE, "threshold"),
    "temporal.offenders_count": (REGRESSION_TEMPORAL_OFFENDERS, True),
    "participation.club_pool_unresolved_season_total_absolute_deviation": (
        REGRESSION_PARTICIPATION_DEVIATION,
        True,
    ),
    "participation.club_pool_unresolved_max_team_season_deviation": (
        REGRESSION_PARTICIPATION_DEVIATION,
        True,
    ),
    "participation.club_pool_unresolved_half_total_absolute_deviation": (
        REGRESSION_PARTICIPATION_DEVIATION,
        True,
    ),
    "participation.club_pool_unresolved_max_team_half_deviation": (
        REGRESSION_PARTICIPATION_DEVIATION,
        True,
    ),
    "participation.club_pool_unresolved_shortfall_count": (REGRESSION_PARTICIPATION_SHORTFALL, True),
    "participation.club_pool_unresolved_avoidable_deviation_count": (
        REGRESSION_PARTICIPATION_AVOIDABLE,
        True,
    ),
    "opponent_diversity.max_club_pair_repeat": (REGRESSION_CLUB_REPETITION, True),
    "opponent_diversity.club_pairs_meeting_3_plus": (REGRESSION_CLUB_REPETITION, True),
    "opponent_diversity.max_club_exposure_index": (REGRESSION_CLUB_EXPOSURE, "exposure"),
    "opponent_diversity.same_club_pairing_count": (REGRESSION_SAME_CLUB_CLUSTERING, True),
    "opponent_diversity.club_count_excess_over_2": (REGRESSION_SAME_CLUB_CLUSTERING, True),
    "opponent_diversity.tournaments_with_3plus_same_club": (REGRESSION_SAME_CLUB_CLUSTERING, True),
    "opponent_diversity.min_distinct_opponent_clubs": (REGRESSION_UNIQUE_OPPONENTS, True),
    "opponent_diversity.inter_club_diversity": (REGRESSION_UNIQUE_OPPONENTS, True),
    "hosting.spread": (REGRESSION_HOSTING_BALANCE, True),
    "hosting.unresolved_obligations_count": (REGRESSION_HOSTING_OBLIGATION, True),
    "home_representation.max_material_spread": (REGRESSION_HOME_REPRESENTATION, True),
    "home_representation.material_skew_pool_count": (REGRESSION_HOME_REPRESENTATION, True),
    # Diagnostic-only exact-squad metrics: reported, never material on their own.
    "opponent_diversity.unique_pairs": ("fewer_unique_opponents", False),
    "opponent_diversity.pairwise_novelty": ("fewer_unique_opponents", False),
    "opponent_diversity.max_pair_repeat": ("exact_squad_repeat_worse", False),
    "opponent_diversity.pairs_meeting_3_plus": ("exact_squad_repeat_worse", False),
    "participation.spread": ("intra_club_label_spread_worse", False),
}

# ``score_candidate`` reports unresolved hosting obligations as a list; the
# comparison works on the numeric count surfaced by
# ``quality_objectives.with_unresolved_obligations_count``.


# ---------------------------------------------------------------------------
# Normalized assignment identity and cycle detection
# ---------------------------------------------------------------------------


def _normalized_team(team: Mapping[str, Any]) -> tuple[str, str, str, bool]:
    return (
        str(team.get("club") or ""),
        str(team.get("label") or ""),
        str(team.get("age_group") or ""),
        bool(team.get("guest", False)),
    )


def normalized_assignment(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a timestamp/history-free normalized view of a plan's assignments.

    Only facts that change the schedule are included (tournament placement and
    participants, plus unresolved placement obligations). Games are derived from
    the participants, so they are deliberately excluded from the identity.
    """
    tournaments: List[Dict[str, Any]] = []
    for tournament in plan.get("tournaments") or []:
        if not isinstance(tournament, Mapping):
            continue
        teams = sorted(
            _normalized_team(team)
            for team in (tournament.get("teams") or [])
            if isinstance(team, Mapping)
        )
        tournaments.append(
            {
                "id": str(tournament.get("id") or ""),
                "date": str(tournament.get("date") or ""),
                "start_time": str(tournament.get("start_time") or ""),
                "host_club": str(tournament.get("host_club") or ""),
                "arena": str(tournament.get("arena") or ""),
                "age_group": str(tournament.get("age_group") or ""),
                "cancelled": bool(tournament.get("cancelled")),
                "teams": [list(team) for team in teams],
            }
        )
    tournaments.sort(key=lambda item: item["id"])
    unresolved = [
        {
            "id": str(entry.get("id") or ""),
            "age_group": str(entry.get("age_group") or ""),
            "date": str(entry.get("date") or ""),
            "responsible_host": str(entry.get("responsible_host") or ""),
        }
        for entry in plan.get("unresolved_tournament_placements") or []
        if isinstance(entry, Mapping)
    ]
    unresolved.sort(key=lambda item: item["id"])
    return {"tournaments": tournaments, "unresolved_tournament_placements": unresolved}


def normalized_assignment_fingerprint(plan: Mapping[str, Any]) -> str:
    """Deterministic fingerprint of normalized canonical season assignments."""
    return stable_payload_sha256(normalized_assignment(plan))


@dataclass
class RepairPassLedger:
    """Bounded pass ledger for one cross-rule improvement pass.

    The ledger pins the pass baseline identity and records each adopted state's
    normalized fingerprint plus a compact lineage (decision/option id, trigger
    rule, affected entities, quality delta and override rationale). It stores no
    schedule bodies, so it does not bloat ``decisions.json``.
    """

    baseline_revision: str = ""
    baseline_fingerprint: str = ""
    baseline_note: str = ""
    max_records: int = 256
    records: List[Dict[str, Any]] = field(default_factory=list)
    dropped_records: int = 0

    def fingerprints(self) -> List[str]:
        seen: List[str] = []
        if self.baseline_fingerprint:
            seen.append(self.baseline_fingerprint)
        seen.extend(
            str(record.get("state_fingerprint") or "")
            for record in self.records
            if record.get("state_fingerprint")
        )
        return seen

    def has_visited(self, fingerprint: str) -> bool:
        return bool(fingerprint) and fingerprint in set(self.fingerprints())

    def record(
        self,
        *,
        state_fingerprint: str,
        revision: str = "",
        decision_id: str = "",
        option_id: str = "",
        trigger_rule: str = "",
        finding_id: str = "",
        affected_tournament_ids: Iterable[str] = (),
        affected_team_ids: Iterable[str] = (),
        quality_delta: Optional[Mapping[str, Any]] = None,
        material_regressions: Iterable[Mapping[str, Any]] = (),
        accepted_regressions: Iterable[Mapping[str, Any]] = (),
        note: str = "",
        at: Optional[str] = None,
    ) -> Dict[str, Any]:
        entry = {
            "state_fingerprint": str(state_fingerprint or ""),
            "revision": str(revision or ""),
            "decision_id": str(decision_id or ""),
            "option_id": str(option_id or ""),
            "trigger_rule": str(trigger_rule or ""),
            "finding_id": str(finding_id or ""),
            "affected_tournament_ids": sorted({str(item) for item in affected_tournament_ids if item}),
            "affected_team_ids": sorted({str(item) for item in affected_team_ids if item}),
            "quality_delta": dict(quality_delta or {}),
            "material_regressions": [dict(item) for item in material_regressions],
            "accepted_regressions": [dict(item) for item in accepted_regressions],
            "note": str(note or ""),
            "at": at or _now_iso(),
        }
        self.records.append(entry)
        if self.max_records and len(self.records) > self.max_records:
            drop = len(self.records) - self.max_records
            self.dropped_records += drop
            del self.records[:drop]
        return entry

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": ADOPTION_GUARD_SCHEMA_VERSION,
            "baseline_revision": self.baseline_revision,
            "baseline_fingerprint": self.baseline_fingerprint,
            "baseline_note": self.baseline_note,
            "max_records": self.max_records,
            "dropped_records": self.dropped_records,
            "records": [dict(record) for record in self.records],
        }

    @classmethod
    def from_history(cls, history: Optional[Iterable[Mapping[str, Any]]]) -> "RepairPassLedger":
        """Rebuild a pass ledger from compact canonical repair history.

        Each applied repair stores a compact ``adoption`` summary in its history
        event. Reconstructing the visited fingerprints from that history keeps
        cycle detection working across processes without a parallel ledger file
        and without storing full schedule snapshots. Only repairs after the most
        recent accepted season-baseline boundary are part of the current pass; a
        baseline create/advance/replace resets the visited-state set.
        """
        events = [entry for entry in (history or []) if isinstance(entry, Mapping)]
        boundary = 0
        for index, entry in enumerate(events):
            if str(entry.get("event") or "") in BASELINE_PASS_BOUNDARY_EVENTS:
                boundary = index + 1
        ledger = cls()
        for entry in events[boundary:]:
            if not isinstance(entry, Mapping):
                continue
            details = entry.get("details") if isinstance(entry.get("details"), Mapping) else {}
            adoption = details.get("adoption")
            if not isinstance(adoption, Mapping):
                continue
            baseline_fingerprint = str(adoption.get("baseline_fingerprint") or "")
            if baseline_fingerprint and not ledger.baseline_fingerprint:
                ledger.baseline_fingerprint = baseline_fingerprint
                ledger.baseline_revision = str(adoption.get("baseline_revision") or "")
            ledger.record(
                state_fingerprint=str(adoption.get("candidate_fingerprint") or ""),
                revision=str(adoption.get("revision") or ""),
                decision_id=str(adoption.get("decision_id") or ""),
                option_id=str(adoption.get("option_id") or ""),
                trigger_rule=str(adoption.get("trigger_rule") or ""),
                finding_id=str(adoption.get("finding_id") or ""),
                affected_tournament_ids=adoption.get("affected_tournament_ids") or (),
                affected_team_ids=adoption.get("affected_team_ids") or (),
                quality_delta=adoption.get("quality_delta") or {},
                material_regressions=adoption.get("material_regressions") or (),
                accepted_regressions=adoption.get("accepted_regressions") or (),
                note=str(adoption.get("note") or ""),
                at=str(entry.get("at") or "") or None,
            )
        return ledger

    @classmethod
    def from_dict(cls, payload: Optional[Mapping[str, Any]]) -> "RepairPassLedger":
        payload = payload or {}
        return cls(
            baseline_revision=str(payload.get("baseline_revision") or ""),
            baseline_fingerprint=str(payload.get("baseline_fingerprint") or ""),
            baseline_note=str(payload.get("baseline_note") or ""),
            max_records=int(payload.get("max_records") or 256),
            records=[dict(record) for record in payload.get("records") or []],
            dropped_records=int(payload.get("dropped_records") or 0),
        )


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def adoption_history_summary(
    adoption: Mapping[str, Any],
    *,
    affected_tournament_ids: Iterable[str] = (),
    affected_team_ids: Iterable[str] = (),
    revision: str = "",
    decision_id: str = "",
    baseline_revision: str = "",
    baseline_fingerprint: str = "",
    note: str = "",
) -> Dict[str, Any]:
    """Return the compact pass-ledger record for a canonical history event.

    Deliberately excludes the full quality metric list and per-team profiles so
    ``decisions.json`` is not bloated with repeated schedule evidence.
    """
    finding_delta = adoption.get("finding_delta") or {}
    return {
        "schema_version": ADOPTION_GUARD_SCHEMA_VERSION,
        "revision": str(revision or ""),
        "decision_id": str(decision_id or ""),
        "option_id": str(adoption.get("option_id") or ""),
        "trigger_rule": str(adoption.get("trigger_rule") or ""),
        "finding_id": str(adoption.get("finding_id") or ""),
        "before_fingerprint": str(adoption.get("before_fingerprint") or ""),
        "candidate_fingerprint": str(adoption.get("candidate_fingerprint") or ""),
        "baseline_revision": str(baseline_revision or ""),
        "baseline_fingerprint": str(baseline_fingerprint or ""),
        "finding_delta": {
            "resolved": int(finding_delta.get("resolved_count") or 0),
            "new": int(finding_delta.get("new_count") or 0),
            "persisting": int(finding_delta.get("persisting_count") or 0),
            "improved": int(finding_delta.get("improved_count") or 0),
            "regressed": int(finding_delta.get("regressed_count") or 0),
        },
        "material_regression_codes": [
            str(item.get("code") or "") for item in adoption.get("material_regressions") or []
        ],
        "blocking_regression_codes": [
            str(item.get("code") or "") for item in adoption.get("blocking_regressions") or []
        ],
        "measurement_incomplete": list(adoption.get("measurement_incomplete") or []),
        "priority_improvement_tier": adoption.get("priority_improvement_tier"),
        "accepted_regressions": [
            {"code": str(item.get("code") or ""), "reason": str(item.get("reason") or "")}
            for item in adoption.get("accepted_regressions") or []
        ],
        "affected_tournament_ids": sorted({str(item) for item in affected_tournament_ids if item}),
        "affected_team_ids": sorted({str(item) for item in affected_team_ids if item}),
        "note": str(note or ""),
    }


# ---------------------------------------------------------------------------
# Finding-set comparison
# ---------------------------------------------------------------------------


def compare_finding_sets(
    before_findings: Sequence[Mapping[str, Any]],
    after_findings: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Classify after-findings as resolved / new / persisting vs before.

    Unlike the season-baseline comparison this keeps hard findings: a newly
    introduced hard violation must be visible in the cross-rule delta. A finding
    whose deterministic severity score rose is ``REGRESSED``; one that fell is
    ``IMPROVED``; an unchanged one is ``PERSISTING``.
    """
    before = {
        str(finding.get("finding_id")): finding_identity_record(finding)
        for finding in before_findings
        if isinstance(finding, Mapping) and finding.get("finding_id")
    }
    after = {
        str(finding.get("finding_id")): finding_identity_record(finding)
        for finding in after_findings
        if isinstance(finding, Mapping) and finding.get("finding_id")
    }

    resolved: List[Dict[str, Any]] = []
    new: List[Dict[str, Any]] = []
    persisting: List[Dict[str, Any]] = []
    improved: List[Dict[str, Any]] = []
    regressed: List[Dict[str, Any]] = []

    for finding_id in sorted(set(before) | set(after)):
        old = before.get(finding_id)
        current = after.get(finding_id)
        if current is None:
            resolved.append(dict(old))
            continue
        if old is None:
            new.append(dict(current))
            continue
        old_score = finding_severity_score(old)
        new_score = finding_severity_score(current)
        entry = {"finding_id": finding_id, "baseline": old, "current": current}
        if new_score > old_score:
            regressed.append(entry)
        elif new_score < old_score:
            improved.append(entry)
        else:
            persisting.append(entry)

    return {
        "resolved": resolved,
        "new": new,
        "persisting": persisting,
        "improved": improved,
        "regressed": regressed,
        "resolved_count": len(resolved),
        "new_count": len(new),
        "persisting_count": len(persisting),
        "improved_count": len(improved),
        "regressed_count": len(regressed),
        "net_finding_delta": len(resolved) + len(improved) - len(new) - len(regressed),
    }


# ---------------------------------------------------------------------------
# Material regression classification
# ---------------------------------------------------------------------------


def _temporal_threshold(finding: Mapping[str, Any]) -> int:
    return int(finding.get("threshold_days") or DEFAULT_TEMPORAL_COVERAGE_THRESHOLD_DAYS)


def classify_quality_regressions(
    comparison: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Turn a ``compare_quality_scores`` result into coded materiality records.

    Every regressed metric is reported. Only metrics declared material (and
    crossing their deterministic threshold) get ``material: true``; the rest are
    kept as visible warnings so a later surface can explain a soft trade-off
    without silently blocking it.
    """
    records: List[Dict[str, Any]] = []
    for metric in comparison.get("metrics") or []:
        if not metric.get("regressed"):
            continue
        path = str(metric.get("metric") or "")
        code, materiality = _QUALITY_MATERIALITY.get(path, (f"quality_regression:{path}", False))
        material = bool(materiality) is True
        reason = "declared_material"
        if materiality == "threshold":
            # A wider worst-case gap is material only when it crosses the
            # normal offender threshold by at least the declared material delta;
            # a small increase stays a warning.
            delta = int(metric.get("delta") or 0)
            after = int(metric.get("new") or 0)
            material = (
                delta >= MATERIAL_TEMPORAL_GAP_DELTA_DAYS
                and after >= DEFAULT_TEMPORAL_COVERAGE_THRESHOLD_DAYS
            )
            reason = "temporal_threshold"
        elif materiality == "exposure":
            delta = float(metric.get("delta") or 0.0)
            material = delta >= MATERIAL_CLUB_EXPOSURE_INDEX_DELTA
            reason = "exposure_threshold"
        record = {
            "code": code,
            "metric": path,
            "direction": metric.get("direction"),
            "before": metric.get("old"),
            "after": metric.get("new"),
            "delta": metric.get("delta"),
            "material": material,
            "materiality": reason if material else "warning",
        }
        records.append(record)
    return records


def classify_travel_regression(
    before_travel: Mapping[str, Any],
    after_travel: Mapping[str, Any],
) -> Dict[str, Any]:
    """Classify canonical travel change using the shared material thresholds."""
    before_km = float(before_travel.get("total_travel_km", 0.0) or 0.0)
    after_km = float(after_travel.get("total_travel_km", 0.0) or 0.0)
    delta = after_km - before_km
    ratio = (delta / before_km) if before_km > 0 else None
    material = bool(after_travel.get("available", True) and before_travel.get("available", True)) and (
        delta >= MATERIAL_TRAVEL_INCREASE_KM
        and (before_km == 0 or (ratio is not None and ratio >= MATERIAL_TRAVEL_INCREASE_RATIO))
    )
    return {
        "code": REGRESSION_TRAVEL,
        "metric": "travel.total_travel_km",
        "before": before_km,
        "after": after_km,
        "delta": delta,
        "relative_increase": ratio,
        "material": material,
        "materiality": "travel_threshold" if material else "warning",
    }


def compute_travel(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Return canonical season travel totals for a plan, or a zero fallback."""
    try:
        from .club_distances import compute_team_travel_distances
        from .serialization.season_plan import season_plan_from_dict

        season_plan = season_plan_from_dict(dict(plan))
        team_travel = compute_team_travel_distances(season_plan)
    except Exception:
        return {"total_travel_km": 0.0, "max_team_travel_km": 0.0, "available": False}
    values = list(team_travel.values())
    return {
        "total_travel_km": float(sum(values)),
        "max_team_travel_km": float(max(values) if values else 0),
        "available": True,
    }


def parse_adoption_overrides(
    raw: Any,
    reason: Optional[str],
) -> List[Dict[str, str]]:
    """Normalize explicit operator acceptances of named adoption regressions.

    Each item is a regression code or a mapping with ``code``/``reason``. A
    non-empty reason is mandatory, and a hard-verification regression can never
    be accepted. Mirrors ``team_schedule_quality.parse_regression_acceptances``
    so the guard's override contract is the same kind of explicit, auditable
    decision.
    """
    items = list(raw or [])
    if not items:
        return []
    resolved_reason = str(reason or "").strip()
    if not resolved_reason:
        raise ValueError(
            "Accepting a cross-rule adoption regression requires an explicit operator reason"
        )
    acceptances: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, Mapping):
            code = str(item.get("code") or "").strip()
        else:
            code = str(item).strip()
        if not code:
            raise ValueError(
                "Adoption override must be a regression code or {'code': ..., 'reason': ...}"
            )
        if code == REGRESSION_HARD_VERIFICATION:
            raise ValueError(
                f"Regression code {code!r} is a hard-constraint regression and cannot be accepted"
            )
        if code not in ACCEPTABLE_ADOPTION_REGRESSION_CODES:
            raise ValueError(
                f"Regression code {code!r} cannot be accepted; acceptable codes: "
                + ", ".join(sorted(ACCEPTABLE_ADOPTION_REGRESSION_CODES))
            )
        if code in seen:
            continue
        seen.add(code)
        acceptances.append({"code": code, "reason": resolved_reason})
    return acceptances


def apply_adoption_overrides(
    material_regressions: Sequence[Mapping[str, Any]],
    acceptances: Sequence[Mapping[str, str]],
) -> Dict[str, Any]:
    """Split material regressions into accepted and unaccepted by exact code.

    An acceptance that matches no material regression in the candidate is
    itself a refusal, so overrides cannot be supplied pre-emptively or as a
    blanket waiver.
    """
    by_code = {str(item.get("code") or ""): item for item in material_regressions}
    accepted: List[Dict[str, Any]] = []
    unaccepted: List[Dict[str, Any]] = []
    matched: set[str] = set()
    for regression in material_regressions:
        code = str(regression.get("code") or "")
        acceptance = next(
            (item for item in acceptances if str(item.get("code") or "") == code), None
        )
        if acceptance is not None:
            matched.add(code)
            accepted.append({**dict(regression), "reason": str(acceptance.get("reason") or "")})
        else:
            unaccepted.append(dict(regression))
    unmatched = [
        {"code": str(item.get("code") or ""), "reason": str(item.get("reason") or "")}
        for item in acceptances
        if str(item.get("code") or "") not in matched
    ]
    return {
        "acceptable": not unaccepted and not unmatched,
        "accepted_regressions": accepted,
        "unaccepted_regressions": unaccepted,
        "unmatched_acceptances": unmatched,
        "material_codes": sorted(code for code in by_code if code),
    }


# ---------------------------------------------------------------------------
# The adoption evaluation
# ---------------------------------------------------------------------------


def evaluate_adoption(
    before_plan: Mapping[str, Any],
    candidate_plan: Mapping[str, Any],
    *,
    problem: Optional[Mapping[str, Any]] = None,
    before_findings: Sequence[Mapping[str, Any]] = (),
    candidate_findings: Sequence[Mapping[str, Any]] = (),
    pass_baseline: Optional[Mapping[str, Any]] = None,
    ledger: Optional[RepairPassLedger] = None,
    accept_regressions: Any = (),
    regression_reason: Optional[str] = None,
    trigger_rule: str = "",
    finding_id: str = "",
    option_id: str = "",
    priority_improvement_tier: Optional[int] = None,
) -> Dict[str, Any]:
    """Evaluate one candidate repair against current state and a pass baseline.

    Hard constraints come first: an increase in hard violations is always a
    material regression and can never be overridden. Then material soft
    regressions are collected by explicit code and must be accepted one by one.
    Finally an already-visited normalized assignment is refused as a cycle.

    The result is evidence, not a mutation: ``adoptable`` is the guard's verdict
    and the caller (``season_maintenance.apply_repair``) is the boundary that
    enforces it.
    """
    before_score = with_unresolved_obligations_count(
        score_candidate(dict(before_plan), problem=dict(problem) if problem is not None else None)
    )
    after_score = with_unresolved_obligations_count(
        score_candidate(dict(candidate_plan), problem=dict(problem) if problem is not None else None)
    )
    before_verification = verify_canonical_candidate(dict(before_plan), dict(problem) if problem is not None else None)
    after_verification = verify_canonical_candidate(dict(candidate_plan), dict(problem) if problem is not None else None)
    before_travel = compute_travel(before_plan)
    after_travel = compute_travel(candidate_plan)

    quality_vs_current = compare_quality_scores(before_score, after_score)
    quality_records = classify_quality_regressions(quality_vs_current)
    travel_record = classify_travel_regression(before_travel, after_travel)
    travel_available = bool(before_travel.get("available", True)) and bool(
        after_travel.get("available", True)
    )
    measurement_incomplete: List[str] = [] if travel_available else ["travel"]

    material: List[Dict[str, Any]] = []
    hard_violations_before = len(before_verification.get("violations") or [])
    hard_violations_after = len(after_verification.get("violations") or [])
    hard_violations_decreased = hard_violations_after < hard_violations_before
    if hard_violations_after > hard_violations_before:
        material.append(
            {
                "code": REGRESSION_HARD_VERIFICATION,
                "before": hard_violations_before,
                "after": hard_violations_after,
                "material": True,
                "materiality": "hard_verification",
            }
        )
    material.extend(record for record in quality_records if record["material"])
    if travel_record["material"]:
        material.append(travel_record)

    finding_delta = compare_finding_sets(before_findings, candidate_findings)
    baseline_comparison = (
        compare_findings_to_baseline(dict(pass_baseline), list(candidate_findings))
        if pass_baseline
        else None
    )

    # Catalog precedence: only a strictly higher-priority tier improvement can
    # auto-waive a *soft* regression. Operational-obligation and strong-goal
    # regressions always need an explicit named override, so a higher-tier fix
    # can never silently waive an unrelated shortfall, exposure or travel
    # regression. Hard-verification regressions and cycles are never waived.
    # The caller supplies the canonical improvement tier when it has one;
    # otherwise only a strict hard-violation reduction qualifies.
    resolved_improvement_tier = (
        priority_improvement_tier
        if priority_improvement_tier is not None
        else (TIER_HARD if hard_violations_decreased else None)
    )
    blocking_material = select_blocking_regressions(material, resolved_improvement_tier)

    candidate_fingerprint = normalized_assignment_fingerprint(candidate_plan)
    cycle_detected = bool(ledger is not None and ledger.has_visited(candidate_fingerprint))
    if cycle_detected:
        cycle_record = {
            "code": REGRESSION_CYCLE,
            "state_fingerprint": candidate_fingerprint,
            "material": True,
            "materiality": "cycle",
        }
        material.append(cycle_record)
        blocking_material.append(cycle_record)

    acceptances = parse_adoption_overrides(accept_regressions, regression_reason)
    override_result = apply_adoption_overrides(blocking_material, acceptances)

    hard_ok = bool(after_verification.get("ok"))
    reasons: List[str] = []
    if not hard_ok:
        reasons.append("candidate fails hard verification")
    if not override_result["acceptable"]:
        reasons.append("unaccepted material regression(s)")
    if cycle_detected:
        reasons.append("candidate assignment was already visited in this pass")
    if measurement_incomplete:
        reasons.append(
            "measurement unavailable: " + ", ".join(measurement_incomplete)
        )

    adoptable = (
        hard_ok
        and override_result["acceptable"]
        and not cycle_detected
        and not measurement_incomplete
    )
    return {
        "schema_version": ADOPTION_GUARD_SCHEMA_VERSION,
        "adoptable": adoptable,
        "hard_ok": hard_ok,
        "hard_violations_before": hard_violations_before,
        "hard_violations_after": hard_violations_after,
        "hard_violations_decreased": hard_violations_decreased,
        "priority_improvement_tier": resolved_improvement_tier,
        "soft_regression_priority_exemption": resolved_improvement_tier is not None,
        "measurement_incomplete": measurement_incomplete,
        "cycle_detected": cycle_detected,
        "trigger_rule": trigger_rule,
        "finding_id": finding_id,
        "option_id": option_id,
        "before_fingerprint": normalized_assignment_fingerprint(before_plan),
        "candidate_fingerprint": candidate_fingerprint,
        "finding_delta": finding_delta,
        "baseline_comparison": baseline_comparison,
        "quality_vs_current": {
            "metrics": quality_vs_current["metrics"],
            "regressions": quality_vs_current["regressions"],
            "coded": quality_records,
        },
        "travel": {
            "before": before_travel,
            "after": after_travel,
            "delta": travel_record,
        },
        "material_regressions": material,
        "blocking_regressions": blocking_material,
        "accepted_regressions": override_result["accepted_regressions"],
        "unaccepted_regressions": override_result["unaccepted_regressions"],
        "unmatched_acceptances": override_result["unmatched_acceptances"],
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Catalog-driven full-season audit and completion gate
# ---------------------------------------------------------------------------

# Rule classifications that are pass/fail checks. ``operator_decision`` and
# ``fact_evidence_semantic`` are inputs to decisions, not completeness gates.
_CHECK_CLASSIFICATIONS = (HARD_CONSTRAINT, OPERATIONAL_OBLIGATION, SOFT_OBJECTIVE)


def _finding_rule_ids(findings: Sequence[Mapping[str, Any]]) -> set[str]:
    return {str(finding.get("rule_id") or "") for finding in findings if finding.get("rule_id")}


def _finding_codes(findings: Sequence[Mapping[str, Any]]) -> set[str]:
    return {str(finding.get("code") or "") for finding in findings if finding.get("code")}


def _accepted_finding_ids(findings: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        str(finding.get("finding_id") or "")
        for finding in findings
        if isinstance(finding, Mapping) and (finding.get("accepted") or finding.get("acceptance_id"))
    }


def _check_rule(
    entry: Any,
    *,
    finding_rule_ids: set[str],
    finding_codes: set[str],
    violation_codes: set[str],
    accepted_finding_rule_ids: set[str],
    score_paths: set[str],
    covered_verifier_owners: set[str],
) -> Dict[str, Any] | None:
    """Resolve one catalog rule to a check result, or ``None`` if not a check."""
    classification = entry.classification
    if classification not in _CHECK_CLASSIFICATIONS:
        return None
    mandatory = classification != SOFT_OBJECTIVE
    verifier_codes = set(entry.verifier_codes)
    finding_codes_owned = set(entry.finding_codes)
    status = "clear"
    evidence: List[str] = []
    incomplete_reason = ""
    if classification == SOFT_OBJECTIVE:
        if finding_codes_owned & finding_codes or entry.id in finding_rule_ids:
            status = "finding"
            evidence.extend(sorted(finding_codes_owned & finding_codes) or ["finding"])
        elif set(entry.score_paths) & score_paths:
            status = "measured"
        else:
            status = "not_measured"
    elif entry.id in accepted_finding_rule_ids:
        status = "accepted_exception"
        evidence.append("accepted by an explicit operator decision")
    elif verifier_codes & violation_codes:
        status = "violation"
        evidence.extend(sorted(verifier_codes & violation_codes))
    elif finding_codes_owned & finding_codes:
        status = "finding"
        evidence.extend(sorted(finding_codes_owned & finding_codes))
    elif entry.id in finding_rule_ids:
        status = "finding"
        evidence.append("finding")
    elif (verifier_codes or finding_codes_owned) and (
        entry.verifier_owner.endswith("verify_candidate")
        or entry.verifier_owner in covered_verifier_owners
    ):
        # The owning verifier ran and reported no code for this rule, so the
        # rule itself is clear. A rule owned by a verifier that emits findings
        # (rather than verifier codes) is clear once its owner is explicitly
        # covered and no finding with one of its codes was surfaced. Overall
        # verification success is a separate gate and a different rule's
        # violation must not be attributed to this one.
        status = "clear"
    elif verifier_codes or finding_codes_owned or entry.verifier_owner:
        # The catalog names a verifier/finding owner this audit surface was not
        # handed evidence for. Surface it as incomplete coverage rather than a
        # false green.
        status = "incomplete"
        incomplete_reason = "verifier/finding evidence was not provided to this audit"
    else:
        status = "incomplete"
        incomplete_reason = "catalog entry declares no measurable verifier or finding"
    return {
        "rule_id": entry.id,
        "classification": classification,
        "status": status,
        "mandatory": mandatory,
        "owner": entry.canonical_owner,
        "verifier_owner": entry.verifier_owner,
        "evidence": evidence,
        "incomplete_reason": incomplete_reason,
    }


def season_wide_audit(
    *,
    plan: Mapping[str, Any],
    findings: Sequence[Mapping[str, Any]],
    verification: Mapping[str, Any],
    score: Optional[Mapping[str, Any]] = None,
    reconciliation: Optional[Mapping[str, Any]] = None,
    expected_fingerprint: str = "",
    expected_revision: str = "",
    current_revision: str = "",
    catalog: Optional[Iterable[Any]] = None,
    covered_verifier_owners: Iterable[str] = (),
    incomplete_reasons: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Catalog-driven, exhaustive season-wide audit and completion gate.

    It enumerates every catalogued hard/obligation check and resolves each one
    against the verification and finding evidence it is handed. Checks whose
    evidence is unavailable are reported ``incomplete`` -- a skipped check is
    never a pass. ``incomplete_reasons`` maps a rule ID to the actionable
    reason its owner's evidence is missing or failed, overriding the generic
    message. The gate is green only when there are no hard violations, no
    incomplete mandatory checks, an explicit successful reconciliation, and the
    audited plan still matches the canonical revision re-read after the audit.
    """
    entries = list(catalog) if catalog is not None else list(CATALOG_BY_ID.values())
    finding_rule_ids = _finding_rule_ids(findings)
    finding_codes = _finding_codes(findings)
    violation_codes = {
        str(violation.get("code") or "")
        for violation in verification.get("violations") or []
        if violation.get("code")
    }
    accepted_rule_ids = _finding_rule_ids(
        [
            finding
            for finding in findings
            if finding.get("finding_id") in _accepted_finding_ids(findings)
        ]
    ) - {""}
    score_paths = _score_paths(score)
    verification_ok = bool(verification.get("ok"))
    covered_owners = set(covered_verifier_owners)

    checks: List[Dict[str, Any]] = []
    for entry in entries:
        result = _check_rule(
            entry,
            finding_rule_ids=finding_rule_ids,
            finding_codes=finding_codes,
            violation_codes=violation_codes,
            accepted_finding_rule_ids=accepted_rule_ids,
            score_paths=score_paths,
            covered_verifier_owners=covered_owners,
        )
        if result is not None:
            reason = (incomplete_reasons or {}).get(result["rule_id"])
            if reason:
                # A failed or unevaluable owner is incomplete coverage even when
                # another source (for example the findings projection) emitted a
                # same-rule finding: the gate must not report complete coverage
                # for a check whose owner did not run.
                result["status"] = "incomplete"
                result["incomplete_reason"] = str(reason)
            checks.append(result)

    incomplete = [check for check in checks if check["status"] == "incomplete" and check["mandatory"]]
    violations = [check for check in checks if check["status"] == "violation"]
    accepted = [check for check in checks if check["status"] == "accepted_exception"]
    # A catalogued mandatory check that resolved to an unresolved finding is a
    # blocker regardless of the finding's severity label: an operational
    # obligation is not a soft preference, so it must not be reported as a pass.
    mandatory_findings = [
        check for check in checks if check["status"] == "finding" and check["mandatory"]
    ]
    soft_findings = [
        check for check in checks if check["status"] == "finding" and not check["mandatory"]
    ]
    blocking_findings = [
        finding
        for finding in findings
        if str(finding.get("severity") or "").lower() == "hard"
        and str(finding.get("finding_id") or "") not in _accepted_finding_ids(findings)
    ]
    reconciliation_ok = (
        None if not reconciliation else bool(reconciliation.get("ok"))
    )
    reconciliation_status = (
        "missing" if reconciliation_ok is None else "ok" if reconciliation_ok else "failed"
    )
    assignment_fingerprint = normalized_assignment_fingerprint(plan)
    content_fingerprint = stable_payload_sha256(plan.get("tournaments", []))
    game_count = sum(
        len(tournament.get("games") or [])
        for tournament in plan.get("tournaments") or []
        if isinstance(tournament, Mapping)
    )
    revision_matches = (
        (not expected_fingerprint) or expected_fingerprint == assignment_fingerprint
    ) and ((not expected_revision) or expected_revision == current_revision)
    coverage_ok = not incomplete
    ok = (
        verification_ok
        and coverage_ok
        and not violations
        and not mandatory_findings
        and not blocking_findings
        and reconciliation_ok is True
        and revision_matches
    )
    reasons: List[str] = []
    if not verification_ok:
        reasons.append("hard verification failed")
    if incomplete:
        reasons.append(
            "mandatory check(s) skipped/incomplete: "
            + ", ".join(check["rule_id"] for check in incomplete)
        )
    if violations:
        reasons.append(
            "hard violation(s): " + ", ".join(check["rule_id"] for check in violations)
        )
    if mandatory_findings:
        reasons.append(
            "unresolved mandatory obligation(s): "
            + ", ".join(check["rule_id"] for check in mandatory_findings)
        )
    if blocking_findings:
        reasons.append(f"{len(blocking_findings)} unresolved hard finding(s)")
    if reconciliation_ok is None:
        reasons.append("canonical reconciliation evidence is missing")
    elif reconciliation_ok is False:
        reasons.append("canonical reconciliation failed")
    if not revision_matches:
        reasons.append("audited assignment fingerprint does not match the audited plan")
    return {
        "schema_version": ADOPTION_GUARD_SCHEMA_VERSION,
        "fingerprint": assignment_fingerprint,
        "assignment_fingerprint": assignment_fingerprint,
        "content_fingerprint": content_fingerprint,
        "game_count": game_count,
        "tournament_count": len(plan.get("tournaments") or []),
        "expected_fingerprint": expected_fingerprint,
        "expected_revision": expected_revision,
        "current_revision": current_revision,
        "revision_matches": revision_matches,
        "hard_verification_ok": verification_ok,
        "coverage_ok": coverage_ok,
        "reconciliation_ok": reconciliation_ok,
        "reconciliation_status": reconciliation_status,
        "check_count": len(checks),
        "checks": checks,
        "incomplete_checks": [check["rule_id"] for check in incomplete],
        "violation_checks": [check["rule_id"] for check in violations],
        "mandatory_finding_checks": [check["rule_id"] for check in mandatory_findings],
        "soft_finding_checks": [check["rule_id"] for check in soft_findings],
        "accepted_exceptions": [check["rule_id"] for check in accepted],
        "blocking_finding_count": len(blocking_findings),
        "ok": ok,
        "status": "PASS" if ok else "INCOMPLETE" if incomplete else "FAIL",
        "reasons": reasons,
    }


def _score_paths(score: Optional[Mapping[str, Any]]) -> set[str]:
    """Return the resolved metric paths present in a ``score_candidate`` report."""
    if not score:
        return set()
    from .quality_objectives import QUALITY_METRIC_PATHS, get_metric_path

    return {path for path, _direction in QUALITY_METRIC_PATHS if get_metric_path(dict(score), path) is not None}


__all__ = [
    "ACCEPTABLE_ADOPTION_REGRESSION_CODES",
    "ADOPTION_GUARD_SCHEMA_VERSION",
    "BASELINE_PASS_BOUNDARY_EVENTS",
    "REGRESSION_CLUB_EXPOSURE",
    "REGRESSION_CLUB_REPETITION",
    "REGRESSION_CYCLE",
    "REGRESSION_HARD_VERIFICATION",
    "REGRESSION_MORE_GAPS_UNDER_14",
    "REGRESSION_MORE_GAPS_UNDER_7",
    "REGRESSION_PARTICIPATION_DEVIATION",
    "REGRESSION_PARTICIPATION_SHORTFALL",
    "REGRESSION_TEMPORAL_COVERAGE",
    "REGRESSION_TRAVEL",
    "TIER_HARD",
    "TIER_OPERATIONAL_OBLIGATION",
    "TIER_SOFT",
    "TIER_STRONG_GOAL",
    "RepairPassLedger",
    "adoption_history_summary",
    "apply_adoption_overrides",
    "classify_quality_regressions",
    "classify_travel_regression",
    "compare_finding_sets",
    "compute_travel",
    "evaluate_adoption",
    "normalized_assignment",
    "normalized_assignment_fingerprint",
    "parse_adoption_overrides",
    "regression_tier",
    "season_wide_audit",
    "select_blocking_regressions",
]
