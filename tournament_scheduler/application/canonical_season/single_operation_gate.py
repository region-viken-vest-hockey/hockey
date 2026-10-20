"""Shared hard-verification gate for scoped single operations.

Replace, swap, move and remove verify the candidate against the pre-operation
canonical state with the same baseline classification batch maintenance uses.
Writers and dry-run previews both go through this module, so the preview
verdict is the writer verdict by construction.

A dry-run refusal is reported as a blocked envelope that carries the classified
verification result, so the preview can explain the verdict. Apply raises the
same message and writes nothing.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.planning.verification import verify_canonical_candidate


def verify_single_operation_candidate(
    baseline_plan: Mapping[str, Any],
    candidate: dict[str, Any],
    baseline_problem: Mapping[str, Any] | None,
    *,
    candidate_problem: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify the candidate against the pre-operation baseline.

    ``candidate_problem`` defaults to ``baseline_problem``; remove passes its
    projected problem because withdrawal records change the candidate's facts.
    """

    if candidate_problem is None:
        candidate_problem = baseline_problem
    if baseline_problem:
        baseline = verify_canonical_candidate(dict(baseline_plan), baseline_problem)
    else:
        baseline = verify_canonical_candidate(dict(baseline_plan))
    if candidate_problem:
        return verify_canonical_candidate(candidate, candidate_problem, baseline_verification=baseline)
    return verify_canonical_candidate(candidate, baseline_verification=baseline)


def refuse_on_verification(
    result: Mapping[str, Any],
    dry_run: bool,
    season: str,
    schedule: Mapping[str, Any],
    operation: str,
    detail_key: str,
    details: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return the dry-run refusal envelope, raise the apply refusal, or return None if admitted."""

    if result.get("ok", True):
        return None
    messages = "; ".join(str(v.get("message") or v.get("code")) for v in result.get("violations") or [])
    message = f"candidate fails hard verification: {messages}"
    if not dry_run:
        raise SeasonStateError(f"Refusing {operation}: {message}")
    return {
        "season": season,
        "dry_run": True,
        "current_revision": schedule.get("revision"),
        "candidate_revision": None,
        "candidate_fingerprint": None,
        "verification_result": dict(result),
        "change_cost": None,
        "verdict": {"status": "blocked", "applicable": False, "checks": {}, "blockers": [message]},
        detail_key: {**dict(details), "verification_result": dict(result)},
    }
