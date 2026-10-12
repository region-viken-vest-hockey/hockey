"""Shared resolution classification for season findings.

Raw verifier and planning findings remain visible, but audit/readiness gates need
one canonical answer to a narrower question: is this finding still actionable
blocking debt for the current canonical revision, or has authoritative current
reality deterministically resolved it as diagnostic planning debt?
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping, MutableMapping

ACTIONABLE_UNRESOLVED = "actionable_unresolved_violation"
ACCEPTED_AUTHORITATIVE_FACT = "accepted_authoritative_fact"
INPUT_CONSTRAINED_FACTUAL_STATE = "input_constrained_factual_state"
PROVEN_INFEASIBLE_WITH_CURRENT_CAPACITY = "proven_infeasible_with_current_capacity"
HISTORICAL_PLANNING_DEVIATION = "historical_planning_deviation"

_RESOLVED_NON_BLOCKING = {
    ACCEPTED_AUTHORITATIVE_FACT,
    INPUT_CONSTRAINED_FACTUAL_STATE,
    PROVEN_INFEASIBLE_WITH_CURRENT_CAPACITY,
    HISTORICAL_PLANNING_DEVIATION,
}


def classify_finding_resolution(finding: Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical actionable/resolved classification for one finding.

    The function is intentionally evidence-driven. It does not special-case a
    tournament id or waive a rule; each non-blocking result names the current
    structured fact that makes the raw planning deviation diagnostic only.
    """

    code = str(finding.get("code") or "")
    evidence: dict[str, Any] = {}
    status = ACTIONABLE_UNRESOLVED
    reason = "unresolved"
    blocking = True

    if finding.get("accepted_booking_interval") and finding.get("accepted_exception"):
        status = ACCEPTED_AUTHORITATIVE_FACT
        reason = "accepted_calendar_booking"
        blocking = False
        evidence = {"accepted_exception": dict(finding.get("accepted_exception") or {})}
    elif code == "input_constrained_shape":
        status = INPUT_CONSTRAINED_FACTUAL_STATE
        reason = "registered_pool_cannot_support_preferred_shape"
        blocking = False
        facts = finding.get("facts")
        if isinstance(facts, Mapping):
            evidence = {"facts": dict(facts)}
    elif code == "unplaced_tournament_placement":
        coverage = finding.get("search_coverage")
        if isinstance(coverage, Mapping):
            coverage_status = str(coverage.get("status") or "")
            stale = bool(coverage.get("capability_stale") or finding.get("bounded_repair_exhausted_stale"))
            if not stale and (
                coverage_status == "proven_infeasible" or coverage.get("proven_infeasible") is True
            ):
                status = PROVEN_INFEASIBLE_WITH_CURRENT_CAPACITY
                reason = "bounded_search_zero_feasible_candidates"
                blocking = False
                evidence = {"search_coverage": dict(coverage)}
    elif finding.get("accepted") or finding.get("acceptance_id"):
        status = ACCEPTED_AUTHORITATIVE_FACT
        reason = "accepted_canonical_decision"
        blocking = False
        if finding.get("acceptance"):
            evidence = {"acceptance": dict(finding.get("acceptance") or {})}

    return {
        "status": status,
        "blocking": blocking,
        "reason": reason,
        "evidence": evidence,
    }


def annotate_resolutions(findings: Iterable[MutableMapping[str, Any]]) -> None:
    """Attach resolution metadata to each finding in place."""

    for finding in findings:
        finding["resolution"] = classify_finding_resolution(finding)


def is_blocking_finding(finding: Mapping[str, Any]) -> bool:
    """Return whether a finding contributes to blocking audit status."""

    resolution = finding.get("resolution")
    if not isinstance(resolution, Mapping):
        resolution = classify_finding_resolution(finding)
    return bool(resolution.get("blocking", True))


def resolution_summary(findings: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize resolved/actionable findings for reports and audits."""

    by_status: Counter[str] = Counter()
    by_rule: Counter[str] = Counter()
    blocking = 0
    for finding in findings:
        resolution = finding.get("resolution")
        if not isinstance(resolution, Mapping):
            resolution = classify_finding_resolution(finding)
        status = str(resolution.get("status") or ACTIONABLE_UNRESOLVED)
        by_status[status] += 1
        if bool(resolution.get("blocking", True)):
            blocking += 1
        rule_id = str(finding.get("rule_id") or "")
        if rule_id and status in _RESOLVED_NON_BLOCKING:
            by_rule[f"{rule_id}:{status}"] += 1
    return {
        "blocking_count": blocking,
        "counts_by_status": dict(sorted(by_status.items())),
        "resolved_counts_by_rule_status": dict(sorted(by_rule.items())),
    }


__all__ = [
    "ACTIONABLE_UNRESOLVED",
    "ACCEPTED_AUTHORITATIVE_FACT",
    "INPUT_CONSTRAINED_FACTUAL_STATE",
    "PROVEN_INFEASIBLE_WITH_CURRENT_CAPACITY",
    "HISTORICAL_PLANNING_DEVIATION",
    "annotate_resolutions",
    "classify_finding_resolution",
    "is_blocking_finding",
    "resolution_summary",
]
