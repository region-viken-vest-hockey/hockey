"""The single application-layer gate for canonical season writes.

:class:`CanonicalSeasonService` is decorated with :func:`gate_canonical_writes`,
which wraps every name in :data:`GATED_WRITE_METHODS`. Each wrapper calls the
service's ``write_gate`` before the use case does any work, so a refused client
leaves local canonical state untouched. Read-only use cases are not wrapped.

Policy (version minimums, manifest trust, refusal text) lives in
``distribution_update``. This module only selects the installed version and
cache location and calls the policy. It holds no scheduling or hockey rules.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from tournament_scheduler import distribution, distribution_update

GATED_WRITE_METHODS = frozenset(
    {
        "compact_history", "season_baseline_create", "season_baseline_advance", "refresh_calendars",
        "reconcile_config", "mark_export_fresh", "add_request_constraint", "release_request_constraints",
        "allow_holiday_date", "disallow_holiday_dates", "add_banned_date", "release_banned_dates", "promote",
        "move_tournament", "swap_participants", "replace_participant", "remove_participant",
        "release_participation_withdrawals", "apply_retire_team", "batch_maintenance", "rename_teams",
        "apply_candidate", "seal_published_season", "reopen_planning", "normalize_placements",
        "normalize_arena_identities", "reserve_guest_slot", "fill_guest_slot", "release_guest_slot",
        "release_change_protections", "reconcile_calendar_bookings", "confirm_calendar_booking",
        "release_calendar_booking", "set_manual_booking_assertion", "clear_manual_booking_assertion",
        "set_club_booking_source", "set_ice_time_minutes", "clear_ice_time_minutes",
        "record_placement_infeasibility_proofs", "release_placement_infeasibility_proofs",
        "approve_tournament", "unapprove_tournament", "record_participation_acceptance",
        "revoke_participation_acceptance",
    }
)

WriteGate = Callable[[], None]
_Method = TypeVar("_Method", bound=Callable[..., Any])


def write_gate_for(installed: str, cache_dir: Path) -> WriteGate:
    """Build a gate that refuses writes when the cached release manifest forbids them."""

    def gate() -> None:
        distribution_update.require_cached_write_allowed(installed, Path(cache_dir))

    return gate


def production_write_gate() -> WriteGate:
    """Gate for the installed application and its per-user update cache."""
    return write_gate_for(distribution.version_metadata()["version"], distribution.default_update_cache_dir())


def gate_canonical_writes(cls: type) -> type:
    """Class decorator: every gated write consults ``self.write_gate`` before running.

    The gate is a zero-argument callable that raises ``WriteBlockedError`` to refuse.
    Unlisted methods are untouched, so reads are never refused by this gate.
    """
    missing = sorted(GATED_WRITE_METHODS - set(vars(cls)))
    if missing:
        raise TypeError(f"{cls.__name__} has no gated write methods: {missing}")

    def gated(method: _Method) -> _Method:
        @functools.wraps(method)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            self.write_gate()
            return method(self, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    for name in GATED_WRITE_METHODS:
        setattr(cls, name, gated(getattr(cls, name)))
    return cls


__all__ = ["GATED_WRITE_METHODS", "WriteGate", "gate_canonical_writes", "production_write_gate", "write_gate_for"]
