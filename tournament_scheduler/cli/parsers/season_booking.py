"""Season booking, ice-time and booking-reconciliation commands."""

from __future__ import annotations

import argparse


def add_season_booking_parsers(season_sub: argparse._SubParsersAction) -> None:
    season_booking_assessment = season_sub.add_parser(
        "booking-assessment",
        help=(
            "Read-only, revision/source-bound tournament-event booking crosswalk "
            "(candidates, competing mappings, group bookings, unmatched rows)"
        ),
    )
    season_booking_assessment.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_booking_assessment.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_booking_assessment.add_argument("--club", default=None, help="Optional host club filter")
    season_booking_assessment.add_argument(
        "--date-window-days",
        type=int,
        default=7,
        help="Bounded +/- day window for plausible changed-date candidates (default: 7)",
    )
    season_booking_assessment.add_argument("--json", action="store_true", help="Print structured JSON")

    season_booking_candidates = season_sub.add_parser(
        "calendar-booking-candidates",
        help="Return deterministic tournament candidates for scraped calendar bookings",
    )
    season_booking_candidates.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_booking_candidates.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_booking_candidates.add_argument("--club", default=None, help="Optional host club filter")
    season_booking_candidates.add_argument("--json", action="store_true", help="Print structured JSON")

    season_confirm_booking = season_sub.add_parser(
        "confirm-calendar-booking",
        help="Bind one scraped calendar event to one canonical tournament and approve/lock the placement",
    )
    season_confirm_booking.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_confirm_booking.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_confirm_booking.add_argument("--event-fingerprint", required=True, help="Scraped calendar event fingerprint")
    season_confirm_booking.add_argument("--tournament-id", required=True, help="Canonical tournament id")
    season_confirm_booking.add_argument("--actor", default=None, help="Operator identity")
    season_confirm_booking.add_argument("--note", default="", help="Audited rationale for this semantic match")
    season_confirm_booking.add_argument("--dry-run", action="store_true", help="Validate without writing canonical state")
    season_confirm_booking.add_argument("--json", action="store_true", help="Print structured JSON")

    season_booking_findings = season_sub.add_parser(
        "calendar-booking-findings",
        help="Report stale scraped-event booking associations",
    )
    season_booking_findings.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_booking_findings.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_booking_findings.add_argument("--json", action="store_true", help="Print structured JSON")

    season_booking_status = season_sub.add_parser(
        "booking-status",
        help="Report per-tournament booking evidence status separately from approvals",
    )
    season_booking_status.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_booking_status.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_booking_status.add_argument("--json", action="store_true", help="Print structured JSON")

    season_booking_set = season_sub.add_parser(
        "booking-set",
        help=(
            "Record an explicit operator/club booking assertion for one tournament "
            "without scraping; durable against calendar reconcile/refresh"
        ),
    )
    season_booking_set.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_booking_set.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_booking_set.add_argument("--tournament-id", required=True, help="Canonical tournament id")
    season_booking_set.add_argument(
        "--status",
        required=True,
        choices=("booked", "not-booked"),
        help="Manual conclusion: booked or not-booked/rejected",
    )
    season_booking_set.add_argument("--actor", default=None, help="Operator identity")
    season_booking_set.add_argument("--note", default="", help="Audited rationale for this assertion")
    season_booking_set.add_argument("--reference", default="", help="Source reference, e.g. email id/date/sender")
    season_booking_set.add_argument(
        "--source-scope",
        default="tournament",
        choices=("tournament", "club_wide_interpretation"),
        help=(
            "Whether this is a direct per-tournament assertion or one deliberately "
            "accepted interpretation of a club-wide statement"
        ),
    )
    season_booking_set.add_argument(
        "--source-assertion-id",
        default=None,
        help=(
            "Link this per-tournament interpretation to a recorded club-wide booking "
            "source (requires --source-scope club_wide_interpretation)"
        ),
    )
    season_booking_set.add_argument(
        "--stated-date",
        default=None,
        help="Optional source-stated date YYYY-MM-DD (defaults to the current canonical date when omitted)",
    )
    season_booking_set.add_argument(
        "--stated-start",
        default=None,
        help="Optional source-stated start time HH:MM; booked assertions apply the stated interval canonically",
    )
    season_booking_set.add_argument(
        "--stated-end",
        default=None,
        help=(
            "Optional source-stated end time HH:MM, strictly after --stated-start on the same day "
            "(overnight not supported); booked assertions apply this actual interval canonically"
        ),
    )
    season_booking_set.add_argument(
        "--expected-revision",
        default=None,
        help="Fail closed unless the canonical-state revision matches",
    )
    season_booking_set.add_argument(
        "--supersede",
        action="store_true",
        help="Replace a different active assertion for this tournament (requires --note)",
    )
    season_booking_set.add_argument("--dry-run", action="store_true", help="Preview without writing canonical state")
    season_booking_set.add_argument("--json", action="store_true", help="Print structured JSON")

    season_booking_clear = season_sub.add_parser(
        "booking-clear",
        help="Revoke an active manual booking assertion without touching the schedule",
    )
    season_booking_clear.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_booking_clear.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_booking_clear.add_argument("--tournament-id", required=True, help="Canonical tournament id")
    season_booking_clear.add_argument("--actor", default=None, help="Operator identity")
    season_booking_clear.add_argument("--note", default="", help="Audited reason for revoking the assertion")
    season_booking_clear.add_argument("--dry-run", action="store_true", help="Preview without writing canonical state")
    season_booking_clear.add_argument("--json", action="store_true", help="Print structured JSON")

    season_booking_source_set = season_sub.add_parser(
        "booking-source-set",
        help=(
            "Record a club-wide booking source document (list/email/spreadsheet) as durable "
            "accepted authority, preserving its version and provenance"
        ),
    )
    season_booking_source_set.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_booking_source_set.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_booking_source_set.add_argument("--club", required=True, help="Host club the booking list belongs to")
    season_booking_source_set.add_argument(
        "--source-document",
        required=True,
        help="Source document identity/path, e.g. changes_and_confirmations/2026-27/Holmen.xlsx",
    )
    season_booking_source_set.add_argument(
        "--source-version",
        required=True,
        help="Source version the operator reviewed, e.g. a date, email id or received timestamp",
    )
    season_booking_source_set.add_argument(
        "--source-fingerprint",
        default=None,
        help="Optional explicit content fingerprint; defaults to a deterministic identity of club/document/version",
    )
    season_booking_source_set.add_argument("--actor", default=None, help="Operator identity")
    season_booking_source_set.add_argument("--note", default="", help="Audited rationale for accepting this source")
    season_booking_source_set.add_argument("--reference", default="", help="Source reference, e.g. email id/date/sender")
    season_booking_source_set.add_argument(
        "--expected-revision",
        default=None,
        help="Fail closed unless the canonical-state revision matches",
    )
    season_booking_source_set.add_argument("--dry-run", action="store_true", help="Preview without writing canonical state")
    season_booking_source_set.add_argument("--json", action="store_true", help="Print structured JSON")

    season_booking_sources = season_sub.add_parser(
        "booking-sources",
        help="Read-only per-ID disposition for recorded club-wide booking sources",
    )
    season_booking_sources.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_booking_sources.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_booking_sources.add_argument("--club", default=None, help="Optional host-club filter")
    season_booking_sources.add_argument("--json", action="store_true", help="Print the source disposition as JSON")

    season_set_ice_time = season_sub.add_parser(
        "set-ice-time-minutes",
        help=(
            "Record a host-confirmed per-tournament ice-time/duration override used by "
            "arena-conflict and calendar-booking interval verification"
        ),
    )
    season_set_ice_time.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_set_ice_time.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_set_ice_time.add_argument("--tournament-id", required=True, help="Canonical tournament id")
    season_set_ice_time.add_argument(
        "--minutes",
        required=True,
        type=int,
        help="Host-confirmed occupied duration in minutes for this tournament instance",
    )
    season_set_ice_time.add_argument(
        "--request-id",
        required=True,
        help="Stable host/request id recorded as provenance",
    )
    season_set_ice_time.add_argument("--actor", default=None, help="Operator identity")
    season_set_ice_time.add_argument("--note", default="", help="Audited rationale for this override")
    season_set_ice_time.add_argument("--reference", default="", help="Source reference, e.g. email id/date/sender")
    season_set_ice_time.add_argument(
        "--expected-revision",
        default=None,
        help="Fail closed unless the canonical-state revision matches",
    )
    season_set_ice_time.add_argument("--dry-run", action="store_true", help="Preview without writing canonical state")
    season_set_ice_time.add_argument("--json", action="store_true", help="Print structured JSON")

    season_clear_ice_time = season_sub.add_parser(
        "clear-ice-time-minutes",
        help="Release an active per-tournament ice-time override, restoring the age-group default",
    )
    season_clear_ice_time.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_clear_ice_time.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_clear_ice_time.add_argument("--tournament-id", required=True, help="Canonical tournament id")
    season_clear_ice_time.add_argument("--actor", default=None, help="Operator identity")
    season_clear_ice_time.add_argument("--note", default="", help="Audited reason for releasing the override")
    season_clear_ice_time.add_argument("--dry-run", action="store_true", help="Preview without writing canonical state")
    season_clear_ice_time.add_argument("--json", action="store_true", help="Print structured JSON")

    season_ice_time_overrides = season_sub.add_parser(
        "ice-time-overrides",
        help="List active per-tournament ice-time overrides and the age-group default they replace",
    )
    season_ice_time_overrides.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_ice_time_overrides.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_ice_time_overrides.add_argument("--all", action="store_true", help="Include released overrides")
    season_ice_time_overrides.add_argument("--json", action="store_true", help="Print the override report as JSON")

    season_reconcile_bookings = season_sub.add_parser(
        "reconcile-calendar-bookings",
        help="Record host-calendar booking evidence for one club (overlap is candidate evidence, never a confirmed booking)",
    )
    season_reconcile_bookings.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_reconcile_bookings.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_reconcile_bookings.add_argument("--club", required=True, help="Host club whose calendar was reviewed")
    season_reconcile_bookings.add_argument("--actor", default=None, help="Operator identity")
    season_reconcile_bookings.add_argument("--note", default="", help="Audit note for the calendar review")
    season_reconcile_bookings.add_argument("--dry-run", action="store_true", help="Classify without writing canonical state")
    season_reconcile_bookings.add_argument("--json", action="store_true", help="Print structured JSON")

    season_release_booking = season_sub.add_parser(
        "release-calendar-booking",
        help="Release an active scraped-event booking association before rebinding",
    )
    season_release_booking.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_release_booking.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_release_booking.add_argument("--event-fingerprint", required=True, help="Scraped calendar event fingerprint")
    season_release_booking.add_argument("--tournament-id", default=None, help="Optional associated tournament id to release")
    season_release_booking.add_argument("--actor", default=None, help="Operator identity")
    season_release_booking.add_argument("--note", default="", help="Audited reason for releasing this association")
    season_release_booking.add_argument("--dry-run", action="store_true", help="Validate without writing canonical state")
    season_release_booking.add_argument("--json", action="store_true", help="Print structured JSON")
