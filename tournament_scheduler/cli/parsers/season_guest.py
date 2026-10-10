"""Season guest-slot commands."""

from __future__ import annotations

import argparse


def add_season_guest_parsers(season_sub: argparse._SubParsersAction) -> None:
    season_guest_report = season_sub.add_parser(
        "guest-report",
        help="Show reserved guest places and their open/filled/released lifecycle status",
    )
    season_guest_report.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_guest_report.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_guest_report.add_argument("--json", action="store_true", help="Print the guest-slot report as JSON")

    season_guest_candidates = season_sub.add_parser(
        "guest-candidates",
        help="List deterministic legal candidates for reserving guest places",
    )
    season_guest_candidates.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_guest_candidates.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_guest_candidates.add_argument(
        "--age-groups",
        default=None,
        help="Comma-separated age groups (default: JU10,JU12)",
    )
    season_guest_candidates.add_argument(
        "--max-per-tournament",
        type=int,
        default=1,
        help="Maximum reservations per tournament when ranking candidates (default: 1)",
    )
    season_guest_candidates.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_guest_candidates.add_argument("--json", action="store_true", help="Print candidates as JSON")

    season_guest_reserve = season_sub.add_parser(
        "guest-reserve",
        help="Reserve one or more guest places on one canonical tournament",
    )
    season_guest_reserve.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_guest_reserve.add_argument("--tournament-id", required=True, help="Durable tournament id to reserve on")
    season_guest_reserve.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_guest_reserve.add_argument("--count", type=int, default=1, help="Number of places to reserve (default: 1)")
    season_guest_reserve.add_argument(
        "--displaced-team",
        dest="displaced_teams",
        action="append",
        default=None,
        help="Participant label to displace when the tournament is full (repeatable)",
    )
    season_guest_reserve.add_argument("--actor", default=None, help="Operator identity")
    season_guest_reserve.add_argument("--note", default="", help="Reservation note/reason for decisions history")
    season_guest_reserve.add_argument("--dry-run", action="store_true", help="Validate and preview without writing canonical state")
    season_guest_reserve.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_guest_reserve.add_argument("--json", action="store_true", help="Print updated schedule.json as JSON")

    season_guest_fill = season_sub.add_parser(
        "guest-fill",
        help="Accept an external team into a reserved guest place and regenerate games",
    )
    season_guest_fill.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_guest_fill.add_argument("--tournament-id", required=True, help="Durable tournament id holding the reservation")
    season_guest_fill.add_argument("--slot-id", default=None, help="Reservation id (default: first open reservation)")
    season_guest_fill.add_argument("--external-club", default="", help="External guest team's club")
    season_guest_fill.add_argument("--external-label", required=True, help="External guest team's label")
    season_guest_fill.add_argument("--external-age-group", default=None, help="External guest team's age group (default: tournament age group)")
    season_guest_fill.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_guest_fill.add_argument("--actor", default=None, help="Operator identity")
    season_guest_fill.add_argument("--note", default="", help="Fill note for decisions history")
    season_guest_fill.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_guest_fill.add_argument("--json", action="store_true", help="Print updated schedule.json as JSON")

    season_guest_release = season_sub.add_parser(
        "guest-release",
        help="Release a reserved guest place, optionally filling it with a real RVV team",
    )
    season_guest_release.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_guest_release.add_argument("--tournament-id", required=True, help="Durable tournament id holding the reservation")
    season_guest_release.add_argument("--slot-id", default=None, help="Reservation id (default: first active reservation)")
    season_guest_release.add_argument("--replacement-club", default=None, help="RVV club to fill the released place")
    season_guest_release.add_argument("--replacement-label", default=None, help="RVV team label to fill the released place")
    season_guest_release.add_argument("--replacement-age-group", default=None, help="Replacement team's age group (default: tournament age group)")
    season_guest_release.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_guest_release.add_argument("--actor", default=None, help="Operator identity")
    season_guest_release.add_argument("--note", default="", help="Release reason for decisions history")
    season_guest_release.add_argument("--dry-run", action="store_true", help="Validate and preview without writing canonical state")
    season_guest_release.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_guest_release.add_argument("--json", action="store_true", help="Print updated schedule.json as JSON")
