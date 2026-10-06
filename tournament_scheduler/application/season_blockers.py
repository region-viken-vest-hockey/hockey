"""Read-only operational blocker projection for a canonical season.

This module composes existing repository-owned conclusions.  It does not define
another verifier or publication policy:

* canonical findings own actionable versus accepted debt;
* canonical lifecycle owns published-baseline reconciliation;
* publication scope owns incremental publication eligibility;
* canonical export freshness owns the fresh-export requirement; and
* the catalog-driven season audit remains diagnostic context.

The CLI and future operator adapters consume the same stable report instead of
re-deriving these semantics from JSON artifacts.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from tournament_scheduler.canonical_state import canonical_state_revision
from tournament_scheduler.finding_resolution import is_blocking_finding
from tournament_scheduler.pipeline.export_parity.gate import (
    CanonicalFreshness,
    resolve_canonical_freshness,
)
from tournament_scheduler.pipeline.publication_scope import (
    STATUS_BLOCKED,
    STATUS_ELIGIBLE,
    STATUS_HELD,
    STATUS_NOT_CHECKABLE,
    evaluate_publication_scope,
)

SEASON_BLOCKERS_SCHEMA_VERSION = 1

EXIT_CLEAR = 0
EXIT_BLOCKED = 1
EXIT_NOT_CHECKABLE = 2

_DIAGNOSTIC_IMPLICATION = (
    "diagnostic only; deterministic publication scope and global safety gates "
    "control publication eligibility"
)


def _finding_entry(finding: Mapping[str, Any]) -> dict[str, Any]:
    tournament_ids: list[str] = []
    tournament_id = str(finding.get("tournament_id") or "")
    if tournament_id:
        tournament_ids.append(tournament_id)
    for value in finding.get("tournament_ids") or []:
        resolved = str(value or "")
        if resolved and resolved not in tournament_ids:
            tournament_ids.append(resolved)
    resolution = finding.get("resolution")
    return {
        "source": "season_findings",
        "code": str(finding.get("code") or "unknown_finding"),
        "finding_id": str(finding.get("finding_id") or ""),
        "rule_id": str(finding.get("rule_id") or ""),
        "category": str(finding.get("category") or ""),
        "severity": str(finding.get("severity") or ""),
        "message": str(finding.get("message") or ""),
        "tournament_ids": tournament_ids,
        "resolution": dict(resolution) if isinstance(resolution, Mapping) else {},
    }


def _reason_entry(
    reason: Mapping[str, Any],
    *,
    source: str,
    default_code: str,
) -> dict[str, Any]:
    tournament_ids: list[str] = []
    tournament_id = str(reason.get("tournament_id") or "")
    if tournament_id:
        tournament_ids.append(tournament_id)
    return {
        "source": source,
        "code": str(reason.get("code") or default_code),
        "message": str(reason.get("message") or reason.get("reason") or default_code),
        "tournament_ids": tournament_ids,
    }


def _summary(entries: list[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(entry.get("code") or "unknown") for entry in entries)
    tournament_ids = sorted(
        {
            str(tournament_id)
            for entry in entries
            for tournament_id in entry.get("tournament_ids") or []
            if str(tournament_id)
        }
    )
    return {
        "count": len(entries),
        "counts_by_code": dict(sorted(counts.items())),
        "tournament_ids": tournament_ids,
    }


def build_blocker_report(
    *,
    season: str,
    revision: str,
    findings_report: Mapping[str, Any],
    publication_scope: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    publication_evidence: Mapping[str, Any],
    diagnostic_audit: Mapping[str, Any],
    freshness: CanonicalFreshness,
    source_revisions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the stable blocker contract from repository-owned source results."""

    findings = [
        finding
        for finding in findings_report.get("findings") or []
        if isinstance(finding, Mapping)
    ]
    accepted_non_blocking = [
        _finding_entry(finding) for finding in findings if not is_blocking_finding(finding)
    ]
    hard_findings = [
        _finding_entry(finding)
        for finding in findings
        if is_blocking_finding(finding)
        and str(finding.get("severity") or "").lower() == "hard"
    ]
    historical_debt = [
        _finding_entry(finding)
        for finding in findings
        if is_blocking_finding(finding)
        and str(finding.get("severity") or "").lower() != "hard"
    ]

    genuine_blockers: list[dict[str, Any]] = []
    global_blockers: list[dict[str, Any]] = []
    prerequisites: list[dict[str, Any]] = []

    scope_status = str(publication_scope.get("status") or STATUS_NOT_CHECKABLE)
    if scope_status == STATUS_HELD:
        held_entries = [
            _reason_entry(
                held,
                source="publication_scope",
                default_code="publication_scope_held",
            )
            for held in publication_scope.get("held") or []
            if isinstance(held, Mapping)
        ]
        if not held_entries:
            held_entries = [
                _reason_entry(
                    reason,
                    source="publication_scope",
                    default_code="publication_scope_held",
                )
                for reason in publication_scope.get("reasons") or []
                if isinstance(reason, Mapping)
            ]
        genuine_blockers.extend(
            held_entries
            or [
                {
                    "source": "publication_scope",
                    "code": "publication_scope_held",
                    "message": "the current publication scope is held",
                    "tournament_ids": [],
                }
            ]
        )
    elif scope_status == STATUS_BLOCKED:
        global_blockers.extend(
            [
                _reason_entry(
                    reason,
                    source="publication_scope",
                    default_code="publication_scope_blocked",
                )
                for reason in publication_scope.get("reasons") or []
                if isinstance(reason, Mapping)
            ]
            or [
                {
                    "source": "publication_scope",
                    "code": "publication_scope_blocked",
                    "message": "the current publication scope is globally blocked",
                    "tournament_ids": [],
                }
            ]
        )
    elif scope_status == STATUS_NOT_CHECKABLE:
        for reason in publication_scope.get("reasons") or []:
            if isinstance(reason, Mapping):
                prerequisites.append(
                    _reason_entry(
                        reason,
                        source="publication_scope",
                        default_code="publication_scope_not_checkable",
                    )
                )
        if not prerequisites:
            prerequisites.append(
                {
                    "source": "publication_scope",
                    "code": "publication_scope_not_checkable",
                    "message": "publication scope could not be established",
                    "tournament_ids": [],
                }
            )

    reconciliation = lifecycle.get("reconciliation")
    if not isinstance(reconciliation, Mapping):
        prerequisites.append(
            {
                "source": "canonical_lifecycle",
                "code": "reconciliation_unavailable",
                "message": "published-baseline reconciliation is unavailable",
                "tournament_ids": [],
            }
        )
    elif not bool(reconciliation.get("ok")):
        global_blockers.append(
            {
                "source": "canonical_lifecycle",
                "code": "canonical_reconciliation_failed",
                "message": "canonical state does not reconcile to the published baseline",
                "tournament_ids": [],
                "detail": dict(reconciliation.get("unexplained_delta") or {}),
            }
        )

    if publication_evidence.get("blocked"):
        global_blockers.append(
            {
                "source": "publication_evidence",
                "code": "publication_evidence_blocked",
                "message": str(
                    publication_evidence.get("delta_error")
                    or "published-to-canonical delta could not be established"
                ),
                "tournament_ids": [],
            }
        )

    if not bool(findings_report.get("verification_ok")):
        global_blockers.extend(hard_findings)
        if not hard_findings:
            global_blockers.append(
                {
                    "source": "season_findings",
                    "code": "hard_verification_failed",
                    "message": "canonical hard verification failed",
                    "tournament_ids": [],
                }
            )

    if not freshness.determined:
        prerequisites.append(
            {
                "source": "canonical_export_freshness",
                "code": "canonical_revision_unavailable",
                "message": "current canonical revision could not be determined",
                "tournament_ids": [],
            }
        )
    elif freshness.requires_fresh_export:
        global_blockers.append(
            {
                "source": "canonical_export_freshness",
                "code": "requires_fresh_export",
                "message": "canonical state requires a fresh export before publication",
                "tournament_ids": [],
            }
        )

    revisions = {
        "canonical": revision,
        "findings": str(findings_report.get("revision") or ""),
        "lifecycle": str(lifecycle.get("canonical_state_revision") or ""),
        **{str(key): str(value or "") for key, value in (source_revisions or {}).items()},
    }
    observed = {value for value in revisions.values() if value}
    if revision and any(value != revision for value in observed):
        prerequisites.append(
            {
                "source": "season_blockers",
                "code": "canonical_revision_changed_during_query",
                "message": "source projections were not bound to one canonical revision",
                "tournament_ids": [],
                "revisions": revisions,
            }
        )

    audit = diagnostic_audit.get("audit")
    if not isinstance(audit, Mapping):
        audit = diagnostic_audit
    audit_projection = {
        "status": str(audit.get("status") or "NOT_CHECKABLE"),
        "ok": bool(audit.get("ok")),
        "reasons": list(audit.get("reasons") or []),
        "blocking_finding_count": int(audit.get("blocking_finding_count") or 0),
        "publication_implication": _DIAGNOSTIC_IMPLICATION,
    }

    if prerequisites:
        status = STATUS_NOT_CHECKABLE
        exit_code = EXIT_NOT_CHECKABLE
    elif global_blockers:
        status = STATUS_BLOCKED
        exit_code = EXIT_BLOCKED
    elif genuine_blockers:
        status = STATUS_HELD
        exit_code = EXIT_BLOCKED
    elif scope_status == STATUS_ELIGIBLE:
        status = STATUS_ELIGIBLE
        exit_code = EXIT_CLEAR
    else:
        status = STATUS_NOT_CHECKABLE
        exit_code = EXIT_NOT_CHECKABLE

    publishable = status == STATUS_ELIGIBLE
    if status == STATUS_ELIGIBLE:
        next_action = "No deterministic blocker for the current publication scope."
    elif status == STATUS_HELD:
        next_action = "Resolve the affected tournament blockers listed above."
    elif status == STATUS_BLOCKED:
        next_action = "Resolve the global safety blockers before publication."
    else:
        next_action = "Restore the missing prerequisite evidence, then rerun season blockers."

    report: dict[str, Any] = {
        "schema_version": SEASON_BLOCKERS_SCHEMA_VERSION,
        "season": season,
        "revision": revision,
        "status": status,
        "publishable": publishable,
        "exit_code": exit_code,
        "genuine_blockers": genuine_blockers,
        "global_blockers": global_blockers,
        "prerequisite_failures": prerequisites,
        "accepted_non_blocking": accepted_non_blocking,
        "historical_debt": historical_debt,
        "publication_scope": dict(publication_scope),
        "diagnostic_audit": audit_projection,
        "next_action": next_action,
    }
    report["summary"] = {
        "genuine_blockers": _summary(genuine_blockers),
        "global_blockers": _summary(global_blockers),
        "prerequisite_failures": _summary(prerequisites),
        "accepted_non_blocking": _summary(accepted_non_blocking),
        "historical_debt": _summary(historical_debt),
    }
    return report


