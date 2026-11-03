"""Service surface for reviewed club-wide booking acceptance.

Mixed into :class:`~tournament_scheduler.application.canonical_season_service.CanonicalSeasonService`
so the stable service facade does not grow; the use cases live in
:mod:`tournament_scheduler.application.canonical_season.calendars.acceptance`.
``club_booking_acceptance_apply`` is a gated canonical write (see ``write_gate``);
the preview is read-only.
"""

from __future__ import annotations

from typing import Any

from .calendars import acceptance as _calendars


class ClubBookingAcceptanceMixin:
    def club_booking_acceptance_preview(
        self,
        *,
        season: str,
        source_id: str,
        actor: str | None = None,
        note: str = "",
        reference: str = "",
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        return _calendars.club_booking_acceptance_preview(
            self,
            season=season,
            source_id=source_id,
            actor=actor,
            note=note,
            reference=reference,
            expected_revision=expected_revision,
        )

    def club_booking_acceptance_apply(
        self,
        *,
        season: str,
        source_id: str,
        actor: str | None = None,
        note: str = "",
        reference: str = "",
        expected_revision: str | None = None,
        reviewed_token: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return _calendars.club_booking_acceptance_apply(
            self,
            season=season,
            source_id=source_id,
            actor=actor,
            note=note,
            reference=reference,
            expected_revision=expected_revision,
            reviewed_token=reviewed_token,
            dry_run=dry_run,
        )
