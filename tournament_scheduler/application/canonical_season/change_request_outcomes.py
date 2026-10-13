"""Structured lifecycle outcomes for legacy change-request history.

This focused owner keeps outcome validation and the narrow history-only backfill
operation out of the read-only ledger renderer.  Importing the canonical season
service is deliberately local to the operation so the service can continue to
load the ledger projection without a module cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import os
from typing import Any

from tournament_scheduler.application.canonical_season import lifecycle
from tournament_scheduler.infrastructure.canonical_season_store import (
    DEFAULT_SEASON_ROOT,
    SeasonStateError,
)
from tournament_scheduler.season.state import canonical_state_revision


COMPLETED = "completed"
MANUAL_FOLLOW_UP_REQUIRED = "manual_follow_up_required"


def mutation_request_status(event: Mapping[str, Any]) -> str | None:
    """Map durable mutation outcome evidence to the ledger lifecycle status."""

    details = event.get("details") if isinstance(event.get("details"), Mapping) else {}
    outcome = details.get("change_request_outcome")
    if outcome is None:
        return None
    if outcome == COMPLETED:
        return "resolved"
    # Explicit unresolved evidence and unknown future/invalid values must never
    # fall through to the legacy mutation-is-completed compatibility rule.
    return "needs_action"


def combine_status(statuses: list[str]) -> str:
    """Combine lifecycle evidence, preferring unresolved/actionable states."""

    values = {status for status in statuses if status}
    if not values:
        return "unknown"
    if "needs_action" in values and (values - {"needs_action"}):
        return "partially_resolved"
    if "needs_action" in values:
        return "needs_action"
    if "active" in values and "resolved" in values:
        return "partially_resolved"
    if "active" in values:
        return "active"
    if "resolved" in values:
        return "resolved"
    if values == {"released"}:
        return "released"
    if values == {"superseded"}:
        return "superseded"
    if "released" in values and "superseded" in values:
        return "released"
    rank = {
        "needs_action": 0,
        "partially_resolved": 1,
        "active": 2,
        "resolved": 3,
        "superseded": 4,
        "released": 5,
        "unknown": 6,
    }
    return sorted(values, key=lambda status: rank.get(status, 99))[0]


def set_change_request_outcome(
    *,
    season: str,
    history_index: int,
    event: str,
    tournament_id: str,
    before_canonical_revision: str,
    outcome: str,
    expected_revision: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
) -> dict[str, Any]:
    """Backfill structured request-lifecycle evidence on one exact history event.

    This is a history-only, revision-bound annotation: it cannot change the
    schedule, booking evidence, or effective canonical-state revision. The
    complete legacy identity is required so an index shifted by intervening
    history cannot annotate the wrong event.
    """

    valid_outcomes = {COMPLETED, MANUAL_FOLLOW_UP_REQUIRED}
    if outcome not in valid_outcomes:
        raise SeasonStateError(
            "Unsupported change-request outcome; expected one of: "
            + ", ".join(sorted(valid_outcomes))
        )

    from tournament_scheduler.application.canonical_season_service import (
        CanonicalSeasonService,
    )

    service = CanonicalSeasonService(root=root)
    snapshot = service.load(season)
    current_revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    if current_revision != expected_revision:
        raise SeasonStateError(
            "Refusing stale change-request outcome annotation: expected canonical "
            f"revision {expected_revision}, found {current_revision}"
        )
    history = deepcopy(snapshot.decisions.get("history") or [])
    if history_index < 0 or history_index >= len(history):
        raise SeasonStateError(f"History index {history_index} does not exist")
    history_event = history[history_index]
    details = history_event.get("details") if isinstance(history_event, Mapping) else None
    identity = (
        str(history_event.get("event") or "") if isinstance(history_event, Mapping) else "",
        str(history_event.get("tournament_id") or "") if isinstance(history_event, Mapping) else "",
        str(details.get("before_canonical_revision") or "") if isinstance(details, Mapping) else "",
    )
    expected_identity = (event, tournament_id, before_canonical_revision)
    if identity != expected_identity:
        raise SeasonStateError(
            "Refusing change-request outcome annotation: history event identity "
            f"{identity!r} does not match {expected_identity!r}"
        )
    existing = details.get("change_request_outcome")
    if existing is not None and existing != outcome:
        raise SeasonStateError(
            "Refusing to replace existing change-request outcome "
            f"{existing!r} with {outcome!r}"
        )
    if existing == outcome:
        return {
            "season": season,
            "history_index": history_index,
            "outcome": outcome,
            "changed": False,
            "canonical_state_revision": current_revision,
        }

    updated_details = dict(details)
    updated_details["change_request_outcome"] = outcome
    history[history_index] = {**history_event, "details": updated_details}
    updated_decisions = dict(snapshot.decisions)
    updated_decisions["history"] = history
    committed = lifecycle._commit_history_only(
        service,
        snapshot.with_decisions(updated_decisions),
        expected_revision=expected_revision,
    )
    committed_revision = canonical_state_revision(committed.schedule, committed.decisions)
    if committed_revision != current_revision:
        raise SeasonStateError("History-only outcome annotation changed canonical revision")
    return {
        "season": season,
        "history_index": history_index,
        "outcome": outcome,
        "changed": True,
        "canonical_state_revision": committed_revision,
    }