def _not_checkable_report(*, season: str, message: str) -> dict[str, Any]:
    failure = {
        "source": "season_blockers",
        "code": "blocker_query_failed",
        "message": message,
        "tournament_ids": [],
    }
    return {
        "schema_version": SEASON_BLOCKERS_SCHEMA_VERSION,
        "season": season,
        "revision": "",
        "status": STATUS_NOT_CHECKABLE,
        "publishable": False,
        "exit_code": EXIT_NOT_CHECKABLE,
        "genuine_blockers": [],
        "global_blockers": [],
        "prerequisite_failures": [failure],
        "accepted_non_blocking": [],
        "historical_debt": [],
        "publication_scope": {"status": STATUS_NOT_CHECKABLE, "reasons": [failure]},
        "diagnostic_audit": {
            "status": "NOT_CHECKABLE",
            "ok": False,
            "reasons": [message],
            "blocking_finding_count": 0,
            "publication_implication": _DIAGNOSTIC_IMPLICATION,
        },
        "next_action": "Restore the missing prerequisite evidence, then rerun season blockers.",
        "summary": {
            "genuine_blockers": _summary([]),
            "global_blockers": _summary([]),
            "prerequisite_failures": _summary([failure]),
            "accepted_non_blocking": _summary([]),
            "historical_debt": _summary([]),
        },
    }


