"""Stable function facade for reviewed club-wide booking acceptance."""

from __future__ import annotations

import os
from typing import Any

from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.infrastructure.canonical_season_store import DEFAULT_SEASON_ROOT

__all__ = ["club_booking_acceptance_apply", "club_booking_acceptance_preview"]


def club_booking_acceptance_preview(
    *,
    season: str,
    source_id: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
    reference: str = "",
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """Read-only preview of the interpretations linked to one active club source."""

    return CanonicalSeasonService(root=root).club_booking_acceptance_preview(
        season=season,
        source_id=source_id,
        actor=actor,
        note=note,
        reference=reference,
        expected_revision=expected_revision,
    )


def club_booking_acceptance_apply(
    *,
    season: str,
    source_id: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
    reference: str = "",
    expected_revision: str | None = None,
    reviewed_token: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Record acceptance of the exact previewed interpretations (gated write)."""

    return CanonicalSeasonService(root=root).club_booking_acceptance_apply(
        season=season,
        source_id=source_id,
        actor=actor,
        note=note,
        reference=reference,
        expected_revision=expected_revision,
        reviewed_token=reviewed_token,
        dry_run=dry_run,
    )
