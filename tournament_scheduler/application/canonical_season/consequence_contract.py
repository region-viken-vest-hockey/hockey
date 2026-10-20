"""Normalization of scoped dry-run results into the shared consequence preview contract.

Every gate verdict is read from the operation's dry-run result; this module owns
no rule of its own. It reads no state, so callers that already hold a dry-run
result (the preview itself and candidate discovery) derive verdict, findings and
consequences the same way.
"""

from __future__ import annotations

from typing import Any, Mapping

from .consequence_summary import NO_CONSEQUENCES, build_consequences, build_findings, unclassified_findings

PREVIEW_CONTRACT = "canonical-consequence-preview/1"

STATUS_SAFE_TO_APPLY = "safe_to_apply"
STATUS_BLOCKED = "blocked"
STATUS_STALE_REVISION = "stale_revision"


def build_preview(
    operation: str,
    *,
    season: str,
    current_revision: str,
    changed_tournament_ids: list[str],
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize one dry-run result into the shared preview contract."""

    details = _operation_details(operation, raw)
    reviewed_token = None
    changed_ids = list(changed_tournament_ids)
    if operation == "batch":
        verdict = _batch_verdict(raw)
        changed_ids = sorted(str(item) for item in raw.get("changed_tournament_ids") or changed_ids)
        reviewed_token = raw.get("reviewed_token")
    elif operation == "repair":
        verdict = _repair_verdict(raw, details)
        changed_ids = sorted(str(item) for item in (raw.get("delta") or {}).get("changed_tournament_ids") or [])
    else:
        verdict = _gate_verdict(details, raw)
    envelope = _envelope(
        operation=operation,
        season=season,
        current_revision=current_revision,
        candidate_fingerprint=_candidate_fingerprint(raw, details),
        changed_tournament_ids=changed_ids,
        verdict=verdict,
        details=details,
        consequences=build_consequences(operation, details),
        findings=build_findings(details),
    )
    envelope["reviewed_token"] = reviewed_token
    return envelope


def blocked_preview(
    operation: str,
    *,
    season: str,
    current_revision: str,
    changed_tournament_ids: list[str],
    message: str,
) -> dict[str, Any]:
    """Return the blocked contract envelope for a dry-run refused with ``message``."""

    return _envelope(
        operation=operation,
        season=season,
        current_revision=current_revision,
        candidate_fingerprint=None,
        changed_tournament_ids=list(changed_tournament_ids),
        verdict=_verdict(STATUS_BLOCKED, [message]),
        details={},
    )


def stale_preview(
    operation: str,
    *,
    season: str,
    current_revision: str,
    changed_tournament_ids: list[str],
    expected_revision: str,
) -> dict[str, Any]:
    """Return the envelope for a caller bound to a revision that is no longer current."""

    return _envelope(
        operation=operation,
        season=season,
        current_revision=current_revision,
        candidate_fingerprint=None,
        changed_tournament_ids=list(changed_tournament_ids),
        verdict=_verdict(STATUS_STALE_REVISION, [f"expected revision {expected_revision} is no longer current"]),
        details={},
    )


def _batch_verdict(raw: Mapping[str, Any]) -> dict[str, Any]:
    # A batch dry-run already composes every gate for the whole candidate, so its
    # verdict is passed through rather than rebuilt from a subset of fields.
    reported = dict(raw.get("verdict") or {})
    status = STATUS_SAFE_TO_APPLY if reported.get("applicable") else STATUS_BLOCKED
    verdict = _verdict(status, list(reported.get("blockers") or []), checks=reported.get("checks") or {})
    verdict["not_evaluated"] = []
    verdict["review"] = dict(reported.get("review") or {})
    return verdict


def _operation_details(operation: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    if operation == "batch":
        return dict(raw)
    if operation == "repair":
        return _repair_details(raw)
    if operation == "move":
        return dict((raw.get("move_preview") or {}))
    key = {"replace": "replacement", "swap": "swap", "remove": "removal"}[operation]
    return dict(raw.get(key) or {})


def _repair_details(raw: Mapping[str, Any]) -> dict[str, Any]:
    # Gate fields come from the repair dry-run as the repair owner computed them.
    # A field the dry-run did not reach stays absent, so it reads as not evaluated.
    details: dict[str, Any] = {"delta": dict(raw.get("delta") or {})}
    if isinstance(raw.get("verification"), Mapping):
        details["verification_result"] = dict(raw["verification"])
    if isinstance(raw.get("operational_acceptability"), Mapping):
        details["operational_acceptability"] = dict(raw["operational_acceptability"])
    if raw.get("ok") or raw.get("reason") == "request_constraint_violation":
        # Request constraints are checked before the dry-run can succeed.
        details["request_constraint_acceptable"] = bool(raw.get("ok"))
    adoption = raw.get("adoption")
    if isinstance(adoption, Mapping):
        details["adoption"] = dict(adoption)
        details["candidate_fingerprint"] = adoption.get("candidate_fingerprint")
    return details


def _repair_verdict(raw: Mapping[str, Any], details: Mapping[str, Any]) -> dict[str, Any]:
    if not raw.get("ok"):
        # A rejected repair names its reason; gate fields may be partial.
        reason = str(raw.get("reason") or "repair_rejected")
        message = raw.get("message")
        blockers = [reason] + ([str(message)] if message else [])
        return _verdict(STATUS_BLOCKED, blockers)
    return _gate_verdict(details, raw)


def _candidate_fingerprint(raw: Mapping[str, Any], details: Mapping[str, Any]) -> str | None:
    for value in (
        raw.get("candidate_fingerprint"),
        raw.get("candidate_revision"),
        raw.get("candidate_schedule_revision"),
        details.get("after_fingerprint"),
        details.get("candidate_fingerprint"),
    ):
        if value:
            return str(value)
    return None


def _gate_verdict(details: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    blockers: list[str] = []
    not_evaluated: list[str] = []

    verification = details.get("verification_result") or raw.get("verification_result")
    if isinstance(verification, Mapping):
        checks["schedule_acceptable"] = bool(verification.get("ok", False))
    else:
        not_evaluated.append("schedule_acceptable")

    operational = details.get("operational_acceptability")
    if "consequence_acceptable" in details:
        checks["consequences_acceptable"] = bool(details["consequence_acceptable"])
    elif isinstance(operational, Mapping):
        checks["consequences_acceptable"] = bool(operational.get("ok", False))
    else:
        not_evaluated.append("consequences_acceptable")

    simple_gates = {
        "change_protection_acceptable": "change_protection_acceptable",
        "request_constraints_acceptable": "request_constraint_acceptable",
        "hosting_responsibility_acceptable": "hosting_responsibility_ok",
    }
    for gate, field in simple_gates.items():
        if field in details:
            checks[gate] = bool(details[field])
        else:
            not_evaluated.append(gate)

    guest = details.get("guest_reservation_integrity")
    if isinstance(guest, Mapping):
        checks["guest_reservation_integrity"] = bool(guest.get("ok", False))
    else:
        not_evaluated.append("guest_reservation_integrity")

    blockers.extend(gate for gate, passed in checks.items() if not passed)

    # A dry-run that already reports a blocked verdict (for example a
    # participant lock) carries blockers the gate fields cannot express.
    reported = raw.get("verdict")
    if isinstance(reported, Mapping) and not reported.get("applicable", True):
        blockers.extend(str(item) for item in reported.get("blockers") or [] if str(item) not in blockers)
    elif not checks:
        # Nothing was evaluated, so nothing can be called safe to apply.
        blockers.append("no_gates_evaluated")

    status = STATUS_BLOCKED if blockers else STATUS_SAFE_TO_APPLY
    verdict = _verdict(status, blockers, checks=checks)
    verdict["not_evaluated"] = not_evaluated
    return verdict


def _verdict(
    status: str,
    blockers: list[str],
    *,
    checks: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "applicable": status == STATUS_SAFE_TO_APPLY,
        "checks": dict(checks or {}),
        "blockers": list(blockers),
    }


def _envelope(
    *,
    operation: str,
    season: str,
    current_revision: str,
    candidate_fingerprint: str | None,
    changed_tournament_ids: list[str],
    verdict: dict[str, Any],
    details: Mapping[str, Any],
    consequences: Mapping[str, Any] | None = None,
    findings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "contract": PREVIEW_CONTRACT,
        "operation": operation,
        "season": season,
        "writes_canonical_state": False,
        "expected_revision": current_revision,
        "candidate_fingerprint": candidate_fingerprint,
        "changed_tournament_ids": changed_tournament_ids,
        "verdict": verdict,
        "consequences": dict(consequences or NO_CONSEQUENCES),
        "findings": dict(findings or unclassified_findings()),
        "details": dict(details),
    }