def season_blockers(
    season: str,
    *,
    root: str | Path = "season",
) -> dict[str, Any]:
    """Query current canonical owners and return the operational blocker report.

    The operation is read-only.  Any unavailable source fails closed to a typed
    ``NOT_CHECKABLE`` result instead of raising or fabricating eligibility.
    """

    try:
        from tournament_scheduler.season_maintenance import (
            list_findings,
            load_context,
            season_audit,
        )
        from tournament_scheduler.season_state import (
            publication_evidence_report,
            season_lifecycle_report,
        )

        findings = list_findings(season, root=str(root))
        schedule, decisions, plan, problem = load_context(season, root=str(root))
        revision = str(canonical_state_revision(schedule, decisions) or "")
        audit = season_audit(season, root=str(root))
        lifecycle = season_lifecycle_report(season, root=str(root))
        evidence = publication_evidence_report(season, root=str(root))
        audit_payload = audit.get("audit") if isinstance(audit, Mapping) else None
        audit_reasons = (
            list(audit_payload.get("reasons") or [])
            if isinstance(audit_payload, Mapping)
            else []
        )
        scope = evaluate_publication_scope(
            reviewed_plan=plan,
            problem=problem,
            decisions=decisions,
            season=season,
            season_root=root,
            canonical_revision=revision,
            full_season_reasons=audit_reasons,
        )
        freshness = resolve_canonical_freshness(season=season, season_root=root)
        return build_blocker_report(
            season=season,
            revision=revision,
            findings_report=findings,
            publication_scope=scope,
            lifecycle=lifecycle,
            publication_evidence=evidence,
            diagnostic_audit=audit,
            freshness=freshness,
            source_revisions={
                "audit": str(audit.get("revision") or ""),
                "audit_after": str(audit.get("revision_after") or ""),
                "publication_evidence": str(
                    evidence.get("current_canonical_revision") or ""
                ),
                "publication_scope": str(scope.get("canonical_revision") or ""),
                "export_freshness": str(freshness.revision or ""),
            },
        )
    except Exception as exc:  # noqa: BLE001 - read-only query fails closed
        return _not_checkable_report(
            season=season,
            message=f"{type(exc).__name__}: {exc}",
        )


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_CLEAR",
    "EXIT_NOT_CHECKABLE",
    "SEASON_BLOCKERS_SCHEMA_VERSION",
    "build_blocker_report",
    "season_blockers",
]
