"""Shared read-only consequence preview for scoped canonical operations.

Replacement, swap, move and removal each compute their own dry-run with the
same domain gates (verification, operational acceptability, change protections,
request constraints, guest reservations, hosting responsibility). Their result
shapes differ, so callers used to re-derive applicability from per-operation
fields. This module runs the existing dry-run for the requested operation and
normalizes its result into one stable contract. It owns no rule of its own:
every gate verdict is read from the operation's dry-run result.

The preview never writes. It refuses to report on a stale expected revision,
and it verifies after the dry-run that the canonical revision is unchanged.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.season.state import canonical_state_revision

from .batch import batch_maintenance

PREVIEW_CONTRACT = "canonical-consequence-preview/1"

SUPPORTED_OPERATIONS = ("replace", "swap", "move", "remove", "batch")

STATUS_SAFE_TO_APPLY = "safe_to_apply"
STATUS_BLOCKED = "blocked"
STATUS_STALE_REVISION = "stale_revision"


def preview_canonical_operation(
    service,
    *,
    operation: str,
    season: str,
    expected_revision: str | None = None,
    **arguments: Any,
) -> dict[str, Any]:
    """Return the normalized consequence preview for one scoped canonical operation.

    ``arguments`` are passed to the operation's existing service method with
    ``dry_run=True``. ``expected_revision`` binds the preview to the canonical
    revision the caller reviewed; a different current revision yields a
    ``stale_revision`` verdict without running the dry-run.
    """

    if operation not in SUPPORTED_OPERATIONS:
        raise ValueError(
            f"Unsupported preview operation {operation!r}; expected one of {', '.join(SUPPORTED_OPERATIONS)}"
        )

    before = service.load(season)
    current_revision = canonical_state_revision(before.schedule, before.decisions)
    changed_ids = _changed_tournament_ids(operation, arguments)

    if expected_revision is not None and str(expected_revision) != current_revision:
        return _envelope(
            operation=operation,
            season=season,
            current_revision=current_revision,
            candidate_fingerprint=None,
            changed_tournament_ids=changed_ids,
            verdict=_verdict(STATUS_STALE_REVISION, [f"expected revision {expected_revision} is no longer current"]),
            details={},
        )

    try:
        raw = _DISPATCH[operation](service, season=season, dry_run=True, **arguments)
    except SeasonStateError as exc:
        return _envelope(
            operation=operation,
            season=season,
            current_revision=current_revision,
            candidate_fingerprint=None,
            changed_tournament_ids=changed_ids,
            verdict=_verdict(STATUS_BLOCKED, [str(exc)]),
            details={},
        )

    after = service.load(season)
    if canonical_state_revision(after.schedule, after.decisions) != current_revision:
        raise RuntimeError(
            "Consequence preview changed canonical state; a dry-run must never write"
        )

    details = _operation_details(operation, raw)
    reviewed_token = None
    if operation == "batch":
        verdict = _batch_verdict(raw)
        changed_ids = sorted(str(item) for item in raw.get("changed_tournament_ids") or changed_ids)
        reviewed_token = raw.get("reviewed_token")
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
    )
    envelope["reviewed_token"] = reviewed_token
    return envelope


def _batch_verdict(raw: Mapping[str, Any]) -> dict[str, Any]:
    # A batch dry-run already composes every gate for the whole candidate, so its
    # verdict is passed through rather than rebuilt from a subset of fields.
    reported = dict(raw.get("verdict") or {})
    status = STATUS_SAFE_TO_APPLY if reported.get("applicable") else STATUS_BLOCKED
    verdict = _verdict(status, list(reported.get("blockers") or []), checks=reported.get("checks") or {})
    verdict["not_evaluated"] = []
    verdict["review"] = dict(reported.get("review") or {})
    return verdict


def _changed_tournament_ids(operation: str, arguments: Mapping[str, Any]) -> list[str]:
    if operation == "batch":
        ids = list(arguments.get("scope") or [])
    elif operation == "swap":
        ids = [arguments.get("tournament_a_id"), arguments.get("tournament_b_id")]
    elif operation == "remove":
        ids = list(arguments.get("tournament_ids") or [])
    else:
        ids = [arguments.get("tournament_id")]
    return sorted({str(item) for item in ids if item})


def _operation_details(operation: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    if operation == "batch":
        return dict(raw)
    if operation == "move":
        return dict((raw.get("move_preview") or {}))
    key = {"replace": "replacement", "swap": "swap", "remove": "removal"}[operation]
    return dict(raw.get(key) or {})


def _candidate_fingerprint(raw: Mapping[str, Any], details: Mapping[str, Any]) -> str | None:
    for value in (
        raw.get("candidate_fingerprint"),
        raw.get("candidate_revision"),
        raw.get("candidate_schedule_revision"),
        details.get("after_fingerprint"),
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
        "details": dict(details),
    }


def _replace(service, **kwargs: Any) -> dict[str, Any]:
    return service.replace_participant(**kwargs)


def _swap(service, **kwargs: Any) -> dict[str, Any]:
    return service.swap_participants(**kwargs)


def _move(service, **kwargs: Any) -> dict[str, Any]:
    return service.move_tournament(**kwargs)


def _remove(service, **kwargs: Any) -> dict[str, Any]:
    return service.remove_participant(**kwargs)


def _batch(service, **kwargs: Any) -> dict[str, Any]:
    return batch_maintenance(service, **kwargs)


_DISPATCH: dict[str, Callable[..., dict[str, Any]]] = {
    "replace": _replace,
    "swap": _swap,
    "move": _move,
    "remove": _remove,
    "batch": _batch,
}
