"""Refresh a plan's descriptive hosting/readiness snapshot from a verification result.

``plan.publication_readiness``, ``plan.unresolved_hosting_obligations``,
``plan.hosting_balance_imbalances`` and the ``same_age_hosting_repairs`` /
``cross_age_hosting_repairs`` attempt logs are *descriptive projections* of
the plan's tournaments as verified at one point in time.  A canonical
mutation (``season move`` / ``season apply``) changes ``plan.tournaments``
without rerunning the planner, so those snapshots go stale and can contradict
a fresh deterministic verify result -- e.g. still reporting a club x age-group
hosting obligation the mutation just resolved.

This module owns the single refresh used by the pipeline's Stage 4
reconciliation, the canonical-season mutation path, and the read-only audit
context, so the projection can never disagree with the verifier that owns the
underlying rule.  It is a projection/reporting concern only: it never decides
hosting legality or changes a placement.
"""

from __future__ import annotations

from typing import Any

from .final_verification import publication_readiness

_GENERIC_HOSTING_REASON = "required hosting obligation has no verified feasible automatic slot"


def _obligation_keys(obligations: Any) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for item in obligations or []:
        if not isinstance(item, dict):
            continue
        keys.add((str(item.get("club") or ""), str(item.get("age_group") or "")))
    return keys


def publication_readiness_with_plan_placements(
    verify_result: dict[str, Any] | None,
    plan_dict: dict[str, Any] | None,
) -> dict[str, Any]:
    """Publication readiness for *plan_dict* derived from *verify_result*.

    ``verify_candidate``/``verify_final_candidate`` re-derive every finding
    from the candidate's tournaments, but ``unresolved_tournament_placements``
    is a planner-time fact about tournaments that were never created and cannot
    be rediscovered from the candidate.  Fold it into the verifier's own
    readiness the same way the other unresolved findings block PUBLISHABLE
    status.
    """
    result = verify_result if isinstance(verify_result, dict) else {}
    plan = plan_dict if isinstance(plan_dict, dict) else {}
    readiness = publication_readiness(result)
    unresolved_placements = plan.get("unresolved_tournament_placements") or []
    if unresolved_placements and readiness.get("status") != "INVALID":
        reasons = list(readiness.get("reasons") or [])
        reasons.append({"code": "tournament_placement_shortfall", "count": len(unresolved_placements)})
        readiness["reasons"] = reasons
        readiness["status"] = "REVIEW_REQUIRED"
        readiness["publishable"] = False
    return readiness


def reconcile_plan_derived_state(
    plan_dict: dict[str, Any] | None,
    authoritative: dict[str, Any] | None,
    *,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Re-derive a plan's descriptive hosting/readiness snapshot in place.

    *authoritative* is the freshest verifier/evidence state for the same plan:
    a ``verify_candidate``/``verify_final_candidate`` result or a
    fingerprint-bound ``final_operator_evidence`` block.  Only facts the
    authoritative source actually carries are overwritten, so a partial
    fixture that omits hosting verification never clears real plan data.

    *readiness* overrides the derived readiness.  Pass it when the caller
    already holds an authoritative, already-reconciled readiness (the
    fingerprint-bound final operator evidence), so plan-only facts such as
    ``unresolved_tournament_placements`` are not folded in twice.
    """
    if not isinstance(plan_dict, dict):
        return plan_dict
    source = authoritative if isinstance(authoritative, dict) else {}

    if "unresolved_hosting_obligations" in source:
        unresolved = [item for item in (source.get("unresolved_hosting_obligations") or []) if isinstance(item, dict)]
        plan_dict["unresolved_hosting_obligations"] = [
            {
                "club": item.get("club", ""),
                "age_group": item.get("age_group", ""),
                "reason": item.get("reason") or _GENERIC_HOSTING_REASON,
            }
            for item in unresolved
        ]
        live_keys = _obligation_keys(unresolved)
        # A repair attempt logged as `unresolved` is only evidence while the
        # obligation is still unresolved.  Once a mutation resolves it, the
        # stale "still unresolved" row must not keep contradicting the verifier.
        for key in ("same_age_hosting_repairs", "cross_age_hosting_repairs"):
            rows = plan_dict.get(key)
            if not isinstance(rows, list):
                continue
            plan_dict[key] = [
                row
                for row in rows
                if not (
                    isinstance(row, dict)
                    and str(row.get("status") or "") == "unresolved"
                    and (str(row.get("club") or ""), str(row.get("age_group") or "")) not in live_keys
                )
            ]

    if "hosting_balance" in source:
        plan_dict["hosting_balance"] = list(source.get("hosting_balance") or [])
    if "hosting_balance_imbalances" in source:
        plan_dict["hosting_balance_imbalances"] = list(source.get("hosting_balance_imbalances") or [])

    if readiness is not None:
        plan_dict["publication_readiness"] = dict(readiness)
    else:
        plan_dict["publication_readiness"] = publication_readiness_with_plan_placements(source, plan_dict)
    return plan_dict
