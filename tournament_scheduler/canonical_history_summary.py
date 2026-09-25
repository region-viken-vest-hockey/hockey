"""Bounded provenance summaries for canonical decision history.

A full :func:`~tournament_scheduler.planning_contract.verify_candidate` result is
whole-season evidence: hundreds of participation deviations, club-pool records,
manual-placement rows and hosting-balance rows. Persisting it inline in every
``move`` history entry duplicates the same payload per move and dominates the
size of ``season/<season>/decisions.json``.

This module owns the bounded, verifiable summaries that canonical history
stores instead. A summary keeps the whole-season facts as *counts* (never as
the full rows), keeps the tournament-scoped findings that actually concern the
moved tournament, and carries a stable content hash of the full result so a
reader can address the complete evidence when it is retained separately. The
exact full result stays in the immediate dry-run/CLI response; it is never
rebuilt from the summary.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

VERIFICATION_SUMMARY_SCHEMA_VERSION = 1
OPERATIONAL_SUMMARY_SCHEMA_VERSION = 1

# List-typed verification fields summarised as counts rather than embedded.
_COUNTED_LIST_FIELDS = (
    "violations",
    "waived_violations",
    "manual_calendar_placements",
    "manual_external_conflict_placements",
    "manual_participation_placements",
    "movable_allocations_used",
    "unresolved_hosting_obligations",
    "hosting_balance_imbalances",
    "participation_deviations",
    "participation_club_pool_shortfalls",
    "participation_club_pools",
    "input_constrained_shapes",
    "stale_approvals",
    "orphaned_approvals",
)


def verification_hash(result: Mapping[str, Any]) -> str:
    """Return the stable content hash of a full verification result."""

    return stable_payload_sha256(result)


def _tournament_violations(result: Mapping[str, Any], tournament_id: str | None) -> list[dict[str, Any]]:
    """Return the hard violations attributable to one tournament, bounded."""

    if not tournament_id:
        return []
    found: list[dict[str, Any]] = []
    for violation in result.get("violations") or []:
        if not isinstance(violation, Mapping):
            continue
        owner = violation.get("tournament_id")
        if owner is not None:
            if str(owner) == tournament_id:
                found.append({"code": str(violation.get("code") or ""), "message": str(violation.get("message") or "")})
            continue
        if tournament_id in str(violation.get("message") or ""):
            found.append({"code": str(violation.get("code") or ""), "message": str(violation.get("message") or "")})
    return found


def _tournament_placements(
    result: Mapping[str, Any],
    field: str,
    tournament_id: str | None,
) -> list[str]:
    """Return the stable ids of one placement list attributable to a tournament."""

    if not tournament_id:
        return []
    return [
        str(entry.get("tournament_id") or "")
        for entry in result.get(field) or []
        if isinstance(entry, Mapping) and str(entry.get("tournament_id") or "") == tournament_id
    ]


def verification_summary(result: Mapping[str, Any], *, tournament_id: str | None = None) -> dict[str, Any]:
    """Return a bounded, verifiable summary of one verification result.

    ``counts`` are the whole-season list lengths (bounded integers); the only
    embedded records are the hard violations and placement markers that concern
    ``tournament_id``, which are inherently small for one tournament. The full
    result is addressed, not embedded, via ``verification_hash``.
    """

    counts = {field: len(result.get(field) or []) for field in _COUNTED_LIST_FIELDS}
    ok = result.get("ok")
    if not isinstance(ok, bool):
        raise ValueError(
            "Verification result is missing a boolean 'ok' verdict; "
            "refusing to summarize a malformed result as successful"
        )
    summary: dict[str, Any] = {
        "schema_version": VERIFICATION_SUMMARY_SCHEMA_VERSION,
        "ok": ok,
        "verification_hash": verification_hash(result),
        "counts": counts,
    }
    if tournament_id:
        summary["tournament_violations"] = _tournament_violations(result, tournament_id)
        external_conflicts = _tournament_placements(
            result, "manual_external_conflict_placements", tournament_id
        )
        manual_calendar = _tournament_placements(
            result, "manual_calendar_placements", tournament_id
        )
        summary["tournament_external_conflicts"] = external_conflicts
        summary["tournament_manual_calendar_placements"] = manual_calendar
    return summary


def operational_acceptability_summary(acceptability: Mapping[str, Any]) -> dict[str, Any]:
    """Return a bounded summary of an operational-acceptability verdict.

    The full profiles list every tournament id per category; a committed move
    never has regressions, so the profiles are redundant with the per-category
    counts. The regressions (which would have refused the move) are kept verbatim
    because they are inherently small and are the only part an auditor needs.
    """

    return {
        "schema_version": OPERATIONAL_SUMMARY_SCHEMA_VERSION,
        "ok": bool(acceptability.get("ok", True)),
        "regressions": list(acceptability.get("regressions") or []),
        "before_counts": dict(acceptability.get("before_counts") or {}),
        "after_counts": dict(acceptability.get("after_counts") or {}),
    }


__all__ = [
    "OPERATIONAL_SUMMARY_SCHEMA_VERSION",
    "VERIFICATION_SUMMARY_SCHEMA_VERSION",
    "operational_acceptability_summary",
    "verification_hash",
    "verification_summary",
]
