"""Shared read-only consequence preview for scoped canonical operations.

Replacement, swap, move and removal each compute their own dry-run with the
same domain gates (verification, operational acceptability, change protections,
request constraints, guest reservations, hosting responsibility). Their result
shapes differ, so callers used to re-derive applicability from per-operation
fields. This module runs the existing dry-run for the requested operation and
normalizes its result through ``consequence_contract`` into one stable contract.

The preview never writes. It refuses to report on a stale expected revision,
and it verifies after the dry-run that the canonical revision is unchanged.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.season.state import canonical_state_revision

from .batch import batch_maintenance
from .consequence_contract import (
    PREVIEW_CONTRACT,
    STATUS_BLOCKED,
    STATUS_SAFE_TO_APPLY,
    STATUS_STALE_REVISION,
    blocked_preview,
    build_preview,
    stale_preview,
)
from .repair_consequences import repair_team_consequences

SUPPORTED_OPERATIONS = ("replace", "swap", "move", "remove", "batch", "repair")

__all__ = [
    "PREVIEW_CONTRACT",
    "STATUS_BLOCKED",
    "STATUS_SAFE_TO_APPLY",
    "STATUS_STALE_REVISION",
    "SUPPORTED_OPERATIONS",
    "blocked_preview",
    "build_preview",
    "preview_canonical_operation",
]


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
        return stale_preview(
            operation,
            season=season,
            current_revision=current_revision,
            changed_tournament_ids=changed_ids,
            expected_revision=str(expected_revision),
        )

    if operation == "repair":
        # A repair option is reproduced from the canonical revision it was
        # reviewed against; passing it through makes the dry-run refuse a
        # revision that changed after the stale check above.
        arguments = {**arguments, "expected_revision": current_revision}

    try:
        raw = _DISPATCH[operation](service, season=season, dry_run=True, **arguments)
    except SeasonStateError as exc:
        return blocked_preview(
            operation,
            season=season,
            current_revision=current_revision,
            changed_tournament_ids=changed_ids,
            message=str(exc),
        )

    after = service.load(season)
    if canonical_state_revision(after.schedule, after.decisions) != current_revision:
        raise RuntimeError(
            "Consequence preview changed canonical state; a dry-run must never write"
        )

    return build_preview(
        operation,
        season=season,
        current_revision=current_revision,
        changed_tournament_ids=changed_ids,
        raw=raw,
    )


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


def _repair(
    service,
    *,
    season: str,
    dry_run: bool,
    option_id: str,
    expected_revision: str,
    finding_id: str | None = None,
    dimensions: tuple[str, ...] | None = None,
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
    accept_regressions: Any = (),
    regression_reason: str | None = None,
    **_context: Any,
) -> dict[str, Any]:
    # The repair option is re-derived from the canonical plan by the repair
    # owner. Verification problem and request context are not inputs here.
    from tournament_scheduler.repair.apply import apply_repair, apply_repair_to_plan
    from tournament_scheduler.repair.dispatch import DEFAULT_DIMENSIONS
    from tournament_scheduler.repair.host_team_missing import search_dimensions_from_option_id
    from tournament_scheduler.season.maintenance_context import SeasonMaintenanceError

    options: dict[str, Any] = {}
    if dimensions:
        options["dimensions"] = tuple(dimensions)
    try:
        result = apply_repair(
            season,
            option_id,
            expected_revision,
            root=str(service.store.root),
            dry_run=dry_run,
            finding_id=finding_id,
            allow_manual_placement=allow_manual_placement,
            allow_host_confirmation=allow_host_confirmation,
            accept_regressions=accept_regressions,
            regression_reason=regression_reason,
            **options,
        )
    except SeasonMaintenanceError as exc:
        return {"ok": False, "reason": "unknown_or_stale_finding", "message": str(exc)}
    # Reproduce the candidate the writer would build, so rejected repairs also
    # report which teams they would move. Gates are not re-run here.
    snapshot = service.load(season)
    problem = (snapshot.schedule.get("verification_context") or {}).get("problem")
    recovered = search_dimensions_from_option_id(option_id)
    reproduction = apply_repair_to_plan(
        snapshot.schedule["plan"],
        problem or {},
        option_id,
        finding_id=finding_id,
        dimensions=recovered or tuple(dimensions or DEFAULT_DIMENSIONS),
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
        decisions=snapshot.decisions,
    )
    if reproduction.get("ok") and isinstance(reproduction.get("candidate"), Mapping):
        result.update(
            repair_team_consequences(snapshot.schedule["plan"], reproduction["candidate"], problem)
        )
    return result


_DISPATCH: dict[str, Callable[..., dict[str, Any]]] = {
    "replace": _replace,
    "swap": _swap,
    "move": _move,
    "remove": _remove,
    "batch": _batch,
    "repair": _repair,
}
