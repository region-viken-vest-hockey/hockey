"""Refresh a plan's descriptive snapshot from a verification result.

``plan.publication_readiness``, ``plan.unresolved_hosting_obligations``,
``plan.hosting_balance_imbalances``, the ``same_age_hosting_repairs`` /
``cross_age_hosting_repairs`` attempt logs, ``unresolved_external_conflicts``,
``unresolved_participation_shortfalls`` and the operator-waiver audit rows are
*descriptive projections* of the plan's tournaments as verified at one point in
time.  A canonical mutation (``season move`` / ``season apply``) changes
``plan.tournaments`` without rerunning the planner, so those snapshots go stale
and can contradict a fresh deterministic verify result -- e.g. still reporting a
club x age-group hosting obligation the mutation just resolved.

This module owns the single refresh used by the pipeline's Stage 4
reconciliation, the canonical-season mutation path, and the read-only audit
context, so a projection can never disagree with the verifier that owns the
underlying rule.  It is a projection/reporting concern only: it never decides
legality or changes a placement.

Planner-time facts that cannot be reconstructed from the tournaments (for
example ``unresolved_tournament_placements``) are *not* verifier projections and
are deliberately left untouched here; they must be carried by whoever owns the
plan-level obligation.
"""

from __future__ import annotations

from typing import Any

from .final_verification import publication_readiness
from .operator_waivers import waiver_audit_rows

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


def _reconciled_external_conflicts(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Project the verifier's external-conflict placements into plan audit rows."""
    return [
        {
            "tournament_id": item.get("tournament_id", ""),
            "host_club": item.get("host_club", ""),
            "age_group": item.get("age_group", ""),
            "date": item.get("date", ""),
            "reason": "external calendar conflict requires manual resolution",
        }
        for item in (source.get("manual_external_conflict_placements") or [])
        if isinstance(item, dict)
    ]


def _reconciled_participation_shortfalls(
    plan_dict: dict[str, Any], source: dict[str, Any]
) -> list[dict[str, Any]]:
    """Project the verifier's participation findings, keeping plan provenance.

    The independent verifier re-derives *which* under-target findings exist from
    the true candidate but only reports club/label/age_group/half/actual/target.
    ``SeasonPlanner`` computed a richer cause classification and the #318
    same-date-capacity evidence.  Look each verifier finding up against the
    plan's own prior finding (by club/label/age_group/half) to recover that
    provenance instead of degrading every mismatch to the same generic reason.
    """
    previous_shortfalls = [
        item for item in (plan_dict.get("unresolved_participation_shortfalls") or []) if isinstance(item, dict)
    ]
    shortfall_lookup: dict[tuple[Any, ...], dict[str, Any]] = {}
    shortfall_lookup_no_label: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in previous_shortfalls:
        half = item.get("period") or item.get("half")
        shortfall_lookup[(item.get("club", ""), item.get("label", ""), item.get("age_group", ""), half)] = item
        shortfall_lookup_no_label.setdefault((item.get("club", ""), item.get("age_group", ""), half), item)

    evidence_by_age_group_half: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for entry in plan_dict.get("same_date_capacity_evidence") or []:
        if not isinstance(entry, dict):
            continue
        evidence_by_age_group_half.setdefault((entry.get("age_group"), entry.get("period")), []).append(entry)

    reconciled: list[dict[str, Any]] = []
    for item in source.get("manual_participation_placements") or []:
        if not isinstance(item, dict):
            continue
        club = item.get("club", "")
        label = item.get("label", "")
        age_group = item.get("age_group", "")
        half = item.get("half")
        source_entry = shortfall_lookup.get((club, label, age_group, half)) or shortfall_lookup_no_label.get(
            (club, age_group, half)
        )
        actual_raw, target_raw = item.get("actual", ""), item.get("target", "")
        try:
            under_target = int(actual_raw) < int(target_raw)
        except (TypeError, ValueError):
            under_target = True
        category = (source_entry or {}).get(
            "category", "participation_under_target" if under_target else "participation_over_target"
        )
        reason = (source_entry or {}).get("reason", "actual participation count does not match target")
        reconciled_entry: dict[str, Any] = {
            "club": club,
            "label": label,
            "age_group": age_group,
            "actual": actual_raw,
            "target": target_raw,
            "category": category,
            "reason": reason,
        }
        if half:
            reconciled_entry["half"] = half
        if category == "participation_under_target_same_date_capacity":
            evidence = evidence_by_age_group_half.get((age_group, half))
            if evidence:
                reconciled_entry["same_date_capacity_evidence"] = evidence
        reconciled.append(reconciled_entry)
    return reconciled


def _reconcile_operator_waivers(
    plan_dict: dict[str, Any],
    source: dict[str, Any],
    problem: dict[str, Any] | None,
) -> None:
    """Refresh the plan's operator-waiver projection from the verifier result.

    A waiver that stopped matching is no longer in ``waived_violations`` and
    must disappear from the plan; a still-applied one stays visible so the
    exported plan can distinguish "hard-valid" from "hard rule explicitly
    waived".  Audit rows need the configured waiver definitions, so they are
    only rebuilt when the caller provides the problem.
    """
    applied = {
        str(item.get("waiver_id"))
        for item in (source.get("waived_violations") or [])
        if isinstance(item, dict) and item.get("waiver_id")
    }
    plan_dict["operator_waived_violations"] = list(source.get("waived_violations") or [])
    if problem is None:
        return
    plan_dict["operator_waivers"] = [
        row for row in waiver_audit_rows(problem) if str(row.get("id")) in applied
    ]


def reconcile_plan_derived_state(
    plan_dict: dict[str, Any] | None,
    authoritative: dict[str, Any] | None,
    *,
    readiness: dict[str, Any] | None = None,
    problem: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Re-derive a plan's descriptive snapshot in place from a verify result.

    This is the *one* boundary that refreshes every verifier-derived plan
    projection -- hosting coverage/imbalance and repair logs, publication
    readiness, external-calendar conflicts, participation shortfalls and the
    operator-waiver audit rows -- so no canonical write or Stage 4 render can
    install a snapshot that contradicts the verifier that owns the rule.

    *authoritative* is the freshest verifier/evidence state for the same plan:
    a ``verify_candidate``/``verify_final_candidate`` result or a
    fingerprint-bound ``final_operator_evidence`` block.  Only facts the
    authoritative source actually carries are overwritten, so a partial
    fixture that omits a field never clears real plan data.

    *readiness* overrides the derived readiness.  Pass it when the caller
    already holds an authoritative, already-reconciled readiness (the
    fingerprint-bound final operator evidence), so plan-only facts such as
    ``unresolved_tournament_placements`` are not folded in twice.

    *problem* is needed only to rebuild the configured operator-waiver audit
    rows; without it the verifier-derived ``operator_waived_violations`` is
    still refreshed and existing waiver rows are left untouched.
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

    if "manual_external_conflict_placements" in source:
        plan_dict["unresolved_external_conflicts"] = _reconciled_external_conflicts(source)
    if "manual_participation_placements" in source:
        plan_dict["unresolved_participation_shortfalls"] = _reconciled_participation_shortfalls(
            plan_dict, source
        )
    if "waived_violations" in source:
        _reconcile_operator_waivers(plan_dict, source, problem)

    if readiness is not None:
        plan_dict["publication_readiness"] = dict(readiness)
    else:
        plan_dict["publication_readiness"] = publication_readiness_with_plan_placements(source, plan_dict)
    return plan_dict


__all__ = [
    "publication_readiness_with_plan_placements",
    "reconcile_plan_derived_state",
]
